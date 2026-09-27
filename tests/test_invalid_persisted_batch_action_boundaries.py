"""4E-25C: frontières fail-closed des métadonnées de batch persistées."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.application.service import (
    resolve_history_recovery_without_file_action,
)
from file_janitor.executor import undo_batch
from file_janitor.recovery import (
    reconcile_published_planned_operations,
    resolve_no_published_planned_operations,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


_INVALID_BATCH_CASES = (
    pytest.param({"root": b"invalid-root"}, id="root"),
    pytest.param({"undone": 2}, id="undone"),
    pytest.param({"planned_count": -1}, id="planned-count"),
    pytest.param({"success_count": "bad"}, id="success-count"),
    pytest.param({"failed_count": b"bad"}, id="failed-count"),
    pytest.param({"skipped_count": -2}, id="skipped-count"),
    pytest.param(
        {
            "planned_count": 1,
            "success_count": 2,
            "failed_count": 0,
            "skipped_count": 0,
        },
        id="inconsistent-counts",
    ),
)

_MUTATION_CASES = (
    pytest.param({"root": b"invalid-root"}, id="root"),
    pytest.param(
        {"planned_count": 1, "success_count": 2},
        id="inconsistent-counts",
    ),
)


def _update_batch(
    store: HistoryStore,
    batch_id: int,
    updates: dict[str, object],
) -> None:
    allowed = {
        "root",
        "undone",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
        "status",
    }
    assert set(updates).issubset(allowed)
    assignments = ", ".join(f"{column} = ?" for column in updates)
    with store._cursor() as cursor:
        cursor.execute(
            f"UPDATE batches SET {assignments} WHERE id = ?",
            (*updates.values(), batch_id),
        )


def _insert_operation(
    store: HistoryStore,
    batch_id: int,
    *,
    original_path: Path,
    stored_path: Path,
    status: str,
    with_identity: bool = False,
) -> int:
    identity = stored_path.stat() if with_identity else None
    with store._cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO operations (
                batch_id,
                kind,
                original_path,
                stored_path,
                size,
                category,
                status,
                stored_device,
                stored_inode,
                stored_size,
                stored_mtime_ns
            )
            VALUES (?, 'copy', ?, ?, 1, 'documents', ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                str(original_path),
                str(stored_path),
                status,
                identity.st_dev if identity is not None else None,
                identity.st_ino if identity is not None else None,
                identity.st_size if identity is not None else None,
                identity.st_mtime_ns if identity is not None else None,
            ),
        )
        operation_id = cursor.lastrowid
    assert operation_id is not None
    return int(operation_id)


def _completed_batch(store: HistoryStore, root: Path) -> int:
    batch_id = store.start_batch(str(root), planned_count=1)
    stored = root / f"stored-{batch_id}.txt"
    original = root / f"original-{batch_id}.txt"
    _insert_operation(
        store,
        batch_id,
        original_path=original,
        stored_path=stored,
        status=OperationStatus.COMPLETED.value,
    )
    _update_batch(
        store,
        batch_id,
        {
            "status": BatchStatus.COMPLETED.value,
            "success_count": 1,
        },
    )
    return batch_id


def _running_batch_with_operation(
    store: HistoryStore,
    root: Path,
    *,
    published: bool,
) -> tuple[int, int, Path]:
    batch_id = store.start_batch(str(root), planned_count=1)
    original = root / f"original-{batch_id}.txt"
    stored = root / f"stored-{batch_id}.txt"
    original.write_text("original", encoding="utf-8")
    if published:
        stored.write_text("published", encoding="utf-8")
    operation_id = _insert_operation(
        store,
        batch_id,
        original_path=original,
        stored_path=stored,
        status=OperationStatus.PLANNED.value,
        with_identity=published,
    )
    return batch_id, operation_id, stored


def test_valid_batch_metadata_is_actionable(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.has_actionable_metadata()
    store.close()


@pytest.mark.parametrize("updates", _INVALID_BATCH_CASES)
def test_latest_undoable_batch_skips_invalid_batch_metadata(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    valid_batch = _completed_batch(store, tmp_path)
    invalid_batch = _completed_batch(store, tmp_path)
    _update_batch(store, invalid_batch, updates)

    assert store.latest_undoable_batch_id() == valid_batch
    store.close()


@pytest.mark.parametrize("updates", _MUTATION_CASES)
def test_direct_undo_refuses_invalid_batch_without_file_action(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    original = tmp_path / "original.txt"
    stored = tmp_path / "stored.txt"
    original.write_text("original", encoding="utf-8")
    stored.write_text("published", encoding="utf-8")
    operation_id = _insert_operation(
        store,
        batch_id,
        original_path=original,
        stored_path=stored,
        status=OperationStatus.COMPLETED.value,
        with_identity=True,
    )
    _update_batch(
        store,
        batch_id,
        {"status": BatchStatus.COMPLETED.value, "success_count": 1},
    )
    _update_batch(store, batch_id, updates)

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert errors == [
        f"batch #{batch_id}: métadonnées persistées invalides ; "
        "undo refusé par sécurité"
    ]
    assert stored.read_text(encoding="utf-8") == "published"
    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.COMPLETED
    assert store.get_batch(batch_id).status is BatchStatus.COMPLETED
    store.close()


@pytest.mark.parametrize("updates", _MUTATION_CASES)
def test_published_recovery_skips_invalid_batch(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, operation_id, stored = _running_batch_with_operation(
        store,
        tmp_path,
        published=True,
    )
    _update_batch(store, batch_id, updates)

    assert reconcile_published_planned_operations(store) == []
    assert stored.exists()
    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()


@pytest.mark.parametrize("updates", _MUTATION_CASES)
def test_no_publication_recovery_skips_invalid_batch(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, operation_id, stored = _running_batch_with_operation(
        store,
        tmp_path,
        published=False,
    )
    _update_batch(store, batch_id, updates)

    assert resolve_no_published_planned_operations(store) == []
    assert not stored.exists()
    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.PLANNED
    assert operation.recovery_state is None
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()


@pytest.mark.parametrize("updates", _MUTATION_CASES)
def test_manual_recovery_resolution_rejects_invalid_batch(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _update_batch(store, batch_id, updates)
    store.close()

    with pytest.raises(
        ValueError,
        match="clôture recovery refusée : métadonnées de batch incohérentes",
    ):
        resolve_history_recovery_without_file_action(
            batch_id,
            db_path=db_path,
        )


@pytest.mark.parametrize("updates", _MUTATION_CASES)
def test_running_batch_reconciliation_skips_invalid_batch(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _update_batch(store, batch_id, updates)

    assert store.reconcile_running_batches() == []
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()
