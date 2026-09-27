"""Tests 4E-6C : feedback d'annulation associé au profil réellement lancé."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


@pytest.fixture
def window() -> MainWindow:
    app = create_application(["file-janitor-analysis-profile-cancel-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _select_profile(window: MainWindow, profile: str) -> None:
    index = window.analysis_profile_combo.findData(profile)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


class DummyWorker:
    def __init__(self) -> None:
        self.cancel_requested = False

    def request_cancel(self) -> None:
        self.cancel_requested = True


@pytest.mark.parametrize(
    ("profile", "label"),
    (
        ("standard", "Standard"),
        ("remote", "Remote / rapide"),
    ),
)
def test_cancel_request_reports_frozen_profile(
    window: MainWindow,
    profile: str,
    label: str,
) -> None:
    worker = DummyWorker()
    window._active_worker = worker
    window._active_analysis_profile = profile

    window._cancel_analysis()

    assert worker.cancel_requested is True
    assert window.cancel_analysis_button.isEnabled() is False
    assert (
        window.status_label.text()
        == f"Annulation de l'analyse {label} demandée…"
    )
    assert (
        window.activity_label.text()
        == f"Annulation de l'analyse {label} demandée… — 00:00:00"
    )


@pytest.mark.parametrize(
    ("profile", "label"),
    (
        ("standard", "Standard"),
        ("remote", "Remote / rapide"),
    ),
)
def test_cancelled_analysis_reports_frozen_profile(
    window: MainWindow,
    profile: str,
    label: str,
) -> None:
    window.summary_label.setText("stale")
    window._active_analysis_profile = profile

    window._analysis_cancelled()

    assert window.summary_label.text() == ""
    assert window.status_label.text() == f"Analyse {label} annulée."
    assert window.activity_label.text() == f"Analyse {label} annulée — 00:00:00"


def test_cancellation_feedback_ignores_combo_changes_after_start(
    window: MainWindow,
) -> None:
    worker = DummyWorker()
    window._active_worker = worker
    window._active_analysis_profile = "remote"
    _select_profile(window, "standard")

    window._cancel_analysis()
    assert (
        window.status_label.text()
        == "Annulation de l'analyse Remote / rapide demandée…"
    )

    window._analysis_cancelled()
    assert window.status_label.text() == "Analyse Remote / rapide annulée."
    assert window.activity_label.text() == "Analyse Remote / rapide annulée — 00:00:00"
