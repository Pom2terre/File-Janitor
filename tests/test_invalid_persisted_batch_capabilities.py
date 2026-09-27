"""4E-25B: propagation et rendu des métadonnées de batch invalides."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application.service import (
    _history_batch_core_metadata_issues,
    get_history_summary,
)
from file_janitor.gui.main_window import MainWindow
from file_janitor.storage.history import HistoryStore


def _summary_with_batch_metadata(
    tmp_path: Path,
    updates: dict[str, object] | None = None,
):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    if updates:
        allowed = {
            "root",
            "undone",
            "planned_count",
            "success_count",
            "failed_count",
            "skipped_count",
        }
        assert set(updates).issubset(allowed)
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with store._cursor() as cursor:
            cursor.execute(
                f"UPDATE batches SET {assignments} WHERE id = ?",
                (*updates.values(), batch_id),
            )
    store.close()
    return get_history_summary(limit=20, db_path=db_path)[0]


def test_valid_batch_metadata_is_propagated_without_issues(
    tmp_path: Path,
) -> None:
    summary = _summary_with_batch_metadata(tmp_path)

    assert summary.root == str(tmp_path)
    assert summary.root_raw is None
    assert summary.undone is False
    assert summary.undone_raw is None
    assert summary.planned_count == 1
    assert summary.success_count == 0
    assert summary.failed_count == 0
    assert summary.skipped_count == 0
    assert summary.planned_count_raw is None
    assert summary.success_count_raw is None
    assert summary.failed_count_raw is None
    assert summary.skipped_count_raw is None
    assert summary.core_metadata_issues == ()


@pytest.mark.parametrize(
    ("column", "value", "field", "raw_field", "issue"),
    (
        ("root", b"invalid-root", "root", "root_raw", "invalid_root"),
        ("undone", 2, "undone", "undone_raw", "invalid_undone"),
        (
            "planned_count", -1, "planned_count", "planned_count_raw",
            "invalid_planned_count",
        ),
        (
            "success_count", "bad", "success_count", "success_count_raw",
            "invalid_success_count",
        ),
        (
            "failed_count", b"bad", "failed_count", "failed_count_raw",
            "invalid_failed_count",
        ),
        (
            "skipped_count", -2, "skipped_count", "skipped_count_raw",
            "invalid_skipped_count",
        ),
    ),
)
def test_history_summary_preserves_and_classifies_invalid_batch_field(
    tmp_path: Path,
    column: str,
    value: object,
    field: str,
    raw_field: str,
    issue: str,
) -> None:
    summary = _summary_with_batch_metadata(tmp_path, {column: value})

    assert getattr(summary, field) is None
    assert getattr(summary, raw_field) == value
    assert summary.core_metadata_issues == (issue,)


def test_all_invalid_batch_fields_are_propagated_and_classified(
    tmp_path: Path,
) -> None:
    updates = {
        "root": b"invalid-root",
        "undone": 2,
        "planned_count": -1,
        "success_count": "bad-success",
        "failed_count": b"bad-failed",
        "skipped_count": -2,
    }
    summary = _summary_with_batch_metadata(tmp_path, updates)

    assert summary.root_raw == updates["root"]
    assert summary.undone_raw == updates["undone"]
    assert summary.planned_count_raw == updates["planned_count"]
    assert summary.success_count_raw == updates["success_count"]
    assert summary.failed_count_raw == updates["failed_count"]
    assert summary.skipped_count_raw == updates["skipped_count"]
    assert summary.core_metadata_issues == (
        "invalid_root",
        "invalid_undone",
        "invalid_planned_count",
        "invalid_success_count",
        "invalid_failed_count",
        "invalid_skipped_count",
    )


def test_inconsistent_valid_counts_are_classified(tmp_path: Path) -> None:
    summary = _summary_with_batch_metadata(
        tmp_path,
        {
            "planned_count": 1,
            "success_count": 2,
            "failed_count": 3,
            "skipped_count": 4,
        },
    )

    assert summary.core_metadata_issues == ("inconsistent_counts",)


def test_batch_issues_compose_with_existing_core_metadata_issues() -> None:
    batch = SimpleNamespace(
        created_at_raw="bad-date",
        status_raw="future-status",
        root_raw=b"bad-root",
        undone_raw=2,
        planned_count=None,
        success_count=None,
        failed_count=None,
        skipped_count=None,
        planned_count_raw=-1,
        success_count_raw="bad",
        failed_count_raw=b"bad",
        skipped_count_raw=-2,
    )

    assert _history_batch_core_metadata_issues(batch) == (
        "invalid_created_at",
        "unknown_status",
        "invalid_root",
        "invalid_undone",
        "invalid_planned_count",
        "invalid_success_count",
        "invalid_failed_count",
        "invalid_skipped_count",
    )


def test_gui_labels_invalid_batch_metadata() -> None:
    assert MainWindow._history_core_metadata_issue_text(
        ("invalid_root", "invalid_undone", "inconsistent_counts")
    ) == (
        "Métadonnées cœur incohérentes : racine de batch invalide, "
        "indicateur d’annulation invalide, bilan d’exécution incohérent"
    )


def test_gui_batch_root_text_distinguishes_valid_invalid_and_missing() -> None:
    assert MainWindow._history_batch_root_text("/tmp/root", None) == "/tmp/root"
    assert MainWindow._history_batch_root_text(None, b"bad") == (
        "Valeur persistée invalide (b'bad')"
    )
    assert MainWindow._history_batch_root_text(None, None) == (
        "Racine indisponible"
    )


def test_gui_execution_summary_handles_invalid_and_missing_counts() -> None:
    valid = SimpleNamespace(
        planned_count=2,
        success_count=1,
        failed_count=1,
        skipped_count=0,
        planned_count_raw=None,
        success_count_raw=None,
        failed_count_raw=None,
        skipped_count_raw=None,
    )
    invalid = SimpleNamespace(
        planned_count=None,
        success_count=0,
        failed_count=0,
        skipped_count=0,
        planned_count_raw=-1,
        success_count_raw=None,
        failed_count_raw=None,
        skipped_count_raw=None,
    )
    missing = SimpleNamespace(
        planned_count=None,
        success_count=None,
        failed_count=None,
        skipped_count=None,
        planned_count_raw=None,
        success_count_raw=None,
        failed_count_raw=None,
        skipped_count_raw=None,
    )

    assert MainWindow._history_execution_summary(valid) == (
        "1/2 réussie · 1 échec"
    )
    assert MainWindow._history_execution_summary(invalid) == (
        "Bilan persisté invalide (-1, None, None, None)"
    )
    assert MainWindow._history_execution_summary(missing) == (
        "Bilan indisponible"
    )
