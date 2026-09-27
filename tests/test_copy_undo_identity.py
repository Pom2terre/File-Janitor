"""Tests 2G-H : identité persistante et undo COPY protégé."""

from __future__ import annotations

import os
from pathlib import Path

from file_janitor.executor import execute_items, undo_batch
from file_janitor.models import ActionItem, ActionKind, FileCategory
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _copy_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test identité undo COPY",
        destination=destination,
        action=ActionKind.COPY,
    )


def _execute_copy(
    tmp_path: Path,
) -> tuple[HistoryStore, int, Path, Path]:
    source = tmp_path / "source.txt"
    source.write_text("ORIGINAL")
    destination = tmp_path / "copies" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert source.read_text() == "ORIGINAL"
    assert destination.read_text() == "ORIGINAL"

    return store, batch_id, source, destination


def test_copy_persists_published_file_identity(tmp_path: Path) -> None:
    store, batch_id, _source, destination = _execute_copy(tmp_path)

    operation = store.get_operations(batch_id)[0]
    stat_result = destination.stat()

    assert operation.kind == "copy"
    assert operation.status is OperationStatus.COMPLETED
    assert operation.stored_identity is not None
    assert operation.stored_identity.device == stat_result.st_dev
    assert operation.stored_identity.inode == stat_result.st_ino
    assert operation.stored_identity.size == stat_result.st_size
    assert operation.stored_identity.mtime_ns == stat_result.st_mtime_ns

    store.close()


def test_copy_undo_succeeds_when_identity_is_unchanged(tmp_path: Path) -> None:
    store, batch_id, source, destination = _execute_copy(tmp_path)

    success, errors = undo_batch(batch_id, store)

    assert success == 1
    assert not errors
    assert source.read_text() == "ORIGINAL"
    assert not destination.exists()
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDONE

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDONE

    store.close()


def test_copy_undo_refuses_regular_file_replacement(tmp_path: Path) -> None:
    store, batch_id, source, destination = _execute_copy(tmp_path)

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("REPLACEMENT")
    os.replace(replacement, destination)

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "ORIGINAL"
    assert destination.read_text() == "REPLACEMENT"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_copy_undo_refuses_in_place_modification(tmp_path: Path) -> None:
    store, batch_id, source, destination = _execute_copy(tmp_path)

    destination.write_text("MODIFIED-CONTENT")

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "ORIGINAL"
    assert destination.read_text() == "MODIFIED-CONTENT"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_copy_undo_refuses_legacy_operation_without_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    copied = tmp_path / "copy.txt"
    copied.write_text("COPY")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="copy",
        original_path=source,
        stored_path=copied,
        size=copied.stat().st_size,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    store.finish_batch_execution(
        batch_id,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "identité de la copie absente" in errors[0]
    assert source.read_text() == "SOURCE"
    assert copied.read_text() == "COPY"

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDO_FAILED
    assert operation.error == (
        "identité de la copie absente ; undo refusé par sécurité"
    )

    store.close()
