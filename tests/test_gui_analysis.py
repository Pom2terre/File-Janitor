"""Tests du raccordement GUI vers la couche application."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from file_janitor.application import (
    AnalysisItem,
    AnalysisResult,
    AnalysisSummary,
    CategoryDetails,
    CategorySummary,
    ClassificationMode,
    DateGranularity,
    DuplicateGroup,
    DuplicateGroupMember,
    GroupBy,
)
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.models import ActionItem, ActionKind, FileCategory


def _wait_until(predicate, *, app, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout en attendant la fin de l'analyse GUI")
        app.processEvents()
        time.sleep(0.005)


def _result(tmp_path: Path) -> AnalysisResult:
    summary = AnalysisSummary(
        root=tmp_path,
        total_count=3,
        total_size=3072,
        categories=(
            CategorySummary(
                key="to_sort",
                label="Fichiers à classer",
                item_count=2,
                total_size=2048,
                display_metric="count",
            ),
            CategorySummary(
                key="duplicate",
                label="Doublons",
                item_count=1,
                total_size=1024,
                display_metric="size",
            ),
        ),
    )
    return AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "first.txt",
                        size=1024,
                        reason="à classer",
                        destination=tmp_path / "Documents" / "first.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "second.txt",
                        size=1024,
                        reason="à classer aussi",
                        destination=tmp_path / "Documents" / "second.txt",
                        action="move",
                    ),
                ),
            ),
            CategoryDetails(
                key="duplicate",
                label="Doublons",
                items=(
                    AnalysisItem(
                        path=tmp_path / "duplicate.txt",
                        size=1024,
                        reason="doublon",
                        destination=None,
                        action="trash",
                    ),
                ),
            ),
        ),
        _actions=(
            (
                "to_sort",
                0,
                ActionItem(
                    category=FileCategory.TO_SORT,
                    path=tmp_path / "first.txt",
                    size=1024,
                    reason="à classer",
                    destination=tmp_path / "Documents" / "first.txt",
                    action=ActionKind.MOVE,
                ),
            ),
            (
                "to_sort",
                1,
                ActionItem(
                    category=FileCategory.TO_SORT,
                    path=tmp_path / "second.txt",
                    size=1024,
                    reason="à classer aussi",
                    destination=tmp_path / "Documents" / "second.txt",
                    action=ActionKind.MOVE,
                ),
            ),
            (
                "duplicate",
                0,
                ActionItem(
                    category=FileCategory.DUPLICATE,
                    path=tmp_path / "duplicate.txt",
                    size=1024,
                    reason="doublon",
                    action=ActionKind.TRASH,
                ),
            ),
        ),
    )



def test_classification_mode_selector_exposes_four_product_strategies() -> None:
    create_application(["janitor-gui-classification-modes-test"])
    window = MainWindow()

    assert [
        window.classification_mode_combo.itemData(index)
        for index in range(window.classification_mode_combo.count())
    ] == [
        ClassificationMode.EXTENSION.value,
        ClassificationMode.DATE.value,
        ClassificationMode.COMMON_NAME.value,
        ClassificationMode.SIZE.value,
    ]
    assert window.secondary_mode_combo.currentData() is None
    assert [window.secondary_mode_combo.itemData(index) for index in range(1, 5)] == [
        ClassificationMode.EXTENSION,
        ClassificationMode.DATE,
        ClassificationMode.COMMON_NAME,
        ClassificationMode.SIZE,
    ]

    window.close()


def test_analysis_uses_selected_classification_mode(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-classification-selection-test"])
    window = MainWindow()
    result = _result(tmp_path)
    calls = []

    def analyze(folder: Path, *, config=None, **kwargs: object):
        calls.append((folder, config))
        return result

    monkeypatch.setattr(main_window_module, "analyze_folder", analyze)
    window.folder_edit.setText(str(tmp_path))
    window.classification_mode_combo.setCurrentIndex(1)
    window.analyze_button.setEnabled(True)

    window._start_analysis()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert calls[0][0] == tmp_path
    assert calls[0][1].group_by is GroupBy.DATE
    assert calls[0][1].date_granularity is DateGranularity.MONTH
    assert "AAAA-MM" in window.classification_mode_hint.text()

    window.close()



def test_partial_analysis_is_explicit_and_lists_scan_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-partial-scan-test"])
    window = MainWindow()
    base = _result(tmp_path)
    result = AnalysisResult(
        summary=base.summary,
        details=base.details,
        capabilities=base.capabilities,
        duplicate_groups=base.duplicate_groups,
        scan_errors=(
            f"{tmp_path / 'blocked.txt'}: Permission denied",
            f"{tmp_path / 'vanished.txt'}: No such file",
        ),
    )

    monkeypatch.setattr(
        main_window_module,
        "analyze_folder",
        lambda folder, *, config=None, **kwargs: result,
    )
    window.folder_edit.setText(str(tmp_path))
    window.analyze_button.setEnabled(True)

    window._start_analysis()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == (
        "Analyse Standard terminée avec 2 erreurs de scan — résultat partiel."
    )
    assert window.status_label.property("tone") == "warning"
    assert window.scan_errors_label.isHidden() is False
    assert window.scan_errors_label.text() == (
        "Analyse partielle : 2 erreurs de scan. "
        "Certaines entrées peuvent être absentes du résultat."
    )
    assert window.scan_errors_table.isHidden() is False
    assert window.scan_errors_table.rowCount() == 2
    assert "blocked.txt" in window.scan_errors_table.item(0, 0).text()
    assert "vanished.txt" in window.scan_errors_table.item(1, 0).text()

    # L'analyse partielle reste consultable : les résultats sûrs ne sont
    # pas jetés simplement parce qu'une autre entrée a échoué.
    assert window.summary_label.text() == "3 fichiers — 3.1 Ko"
    assert window.category_table.rowCount() == 2

    window.close()


def test_complete_analysis_hides_scan_error_ui(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-complete-scan-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))

    assert window.scan_errors_label.isHidden() is True
    assert window.scan_errors_table.isHidden() is True
    assert window.scan_errors_table.rowCount() == 0

    window.close()


def test_history_execution_summary_uses_persisted_batch_counts() -> None:
    cancelled = main_window_module.HistoryBatchSummary(
        id=7,
        created_at="2026-09-10 12:00",
        root="/tmp/example",
        status="cancelled",
        undone=False,
        planned_count=3,
        success_count=1,
        failed_count=0,
        skipped_count=2,
    )
    partial = main_window_module.HistoryBatchSummary(
        id=8,
        created_at="2026-09-10 12:01",
        root="/tmp/example",
        status="partial",
        undone=False,
        planned_count=3,
        success_count=2,
        failed_count=1,
        skipped_count=0,
    )
    legacy = main_window_module.HistoryBatchSummary(
        id=6,
        created_at="2026-09-09 12:00",
        root="/tmp/legacy",
        status="completed",
        undone=False,
    )

    assert MainWindow._history_execution_summary(cancelled) == (
        "1/3 réussie · 2 non démarrées"
    )
    assert MainWindow._history_execution_summary(partial) == (
        "2/3 réussies · 1 échec"
    )
    assert MainWindow._history_execution_summary(legacy) == "—"


def test_analysis_runs_in_background_and_updates_window(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-analysis-test"])
    window = MainWindow()
    result = _result(tmp_path)

    monkeypatch.setattr(
        main_window_module,
        "analyze_folder",
        lambda folder, *, config=None, **kwargs: result,
    )

    window.folder_edit.setText(str(tmp_path))
    window.analyze_button.setEnabled(True)
    window._start_analysis()

    assert window.browse_button.isEnabled() is False
    assert window.analyze_button.isEnabled() is False
    assert window.status_label.text() == "Analyse Standard en cours…"

    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == "Analyse Standard terminée."
    assert window.summary_label.text() == "3 fichiers — 3.1 Ko"
    assert window.category_table.rowCount() == 2
    assert window.category_table.item(0, 0).text() == "Tous les fichiers classés"
    assert window.category_table.item(0, 1).text() == "2"
    assert window.category_table.item(1, 0).text() == "Documents"
    assert window.category_table.item(1, 1).text() == "2"
    assert (
        window.details_label.text()
        == "Prévisualisation — Tous les fichiers classés (2 éléments)"
    )
    assert window.details_table.rowCount() == 2
    assert window.details_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert window.details_table.item(0, 1).text() == "Déplacer"
    assert window.details_table.item(0, 2).text() == str(tmp_path / "first.txt")
    assert window.details_table.item(0, 3).text() == "1.0 Ko"
    assert window.details_table.item(0, 4).text() == str(
        tmp_path / "Documents" / "first.txt"
    )
    assert window.details_table.item(0, 5).text() == "à classer"
    assert window.selection_label.text() == "Sélection globale : 0 / 2 actions"
    assert (
        window.preview_scope_summary.text()
        == "Périmètre : Tous les fichiers classés · 2 fichiers affichés"
    )
    assert window.select_all_button.text() == "Tout sélectionner (2)"
    assert window.select_all_button.isEnabled() is True
    assert window.clear_selection_button.text() == "Tout désélectionner (0)"
    assert window.clear_selection_button.isEnabled() is False
    assert window.browse_button.isEnabled() is True
    assert window.analyze_button.isEnabled() is False

    window.close()


def test_selecting_classification_group_updates_read_only_details(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-details-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    window.category_table.selectRow(1)
    app.processEvents()

    assert window.details_label.text() == "Prévisualisation — Documents (2 éléments)"
    assert window.details_table.rowCount() == 2
    assert window.details_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert window.details_table.item(0, 1).text() == "Déplacer"
    assert window.details_table.item(0, 2).text() == str(tmp_path / "first.txt")
    assert window.selection_label.text() == "Sélection globale : 0 / 2 actions"
    assert window.preview_scope_summary.text() == "Périmètre : Documents · 2 fichiers affichés"
    assert (
        window.select_all_button.text()
        == "Sélectionner cette vue (2)"
    )

    window.close()


def test_preview_category_selection_sets_current_cell_before_rendering(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-preview-current-cell-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    # Reproduce a fresh table with a selected row but no current index: the
    # preview renderer must not depend on Qt synchronizing those two states.
    window.category_table.clearSelection()
    window.category_table.setCurrentCell(-1, -1)

    window._select_preview_category(1)
    app.processEvents()

    assert window.category_table.currentRow() == 1
    assert window.details_label.text() == "Prévisualisation — Documents (2 éléments)"
    assert window.details_table.rowCount() == 2
    assert window.details_table.isHidden() is False

    window.close()


def test_analysis_error_is_reported_and_controls_are_restored(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-analysis-error-test"])
    window = MainWindow()
    messages: list[tuple[str, str]] = []

    def fail(folder: Path, *, config=None, **kwargs: object) -> None:
        raise ValueError("scan impossible")

    monkeypatch.setattr(main_window_module, "analyze_folder", fail)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )

    window.folder_edit.setText(str(tmp_path))
    window.analyze_button.setEnabled(True)
    window._start_analysis()

    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == "L'analyse Standard a échoué."
    assert messages == [("Erreur d'analyse", "scan impossible")]
    assert window.browse_button.isEnabled() is True
    assert window.analyze_button.isEnabled() is True

    window.close()


def test_new_folder_selection_clears_previous_results(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-folder-reset-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    assert window.category_table.rowCount() == 2
    assert window.details_table.rowCount() == 2

    next_folder = tmp_path / "next"
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(next_folder),
    )

    window._choose_folder()

    assert window.folder_edit.text() == str(next_folder)
    assert window.summary_label.text() == ""
    assert window.category_table.rowCount() == 0
    assert window.details_label.text() == ""
    assert window.details_table.rowCount() == 0
    assert window.details_table.isVisible() is False

    window.close()


def test_details_filter_hides_non_matching_rows(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-details-filter-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    window.details_filter.setText("second")
    app.processEvents()

    assert window.details_table.isRowHidden(0) is True
    assert window.details_table.isRowHidden(1) is False

    window.details_filter.clear()
    app.processEvents()

    assert window.details_table.isRowHidden(0) is False
    assert window.details_table.isRowHidden(1) is False

    window.close()


def test_details_filter_searches_destination_and_reason(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-details-filter-fields-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    window.details_filter.setText("à classer aussi")
    app.processEvents()
    assert window.details_table.isRowHidden(0) is True
    assert window.details_table.isRowHidden(1) is False

    window.details_filter.setText("Documents/first.txt")
    app.processEvents()
    assert window.details_table.isRowHidden(0) is False
    assert window.details_table.isRowHidden(1) is True

    window.details_filter.setText("Déplacer")
    app.processEvents()
    assert window.details_table.isRowHidden(0) is True
    assert window.details_table.isRowHidden(1) is True

    window.close()


def test_action_selection_is_explicit_and_persists_when_group_is_refreshed(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-selection-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    first_selection = window.details_table.item(0, 0)
    first_selection.setCheckState(Qt.CheckState.Checked)
    app.processEvents()

    assert window.selection_label.text() == "Sélection globale : 1 / 2 actions"

    window._show_selected_details()
    app.processEvents()
    assert window.details_table.item(0, 0).checkState() == Qt.CheckState.Checked

    window.close()


def test_select_all_only_selects_visible_actions(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-visible-selection-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    window.details_filter.setText("second")
    app.processEvents()
    assert window.select_all_button.text() == "Sélectionner les visibles (1)"
    assert (
        window.preview_scope_summary.text()
        == "Périmètre : Tous les fichiers classés · Recherche texte : « second » · 1 / 2 fichiers affichés"
    )
    assert window.clear_selection_button.text() == "Désélectionner les visibles (0)"
    assert window.clear_selection_button.isEnabled() is False

    window.select_all_button.click()
    app.processEvents()

    assert window.details_table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert window.details_table.item(1, 0).checkState() == Qt.CheckState.Checked
    assert window.selection_label.text() == "Sélection globale : 1 / 2 actions"
    assert window.select_all_button.isEnabled() is False
    assert window.clear_selection_button.text() == "Désélectionner les visibles (1)"
    assert window.clear_selection_button.isEnabled() is True

    window.clear_selection_button.click()
    app.processEvents()
    assert window.details_table.item(1, 0).checkState() == Qt.CheckState.Unchecked
    assert window.selection_label.text() == "Sélection globale : 0 / 2 actions"

    window.close()



def test_selection_scope_keeps_global_denominator_when_group_is_filtered(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-selection-scope-test"])
    window = MainWindow()

    summary = AnalysisSummary(
        root=tmp_path,
        total_count=3,
        total_size=300,
        categories=(
            CategorySummary(
                key="to_sort",
                label="Fichiers à classer",
                item_count=3,
                total_size=300,
                display_metric="count",
            ),
        ),
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "jan.txt",
                        size=100,
                        reason="Classer",
                        destination=tmp_path / "2025-01" / "jan.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "feb-a.txt",
                        size=100,
                        reason="Classer",
                        destination=tmp_path / "2025-02" / "feb-a.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "feb-b.txt",
                        size=100,
                        reason="Classer",
                        destination=tmp_path / "2025-02" / "feb-b.txt",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    window._show_result(result)
    app.processEvents()
    assert window.selection_label.text() == "Sélection globale : 0 / 3 actions"
    assert window.select_all_button.text() == "Tout sélectionner (3)"

    # Le groupe 2025-02 contient deux fichiers, mais le dénominateur reste
    # l'ensemble des trois actions de classement.
    window.category_table.selectRow(2)
    app.processEvents()
    assert window.details_table.rowCount() == 2
    assert window.selection_label.text() == "Sélection globale : 0 / 3 actions"
    assert window.preview_scope_summary.text() == "Périmètre : 2025-02 · 2 fichiers affichés"
    assert window.select_all_button.text() == "Sélectionner cette vue (2)"

    window.select_all_button.click()
    app.processEvents()
    assert window.selection_label.text() == "Sélection globale : 2 / 3 actions"
    assert window.select_all_button.isEnabled() is False
    assert window.clear_selection_button.text() == (
        "Désélectionner cette vue (2)"
    )

    window.close()



def test_preview_filter_explains_scope_and_is_accessible(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-filter-guidance-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    app.processEvents()

    assert window.details_filter_label.text() == "RECHERCHE TEXTE DANS LE PÉRIMÈTRE"
    assert (
        window.details_filter.accessibleName()
        == "Rechercher du texte dans les fichiers affichés"
    )
    assert "nom du fichier" in window.details_filter_hint.text()
    assert "Périmètre de prévisualisation" in window.details_filter_hint.text()
    assert window.preview_scope_label.text() == "PÉRIMÈTRE DE PRÉVISUALISATION"
    assert window.preview_scope_combo.isHidden() is False
    assert window.preview_scope_summary.isHidden() is False
    assert window.details_filter_label.isHidden() is False
    assert window.details_filter_hint.isHidden() is False

    window.close()




def test_preview_scope_combo_changes_classification_group_without_returning_to_analysis(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-preview-scope-combo-test"])
    window = MainWindow()

    summary = AnalysisSummary(
        root=tmp_path,
        total_count=3,
        total_size=3,
        categories=(),
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "a.xml",
                        size=1,
                        reason="Extension .xml, à classer",
                        destination=tmp_path / "xml" / "a.xml",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "b.xml",
                        size=1,
                        reason="Extension .xml, à classer",
                        destination=tmp_path / "xml" / "b.xml",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "c.c9r",
                        size=1,
                        reason="Extension .c9r, à classer",
                        destination=tmp_path / "c9r" / "c.c9r",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    window._show_result(result)
    app.processEvents()

    xml_index = window.preview_scope_combo.findData("classification_group:xml")
    assert xml_index >= 0
    window.preview_scope_combo.setCurrentIndex(xml_index)
    app.processEvents()

    assert window.details_label.text() == "Prévisualisation — xml (2 éléments)"
    assert window.details_table.rowCount() == 2
    assert window.category_table.currentRow() > 0
    assert window.preview_scope_summary.text() == "Périmètre : xml · 2 fichiers affichés"

    window.close()


def test_combined_groups_filter_by_both_levels(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-combined-group-test"])
    window = MainWindow()
    window.classification_mode_combo.setCurrentIndex(
        window.classification_mode_combo.findData(ClassificationMode.DATE)
    )
    window.secondary_mode_combo.setCurrentIndex(
        window.secondary_mode_combo.findData(ClassificationMode.EXTENSION)
    )
    result = AnalysisResult(
        summary=AnalysisSummary(root=tmp_path, total_count=3, total_size=3, categories=()),
        details=(CategoryDetails(
            key="to_sort", label="Fichiers à classer", items=tuple(
                AnalysisItem(
                    path=tmp_path / f"{month}-{name}", size=1, reason="tri",
                    destination=tmp_path / month / "mp4" / name, action="move",
                )
                for month, name in (("2025-01", "a.mp4"), ("2025-02", "b.mp4"), ("2025-02", "c.mp4"))
            ),
        ),),
    )
    window._show_result(result)
    app.processEvents()
    assert window.category_table.item(1, 0).text() == "2025-01/mp4"
    assert window.category_table.item(2, 0).text() == "2025-02/mp4"
    index = window.preview_scope_combo.findData("classification_group:2025-02/mp4")
    assert index >= 0
    window.preview_scope_combo.setCurrentIndex(index)
    app.processEvents()
    assert window.details_table.rowCount() == 2
    assert window.details_label.text() == "Prévisualisation — 2025-02/mp4 (2 éléments)"
    window.close()




def test_preview_scope_repopulation_clears_stale_hidden_rows(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-preview-stale-hidden-row-test"])
    window = MainWindow()

    summary = AnalysisSummary(
        root=tmp_path,
        total_count=3,
        total_size=3,
        categories=(),
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "first.mp4",
                        size=1,
                        reason="Extension .mp4, à classer",
                        destination=tmp_path / "mp4" / "first.mp4",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "second.mp4",
                        size=1,
                        reason="Extension .mp4, à classer",
                        destination=tmp_path / "mp4" / "second.mp4",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "other.pdf",
                        size=1,
                        reason="Extension .pdf, à classer",
                        destination=tmp_path / "pdf" / "other.pdf",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    window._show_result(result)
    app.processEvents()

    # Reproduit un état de ligne masquée laissé par un filtrage antérieur.
    window.details_table.setRowHidden(1, True)
    assert window.details_table.isRowHidden(1) is True

    mp4_index = window.preview_scope_combo.findData("classification_group:mp4")
    assert mp4_index >= 0
    window.preview_scope_combo.setCurrentIndex(mp4_index)
    app.processEvents()

    assert window.details_label.text() == "Prévisualisation — mp4 (2 éléments)"
    assert window.details_table.rowCount() == 2
    assert window.details_table.isRowHidden(0) is False
    assert window.details_table.isRowHidden(1) is False
    assert window.preview_scope_summary.text() == "Périmètre : mp4 · 2 fichiers affichés"
    assert window.select_all_button.text() == "Sélectionner cette vue (2)"

    window.close()


def test_exact_extension_search_from_global_scope_switches_to_extension_group(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-extension-search-scope-test"])
    window = MainWindow()

    summary = AnalysisSummary(root=tmp_path, total_count=2, total_size=2, categories=())
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "opaqueXMLtoken.c9r",
                        size=1,
                        reason="Extension .c9r, à classer",
                        destination=tmp_path / "c9r" / "opaqueXMLtoken.c9r",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "actual.xml",
                        size=1,
                        reason="Extension .xml, à classer",
                        destination=tmp_path / "xml" / "actual.xml",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    extension_index = window.classification_mode_combo.findData(
        ClassificationMode.EXTENSION.value
    )
    assert extension_index >= 0
    window.classification_mode_combo.setCurrentIndex(extension_index)
    window._show_result(result)
    window.details_filter.setText("xml")
    app.processEvents()

    assert window.details_filter.text() == ""
    assert window.preview_scope_combo.currentData() == "classification_group:xml"
    assert window.details_label.text() == "Prévisualisation — xml (1 élément)"
    assert window.details_table.rowCount() == 1
    assert window.details_table.item(0, 2).text().endswith("actual.xml")
    assert window.preview_scope_summary.text() == "Périmètre : xml · 1 fichier affiché"

    window.close()


def test_text_search_still_ignores_source_parent_directory_name(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-filter-basename-only-test"])
    window = MainWindow()

    summary = AnalysisSummary(root=tmp_path, total_count=2, total_size=2, categories=())
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "parent_workspace" / "opaque.c9r",
                        size=1,
                        reason="Extension .c9r, à classer",
                        destination=tmp_path / "c9r" / "opaque.c9r",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "workspace.xml",
                        size=1,
                        reason="Extension .xml, à classer",
                        destination=tmp_path / "xml" / "workspace.xml",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    window._show_result(result)
    window.details_filter.setText("workspace")
    app.processEvents()

    assert window.details_table.isRowHidden(0) is True
    assert window.details_table.isRowHidden(1) is False
    assert "Recherche texte : « workspace »" in window.preview_scope_summary.text()

    window.close()


def test_execution_requires_explicit_confirmation(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-execution-confirm-test"])
    window = MainWindow()
    calls = []

    window._show_result(_result(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        main_window_module,
        "execute_selected_actions",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    window.execute_button.click()
    app.processEvents()

    assert calls == []
    assert window._active_worker is None
    window.close()


def test_duplicate_execution_uses_preview_keeper_and_forwards_choice(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import ExecuteResult

    app = create_application(["janitor-gui-duplicate-keeper-test"])
    window = MainWindow()
    result = _result(tmp_path)
    window._show_result(result)
    window._selected_actions = {("duplicate", 0)}

    group = DuplicateGroup(
        key="samehash",
        members=(
            DuplicateGroupMember(path=tmp_path / "original.txt", size=1024),
            DuplicateGroupMember(path=tmp_path / "duplicate.txt", size=1024),
        ),
    )
    window._duplicate_keepers = {"samehash": group.members[1].path}
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module,
        "duplicate_groups_for_selection",
        lambda _result, selected: (group,),
    )
    calls = []

    def fake_execute(
        _result,
        selected,
        *,
        duplicate_keepers,
        progress_callback=None,
        cancel_callback=None,
    ):
        calls.append((set(selected), dict(duplicate_keepers)))
        return ExecuteResult(batch_id=91, success=2, errors=())

    monkeypatch.setattr(main_window_module, "execute_selected_actions", fake_execute)

    window._confirm_execution()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert calls == [
        ({("duplicate", 0)}, {"samehash": group.members[1].path})
    ]
    window.close()






def test_regular_confirmation_does_not_require_private_action_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-regular-confirmation-without-actions-test"])
    window = MainWindow()
    base = _result(tmp_path)
    result = AnalysisResult(
        summary=base.summary,
        details=base.details,
        capabilities=base.capabilities,
        duplicate_groups=(),
    )
    window._show_result(result)
    window._selected_actions = {("to_sort", 0)}

    questions = []
    warnings = []

    def question(parent, title, message, *args, **kwargs):
        questions.append((title, message))
        return main_window_module.QMessageBox.StandardButton.No

    def warning(parent, title, message, *args, **kwargs):
        warnings.append((title, message))
        return main_window_module.QMessageBox.StandardButton.Ok

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", warning)

    window._confirm_execution()

    assert warnings == []
    assert len(questions) == 1
    assert "1 mutation prévue" in questions[0][1]
    assert window._active_worker is None
    window.close()


def test_duplicate_confirmation_counts_resolved_mutations(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from file_janitor.application import build_analysis_result
    from file_janitor.models import FileRecord, Plan, ScanResult

    create_application(["janitor-gui-duplicate-mutation-count-test"])
    window = MainWindow()
    records = [
        FileRecord(
            path=tmp_path / name,
            size=4,
            mtime=datetime(2024, 1, day, tzinfo=timezone.utc),
            extension=".txt",
            hash="group3",
        )
        for day, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1)
    ]
    scan = ScanResult(root=tmp_path, files=records)
    plan = Plan(scan=scan)
    for record in records:
        plan.add(
            ActionItem(
                category=FileCategory.TO_SORT,
                path=record.path,
                size=4,
                reason="Classer",
                destination=tmp_path / "Sorted" / record.path.name,
                action=ActionKind.MOVE,
            )
        )
    for record in records[1:]:
        plan.add(
            ActionItem(
                category=FileCategory.DUPLICATE,
                path=record.path,
                size=4,
                reason="Doublon",
                action=ActionKind.TRASH,
            )
        )
    result = build_analysis_result(scan, plan)
    window._show_result(result)
    window._selected_actions = {("duplicate", 0), ("duplicate", 1)}
    window._duplicate_keepers = {"group3": records[1].path}

    questions = []

    def question(parent, title, message, *args, **kwargs):
        questions.append((title, message))
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)

    window._confirm_execution()

    assert questions
    assert "3 mutations prévues" in questions[0][1]
    assert window._active_worker is None
    window.close()


def test_unresolved_duplicate_selection_cancels_whole_execution(
    monkeypatch, tmp_path: Path
) -> None:
    app = create_application(["janitor-gui-duplicate-unresolved-test"])
    window = MainWindow()
    result = _result(tmp_path)
    window._show_result(result)
    window._selected_actions = {("duplicate", 0)}

    group = DuplicateGroup(
        key="samehash",
        members=(
            DuplicateGroupMember(path=tmp_path / "original.txt", size=1024),
            DuplicateGroupMember(path=tmp_path / "duplicate.txt", size=1024),
        ),
    )
    monkeypatch.setattr(
        main_window_module,
        "duplicate_groups_for_selection",
        lambda _result, selected: (group,),
    )
    messages = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda parent, title, message: messages.append((title, message)),
    )
    calls = []
    monkeypatch.setattr(
        main_window_module,
        "execute_selected_actions",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    window._confirm_execution()

    assert calls == []
    assert window._active_worker is None
    assert messages and messages[0][0] == "Résoudre les doublons"
    assert window._analysis_result is result
    window.close()


def test_clicking_duplicate_category_opens_resolution_dialog_and_selects_actions(
    monkeypatch, tmp_path: Path
) -> None:
    from PySide6.QtWidgets import QDialog
    from file_janitor.application import (
        AnalysisResult,
        AnalysisSummary,
        AnalysisCapabilities,
        CategoryDetails,
        CategorySummary,
        AnalysisItem,
    )

    app = create_application(["janitor-gui-duplicate-preview-dialog-test"])
    window = MainWindow()
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    group = DuplicateGroup(
        key="samehash",
        members=(
            DuplicateGroupMember(path=first, size=4),
            DuplicateGroupMember(path=second, size=4),
        ),
    )
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=tmp_path,
            total_count=2,
            total_size=8,
            categories=(
                CategorySummary(
                    key="duplicate",
                    label="Doublons",
                    item_count=1,
                    total_size=4,
                    display_metric="size",
                ),
            ),
        ),
        details=(
            CategoryDetails(
                key="duplicate",
                label="Doublons",
                items=(
                    AnalysisItem(
                        path=second,
                        size=4,
                        reason="Doublon",
                        destination=None,
                        action="trash",
                    ),
                ),
            ),
        ),
        capabilities=AnalysisCapabilities(),
        duplicate_groups=(group,),
        _actions=(
            (
                "duplicate",
                0,
                ActionItem(
                    category=FileCategory.DUPLICATE,
                    path=second,
                    size=4,
                    reason="Doublon",
                    action=ActionKind.TRASH,
                ),
            ),
        ),
    )
    window._show_result(result)

    captured = {}

    class FakeDialog:
        def __init__(self, groups, *, keepers=None, parent=None):
            captured["groups"] = groups
            captured["keepers"] = dict(keepers or {})
        def exec(self):
            return QDialog.DialogCode.Accepted
        def keepers(self):
            return {"samehash": second}

    monkeypatch.setattr(
        main_window_module, "DuplicateResolutionDialog", FakeDialog
    )

    assert window.duplicate_resolution_button.isHidden() is False
    window.duplicate_resolution_button.click()
    app.processEvents()

    assert captured["groups"] == (group,)
    assert window._duplicate_keepers == {"samehash": second}
    assert ("duplicate", 0) in window._selected_actions
    assert window.tabs.currentWidget() is window.preview_tab
    assert window.details_label.text() == "Prévisualisation — Doublons résolus (2 éléments)"
    assert window.details_table.rowCount() == 2
    assert window.details_table.item(0, 1).text() == "Conserver"
    assert window.details_table.item(0, 2).text() == str(second)
    assert window.details_table.item(1, 1).text() == "Corbeille"
    assert window.details_table.item(1, 2).text() == str(first)
    assert not (
        window.details_table.item(0, 0).flags()
        & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert not (
        window.details_table.item(1, 0).flags()
        & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert window.selection_label.text() == (
        "Doublons résolus : 1 groupe · 1 mutation prévue"
    )
    assert window.select_all_button.isHidden() is True
    assert window.clear_selection_button.isHidden() is True
    window.close()


def test_cancelling_duplicate_preview_dialog_keeps_preview_state_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    from PySide6.QtWidgets import QDialog
    from file_janitor.application import (
        AnalysisResult,
        AnalysisSummary,
        AnalysisCapabilities,
        CategoryDetails,
        CategorySummary,
        AnalysisItem,
    )

    app = create_application(["janitor-gui-duplicate-preview-cancel-test"])
    window = MainWindow()
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    group = DuplicateGroup(
        key="samehash",
        members=(
            DuplicateGroupMember(path=first, size=4),
            DuplicateGroupMember(path=second, size=4),
        ),
    )
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=tmp_path,
            total_count=2,
            total_size=8,
            categories=(
                CategorySummary(
                    key="duplicate",
                    label="Doublons",
                    item_count=1,
                    total_size=4,
                    display_metric="size",
                ),
            ),
        ),
        details=(
            CategoryDetails(
                key="duplicate",
                label="Doublons",
                items=(
                    AnalysisItem(
                        path=second,
                        size=4,
                        reason="Doublon",
                        destination=None,
                        action="trash",
                    ),
                ),
            ),
        ),
        capabilities=AnalysisCapabilities(),
        duplicate_groups=(group,),
    )
    window._show_result(result)

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass
        def exec(self):
            return QDialog.DialogCode.Rejected
        def keepers(self):
            raise AssertionError("keepers must not be read after cancel")

    monkeypatch.setattr(
        main_window_module, "DuplicateResolutionDialog", FakeDialog
    )

    assert window.duplicate_resolution_button.isHidden() is False
    window.duplicate_resolution_button.click()

    assert window._duplicate_keepers == {}
    assert window._selected_actions == set()
    assert window.tabs.currentWidget() is window.analysis_tab
    window.close()


def test_confirmed_execution_runs_in_background_and_clears_stale_preview(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import ExecuteResult

    app = create_application(["janitor-gui-execution-test"])
    window = MainWindow()
    calls = []

    window._show_result(_result(tmp_path))
    window.folder_edit.setText(str(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )

    def fake_execute(
        result, selected, *, progress_callback=None, cancel_callback=None
    ):
        calls.append((result, selected))
        return ExecuteResult(batch_id=12, success=1, errors=())

    monkeypatch.setattr(main_window_module, "execute_selected_actions", fake_execute)

    window.execute_button.click()
    assert window.status_label.text() == "Exécution en cours…"
    assert window.analyze_button.isEnabled() is False
    _wait_until(lambda: window._active_worker is None, app=app)

    assert len(calls) == 1
    assert calls[0][1] == {("to_sort", 0)}
    assert window.status_label.text() == "Exécution terminée."
    assert window.execution_summary_label.text() == (
        "Batch #12 — 1 action réussie, 0 erreurs."
    )
    assert window.execution_errors_table.rowCount() == 0
    assert window._analysis_result is None
    assert window.category_table.rowCount() == 0
    assert window.execute_button.isEnabled() is False
    assert window.analyze_button.isEnabled() is True
    window.close()


def test_partial_execution_keeps_structured_feedback_after_preview_is_cleared(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import ExecuteResult

    app = create_application(["janitor-gui-partial-execution-test"])
    window = MainWindow()
    window._show_result(_result(tmp_path))
    window.folder_edit.setText(str(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        main_window_module,
        "execute_selected_actions",
        lambda result, selected, **kwargs: ExecuteResult(
            batch_id=27,
            success=0,
            errors=("destination occupée", "source introuvable"),
        ),
    )

    window.execute_button.click()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == "Exécution terminée avec erreurs."
    assert window.execution_summary_label.text() == (
        "Batch #27 — 0 actions réussies, 2 erreurs."
    )
    assert window.execution_errors_table.rowCount() == 2
    assert window.execution_errors_table.item(0, 0).text() == "destination occupée"
    assert window.execution_errors_table.item(1, 0).text() == "source introuvable"
    assert window.execution_errors_table.item(0, 0).toolTip() == "destination occupée"
    assert warnings == [(
        "Exécution partielle",
        "Certaines actions n’ont pas pu être exécutées. "
        "Consultez les erreurs affichées dans la fenêtre.",
    )]
    assert window._analysis_result is None
    assert window.execute_button.isEnabled() is False

    window.close()




def test_zero_mutation_execution_preserves_previous_undo_target(
    monkeypatch,
) -> None:
    from file_janitor.application import ExecuteResult

    create_application(["janitor-gui-zero-mutation-preserves-undo-test"])
    window = MainWindow()
    window._last_batch_id = 73
    window.undo_button.setVisible(True)
    window.undo_button.setEnabled(True)

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: None,
    )

    # Un batch journalisé mais entièrement échoué ne doit pas remplacer la
    # dernière exécution ayant réellement modifié le filesystem.
    window._execution_succeeded(
        ExecuteResult(
            batch_id=99,
            success=0,
            errors=("source introuvable",),
        )
    )
    assert window._last_batch_id == 73
    assert window.undo_button.isHidden() is False

    # Même contrat pour un préflight refusé avant création de batch.
    window._execution_succeeded(
        ExecuteResult(
            batch_id=None,
            success=0,
            errors=("préflight refusé",),
        )
    )
    assert window._last_batch_id == 73
    assert window.undo_button.isHidden() is False
    assert window.undo_button.isEnabled() is True
    window.close()


def test_starting_new_analysis_clears_previous_execution_feedback(
    monkeypatch, tmp_path: Path
) -> None:
    app = create_application(["janitor-gui-execution-feedback-reset-test"])
    window = MainWindow()
    window.folder_edit.setText(str(tmp_path))
    window.analyze_button.setEnabled(True)
    window.execution_summary_label.setText("Batch #12 — ancien résultat")
    window.execution_summary_label.setVisible(True)
    window.execution_errors_table.setRowCount(1)
    window.execution_errors_table.setItem(0, 0, main_window_module.QTableWidgetItem("ancienne erreur"))
    window.execution_errors_table.setVisible(True)

    monkeypatch.setattr(
        main_window_module,
        "analyze_folder",
        lambda folder, *, config=None, **kwargs: _result(tmp_path),
    )
    window._start_analysis()

    assert window.execution_summary_label.text() == ""
    assert window.execution_errors_table.rowCount() == 0
    _wait_until(lambda: window._active_worker is None, app=app)

    window.close()


def test_undo_requires_explicit_confirmation(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-undo-confirmation-test"])
    window = MainWindow()
    calls = []
    window._last_batch_id = 12
    window.undo_button.setVisible(True)
    window.undo_button.setEnabled(True)

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(
        main_window_module,
        "undo_execution",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    window.undo_button.click()
    app.processEvents()

    assert calls == []
    assert window._last_batch_id == 12
    assert window._active_worker is None
    window.close()


def test_confirmed_undo_runs_in_background_and_consumes_batch(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import UndoResult

    app = create_application(["janitor-gui-undo-test"])
    window = MainWindow()
    calls = []
    window._last_batch_id = 27
    window.undo_button.setVisible(True)
    window.undo_button.setEnabled(True)

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )

    def fake_undo(batch_id):
        calls.append(batch_id)
        return UndoResult(success=2, errors=())

    monkeypatch.setattr(main_window_module, "undo_execution", fake_undo)
    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: None,
    )

    window.undo_button.click()
    assert window.status_label.text() == "Annulation en cours…"
    assert window.undo_button.isEnabled() is False
    _wait_until(lambda: window._active_worker is None, app=app)

    assert calls == [27]
    assert window.status_label.text() == "Annulation terminée."
    assert window.undo_summary_label.text() == "2 actions restaurées, 0 erreurs."
    assert window._last_batch_id is None
    assert window.undo_button.isVisible() is False
    window.close()


def test_partial_undo_keeps_batch_available_for_retry(monkeypatch, tmp_path: Path) -> None:
    from file_janitor.application import UndoResult

    app = create_application(["janitor-gui-partial-undo-test"])
    window = MainWindow()
    window._last_batch_id = 31
    window.undo_button.setVisible(True)
    window.undo_button.setEnabled(True)
    warnings = []

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        main_window_module,
        "undo_execution",
        lambda batch_id: UndoResult(success=1, errors=("destination occupée",)),
    )

    window.undo_button.click()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == "Annulation terminée avec erreurs."
    assert window.undo_summary_label.text() == "1 action restaurée, 1 erreur."
    assert window._last_batch_id == 31
    assert window.undo_button.isEnabled() is True
    assert warnings == [(
        "Annulation partielle",
        "Certains fichiers n’ont pas pu être restaurés. "
        "Cette exécution reste disponible pour une nouvelle tentative.",
    )]
    window.close()


def test_busy_state_disables_preview_controls_until_execution_finishes(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import ExecuteResult

    app = create_application(["janitor-gui-busy-controls-test"])
    window = MainWindow()
    release = threading.Event()

    window._show_result(_result(tmp_path))
    window.folder_edit.setText(str(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )

    def execute(
        result, selected, *, progress_callback=None, cancel_callback=None
    ):
        release.wait(timeout=1.0)
        return ExecuteResult(batch_id=73, success=1, errors=())

    monkeypatch.setattr(main_window_module, "execute_selected_actions", execute)

    window.execute_button.click()

    assert window.browse_button.isEnabled() is False
    assert window.analyze_button.isEnabled() is False
    assert window.category_table.isEnabled() is False
    assert window.details_filter.isEnabled() is False
    assert window.select_all_button.isEnabled() is False
    assert window.clear_selection_button.isEnabled() is False
    assert window.execute_button.isEnabled() is False
    assert window.history_button.isEnabled() is False
    assert window.history_table.isEnabled() is False

    release.set()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.browse_button.isEnabled() is True
    assert window.analyze_button.isEnabled() is True
    assert window.history_button.isEnabled() is True
    assert window.history_table.isEnabled() is True
    window.close()


def test_execution_result_does_not_enable_undo_before_worker_finishes(
    tmp_path: Path,
) -> None:
    from file_janitor.application import ExecuteResult
    from file_janitor.gui.workers import Worker

    create_application(["janitor-gui-undo-busy-state-test"])
    window = MainWindow()
    worker = Worker(lambda: None)
    window._active_worker = worker
    window._set_busy(True)

    window._execution_succeeded(
        ExecuteResult(batch_id=81, success=1, errors=())
    )

    assert window.undo_button.isHidden() is False
    assert window.undo_button.isEnabled() is False

    window._active_worker = None
    window._set_busy(False)
    assert window.undo_button.isEnabled() is True
    window.close()


def test_analysis_shows_destination_groups_from_application_result(tmp_path: Path) -> None:
    create_application(["janitor-gui-classification-groups-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))

    assert window.classification_groups_label.isHidden() is False
    assert window.classification_groups_label.text() == "Extensions trouvées"
    assert window.category_table.item(0, 0).text() == "Tous les fichiers classés"
    assert window.category_table.item(0, 1).text() == "2"
    assert window.category_table.item(1, 0).text() == "Documents"
    assert window.category_table.item(1, 1).text() == "2"

    window.close()



def test_preview_defaults_to_all_classified_files_before_group_filter(
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-preview-all-classified-test"])
    window = MainWindow()

    summary = AnalysisSummary(
        root=tmp_path,
        total_count=3,
        total_size=300,
        categories=(
            CategorySummary(
                key="to_sort",
                label="Fichiers à classer",
                item_count=3,
                total_size=300,
                display_metric="count",
            ),
        ),
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "a.txt",
                        size=100,
                        reason="Modifié en 2025-01, à classer",
                        destination=tmp_path / "2025-01" / "a.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "b.txt",
                        size=100,
                        reason="Modifié en 2025-02, à classer",
                        destination=tmp_path / "2025-02" / "b.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=tmp_path / "c.txt",
                        size=100,
                        reason="Modifié en 2025-02, à classer",
                        destination=tmp_path / "2025-02" / "c.txt",
                        action="move",
                    ),
                ),
            ),
        ),
    )

    window._show_result(result)
    app.processEvents()

    assert window.category_table.currentRow() == 0
    assert window.category_table.item(0, 0).text() == "Tous les fichiers classés"
    assert window.category_table.item(0, 1).text() == "3"
    assert window.details_table.rowCount() == 3
    assert (
        window.details_label.text()
        == "Prévisualisation — Tous les fichiers classés (3 éléments)"
    )

    window.category_table.selectRow(2)
    app.processEvents()

    assert window.category_table.item(2, 0).text() == "2025-02"
    assert window.details_table.rowCount() == 2
    assert window.details_label.text() == "Prévisualisation — 2025-02 (2 éléments)"

    window.close()


def test_preview_tables_keep_usable_minimum_heights(tmp_path: Path) -> None:
    create_application(["janitor-gui-preview-height-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))

    assert window.category_table.minimumHeight() >= 120
    assert window.details_table.minimumHeight() >= 220
    assert window.details_table.rowCount() == 2
    assert window.details_table.isHidden() is False

    window.close()



def test_clicking_analysis_category_opens_preview_tab(tmp_path: Path) -> None:
    create_application(["janitor-gui-preview-tab-test"])
    window = MainWindow()

    window._show_result(_result(tmp_path))
    assert window.tabs.currentWidget() is window.analysis_tab

    item = window.category_table.item(0, 0)
    assert item is not None
    window.category_table.itemClicked.emit(item)

    assert window.tabs.currentWidget() is window.preview_tab
    assert window.details_table.rowCount() == 2
    assert window.details_table.isHidden() is False

    window.close()


def test_busy_feedback_is_visible_and_restores_ready_state() -> None:
    create_application(["janitor-gui-busy-feedback-test"])
    window = MainWindow()

    window._set_activity(True, "Analyse en cours…", "busy")
    assert window.activity_progress.isHidden() is False
    assert window.activity_label.text() == "Analyse en cours…"
    assert window.activity_label.property("tone") == "busy"

    window._set_activity(False, "Analyse terminée", "success")
    assert window.activity_progress.isHidden() is True
    assert window.activity_label.text() == "Analyse terminée"
    assert window.activity_label.property("tone") == "success"

    window.close()

def test_feedback_helper_assigns_status_tone() -> None:
    create_application(["janitor-gui-status-tone-test"])
    window = MainWindow()

    window._set_feedback(window.status_label, "Opération réussie.", "success")

    assert window.status_label.text() == "Opération réussie."
    assert window.status_label.property("tone") == "success"
    window.close()


def test_execution_confirmation_explains_effects(monkeypatch, tmp_path: Path) -> None:
    create_application(["janitor-gui-execution-copy-test"])
    window = MainWindow()
    window._show_result(_result(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    captured = []

    def question(parent, title, message, *args):
        captured.append((title, message))
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    window._confirm_execution()

    assert captured == [(
        "Exécuter les actions sélectionnées ?",
        "Vous êtes sur le point d’exécuter 1 mutation prévue.\n\n"
        "Les fichiers concernés pourront être déplacés ou copiés. "
        "Vérifiez la prévisualisation avant de continuer.",
    )]
    window.close()


def test_undo_confirmation_explains_restoration_and_cleanup(monkeypatch) -> None:
    create_application(["janitor-gui-undo-copy-test"])
    window = MainWindow()
    window._last_batch_id = 42
    captured = []

    def question(parent, title, message, *args):
        captured.append((title, message))
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    window._confirm_undo()

    assert captured == [(
        "Annuler la dernière exécution ?",
        "Restaurer les fichiers modifiés par l’exécution #42 ?\n\n"
        "Les fichiers restaurés retrouveront leur emplacement d’origine. "
        "Les dossiers de destination devenus vides seront supprimés.",
    )]
    window.close()


def test_analyze_button_tracks_last_successful_folder_and_mode(tmp_path: Path) -> None:
    create_application(["janitor-gui-analysis-context-test"])
    window = MainWindow()
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()

    window.folder_edit.setText(str(first_folder))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True

    window._last_analysis_context = window._current_analysis_context()
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    window.tabs.setCurrentWidget(window.preview_tab)
    window.tabs.setCurrentWidget(window.analysis_tab)
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    original_mode = window.classification_mode_combo.currentIndex()
    other_mode = 1 if original_mode != 1 else 0
    window.classification_mode_combo.setCurrentIndex(other_mode)
    assert window.analyze_button.isEnabled() is True

    window.classification_mode_combo.setCurrentIndex(original_mode)
    assert window.analyze_button.isEnabled() is False

    window.folder_edit.setText(str(second_folder))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True

    window.folder_edit.setText(str(first_folder))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False
    window.close()


def test_successful_analysis_records_context_and_disables_analyze(tmp_path: Path) -> None:
    create_application(["janitor-gui-analysis-context-success-test"])
    window = MainWindow()
    window.folder_edit.setText(str(tmp_path))

    window._analysis_succeeded(_result(tmp_path))

    assert window._last_analysis_context == window._current_analysis_context()
    assert window.analyze_button.isEnabled() is False
    window.close()

def test_analysis_context_includes_effective_destination(tmp_path: Path) -> None:
    create_application(["janitor-gui-destination-context-test"])
    window = MainWindow()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    window.folder_edit.setText(str(source))
    source_context = window._current_analysis_context()
    assert source_context is not None
    assert source_context[0] == source.absolute()
    assert source_context[1] == source.absolute()

    window._last_analysis_context = source_context
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    window.destination_edit.setText(str(destination))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True

    window.destination_edit.clear()
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False
    window.close()


def test_choose_destination_reactivates_analysis(monkeypatch, tmp_path: Path) -> None:
    create_application(["janitor-gui-destination-picker-test"])
    window = MainWindow()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    window.folder_edit.setText(str(source))
    window._last_analysis_context = window._current_analysis_context()
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(destination),
    )
    window._choose_destination()

    assert window.destination_edit.text() == str(destination)
    assert window.analyze_button.isEnabled() is True

    window._use_source_as_destination()
    assert window.destination_edit.text() == ""
    assert window.analyze_button.isEnabled() is False
    window.close()




def test_source_change_invalidates_existing_preview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-source-invalidation-test"])
    window = MainWindow()
    source = tmp_path / "source"
    second = tmp_path / "second"
    source.mkdir()
    second.mkdir()

    window.folder_edit.setText(str(source))
    window._last_analysis_context = window._current_analysis_context()
    window._show_result(_result(source))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    window._duplicate_keepers["group"] = source / "keeper.txt"

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(second),
    )
    window._choose_folder()

    assert window.folder_edit.text() == str(second)
    assert window._analysis_result is None
    assert window._selected_actions == set()
    assert window._duplicate_keepers == {}
    assert window.details_table.rowCount() == 0
    assert window.execute_button.isEnabled() is False
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    assert window.status_label.text() == (
        "Dossier source modifié — relancez l’analyse."
    )
    window.close()


def test_destination_change_invalidates_existing_preview_and_cannot_revive_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-destination-invalidation-test"])
    window = MainWindow()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    window.folder_edit.setText(str(source))
    window._last_analysis_context = window._current_analysis_context()
    window._show_result(_result(source))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(destination),
    )
    window._choose_destination()

    assert window.destination_edit.text() == str(destination)
    assert window._analysis_result is None
    assert window._selected_actions == set()
    assert window.details_table.rowCount() == 0
    assert window.execute_button.isEnabled() is False
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    assert window.status_label.text() == (
        "Destination modifiée — relancez l’analyse."
    )

    # Revenir au contexte qui avait produit l'ancienne Preview ne doit pas
    # réactiver implicitement celle-ci : une nouvelle analyse reste requise.
    window._use_source_as_destination()
    assert window.destination_edit.text() == ""
    assert window._analysis_result is None
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    window.close()


def test_use_source_as_destination_invalidates_existing_preview(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-source-destination-invalidation-test"])
    window = MainWindow()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    window.folder_edit.setText(str(source))
    window.destination_edit.setText(str(destination))
    window._last_analysis_context = window._current_analysis_context()
    window._show_result(_result(source))

    window._use_source_as_destination()

    assert window.destination_edit.text() == ""
    assert window._analysis_result is None
    assert window.details_table.rowCount() == 0
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    assert window.status_label.text() == (
        "Destination remplacée par la source — relancez l’analyse."
    )
    window.close()


def test_classification_mode_change_invalidates_existing_preview(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-mode-invalidation-test"])
    window = MainWindow()
    source = tmp_path / "source"
    source.mkdir()

    window.folder_edit.setText(str(source))
    original_mode = window.classification_mode_combo.currentIndex()
    window._last_analysis_context = window._current_analysis_context()
    window._show_result(_result(source))

    other_mode = 1 if original_mode != 1 else 0
    window.classification_mode_combo.setCurrentIndex(other_mode)

    assert window._analysis_result is None
    assert window.details_table.rowCount() == 0
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    assert window.status_label.text() == (
        "Mode de classement modifié — relancez l’analyse."
    )

    window.classification_mode_combo.setCurrentIndex(original_mode)
    assert window.analyze_button.isEnabled() is True
    window.close()


def test_analysis_profile_change_invalidates_existing_preview(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-profile-invalidation-test"])
    window = MainWindow()
    source = tmp_path / "source"
    source.mkdir()

    window.folder_edit.setText(str(source))
    window._analysis_profile_source = source.absolute()
    window._last_analysis_context = window._current_analysis_context()
    window._show_result(_result(source))

    remote = window.analysis_profile_combo.findData("remote")
    assert remote >= 0
    window.analysis_profile_combo.setCurrentIndex(remote)

    assert window._analysis_result is None
    assert window.details_table.rowCount() == 0
    assert window._last_analysis_context is None
    assert window.analyze_button.isEnabled() is True
    assert window.status_label.text() == (
        "Profil d’analyse modifié — relancez l’analyse."
    )

    standard = window.analysis_profile_combo.findData("standard")
    assert standard >= 0
    window.analysis_profile_combo.setCurrentIndex(standard)
    assert window.analyze_button.isEnabled() is True
    window.close()


def test_start_analysis_passes_destination_to_application_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-destination-analysis-test"])
    window = MainWindow()
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    captured = {}

    def analyze(
        folder,
        *,
        config=None,
        compute_hashes=True,
        compute_content_type=True,
        progress_callback=None,
        cancel_callback=None,
    ):
        captured["folder"] = folder
        captured["config"] = config
        captured["compute_hashes"] = compute_hashes
        captured["compute_content_type"] = compute_content_type
        captured["progress_callback"] = progress_callback
        captured["cancel_callback"] = cancel_callback
        return _result(source)

    monkeypatch.setattr(main_window_module, "analyze_folder", analyze)

    window.folder_edit.setText(str(source))
    window.destination_edit.setText(str(destination))
    window._start_analysis()

    deadline = 200
    while window._active_worker is not None and deadline:
        app.processEvents()
        deadline -= 1

    assert captured["folder"] == source
    assert captured["config"].destination_root == destination
    assert captured["compute_hashes"] is True
    assert captured["compute_content_type"] is True
    assert callable(captured["progress_callback"])
    assert callable(captured["cancel_callback"])
    window.close()


def test_execution_cancel_button_requests_cooperative_stop(
    monkeypatch, tmp_path: Path
) -> None:
    app = create_application(["janitor-gui-execution-cancel-test"])
    window = MainWindow()

    class DummySignal:
        def connect(self, callback):
            pass

    class DummyWorker:
        def __init__(self, function, *args, **kwargs):
            self.kwargs = kwargs
            self.cancel_requested = False
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

        def request_cancel(self):
            self.cancel_requested = True

    captured = {}

    def make_worker(function, *args, **kwargs):
        worker = DummyWorker(function, *args, **kwargs)
        captured["worker"] = worker
        return worker

    window._show_result(_result(tmp_path))
    window.details_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(main_window_module, "Worker", make_worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)

    window._confirm_execution()

    worker = captured["worker"]
    assert worker.kwargs["cancel_kwarg"] == "cancel_callback"
    assert worker.kwargs["return_result_on_cancel"] is True
    assert window.cancel_analysis_button.text() == "Annuler l’exécution"
    assert window.cancel_analysis_button.isEnabled() is True

    window._cancel_active_operation()

    assert worker.cancel_requested is True
    assert window.cancel_analysis_button.isEnabled() is False
    assert "L’action en cours sera terminée" in window.status_label.text()
    window.close()


def test_cancelled_execution_reports_unstarted_actions_and_keeps_undo(
    tmp_path: Path, monkeypatch,
) -> None:
    from file_janitor.application import ExecuteResult
    from file_janitor.gui.workers import Worker

    create_application(["janitor-gui-cancelled-execution-result-test"])
    window = MainWindow()
    worker = Worker(lambda: None)
    window._active_worker = worker
    window._active_operation = "execution"
    window._set_busy(True)

    window._execution_succeeded(
        ExecuteResult(
            batch_id=91,
            success=3,
            errors=(),
            cancelled=True,
            skipped=7,
        )
    )

    assert window.status_label.text() == "Exécution interrompue."
    assert (
        window.execution_summary_label.text()
        == "Batch #91 — 3 actions réussies, 0 erreurs, 7 non démarrées."
    )
    assert window._last_batch_id == 91
    assert window.undo_button.isHidden() is False
    assert window.undo_button.isEnabled() is False

    # Le rafraîchissement d'historique est un workflow asynchrone distinct :
    # il remet temporairement l'UI en état occupé. Ce test vérifie uniquement
    # que l'Undo du batch interrompu devient disponible lorsque le worker
    # d'exécution est terminé.
    monkeypatch.setattr(window, "_refresh_history_after_mutation", lambda: None)
    window._execution_finished()
    assert window.undo_button.isEnabled() is True
    window.close()


def test_preview_explains_preplanned_batch_collision(tmp_path: Path) -> None:
    create_application(["janitor-gui-batch-collision-preview-test"])
    window = MainWindow()
    summary = AnalysisSummary(
        root=tmp_path, total_count=1, total_size=1, categories=()
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "source" / "report.pdf",
                        size=1,
                        reason="Extension pdf, à classer",
                        destination=tmp_path / "pdf" / "report (1).pdf",
                        action="move",
                        conflict=True,
                        conflict_reason=(
                            "Collision interne au lot : destination renommée en "
                            "« report (1).pdf »."
                        ),
                    ),
                ),
            ),
        ),
    )
    window._analysis_result = result
    item = result.details_for("to_sort").items[0]
    window._populate_details_table(
        ((0, item),),
        category_key="to_sort",
        label="Tous les fichiers classés",
    )

    assert window.details_table.item(0, 4).text().endswith("report (1).pdf")
    assert "Collision interne au lot" in window.details_table.item(0, 5).text()
    assert "report (1).pdf" in window.details_table.item(0, 4).toolTip()
    window.close()


def test_execution_confirmation_mentions_selected_batch_collision(
    monkeypatch, tmp_path: Path
) -> None:
    create_application(["janitor-gui-batch-collision-confirm-test"])
    window = MainWindow()
    summary = AnalysisSummary(
        root=tmp_path, total_count=1, total_size=1, categories=()
    )
    result = AnalysisResult(
        summary=summary,
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "report.pdf",
                        size=1,
                        reason="Classer",
                        destination=tmp_path / "pdf" / "report (1).pdf",
                        action="move",
                        conflict=True,
                        conflict_reason="Collision interne au lot.",
                    ),
                ),
            ),
        ),
        _actions=(
            (
                "to_sort",
                0,
                ActionItem(
                    category=FileCategory.TO_SORT,
                    path=tmp_path / "report.pdf",
                    size=1,
                    reason="Classer",
                    destination=tmp_path / "pdf" / "report (1).pdf",
                    action=ActionKind.MOVE,
                    conflict=True,
                    conflict_reason="Collision interne au lot.",
                ),
            ),
        ),
    )
    window._analysis_result = result
    window._selected_actions = {("to_sort", 0)}
    captured = []

    def question(parent, title, message, *args):
        captured.append((title, message))
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    window._confirm_execution()

    assert "1 action appartient à une collision interne" in captured[0][1]
    window.close()


def test_persistent_undo_target_is_restored_on_first_show(
    monkeypatch,
) -> None:
    app = create_application(["janitor-gui-persistent-undo-target-test"])
    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: 77,
    )
    window = MainWindow()

    assert window._last_batch_id is None
    window.show()
    app.processEvents()

    assert window._last_batch_id == 77
    assert window.undo_button.isVisible() is True
    assert window.undo_button.isEnabled() is True
    window.close()


def test_successful_undo_falls_back_to_previous_persistent_target(
    monkeypatch,
) -> None:
    from file_janitor.application import UndoResult

    create_application(["janitor-gui-persistent-undo-fallback-test"])
    window = MainWindow()
    window._persistent_undo_target_loaded = True
    window._last_batch_id = 90
    window.undo_button.setVisible(True)

    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: 41,
    )

    window._undo_succeeded(UndoResult(success=1, errors=()))

    assert window._last_batch_id == 41
    assert window.undo_button.isHidden() is False
    window.close()

def test_persistent_undo_restore_runs_crash_recovery_before_lookup(
    monkeypatch,
) -> None:
    create_application(["janitor-gui-crash-recovery-order-test"])
    window = MainWindow()
    calls: list[str] = []

    monkeypatch.setattr(
        main_window_module,
        "run_startup_crash_recovery",
        lambda: calls.append("recovery"),
    )

    def fake_latest() -> int:
        calls.append("latest")
        return 77

    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        fake_latest,
    )

    window._persistent_undo_target_loaded = False
    window._last_batch_id = None
    window._restore_persistent_undo_target()

    assert calls == ["recovery", "latest"]
    assert window._last_batch_id == 77
    window.close()


def test_persistent_undo_restore_fails_closed_when_crash_recovery_fails(
    monkeypatch,
) -> None:
    create_application(["janitor-gui-crash-recovery-fail-closed-test"])
    window = MainWindow()
    calls: list[str] = []

    def fail_recovery() -> None:
        calls.append("recovery")
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(
        main_window_module,
        "run_startup_crash_recovery",
        fail_recovery,
    )
    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: calls.append("latest") or 99,
    )

    window._persistent_undo_target_loaded = False
    window._last_batch_id = 73
    window._restore_persistent_undo_target()

    assert calls == ["recovery"]
    assert window._last_batch_id is None
    assert not window.undo_button.isVisible()
    assert not window.undo_button.isEnabled()
    window.close()
