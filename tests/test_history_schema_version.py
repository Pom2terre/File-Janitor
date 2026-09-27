"""4E-30A : version explicite et migrations du schéma SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore


CURRENT_SCHEMA_VERSION = 2


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("PRAGMA user_version").fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def _columns(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
    finally:
        connection.close()


def _create_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                root TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES batches(id),
                kind TEXT NOT NULL,
                original_path TEXT NOT NULL,
                stored_path TEXT,
                size INTEGER NOT NULL,
                category TEXT NOT NULL
            );
            INSERT INTO batches(created_at, root, undone)
            VALUES ('2026-09-18T00:00:00', '/tmp/legacy', 0);
            INSERT INTO operations(
                batch_id, kind, original_path, stored_path, size, category
            ) VALUES (
                1, 'move', '/tmp/source.txt', '/tmp/stored.txt', 12, 'legacy'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _create_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                root TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                planned_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES batches(id),
                kind TEXT NOT NULL,
                original_path TEXT NOT NULL,
                stored_path TEXT,
                size INTEGER NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                error TEXT,
                recovery_state TEXT,
                resolution_kind TEXT,
                resolved_at TEXT,
                stored_device INTEGER,
                stored_inode INTEGER,
                stored_size INTEGER,
                stored_mtime_ns INTEGER
            );
            INSERT INTO batches(
                created_at, root, undone, status, planned_count, skipped_count
            ) VALUES (
                '2026-09-21T00:00:00', '/tmp/v1', 0, 'cancelled', 1, 1
            );
            INSERT INTO operations(
                batch_id, kind, original_path, stored_path, size, category,
                status
            ) VALUES (
                1, 'move', '/tmp/source.txt', '/tmp/stored.txt', 12,
                'to_sort', 'queued'
            );
            PRAGMA user_version = 1;
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_fresh_database_records_current_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"

    store = HistoryStore(db_path=db_path)
    store.close()

    assert _user_version(db_path) == CURRENT_SCHEMA_VERSION


def test_unversioned_legacy_database_migrates_to_current_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)
    assert _user_version(db_path) == 0

    store = HistoryStore(db_path=db_path)
    store.close()

    assert _user_version(db_path) == CURRENT_SCHEMA_VERSION
    assert {
        "status",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
    } <= _columns(db_path, "batches")
    assert {
        "status",
        "error",
        "recovery_state",
        "resolution_kind",
        "resolved_at",
        "stored_device",
        "stored_inode",
        "stored_size",
        "stored_mtime_ns",
        "original_device",
        "original_inode",
        "original_size",
        "original_mtime_ns",
        "conflict_policy",
    } <= _columns(db_path, "operations")

    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute(
            "SELECT root FROM batches WHERE id = 1"
        ).fetchone() == ("/tmp/legacy",)
        assert connection.execute(
            "SELECT original_path FROM operations WHERE id = 1"
        ).fetchone() == ("/tmp/source.txt",)
    finally:
        connection.close()


def test_v1_database_migrates_queued_rows_without_inventing_resume_proof(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    store = HistoryStore(db_path=db_path)
    operation = store.get_operations(1)[0]
    store.close()

    assert _user_version(db_path) == CURRENT_SCHEMA_VERSION
    assert operation.original_path == Path("/tmp/source.txt")
    assert operation.original_identity is None
    assert operation.original_identity_raw is None
    assert operation.conflict_policy is None
    assert operation.conflict_policy_raw is None
    assert {
        "original_device",
        "original_inode",
        "original_size",
        "original_mtime_ns",
        "conflict_policy",
    } <= _columns(db_path, "operations")


def test_current_database_reopen_is_byte_stable_and_skips_legacy_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    store.start_batch(str(tmp_path), planned_count=1)
    store.close()
    before = db_path.read_bytes()

    def unexpected_migration(_store: HistoryStore) -> None:
        raise AssertionError("legacy migration must not run for version 2")

    monkeypatch.setattr(HistoryStore, "_migrate_schema", unexpected_migration)
    reopened = HistoryStore(db_path=db_path)
    reopened.close()

    assert db_path.read_bytes() == before
    assert _user_version(db_path) == CURRENT_SCHEMA_VERSION


def test_future_schema_version_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "future.db"
    store = HistoryStore(db_path=db_path)
    store.close()

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    before = db_path.read_bytes()

    with pytest.raises(sqlite3.DatabaseError, match="[Vv]ersion.*3"):
        HistoryStore(db_path=db_path)

    assert db_path.read_bytes() == before
    assert _user_version(db_path) == 3


def test_current_version_with_legacy_shape_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "incomplete.db"
    _create_legacy_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    connection.close()
    before = db_path.read_bytes()

    with pytest.raises(sqlite3.DatabaseError, match="version 2.*incomplet"):
        HistoryStore(db_path=db_path)

    assert db_path.read_bytes() == before
    assert _user_version(db_path) == CURRENT_SCHEMA_VERSION


def test_failed_legacy_migration_does_not_advance_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)
    before = db_path.read_bytes()
    original = HistoryStore._migrate_schema

    def fail_after_migration(store: HistoryStore) -> None:
        original(store)
        raise RuntimeError("forced migration failure")

    monkeypatch.setattr(HistoryStore, "_migrate_schema", fail_after_migration)
    with pytest.raises(RuntimeError, match="forced migration failure"):
        HistoryStore(db_path=db_path)

    assert db_path.read_bytes() == before
    assert _user_version(db_path) == 0
    assert "status" not in _columns(db_path, "batches")
    assert "status" not in _columns(db_path, "operations")
