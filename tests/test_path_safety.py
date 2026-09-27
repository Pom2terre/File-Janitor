"""Tests 2E : validation des chemins et frontières de sécurité."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from file_janitor.executor import execute_items, undo_batch
from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
)
from file_janitor.path_safety import (
    PathSafetyError,
    validate_destination_path,
    validate_source_file,
)
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
        reason="test path safety",
        destination=destination,
        action=ActionKind.MOVE,
        conflict_policy=ConflictPolicy.RENAME,
    )


def test_validate_source_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("data")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(PathSafetyError, match="lien symbolique"):
        validate_source_file(link)


def test_move_rejects_same_source_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "document.txt"
    source.write_text("important")

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _move_item(source, source)

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "même chemin" in errors[0] or "même fichier" in errors[0]
    assert source.read_text() == "important"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_move_rejects_existing_directory_as_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination = tmp_path / "ExistingDirectory"
    destination.mkdir()

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _move_item(source, destination)

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "dossier" in errors[0]
    assert source.exists()
    assert destination.is_dir()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_destination_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source")

    real_destination = tmp_path / "real.txt"
    real_destination.write_text("real")

    link_destination = tmp_path / "destination.txt"
    link_destination.symlink_to(real_destination)

    with pytest.raises(PathSafetyError, match="lien symbolique"):
        validate_destination_path(source, link_destination)

    assert real_destination.read_text() == "real"
    assert source.read_text() == "source"

def test_destination_samefile_error_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une erreur samefile doit interrompre la validation de destination."""

    source = tmp_path / "source.txt"
    source.write_text("source")

    destination = tmp_path / "destination.txt"
    destination.write_text("destination")

    def failing_samefile(
        self: Path,
        other: Path,
    ) -> bool:
        raise OSError("samefile impossible")

    monkeypatch.setattr(
        Path,
        "samefile",
        failing_samefile,
    )

    with pytest.raises(
        PathSafetyError,
        match="impossible de vérifier",
    ):
        validate_destination_path(
            source,
            destination,
        )

    assert source.read_text() == "source"
    assert destination.read_text() == "destination"

def test_symlink_parent_of_destination_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source")

    real_dir = tmp_path / "real"
    real_dir.mkdir()

    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    destination = linked_dir / "source.txt"

    with pytest.raises(PathSafetyError, match="parent.*lien symbolique"):
        validate_destination_path(source, destination)


def test_executor_rejects_source_replaced_by_symlink(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    original.write_text("original")

    external = tmp_path / "external.txt"
    external.write_text("external")

    destination = tmp_path / "Sorted" / "original.txt"

    # Le plan aurait pu être construit ici. Avant execute, le fichier est
    # remplacé par un symlink.
    original.unlink()
    original.symlink_to(external)

    store = HistoryStore(db_path=tmp_path / "history.db")

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=original,
        size=len("original"),
        reason="source changée",
        destination=destination,
        action=ActionKind.MOVE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "lien symbolique" in errors[0]
    assert original.is_symlink()
    assert external.read_text() == "external"
    assert not destination.exists()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_undo_rejects_symlink_parent_on_original_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / "file.txt"
    source.write_text("data")

    destination = tmp_path / "sorted" / "file.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = _move_item(source, destination)

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert destination.exists()
    assert not source.exists()

    # Le dossier original est remplacé par un symlink vers un autre endroit.
    source_dir.rmdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    source_dir.symlink_to(other_dir, target_is_directory=True)

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 0
    assert len(undo_errors) == 1
    assert "lien symbolique" in undo_errors[0]
    assert destination.exists()
    assert not (other_dir / "file.txt").exists()

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.UNDO_FAILED

    store.close()


def test_copy_undo_refuses_copy_replaced_by_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source")
    destination = tmp_path / "Copies" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="copy",
        destination=destination,
        action=ActionKind.COPY,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors

    destination.unlink()
    external = tmp_path / "external.txt"
    external.write_text("external")
    destination.symlink_to(external)

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 0
    assert len(undo_errors) == 1
    assert "lien symbolique" in undo_errors[0]
    assert destination.is_symlink()
    assert external.read_text() == "external"

    store.close()