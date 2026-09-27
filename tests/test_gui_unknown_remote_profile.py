"""Tests 4E-5B : profil conservé lorsque le filesystem est indéterminé."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox

from file_janitor.application import RemoteFolderInfo
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["janitor-gui-unknown-remote-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _select_profile(window: MainWindow, value: str) -> None:
    index = window.analysis_profile_combo.findData(value)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


def _unknown_info(path: Path) -> RemoteFolderInfo:
    return RemoteFolderInfo(False, None, None)


@pytest.mark.parametrize("profile", ["standard", "remote"])
def test_unknown_filesystem_preserves_current_profile(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    _select_profile(window, profile)
    monkeypatch.setattr(main_window_module, "remote_folder_info", _unknown_info)

    window._auto_select_analysis_profile(tmp_path)

    assert window.analysis_profile_combo.currentData() == profile
    hint = window.analysis_profile_hint.text().lower()
    assert "indéterminé" in hint
    assert "conservé" in hint
    assert "dossier local" not in hint


def test_unknown_new_source_clears_old_override_but_preserves_profile(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_source = tmp_path / "old"
    new_source = tmp_path / "new"
    _select_profile(window, "remote")
    window._analysis_profile_source = old_source.expanduser().absolute()
    window._analysis_profile_user_override = True
    monkeypatch.setattr(main_window_module, "remote_folder_info", _unknown_info)

    window._auto_select_analysis_profile(new_source)

    assert window.analysis_profile_combo.currentData() == "remote"
    assert window._analysis_profile_source == new_source.expanduser().absolute()
    assert window._analysis_profile_user_override is False


def test_unknown_standard_analysis_does_not_trigger_remote_guard(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _select_profile(window, "standard")
    monkeypatch.setattr(main_window_module, "remote_folder_info", _unknown_info)
    asked: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: (
            asked.append(args) or QMessageBox.StandardButton.No
        ),
    )

    assert window._confirm_slow_remote_analysis(tmp_path) is True
    assert asked == []
