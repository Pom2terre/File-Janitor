"""Tests de l’annulation utilisateur d’une analyse GUI."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import file_janitor.gui.main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["file-janitor-analysis-cancel-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def test_analysis_cancel_button_requests_cooperative_cancel(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            self.cancel_requested = False
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

        def request_cancel(self) -> None:
            self.cancel_requested = True

    captured: dict[str, DummyWorker] = {}

    def make_worker(function: object, *args: object, **kwargs: object) -> DummyWorker:
        worker = DummyWorker(function, *args, **kwargs)
        captured["worker"] = worker
        return worker

    monkeypatch.setattr(main_window_module, "Worker", make_worker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)

    window.folder_edit.setText(str(tmp_path))
    window._start_analysis()

    assert window.cancel_analysis_button.isHidden() is False
    assert window.cancel_analysis_button.isEnabled() is True

    window._cancel_analysis()

    assert captured["worker"].cancel_requested is True
    assert window.cancel_analysis_button.isEnabled() is False
    assert (
        window.status_label.text()
        == "Annulation de l'analyse Standard demandée…"
    )


def test_analysis_cancelled_clears_partial_state(window: MainWindow) -> None:
    window.summary_label.setText("stale")
    window._analysis_cancelled()

    assert window.summary_label.text() == ""
    assert window.status_label.text() == "Analyse Standard annulée."
    assert window.activity_label.text() == "Analyse Standard annulée — 00:00:00"
