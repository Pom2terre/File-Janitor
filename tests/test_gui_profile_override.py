"""Tests 4E-4C : distinction auto-sélection / choix manuel du profil."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.application import RemoteFolderInfo
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["file-janitor-profile-override-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _set_detection(monkeypatch, *, remote: bool, fs_type: str, mount_point: Path) -> None:
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda folder: RemoteFolderInfo(remote, fs_type, mount_point),
    )


def test_automatic_profile_selection_is_not_a_manual_override(window, tmp_path, monkeypatch) -> None:
    _set_detection(monkeypatch, remote=True, fs_type="fuse.rclone", mount_point=tmp_path)
    window._auto_select_analysis_profile(tmp_path)
    assert window.analysis_profile_combo.currentData() == "remote"
    assert window._analysis_profile_user_override is False
    assert window._analysis_profile_source == tmp_path.absolute()


def test_manual_profile_change_marks_override_for_current_source(window, tmp_path, monkeypatch) -> None:
    _set_detection(monkeypatch, remote=True, fs_type="fuse.rclone", mount_point=tmp_path)
    window._auto_select_analysis_profile(tmp_path)
    standard = window.analysis_profile_combo.findData("standard")
    window.analysis_profile_combo.setCurrentIndex(standard)
    assert window.analysis_profile_combo.currentData() == "standard"
    assert window._analysis_profile_user_override is True


def test_same_source_keeps_manual_profile_override(window, tmp_path, monkeypatch) -> None:
    _set_detection(monkeypatch, remote=True, fs_type="fuse.rclone", mount_point=tmp_path)
    window._auto_select_analysis_profile(tmp_path)
    standard = window.analysis_profile_combo.findData("standard")
    window.analysis_profile_combo.setCurrentIndex(standard)
    window._auto_select_analysis_profile(tmp_path)
    assert window.analysis_profile_combo.currentData() == "standard"
    assert window._analysis_profile_user_override is True
    assert "Choix manuel conservé" in window.analysis_profile_hint.text()


def test_new_source_reenables_automatic_profile_selection(window, tmp_path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    detections = {
        first.absolute(): RemoteFolderInfo(True, "fuse.rclone", first),
        second.absolute(): RemoteFolderInfo(False, "ext4", Path("/")),
    }
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda folder: detections[Path(folder).absolute()],
    )
    window._auto_select_analysis_profile(first)
    standard = window.analysis_profile_combo.findData("standard")
    window.analysis_profile_combo.setCurrentIndex(standard)
    assert window._analysis_profile_user_override is True
    window._auto_select_analysis_profile(second)
    assert window.analysis_profile_combo.currentData() == "standard"
    assert window._analysis_profile_user_override is False
    assert window._analysis_profile_source == second.absolute()
    assert "Dossier local détecté" in window.analysis_profile_hint.text()
