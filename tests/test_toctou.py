"""Tests TOCTOU : scan -> exécution et fenêtre validation -> I/O."""

from __future__ import annotations

import os
from pathlib import Path

import file_janitor.executor as executor_module
from file_janitor.executor import execute_items
from file_janitor.models import (
    ActionItem,
    ActionKind,
    FileCategory,
    FileIdentity,
)
from file_janitor.path_safety import (
    PathSafetyError,
    current_file_identity,
    open_validated_source,
    validate_source_identity,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _identity(path: Path) -> FileIdentity:
    stat_result = path.stat()
    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def test_validate_source_identity_accepts_unchanged_file(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")

    expected = _identity(source)

    current = validate_source_identity(source, expected)

    assert current == expected


def test_validate_source_identity_none_is_backward_compatible(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")

    assert validate_source_identity(source, None) is None


def test_validate_source_identity_rejects_replaced_file(tmp_path: Path) -> None:
    source = tmp_path / "important.txt"
    source.write_text("original")
    expected = _identity(source)

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("nouveau fichier")
    os.replace(replacement, source)

    try:
        validate_source_identity(source, expected)
    except PathSafetyError as exc:
        assert "identité de la source modifiée" in str(exc)
    else:
        raise AssertionError("le remplacement du fichier aurait dû être refusé")


def test_validate_source_identity_rejects_modified_same_inode(tmp_path: Path) -> None:
    source = tmp_path / "important.txt"
    source.write_text("original")
    expected = _identity(source)

    source.write_text("contenu modifié et plus long")

    try:
        validate_source_identity(source, expected)
    except PathSafetyError as exc:
        assert "source modifiée depuis le scan" in str(exc)
    else:
        raise AssertionError("la modification du fichier aurait dû être refusée")


def test_execute_move_refuses_file_replaced_after_plan(tmp_path: Path) -> None:
    source = tmp_path / "important.txt"
    source.write_text("ORIGINAL")
    expected = _identity(source)

    destination = tmp_path / "Sorted" / "important.txt"

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=expected.size,
        reason="test TOCTOU",
        identity=expected,
        destination=destination,
        action=ActionKind.MOVE,
    )

    replacement = tmp_path / "replacement.txt"
    replacement.write_text("NEW FILE")
    os.replace(replacement, source)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "identité de la source modifiée" in errors[0]
    assert source.exists()
    assert source.read_text() == "NEW FILE"
    assert not destination.exists()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED
    assert batch.planned_count == 1
    assert batch.success_count == 0
    assert batch.failed_count == 1
    assert batch.skipped_count == 0

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    operation = operations[0]
    assert operation.original_path == source
    assert operation.stored_path == destination
    assert operation.status is OperationStatus.FAILED
    assert operation.error is not None
    assert "identité de la source modifiée" in operation.error

    store.close()


def test_execute_copy_refuses_file_modified_after_plan(tmp_path: Path) -> None:
    source = tmp_path / "report.txt"
    source.write_text("version 1")
    expected = _identity(source)

    destination = tmp_path / "Copies" / "report.txt"

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=expected.size,
        reason="test TOCTOU",
        identity=expected,
        destination=destination,
        action=ActionKind.COPY,
    )

    source.write_text("version 2 beaucoup plus longue")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "source modifiée depuis le scan" in errors[0]
    assert source.exists()
    assert not destination.exists()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_current_file_identity_matches_stat(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc")

    identity = current_file_identity(source)
    stat_result = source.stat()

    assert identity.device == stat_result.st_dev
    assert identity.inode == stat_result.st_ino
    assert identity.size == stat_result.st_size
    assert identity.mtime_ns == stat_result.st_mtime_ns


def test_open_validated_source_checks_open_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")
    expected = _identity(source)

    fd, current = open_validated_source(source, expected)
    try:
        assert current == expected
        assert os.read(fd, 8) == b"original"
    finally:
        os.close(fd)


def test_move_revalidates_after_destination_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "important.txt"
    source.write_text("ORIGINAL")
    expected = _identity(source)
    destination = tmp_path / "Sorted" / "important.txt"

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=expected.size,
        reason="course MOVE",
        identity=expected,
        destination=destination,
        action=ActionKind.MOVE,
    )

    original_resolve = executor_module._resolve_destination

    def replace_during_resolution(action_item: ActionItem) -> Path:
        resolved = original_resolve(action_item)
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("NEW FILE")
        os.replace(replacement, source)
        return resolved

    monkeypatch.setattr(
        executor_module,
        "_resolve_destination",
        replace_during_resolution,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "identité de la source modifiée" in errors[0]
    assert source.read_text() == "NEW FILE"
    assert not destination.exists()

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED

    store.close()


def test_trash_revalidates_after_trash_path_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "important.txt"
    source.write_text("ORIGINAL")
    expected = _identity(source)

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=source,
        size=expected.size,
        reason="course TRASH",
        identity=expected,
        action=ActionKind.TRASH,
    )

    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    original_unique = executor_module._unique_trash_path

    def replace_during_trash_selection(path: Path) -> Path:
        selected = original_unique(path)
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("NEW FILE")
        os.replace(replacement, source)
        return selected

    monkeypatch.setattr(
        executor_module,
        "_unique_trash_path",
        replace_during_trash_selection,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "identité de la source modifiée" in errors[0]
    assert source.read_text() == "NEW FILE"
    assert not any(trash_dir.iterdir())

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED

    store.close()


def test_copy_rejects_replacement_after_destination_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "report.txt"
    source.write_text("ORIGINAL")
    expected = _identity(source)
    destination = tmp_path / "Copies" / "report.txt"

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=expected.size,
        reason="course COPY",
        identity=expected,
        destination=destination,
        action=ActionKind.COPY,
    )

    original_resolve = executor_module._resolve_destination

    def replace_during_resolution(action_item: ActionItem) -> Path:
        resolved = original_resolve(action_item)
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("NEW FILE")
        os.replace(replacement, source)
        return resolved

    monkeypatch.setattr(
        executor_module,
        "_resolve_destination",
        replace_during_resolution,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "identité de la source modifiée" in errors[0]
    assert source.read_text() == "NEW FILE"
    assert not destination.exists()

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED

    store.close()
