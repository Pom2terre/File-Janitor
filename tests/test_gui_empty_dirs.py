from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from file_janitor.application import analyze_empty_directories
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
    app = create_application(["file-janitor-empty-dir-test"])
    assert app is not None
    return MainWindow()


def test_empty_directories_button_starts_read_only_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    window = _window()
    try:
        window.folder_edit.setText(str(root))
        monkeypatch.setattr(gui_module, "Worker", _Worker)
        monkeypatch.setattr(window, "_start_worker", lambda worker: None)
        _Worker.calls.clear()

        window._start_empty_directory_analysis()

        function, args, kwargs = _Worker.calls[0]
        assert function is gui_module.analyze_empty_directories
        assert args == (root,)
        assert kwargs["progress_kwarg"] == "progress_callback"
        assert kwargs["cancel_kwarg"] == "cancel_callback"
    finally:
        window.close()


def test_empty_directories_preview_displays_removal_action(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    empty = root / "empty"
    empty.mkdir(parents=True)
    result = analyze_empty_directories(root)
    window = _window()
    try:
        window._select_operation("empty")
        window._show_result(result)
        assert window.category_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "empty_directory"
        assert window.details_table.item(0, 1).text() == "Supprimer le dossier"
        assert window.details_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert window.details_table.item(0, 2).text().endswith("empty")
        assert window.preview_scope_label.isHidden()
        assert window.preview_scope_combo.isHidden()
        assert window.details_filter_label.isHidden()
        assert window.details_filter_hint.isHidden()
        assert window.details_table.minimumHeight() == 140
    finally:
        window.close()
