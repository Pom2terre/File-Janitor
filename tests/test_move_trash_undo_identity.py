"""4E-11B1 : identité persistante et undo protégé pour MOVE/TRASH."""

from __future__ import annotations

import os
from pathlib import Path

import file_janitor.executor as executor_module
from file_janitor.executor import execute_items, undo_batch
from file_janitor.models import ActionItem, ActionKind, FileCategory
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _move_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test identité undo MOVE",
        destination=destination,
        action=ActionKind.MOVE,
    )


def _trash_item(source: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test identité undo TRASH",
        action=ActionKind.TRASH,
    )


def test_move_persists_stored_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("MOVE")
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors

    operation = store.get_operations(batch_id)[0]
    stat_result = destination.stat()

    assert operation.kind == "move"
    assert operation.status is OperationStatus.COMPLETED
    assert operation.stored_identity is not None
    assert operation.stored_identity.device == stat_result.st_dev
    assert operation.stored_identity.inode == stat_result.st_ino
    assert operation.stored_identity.size == stat_result.st_size
    assert operation.stored_identity.mtime_ns == stat_result.st_mtime_ns

    store.close()


def test_trash_persists_stored_identity(tmp_path: Path, monkeypatch) -> None:
    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    source = tmp_path / "source.txt"
    source.write_text("TRASH")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors

    operation = store.get_operations(batch_id)[0]
    assert operation.stored_path is not None
    stat_result = operation.stored_path.stat()

    assert operation.kind == "delete"
    assert operation.status is OperationStatus.COMPLETED
    assert operation.stored_identity is not None
    assert operation.stored_identity.device == stat_result.st_dev
    assert operation.stored_identity.inode == stat_result.st_ino
    assert operation.stored_identity.size == stat_result.st_size
    assert operation.stored_identity.mtime_ns == stat_result.st_mtime_ns

    store.close()


def test_move_undo_refuses_regular_file_replacement(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ORIGINAL-MOVE")
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors
    assert not source.exists()

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("REPLACEMENT-MOVE-CONTENT")
    os.replace(replacement, destination)

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "identité" in errors[0] or "modifiée" in errors[0]
    assert not source.exists()
    assert destination.read_text() == "REPLACEMENT-MOVE-CONTENT"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_trash_undo_refuses_regular_file_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    source = tmp_path / "source.txt"
    source.write_text("ORIGINAL-TRASH")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors

    operation = store.get_operations(batch_id)[0]
    assert operation.stored_path is not None

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("REPLACEMENT-TRASH-CONTENT")
    os.replace(replacement, operation.stored_path)

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert "identité" in errors[0] or "modifiée" in errors[0]
    assert not source.exists()
    assert operation.stored_path.read_text() == "REPLACEMENT-TRASH-CONTENT"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_move_undo_refuses_in_place_modification(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ORIGINAL")
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors

    destination.write_text("MODIFIED-CONTENT-WITH-DIFFERENT-SIZE")

    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert not source.exists()
    assert destination.read_text() == "MODIFIED-CONTENT-WITH-DIFFERENT-SIZE"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_legacy_move_and_trash_without_identity_are_not_restored(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    for kind in ("move", "delete"):
        original = tmp_path / f"{kind}-original.txt"
        stored = tmp_path / f"{kind}-stored.txt"
        stored.write_text(f"LEGACY-{kind}")

        batch_id = store.start_batch(str(tmp_path), planned_count=1)
        store.record_operation(
            batch_id,
            kind=kind,
            original_path=original,
            stored_path=stored,
            size=stored.stat().st_size,
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
        assert "identité du fichier stocké absente" in errors[0]
        assert not original.exists()
        assert stored.read_text() == f"LEGACY-{kind}"

        operation = store.get_operations(batch_id)[0]
        assert operation.status is OperationStatus.UNDO_FAILED
        assert operation.error == (
            "identité du fichier stocké absente ; undo refusé par sécurité"
        )

    store.close()
