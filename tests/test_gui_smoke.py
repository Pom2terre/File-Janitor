"""Smoke tests du squelette PySide6."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from file_janitor import __version__
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def test_gui_window_can_be_created_without_showing_it() -> None:
    app = create_application(["janitor-gui-test"])
    window = MainWindow()

    assert app is not None
    assert window.windowTitle() == "File Janitor"
    assert window.version_label.text() == f"File Janitor v{__version__}"
    assert window.centralWidget() is not None
    assert window.analyze_button.isEnabled() is False
    assert window.category_table.rowCount() == 0

    window.close()


def test_main_window_unifies_analysis_preview_and_history_navigation() -> None:
    create_application(["janitor-gui-tabs-test"])
    window = MainWindow()

    assert window.tabs.count() == 2
    assert window.tabs.tabText(0) == "Espace de travail"
    assert window.tabs.tabText(1) == "Historique"
    assert window.tabs.tabBar().isHidden()
    assert window.tabs.currentWidget() is window.analysis_tab
    assert window.preview_tab is window.analysis_tab

    assert window.analysis_tab.isAncestorOf(window.analyze_button)
    assert window.analysis_tab.isAncestorOf(window.category_table)
    assert window.preview_tab.isAncestorOf(window.details_table)
    assert window.preview_tab.isAncestorOf(window.execute_button)
    assert window.history_tab.isAncestorOf(window.history_button)
    assert window.history_tab.isAncestorOf(window.history_table)
    assert window.history_tab.isAncestorOf(window.history_operations_table)

    window.tabs.setCurrentWidget(window.preview_tab)
    assert window.tabs.currentWidget() is window.analysis_tab

    window.close()

def test_main_window_uses_resizable_history_splitter_and_readable_tables() -> None:
    create_application(["janitor-gui-layout-test"])
    window = MainWindow()

    assert window.analysis_summary_panel.isAncestorOf(window.category_table)
    assert window.analysis_actions_panel.isAncestorOf(window.details_table)
    assert window.details_table.minimumHeight() >= 220

    assert window.history_splitter.orientation() == Qt.Orientation.Vertical
    assert window.history_splitter.count() == 2
    assert window.history_splitter.childrenCollapsible() is False
    assert window.history_batches_panel.isAncestorOf(window.history_table)
    assert window.history_operations_panel.isAncestorOf(
        window.history_operations_table
    )

    for table in (
        window.details_table,
        window.history_table,
        window.history_operations_table,
    ):
        assert table.alternatingRowColors() is True
        assert table.wordWrap() is False
        assert table.textElideMode() == Qt.TextElideMode.ElideMiddle

    window.close()


def test_workspace_sidebar_selects_inline_operation_options() -> None:
    create_application(["janitor-gui-sidebar-options-test"])
    window = MainWindow()
    window.folder_edit.setText("/tmp/example")
    window._update_analyze_button()

    window.navigation_buttons["archive"].click()

    assert window.tabs.currentWidget() is window.analysis_tab
    assert window.operation_options_stack.currentIndex() == 2
    assert window.workspace_operation_title.text() == "Archiver les fichiers anciens"
    assert window.navigation_buttons["archive"].isChecked()
    assert window.navigation_buttons["archive"].property("navigationSelected") == "true"
    assert window.analyze_button.text() == "Analyser les fichiers à archiver"
    assert window.analyze_button.property("workspaceCallToAction") == "true"
    assert window.analyze_button.isEnabled()
    assert window.archive_age_spin.value() == 12
    assert window.archive_age_unit_combo.currentData() == "months"
    assert window._archive_older_than_days() == 365
    assert window.archive_format_zip_radio.text() == "Fichier ZIP compressé"
    assert window.archive_format_folders_radio.text() == "Conserver l'arborescence de dossiers"
    assert window.archive_format_folders_radio.isChecked()
    window.archive_format_zip_radio.click()
    assert window.archive_format_zip_radio.isChecked()
    assert window.analysis_tab.isAncestorOf(window.details_table)

    window.close()


def test_operation_options_stack_shrinks_for_empty_directory_scan() -> None:
    create_application(["janitor-gui-empty-layout-test"])
    window = MainWindow()
    try:
        window._select_operation("archive")
        archive_height = window.operation_options_stack.maximumHeight()

        window._select_operation("empty")
        empty_height = window.operation_options_stack.maximumHeight()

        assert empty_height < archive_height
        assert empty_height < 100
    finally:
        window.close()


def test_main_window_applies_professional_visual_hierarchy() -> None:
    create_application(["janitor-gui-theme-test"])
    window = MainWindow()

    assert window.app_header.objectName() == "appHeader"
    assert window.folder_panel.objectName() == "folderPanel"
    assert window.footer_bar.objectName() == "footerBar"
    assert window.footer_bar.isAncestorOf(window.undo_button)
    assert window.analyze_button.objectName() == "primaryButton"
    assert window.execute_button.objectName() == "successButton"
    assert window.undo_button.objectName() == "warningButton"
    assert window.analysis_summary_panel.objectName() == "analysisSummaryPanel"
    assert window.analysis_actions_panel.objectName() == "analysisActionsPanel"
    assert window.history_batches_panel.objectName() == "historyBatchesPanel"
    assert window.history_operations_panel.objectName() == "historyOperationsPanel"

    stylesheet = window.styleSheet()
    assert "#2563eb" in stylesheet  # action principale
    assert "#176b35" in stylesheet  # exécution
    assert "#a95608" in stylesheet  # undo
    assert "#be7cff" in stylesheet  # prévisualisation
    assert 'workspaceCallToAction="true"' in stylesheet
    assert "#navigationSidebar" in stylesheet

    window.close()


def test_dashboard_exposes_clear_empty_states() -> None:
    create_application(["janitor-gui-empty-states-test"])
    window = MainWindow()

    assert window.preview_empty_label.isVisible() is False or window.preview_empty_label.text()
    assert "historique" in window.history_empty_label.text().casefold()
    assert "sélectionnez" in window.history_operations_empty_label.text().casefold()
    assert window.preview_empty_label.objectName() == "emptyState"
    assert window.history_empty_label.objectName() == "emptyState"
    assert window.history_operations_empty_label.objectName() == "emptyState"
    assert "#101d18" in window.styleSheet()

    window.close()


def test_dashboard_exposes_global_activity_feedback() -> None:
    create_application(["janitor-gui-activity-feedback-test"])
    window = MainWindow()

    assert window.footer_bar.isAncestorOf(window.activity_progress)
    assert window.footer_bar.isAncestorOf(window.activity_label)
    assert window.activity_progress.minimum() == 0
    assert window.activity_progress.maximum() == 0
    assert window.activity_progress.isHidden() is True
    assert window.activity_label.text() == "Prêt"
    assert 'activityProgress' in window.styleSheet()
    assert 'tone="success"' in window.styleSheet()

    window.close()


def test_dashboard_uses_user_facing_history_and_empty_state_copy() -> None:
    create_application(["janitor-gui-microcopy-test"])
    window = MainWindow()

    assert window.history_table.horizontalHeaderItem(0).text() == "Exécution"
    assert "Aucune prévisualisation" in window.preview_empty_label.text()
    assert "Aucune exécution affichée" in window.history_empty_label.text()
    assert "Sélectionnez une exécution" in window.history_operations_empty_label.text()
    window.close()


def test_dashboard_exposes_keyboard_navigation_shortcuts() -> None:
    create_application(["janitor-gui-shortcuts-test"])
    window = MainWindow()

    assert len(window._navigation_shortcuts) == 4
    assert {shortcut.key().toString() for shortcut in window._navigation_shortcuts} == {
        "Alt+1", "Alt+2", "Alt+3", "Ctrl+L"
    }
    assert "Alt+1" in window.tabs.tabToolTip(0)
    assert "Alt+3" in window.tabs.tabToolTip(1)

    window.close()


def test_keyboard_navigation_preserves_dashboard_context() -> None:
    app = create_application(["janitor-gui-keyboard-navigation-test"])
    window = MainWindow()
    window.show()
    app.processEvents()

    window.folder_edit.setText("/tmp/example")

    window._focus_tab(window.preview_tab)
    app.processEvents()
    assert window.tabs.currentWidget() is window.preview_tab

    window._focus_tab(window.history_tab)
    app.processEvents()
    assert window.tabs.currentWidget() is window.history_tab

    window._focus_folder_input()
    app.processEvents()

    assert window.tabs.currentWidget() is window.analysis_tab
    assert window.folder_edit.text() == "/tmp/example"
    assert window.focusWidget() is window.folder_edit

    window.close()
