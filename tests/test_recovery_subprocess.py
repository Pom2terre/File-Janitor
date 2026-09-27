from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

_REPO_ROOT = Path(__file__).parents[1]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_REPO_ROOT) if not old else os.pathsep.join((str(_REPO_ROOT), old))
    return env


def _run(code: str, *args: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *(str(a) for a in args)],
        cwd=_REPO_ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_restart_recovers_published_planned_without_replay(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "published.txt"

    writer = r"""
import os, sys
from pathlib import Path
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import HistoryStore, OperationStatus

db = Path(sys.argv[1])
dst = Path(sys.argv[2])
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text("published-before-crash", encoding="utf-8")
store = HistoryStore(db_path=db)
batch_id = store.start_batch(str(db.parent), planned_count=1)
op_id = store.record_operation(
    batch_id,
    kind="copy",
    original_path=db.parent / "source.txt",
    stored_path=dst,
    size=dst.stat().st_size,
    category="to_sort",
    status=OperationStatus.PLANNED,
)
store.set_operation_stored_identity(op_id, current_file_identity(dst))
os._exit(23)
"""
    first = _run(writer, db, destination)
    assert first.returncode == 23, first.stderr
    before = destination.stat()

    reader = r"""
import json, sys
from pathlib import Path
from file_janitor.application import run_startup_crash_recovery
from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
report = run_startup_crash_recovery(db_path=db)
store = HistoryStore(db_path=db)
batch = store.list_batches(limit=1)[0]
op = store.get_operations(batch.id)[0]
print(json.dumps({
    "promoted": list(report.promoted_operation_ids),
    "failed": list(report.failed_operation_ids),
    "annotated": list(report.annotated_operation_ids),
    "batch_status": batch.status.value,
    "operation_status": op.status.value,
    "success_count": batch.success_count,
    "failed_count": batch.failed_count,
    "skipped_count": batch.skipped_count,
}))
store.close()
"""
    second = _run(reader, db)
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)

    assert len(payload["promoted"]) == 1
    assert payload["failed"] == []
    assert payload["annotated"] == []
    assert payload["batch_status"] == "completed"
    assert payload["operation_status"] == "completed"
    assert payload["success_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["skipped_count"] == 0

    after = destination.stat()
    assert destination.read_text(encoding="utf-8") == "published-before-crash"
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


def test_restart_fails_closed_when_no_publication_observed(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"

    writer = r"""
import os, sys
from pathlib import Path
from file_janitor.storage.history import HistoryStore, OperationStatus

db = Path(sys.argv[1])
src = Path(sys.argv[2])
dst = Path(sys.argv[3])
src.write_text("still-at-source", encoding="utf-8")
store = HistoryStore(db_path=db)
batch_id = store.start_batch(str(db.parent), planned_count=1)
store.record_operation(
    batch_id,
    kind="move",
    original_path=src,
    stored_path=dst,
    size=src.stat().st_size,
    category="to_sort",
    status=OperationStatus.PLANNED,
)
os._exit(24)
"""
    first = _run(writer, db, source, destination)
    assert first.returncode == 24, first.stderr
    before = source.stat()

    reader = r"""
import json, sys
from pathlib import Path
from file_janitor.application import run_startup_crash_recovery
from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
report = run_startup_crash_recovery(db_path=db)
store = HistoryStore(db_path=db)
batch = store.list_batches(limit=1)[0]
op = store.get_operations(batch.id)[0]
print(json.dumps({
    "promoted": list(report.promoted_operation_ids),
    "failed": list(report.failed_operation_ids),
    "annotated": list(report.annotated_operation_ids),
    "batch_status": batch.status.value,
    "operation_status": op.status.value,
    "error": op.error,
    "success_count": batch.success_count,
    "failed_count": batch.failed_count,
    "skipped_count": batch.skipped_count,
}))
store.close()
"""
    second = _run(reader, db)
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)

    assert payload["promoted"] == []
    assert len(payload["failed"]) == 1
    assert payload["annotated"] == []
    assert payload["batch_status"] == "failed"
    assert payload["operation_status"] == "failed"
    assert payload["success_count"] == 0
    assert payload["failed_count"] == 1
    assert payload["skipped_count"] == 0
    assert "récupération après crash" in payload["error"]
    assert "non rejouée automatiquement" in payload["error"]

    after = source.stat()
    assert source.read_text(encoding="utf-8") == "still-at-source"
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns
    assert not destination.exists()
