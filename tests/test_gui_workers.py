"""Tests de l'infrastructure de tâches Qt en arrière-plan."""

from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool

from file_janitor.gui.app import create_application
from file_janitor.gui.workers import Worker


def _wait_until(predicate, *, app, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout en attendant un signal du worker")
        app.processEvents()
        time.sleep(0.005)


def test_worker_returns_result_and_finishes() -> None:
    app = create_application(["janitor-gui-worker-test"])
    pool = QThreadPool()
    results: list[int] = []
    finished: list[bool] = []

    worker = Worker(lambda left, right: left + right, 20, 22)
    worker.signals.result.connect(results.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    pool.start(worker)
    _wait_until(lambda: bool(finished), app=app)
    pool.waitForDone()

    assert results == [42]
    assert finished == [True]


def test_worker_executes_outside_gui_thread() -> None:
    app = create_application(["janitor-gui-worker-thread-test"])
    pool = QThreadPool()
    gui_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []
    finished: list[bool] = []

    worker = Worker(threading.get_ident)
    worker.signals.result.connect(worker_thread_ids.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    pool.start(worker)
    _wait_until(lambda: bool(finished), app=app)
    pool.waitForDone()

    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != gui_thread_id


def test_worker_reports_exception_and_still_finishes() -> None:
    app = create_application(["janitor-gui-worker-error-test"])
    pool = QThreadPool()
    errors: list[tuple[Exception, str]] = []
    results: list[object] = []
    finished: list[bool] = []

    def fail() -> None:
        raise ValueError("boom")

    worker = Worker(fail)
    worker.signals.result.connect(results.append)
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    pool.start(worker)
    _wait_until(lambda: bool(finished), app=app)
    pool.waitForDone()

    assert results == []
    assert len(errors) == 1
    exception, formatted_traceback = errors[0]
    assert isinstance(exception, ValueError)
    assert str(exception) == "boom"
    assert "ValueError: boom" in formatted_traceback
    assert finished == [True]


def test_cancelled_worker_suppresses_late_signals() -> None:
    app = create_application(["janitor-gui-worker-cancel-test"])
    pool = QThreadPool()
    started = threading.Event()
    release = threading.Event()
    results: list[int] = []
    finished: list[bool] = []

    def blocked() -> int:
        started.set()
        release.wait(timeout=1.0)
        return 42

    worker = Worker(blocked)
    worker.signals.result.connect(results.append)
    worker.signals.finished.connect(lambda: finished.append(True))

    pool.start(worker)
    assert started.wait(timeout=1.0)
    worker.cancel()
    release.set()
    pool.waitForDone()
    app.processEvents()

    assert results == []
    assert finished == []

def test_worker_forwards_progress_callback() -> None:
    received: list[object] = []

    def work(*, progress_callback=None):
        assert progress_callback is not None
        progress_callback("metadata")
        progress_callback("planning")
        return 42

    worker = Worker(work, progress_kwarg="progress_callback")
    worker.signals.progress.connect(received.append)
    worker.run()

    assert received == ["metadata", "planning"]
