"""4E-24B: propagation et rendu des scalaires persistés invalides."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application.service import (
    _history_operation_core_metadata_issues,
    get_history_operation_summary,
)
from file_janitor.formatting import human_size
from file_janitor.gui.main_window import MainWindow
from file_janitor.storage.history import HistoryStore, OperationStatus


def _summary_with_scalars(
    tmp_path: Path,
    *,
    size: object = 123,
    category: object = "to_sort",
):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "original.txt",
        stored_path=tmp_path / "stored.txt",
        size=123,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    with store._cursor() as cursor:
        cursor.execute(
            "UPDATE operations SET size = ?, category = ? WHERE id = ?",
            (size, category, operation_id),
        )
    store.close()
    return get_history_operation_summary(batch_id, db_path=db_path)[0]


def test_valid_scalars_are_propagated_without_issues(tmp_path: Path) -> None:
    summary = _summary_with_scalars(tmp_path)

    assert summary.size == 123
    assert summary.category == "to_sort"
    assert summary.size_raw is None
    assert summary.category_raw is None
    assert summary.core_metadata_issues == ()


@pytest.mark.parametrize("invalid_size", (-1, b"invalid-size"))
def test_history_summary_preserves_and_classifies_invalid_size(
    tmp_path: Path,
    invalid_size: object,
) -> None:
    summary = _summary_with_scalars(tmp_path, size=invalid_size)

    assert summary.size is None
    assert summary.size_raw == invalid_size
    assert summary.category == "to_sort"
    assert summary.category_raw is None
    assert summary.core_metadata_issues == ("invalid_size",)


@pytest.mark.parametrize("invalid_category", ("", b"invalid-category"))
def test_history_summary_preserves_and_classifies_invalid_category(
    tmp_path: Path,
    invalid_category: object,
) -> None:
    summary = _summary_with_scalars(tmp_path, category=invalid_category)

    assert summary.size == 123
    assert summary.size_raw is None
    assert summary.category is None
    assert summary.category_raw == invalid_category
    assert summary.core_metadata_issues == ("invalid_category",)


def test_scalar_issues_compose_with_existing_core_metadata_issues() -> None:
    operation = SimpleNamespace(
        status_raw="future-status",
        kind="future-kind",
        original_path_raw="",
        stored_path_raw=b"invalid-path",
        size_raw=-1,
        category_raw=b"invalid-category",
    )

    assert _history_operation_core_metadata_issues(operation) == (
        "unknown_status",
        "unknown_kind",
        "invalid_original_path",
        "invalid_stored_path",
        "invalid_size",
        "invalid_category",
    )


def test_gui_labels_invalid_scalar_metadata() -> None:
    assert MainWindow._history_core_metadata_issue_text(
        ("invalid_size", "invalid_category")
    ) == (
        "Métadonnées cœur incohérentes : taille invalide, catégorie invalide"
    )


def test_gui_scalar_text_distinguishes_valid_invalid_and_missing() -> None:
    assert MainWindow._history_size_text(1024, None) == human_size(1024)
    assert MainWindow._history_size_text(None, -1) == (
        "Valeur persistée invalide (-1)"
    )
    assert MainWindow._history_size_text(None, None) == "Taille indisponible"

    assert MainWindow._history_category_text("to_sort", None) == "to_sort"
    assert MainWindow._history_category_text(None, b"invalid") == (
        "Valeur persistée invalide (b'invalid')"
    )
    assert MainWindow._history_category_text(None, None) == (
        "Catégorie indisponible"
    )
