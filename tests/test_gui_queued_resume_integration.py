"""Tests 4E-35A : parcours GUI réel de reprise des opérations QUEUED."""

from __future__ import annotations

import os
import sqlite3
import time
from functools import partial
from pathlib import Path
from threading import Event

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from file_janitor.application import (
    get_history_operation_summary,
    get_history_summary,
    resume_queued_operations,
)
from file_janitor.gui import main_window as main_window_module
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import BatchStatus, HistoryStore, OperationStatus


def _queued_move(
    tmp_path: Path,
) -> tuple[Path, int, int, Path, Path]:
    database = tmp_path / "history.db"
    root = tmp_path / "remote"
    root.mkdir()
    source = root / "source.txt"
    source.write_bytes(b"payload")
    destination = root / "sorted" / source.name
    identity = current_file_identity(source)

    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(root), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=identity.size,
        category="to_sort",
        status=OperationStatus.QUEUED,
        original_identity=identity,
        conflict_policy="rename",
    )
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=0,
        failed_count=0,
        skipped_count=1,
    )
    store.close()
    return database, batch_id, operation_id, source, destination


def _queued_moves(
    tmp_path: Path,
) -> tuple[Path, int, tuple[int, ...], tuple[Path, ...], tuple[Path, ...]]:
    database = tmp_path / "history.db"
    root = tmp_path / "remote"
    root.mkdir()
    sources = tuple(root / f"source-{index}.txt" for index in range(1, 3))
    destinations = tuple(root / "sorted" / source.name for source in sources)
    for index, source in enumerate(sources, start=1):
        source.write_bytes(f"payload-{index}".encode())

    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(root), planned_count=len(sources))
    operation_ids = tuple(
        store.record_operation(
            batch_id,
            kind="move",
            original_path=source,
            stored_path=destination,
            size=(identity := current_file_identity(source)).size,
            category="to_sort",
            status=OperationStatus.QUEUED,
            original_identity=identity,
            conflict_policy="rename",
        )
        for source, destination in zip(sources, destinations, strict=True)
    )
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=0,
        failed_count=0,
        skipped_count=len(sources),
    )
    store.close()
    return database, batch_id, operation_ids, sources, destinations


def _history_rows(database: Path) -> tuple[list[tuple], list[tuple]]:
    connection = sqlite3.connect(database)
    try:
        batches = connection.execute(
            "SELECT id, status, planned_count, success_count, failed_count, "
            "skipped_count FROM batches ORDER BY id"
        ).fetchall()
        operations = connection.execute(
            "SELECT id, batch_id, status, original_path, stored_path, error "
            "FROM operations ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return batches, operations


def _bind_real_history(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
) -> None:
    monkeypatch.setattr(
        main_window_module,
        "get_history_summary",
        partial(get_history_summary, db_path=database),
    )
    monkeypatch.setattr(
        main_window_module,
        "get_history_operation_summary",
        partial(get_history_operation_summary, db_path=database),
    )
    monkeypatch.setattr(
        main_window_module,
        "resume_queued_operations",
        partial(resume_queued_operations, db_path=database),
    )


def _wait_until(predicate, *, app, message: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(f"timeout GUI : {message}")
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def _wait_for_history(window: MainWindow, *, app) -> None:
    _wait_until(
        lambda: (
            window._active_worker is None
            and window._history_operation_worker is None
        ),
        app=app,
        message="chargement de l'historique",
    )


def _accept_questions_and_capture_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple], list[tuple]]:
    warnings: list[tuple] = []
    criticals: list[tuple] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda *args, **kwargs: criticals.append(args),
    )
    return warnings, criticals


def test_gui_history_resume_executes_real_worker_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, batch_id, operation_id, source, destination = _queued_move(tmp_path)
    _bind_real_history(monkeypatch, database)
    warnings, criticals = _accept_questions_and_capture_messages(monkeypatch)
    app = create_application(["janitor-gui-queued-resume-integration-test"])
    window = MainWindow()
    progress_events: list[object] = []
    real_progress = window._execution_progress

    def record_progress(value: object) -> None:
        progress_events.append(value)
        real_progress(value)

    monkeypatch.setattr(window, "_execution_progress", record_progress)
    try:
        window._refresh_history(preferred_batch_id=batch_id)
        _wait_for_history(window, app=app)

        assert window._selected_history_batch_id() == batch_id
        assert window.resume_queued_button.isHidden() is False
        assert window.resume_queued_button.isEnabled() is True
        assert window.resume_queued_button.property("resumeCandidateCount") == 1

        window._confirm_queued_resume()
        assert window._active_worker is not None
        assert window._active_operation == "execution"
        _wait_for_history(window, app=app)

        assert warnings == []
        assert criticals == []
        assert any(
            isinstance(event, tuple) and event[0] == "execution"
            for event in progress_events
        )
        assert any(
            isinstance(event, tuple) and event[0] == "execution_done"
            for event in progress_events
        )
        assert window.status_label.text() == "Reprise terminée."
        assert window.execution_summary_label.text() == (
            f"Batch #{batch_id} — 1 action réussie, 0 erreurs."
        )
        assert window.resume_queued_button.isHidden() is True
        assert not source.exists()
        assert destination.read_bytes() == b"payload"
        batches, operations = _history_rows(database)
        assert batches == [(batch_id, "completed", 1, 1, 0, 0)]
        assert operations == [
            (
                operation_id,
                batch_id,
                "completed",
                str(source),
                str(destination),
                None,
            )
        ]
    finally:
        window._thread_pool.waitForDone(5_000)
        window.close()


def test_gui_history_resume_cancel_stops_before_next_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        database,
        batch_id,
        operation_ids,
        sources,
        destinations,
    ) = _queued_moves(tmp_path)
    _bind_real_history(monkeypatch, database)
    first_action_done = Event()
    release_worker = Event()

    def gated_resume(
        selected_batch_id: int,
        *,
        progress_callback=None,
        cancel_callback=None,
    ):
        def gated_progress(value: object) -> None:
            if progress_callback is not None:
                progress_callback(value)
            if (
                isinstance(value, tuple)
                and len(value) >= 2
                and value[0] == "execution_done"
                and value[1] == 1
            ):
                first_action_done.set()
                if not release_worker.wait(timeout=5):
                    raise TimeoutError("annulation GUI non reçue")

        return resume_queued_operations(
            selected_batch_id,
            db_path=database,
            progress_callback=gated_progress,
            cancel_callback=cancel_callback,
        )

    monkeypatch.setattr(
        main_window_module,
        "resume_queued_operations",
        gated_resume,
    )
    warnings, criticals = _accept_questions_and_capture_messages(monkeypatch)
    app = create_application(["janitor-gui-queued-resume-cancel-integration-test"])
    window = MainWindow()
    try:
        window._refresh_history(preferred_batch_id=batch_id)
        _wait_for_history(window, app=app)

        assert window.resume_queued_button.isEnabled() is True
        assert window.resume_queued_button.property("resumeCandidateCount") == 2
        window._confirm_queued_resume()
        assert window._active_worker is not None
        _wait_until(
            first_action_done.is_set,
            app=app,
            message="fin de la première action reprise",
        )

        assert window.cancel_analysis_button.text() == "Annuler la reprise"
        assert window.cancel_analysis_button.isEnabled() is True
        assert not sources[0].exists()
        assert destinations[0].read_bytes() == b"payload-1"
        assert sources[1].read_bytes() == b"payload-2"
        assert not destinations[1].exists()

        window.cancel_analysis_button.click()
        assert window.cancel_analysis_button.isEnabled() is False
        release_worker.set()
        _wait_for_history(window, app=app)

        assert warnings == []
        assert criticals == []
        assert window.status_label.text() == "Reprise interrompue."
        assert window.execution_summary_label.text() == (
            f"Batch #{batch_id} — 1 action réussie, 0 erreurs, "
            "1 non démarrée."
        )
        assert not sources[0].exists()
        assert destinations[0].read_bytes() == b"payload-1"
        assert sources[1].read_bytes() == b"payload-2"
        assert not destinations[1].exists()
        batches, operations = _history_rows(database)
        assert batches == [(batch_id, "cancelled", 2, 1, 0, 1)]
        assert operations == [
            (
                operation_ids[0],
                batch_id,
                "completed",
                str(sources[0]),
                str(destinations[0]),
                None,
            ),
            (
                operation_ids[1],
                batch_id,
                "queued",
                str(sources[1]),
                str(destinations[1]),
                None,
            ),
        ]
    finally:
        release_worker.set()
        window._thread_pool.waitForDone(5_000)
        window.close()


def test_gui_history_resume_worker_failure_restores_interaction_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, batch_id, _operation_id, source, destination = _queued_move(tmp_path)
    before = _history_rows(database)
    corrupt_database = tmp_path / "corrupt-history.db"
    corrupt_payload = b"not a sqlite database\x00file-janitor-4e35c"
    corrupt_database.write_bytes(corrupt_payload)
    _bind_real_history(monkeypatch, database)
    monkeypatch.setattr(
        main_window_module,
        "resume_queued_operations",
        partial(resume_queued_operations, db_path=corrupt_database),
    )
    warnings, criticals = _accept_questions_and_capture_messages(monkeypatch)
    app = create_application(["janitor-gui-queued-resume-failure-integration-test"])
    window = MainWindow()
    try:
        window._refresh_history(preferred_batch_id=batch_id)
        _wait_for_history(window, app=app)

        assert window.resume_queued_button.isEnabled() is True
        window._confirm_queued_resume()
        assert window._active_worker is not None
        _wait_for_history(window, app=app)

        assert warnings == []
        assert len(criticals) == 1
        assert criticals[0][1:] == (
            "Erreur de reprise",
            "L’historique SQLite est indisponible ou endommagé.",
        )
        assert window.status_label.text() == "La reprise a échoué."
        assert window._active_worker is None
        assert window._active_operation is None
        assert window.history_button.isEnabled() is True
        assert window.resume_queued_button.isEnabled() is True
        assert _history_rows(database) == before
        assert corrupt_database.read_bytes() == corrupt_payload
        assert source.read_bytes() == b"payload"
        assert not destination.exists()
    finally:
        window._thread_pool.waitForDone(5_000)
        window.close()


def test_gui_history_resume_blocks_changed_source_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, batch_id, _operation_id, source, destination = _queued_move(tmp_path)
    source.write_bytes(b"payload changed after persistence")
    before = _history_rows(database)
    _bind_real_history(monkeypatch, database)
    warnings, criticals = _accept_questions_and_capture_messages(monkeypatch)
    app = create_application(["janitor-gui-queued-resume-blocked-integration-test"])
    window = MainWindow()
    try:
        window._refresh_history(preferred_batch_id=batch_id)
        _wait_for_history(window, app=app)

        assert window.resume_queued_button.isEnabled() is True
        window._confirm_queued_resume()
        assert window._active_worker is not None
        _wait_for_history(window, app=app)

        assert len(warnings) == 1
        assert criticals == []
        assert window.status_label.text() == "Reprise terminée avec erreurs."
        assert "1 non démarrée" in window.execution_summary_label.text()
        assert window.execution_errors_table.rowCount() == 1
        assert "source_identity_changed" in (
            window.execution_errors_table.item(0, 0).text()
        )
        assert _history_rows(database) == before
        assert source.read_bytes() == b"payload changed after persistence"
        assert not destination.exists()
    finally:
        window._thread_pool.waitForDone(5_000)
        window.close()
