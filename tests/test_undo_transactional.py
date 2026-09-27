"""Tests spécifiques au cycle transactionnel de l'undo (étapes 2C / 2G-G)."""

from __future__ import annotations

import errno
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
        reason="test undo transactionnel",
        destination=destination,
        action=ActionKind.MOVE,
    )


def test_successful_undo_marks_operation_and_batch_undone(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not destination.exists()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDONE
    assert batch.undone is True

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDONE
    assert operation.error is None

    store.close()


def test_missing_stored_file_marks_undo_failed(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination = tmp_path / "sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    destination.unlink()

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 0
    assert len(undo_errors) == 1
    assert "fichier de sauvegarde introuvable" in undo_errors[0]

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_FAILED
    assert batch.undone is False

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDO_FAILED
    assert operation.error == "fichier de sauvegarde introuvable"

    store.close()


def test_partial_undo_marks_batch_undo_partial(tmp_path: Path) -> None:
    first_source = tmp_path / "first.txt"
    first_source.write_text("first")
    second_source = tmp_path / "second.txt"
    second_source.write_text("second")

    first_destination = tmp_path / "sorted" / "first.txt"
    second_destination = tmp_path / "sorted" / "second.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [
            _move_item(first_source, first_destination),
            _move_item(second_source, second_destination),
        ],
        root=str(tmp_path),
        store=store,
    )

    assert success == 2
    assert not errors

    # Le second fichier est rendu impossible à restaurer.
    second_destination.unlink()

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert len(undo_errors) == 1
    assert first_source.exists()
    assert not first_destination.exists()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_PARTIAL
    assert batch.undone is False

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.UNDONE
    assert operations[1].status is OperationStatus.UNDO_FAILED

    store.close()


def test_undo_with_no_completed_operation_is_failed(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path))
    store.set_batch_status(batch_id, BatchStatus.FAILED)

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 0
    assert len(undo_errors) == 1
    assert "aucune opération terminée à annuler" in undo_errors[0]

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_FAILED
    assert batch.undone is False

    store.close()

def test_undo_restore_uses_no_clobber_engine(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items([_move_item(source, destination)], root=str(tmp_path), store=store)

    observed = []
    real_rename = executor_module._rename_noreplace

    def inspecting_rename(current: Path, target: Path) -> None:
        observed.append((current, target))
        real_rename(current, target)

    monkeypatch.setattr(executor_module, "_rename_noreplace", inspecting_rename)
    monkeypatch.setattr(
        executor_module.shutil, "move",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("shutil.move interdit pour undo")),
    )

    success, errors = undo_batch(batch_id, store)
    assert success == 1
    assert not errors
    assert observed == [(destination, source)]
    assert source.read_text() == "data"
    assert not destination.exists()
    store.close()


def test_undo_concurrent_original_is_never_overwritten(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("saved")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items([_move_item(source, destination)], root=str(tmp_path), store=store)

    def collision(_stored: Path, original: Path) -> None:
        original.write_text("concurrent")
        raise FileExistsError(errno.EEXIST, "destination concurrente", str(original))

    monkeypatch.setattr(executor_module, "_rename_noreplace", collision)
    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "concurrent"
    assert destination.read_text() == "saved"
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED
    store.close()


def test_undo_exdev_uses_transactional_restore(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("cross-filesystem")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items([_move_item(source, destination)], root=str(tmp_path), store=store)

    monkeypatch.setattr(
        executor_module, "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device link")),
    )
    success, errors = undo_batch(batch_id, store)

    assert success == 1
    assert not errors
    assert source.read_text() == "cross-filesystem"
    assert not destination.exists()
    assert not list(tmp_path.glob(".file-janitor-copy-*.tmp"))
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDONE
    store.close()


def test_undo_exdev_publication_collision_preserves_both_files(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("saved")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items([_move_item(source, destination)], root=str(tmp_path), store=store)

    monkeypatch.setattr(
        executor_module, "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device link")),
    )
    real_link = executor_module.os.link

    def racing_link(temp_path, original):
        Path(original).write_text("concurrent")
        return real_link(temp_path, original)

    monkeypatch.setattr(executor_module.os, "link", racing_link)
    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "concurrent"
    assert destination.read_text() == "saved"
    assert not list(tmp_path.glob(".file-janitor-copy-*.tmp"))
    store.close()


def test_undo_exdev_stored_unlink_failure_rolls_back_original(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("saved")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, _, _ = execute_items([_move_item(source, destination)], root=str(tmp_path), store=store)

    monkeypatch.setattr(
        executor_module, "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device link")),
    )
    real_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self == destination:
            raise OSError("stored unlink impossible")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    success, errors = undo_batch(batch_id, store)

    assert success == 0
    assert len(errors) == 1
    assert not source.exists()
    assert destination.read_text() == "saved"
    assert not list(tmp_path.glob(".file-janitor-copy-*.tmp"))
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDO_FAILED
    store.close()


def test_successful_undo_removes_empty_destination_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination_dir = tmp_path / "sorted"
    destination = destination_dir / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [_move_item(source, destination)], root=str(tmp_path), store=store
    )
    assert success == 1
    assert not errors
    assert destination_dir.is_dir()

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not destination_dir.exists()
    store.close()


def test_successful_undo_keeps_destination_directory_when_still_occupied(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination_dir = tmp_path / "sorted"
    destination = destination_dir / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, _, _ = execute_items(
        [_move_item(source, destination)], root=str(tmp_path), store=store
    )
    unrelated = destination_dir / "keep.txt"
    unrelated.write_text("keep")

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert destination_dir.is_dir()
    assert unrelated.read_text() == "keep"
    store.close()


def test_successful_undo_prunes_nested_empty_destinations_deepest_first(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("data")
    destination = tmp_path / "sorted" / "2026" / "09" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, _, _ = execute_items(
        [_move_item(source, destination)], root=str(tmp_path), store=store
    )

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not (tmp_path / "sorted").exists()
    assert tmp_path.exists()
    store.close()


def test_partial_undo_does_not_prune_destination_directories(tmp_path: Path) -> None:
    first_source = tmp_path / "first.txt"
    first_source.write_text("first")
    second_source = tmp_path / "second.txt"
    second_source.write_text("second")
    destination_dir = tmp_path / "sorted"
    first_destination = destination_dir / "first.txt"
    second_destination = destination_dir / "second.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [
            _move_item(first_source, first_destination),
            _move_item(second_source, second_destination),
        ],
        root=str(tmp_path),
        store=store,
    )
    assert success == 2
    assert not errors

    # Rend une restauration impossible. L'autre fichier peut être restauré,
    # mais aucun nettoyage de l'arborescence destination ne doit être lancé.
    second_destination.unlink()

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert len(undo_errors) == 1
    assert first_source.exists()
    assert destination_dir.is_dir()
    store.close()


def test_undo_cleanup_never_prunes_directory_outside_batch_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    executor_module._prune_empty_undo_destinations(
        {outside},
        root=root,
    )

    assert outside.is_dir()


def test_undo_cleanup_never_prunes_batch_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    executor_module._prune_empty_undo_destinations(
        {root},
        root=root,
    )

    assert root.is_dir()


def test_undo_cleanup_refuses_symlinked_destination_parent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    external_child = outside / "child"
    external_child.mkdir()

    symlinked_parent = root / "sorted"
    symlinked_parent.symlink_to(outside, target_is_directory=True)
    lexical_child = symlinked_parent / "child"

    executor_module._prune_empty_undo_destinations(
        {lexical_child},
        root=root,
    )

    assert external_child.is_dir()
    assert symlinked_parent.is_symlink()


def test_fuse_renameat2_and_hardlink_unsupported_move_remains_undoable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors
    assert destination.exists()

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EINVAL, "EINVAL")),
    )
    monkeypatch.setattr(
        executor_module.os,
        "link",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EPERM, "hardlink unsupported")),
    )

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.read_text() == "SOURCE"
    assert not destination.exists()
    assert store.get_operations(batch_id)[0].status is OperationStatus.UNDONE
    store.close()


def test_unsupported_rename_undo_uses_single_direct_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )
    assert success == 1
    assert not errors

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )
    monkeypatch.setattr(
        executor_module.os,
        "link",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("hardlink path must not be used for unsupported rename undo")
        ),
    )

    calls = {"copyfileobj": 0}
    real_copyfileobj = executor_module.shutil.copyfileobj

    def counting_copyfileobj(*args, **kwargs):
        calls["copyfileobj"] += 1
        return real_copyfileobj(*args, **kwargs)

    monkeypatch.setattr(
        executor_module.shutil,
        "copyfileobj",
        counting_copyfileobj,
    )

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert calls["copyfileobj"] == 1
    assert source.read_text() == "payload"
    assert not destination.exists()
    assert not list(source.parent.glob(".file-janitor-copy-*.tmp"))
    store.close()


def test_partial_undo_can_retry_only_failed_operation(tmp_path: Path) -> None:
    first_source = tmp_path / "first.txt"
    first_source.write_text("first")
    second_source = tmp_path / "second.txt"
    second_source.write_text("second")

    first_destination = tmp_path / "sorted_first" / "first.txt"
    second_destination = tmp_path / "sorted_second" / "second.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [
            _move_item(first_source, first_destination),
            _move_item(second_source, second_destination),
        ],
        root=str(tmp_path),
        store=store,
    )
    assert success == 2
    assert not errors

    # L'undo parcourt le batch à rebours : le second restore échoue parce que
    # son emplacement original a été recréé, tandis que le premier réussit.
    second_source.write_text("BLOCKER")
    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert len(undo_errors) == 1
    assert first_source.read_text() == "first"
    assert second_source.read_text() == "BLOCKER"
    assert second_destination.read_text() == "second"

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.UNDONE
    assert operations[1].status is OperationStatus.UNDO_FAILED
    assert store.get_batch(batch_id).status is BatchStatus.UNDO_PARTIAL

    # Après correction de la cause, seule l'opération encore en échec est
    # retentée. L'opération déjà UNDONE ne doit jamais être rejouée.
    second_source.unlink()
    retry_success, retry_errors = undo_batch(batch_id, store)

    assert retry_success == 1
    assert not retry_errors
    assert first_source.read_text() == "first"
    assert second_source.read_text() == "second"
    assert not first_destination.exists()
    assert not second_destination.exists()

    operations = store.get_operations(batch_id)
    assert [op.status for op in operations] == [
        OperationStatus.UNDONE,
        OperationStatus.UNDONE,
    ]
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDONE
    assert batch.undone is True

    # Le premier dossier était resté vide après l'undo partiel. Le retry final
    # doit nettoyer les destinations de tout le batch, pas seulement celle de
    # l'opération retentée.
    assert not first_destination.parent.exists()
    assert not second_destination.parent.exists()
    store.close()


def test_failed_retry_preserves_undo_partial_when_an_operation_is_already_undone(
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "first.txt"
    first_source.write_text("first")
    second_source = tmp_path / "second.txt"
    second_source.write_text("second")

    first_destination = tmp_path / "sorted" / "first.txt"
    second_destination = tmp_path / "sorted" / "second.txt"

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [
            _move_item(first_source, first_destination),
            _move_item(second_source, second_destination),
        ],
        root=str(tmp_path),
        store=store,
    )
    assert success == 2
    assert not errors

    second_source.write_text("BLOCKER")
    first_success, first_errors = undo_batch(batch_id, store)
    assert first_success == 1
    assert len(first_errors) == 1
    assert store.get_batch(batch_id).status is BatchStatus.UNDO_PARTIAL

    # La cause n'est pas corrigée. La seconde tentative ne réussit aucune
    # opération nouvelle, mais le batch reste PARTIEL puisqu'une restauration
    # de la tentative précédente est toujours acquise.
    retry_success, retry_errors = undo_batch(batch_id, store)
    assert retry_success == 0
    assert len(retry_errors) == 1

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDO_PARTIAL
    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.UNDONE
    assert operations[1].status is OperationStatus.UNDO_FAILED
    assert first_source.read_text() == "first"
    assert second_source.read_text() == "BLOCKER"
    assert second_destination.read_text() == "second"
    store.close()
