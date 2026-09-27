from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


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


def _run(code: str, *args: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *(str(arg) for arg in args)],
        cwd=_REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_restart_keeps_unverified_published_move_planned_and_idempotent(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"

    crash_writer = r"""
import os
from pathlib import Path
import sys

from file_janitor.storage.history import HistoryStore, OperationStatus

db = Path(sys.argv[1])
source = Path(sys.argv[2])
destination = Path(sys.argv[3])

source.write_text("moved-before-crash", encoding="utf-8")
destination.parent.mkdir(parents=True, exist_ok=True)
source.replace(destination)

store = HistoryStore(db_path=db)
batch_id = store.start_batch(str(db.parent), planned_count=1)
store.record_operation(
    batch_id,
    kind="move",
    original_path=source,
    stored_path=destination,
    size=destination.stat().st_size,
    category="to_sort",
    status=OperationStatus.PLANNED,
)

os._exit(31)
"""

    first = _run(crash_writer, db, source, destination)
    assert first.returncode == 31, first.stderr

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "moved-before-crash"
    before = destination.stat()

    recovery_reader = r"""
import json
from pathlib import Path
import sys

from file_janitor.application import run_startup_crash_recovery
from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
report = run_startup_crash_recovery(db_path=db)

store = HistoryStore(db_path=db)
batch = store.list_batches(limit=1)[0]
operation = store.get_operations(batch.id)[0]

print(json.dumps({
    "promoted": list(report.promoted_operation_ids),
    "failed": list(report.failed_operation_ids),
    "annotated": list(report.annotated_operation_ids),
    "batch_status": batch.status.value,
    "operation_status": operation.status.value,
    "error": operation.error,
    "success_count": batch.success_count,
    "failed_count": batch.failed_count,
    "skipped_count": batch.skipped_count,
}))
store.close()
"""

    second = _run(recovery_reader, db)
    assert second.returncode == 0, second.stderr
    first_recovery = json.loads(second.stdout)

    assert first_recovery["promoted"] == []
    assert first_recovery["failed"] == []
    assert len(first_recovery["annotated"]) == 1
    assert first_recovery["batch_status"] == "running"
    assert first_recovery["operation_status"] == "planned"
    assert "objet publié observé mais identité non vérifiable" in first_recovery["error"]
    assert "aucune action automatique" in first_recovery["error"]
    assert first_recovery["success_count"] == 0
    assert first_recovery["failed_count"] == 0
    assert first_recovery["skipped_count"] == 0

    after_first = destination.stat()
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "moved-before-crash"
    assert after_first.st_ino == before.st_ino
    assert after_first.st_size == before.st_size
    assert after_first.st_mtime_ns == before.st_mtime_ns

    third = _run(recovery_reader, db)
    assert third.returncode == 0, third.stderr
    second_recovery = json.loads(third.stdout)

    assert second_recovery["promoted"] == []
    assert second_recovery["failed"] == []
    assert second_recovery["annotated"] == first_recovery["annotated"]
    assert second_recovery["batch_status"] == "running"
    assert second_recovery["operation_status"] == "planned"
    assert second_recovery["error"] == first_recovery["error"]

    after_second = destination.stat()
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "moved-before-crash"
    assert after_second.st_ino == before.st_ino
    assert after_second.st_size == before.st_size
    assert after_second.st_mtime_ns == before.st_mtime_ns


def test_restart_keeps_filesystem_ambiguous_move_planned_and_untouched(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"

    crash_writer = r"""
import os
from pathlib import Path
import sys

from file_janitor.storage.history import HistoryStore, OperationStatus

db = Path(sys.argv[1])
source = Path(sys.argv[2])
destination = Path(sys.argv[3])

source.write_text("source-copy", encoding="utf-8")
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text("destination-copy", encoding="utf-8")

store = HistoryStore(db_path=db)
batch_id = store.start_batch(str(db.parent), planned_count=1)
store.record_operation(
    batch_id,
    kind="move",
    original_path=source,
    stored_path=destination,
    size=source.stat().st_size,
    category="to_sort",
    status=OperationStatus.PLANNED,
)

os._exit(32)
"""

    first = _run(crash_writer, db, source, destination)
    assert first.returncode == 32, first.stderr

    source_before = source.stat()
    destination_before = destination.stat()

    recovery_reader = r"""
import json
from pathlib import Path
import sys

from file_janitor.application import run_startup_crash_recovery
from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
report = run_startup_crash_recovery(db_path=db)

store = HistoryStore(db_path=db)
batch = store.list_batches(limit=1)[0]
operation = store.get_operations(batch.id)[0]

print(json.dumps({
    "promoted": list(report.promoted_operation_ids),
    "failed": list(report.failed_operation_ids),
    "annotated": list(report.annotated_operation_ids),
    "batch_status": batch.status.value,
    "operation_status": operation.status.value,
    "error": operation.error,
}))
store.close()
"""

    second = _run(recovery_reader, db)
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)

    assert payload["promoted"] == []
    assert payload["failed"] == []
    assert len(payload["annotated"]) == 1
    assert payload["batch_status"] == "running"
    assert payload["operation_status"] == "planned"
    assert "état filesystem ambigu" in payload["error"]
    assert "aucune action automatique" in payload["error"]

    source_after = source.stat()
    destination_after = destination.stat()

    assert source.read_text(encoding="utf-8") == "source-copy"
    assert destination.read_text(encoding="utf-8") == "destination-copy"

    assert source_after.st_ino == source_before.st_ino
    assert source_after.st_size == source_before.st_size
    assert source_after.st_mtime_ns == source_before.st_mtime_ns

    assert destination_after.st_ino == destination_before.st_ino
    assert destination_after.st_size == destination_before.st_size
    assert destination_after.st_mtime_ns == destination_before.st_mtime_ns
