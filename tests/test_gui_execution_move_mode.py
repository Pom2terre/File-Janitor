"""Le mode MOVE est visible et choisi pour chaque exécution GUI."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from file_janitor.application import (
    AnalysisItem, AnalysisResult, AnalysisSummary, CategoryDetails, RemoteFolderInfo,
)
from file_janitor.gui import main_window as gui_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.preferences import GuiPreferences


def _preferences(tmp_path: Path) -> GuiPreferences:
    return GuiPreferences(QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat))


class _Signal:
    def connect(self, callback) -> None:
        pass


class _Worker:
    calls: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calls.append(kwargs)
        self.signals = SimpleNamespace(
            result=_Signal(),
            error=_Signal(),
            progress=_Signal(),
            finished=_Signal(),
        )


@pytest.mark.parametrize(
    ("prefers_fast", "chosen_index", "expected_fast"),
    [
        (False, None, False),
        (False, 1, True),
        (True, 0, False),
        (True, None, True),
    ],
)
def test_preview_choice_controls_execution_only(
    tmp_path: Path,
    monkeypatch,
    prefers_fast: bool,
    chosen_index: int | None,
    expected_fast: bool,
) -> None:
    create_application(["janitor-execution-mode-test"])
    preferences = _preferences(tmp_path)
    if prefers_fast:
        preferences.set_fast_remote_move_enabled(True)
    window = MainWindow(preferences=preferences)

    combo = window.execution_mode_combo
    assert combo.count() == 2
    assert combo.itemData(0) is False
    assert combo.itemData(1) is True
    assert combo.isHidden() is False
    assert combo.currentIndex() == (1 if prefers_fast else 0)
    if chosen_index is not None:
        combo.setCurrentIndex(chosen_index)

    prompts: list[tuple[str, QMessageBox.StandardButton]] = []

    def confirm(parent, title, message, buttons, default):
        prompts.append((message, default))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_module.QMessageBox, "question", confirm)
    monkeypatch.setattr(gui_module, "duplicate_groups_for_selection", lambda *args: ())
    monkeypatch.setattr(gui_module, "Worker", _Worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)
    _Worker.calls.clear()
    window._analysis_result = SimpleNamespace(details_for=lambda key: None)
    window._selected_actions = {("to_sort", 0)}

    window._confirm_execution()

    assert len(_Worker.calls) == 1
    assert _Worker.calls[0].get("allow_unsafe_fast_move", False) is expected_fast
    assert prompts[0][1] == QMessageBox.StandardButton.No
    assert ("Mode rapide rclone/FUSE actif" in prompts[0][0]) is expected_fast
    assert preferences.fast_remote_move_enabled() is prefers_fast
    window.close()


def test_rejecting_fast_confirmation_does_not_start_worker(tmp_path: Path, monkeypatch) -> None:
    create_application(["janitor-execution-mode-cancel-test"])
    window = MainWindow(preferences=_preferences(tmp_path))
    window.execution_mode_combo.setCurrentIndex(1)
    window._analysis_result = SimpleNamespace(details_for=lambda key: None)
    window._selected_actions = {("to_sort", 0)}
    monkeypatch.setattr(gui_module, "duplicate_groups_for_selection", lambda *args: ())
    monkeypatch.setattr(
        gui_module.QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(gui_module, "Worker", _Worker)
    _Worker.calls.clear()

    window._confirm_execution()

    assert _Worker.calls == []
    assert window._active_worker is None
    window.close()


def test_preferences_change_updates_visible_choice(tmp_path: Path, monkeypatch) -> None:
    create_application(["janitor-execution-mode-pref-test"])
    preferences = _preferences(tmp_path)
    window = MainWindow(preferences=preferences)

    class AcceptedPreferences:
        def __init__(self, preferences, parent):
            pass

        def exec(self):
            preferences.set_fast_remote_move_enabled(True)
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(gui_module, "PreferencesDialog", AcceptedPreferences)
    window._open_preferences()

    assert window.execution_mode_combo.currentData() is True
    window.close()


@pytest.mark.parametrize(
    ("choice", "expected_fast", "expected_workers"),
    [(True, True, 1), (False, False, 1), (None, False, 0)],
)
def test_large_pcloud_move_offers_explicit_fast_choice(
    tmp_path: Path, monkeypatch, choice, expected_fast, expected_workers
) -> None:
    create_application(["janitor-large-remote-move-choice-test"])
    window = MainWindow()
    root = tmp_path / "pcloud"
    root.mkdir()
    item = AnalysisItem(
        path=root / "video.mp4",
        size=2 * 1024**3,
        reason="à classer",
        destination=root / "2026-09" / "video.mp4",
        action="move",
    )
    window._analysis_result = AnalysisResult(
        summary=AnalysisSummary(root=root, total_count=1, total_size=item.size, categories=()),
        details=(CategoryDetails(key="to_sort", label="Fichiers à classer", items=(item,)),),
    )
    window._selected_actions = {("to_sort", 0)}
    monkeypatch.setattr(
        gui_module, "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse", root, "pCloud.fs"),
    )
    monkeypatch.setattr(gui_module, "Worker", _Worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)
    monkeypatch.setattr(
        window, "_confirm_large_remote_execution", lambda *args: choice
    )
    monkeypatch.setattr(
        gui_module.QMessageBox, "question",
        lambda *args: pytest.fail("une seule confirmation est requise"),
    )
    _Worker.calls.clear()

    window._confirm_execution()

    assert len(_Worker.calls) == expected_workers
    if expected_workers:
        assert _Worker.calls[0].get("allow_unsafe_fast_move", False) is expected_fast
    assert window.execution_mode_combo.currentData() is expected_fast
    window.close()


def test_large_remote_dialog_explains_copy_and_overwrite_risk(
    monkeypatch,
) -> None:
    create_application(["janitor-large-remote-dialog-test"])
    window = MainWindow()

    def select_fast(dialog):
        assert "2.1 Go" in dialog.text()
        assert "recopier" in dialog.informativeText()
        assert "écraser" in dialog.informativeText()
        next(button for button in dialog.buttons() if "mode rapide" in button.text()).click()
        return 0

    monkeypatch.setattr(gui_module.QMessageBox, "exec", select_fast)
    assert window._confirm_large_remote_execution(1, 2 * 1024**3, "") is True
    window.close()
