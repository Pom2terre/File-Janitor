"""Tests du dialogue de préférences GUI."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from file_janitor.gui.app import create_application
from file_janitor.gui.preferences import GuiPreferences
from file_janitor.gui.preferences_dialog import PreferencesDialog


def _preferences(tmp_path: Path) -> GuiPreferences:
    return GuiPreferences(QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat))


def test_preferences_dialog_saves_restore_choices(tmp_path: Path) -> None:
    create_application(["janitor-preferences-dialog-test"])
    preferences = _preferences(tmp_path)
    dialog = PreferencesDialog(preferences)
    dialog.restore_folder_checkbox.setChecked(False)
    dialog.restore_destination_checkbox.setChecked(False)
    dialog.restore_mode_checkbox.setChecked(True)
    dialog._save()
    restored = _preferences(tmp_path)
    assert restored.restore_folder() is False
    assert restored.restore_destination() is False
    assert restored.restore_classification_mode() is True
    assert restored.restore_active_tab() is False


def test_preferences_dialog_reset_is_explicit_and_non_destructive_to_other_data(tmp_path: Path, monkeypatch) -> None:
    create_application(["janitor-preferences-reset-test"])
    preferences = _preferences(tmp_path)
    preferences._settings.setValue("unrelated/history_marker", "keep")
    # reset() owns only this QSettings namespace; the dialog explicitly states that
    # application history is stored elsewhere and is therefore unaffected.
    dialog = PreferencesDialog(preferences)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog._reset_preferences()
    assert dialog.was_reset is True
    assert preferences.last_folder() == ""


def test_preferences_dialog_forgets_values_when_restore_is_disabled(tmp_path: Path) -> None:
    create_application(["janitor-preferences-forget-values-test"])
    preferences = _preferences(tmp_path)
    preferences._settings.setValue("analysis/last_folder", "/tmp/example")
    preferences._settings.setValue("analysis/last_destination", "/tmp/destination")
    preferences._settings.setValue("analysis/classification_mode", "date")
    preferences._settings.setValue("navigation/active_tab", "history")
    preferences._settings.sync()

    dialog = PreferencesDialog(preferences)
    dialog.restore_folder_checkbox.setChecked(False)
    dialog.restore_destination_checkbox.setChecked(False)
    dialog.restore_mode_checkbox.setChecked(False)
    dialog._save()

    restored = _preferences(tmp_path)
    assert restored.last_folder() == ""
    assert restored.last_destination() == ""
    assert restored.classification_mode() == ""
    assert restored.active_tab() == "analysis"
