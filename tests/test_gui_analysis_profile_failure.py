"""Tests 4E-6B : le profil actif reste visible lorsqu'une analyse échoue."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def _wait_until(predicate, *, app, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout en attendant la fin de l'analyse GUI")
        app.processEvents()
        time.sleep(0.005)


def _select_profile(window: MainWindow, value: str) -> None:
    index = window.analysis_profile_combo.findData(value)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


@pytest.mark.parametrize(
    ("profile", "label"),
    (
        ("standard", "Standard"),
        ("remote", "Remote / rapide"),
    ),
)
def test_failed_analysis_reports_active_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    label: str,
) -> None:
    app = create_application([f"janitor-gui-analysis-error-profile-{profile}"])
    window = MainWindow()
    messages: list[tuple[str, str]] = []

    def fail(folder: Path, *, config=None, **kwargs: object) -> None:
        raise ValueError("scan impossible")

    monkeypatch.setattr(main_window_module, "analyze_folder", fail)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )

    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, profile)
    window._start_analysis()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == f"L'analyse {label} a échoué."
    assert messages == [("Erreur d'analyse", "scan impossible")]
    assert window._active_analysis_profile is None
    window.close()


def test_failed_analysis_uses_profile_frozen_at_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_application(["janitor-gui-analysis-error-frozen-profile"])
    window = MainWindow()
    entered = threading.Event()
    release = threading.Event()

    def fail(folder: Path, *, config=None, **kwargs: object) -> None:
        entered.set()
        release.wait(timeout=1.0)
        raise ValueError("scan impossible")

    monkeypatch.setattr(main_window_module, "analyze_folder", fail)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda *args, **kwargs: None,
    )

    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "remote")
    window._start_analysis()

    assert entered.wait(timeout=1.0)
    # Une modification programmatique de la combo ne doit pas réétiqueter
    # l'opération déjà lancée.
    _select_profile(window, "standard")
    release.set()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.status_label.text() == "L'analyse Remote / rapide a échoué."
    window.close()
