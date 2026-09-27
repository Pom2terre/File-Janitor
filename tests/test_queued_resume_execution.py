"""Tests 4E-33A : reprise contrôlée des opérations QUEUED."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.application import resume_queued_operations
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import BatchStatus, HistoryStore, OperationStatus


def _rows(database: Path) -> tuple[list[tuple], list[tuple]]:
    connection = sqlite3.connect(database)
    try:
        batches = connection.execute(
            "SELECT id, status, planned_count, success_count, failed_count, "
            "skipped_count FROM batches ORDER BY id"
        ).fetchall()
        operations = connection.execute(
            "SELECT id, batch_id, kind, original_path, stored_path, status, "
            "error FROM operations ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return batches, operations


def _queued_batch(
    tmp_path: Path,
    *,
    kinds: tuple[str, ...] = ("move",),
) -> tuple[Path, int, list[tuple[Path, Path | None]]]:
    database = tmp_path / "history.db"
    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(tmp_path), planned_count=len(kinds))
    paths: list[tuple[Path, Path | None]] = []
    for index, kind in enumerate(kinds, start=1):
        source = tmp_path / f"source-{index}.txt"
        source.write_bytes(f"payload-{index}".encode())
        destination = (
            tmp_path / "sorted" / source.name
            if kind in {"copy", "move"}
            else None
        )
        identity = current_file_identity(source)
        store.record_operation(
            batch_id,
            kind=kind,
            original_path=source,
            stored_path=destination,
            size=identity.size,
            category="to_sort",
            status=OperationStatus.QUEUED,
            original_identity=identity,
            conflict_policy=("rename" if destination is not None else None),
        )
        paths.append((source, destination))
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=0,
        failed_count=0,
        skipped_count=len(kinds),
    )
    store.close()
    return database, batch_id, paths


@pytest.mark.parametrize("kind", ["move", "copy"])
def test_resume_executes_in_place_without_duplicate_rows(
    tmp_path: Path,
    kind: str,
) -> None:
    database, batch_id, paths = _queued_batch(tmp_path, kinds=(kind,))
    source, destination = paths[0]

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.batch_id == batch_id
    assert result.success == 1
    assert result.errors == ()
    assert result.cancelled is False
    assert result.skipped == 0
    batches, operations = _rows(database)
    assert batches == [(batch_id, "completed", 1, 1, 0, 0)]
    assert len(operations) == 1
    assert operations[0][1] == batch_id
    assert operations[0][5:] == ("completed", None)
    assert destination is not None
    assert destination.read_bytes() == b"payload-1"
    assert source.exists() is (kind == "copy")


def test_resume_delete_uses_existing_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import file_janitor.executor as executor_module

    trash = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash)
    database, batch_id, paths = _queued_batch(tmp_path, kinds=("delete",))
    source, _destination = paths[0]

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.success == 1
    assert not source.exists()
    assert len(list(trash.iterdir())) == 1
    _batches, operations = _rows(database)
    assert len(operations) == 1
    assert operations[0][5] == "completed"


def test_resume_blocks_all_operations_before_mutation_if_one_is_invalid(
    tmp_path: Path,
) -> None:
    database, batch_id, paths = _queued_batch(
        tmp_path,
        kinds=("move", "move"),
    )
    valid_source, valid_destination = paths[0]
    missing_source, _missing_destination = paths[1]
    missing_source.unlink()
    before = _rows(database)

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.success == 0
    assert result.skipped == 2
    assert "source_unavailable_or_unsafe" in result.errors[0]
    assert valid_source.exists()
    assert valid_destination is not None and not valid_destination.exists()
    assert _rows(database) == before


def test_resume_cancellation_leaves_every_operation_queued(
    tmp_path: Path,
) -> None:
    database, batch_id, paths = _queued_batch(
        tmp_path,
        kinds=("move", "copy"),
    )
    before = _rows(database)

    result = resume_queued_operations(
        batch_id,
        db_path=database,
        cancel_callback=lambda: True,
    )

    assert result.cancelled is True
    assert result.success == 0
    assert result.skipped == 2
    assert _rows(database) == before
    assert all(source.exists() for source, _destination in paths)
    assert all(
        destination is None or not destination.exists()
        for _source, destination in paths
    )


def test_resume_revalidates_source_after_public_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import file_janitor.application.service as service_module

    database, batch_id, paths = _queued_batch(tmp_path)
    source, destination = paths[0]
    real_execute = service_module.execute_items

    def change_source_then_execute(items, **kwargs):
        source.write_bytes(b"changed-after-preflight")
        return real_execute(items, **kwargs)

    monkeypatch.setattr(
        service_module,
        "execute_items",
        change_source_then_execute,
    )

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.success == 0
    assert len(result.errors) == 1
    assert destination is not None and not destination.exists()
    batches, operations = _rows(database)
    assert batches == [(batch_id, "failed", 1, 0, 1, 0)]
    assert len(operations) == 1
    assert operations[0][5] == "failed"


def test_resume_preserves_prior_results_and_recomputes_batch_counts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.db"
    completed_source = tmp_path / "already-moved.txt"
    completed_destination = tmp_path / "sorted" / completed_source.name
    queued_source = tmp_path / "queued.txt"
    queued_source.write_bytes(b"queued")
    identity = current_file_identity(queued_source)
    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(tmp_path), planned_count=2)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=completed_source,
        stored_path=completed_destination,
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
        conflict_policy="rename",
    )
    store.record_operation(
        batch_id,
        kind="move",
        original_path=queued_source,
        stored_path=tmp_path / "sorted" / queued_source.name,
        size=identity.size,
        category="to_sort",
        status=OperationStatus.QUEUED,
        original_identity=identity,
        conflict_policy="rename",
    )
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=1,
        failed_count=0,
        skipped_count=1,
    )
    store.close()

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.success == 1
    assert _rows(database)[0] == [(batch_id, "completed", 2, 2, 0, 0)]
    assert len(_rows(database)[1]) == 2


def test_batch_resume_claim_is_atomic_across_store_instances(
    tmp_path: Path,
) -> None:
    database, batch_id, _paths = _queued_batch(tmp_path)
    first = HistoryStore(db_path=database)
    second = HistoryStore(db_path=database)

    first.start_batch_resume(batch_id)
    with pytest.raises(ValueError, match="déjà active"):
        second.start_batch_resume(batch_id)

    assert first.get_batch(batch_id).status is BatchStatus.RUNNING
    first.close()
    second.close()


def test_unknown_persisted_category_blocks_without_mutation(tmp_path: Path) -> None:
    database, batch_id, paths = _queued_batch(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE operations SET category = 'mystery' WHERE batch_id = ?",
        (batch_id,),
    )
    connection.commit()
    connection.close()
    before = _rows(database)

    result = resume_queued_operations(batch_id, db_path=database)

    assert result.success == 0
    assert result.skipped == 1
    assert "unsupported_category" in result.errors[0]
    assert _rows(database) == before
    source, destination = paths[0]
    assert source.exists()
    assert destination is not None and not destination.exists()
