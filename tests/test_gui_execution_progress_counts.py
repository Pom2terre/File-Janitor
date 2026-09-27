"""Compteurs de progression visibles pour les lots, les copies et l'annulation."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def test_progress_counts_only_finished_files_and_resets_for_analysis() -> None:
    create_application(["janitor-execution-count-test"])
    window = MainWindow()

    window._execution_progress(("execution", 1, 3391, "/remote/first.mp3"))
    assert window.activity_progress.maximum() == 3391
    assert window.activity_progress.value() == 0
    assert window.activity_progress.isTextVisible() is False
    assert window.activity_percent.text() == "Lot : 0 %"
    assert window.activity_percent.isHidden() is False
    assert "Traités : 0 · restants : 3 391" in window.status_label.text()

    window._execution_progress(("execution_done", 1, 3391, "/remote/first.mp3"))
    assert window.activity_progress.value() == 1
    assert window.activity_percent.text() == "Lot : <0,1 %"
    assert "Traités : 1 · restants : 3 390" in window.activity_label.text()

    window._execution_progress(("execution", 2, 3391, "/remote/second.mp3"))
    assert window.activity_progress.value() == 1

    window._active_operation = "analysis"
    window._set_activity(True, "Analyse en cours…", "busy")
    assert window.activity_progress.minimum() == 0
    assert window.activity_progress.maximum() == 0
    assert window.activity_progress.isTextVisible() is False
    assert window.activity_percent.isHidden() is True
    window.close()


def test_safe_copy_shows_bytes_without_marking_file_done() -> None:
    create_application(["janitor-execution-copy-count-test"])
    window = MainWindow()

    window._execution_progress(
        ("execution_copy", 1, 3, "/remote/video.mkv", 5_000_000, 20_000_000, 2.0)
    )
    assert window.activity_progress.maximum() == 3
    assert window.activity_progress.value() == 0
    assert window.activity_percent.text() == "Lot : 8,3 %\nFichier : 25 %"
    assert "25%" in window.status_label.text()
    assert "5.0 Mo / 20.0 Mo" in window.status_label.text()
    assert "Traités : 0 · restants : 3" in window.status_label.text()

    window._execution_progress(("execution_done", 1, 3, "/remote/video.mkv"))
    assert window.activity_progress.value() == 1
    assert window.activity_percent.text() == "Lot : 33 %"
    assert "Traités : 1 · restants : 2" in window.activity_label.text()
    window.close()


def test_cancel_keeps_in_flight_file_in_remaining_count() -> None:
    create_application(["janitor-execution-cancel-count-test"])
    window = MainWindow()
    requested = []
    window._active_worker = SimpleNamespace(request_cancel=lambda: requested.append(True))
    window._active_operation = "execution"

    window._execution_progress(("execution", 1, 3, "/remote/first.txt"))
    window._cancel_execution()

    assert requested == [True]
    assert window.activity_progress.value() == 0
    assert window.activity_progress.maximum() == 3
    window._execution_progress(("execution_done", 1, 3, "/remote/first.txt"))
    assert window.activity_progress.value() == 1
    assert "restants : 2" in window.activity_label.text()
    window.close()
