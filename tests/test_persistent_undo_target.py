from pathlib import Path

from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _record_completed(store: HistoryStore, batch_id: int, root: Path, name: str) -> int:
    return store.record_operation(
        batch_id,
        kind="move",
        original_path=root / f"{name}.txt",
        stored_path=root / "sorted" / f"{name}.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


def test_latest_undoable_batch_id_is_lifo_and_retryable(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)

    first = store.start_batch(str(tmp_path), planned_count=1)
    first_op = _record_completed(store, first, tmp_path, "first")
    store.finish_batch_execution(
        first, BatchStatus.COMPLETED,
        success_count=1, failed_count=0, skipped_count=0,
    )

    second = store.start_batch(str(tmp_path), planned_count=1)
    second_op = _record_completed(store, second, tmp_path, "second")
    store.finish_batch_execution(
        second, BatchStatus.COMPLETED,
        success_count=1, failed_count=0, skipped_count=0,
    )

    # Un état de crash non réconcilié ne doit jamais devenir une cible Undo.
    running = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, running, tmp_path, "running")

    assert store.latest_undoable_batch_id() == second

    store.set_operation_status(second_op, OperationStatus.UNDONE)
    store.set_batch_status(second, BatchStatus.UNDONE)
    assert store.latest_undoable_batch_id() == first

    store.set_operation_status(first_op, OperationStatus.UNDO_FAILED, error="blocked")
    store.set_batch_status(first, BatchStatus.UNDO_PARTIAL)
    assert store.latest_undoable_batch_id() == first

    store.close()


def test_latest_undoable_batch_id_returns_none_without_pending_mutation(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = _record_completed(store, batch_id, tmp_path, "done")
    store.set_operation_status(op_id, OperationStatus.UNDONE)
    store.set_batch_status(batch_id, BatchStatus.UNDONE)

    assert store.latest_undoable_batch_id() is None
    store.close()


def test_application_facade_restores_latest_undoable_batch(tmp_path: Path) -> None:
    from file_janitor.application import get_latest_undoable_batch_id

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, batch_id, tmp_path, "latest")
    store.finish_batch_execution(
        batch_id, BatchStatus.COMPLETED,
        success_count=1, failed_count=0, skipped_count=0,
    )
    store.close()

    assert get_latest_undoable_batch_id(db_path=db) == batch_id

def test_latest_undoable_batch_id_skips_unknown_batch_status(
    tmp_path: Path,
) -> None:
    import sqlite3

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)

    valid = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, valid, tmp_path, "valid")
    store.finish_batch_execution(
        valid,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    unknown = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, unknown, tmp_path, "unknown-batch")
    store.finish_batch_execution(
        unknown,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE batches SET status = ? WHERE id = ?",
        ("future_batch_status", unknown),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    assert store.latest_undoable_batch_id() == valid
    store.close()


def test_latest_undoable_batch_id_skips_unknown_operation_status(
    tmp_path: Path,
) -> None:
    import sqlite3

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)

    valid = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, valid, tmp_path, "valid")
    store.finish_batch_execution(
        valid,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    unknown = store.start_batch(str(tmp_path), planned_count=1)
    unknown_op = _record_completed(store, unknown, tmp_path, "unknown-operation")
    store.finish_batch_execution(
        unknown,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE operations SET status = ? WHERE id = ?",
        ("future_operation_status", unknown_op),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    assert store.latest_undoable_batch_id() == valid
    store.close()
