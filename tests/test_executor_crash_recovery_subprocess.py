from __future__ import annotations

import inspect
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


def test_executor_crash_after_move_publication_before_stored_identity_is_fail_closed(
    tmp_path: Path,
) -> None:
    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"

    executor_child = r"""
import inspect
import os
from pathlib import Path
import sys

import file_janitor.executor as executor_module
from file_janitor.models import ActionItem, ActionKind, FileCategory
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
source = Path(sys.argv[2])
destination = Path(sys.argv[3])

source.write_text("executor-real-move", encoding="utf-8")

candidate_kwargs = {
    "category": FileCategory.TO_SORT,
    "path": source,
    "size": source.stat().st_size,
    "reason": "E3A crash-window test",
    "destination": destination,
    "action": ActionKind.MOVE,
    "identity": current_file_identity(source),
}
signature = inspect.signature(ActionItem)
item = ActionItem(
    **{
        key: value
        for key, value in candidate_kwargs.items()
        if key in signature.parameters
    }
)

real_move = executor_module._move_with_cross_filesystem_fallback

def crash_after_real_publication(item, effective_destination):
    real_move(item, effective_destination)

    # La publication filesystem a réellement réussi. Tuer immédiatement le
    # processus empêche execute_items() d'atteindre
    # store.set_operation_stored_identity(...).
    os._exit(41)

executor_module._move_with_cross_filesystem_fallback = crash_after_real_publication

store = HistoryStore(db_path=db)
executor_module.execute_items(
    [item],
    root=str(db.parent),
    store=store,
)

raise AssertionError("le point de crash E3A n'a pas été atteint")
"""

    crashed = _run(executor_child, db, source, destination)
    assert crashed.returncode == 41, crashed.stderr

    # Le MOVE réel de l'executor a bien publié la destination.
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "executor-real-move"
    destination_before = destination.stat()

    inspect_before_recovery = r"""
import json
from pathlib import Path
import sys

from file_janitor.storage.history import HistoryStore

db = Path(sys.argv[1])
store = HistoryStore(db_path=db)
batch = store.list_batches(limit=1)[0]
operation = store.get_operations(batch.id)[0]

print(json.dumps({
    "batch_status": batch.status.value,
    "operation_status": operation.status.value,
    "kind": operation.kind,
    "original_path": str(operation.original_path),
    "stored_path": str(operation.stored_path),
    "stored_identity": (
        None
        if operation.stored_identity is None
        else {
            "device": operation.stored_identity.device,
            "inode": operation.stored_identity.inode,
            "size": operation.stored_identity.size,
            "mtime_ns": operation.stored_identity.mtime_ns,
        }
    ),
}))
store.close()
"""

    journal = _run(inspect_before_recovery, db)
    assert journal.returncode == 0, journal.stderr
    before_recovery = json.loads(journal.stdout)

    # Vérifie que le crash provient bien de la fenêtre voulue et non d'un
    # autre point du chemin executor.
    assert before_recovery["batch_status"] == "running"
    assert before_recovery["operation_status"] == "planned"
    assert before_recovery["kind"] == "move"
    assert before_recovery["original_path"] == str(source)
    assert before_recovery["stored_path"] == str(destination)
    assert before_recovery["stored_identity"] is None

    recovery_child = r"""
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
    "stored_identity": (
        None
        if operation.stored_identity is None
        else {
            "device": operation.stored_identity.device,
            "inode": operation.stored_identity.inode,
            "size": operation.stored_identity.size,
            "mtime_ns": operation.stored_identity.mtime_ns,
        }
    ),
}))
store.close()
"""

    recovered = _run(recovery_child, db)
    assert recovered.returncode == 0, recovered.stderr
    payload = json.loads(recovered.stdout)

    assert payload["promoted"] == []
    assert payload["failed"] == []
    assert len(payload["annotated"]) == 1

    assert payload["batch_status"] == "running"
    assert payload["operation_status"] == "planned"
    assert "objet publié observé mais identité non vérifiable" in payload["error"]
    assert "aucune action automatique" in payload["error"]

    # Le recovery ne fabrique pas rétroactivement une identité qu'il ne peut
    # pas prouver issue de l'exécution originale.
    assert payload["stored_identity"] is None

    # Surtout : aucune tentative de replay/rollback automatique.
    destination_after = destination.stat()
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "executor-real-move"
    assert destination_after.st_ino == destination_before.st_ino
    assert destination_after.st_size == destination_before.st_size
    assert destination_after.st_mtime_ns == destination_before.st_mtime_ns

    # 4E-29B : l'annotation fail-closed reste stable à la seconde reprise.
    recovered_again = _run(recovery_child, db)
    assert recovered_again.returncode == 0, recovered_again.stderr
    second_payload = json.loads(recovered_again.stdout)

    assert second_payload["promoted"] == []
    assert second_payload["failed"] == []
    assert second_payload["annotated"] == payload["annotated"]
    assert second_payload["batch_status"] == payload["batch_status"]
    assert second_payload["operation_status"] == payload["operation_status"]
    assert second_payload["error"] == payload["error"]
    assert second_payload["stored_identity"] is None

    destination_after_retry = destination.stat()
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "executor-real-move"
    assert destination_after_retry.st_ino == destination_before.st_ino
    assert destination_after_retry.st_size == destination_before.st_size
    assert destination_after_retry.st_mtime_ns == destination_before.st_mtime_ns
