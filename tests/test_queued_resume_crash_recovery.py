"""Tests 4E-33C : recovery d'une reprise QUEUED interrompue."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from file_janitor.application import (
    resume_queued_operations,
    run_startup_crash_recovery,
)
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import BatchStatus, HistoryStore, OperationStatus


_REPO_ROOT = Path(__file__).parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(_REPO_ROOT)
        if not existing
        else os.pathsep.join((str(_REPO_ROOT), existing))
    )
    return env


def _run(code: str, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *(str(arg) for arg in args)],
        cwd=_REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def _queued_batch(
    tmp_path: Path,
    *,
    count: int = 2,
) -> tuple[Path, int, tuple[int, ...], tuple[tuple[Path, Path], ...]]:
    database = tmp_path / "history.db"
    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(tmp_path), planned_count=count)
    operation_ids: list[int] = []
    paths: list[tuple[Path, Path]] = []
    for index in range(1, count + 1):
        source = tmp_path / f"source-{index}.txt"
        destination = tmp_path / "sorted" / source.name
        source.write_bytes(f"payload-{index}".encode())
        identity = current_file_identity(source)
        operation_ids.append(
            store.record_operation(
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
        )
        paths.append((source, destination))
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=0,
        failed_count=0,
        skipped_count=count,
    )
    store.close()
    return database, batch_id, tuple(operation_ids), tuple(paths)


def _rows(database: Path) -> tuple[list[tuple], list[tuple]]:
    connection = sqlite3.connect(database)
    try:
        batches = connection.execute(
            "SELECT id, status, planned_count, success_count, failed_count, "
            "skipped_count FROM batches ORDER BY id"
        ).fetchall()
        operations = connection.execute(
            "SELECT id, batch_id, status, error FROM operations ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return batches, operations


def test_crash_after_resume_claim_keeps_all_queued_rows_restartable(
    tmp_path: Path,
) -> None:
    database, batch_id, operation_ids, paths = _queued_batch(tmp_path)

    resume_child = r"""
import os
from pathlib import Path
import sys

from file_janitor.application import resume_queued_operations
from file_janitor.storage.history import HistoryStore

database = Path(sys.argv[1])
batch_id = int(sys.argv[2])
real_start = HistoryStore.start_batch_resume

def crash_after_claim(self, claimed_batch_id):
    real_start(self, claimed_batch_id)
    os._exit(71)

HistoryStore.start_batch_resume = crash_after_claim
resume_queued_operations(batch_id, db_path=database)
raise AssertionError("le point de crash après réservation n'a pas été atteint")
"""

    crashed = _run(resume_child, database, batch_id)
    assert crashed.returncode == 71, crashed.stderr

    assert _rows(database) == (
        [(batch_id, "running", 2, 0, 0, 2)],
        [
            (operation_ids[0], batch_id, "queued", None),
            (operation_ids[1], batch_id, "queued", None),
        ],
    )
    assert all(
        source.read_bytes() == f"payload-{index}".encode()
        for index, (source, _destination) in enumerate(paths, start=1)
    )
    assert all(not destination.exists() for _source, destination in paths)

    first_report = run_startup_crash_recovery(db_path=database)
    after_first_recovery = _rows(database)
    assert first_report.promoted_operation_ids == ()
    assert first_report.failed_operation_ids == ()
    assert first_report.annotated_operation_ids == ()
    assert after_first_recovery == (
        [(batch_id, "failed", 2, 0, 0, 2)],
        [
            (operation_ids[0], batch_id, "queued", None),
            (operation_ids[1], batch_id, "queued", None),
        ],
    )

    second_report = run_startup_crash_recovery(db_path=database)
    assert second_report.promoted_operation_ids == ()
    assert second_report.failed_operation_ids == ()
    assert second_report.annotated_operation_ids == ()
    assert _rows(database) == after_first_recovery
    assert all(not destination.exists() for _source, destination in paths)

    resumed = resume_queued_operations(batch_id, db_path=database)
    assert resumed.success == 2
    assert resumed.errors == ()
    assert resumed.cancelled is False
    assert resumed.skipped == 0
    assert _rows(database) == (
        [(batch_id, "completed", 2, 2, 0, 0)],
        [
            (operation_ids[0], batch_id, "completed", None),
            (operation_ids[1], batch_id, "completed", None),
        ],
    )
    assert all(not source.exists() for source, _destination in paths)
    assert all(
        destination.read_bytes() == f"payload-{index}".encode()
        for index, (_source, destination) in enumerate(paths, start=1)
    )


def test_crash_after_first_resumed_action_recovers_without_replaying_it(
    tmp_path: Path,
) -> None:
    database, batch_id, operation_ids, paths = _queued_batch(tmp_path)

    resume_child = r"""
import os
from pathlib import Path
import sys

from file_janitor.application import resume_queued_operations

database = Path(sys.argv[1])
batch_id = int(sys.argv[2])

def crash_after_first_completed(event):
    if event[0] == "execution_done" and event[1] == 1:
        os._exit(72)

resume_queued_operations(
    batch_id,
    db_path=database,
    progress_callback=crash_after_first_completed,
)
raise AssertionError(
    "le point de crash après la première action n'a pas été atteint"
)
"""

    crashed = _run(resume_child, database, batch_id)
    assert crashed.returncode == 72, crashed.stderr

    first_source, first_destination = paths[0]
    second_source, second_destination = paths[1]
    assert not first_source.exists()
    assert first_destination.read_bytes() == b"payload-1"
    first_destination_before = first_destination.stat()
    assert second_source.read_bytes() == b"payload-2"
    assert not second_destination.exists()
    assert _rows(database) == (
        [(batch_id, "running", 2, 0, 0, 2)],
        [
            (operation_ids[0], batch_id, "completed", None),
            (operation_ids[1], batch_id, "queued", None),
        ],
    )

    first_report = run_startup_crash_recovery(db_path=database)
    after_first_recovery = _rows(database)
    assert first_report.promoted_operation_ids == ()
    assert first_report.failed_operation_ids == ()
    assert first_report.annotated_operation_ids == ()
    assert after_first_recovery == (
        [(batch_id, "partial", 2, 1, 0, 1)],
        [
            (operation_ids[0], batch_id, "completed", None),
            (operation_ids[1], batch_id, "queued", None),
        ],
    )

    second_report = run_startup_crash_recovery(db_path=database)
    assert second_report.promoted_operation_ids == ()
    assert second_report.failed_operation_ids == ()
    assert second_report.annotated_operation_ids == ()
    assert _rows(database) == after_first_recovery

    first_destination_after_recovery = first_destination.stat()
    assert first_destination.read_bytes() == b"payload-1"
    assert first_destination_after_recovery.st_ino == first_destination_before.st_ino
    assert (
        first_destination_after_recovery.st_mtime_ns
        == first_destination_before.st_mtime_ns
    )

    resumed = resume_queued_operations(batch_id, db_path=database)
    assert resumed.success == 1
    assert resumed.errors == ()
    assert resumed.cancelled is False
    assert resumed.skipped == 0
    assert _rows(database) == (
        [(batch_id, "completed", 2, 2, 0, 0)],
        [
            (operation_ids[0], batch_id, "completed", None),
            (operation_ids[1], batch_id, "completed", None),
        ],
    )
    first_destination_after_resume = first_destination.stat()
    assert first_destination.read_bytes() == b"payload-1"
    assert first_destination_after_resume.st_ino == first_destination_before.st_ino
    assert (
        first_destination_after_resume.st_mtime_ns
        == first_destination_before.st_mtime_ns
    )
    assert not second_source.exists()
    assert second_destination.read_bytes() == b"payload-2"
