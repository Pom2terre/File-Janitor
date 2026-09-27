from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from file_janitor.application import ArchiveFormat, analyze_archive_folder
from file_janitor.gui import main_window as gui_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


class _Signal:
    def connect(self, callback: object) -> None:
        pass


class _Worker:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def __init__(self, function: object, *args: object, **kwargs: object) -> None:
        self.calls.append((function, args, kwargs))
        self.signals = SimpleNamespace(
            result=_Signal(), error=_Signal(), progress=_Signal(),
            cancelled=_Signal(), finished=_Signal(),
        )


def _window() -> MainWindow:
    app = create_application(["file-janitor-archive-test"])
    assert app is not None
    return MainWindow()


def test_archive_button_starts_preview_with_chosen_destination_and_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    window = _window()
    try:
        window.folder_edit.setText(str(source))
        monkeypatch.setattr(gui_module.QInputDialog, "getInt", lambda *a, **k: (180, True))
        monkeypatch.setattr(
            gui_module.QInputDialog,
            "getItem",
            lambda *a, **k: (
                "Dossiers conservés (arborescence actuelle)", True
            ),
        )
        monkeypatch.setattr(gui_module.QFileDialog, "getExistingDirectory", lambda *a, **k: str(destination))
        monkeypatch.setattr(gui_module, "Worker", _Worker)
        monkeypatch.setattr(window, "_start_worker", lambda worker: None)
        _Worker.calls.clear()

        window._open_archive_dialog()

        function, args, kwargs = _Worker.calls[0]
        assert function is gui_module.analyze_archive_folder
        assert args == (source, destination, 180)
        assert kwargs["progress_kwarg"] == "progress_callback"
        assert kwargs["cancel_kwarg"] == "cancel_callback"
    finally:
        window.close()


def test_archive_preview_displays_selectable_move(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    old = source / "old.txt"
    old.write_text("old")
    timestamp = datetime(2020, 1, 1).timestamp()
    os.utime(old, (timestamp, timestamp))
    result = analyze_archive_folder(
        source, tmp_path / "archive", 365, now=datetime(2026, 1, 1)
    )
    window = _window()
    try:
        window._show_result(result)
        assert window.category_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "archive"
        assert window.details_table.item(0, 1).text() == "Archiver"
        assert window.details_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    finally:
        window.close()


def test_analysis_action_buttons_highlight_one_exclusive_choice() -> None:
    window = _window()
    try:
        buttons = (
            window.analyze_button,
            window.rename_button,
            window.archive_button,
            window.empty_directories_button,
        )
        assert all(button.isCheckable() for button in buttons)
        assert not any(button.isChecked() for button in buttons)
        assert "QPushButton#primaryButton:checked" in window.styleSheet()
        assert "QPushButton#secondaryButton:checked" in window.styleSheet()
        assert all(button.property("analysisAction") == "true" for button in buttons)
        assert all(button.property("analysisSelected") == "false" for button in buttons)
        assert (
            'QPushButton#secondaryButton[analysisAction="true"]'
            '[analysisSelected="true"]'
        ) in (
            window.styleSheet()
        )

        window.archive_button.setChecked(True)
        assert window.archive_button.isChecked()
        assert window.archive_button.property("analysisSelected") == "true"
        assert window.analyze_button.property("analysisSelected") == "false"
        assert sum(button.isChecked() for button in buttons) == 1

        window.analyze_button.setChecked(True)
        assert window.analyze_button.isChecked()
        assert window.analyze_button.property("analysisSelected") == "true"
        assert window.archive_button.property("analysisSelected") == "false"
        assert not window.archive_button.isChecked()
        assert sum(button.isChecked() for button in buttons) == 1
    finally:
        window.close()


def test_general_analyze_remains_available_but_unselected_after_archive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "archive"
    source.mkdir()
    destination.mkdir()
    old = source / "old.txt"
    old.write_text("old")
    timestamp = datetime(2020, 1, 1).timestamp()
    os.utime(old, (timestamp, timestamp))
    result = analyze_archive_folder(
        source, destination, 180, now=datetime(2026, 1, 1)
    )

    window = _window()
    try:
        window.folder_edit.setText(str(source))
        window.archive_button.setChecked(True)
        window._active_analysis_profile = "archive"
        window._analysis_succeeded(result)
        window._analysis_finished()

        assert window._analysis_result is result
        assert window._last_analysis_context == window._current_analysis_context()
        assert window.analyze_button.isEnabled() is True
        assert window.analyze_button.isChecked() is False
        assert window.archive_button.isEnabled() is True
        assert window.archive_button.isChecked() is True
    finally:
        window.close()
