"""Tests 4E-4F : cohérence profil restauré, garde-fou et changement de source."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox

from file_janitor.application import ClassificationMode, RemoteFolderInfo
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.preferences import GuiPreferences


def _preferences(tmp_path: Path) -> GuiPreferences:
    from PySide6.QtCore import QSettings

    return GuiPreferences(
        QSettings(str(tmp_path / "gui-4e-4f.ini"), QSettings.Format.IniFormat)
    )


def _saved_preferences(
    tmp_path: Path,
    folder: Path,
    *,
    profile: str,
) -> GuiPreferences:
    preferences = _preferences(tmp_path)
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()
    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.EXTENSION.value,
        analysis_profile=profile,
        active_tab="analysis",
    )
    return preferences


def test_restored_standard_local_profile_does_not_prompt(tmp_path, monkeypatch) -> None:
    create_application(["janitor-gui-4e-4f-local"])
    folder = tmp_path / "local"
    folder.mkdir()
    preferences = _saved_preferences(tmp_path, folder, profile="standard")

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(False, "ext4", Path("/")),
    )
    asked = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: asked.append(args) or QMessageBox.StandardButton.No,
    )

    window = MainWindow(preferences=preferences)
    assert window._selected_analysis_profile() == "standard"
    assert window._analysis_profile_user_override is True
    assert window._confirm_slow_remote_analysis(folder) is True
    assert asked == []
    window.close()


def test_restored_standard_remote_profile_is_guarded(tmp_path, monkeypatch) -> None:
    create_application(["janitor-gui-4e-4f-remote"])
    folder = tmp_path / "remote"
    folder.mkdir()
    preferences = _saved_preferences(tmp_path, folder, profile="standard")

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", folder),
    )
    asked = []

    def question(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)

    window = MainWindow(preferences=preferences)
    assert window._selected_analysis_profile() == "standard"
    assert window._analysis_profile_user_override is True
    assert window._confirm_slow_remote_analysis(folder) is False
    assert len(asked) == 1
    window.close()


def test_restored_standard_remote_decline_starts_no_worker(tmp_path, monkeypatch) -> None:
    create_application(["janitor-gui-4e-4f-decline"])
    folder = tmp_path / "remote"
    folder.mkdir()
    preferences = _saved_preferences(tmp_path, folder, profile="standard")

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", folder),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    window = MainWindow(preferences=preferences)
    started = []
    monkeypatch.setattr(window, "_start_worker", lambda worker: started.append(worker))
    window._start_analysis()

    assert started == []
    assert window._active_worker is None
    assert window.status_label.text() == "Analyse Standard annulée avant démarrage."
    window.close()


def test_new_source_after_restored_override_reenables_auto_detection(
    tmp_path, monkeypatch
) -> None:
    create_application(["janitor-gui-4e-4f-source-change"])
    first = tmp_path / "remote"
    second = tmp_path / "local"
    first.mkdir()
    second.mkdir()
    preferences = _saved_preferences(tmp_path, first, profile="standard")

    detections = {
        first.absolute(): RemoteFolderInfo(True, "fuse.rclone", first),
        second.absolute(): RemoteFolderInfo(False, "ext4", Path("/")),
    }
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: detections[Path(path).expanduser().absolute()],
    )

    window = MainWindow(preferences=preferences)
    assert window._selected_analysis_profile() == "standard"
    assert window._analysis_profile_user_override is True
    assert window._analysis_profile_source == first.absolute()

    window.folder_edit.setText(str(second))
    window._auto_select_analysis_profile(second)

    assert window._selected_analysis_profile() == "standard"
    assert window._analysis_profile_user_override is False
    assert window._analysis_profile_source == second.absolute()
    assert "Dossier local détecté" in window.analysis_profile_hint.text()
    window.close()


def test_new_remote_source_after_restored_override_selects_remote(
    tmp_path, monkeypatch
) -> None:
    create_application(["janitor-gui-4e-4f-source-change-remote"])
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    preferences = _saved_preferences(tmp_path, first, profile="standard")

    detections = {
        first.absolute(): RemoteFolderInfo(False, "ext4", Path("/")),
        second.absolute(): RemoteFolderInfo(True, "fuse.rclone", second),
    }
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: detections[Path(path).expanduser().absolute()],
    )

    window = MainWindow(preferences=preferences)
    assert window._analysis_profile_user_override is True

    window.folder_edit.setText(str(second))
    window._auto_select_analysis_profile(second)

    assert window._selected_analysis_profile() == "remote"
    assert window._analysis_profile_user_override is False
    assert window._analysis_profile_source == second.absolute()
    assert "Remote / rapide sélectionné" in window.analysis_profile_hint.text()
    window.close()
