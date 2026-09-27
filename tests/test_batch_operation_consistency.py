"""4E-27A: détection read-only des incohérences batch/opérations."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore, OperationStatus


def _batch_with_state(
    tmp_path: Path,
    *,
    batch_status: str,
    undone: int,
    planned_count: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    operation_status: str,
) -> tuple[HistoryStore, int]:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "original.txt",
        stored_path=tmp_path / "stored.txt",
        size=1,
        category="documents",
        status=OperationStatus.PLANNED,
    )
    with store._cursor() as cursor:
        cursor.execute(
            "UPDATE operations SET status = ? WHERE id = ?",
            (operation_status, operation_id),
        )
        cursor.execute(
            """
            UPDATE batches
            SET status = ?, undone = ?,
                planned_count = ?, success_count = ?,
                failed_count = ?, skipped_count = ?
            WHERE id = ?
            """,
            (
                batch_status,
                undone,
                planned_count,
                success_count,
                failed_count,
                skipped_count,
                batch_id,
            ),
        )
    return store, batch_id


def test_detects_operation_and_result_count_mismatches(tmp_path: Path) -> None:
    store, batch_id = _batch_with_state(
        tmp_path,
        batch_status="completed",
        undone=0,
        planned_count=9,
        success_count=4,
        failed_count=3,
        skipped_count=2,
        operation_status="completed",
    )

    assert store.get_batch_consistency_issues(batch_id) == (
        "operation_count_mismatch",
        "result_count_mismatch",
    )
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.planned_count == 9
    assert batch.success_count == 4
    assert batch.failed_count == 3
    assert batch.skipped_count == 2
    store.close()


def test_detects_pending_operation_in_terminal_batch(tmp_path: Path) -> None:
    store, batch_id = _batch_with_state(
        tmp_path,
        batch_status="completed",
        undone=0,
        planned_count=1,
        success_count=1,
        failed_count=0,
        skipped_count=0,
        operation_status="planned",
    )

    assert store.get_batch_consistency_issues(batch_id) == (
        "pending_operation_in_terminal_batch",
    )
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    store.close()


def test_detects_undo_state_mismatch(tmp_path: Path) -> None:
    store, batch_id = _batch_with_state(
        tmp_path,
        batch_status="undone",
        undone=0,
        planned_count=1,
        success_count=1,
        failed_count=0,
        skipped_count=0,
        operation_status="undone",
    )

    assert store.get_batch_consistency_issues(batch_id) == (
        "undo_state_mismatch",
    )
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.undone is False
    store.close()


@pytest.mark.parametrize(
    (
        "batch_status",
        "undone",
        "success_count",
        "operation_status",
    ),
    (
        pytest.param("running", 0, 0, "planned", id="running"),
        pytest.param("completed", 0, 1, "completed", id="completed"),
        pytest.param("undone", 1, 1, "undone", id="undone"),
    ),
)
def test_consistent_batches_have_no_cross_row_issues(
    tmp_path: Path,
    batch_status: str,
    undone: int,
    success_count: int,
    operation_status: str,
) -> None:
    store, batch_id = _batch_with_state(
        tmp_path,
        batch_status=batch_status,
        undone=undone,
        planned_count=1,
        success_count=success_count,
        failed_count=0,
        skipped_count=0,
        operation_status=operation_status,
    )

    assert store.get_batch_consistency_issues(batch_id) == ()
    store.close()
