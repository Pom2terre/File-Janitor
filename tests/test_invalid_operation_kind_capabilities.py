"""4E-22A: un type d'opération persisté inconnu reste non actionnable."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.executor import undo_batch
from file_janitor.models import FileIdentity
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


def _identity(path: Path) -> FileIdentity:
    stat_result = path.stat()
    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def _corrupt_operation_kind(
    store: HistoryStore,
    operation_id: int,
    *,
    kind: str = "rename",
) -> None:
    with store._cursor() as cursor:
        cursor.execute(
            "UPDATE operations SET kind = ? WHERE id = ?",
            (kind, operation_id),
        )


@pytest.mark.parametrize(
    ("operation_status", "batch_status"),
    [
        (OperationStatus.COMPLETED, BatchStatus.COMPLETED),
        (OperationStatus.UNDO_FAILED, BatchStatus.UNDO_FAILED),
    ],
)
def test_undo_refuses_unknown_persisted_kind_without_file_action(
    tmp_path: Path,
    operation_status: OperationStatus,
    batch_status: BatchStatus,
) -> None:
    original = tmp_path / "original.txt"
    stored = tmp_path / "stored.txt"
    stored.write_text("STORED", encoding="utf-8")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=original,
        stored_path=stored,
        size=stored.stat().st_size,
        category="to_sort",
        status=operation_status,
    )
    _corrupt_operation_kind(store, operation_id)
    store.set_operation_stored_identity(operation_id, _identity(stored))
    store.finish_batch_execution(
        batch_id,
        batch_status,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "type d’opération persisté inconnu" in errors[0]
    assert "undo refusé par sécurité" in errors[0]
    assert not original.exists()
    assert stored.read_text(encoding="utf-8") == "STORED"

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDO_FAILED
    assert operation.error is not None
    assert "type d’opération persisté inconnu" in operation.error
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_FAILED
    store.close()


def test_recovery_keeps_unknown_persisted_kind_ambiguous(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.txt"
    original.write_text("ORIGINAL", encoding="utf-8")
    stored = tmp_path / "stored.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=original,
        stored_path=stored,
        size=original.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    _corrupt_operation_kind(store, operation_id)

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    assert inspections[0].operation_id == operation_id
    assert inspections[0].state is PlannedRecoveryState.AMBIGUOUS
    assert resolve_no_published_planned_operations(store) == []
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert original.read_text(encoding="utf-8") == "ORIGINAL"
    assert not stored.exists()
    store.close()
