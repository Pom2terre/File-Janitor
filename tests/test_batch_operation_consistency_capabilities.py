"""4E-27B: propagation et rendu des incohérences batch/opérations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application import get_history_summary
from file_janitor.gui.main_window import MainWindow
from file_janitor.storage.history import HistoryStore, OperationStatus


def _summary_for_state(
    tmp_path: Path,
    *,
    batch_status: str,
    undone: int,
    planned_count: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    operation_status: str,
):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
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
    store.close()

    connection = sqlite3.connect(db_path)
    before = (
        connection.execute("SELECT * FROM batches ORDER BY id").fetchall(),
        connection.execute("SELECT * FROM operations ORDER BY id").fetchall(),
    )
    connection.close()

    summary = get_history_summary(db_path=db_path)[0]

    connection = sqlite3.connect(db_path)
    after = (
        connection.execute("SELECT * FROM batches ORDER BY id").fetchall(),
        connection.execute("SELECT * FROM operations ORDER BY id").fetchall(),
    )
    connection.close()
    assert after == before
    return summary


@pytest.mark.parametrize(
    (
        "batch_status",
        "undone",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
        "operation_status",
        "expected",
    ),
    (
        pytest.param(
            "completed", 0, 9, 4, 3, 2, "completed",
            ("operation_count_mismatch", "result_count_mismatch"),
            id="counters",
        ),
        pytest.param(
            "completed", 0, 1, 1, 0, 0, "planned",
            ("pending_operation_in_terminal_batch",),
            id="pending-terminal-operation",
        ),
        pytest.param(
            "undone", 0, 1, 1, 0, 0, "undone",
            ("undo_state_mismatch",),
            id="undo-state",
        ),
        pytest.param(
            "completed", 0, 1, 1, 0, 0, "completed", (),
            id="consistent",
        ),
    ),
)
def test_history_summary_surfaces_consistency_issues_without_repair(
    tmp_path: Path,
    batch_status: str,
    undone: int,
    planned_count: int,
    success_count: int,
    failed_count: int,
    skipped_count: int,
    operation_status: str,
    expected: tuple[str, ...],
) -> None:
    summary = _summary_for_state(
        tmp_path,
        batch_status=batch_status,
        undone=undone,
        planned_count=planned_count,
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        operation_status=operation_status,
    )

    assert summary.consistency_issues == expected


def test_gui_labels_batch_operation_consistency_issues() -> None:
    assert MainWindow._history_batch_consistency_issue_text(
        (
            "operation_count_mismatch",
            "result_count_mismatch",
            "pending_operation_in_terminal_batch",
            "undo_state_mismatch",
        )
    ) == (
        "Cohérence batch/opérations : nombre d’opérations incohérent, "
        "bilan incohérent avec les opérations, "
        "opération planifiée dans un batch terminé, "
        "état d’annulation incohérent"
    )


def test_gui_combines_core_and_cross_row_batch_diagnostics() -> None:
    batch = SimpleNamespace(
        core_metadata_issues=("unknown_status",),
        consistency_issues=("undo_state_mismatch",),
    )

    assert MainWindow._history_batch_issue_text(batch) == (
        "Métadonnées cœur incohérentes : état inconnu\n"
        "Cohérence batch/opérations : état d’annulation incohérent"
    )
