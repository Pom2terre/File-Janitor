"""Tests du profil d'analyse GUI Standard / Remote rapide."""
from __future__ import annotations

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
    app = create_application(["file-janitor-analysis-profile-test"])
    assert app is not None
    widget = MainWindow()
    yield widget
    widget.close()


def _select_profile(window: MainWindow, profile: str) -> None:
    index = window.analysis_profile_combo.findData(profile)
    assert index >= 0
    window.analysis_profile_combo.setCurrentIndex(index)


def test_analysis_profile_defaults_to_standard(window: MainWindow) -> None:
    assert window.analysis_profile_combo.currentData() == "standard"
    assert window.analysis_profile_combo.currentText() == "Standard"
    assert window.analysis_profile_combo.accessibleName() == "Profil d'analyse"
    assert window._analysis_compute_content_type() is True


def test_analysis_profile_is_part_of_context(window: MainWindow, tmp_path: Path) -> None:
    window.folder_edit.setText(str(tmp_path))
    standard = window._current_analysis_context()
    assert standard is not None
    assert standard[-1] == "standard"

    window._last_analysis_context = standard
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is False

    _select_profile(window, "remote")
    remote = window._current_analysis_context()
    assert remote is not None
    assert remote[-1] == "remote"
    assert remote != standard
    assert window.analyze_button.isEnabled() is True
    assert window._analysis_compute_content_type() is False


def test_analysis_profile_is_disabled_while_busy(window: MainWindow) -> None:
    assert window.analysis_profile_combo.isEnabled() is True
    window._set_busy(True)
    assert window.analysis_profile_combo.isEnabled() is False
    window._set_busy(False)
    assert window.analysis_profile_combo.isEnabled() is True


def test_remote_profile_worker_kwargs(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            captured["kwargs"] = kwargs
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)

    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "remote")
    window._start_analysis()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["compute_hashes"] is False
    assert kwargs["compute_content_type"] is False
    assert kwargs["collect_metadata"] is False


def test_remote_profile_reads_metadata_for_secondary_date(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            self.signals = SimpleNamespace(**{
                name: DummySignal() for name in ("result", "error", "progress", "cancelled", "finished")
            })

    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)
    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "remote")
    window.secondary_mode_combo.setCurrentIndex(window.secondary_mode_combo.findData(
        main_window_module.ClassificationMode.DATE
    ))
    window._start_analysis()

    assert captured["config"].secondary_group_by.value == "date"
    assert captured["compute_hashes"] is False
    assert captured["compute_content_type"] is False
    assert "collect_metadata" not in captured

def test_analysis_progress_updates_feedback(window: MainWindow) -> None:
    window._analysis_progress("metadata")
    assert window.status_label.text() == "Lecture des métadonnées · Standard…"
    assert window.activity_label.text() == "Lecture des métadonnées · Standard… — 00:00:00"
    assert window.activity_progress.isHidden() is False

    window._analysis_progress("planning")
    assert window.status_label.text() == "Préparation du plan · Standard…"

    window._analysis_progress("unknown-stage")
    assert window.status_label.text() == "Préparation du plan · Standard…"

def test_remote_profile_disables_hashes_for_any_folder(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            captured["kwargs"] = kwargs
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)

    arbitrary_remote_like_folder = tmp_path / "OneDrive" / "Images"
    arbitrary_remote_like_folder.mkdir(parents=True)

    window.folder_edit.setText(str(arbitrary_remote_like_folder))
    _select_profile(window, "remote")
    window._start_analysis()

    kwargs = captured["kwargs"]
    assert kwargs["compute_hashes"] is False
    assert kwargs["compute_content_type"] is False
    assert kwargs["progress_kwarg"] == "progress_callback"
    assert kwargs["cancel_kwarg"] == "cancel_callback"


def test_standard_profile_keeps_full_analysis(
    window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummySignal:
        def connect(self, callback: object) -> None:
            pass

    class DummyWorker:
        def __init__(self, function: object, *args: object, **kwargs: object) -> None:
            captured["kwargs"] = kwargs
            self.signals = SimpleNamespace(
                result=DummySignal(),
                error=DummySignal(),
                progress=DummySignal(),
                cancelled=DummySignal(),
                finished=DummySignal(),
            )

    monkeypatch.setattr(main_window_module, "Worker", DummyWorker)
    monkeypatch.setattr(window, "_start_worker", lambda worker: None)

    window.folder_edit.setText(str(tmp_path))
    _select_profile(window, "standard")
    window._start_analysis()

    kwargs = captured["kwargs"]
    assert kwargs["compute_hashes"] is True
    assert kwargs["compute_content_type"] is True


def test_remote_preview_displays_unknown_size_instead_of_zero(window, tmp_path):
    from file_janitor.application import (
        AnalysisCapabilities,
        AnalysisItem,
        AnalysisResult,
        AnalysisSummary,
        CategoryDetails,
        CategorySummary,
    )

    result = AnalysisResult(
        summary=AnalysisSummary(
            root=tmp_path,
            total_count=1,
            total_size=0,
            categories=(
                CategorySummary(
                    key="to_sort",
                    label="Fichiers à classer",
                    item_count=1,
                    total_size=0,
                    display_metric="size",
                ),
            ),
        ),
        details=(
            CategoryDetails(
                key="to_sort",
                label="Fichiers à classer",
                items=(
                    AnalysisItem(
                        path=tmp_path / "photo.jpg",
                        size=0,
                        reason="Classement par extension",
                        destination=tmp_path / "jpg" / "photo.jpg",
                        action="move",
                    ),
                ),
            ),
        ),
        capabilities=AnalysisCapabilities(
            hashes_computed=False,
            content_types_computed=False,
            metadata_computed=False,
        ),
    )

    window._show_result(result)
    window._show_selected_details()

    size_item = window.details_table.item(0, 3)
    assert size_item is not None
    assert size_item.text() == "Non calculée"


def test_analysis_progress_ignores_file_counts_in_status_bar(window: MainWindow) -> None:
    window._analysis_started_at = main_window_module.monotonic()
    window._analysis_progress(("metadata", 1200))
    assert "1 200" not in window.activity_label.text()
    assert window.activity_label.text().endswith("00:00:00")

    window._analysis_progress(("hashes", 25, 100))
    assert "25/100" not in window.activity_label.text()
    assert "Recherche des doublons" in window.activity_label.text()
