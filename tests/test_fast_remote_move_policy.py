"""Tests 4E-30C : copie sûre par défaut et MOVE rapide explicitement activé."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

import file_janitor.executor as executor_module
from file_janitor.application import service
from file_janitor.executor import execute_items
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.preferences import GuiPreferences
from file_janitor.gui.preferences_dialog import PreferencesDialog
from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
)
from file_janitor.storage.history import BatchStatus, HistoryStore


def _move_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test 4E-30C",
        destination=destination,
        action=ActionKind.MOVE,
        conflict_policy=ConflictPolicy.SKIP,
    )


def _unsupported_rename(source: Path, destination: Path) -> None:
    raise OSError(
        errno.EINVAL,
        "RENAME_NOREPLACE unsupported",
        str(source),
        str(destination),
    )


def _preferences(tmp_path: Path) -> GuiPreferences:
    return GuiPreferences(
        QSettings(
            str(tmp_path / "gui.ini"),
            QSettings.Format.IniFormat,
        )
    )


def test_safe_copy_remains_default_and_reports_byte_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source" * 300_000)
    destination = tmp_path / "sorted" / "source.bin"
    events: list[object] = []

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        _unsupported_rename,
    )

    def forbidden_plain_rename(*args, **kwargs):
        raise AssertionError("le mode sûr ne doit pas appeler os.rename")

    monkeypatch.setattr(executor_module.os, "rename", forbidden_plain_rename)

    store = HistoryStore(db_path=tmp_path / "history.db")
    _batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
        progress_callback=events.append,
    )
    store.close()

    assert success == 1
    assert errors == []
    assert not source.exists()
    assert destination.stat().st_size == len(b"source" * 300_000)
    copy_events = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "execution_copy"
    ]
    assert copy_events
    assert copy_events[-1][4] == destination.stat().st_size
    assert copy_events[-1][5] == destination.stat().st_size
    assert copy_events[-1][6] > 0


def test_explicit_fast_mode_uses_plain_rename_after_unsupported_noreplace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"SOURCE")
    destination = tmp_path / "sorted" / "source.bin"
    rename_calls: list[tuple[Path, Path]] = []
    real_rename = os.rename

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        _unsupported_rename,
    )

    def observing_rename(source_path: Path, destination_path: Path) -> None:
        rename_calls.append((Path(source_path), Path(destination_path)))
        real_rename(source_path, destination_path)

    monkeypatch.setattr(executor_module.os, "rename", observing_rename)

    def forbidden_copy(*args, **kwargs):
        raise AssertionError("le mode rapide ne doit pas copier les octets")

    monkeypatch.setattr(
        executor_module,
        "_copy_fd_direct_noreplace",
        forbidden_copy,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    _batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
        allow_unsafe_fast_move=True,
    )
    store.close()

    assert success == 1
    assert errors == []
    assert rename_calls == [(source, destination)]
    assert not source.exists()
    assert destination.read_bytes() == b"SOURCE"


def test_application_forwards_fast_move_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    received: list[bool] = []

    def fake_execute_items(items, *, root, store, **kwargs):
        received.append(kwargs.get("allow_unsafe_fast_move", False))
        batch_id = store.start_batch(root, planned_count=0)
        store.finish_batch_execution(
            batch_id,
            BatchStatus.COMPLETED,
            success_count=0,
            failed_count=0,
            skipped_count=0,
        )
        return batch_id, 0, []

    monkeypatch.setattr(service, "execute_items", fake_execute_items)

    service.execute_actions(
        [],
        root=str(tmp_path),
        db_path=tmp_path / "safe.db",
    )
    service.execute_actions(
        [],
        root=str(tmp_path),
        db_path=tmp_path / "fast.db",
        allow_unsafe_fast_move=True,
    )

    assert received == [False, True]


def test_fast_move_preference_defaults_to_safe_and_persists_opt_in(
    tmp_path: Path,
) -> None:
    preferences = _preferences(tmp_path)
    assert preferences.fast_remote_move_enabled() is False

    preferences.set_fast_remote_move_enabled(True)
    assert _preferences(tmp_path).fast_remote_move_enabled() is True

    preferences.reset()
    assert preferences.fast_remote_move_enabled() is False


def test_preferences_dialog_requires_explicit_risk_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    create_application(["janitor-fast-move-preference-test"])
    preferences = _preferences(tmp_path)
    dialog = PreferencesDialog(preferences)
    dialog.fast_move_checkbox.setChecked(True)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    dialog._save()
    assert preferences.fast_remote_move_enabled() is False

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._save()
    assert preferences.fast_remote_move_enabled() is True
    dialog.close()


def test_gui_formats_safe_copy_progress() -> None:
    create_application(["janitor-fast-move-progress-test"])
    window = MainWindow()

    window._execution_progress(
        ("execution_copy", 1, 3, "/remote/video.mkv", 5_000_000, 20_000_000, 2.0)
    )

    assert "Écriture sûre 1/3" in window.status_label.text()
    assert "5.0 Mo / 20.0 Mo" in window.status_label.text()
    assert "2.5 Mo/s" in window.status_label.text()
    window.close()
