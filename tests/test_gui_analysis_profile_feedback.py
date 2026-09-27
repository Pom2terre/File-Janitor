"""Tests du feedback de progression associé au profil réellement lancé."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import file_janitor.gui.main_window as main_window_module
from file_janitor.application import AnalysisResult, AnalysisSummary
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["file-janitor-analysis-profile-feedback-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _select_profile(window: MainWindow, profile: str) -> None:
    index = window.analysis_profile_combo.findData(profile)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


def _capture_worker(monkeypatch: pytest.MonkeyPatch, window: MainWindow) -> None:
    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            self.signals = SimpleNamespace(
                result=DummySignal(), error=DummySignal(), progress=DummySignal(),
                cancelled=DummySignal(), finished=DummySignal(),
            )

        def request_cancel(self) -> None:
            pass

    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)


def _result(root: Path) -> AnalysisResult:
    return AnalysisResult(
        summary=AnalysisSummary(root=root, total_count=0, total_size=0, categories=()),
        details=(),
    )


@pytest.mark.parametrize(
    ("profile", "label"), (("standard", "Standard"), ("remote", "Remote / rapide"))
)
def test_analysis_start_feedback_names_launched_profile(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    profile: str, label: str,
) -> None:
    _capture_worker(monkeypatch, window)
    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, profile)

    window._start_analysis()

    assert window._active_analysis_profile == profile
    assert window.status_label.text() == f"Analyse {label} en cours…"
    assert window.activity_label.text() == f"Analyse {label} en cours… — 00:00:00"


@pytest.mark.parametrize(
    ("profile", "label"), (("standard", "Standard"), ("remote", "Remote / rapide"))
)
def test_analysis_progress_keeps_launched_profile(
    window: MainWindow, profile: str, label: str,
) -> None:
    window._active_analysis_profile = profile
    expected = {
        "metadata": "Lecture des métadonnées",
        "hashes": "Recherche des doublons",
        "content": "Détection des types de contenu",
        "planning": "Préparation du plan",
        "result": "Préparation des résultats",
    }
    for stage, phase in expected.items():
        window._analysis_progress(stage)
        assert window.status_label.text() == f"{phase} · {label}…"
        assert window.activity_label.text() == f"{phase} · {label}… — 00:00:00"

    previous = window.status_label.text()
    window._analysis_progress("unknown-stage")
    assert window.status_label.text() == previous


def test_feedback_uses_frozen_profile_even_if_combo_changes(window: MainWindow) -> None:
    window._active_analysis_profile = "remote"
    _select_profile(window, "standard")

    window._analysis_progress("planning")

    assert window.status_label.text() == "Préparation du plan · Remote / rapide…"


def test_success_feedback_keeps_launched_profile(window: MainWindow, tmp_path: Path) -> None:
    window._active_analysis_profile = "remote"
    _select_profile(window, "standard")

    window._analysis_succeeded(_result(tmp_path))

    assert window.status_label.text() == "Analyse Remote / rapide terminée."
    assert window.activity_label.text() == "Analyse Remote / rapide terminée — 00:00:00"


def test_cancellation_feedback_keeps_frozen_profile(window: MainWindow) -> None:
    window._active_analysis_profile = "remote"
    window._analysis_cancelled()
    assert window.status_label.text() == "Analyse Remote / rapide annulée."
    assert window.activity_label.text() == "Analyse Remote / rapide annulée — 00:00:00"


def test_finished_clears_frozen_profile(window: MainWindow) -> None:
    window._active_analysis_profile = "remote"
    window._analysis_finished()
    assert window._active_analysis_profile is None


def test_analysis_elapsed_status_uses_monotonic_clock(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [100.0]
    monkeypatch.setattr(main_window_module, "monotonic", lambda: now[0])

    window._start_analysis_clock("Inventaire cloud via rclone")
    now[0] = 165.0
    window._update_analysis_elapsed()

    assert window.activity_label.text() == "Inventaire cloud via rclone — 00:01:05"
    window._stop_analysis_clock()
