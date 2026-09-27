"""Tests 4E-4A : sélection automatique du profil d'analyse."""

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
    app = create_application(["file-janitor-auto-profile-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _profile(window: MainWindow) -> str:
    return str(window.analysis_profile_combo.currentData())


def test_choose_remote_folder_auto_selects_remote(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(
            is_remote=True,
            filesystem_type="fuse.rclone",
            mount_point=tmp_path,
        ),
    )

    window._choose_folder()

    assert window.folder_edit.text() == str(tmp_path)
    assert _profile(window) == "remote"


def test_choose_local_folder_auto_selects_standard(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_index = window.analysis_profile_combo.findData("remote")
    window.analysis_profile_combo.setCurrentIndex(remote_index)

    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(
            is_remote=False,
            filesystem_type="ext4",
            mount_point=Path("/"),
        ),
    )

    window._choose_folder()

    assert _profile(window) == "standard"


def test_manual_profile_choice_is_not_overridden_without_source_change(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_window_module.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path),
    )
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(
            is_remote=True,
            filesystem_type="fuse.rclone",
            mount_point=tmp_path,
        ),
    )

    window._choose_folder()
    assert _profile(window) == "remote"

    standard_index = window.analysis_profile_combo.findData("standard")
    window.analysis_profile_combo.setCurrentIndex(standard_index)

    window._update_analyze_button()
    assert _profile(window) == "standard"
