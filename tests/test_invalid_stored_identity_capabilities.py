"""4E-21C: capacités fail-closed face à stored_identity corrompue."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from file_janitor.application.service import (
    resolve_history_recovery_without_file_action,
)
from file_janitor.executor import undo_batch
from file_janitor.recovery import (
    inspect_unidentified_planned_operations,
    resolve_no_published_planned_operations,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
    PlannedRecoveryState,
)


def _record_operation(
    db: Path,
    *,
    root: Path,
    kind: str,
    original: Path,
    stored: Path,
    status: OperationStatus,
) -> tuple[int, int]:
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(root), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind=kind,
        original_path=original,
        stored_path=stored,
        size=1,
        category="to_sort",
        status=status,
    )
    if status is OperationStatus.COMPLETED:
        store.finish_batch_execution(
            batch_id,
            BatchStatus.COMPLETED,
            success_count=1,
            failed_count=0,
            skipped_count=0,
        )
    store.close()
    return batch_id, operation_id


def _corrupt_stored_identity(db: Path, operation_id: int) -> None:
    connection = sqlite3.connect(db)
    connection.execute(
        """
        UPDATE operations
        SET stored_device = ?,
            stored_inode = ?,
            stored_size = ?,
            stored_mtime_ns = ?
        WHERE id = ?
        """,
        (123, 456, "invalid-size", 789, operation_id),
    )
    connection.commit()
    connection.close()


def test_recovery_never_auto_resolves_invalid_stored_identity(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    original = tmp_path / "source.txt"
    original.write_text("SOURCE", encoding="utf-8")
    stored = tmp_path / "sorted/source.txt"
    batch_id, operation_id = _record_operation(
        db,
        root=tmp_path,
        kind="move",
        original=original,
        stored=stored,
        status=OperationStatus.PLANNED,
    )
    _corrupt_stored_identity(db, operation_id)

    store = HistoryStore(db_path=db)
    inspections = inspect_unidentified_planned_operations(store)
    assert len(inspections) == 1
    assert inspections[0].state is PlannedRecoveryState.AMBIGUOUS
    assert resolve_no_published_planned_operations(store) == []
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.stored_identity is None
    assert operation.stored_identity_raw is not None
    assert original.read_text(encoding="utf-8") == "SOURCE"
    assert not stored.exists()
    store.close()


def test_manual_recovery_refuses_invalid_stored_identity(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    original = tmp_path / "source.txt"
    original.write_text("SOURCE", encoding="utf-8")
    stored = tmp_path / "sorted/source.txt"
    batch_id, operation_id = _record_operation(
        db,
        root=tmp_path,
        kind="move",
        original=original,
        stored=stored,
        status=OperationStatus.PLANNED,
    )
    _corrupt_stored_identity(db, operation_id)

    with pytest.raises(ValueError, match="identité stockée incohérente"):
        resolve_history_recovery_without_file_action(batch_id, db_path=db)

    store = HistoryStore(db_path=db)
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert original.read_text(encoding="utf-8") == "SOURCE"
    assert not stored.exists()
    store.close()


@pytest.mark.parametrize("kind", ["copy", "move", "delete"])
def test_undo_refuses_invalid_stored_identity_without_file_action(
    tmp_path: Path,
    kind: str,
) -> None:
    db = tmp_path / "history.db"
    original = tmp_path / f"{kind}-original.txt"
    stored = tmp_path / f"{kind}-stored.txt"
    stored.write_text("STORED", encoding="utf-8")
    if kind == "copy":
        original.write_text("ORIGINAL", encoding="utf-8")

    batch_id, operation_id = _record_operation(
        db,
        root=tmp_path,
        kind=kind,
        original=original,
        stored=stored,
        status=OperationStatus.COMPLETED,
    )
    _corrupt_stored_identity(db, operation_id)

    store = HistoryStore(db_path=db)
    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "persistée invalide" in errors[0]
    assert stored.read_text(encoding="utf-8") == "STORED"
    if kind == "copy":
        assert original.read_text(encoding="utf-8") == "ORIGINAL"
    else:
        assert not original.exists()
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDO_FAILED
    assert operation.stored_identity is None
    assert operation.stored_identity_raw is not None
    store.close()
