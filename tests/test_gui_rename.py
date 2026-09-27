from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from file_janitor.application import ArchiveFormat, analyze_archive_folder, analyze_rename_folder
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
            result=_Signal(),
            error=_Signal(),
            progress=_Signal(),
            cancelled=_Signal(),
            finished=_Signal(),
        )


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["file-janitor-rename-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def test_rename_button_starts_recursive_read_only_analysis(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    window.folder_edit.setText(str(tmp_path))
    monkeypatch.setattr(
        gui_module.QInputDialog,
        "getText",
        lambda *args, **kwargs: ("{name}_{counter:03d}{ext}", True),
    )
    monkeypatch.setattr(gui_module, "Worker", _Worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)
    _Worker.calls.clear()

    window._open_rename_dialog()

    function, args, kwargs = _Worker.calls[0]
    assert function is gui_module.analyze_rename_folder
    assert args == (tmp_path, "{name}_{counter:03d}{ext}")
    assert kwargs["recursive"] is True
    assert window._active_analysis_profile == "rename"
    assert window.cancel_analysis_button.text() == "Annuler le renommage"


def test_rename_analysis_uses_standard_preview_and_selectable_action(
    window: MainWindow, tmp_path: Path,
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"image")
    result = analyze_rename_folder(tmp_path, "{name}_archive{ext}")

    window._show_result(result)

    assert window.category_table.rowCount() == 1
    assert window.category_table.item(0, 0).text() == "Noms proposés"
    assert window.category_table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "rename"
    assert window.details_table.rowCount() == 1
    assert window.details_table.item(0, 1).text() == "Renommer"
    assert window.details_table.item(0, 4).text().endswith("photo_archive.jpg")
    assert window.details_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_rename_never_uses_opt_in_unsafe_fast_move(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"image")
    window._show_result(analyze_rename_folder(tmp_path, "{name}_new{ext}"))
    window._selected_actions = {("rename", 0)}
    window.execution_mode_combo.setCurrentIndex(1)
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gui_module.QMessageBox,
        "question",
        lambda _parent, title, message, *_args: (
            prompts.append((title, message))
            or QMessageBox.StandardButton.Yes
        ),
    )
    monkeypatch.setattr(gui_module, "Worker", _Worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)
    _Worker.calls.clear()

    window._confirm_execution()

    assert _Worker.calls
    assert "allow_unsafe_fast_move" not in _Worker.calls[-1][2]
    assert prompts[0][0] == "Renommer les fichiers sélectionnés ?"
    assert "Mode rapide rclone/FUSE actif" not in prompts[0][1]
