"""Tests 4E-9B : détails explicites pour les catégories non analysées."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.application import (
    AnalysisCapabilities,
    AnalysisResult,
    AnalysisSummary,
    CategoryDetails,
    CategorySummary,
)
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["janitor-gui-unavailable-details-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _result(
    root: Path,
    *,
    hashes_computed: bool,
    content_types_computed: bool,
) -> AnalysisResult:
    categories = (
        CategorySummary(
            key="to_sort",
            label="Fichiers à classer",
            item_count=0,
            total_size=0,
            display_metric="count",
        ),
        CategorySummary(
            key="duplicate",
            label="Doublons",
            item_count=0,
            total_size=0,
            display_metric="size",
        ),
        CategorySummary(
            key="extension_mismatch",
            label="Extensions trompeuses",
            item_count=0,
            total_size=0,
            display_metric="count",
        ),
    )
    return AnalysisResult(
        summary=AnalysisSummary(
            root=root,
            total_count=0,
            total_size=0,
            categories=categories,
        ),
        details=tuple(
            CategoryDetails(
                key=category.key,
                label=category.label,
                items=(),
            )
            for category in categories
        ),
        capabilities=AnalysisCapabilities(
            hashes_computed=hashes_computed,
            content_types_computed=content_types_computed,
        ),
    )


@pytest.mark.parametrize(
    ("key", "hashes_computed", "content_types_computed", "expected"),
    (
        ("duplicate", False, True, False),
        ("duplicate", True, False, True),
        ("extension_mismatch", True, False, False),
        ("extension_mismatch", False, True, True),
        ("to_sort", False, False, True),
    ),
)
def test_category_availability_follows_analysis_capabilities(
    window: MainWindow,
    tmp_path: Path,
    key: str,
    hashes_computed: bool,
    content_types_computed: bool,
    expected: bool,
) -> None:
    result = _result(
        tmp_path,
        hashes_computed=hashes_computed,
        content_types_computed=content_types_computed,
    )
    assert window._analysis_category_available(result, key) is expected


def test_unavailable_diagnostics_are_not_rows_in_classification_table(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    result = _result(
        tmp_path,
        hashes_computed=False,
        content_types_computed=False,
    )
    window._show_result(result)

    assert window.category_table.rowCount() == 0
    assert window.duplicate_resolution_button.isHidden() is True


def test_analyzed_empty_diagnostics_stay_outside_classification_table(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    result = _result(
        tmp_path,
        hashes_computed=True,
        content_types_computed=True,
    )
    window._show_result(result)

    assert window.category_table.rowCount() == 0
    assert window.duplicate_resolution_button.isHidden() is True
