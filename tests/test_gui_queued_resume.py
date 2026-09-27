"""Tests 4E-33B : reprise contrôlée depuis l'historique GUI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.application import (
    ExecuteResult,
    HistoryBatchSummary,
    HistoryOperationSummary,
)
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def _prepare_history(window: MainWindow, tmp_path: Path) -> None:
    window._history_batches = (
        HistoryBatchSummary(
            id=42,
            created_at="2026-09-21 18:00",
            root=str(tmp_path),
            status="cancelled",
            undone=False,
            planned_count=2,
            success_count=0,
            failed_count=0,
            skipped_count=2,
        ),
    )
    window.history_table.setRowCount(1)
    window.history_table.selectRow(0)


def _queued_operation(
    tmp_path: Path,
    *,
    name: str,
    candidate: bool,
    blockers: tuple[str, ...] = (),
) -> HistoryOperationSummary:
    return HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / name,
        stored_path=tmp_path / "sorted" / name,
        size=1,
        category="to_sort",
        status="queued",
        error=None,
        conflict_policy="rename" if candidate else None,
        resume_candidate=candidate,
        resume_blockers=blockers,
    )


def test_history_enables_manual_resume_only_when_every_queued_row_is_candidate(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-queued-resume-ready-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    operations = (
        _queued_operation(tmp_path, name="first.txt", candidate=True),
        _queued_operation(tmp_path, name="second.txt", candidate=True),
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        operations,
    )

    assert window.resume_queued_button.isHidden() is False
    assert window.resume_queued_button.isEnabled() is True
    assert window.resume_queued_button.property("resumeReady") is True
    assert window.resume_queued_button.property("resumeCandidateCount") == 2
    assert window.resume_queued_button.property("resumeBlockedCount") == 0
    assert "Reprendre les opérations" in window.resume_queued_button.text()
    window.close()


def test_history_shows_but_disables_resume_when_one_row_is_blocked(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-queued-resume-blocked-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    operations = (
        _queued_operation(tmp_path, name="ready.txt", candidate=True),
        _queued_operation(
            tmp_path,
            name="blocked.txt",
            candidate=False,
            blockers=("missing_original_identity",),
        ),
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        operations,
    )

    assert window.resume_queued_button.isHidden() is False
    assert window.resume_queued_button.isEnabled() is False
    assert window.resume_queued_button.property("resumeReady") is False
    assert "bloquée" in window.history_operations_status_label.text()
    assert "bloquée" in window.resume_queued_button.toolTip()
    window.close()


def test_resume_confirmation_cancel_does_not_start_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_application(["janitor-gui-queued-resume-cancel-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    window.resume_queued_button.setProperty("resumeCandidateCount", 1)
    window.resume_queued_button.setProperty("resumeBlockedCount", 0)
    window.resume_queued_button.setProperty("resumeReady", True)
    started = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(window, "_start_worker", started.append)

    window._confirm_queued_resume()

    assert started == []
    assert window._active_worker is None
    window.close()


def test_resume_confirmation_starts_cancellable_progress_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_application(["janitor-gui-queued-resume-start-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    window.resume_queued_button.setVisible(True)
    window.resume_queued_button.setEnabled(True)
    window.resume_queued_button.setProperty("resumeCandidateCount", 2)
    window.resume_queued_button.setProperty("resumeBlockedCount", 0)
    window.resume_queued_button.setProperty("resumeReady", True)
    started = []
    questions: list[str] = []

    def accept(_parent, _title, message, *_args, **_kwargs):
        questions.append(message)
        return main_window_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(main_window_module.QMessageBox, "question", accept)
    monkeypatch.setattr(window, "_start_worker", started.append)

    window._confirm_queued_resume()

    assert len(started) == 1
    worker = started[0]
    assert worker._function is main_window_module.resume_queued_operations
    assert worker._args == (42,)
    assert worker._progress_kwarg == "progress_callback"
    assert worker._cancel_kwarg == "cancel_callback"
    assert worker._return_result_on_cancel is True
    assert window._active_worker is worker
    assert window._active_operation == "execution"
    assert window.cancel_analysis_button.isHidden() is False
    assert window.cancel_analysis_button.text() == "Annuler la reprise"
    assert window.activity_progress.maximum() == 2
    assert "mode sûr" in questions[0]
    window.close()


def test_resume_result_reuses_execution_summary_and_refresh_target(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-queued-resume-result-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)

    window._queued_resume_succeeded(
        ExecuteResult(
            batch_id=42,
            success=1,
            errors=(),
            cancelled=False,
            skipped=0,
        )
    )

    assert window.status_label.text() == "Reprise terminée."
    assert window.execution_summary_label.text() == (
        "Batch #42 — 1 action réussie, 0 erreurs."
    )
    assert window._last_batch_id == 42
    assert window._pending_history_refresh is True
    assert window._pending_history_batch_id == 42
    window.close()


def test_resume_preflight_refusal_surfaces_unstarted_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_application(["janitor-gui-queued-resume-refusal-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    warnings = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )

    window._queued_resume_succeeded(
        ExecuteResult(
            batch_id=42,
            success=0,
            errors=("opération #1 : reprise bloquée",),
            cancelled=False,
            skipped=2,
        )
    )

    assert window.status_label.text() == "Reprise terminée avec erreurs."
    assert "2 non démarrées" in window.execution_summary_label.text()
    assert window._last_batch_id is None
    assert len(warnings) == 1
    window.close()


def test_resume_finish_refreshes_same_batch_and_restores_interaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_application(["janitor-gui-queued-resume-finish-test"])
    window = MainWindow()
    _prepare_history(window, tmp_path)
    window._active_worker = object()  # type: ignore[assignment]
    window._active_operation = "execution"
    window._pending_history_refresh = True
    window._pending_history_batch_id = 42
    refreshed = []
    monkeypatch.setattr(
        window,
        "_refresh_history",
        lambda *, preferred_batch_id: refreshed.append(preferred_batch_id),
    )

    window._queued_resume_finished()

    assert window._active_worker is None
    assert window._active_operation is None
    assert refreshed == [42]
    assert window.history_button.isEnabled() is True
    window.close()


def test_resume_button_has_explicit_accessible_name() -> None:
    create_application(["janitor-gui-queued-resume-accessibility-test"])
    window = MainWindow()

    assert "Reprendre" in window.resume_queued_button.accessibleName()
    assert "sélectionnée" in window.resume_queued_button.accessibleName()
    window.close()


def test_resume_blocker_uses_a_user_facing_category_label(
    tmp_path: Path,
) -> None:
    operation = _queued_operation(
        tmp_path,
        name="unsupported.bin",
        candidate=False,
        blockers=("unsupported_category",),
    )

    assert MainWindow._history_resume_qualification_text(operation) == (
        "Reprise bloquée : catégorie non reprenable"
    )
