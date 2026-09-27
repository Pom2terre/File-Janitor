"""Tests d'accessibilité et de robustesse des états interactifs GUI."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def test_primary_controls_expose_accessible_names_and_mode_label_buddy() -> None:
    create_application(["janitor-gui-accessibility-test"])
    window = MainWindow()

    expected_names = {
        window.folder_edit: "Dossier à analyser",
        window.browse_button: "Choisir un dossier à analyser",
        window.classification_mode_combo: "Mode de classement",
        window.analyze_button: "Analyser le dossier",
        window.execute_button: "Exécuter la sélection",
        window.undo_button: "Annuler la dernière exécution",
        window.history_button: "Actualiser l'historique",
        window.preferences_button: "Préférences",
    }
    for widget, expected in expected_names.items():
        assert widget.accessibleName() == expected

    assert window.classification_mode_label.buddy() is window.classification_mode_combo
    window.close()


def test_busy_state_locks_mutating_configuration_but_keeps_tabs_navigable() -> None:
    create_application(["janitor-gui-busy-accessibility-test"])
    window = MainWindow()

    window.folder_edit.setText("/tmp/example")
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True
    assert window.preferences_button.isEnabled() is True
    assert window.tabs.isEnabled() is True

    window._set_busy(True)

    assert window.browse_button.isEnabled() is False
    assert window.preferences_button.isEnabled() is False
    assert window.classification_mode_combo.isEnabled() is False
    assert window.analyze_button.isEnabled() is False
    assert window.execute_button.isEnabled() is False
    assert window.undo_button.isEnabled() is False
    assert window.history_button.isEnabled() is False
    assert window.tabs.isEnabled() is True

    window.tabs.setCurrentWidget(window.history_tab)
    assert window.tabs.currentWidget() is window.history_tab

    window._set_busy(False)

    assert window.browse_button.isEnabled() is True
    assert window.preferences_button.isEnabled() is True
    assert window.classification_mode_combo.isEnabled() is True
    assert window.analyze_button.isEnabled() is True
    assert window.history_button.isEnabled() is True
    window.close()
