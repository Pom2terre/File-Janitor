"""Tests du cycle de vie des workers rattachés à la fenêtre principale."""

from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.workers import Worker


def test_closing_window_cancels_all_tracked_worker_signals() -> None:
    app = create_application(["janitor-gui-worker-lifecycle-test"])
    window = MainWindow()
    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    results: list[int] = []

    def blocked(index: int) -> int:
        started[index].set()
        release.wait(timeout=1.0)
        return index

    workers = [Worker(blocked, 0), Worker(blocked, 1)]
    for worker in workers:
        worker.signals.result.connect(results.append)
        window._start_worker(worker)

    assert all(event.wait(timeout=1.0) for event in started)
    assert len(window._workers) == 2

    window.close()
    assert window._closing is True
    assert window._workers == set()

    release.set()
    window._thread_pool.waitForDone()
    app.processEvents()

    assert results == []


def test_window_refuses_new_worker_after_close() -> None:
    app = create_application(["janitor-gui-worker-after-close-test"])
    window = MainWindow()
    called: list[bool] = []

    window.close()
    worker = Worker(lambda: called.append(True))
    window._start_worker(worker)
    app.processEvents()

    assert called == []
    assert window._workers == set()
