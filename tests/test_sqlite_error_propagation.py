"""4E-28C: propagation contrôlée des erreurs SQLite."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

import file_janitor.application as application
import file_janitor.application.service as service


_PUBLIC_MESSAGE = "L’historique SQLite est indisponible ou endommagé."


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corrupt_database(path: Path) -> None:
    path.write_bytes(b"not a sqlite database\x00file-janitor-4e28c")


def _incompatible_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE batches(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker VALUES ('preserve-me')")
    connection.commit()
    connection.close()


DatabaseFactory = Callable[[Path], None]
ApiCall = Callable[[Path, Path], object]

DATABASE_FACTORIES: tuple[tuple[str, DatabaseFactory], ...] = (
    ("corrupt", _corrupt_database),
    ("incompatible", _incompatible_database),
)

API_CALLS: tuple[tuple[str, ApiCall], ...] = (
    (
        "execute_actions",
        lambda db, root: service.execute_actions([], root=str(root), db_path=db),
    ),
    (
        "undo_execution",
        lambda db, _root: service.undo_execution(1, db_path=db),
    ),
    ("list_history", lambda db, _root: service.list_history(db_path=db)),
    (
        "get_history_operations",
        lambda db, _root: service.get_history_operations(1, db_path=db),
    ),
    (
        "run_startup_crash_recovery",
        lambda db, _root: service.run_startup_crash_recovery(db_path=db),
    ),
    (
        "resolve_history_recovery_without_file_action",
        lambda db, _root: service.resolve_history_recovery_without_file_action(
            1, db_path=db
        ),
    ),
    (
        "get_latest_undoable_batch_id",
        lambda db, _root: service.get_latest_undoable_batch_id(db_path=db),
    ),
    (
        "get_history_summary",
        lambda db, _root: service.get_history_summary(db_path=db),
    ),
)


@pytest.mark.parametrize(
    ("database_kind", "database_factory"),
    DATABASE_FACTORIES,
    ids=[name for name, _factory in DATABASE_FACTORIES],
)
@pytest.mark.parametrize(
    ("api_name", "api_call"),
    API_CALLS,
    ids=[name for name, _call in API_CALLS],
)
def test_application_apis_translate_database_open_failures_without_mutation(
    tmp_path: Path,
    database_kind: str,
    database_factory: DatabaseFactory,
    api_name: str,
    api_call: ApiCall,
) -> None:
    db_path = tmp_path / f"{database_kind}-{api_name}.db"
    database_factory(db_path)
    before = _digest(db_path)

    with pytest.raises(service.HistoryDatabaseError) as captured:
        api_call(db_path, tmp_path)

    assert str(captured.value) == _PUBLIC_MESSAGE
    assert isinstance(captured.value.__cause__, sqlite3.DatabaseError)
    assert _digest(db_path) == before


def test_history_database_error_is_part_of_public_application_api() -> None:
    assert application.HistoryDatabaseError is service.HistoryDatabaseError


def test_history_api_still_initializes_a_healthy_database(tmp_path: Path) -> None:
    db_path = tmp_path / "healthy.db"

    assert service.list_history(db_path=db_path) == []
    assert db_path.is_file()
