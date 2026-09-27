"""Tests 4E-34C : parcours CLI réel de reprise des opérations QUEUED."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import BatchStatus, HistoryStore, OperationStatus


PROJECT_ROOT = Path(__file__).parents[1]


def _queued_move(
    tmp_path: Path,
) -> tuple[Path, Path, int, int, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "remote"
    root.mkdir()
    source = root / "source.txt"
    source.write_bytes(b"payload")
    destination = root / "sorted" / source.name
    database = home / ".file_janitor" / "history.db"

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
    return home, database, batch_id, operation_id, source, destination


def _run_cli(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["COLUMNS"] = "80"
    environment["NO_COLOR"] = "1"
    environment["TERM"] = "dumb"
    return subprocess.run(
        [sys.executable, "-m", "file_janitor.cli", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


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


def test_cli_history_preview_and_apply_resume_the_same_persisted_operation(
    tmp_path: Path,
) -> None:
    (
        home,
        database,
        batch_id,
        operation_id,
        source,
        destination,
    ) = _queued_move(tmp_path)
    before = _history_rows(database)

    history = _run_cli(home, "history")

    assert history.returncode == 0, history.stderr
    assert f"janitor resume {batch_id}" in history.stdout
    assert "1 candidate" in history.stdout

    preview = _run_cli(home, "resume", str(batch_id))

    assert preview.returncode == 0, preview.stderr
    assert "Prête" in preview.stdout
    assert "Aperçu uniquement" in preview.stdout
    assert _history_rows(database) == before
    assert source.read_bytes() == b"payload"
    assert not destination.exists()

    applied = _run_cli(
        home,
        "resume",
        str(batch_id),
        "--apply",
        "--yes",
    )

    assert applied.returncode == 0, applied.stderr
    assert "1 opération(s) reprise(s)" in applied.stdout
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


def test_cli_resume_blocks_changed_source_without_mutation(tmp_path: Path) -> None:
    home, database, batch_id, _operation_id, source, destination = (
        _queued_move(tmp_path)
    )
    source.write_bytes(b"payload changed after persistence")
    before = _history_rows(database)

    history = _run_cli(home, "history")
    preview = _run_cli(home, "resume", str(batch_id))
    applied = _run_cli(
        home,
        "resume",
        str(batch_id),
        "--apply",
        "--yes",
    )

    assert history.returncode == 0, history.stderr
    assert f"janitor resume {batch_id}" in history.stdout
    assert preview.returncode == 0, preview.stderr
    assert "Bloquée" in preview.stdout
    assert "source_identity_changed" in preview.stdout.replace("\n", "")
    assert "Aperçu uniquement" in preview.stdout
    assert applied.returncode == 1
    assert "Reprise refusée" in applied.stdout
    assert _history_rows(database) == before
    assert source.read_bytes() == b"payload changed after persistence"
    assert not destination.exists()
