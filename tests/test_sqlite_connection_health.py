"""4E-28A: invariants de santé des connexions SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import file_janitor.storage.history as history_module
from file_janitor.storage.history import HistoryStore


def _pragma_int(store: HistoryStore, name: str) -> int:
    row = store._conn.execute(f"PRAGMA {name}").fetchone()
    assert row is not None
    return int(row[0])


def test_history_store_applies_explicit_connection_health_policy(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        assert _pragma_int(store, "foreign_keys") == 1
        assert _pragma_int(store, "busy_timeout") == 5_000
    finally:
        store.close()


def test_history_store_reapplies_policy_without_replacing_existing_data(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "existing.db"
    connection = sqlite3.connect(db_path, timeout=0.001)
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserved')")
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA busy_timeout = 1")
    connection.commit()
    connection.close()

    store = HistoryStore(db_path=db_path)
    try:
        assert _pragma_int(store, "foreign_keys") == 1
        assert _pragma_int(store, "busy_timeout") == 5_000
        row = store._conn.execute("SELECT value FROM marker").fetchone()
        assert row is not None
        assert row[0] == "preserved"
    finally:
        store.close()


def test_history_store_closes_connection_when_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "corrupt.db"
    original_bytes = b"not a sqlite database\x00file-janitor-4e28a"
    db_path.write_bytes(original_bytes)
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(history_module.sqlite3, "connect", tracking_connect)

    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        HistoryStore(db_path=db_path)

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")
    assert db_path.read_bytes() == original_bytes
