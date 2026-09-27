"""4E-28B: initialisation SQLite strictement fail-closed."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _user_objects(path: Path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(path)
    try:
        return [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]
    finally:
        connection.close()


def test_incompatible_partial_history_schema_is_rejected_unchanged(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "incompatible.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE batches(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserve-me')")
    connection.commit()
    connection.close()
    before_digest = _digest(db_path)
    before_objects = _user_objects(db_path)

    with pytest.raises(sqlite3.DatabaseError, match="schéma SQLite incompatible"):
        HistoryStore(db_path=db_path)

    assert _digest(db_path) == before_digest
    assert _user_objects(db_path) == before_objects


def test_foreign_nonempty_database_is_extended_without_data_loss(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "foreign.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserve-me')")
    connection.commit()
    connection.close()

    store = HistoryStore(db_path=db_path)
    try:
        marker = store._conn.execute("SELECT value FROM marker").fetchall()
        assert [tuple(row) for row in marker] == [("preserve-me",)]
    finally:
        store.close()
    assert {"batches", "operations", "marker"} <= {
        name for object_type, name in _user_objects(db_path)
        if object_type == "table"
    }


def test_compatible_legacy_schema_is_migrated_and_preserved(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE batches("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL, root TEXT NOT NULL, "
        "undone INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE operations("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "batch_id INTEGER NOT NULL REFERENCES batches(id), "
        "kind TEXT NOT NULL, original_path TEXT NOT NULL, "
        "stored_path TEXT, size INTEGER NOT NULL, category TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO batches(created_at, root, undone) VALUES (?, ?, 0)",
        ("2026-09-18T00:00:00", str(tmp_path)),
    )
    connection.execute(
        "INSERT INTO operations("
        "batch_id, kind, original_path, stored_path, size, category"
        ") VALUES (1, 'move', ?, NULL, 1, 'legacy')",
        (str(tmp_path / "source.txt"),),
    )
    connection.commit()
    connection.close()

    store = HistoryStore(db_path=db_path)
    try:
        assert store._conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
        assert store._conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
        batch_columns = {
            str(row[1])
            for row in store._conn.execute("PRAGMA table_info(batches)")
        }
        operation_columns = {
            str(row[1])
            for row in store._conn.execute("PRAGMA table_info(operations)")
        }
        assert {"status", "planned_count", "success_count"} <= batch_columns
        assert {"status", "error", "recovery_state"} <= operation_columns
    finally:
        store.close()


def test_legacy_batches_only_schema_is_completed_atomically(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "batches-only.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE batches("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL, root TEXT NOT NULL, "
        "undone INTEGER NOT NULL DEFAULT 0, "
        "status TEXT NOT NULL DEFAULT 'completed')"
    )
    connection.execute(
        "INSERT INTO batches(created_at, root, undone, status) "
        "VALUES ('2026-09-18T00:00:00', '/tmp/legacy', 0, 'completed')"
    )
    connection.commit()
    connection.close()

    store = HistoryStore(db_path=db_path)
    try:
        tables = {
            str(row[0])
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        batch_columns = {
            str(row[1])
            for row in store._conn.execute("PRAGMA table_info(batches)")
        }
        assert "operations" in tables
        assert {"planned_count", "success_count"} <= batch_columns
        assert store._conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
    finally:
        store.close()


def test_failed_initial_migration_rolls_back_all_schema_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "rollback.db"

    def fail_migration(store: HistoryStore) -> None:
        store._conn.execute("ALTER TABLE batches ADD COLUMN transient TEXT")
        raise RuntimeError("forced migration failure")

    monkeypatch.setattr(HistoryStore, "_migrate_schema", fail_migration)

    with pytest.raises(RuntimeError, match="forced migration failure"):
        HistoryStore(db_path=db_path)

    assert _user_objects(db_path) == []
