"""Tests 4E-4D du contrôle de restauration du profil dans le dialogue."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from file_janitor.gui.app import create_application
from file_janitor.gui.preferences import GuiPreferences
from file_janitor.gui.preferences_dialog import PreferencesDialog


def _preferences(tmp_path: Path) -> GuiPreferences:
    return GuiPreferences(
        QSettings(
            str(tmp_path / "dialog.ini"),
            QSettings.Format.IniFormat,
        )
    )


def test_dialog_exposes_analysis_profile_restore_checkbox(tmp_path: Path) -> None:
    create_application(["janitor-profile-pref-dialog-test"])
    preferences = _preferences(tmp_path)
    dialog = PreferencesDialog(preferences)

    assert dialog.restore_profile_checkbox.isChecked() is True
    assert "profil" in dialog.restore_profile_checkbox.text().lower()

    dialog.close()


def test_dialog_save_persists_analysis_profile_restore_option(
    tmp_path: Path,
) -> None:
    create_application(["janitor-profile-pref-dialog-save-test"])
    preferences = _preferences(tmp_path)
    dialog = PreferencesDialog(preferences)

    dialog.restore_profile_checkbox.setChecked(False)
    dialog._save()

    restored = _preferences(tmp_path)
    assert restored.restore_analysis_profile() is False
