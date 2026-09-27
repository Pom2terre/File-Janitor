"""4E-27C: actionnabilité fail-closed des batches incohérents."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application import (
    get_latest_undoable_batch_id,
    resolve_history_recovery_without_file_action,
)
from file_janitor.executor import undo_batch
from file_janitor.gui.main_window import MainWindow
from file_janitor.recovery import (
    resolve_unresolved_planned_operations_without_file_action,
    run_crash_recovery,
)
from file_janitor.storage.history import (
    HistoryStore,
    OperationStatus,
    PlannedRecoveryState,
)


def _create_batch(
    tmp_path: Path,
    *,
    batch_status: str,
    undone: int,
    planned_count: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    operation_statuses: tuple[str, ...],
) -> tuple[Path, int, tuple[int, ...]]:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_ids = tuple(
        store.record_operation(
            batch_id,
            kind="move",
            original_path=tmp_path / f"source-{index}.txt",
            stored_path=tmp_path / f"stored-{index}.txt",
            size=1,
            category="to_sort",
            status=OperationStatus.PLANNED,
        )
        for index, _status in enumerate(operation_statuses)
    )
    with store._cursor() as cursor:
        for operation_id, operation_status in zip(
            operation_ids, operation_statuses, strict=True
        ):
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
    store.close()
    return db_path, batch_id, operation_ids


def _rows(db_path: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    connection = sqlite3.connect(db_path)
    try:
        return (
            connection.execute("SELECT * FROM batches ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM operations ORDER BY id").fetchall(),
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    (
        "undone",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
        "operation_statuses",
        "expected_issues",
    ),
    (
        pytest.param(
            0, 9, 4, 3, 2, ("completed",),
            ("operation_count_mismatch", "result_count_mismatch"),
            id="operation-and-result-counts",
        ),
        pytest.param(
            0, 1, 0, 0, 0, ("completed",),
            ("result_count_mismatch",),
            id="result-count",
        ),
        pytest.param(
            0, 2, 2, 0, 0, ("completed", "planned"),
            ("pending_operation_in_terminal_batch",),
            id="pending-terminal-operation",
        ),
        pytest.param(
            1, 1, 1, 0, 0, ("completed",),
            ("undo_state_mismatch",),
            id="undo-state",
        ),
    ),
)
def test_latest_undo_target_excludes_inconsistent_batches(
    tmp_path: Path,
    undone: int,
    planned_count: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    operation_statuses: tuple[str, ...],
    expected_issues: tuple[str, ...],
) -> None:
    db_path, batch_id, _operation_ids = _create_batch(
        tmp_path,
        batch_status="completed",
        undone=undone,
        planned_count=planned_count,
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        operation_statuses=operation_statuses,
    )
    store = HistoryStore(db_path=db_path)
    assert store.get_batch_consistency_issues(batch_id) == expected_issues
    assert store.latest_undoable_batch_id() is None
    store.close()
    assert get_latest_undoable_batch_id(db_path=db_path) is None


def test_consistent_batch_remains_latest_undo_target(tmp_path: Path) -> None:
    db_path, batch_id, _operation_ids = _create_batch(
        tmp_path,
        batch_status="completed",
        undone=0,
        planned_count=1,
        success_count=1,
        failed_count=0,
        skipped_count=0,
        operation_statuses=("completed",),
    )

    assert get_latest_undoable_batch_id(db_path=db_path) == batch_id


def test_direct_undo_refuses_inconsistent_batch_without_mutation(
    tmp_path: Path,
) -> None:
    db_path, batch_id, _operation_ids = _create_batch(
        tmp_path,
        batch_status="completed",
        undone=0,
        planned_count=9,
        success_count=4,
        failed_count=3,
        skipped_count=2,
        operation_statuses=("completed",),
    )
    source = tmp_path / "source-0.txt"
    stored = tmp_path / "stored-0.txt"
    source.write_text("source", encoding="utf-8")
    stored.write_text("stored", encoding="utf-8")
    before = _rows(db_path)

    store = HistoryStore(db_path=db_path)
    success, errors = undo_batch(batch_id, store)
    store.close()

    assert success == 0
    assert len(errors) == 1
    assert "incohérence batch/opérations" in errors[0]
    assert "undo refusé par sécurité" in errors[0]
    assert source.read_text(encoding="utf-8") == "source"
    assert stored.read_text(encoding="utf-8") == "stored"
    assert _rows(db_path) == before


def _inconsistent_running_recovery(
    tmp_path: Path,
) -> tuple[Path, int, int]:
    db_path, batch_id, operation_ids = _create_batch(
        tmp_path,
        batch_status="running",
        undone=1,
        planned_count=1,
        success_count=0,
        failed_count=0,
        skipped_count=0,
        operation_statuses=("planned",),
    )
    (tmp_path / "source-0.txt").write_text("source", encoding="utf-8")
    return db_path, batch_id, operation_ids[0]


def test_crash_recovery_skips_inconsistent_batch_without_mutation(
    tmp_path: Path,
) -> None:
    db_path, _batch_id, _operation_id = _inconsistent_running_recovery(tmp_path)
    before = _rows(db_path)
    store = HistoryStore(db_path=db_path)

    report = run_crash_recovery(store)
    store.close()

    assert report.promoted_operation_ids == ()
    assert report.failed_operation_ids == ()
    assert report.annotated_operation_ids == ()
    assert _rows(db_path) == before


def _annotated_inconsistent_recovery(
    tmp_path: Path,
) -> tuple[Path, int]:
    db_path, batch_id, operation_id = _inconsistent_running_recovery(tmp_path)
    store = HistoryStore(db_path=db_path)
    store.annotate_operation_recovery(
        operation_id,
        error="recovery ambiguë",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
    )
    store.close()
    return db_path, batch_id


def test_low_level_manual_recovery_refuses_inconsistent_batch(
    tmp_path: Path,
) -> None:
    db_path, batch_id = _annotated_inconsistent_recovery(tmp_path)
    before = _rows(db_path)
    store = HistoryStore(db_path=db_path)

    with pytest.raises(ValueError, match="incohérence batch/opérations"):
        resolve_unresolved_planned_operations_without_file_action(store, batch_id)
    store.close()

    assert _rows(db_path) == before


def test_application_manual_recovery_refuses_inconsistent_batch(
    tmp_path: Path,
) -> None:
    db_path, batch_id = _annotated_inconsistent_recovery(tmp_path)
    before = _rows(db_path)

    with pytest.raises(ValueError, match="incohérence batch/opérations"):
        resolve_history_recovery_without_file_action(batch_id, db_path=db_path)

    assert _rows(db_path) == before


@pytest.mark.parametrize(
    ("batch", "expected"),
    (
        pytest.param(None, False, id="missing-selection"),
        pytest.param(
            SimpleNamespace(consistency_issues=()), False, id="consistent"
        ),
        pytest.param(
            SimpleNamespace(consistency_issues=("undo_state_mismatch",)),
            True,
            id="inconsistent",
        ),
    ),
)
def test_gui_classifies_batch_consistency_block(
    batch: object,
    expected: bool,
) -> None:
    assert MainWindow._history_batch_has_consistency_issues(batch) is expected
