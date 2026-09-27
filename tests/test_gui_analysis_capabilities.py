"""Tests 4E-8B : rendu GUI des catégories non analysées."""

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
    app = create_application(["janitor-gui-capabilities-test"])
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
            item_count=2,
            total_size=20,
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
            total_count=2,
            total_size=20,
            categories=categories,
        ),
        details=tuple(
            CategoryDetails(key=category.key, label=category.label, items=())
            for category in categories
        ),
        capabilities=AnalysisCapabilities(
            hashes_computed=hashes_computed,
            content_types_computed=content_types_computed,
        ),
    )


def test_remote_like_result_does_not_expose_diagnostic_categories(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    window._show_result(
        _result(
            tmp_path,
            hashes_computed=False,
            content_types_computed=False,
        )
    )

    # 4E-10I: le tableau principal est réservé aux groupes du mode de
    # classement actif. Les diagnostics globaux ne sont plus des lignes.
    assert window.category_table.rowCount() == 0
    assert window.duplicate_resolution_button.isHidden() is True


def test_standard_empty_diagnostics_do_not_pollute_classification_table(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    window._show_result(
        _result(
            tmp_path,
            hashes_computed=True,
            content_types_computed=True,
        )
    )

    assert window.category_table.rowCount() == 0
    # Aucun groupe de doublons réel : le workflow dédié reste masqué.
    assert window.duplicate_resolution_button.isHidden() is True


@pytest.mark.parametrize(
    ("hashes_computed", "content_types_computed"),
    (
        (True, False),
        (False, True),
    ),
)
def test_capabilities_do_not_reintroduce_global_diagnostic_rows(
    window: MainWindow,
    tmp_path: Path,
    hashes_computed: bool,
    content_types_computed: bool,
) -> None:
    window._show_result(
        _result(
            tmp_path,
            hashes_computed=hashes_computed,
            content_types_computed=content_types_computed,
        )
    )

    assert window.category_table.rowCount() == 0
