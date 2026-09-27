"""Tests 2D : protection contre les collisions et les écrasements."""

from __future__ import annotations

from pathlib import Path

from file_janitor.executor import execute_items, undo_batch
from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _item(
    source: Path,
    destination: Path,
    *,
    action: ActionKind,
    policy: ConflictPolicy,
) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test collision",
        destination=destination,
        action=action,
        conflict_policy=policy,
    )


def test_move_skip_refuses_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("EXISTING")

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _item(
        source,
        destination,
        action=ActionKind.MOVE,
        policy=ConflictPolicy.SKIP,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "SOURCE"
    assert destination.read_text() == "EXISTING"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].stored_path == destination
    assert operations[0].error is not None

    store.close()


def test_copy_rename_uses_free_path_and_history_tracks_it(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_text("NEW")
    destination = tmp_path / "Photos" / "photo.jpg"
    destination.parent.mkdir()
    destination.write_text("OLD")
    second = destination.parent / "photo (1).jpg"
    second.write_text("OLD-2")

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _item(
        source,
        destination,
        action=ActionKind.COPY,
        policy=ConflictPolicy.RENAME,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    effective = destination.parent / "photo (2).jpg"

    assert success == 1
    assert not errors
    assert source.read_text() == "NEW"
    assert destination.read_text() == "OLD"
    assert second.read_text() == "OLD-2"
    assert effective.read_text() == "NEW"

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].stored_path == effective

    undo_success, undo_errors = undo_batch(batch_id, store)
    assert undo_success == 1
    assert not undo_errors
    assert source.read_text() == "NEW"
    assert destination.read_text() == "OLD"
    assert second.read_text() == "OLD-2"
    assert not effective.exists()

    store.close()


def test_replace_is_refused_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "document.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Documents" / "document.txt"
    destination.parent.mkdir()
    destination.write_text("IMPORTANT")

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _item(
        source,
        destination,
        action=ActionKind.MOVE,
        policy=ConflictPolicy.REPLACE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "REPLACE" in errors[0]
    assert source.read_text() == "SOURCE"
    assert destination.read_text() == "IMPORTANT"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED

    store.close()


def test_undo_move_refuses_recreated_original_path(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("ORIGINAL")
    destination = tmp_path / "Sorted" / "report.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _item(
        source,
        destination,
        action=ActionKind.MOVE,
        policy=ConflictPolicy.RENAME,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors
    assert destination.read_text() == "ORIGINAL"
    assert not source.exists()

    # Un autre processus/utilisateur recrée le chemin d'origine.
    source.write_text("NEW FILE")

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 0
    assert len(undo_errors) == 1
    assert "éviter un écrasement" in undo_errors[0]
    assert source.read_text() == "NEW FILE"
    assert destination.read_text() == "ORIGINAL"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_FAILED

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_collision_does_not_block_safe_operation_in_same_batch(
    tmp_path: Path,
) -> None:
    blocked_source = tmp_path / "blocked.txt"
    blocked_source.write_text("BLOCKED-SOURCE")
    blocked_destination = tmp_path / "Sorted" / "blocked.txt"
    blocked_destination.parent.mkdir()
    blocked_destination.write_text("DO-NOT-OVERWRITE")

    safe_source = tmp_path / "safe.txt"
    safe_source.write_text("SAFE")
    safe_destination = tmp_path / "Sorted" / "safe.txt"

    blocked = _item(
        blocked_source,
        blocked_destination,
        action=ActionKind.MOVE,
        policy=ConflictPolicy.SKIP,
    )
    safe = _item(
        safe_source,
        safe_destination,
        action=ActionKind.MOVE,
        policy=ConflictPolicy.RENAME,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [blocked, safe],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert len(errors) == 1
    assert blocked_source.read_text() == "BLOCKED-SOURCE"
    assert blocked_destination.read_text() == "DO-NOT-OVERWRITE"
    assert not safe_source.exists()
    assert safe_destination.read_text() == "SAFE"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL

    operations = store.get_operations(batch_id)
    assert len(operations) == 2
    assert operations[0].status is OperationStatus.FAILED
    assert operations[1].status is OperationStatus.COMPLETED

    store.close()