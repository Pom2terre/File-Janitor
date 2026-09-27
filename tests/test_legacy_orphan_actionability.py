"""4E-26B: neutralisation des opérations orphelines historiques."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.application.service import (
    resolve_history_recovery_without_file_action,
    undo_execution,
)
from file_janitor.executor import undo_batch
from file_janitor.storage.history import HistoryStore, OperationStatus


def _initialize_database(db_path: Path) -> None:
    store = HistoryStore(db_path=db_path)
    store.close()


def _insert_completed_copy_orphan(
    db_path: Path,
    root: Path,
    *,
    batch_id: int = 900_001,
) -> tuple[int, Path]:
    _initialize_database(db_path)
    stored = root / "orphan-copy.txt"
    stored.write_text("orphan-copy", encoding="utf-8")
    identity = stored.stat()

    legacy = sqlite3.connect(db_path)
    assert legacy.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    cursor = legacy.execute(
        """
        INSERT INTO operations (
            batch_id, kind, original_path, stored_path,
            size, category, status, error,
            stored_device, stored_inode, stored_size, stored_mtime_ns
        )
        VALUES (?, 'copy', ?, ?, ?, 'documents', 'completed', NULL, ?, ?, ?, ?)
        """,
        (
            batch_id,
            str(root / "orphan-original.txt"),
            str(stored),
            identity.st_size,
            identity.st_dev,
            identity.st_ino,
            identity.st_size,
            identity.st_mtime_ns,
        ),
    )
    assert cursor.lastrowid is not None
    operation_id = int(cursor.lastrowid)
    legacy.commit()
    legacy.close()
    return operation_id, stored


def _insert_planned_recovery_orphan(
    db_path: Path,
    root: Path,
    *,
    batch_id: int = 900_002,
) -> int:
    _initialize_database(db_path)
    legacy = sqlite3.connect(db_path)
    cursor = legacy.execute(
        """
        INSERT INTO operations (
            batch_id, kind, original_path, stored_path,
            size, category, status, error, recovery_state
        )
        VALUES (?, 'move', ?, ?, 1, 'documents', 'planned', ?, 'ambiguous')
        """,
        (
            batch_id,
            str(root / "recovery-original.txt"),
            str(root / "recovery-stored.txt"),
            "récupération après crash : état filesystem ambigu ; "
            "opération laissée PLANNED, aucune action automatique",
        ),
    )
    assert cursor.lastrowid is not None
    operation_id = int(cursor.lastrowid)
    legacy.commit()
    legacy.close()
    return operation_id


def test_direct_undo_rejects_orphan_without_touching_file(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    operation_id, stored = _insert_completed_copy_orphan(db_path, tmp_path)
    store = HistoryStore(db_path=db_path)

    success, errors = undo_batch(900_001, store)

    assert success == 0
    assert any("introuvable" in error and "undo refusé" in error for error in errors)
    assert stored.read_text(encoding="utf-8") == "orphan-copy"
    operation = store.get_operations(900_001)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.COMPLETED
    store.close()


def test_application_undo_rejects_orphan_without_touching_file(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    operation_id, stored = _insert_completed_copy_orphan(db_path, tmp_path)

    result = undo_execution(900_001, db_path=db_path)

    assert result.success == 0
    assert any("introuvable" in error and "undo refusé" in error for error in result.errors)
    assert stored.read_text(encoding="utf-8") == "orphan-copy"
    store = HistoryStore(db_path=db_path)
    operation = store.get_operations(900_001)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.COMPLETED
    store.close()


def test_manual_recovery_keeps_rejecting_orphan(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    operation_id = _insert_planned_recovery_orphan(db_path, tmp_path)

    with pytest.raises(ValueError, match="batch d'historique introuvable"):
        resolve_history_recovery_without_file_action(900_002, db_path=db_path)

    store = HistoryStore(db_path=db_path)
    operation = store.get_operations(900_002)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.PLANNED
    assert operation.resolution_kind is None
    store.close()


def test_orphan_remains_visible_for_forensic_checks(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    operation_id, stored = _insert_completed_copy_orphan(db_path, tmp_path)
    store = HistoryStore(db_path=db_path)

    assert store.get_batch(900_001) is None
    operations = store.get_operations(900_001)
    assert [operation.id for operation in operations] == [operation_id]
    with store._cursor() as cursor:
        cursor.execute("PRAGMA foreign_key_check")
        violations = [tuple(row) for row in cursor.fetchall()]
    assert violations == [("operations", operation_id, "batches", 0)]
    assert stored.exists()
    store.close()
