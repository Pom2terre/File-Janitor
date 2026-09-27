"""4E-24C: scalaires persistés fail-closed aux frontières actionnables."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.application.service import (
    resolve_history_recovery_without_file_action,
)
from file_janitor.executor import undo_batch
from file_janitor.models import FileIdentity
from file_janitor.recovery import inspect_unidentified_planned_operations
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
    PlannedRecoveryState,
)


def _record(
    store: HistoryStore,
    batch_id: int,
    root: Path,
    *,
    size: int = 1,
    category: str = "to_sort",
    original_path: Path | None = None,
    stored_path: Path | None = None,
    status: OperationStatus = OperationStatus.COMPLETED,
) -> int:
    return store.record_operation(
        batch_id,
        kind="move",
        original_path=(
            root / "original.txt" if original_path is None else original_path
        ),
        stored_path=(
            root / "stored.txt" if stored_path is None else stored_path
        ),
        size=size,
        category=category,
        status=status,
    )


def _corrupt_scalar(
    store: HistoryStore,
    operation_id: int,
    column: str,
    value: object,
) -> None:
    assert column in {"size", "category"}
    with store._cursor() as cursor:
        cursor.execute(
            f"UPDATE operations SET {column} = ? WHERE id = ?",
            (value, operation_id),
        )


def _identity(path: Path) -> FileIdentity:
    stat_result = path.stat()
    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("size", -1),
        ("size", "not-an-integer"),
        ("size", True),
        ("category", ""),
        ("category", "   "),
        ("category", b"not-text"),
    ),
)
def test_record_operation_rejects_invalid_scalars_before_insert(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    kwargs: dict[str, object] = {"size": 1, "category": "to_sort"}
    kwargs[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        store.record_operation(
            batch_id,
            kind="move",
            original_path=tmp_path / "original.txt",
            stored_path=tmp_path / "stored.txt",
            size=kwargs["size"],  # type: ignore[arg-type]
            category=kwargs["category"],  # type: ignore[arg-type]
            status=OperationStatus.COMPLETED,
        )

    assert store.get_operations(batch_id) == []
    store.close()


def test_record_operation_accepts_zero_size_and_nonempty_category(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)

    operation_id = _record(
        store,
        batch_id,
        tmp_path,
        size=0,
        category="custom",
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.size == 0
    assert operation.category == "custom"
    assert operation.size_raw is None
    assert operation.category_raw is None
    store.close()


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (
        ("size", -1),
        ("size", b"not-an-integer"),
        ("category", ""),
        ("category", b"not-text"),
    ),
)
def test_latest_undoable_batch_skips_invalid_persisted_scalars(
    tmp_path: Path,
    column: str,
    invalid_value: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    valid_batch = store.start_batch(str(tmp_path), planned_count=1)
    _record(store, valid_batch, tmp_path)
    store.finish_batch_execution(
        valid_batch,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    invalid_batch = store.start_batch(str(tmp_path), planned_count=1)
    invalid_id = _record(store, invalid_batch, tmp_path)
    store.finish_batch_execution(
        invalid_batch,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )
    _corrupt_scalar(store, invalid_id, column, invalid_value)

    assert invalid_batch > valid_batch
    assert store.latest_undoable_batch_id() == valid_batch
    store.close()


def test_latest_undoable_batch_skips_mixed_invalid_scalar_batch(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    valid_batch = store.start_batch(str(tmp_path), planned_count=1)
    _record(store, valid_batch, tmp_path)
    store.finish_batch_execution(
        valid_batch,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    mixed_batch = store.start_batch(str(tmp_path), planned_count=2)
    _record(
        store,
        mixed_batch,
        tmp_path,
        original_path=tmp_path / "valid-original.txt",
        stored_path=tmp_path / "valid-stored.txt",
    )
    invalid_id = _record(
        store,
        mixed_batch,
        tmp_path,
        original_path=tmp_path / "invalid-original.txt",
        stored_path=tmp_path / "invalid-stored.txt",
    )
    store.finish_batch_execution(
        mixed_batch,
        BatchStatus.COMPLETED,
        success_count=2,
        failed_count=0,
        skipped_count=0,
    )
    _corrupt_scalar(store, invalid_id, "size", -1)

    assert store.latest_undoable_batch_id() == valid_batch
    store.close()


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (("size", -1), ("category", "")),
)
def test_recovery_classifies_invalid_scalars_as_ambiguous(
    tmp_path: Path,
    column: str,
    invalid_value: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record(
        store,
        batch_id,
        tmp_path,
        status=OperationStatus.PLANNED,
    )
    _corrupt_scalar(store, operation_id, column, invalid_value)

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    assert inspections[0].operation_id == operation_id
    assert inspections[0].state is PlannedRecoveryState.AMBIGUOUS
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    store.close()


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (("size", -1), ("category", "")),
)
def test_manual_recovery_resolution_rejects_invalid_scalars(
    tmp_path: Path,
    column: str,
    invalid_value: object,
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record(
        store,
        batch_id,
        tmp_path,
        status=OperationStatus.PLANNED,
    )
    with store._cursor() as cursor:
        cursor.execute(
            (
                f"UPDATE operations SET {column} = ?, "
                "recovery_state = ? WHERE id = ?"
            ),
            (invalid_value, "ambiguous", operation_id),
        )
    store.close()

    with pytest.raises(ValueError, match="métadonnées scalaires incohérentes"):
        resolve_history_recovery_without_file_action(
            batch_id,
            db_path=db_path,
        )

    store = HistoryStore(db_path=db_path)
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    (("size", -1), ("category", "")),
)
def test_direct_undo_refuses_invalid_scalars_without_file_action(
    tmp_path: Path,
    column: str,
    invalid_value: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    original_path = tmp_path / "original.txt"
    stored_path = tmp_path / "stored.txt"
    stored_path.write_text("payload", encoding="utf-8")
    operation_id = _record(
        store,
        batch_id,
        tmp_path,
        original_path=original_path,
        stored_path=stored_path,
    )
    store.set_operation_stored_identity(operation_id, _identity(stored_path))
    store.finish_batch_execution(
        batch_id,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )
    _corrupt_scalar(store, operation_id, column, invalid_value)

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "métadonnées scalaires persistées invalides" in errors[0]
    assert stored_path.read_text(encoding="utf-8") == "payload"
    assert not original_path.exists()
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDO_FAILED
    assert store.get_batch(batch_id).status is BatchStatus.UNDO_FAILED
    store.close()
