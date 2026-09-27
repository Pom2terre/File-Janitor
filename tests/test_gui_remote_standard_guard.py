"""Tests 4E-4E : garde-fou avant analyse Standard sur stockage distant."""

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
    app = create_application(["janitor-gui-remote-standard-guard-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _select_profile(window: MainWindow, value: str) -> None:
    index = window.analysis_profile_combo.findData(value)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


def test_remote_profile_never_prompts(window, tmp_path, monkeypatch) -> None:
    _select_profile(window, "remote")
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", tmp_path),
    )
    asked = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: asked.append(args) or QMessageBox.StandardButton.No,
    )
    assert window._confirm_slow_remote_analysis(tmp_path) is True
    assert asked == []


def test_standard_local_analysis_never_prompts(window, tmp_path, monkeypatch) -> None:
    _select_profile(window, "standard")
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
    assert window._confirm_slow_remote_analysis(tmp_path) is True
    assert asked == []


def test_standard_remote_analysis_requires_confirmation(window, tmp_path, monkeypatch) -> None:
    _select_profile(window, "standard")
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", tmp_path),
    )
    captured = {}
    def question(parent, title, message, buttons, default):
        captured["title"] = title
        captured["message"] = message
        captured["default"] = default
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(main_window_module.QMessageBox, "question", question)
    assert window._confirm_slow_remote_analysis(tmp_path) is True
    assert "distant" in captured["title"].lower()
    assert "fuse.rclone" in captured["message"]
    assert "Remote / rapide" in captured["message"]
    assert captured["default"] is QMessageBox.StandardButton.No


def test_declining_standard_remote_analysis_aborts_before_worker(window, tmp_path, monkeypatch) -> None:
    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "standard")
    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", tmp_path),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    started = []
    monkeypatch.setattr(window, "_start_worker", lambda worker: started.append(worker))
    window._start_analysis()
    assert started == []
    assert window._active_worker is None
    assert window.status_label.text() == "Analyse Standard annulée avant démarrage."


def test_remote_date_mode_does_not_prompt_in_remote_profile(
    window, tmp_path, monkeypatch
) -> None:
    from file_janitor.application import ClassificationMode

    _select_profile(window, "remote")
    mode_index = window.classification_mode_combo.findData(ClassificationMode.DATE.value)
    assert mode_index >= 0
    window.classification_mode_combo.setCurrentIndex(mode_index)

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", tmp_path),
    )
    asked = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: asked.append(args) or QMessageBox.StandardButton.No,
    )

    assert window._confirm_slow_remote_analysis(tmp_path) is True
    assert asked == []


def test_remote_date_analysis_starts_and_shows_metadata_activity(
    window, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from file_janitor.application import ClassificationMode

    class DummySignal:
        def connect(self, callback):
            pass

    class DummyWorker:
        def __init__(self, function, *args, **kwargs):
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "remote")
    mode_index = window.classification_mode_combo.findData(ClassificationMode.DATE.value)
    assert mode_index >= 0
    window.classification_mode_combo.setCurrentIndex(mode_index)

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(True, "fuse.rclone", tmp_path),
    )
    asked = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: asked.append(args) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    started = []
    monkeypatch.setattr(window, "_start_worker", lambda worker: started.append(worker))

    window._start_analysis()

    assert len(started) == 1
    assert asked == []
    assert "Lecture des métadonnées" in window.status_label.text()
    assert "Remote / rapide" in window.status_label.text()
    assert not window.activity_progress.isHidden()
