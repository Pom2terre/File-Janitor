"""Tests du chargement lazy de l'historique dans la GUI."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.application import (
    PlannedRecoveryState,
    HistoryBatchSummary,
    HistoryOperationSummary,
    RecoveryResolutionKind,
)
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def _wait_until(predicate, *, app, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("timeout en attendant l'historique GUI")
        app.processEvents()
        time.sleep(0.005)


def _history(tmp_path: Path) -> tuple[HistoryBatchSummary, ...]:
    return (
        HistoryBatchSummary(
            id=42, created_at="2026-09-03 14:30", root=str(tmp_path),
            status="completed", undone=False,
            planned_count=1, success_count=1, failed_count=0, skipped_count=0,
        ),
        HistoryBatchSummary(
            id=41, created_at="2026-09-03 14:00", root=str(tmp_path),
            status="undone", undone=True,
        ),
    )


def _operations(tmp_path: Path) -> tuple[HistoryOperationSummary, ...]:
    return (
        HistoryOperationSummary(
            kind="move", original_path=tmp_path / "source.txt",
            stored_path=tmp_path / "Documents" / "source.txt", size=1024,
            category="to_sort", status="completed", error=None,
        ),
    )


def test_history_refresh_loads_only_batches_before_selection(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-history-test"])
    window = MainWindow()
    calls: list[int] = []
    monkeypatch.setattr(main_window_module, "get_history_summary", lambda: _history(tmp_path))
    monkeypatch.setattr(
        main_window_module, "get_history_operation_summary",
        lambda batch_id: calls.append(batch_id) or _operations(tmp_path),
    )

    window._start_history_refresh()
    assert window.history_button.isEnabled() is False
    _wait_until(lambda: window._active_worker is None, app=app)
    _wait_until(lambda: window._history_operation_worker is None, app=app)

    assert window.history_status_label.text() == "2 exécutions récentes."
    assert window.history_table.rowCount() == 2
    assert window.history_table.item(0, 0).text() == "42"
    assert window.history_table.item(0, 3).text() == "Terminé"
    assert calls == [42]
    assert window.history_table.item(0, 4).text() == "1/1 réussie"
    assert window.history_operations_table.rowCount() == 1
    window.close()


def test_selecting_batch_loads_operations_on_background_worker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = create_application(["janitor-gui-history-operations-test"])
    window = MainWindow()
    calls: list[tuple[int, int]] = []

    def load(batch_id: int):
        calls.append((batch_id, threading.get_ident()))
        return _operations(tmp_path) if batch_id == 42 else ()

    monkeypatch.setattr(
        main_window_module,
        "get_history_operation_summary",
        load,
    )

    window._history_succeeded(_history(tmp_path))

    # _history_succeeded() sélectionne le premier batch et déclenche son
    # chargement lazy. Il faut laisser ce chargement se terminer avant de
    # tester explicitement la sélection du second batch.
    _wait_until(
        lambda: window._history_operation_worker is None,
        app=app,
    )

    assert calls
    assert calls[-1][0] == 42

    calls.clear()

    window.history_table.selectRow(1)

    _wait_until(
        lambda: window._history_operation_worker is None,
        app=app,
    )

    assert calls
    assert calls[-1][0] == 41
    assert calls[-1][1] != threading.get_ident()
    assert window.history_operations_table.rowCount() == 0

    window.close()


def test_operation_result_from_previous_history_generation_is_ignored(
    monkeypatch, tmp_path: Path
) -> None:
    app = create_application(["janitor-gui-history-generation-test"])
    window = MainWindow()
    release = threading.Event()
    calls = 0

    def load(batch_id: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            release.wait(timeout=1.0)
            return _operations(tmp_path)
        return ()

    monkeypatch.setattr(main_window_module, "get_history_operation_summary", load)
    monkeypatch.setattr(main_window_module, "get_history_summary", lambda: _history(tmp_path))
    window._history_succeeded(_history(tmp_path))
    app.processEvents()

    _wait_until(lambda: calls >= 1, app=app)

    window._refresh_history(preferred_batch_id=42)
    _wait_until(lambda: window._active_worker is None, app=app)
    _wait_until(lambda: calls >= 2, app=app)
    assert window._history_generation == 1

    _wait_until(
        lambda: window.history_table.item(0, 4).text() == "1/1 réussie",
        app=app,
    )

    release.set()
    window._thread_pool.waitForDone()
    app.processEvents()

    assert window.history_table.item(0, 4).text() == "1/1 réussie"
    assert window.history_operations_table.rowCount() == 0
    assert window._selected_history_batch_id() == 42
    window.close()


def test_stale_operation_result_does_not_replace_new_selection(monkeypatch, tmp_path: Path) -> None:
    app = create_application(["janitor-gui-history-stale-test"])
    window = MainWindow()
    release = threading.Event()

    def load(batch_id: int):
        if batch_id == 42:
            release.wait(timeout=1.0)
            return _operations(tmp_path)
        return ()

    monkeypatch.setattr(main_window_module, "get_history_operation_summary", load)
    window._history_succeeded(_history(tmp_path))
    window.history_table.selectRow(0)
    app.processEvents()
    window.history_table.selectRow(1)
    _wait_until(
        lambda: "exécution #41" in window.history_operations_status_label.text(), app=app
    )
    release.set()
    window._thread_pool.waitForDone()
    app.processEvents()

    assert window.history_table.currentRow() == 1
    assert window.history_operations_table.rowCount() == 0
    assert "exécution #41" in window.history_operations_status_label.text()
    window.close()


def test_empty_history_is_reported(monkeypatch) -> None:
    app = create_application(["janitor-gui-empty-history-test"])
    window = MainWindow()
    monkeypatch.setattr(main_window_module, "get_history_summary", lambda: ())

    window._start_history_refresh()
    _wait_until(lambda: window._active_worker is None, app=app)

    assert window.history_status_label.text() == "Aucune exécution dans l’historique."
    assert window.history_table.rowCount() == 0
    assert window.history_table.isVisible() is False
    assert window.history_operations_table.isVisible() is False
    window.close()


def test_successful_execution_refreshes_history_and_prefers_new_batch(
    monkeypatch, tmp_path: Path
) -> None:
    from file_janitor.application import ExecuteResult

    app = create_application(["janitor-gui-history-after-execution-test"])
    window = MainWindow()
    refreshes: list[int | None] = []
    monkeypatch.setattr(
        window,
        "_refresh_history",
        lambda *, preferred_batch_id: refreshes.append(preferred_batch_id),
    )

    window._execution_succeeded(ExecuteResult(batch_id=73, success=1, errors=()))
    window._execution_finished()
    app.processEvents()

    assert refreshes == [73]
    assert window._pending_history_refresh is False
    assert window._pending_history_batch_id is None
    window.close()


def test_successful_undo_refreshes_history_and_keeps_undone_batch_selected(
    monkeypatch,
) -> None:
    from file_janitor.application import UndoResult

    app = create_application(["janitor-gui-history-after-undo-test"])
    window = MainWindow()
    refreshes: list[int | None] = []
    window._last_batch_id = 73
    monkeypatch.setattr(
        window,
        "_refresh_history",
        lambda *, preferred_batch_id: refreshes.append(preferred_batch_id),
    )
    monkeypatch.setattr(
        main_window_module,
        "get_latest_undoable_batch_id",
        lambda: None,
    )

    window._undo_succeeded(UndoResult(success=1, errors=()))
    assert window._last_batch_id is None
    window._undo_finished()
    app.processEvents()

    assert refreshes == [73]
    assert window._pending_history_refresh is False
    assert window._pending_history_batch_id is None
    window.close()


def test_history_refresh_preserves_selection_or_falls_back_to_first_batch(
    monkeypatch, tmp_path: Path
) -> None:
    app = create_application(["janitor-gui-history-selection-refresh-test"])
    window = MainWindow()
    monkeypatch.setattr(
        main_window_module,
        "get_history_operation_summary",
        lambda batch_id: (),
    )

    window._history_refresh_selection_id = 41
    window._history_succeeded(_history(tmp_path))
    _wait_until(lambda: window._history_operation_worker is None, app=app)
    assert window._selected_history_batch_id() == 41

    window._history_refresh_selection_id = 999
    window._history_succeeded(_history(tmp_path))
    _wait_until(lambda: window._history_operation_worker is None, app=app)
    assert window._selected_history_batch_id() == 42

    window.close()


def test_history_status_text_labels_cancelled_batch_and_queued_operation() -> None:
    assert MainWindow._history_status_text("cancelled") == "Interrompu"
    assert MainWindow._history_status_text("queued") == "Non démarrée"


def test_history_status_exposes_queued_resume_candidate(tmp_path: Path) -> None:
    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "sorted" / "source.txt",
        size=4,
        category="to_sort",
        status="queued",
        error=None,
        conflict_policy="skip",
        resume_candidate=True,
    )

    assert MainWindow._history_operation_status_text(operation) == (
        "Candidate à la reprise"
    )
    assert MainWindow._history_resume_qualification_text(operation) == (
        "Candidate à la reprise ; validation live requise avant toute action"
    )


def test_history_status_exposes_queued_resume_blockers(tmp_path: Path) -> None:
    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "sorted" / "source.txt",
        size=4,
        category="to_sort",
        status="queued",
        error=None,
        resume_blockers=(
            "missing_original_identity",
            "missing_conflict_policy",
        ),
    )

    assert MainWindow._history_operation_status_text(operation) == (
        "Reprise bloquée"
    )
    assert MainWindow._history_resume_qualification_text(operation) == (
        "Reprise bloquée : identité source absente, "
        "politique de collision absente"
    )

def test_history_operations_highlight_unverified_recovery_attention(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-history-recovery-unverified-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operations = (
        HistoryOperationSummary(
            kind="move",
            original_path=tmp_path / "source.txt",
            stored_path=tmp_path / "sorted" / "source.txt",
            size=1024,
            category="to_sort",
            status="planned",
            error="diagnostic persistant",
            recovery_state=PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
            recovery_attention_required=True,
        ),
    )

    window._history_operations_succeeded(42, window._history_generation, operations)

    assert window.history_operations_table.item(0, 5).text() == "À vérifier après crash"
    assert "1 opération nécessite une vérification manuelle" in (
        window.history_operations_status_label.text()
    )
    assert window.history_operations_status_label.property("tone") == "warning"
    window.close()


def test_history_operations_highlight_ambiguous_recovery_attention(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-history-recovery-ambiguous-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operations = (
        HistoryOperationSummary(
            kind="move",
            original_path=tmp_path / "source.txt",
            stored_path=tmp_path / "sorted" / "source.txt",
            size=1024,
            category="to_sort",
            status="planned",
            error="diagnostic persistant",
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            recovery_attention_required=True,
        ),
    )

    window._history_operations_succeeded(42, window._history_generation, operations)

    assert window.history_operations_table.item(0, 5).text() == "État ambigu après crash"
    assert window.history_operations_status_label.property("tone") == "warning"
    window.close()


def test_history_operations_keep_normal_status_and_journal_summary(
    tmp_path: Path,
) -> None:
    create_application(["janitor-gui-history-normal-operation-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    window._history_operations_succeeded(
        42,
        window._history_generation,
        _operations(tmp_path),
    )

    assert window.history_operations_table.item(0, 5).text() == "Terminé"
    assert window.history_operations_status_label.text() == (
        "1 opération journalisée pour l’exécution #42."
    )
    assert window.history_operations_status_label.property("tone") == "success"
    window.close()


def test_history_operation_status_does_not_parse_error_text(tmp_path: Path) -> None:
    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "sorted" / "source.txt",
        size=1024,
        category="to_sort",
        status="planned",
        error="récupération après crash : état filesystem ambigu",
        recovery_state=None,
        recovery_attention_required=False,
    )

    assert MainWindow._history_operation_status_text(operation) == "Planifiée"


def test_history_recovery_resolution_requires_confirmation_and_calls_application(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.gui.main_window as main_window_module

    create_application(["janitor-gui-history-manual-recovery-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)
    window.resolve_recovery_button.setProperty("recoveryAttentionCount", 2)
    window.resolve_recovery_button.setVisible(True)
    window.resolve_recovery_button.setEnabled(True)

    calls = []
    monkeypatch.setattr(
        main_window_module,
        "resolve_history_recovery_without_file_action",
        lambda batch_id: calls.append(batch_id) or 2,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Ok,
    )
    refreshed = []
    monkeypatch.setattr(
        window,
        "_refresh_history",
        lambda *, preferred_batch_id: refreshed.append(preferred_batch_id),
    )

    window._confirm_history_recovery_resolution()

    assert calls == [42]
    assert refreshed == [42]
    window.close()


def test_history_recovery_resolution_cancel_is_noop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.gui.main_window as main_window_module

    create_application(["janitor-gui-history-manual-recovery-cancel-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)
    window.resolve_recovery_button.setProperty("recoveryAttentionCount", 1)
    window.resolve_recovery_button.setVisible(True)
    window.resolve_recovery_button.setEnabled(True)

    called = []
    monkeypatch.setattr(
        main_window_module,
        "resolve_history_recovery_without_file_action",
        lambda batch_id: called.append(batch_id) or 1,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.No,
    )

    window._confirm_history_recovery_resolution()

    assert called == []
    window.close()

def test_history_resolution_text_labels_manual_and_automatic() -> None:
    manual = HistoryOperationSummary(
        kind="move",
        original_path=Path("/tmp/a"),
        stored_path=Path("/tmp/b"),
        size=1,
        category="to_sort",
        status="failed",
        error="x",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        recovery_attention_required=False,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
    )
    automatic = HistoryOperationSummary(
        kind="move",
        original_path=Path("/tmp/c"),
        stored_path=Path("/tmp/d"),
        size=1,
        category="to_sort",
        status="failed",
        error="y",
        recovery_state=PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
        recovery_attention_required=False,
        resolution_kind=RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION,
    )

    assert MainWindow._history_resolution_text(manual) == (
        "Clôturée manuellement sans action sur les fichiers"
    )
    assert MainWindow._history_resolution_text(automatic) == (
        "Clôturée automatiquement après crash"
    )


def test_history_operations_show_resolution_audit_in_diagnostic_column(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    create_application(["janitor-gui-history-resolution-audit-test"])
    window = MainWindow()

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="failed",
        error="résolution manuelle après crash",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        recovery_attention_required=False,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        resolved_at=datetime(2026, 9, 13, 11, 0, tzinfo=timezone.utc),
    )

    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)
    window._history_generation = 7

    window._history_operations_succeeded(42, 7, (operation,))

    diagnostic = window.history_operations_table.item(0, 6).text()
    assert "Clôturée manuellement sans action sur les fichiers" in diagnostic
    assert "résolution manuelle après crash" in diagnostic
    window.close()

def test_history_operations_highlight_unknown_recovery_metadata(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
        HistoryOperationSummary,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    create_application(["janitor-gui-history-recovery-unknown-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="planned",
        error="métadonnée recovery inconnue",
        recovery_state=PlannedRecoveryState.UNKNOWN,
        recovery_attention_required=True,
        resolution_kind=RecoveryResolutionKind.UNKNOWN,
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    assert (
        window.history_operations_table.item(0, 5).text()
        == "État de récupération inconnu"
    )
    diagnostic = window.history_operations_table.item(0, 6).text()
    assert "Résolution de récupération inconnue" in diagnostic
    assert window.history_operations_status_label.property("tone") == "warning"
    window.close()

def test_history_unknown_recovery_text_includes_raw_persisted_values(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
        HistoryOperationSummary,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="planned",
        error="métadonnée recovery inconnue",
        recovery_state=PlannedRecoveryState.UNKNOWN,
        recovery_state_raw="future_recovery_state",
        recovery_attention_required=True,
        resolution_kind=RecoveryResolutionKind.UNKNOWN,
        resolution_kind_raw="future_resolution_kind",
    )

    assert MainWindow._history_operation_status_text(operation) == (
        "État de récupération inconnu (future_recovery_state)"
    )
    assert MainWindow._history_resolution_text(operation) == (
        "Résolution de récupération inconnue (future_resolution_kind)"
    )

def test_history_diagnostic_exposes_malformed_resolved_at_raw_value(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
        HistoryOperationSummary,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    create_application(["janitor-gui-history-malformed-resolved-at-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="failed",
        error="resolved",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        recovery_attention_required=False,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        resolved_at=None,
        resolved_at_raw="not-a-valid-timestamp",
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    diagnostic = window.history_operations_table.item(0, 6).text()
    assert "horodatage de résolution invalide" in diagnostic.lower()
    assert "not-a-valid-timestamp" in diagnostic
    window.close()

def test_history_diagnostic_exposes_recovery_audit_inconsistency(
    tmp_path: Path,
) -> None:
    from file_janitor.application import HistoryOperationSummary

    create_application(["janitor-gui-history-audit-inconsistency-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="failed",
        error="diagnostic persistant",
        recovery_audit_issue="resolution_kind_without_resolved_at",
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    diagnostic = window.history_operations_table.item(0, 6).text()
    assert "Audit recovery incohérent" in diagnostic
    assert "type de résolution sans horodatage" in diagnostic
    assert "diagnostic persistant" in diagnostic
    window.close()

def test_history_recovery_button_is_disabled_for_inconsistent_audit(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
        HistoryOperationSummary,
        PlannedRecoveryState,
    )

    create_application(["janitor-gui-history-recovery-audit-block-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="planned",
        error="diagnostic persistant",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        recovery_attention_required=True,
        recovery_audit_issue="resolution_kind_without_resolved_at",
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    assert window.resolve_recovery_button.isHidden() is False
    assert window.resolve_recovery_button.isEnabled() is False
    assert window.resolve_recovery_button.property("recoveryAttentionCount") == 1
    assert window.resolve_recovery_button.property("recoveryBlockedCount") == 1
    assert "clôture manuelle est bloquée" in (
        window.history_operations_status_label.text()
    )
    window.close()


def test_history_recovery_confirmation_is_noop_when_audit_is_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.gui.main_window as main_window_module

    create_application(["janitor-gui-history-recovery-audit-block-confirm-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)
    window.resolve_recovery_button.setProperty("recoveryAttentionCount", 1)
    window.resolve_recovery_button.setProperty("recoveryBlockedCount", 1)
    window.resolve_recovery_button.setVisible(True)
    window.resolve_recovery_button.setEnabled(False)

    calls = []
    monkeypatch.setattr(
        main_window_module,
        "resolve_history_recovery_without_file_action",
        lambda batch_id: calls.append(batch_id) or 1,
    )
    questions = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: questions.append(args)
        or main_window_module.QMessageBox.StandardButton.Yes,
    )

    window._confirm_history_recovery_resolution()

    assert calls == []
    assert questions == []
    window.close()

def test_history_renders_raw_invalid_core_metadata(tmp_path: Path) -> None:
    from file_janitor.application import (
        HistoryBatchSummary,
        HistoryOperationSummary,
    )

    create_application(["janitor-gui-history-invalid-core-metadata-test"])
    window = MainWindow()

    batch = HistoryBatchSummary(
        id=42,
        created_at="Horodatage invalide",
        root=str(tmp_path),
        status="unknown",
        undone=False,
        created_at_raw="bad-created-at",
        status_raw="future_batch_status",
    )

    window._history_succeeded((batch,))

    assert window.history_table.item(0, 1).text() == (
        "Horodatage invalide (bad-created-at)"
    )
    assert window.history_table.item(0, 3).text() == (
        "État inconnu (future_batch_status)"
    )

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="unknown",
        status_raw="future_operation_status",
        error=None,
    )

    assert MainWindow._history_operation_status_text(operation) == (
        "État inconnu (future_operation_status)"
    )
    window.close()

def test_unknown_operation_status_does_not_enable_manual_recovery(
    tmp_path: Path,
) -> None:
    from file_janitor.application import (
        HistoryOperationSummary,
        PlannedRecoveryState,
    )

    create_application(["janitor-gui-history-unknown-operation-capability-test"])
    window = MainWindow()
    window._history_batches = _history(tmp_path)
    window.history_table.setRowCount(2)
    window.history_table.selectRow(0)

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="unknown",
        status_raw="future_operation_status",
        error=None,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        recovery_attention_required=False,
    )

    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    assert window.resolve_recovery_button.isHidden() is True
    assert window.resolve_recovery_button.isEnabled() is False
    assert window.resolve_recovery_button.property("recoveryAttentionCount") == 0
    assert MainWindow._history_operation_status_text(operation) == (
        "État inconnu (future_operation_status)"
    )
    window.close()

def test_history_renders_core_metadata_issue_diagnostics(tmp_path: Path) -> None:
    from file_janitor.application import (
        HistoryBatchSummary,
        HistoryOperationSummary,
    )

    create_application(["janitor-gui-history-core-metadata-issues-test"])
    window = MainWindow()

    batch = HistoryBatchSummary(
        id=42,
        created_at="Horodatage invalide",
        root=str(tmp_path),
        status="unknown",
        undone=False,
        created_at_raw="bad-created-at",
        status_raw="future_batch_status",
        core_metadata_issues=("invalid_created_at", "unknown_status"),
    )
    window._history_succeeded((batch,))

    timestamp_tooltip = window.history_table.item(0, 1).toolTip()
    status_tooltip = window.history_table.item(0, 3).toolTip()
    assert "Métadonnées cœur incohérentes" in timestamp_tooltip
    assert "horodatage invalide" in timestamp_tooltip
    assert "état inconnu" in timestamp_tooltip
    assert status_tooltip == timestamp_tooltip

    operation = HistoryOperationSummary(
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status="unknown",
        status_raw="future_operation_status",
        error="diagnostic persistant",
        core_metadata_issues=("unknown_status",),
    )

    window._history_batches = (batch,)
    window.history_table.selectRow(0)
    window._history_operations_succeeded(
        42,
        window._history_generation,
        (operation,),
    )

    diagnostic = window.history_operations_table.item(0, 6).text()
    assert "Métadonnées cœur incohérentes" in diagnostic
    assert "état inconnu" in diagnostic
    assert "diagnostic persistant" in diagnostic
    window.close()


def test_history_renders_invalid_stored_identity_diagnostic() -> None:
    from types import SimpleNamespace

    from file_janitor.gui.main_window import MainWindow

    operation = SimpleNamespace(
        stored_identity_issue="invalid_stored_identity",
    )

    assert MainWindow._history_stored_identity_issue_text(
        operation,
    ) == "Identité de fichier persistée incohérente"


def test_history_omits_stored_identity_diagnostic_for_normal_identity() -> None:
    from types import SimpleNamespace

    from file_janitor.gui.main_window import MainWindow

    operation = SimpleNamespace(stored_identity_issue=None)

    assert (
        MainWindow._history_stored_identity_issue_text(operation)
        is None
    )
