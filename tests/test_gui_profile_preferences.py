"""Tests 4E-4D : persistance du profil d'analyse."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from file_janitor.gui.preferences import GuiPreferences

def prefs(tmp_path: Path) -> GuiPreferences:
    return GuiPreferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))

def test_analysis_profile_defaults_empty(tmp_path):
    assert prefs(tmp_path).analysis_profile() == ""

def test_valid_analysis_profile_is_saved(tmp_path):
    p = prefs(tmp_path)
    p.save(
        geometry=b"", last_folder="", last_destination="",
        classification_mode="extension", analysis_profile="remote",
        active_tab="analysis",
    )
    assert p.analysis_profile() == "remote"

def test_invalid_analysis_profile_is_not_persisted(tmp_path):
    p = prefs(tmp_path)
    p.save(
        geometry=b"", last_folder="", last_destination="",
        classification_mode="extension", analysis_profile="invalid",
        active_tab="analysis",
    )
    assert p.analysis_profile() == ""

def test_disabling_profile_restore_forgets_value(tmp_path):
    p = prefs(tmp_path)
    p.save(
        geometry=b"", last_folder="", last_destination="",
        classification_mode="extension", analysis_profile="remote",
        active_tab="analysis",
    )
    p.save_restore_options(
        folder=True, destination=True, classification_mode=True,
        analysis_profile=False, active_tab=True,
    )
    assert p.analysis_profile() == ""
    assert p.restore_analysis_profile() is False
