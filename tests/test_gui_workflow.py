"""Tests d'intégration du workflow GUI de bout en bout."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from file_janitor.application import (
    AnalysisItem,
    AnalysisResult,
    AnalysisSummary,
    CategoryDetails,
    CategorySummary,
    ExecuteResult,
    HistoryBatchSummary,
    HistoryOperationSummary,
    UndoResult,
)
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def _wait_until(predicate, *, app, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout en attendant la fin du workflow GUI")
        app.processEvents()
        time.sleep(0.005)


def _analysis_result(root: Path) -> AnalysisResult:
    return AnalysisResult(
        summary=AnalysisSummary(
            root=root,
            total_count=2,
            total_size=3072,
            categories=(
                CategorySummary(
                    key="to_sort",
                    label="Fichiers à classer",
                    item_count=2,
                    total_size=3072,
                    display_metric="count",
                ),
            ),
        ),
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=root / "first.txt",
                        size=1024,
                        reason="à classer",
                        destination=root / "txt" / "first.txt",
                        action="move",
                    ),
                    AnalysisItem(
                        path=root / "second.txt",
                        size=2048,
                        reason="à classer",
                        destination=root / "txt" / "second.txt",
                        action="move",
                    ),
                ),
            ),
        ),
    )


def _history(root: Path, *, undone: bool) -> tuple[HistoryBatchSummary, ...]:
    return (
        HistoryBatchSummary(
            id=73,
            created_at="2026-09-03 19:00",
            root=str(root),
            status="undone" if undone else "completed",
            undone=undone,
        ),
    )


def _operations(root: Path, *, undone: bool) -> tuple[HistoryOperationSummary, ...]:
    return (
        HistoryOperationSummary(
            kind="move",
            original_path=root / "first.txt",
            stored_path=root / "txt" / "first.txt",
            size=1024,
            category="to_sort",
            status="undone" if undone else "completed",
            error=None,
        ),
        HistoryOperationSummary(
            kind="move",
            original_path=root / "second.txt",
            stored_path=root / "txt" / "second.txt",
            size=2048,
            category="to_sort",
            status="undone" if undone else "completed",
            error=None,
        ),
    )


def test_full_gui_workflow_analysis_execute_history_undo(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Le même MainWindow reste cohérent sur tout le cycle utilisateur."""

    app = create_application(["janitor-gui-full-workflow-test"])
    window = MainWindow()
    result = _analysis_result(tmp_path)
    state = {"undone": False}
    executed_selections: list[set[tuple[str, int]]] = []

    monkeypatch.setattr(
        main_window_module,
        "analyze_folder",
        lambda folder, *, config=None, **kwargs: result,
    )

    def execute(
        _result, selected, *, progress_callback=None, cancel_callback=None
    ):
        executed_selections.append(set(selected))
        return ExecuteResult(batch_id=73, success=2, errors=())

    monkeypatch.setattr(main_window_module, "execute_selected_actions", execute)

    def undo(batch_id: int):
        assert batch_id == 73
        state["undone"] = True
        return UndoResult(success=2, errors=())

    monkeypatch.setattr(main_window_module, "undo_execution", undo)
    monkeypatch.setattr(
        main_window_module,
        "get_history_summary",
        lambda: _history(tmp_path, undone=state["undone"]),
    )
    monkeypatch.setattr(
        main_window_module,
        "get_history_operation_summary",
        lambda batch_id: _operations(tmp_path, undone=state["undone"]),
    )
    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: None,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    # Analyse : la prévisualisation est produite et le même contexte ne doit
    # plus pouvoir être analysé une seconde fois tant que rien n'a changé.
    window.folder_edit.setText(str(tmp_path))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True

    window._start_analysis()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window._analysis_result is result
    assert window.details_table.rowCount() == 2
    assert window.analyze_button.isEnabled() is False

    # Sélection et exécution : la prévisualisation devient périmée, l'historique
    # est rafraîchi et une nouvelle analyse doit être possible car le filesystem
    # a potentiellement changé.
    window._select_visible_actions()
    assert len(window._selected_actions) == 2
    assert window.execute_button.isEnabled() is True

    window._confirm_execution()
    _wait_until(lambda: window._active_worker is None, app=app)
    _wait_until(lambda: window._history_operation_worker is None, app=app)

    assert executed_selections == [{("to_sort", 0), ("to_sort", 1)}]
    assert window._analysis_result is None
    assert window.details_table.rowCount() == 0
    assert window.execute_button.isEnabled() is False
    assert window._last_batch_id == 73
    assert window.undo_button.isHidden() is False
    assert window.undo_button.isEnabled() is True
    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 0).text() == "73"
    assert window.history_table.item(0, 3).text() == "Terminé"
    assert window.analyze_button.isEnabled() is True

    # Undo : le batch reste sélectionné dans l'historique, passe à Annulé et
    # l'interface reste prête à relancer une analyse du même dossier/mode.
    window._confirm_undo()
    _wait_until(lambda: window._active_worker is None, app=app)
    _wait_until(lambda: window._history_operation_worker is None, app=app)

    assert state["undone"] is True
    assert window._last_batch_id is None
    assert window.undo_button.isVisible() is False
    assert window.history_table.rowCount() == 1
    assert window.history_table.item(0, 0).text() == "73"
    assert window.history_table.item(0, 3).text() == "Annulé"
    assert window._selected_history_batch_id() == 73
    assert window.history_operations_table.rowCount() == 2
    assert window.history_operations_table.item(0, 5).text() == "Annulé"
    assert window.analyze_button.isEnabled() is True

    window.close()


def test_tab_navigation_does_not_change_analysis_validity(tmp_path: Path) -> None:
    """Changer seulement d'onglet ne rend jamais une analyse périmée."""

    create_application(["janitor-gui-workflow-tab-context-test"])
    window = MainWindow()

    window.folder_edit.setText(str(tmp_path))
    window._last_analysis_context = window._current_analysis_context()
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    window.tabs.setCurrentWidget(window.preview_tab)
    window.tabs.setCurrentWidget(window.history_tab)
    window.tabs.setCurrentWidget(window.analysis_tab)

    assert window.analyze_button.isEnabled() is False
    window.close()
