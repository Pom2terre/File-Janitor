"""Tests 2G-F : TRASH transactionnel, no-clobber et cross-filesystem."""

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


def _trash_item(source: Path) -> ActionItem:
    """Construit une action TRASH avec identité optionnelle historique."""

    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test TRASH transactionnel",
        action=ActionKind.TRASH,
    )


def _configure_trash(
    tmp_path: Path,
    monkeypatch,
) -> Path:
    """Isole la corbeille dans le tmp_path du test."""

    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(
        executor_module,
        "TRASH_DIR",
        trash_dir,
    )
    return trash_dir


def test_trash_uses_transactional_move_engine(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """TRASH doit passer par le moteur MOVE sécurisé, jamais shutil.move."""

    source = tmp_path / "source.txt"
    source.write_text("data")
    trash_dir = _configure_trash(tmp_path, monkeypatch)

    observed: list[tuple[Path, Path]] = []
    real_engine = executor_module._move_with_cross_filesystem_fallback

    def inspecting_engine(item, destination):
        observed.append((item.path, destination))
        return real_engine(item, destination)

    def forbidden_shutil_move(*args, **kwargs):
        raise AssertionError("shutil.move ne doit plus exécuter TRASH")

    monkeypatch.setattr(
        executor_module,
        "_move_with_cross_filesystem_fallback",
        inspecting_engine,
    )
    monkeypatch.setattr(
        executor_module.shutil,
        "move",
        forbidden_shutil_move,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert len(observed) == 1
    assert observed[0][0] == source
    assert observed[0][1].parent == trash_dir
    assert not source.exists()
    assert observed[0][1].read_text() == "data"

    operation = store.get_operations(batch_id)[0]
    assert operation.kind == "delete"
    assert operation.stored_path == observed[0][1]
    assert operation.status is OperationStatus.COMPLETED
    store.close()


def test_trash_same_filesystem_collision_is_no_clobber(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Une destination de corbeille apparue concurremment reste intacte."""

    source = tmp_path / "source.txt"
    source.write_text("source")
    trash_dir = _configure_trash(tmp_path, monkeypatch)
    trash_dir.mkdir()
    destination = trash_dir / "fixed_source.txt"

    monkeypatch.setattr(
        executor_module,
        "_unique_trash_path",
        lambda _original: destination,
    )

    def collision(_source: Path, target: Path) -> None:
        target.write_text("concurrent")
        raise FileExistsError(
            errno.EEXIST,
            "destination concurrente",
            str(target),
        )

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        collision,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "source"
    assert destination.read_text() == "concurrent"

    operation = store.get_operations(batch_id)[0]
    assert operation.kind == "delete"
    assert operation.stored_path == destination
    assert operation.status is OperationStatus.FAILED

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED
    store.close()


def test_trash_exdev_uses_transactional_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """EXDEV doit conserver la sémantique delete tout en utilisant le fallback."""

    source = tmp_path / "source.txt"
    source.write_text("cross-filesystem")
    trash_dir = _configure_trash(tmp_path, monkeypatch)
    trash_dir.mkdir()
    destination = trash_dir / "fixed_source.txt"

    monkeypatch.setattr(
        executor_module,
        "_unique_trash_path",
        lambda _original: destination,
    )

    def exdev(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        exdev,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.read_text() == "cross-filesystem"

    operation = store.get_operations(batch_id)[0]
    assert operation.kind == "delete"
    assert operation.stored_path == destination
    assert operation.status is OperationStatus.COMPLETED
    store.close()


def test_trash_exdev_collision_preserves_source_and_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Une collision à la publication du fallback ne doit rien écraser."""

    source = tmp_path / "source.txt"
    source.write_text("source")
    trash_dir = _configure_trash(tmp_path, monkeypatch)
    trash_dir.mkdir()
    destination = trash_dir / "fixed_source.txt"

    monkeypatch.setattr(
        executor_module,
        "_unique_trash_path",
        lambda _original: destination,
    )

    def exdev(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    real_link = executor_module.os.link

    def racing_link(temp_path, target):
        target = Path(target)
        target.write_text("concurrent")
        return real_link(temp_path, target)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        exdev,
    )
    monkeypatch.setattr(
        executor_module.os,
        "link",
        racing_link,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "source"
    assert destination.read_text() == "concurrent"
    assert not list(trash_dir.glob(".file-janitor-copy-*.tmp"))

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    store.close()


def test_trash_exdev_source_unlink_failure_rolls_back_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Si la source ne peut pas être supprimée, la corbeille publiée est retirée."""

    source = tmp_path / "source.txt"
    source.write_text("source")
    trash_dir = _configure_trash(tmp_path, monkeypatch)
    trash_dir.mkdir()
    destination = trash_dir / "fixed_source.txt"

    monkeypatch.setattr(
        executor_module,
        "_unique_trash_path",
        lambda _original: destination,
    )

    def exdev(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    real_unlink = Path.unlink

    def failing_source_unlink(self, *args, **kwargs):
        if self == source:
            raise OSError("source unlink impossible")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        exdev,
    )
    monkeypatch.setattr(
        Path,
        "unlink",
        failing_source_unlink,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "source"
    assert not destination.exists()
    assert not list(trash_dir.glob(".file-janitor-copy-*.tmp"))

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    store.close()


def test_trash_undo_restores_original_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Un TRASH transactionnel COMPLETED doit rester annulable."""

    source = tmp_path / "source.txt"
    source.write_text("recover me")
    _configure_trash(tmp_path, monkeypatch)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_trash_item(source)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    operation = store.get_operations(batch_id)[0]
    assert operation.kind == "delete"
    assert operation.stored_path is not None
    assert operation.stored_path.exists()
    assert not source.exists()

    undo_success, undo_errors = undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.read_text() == "recover me"
    assert not operation.stored_path.exists()

    operation_after = store.get_operations(batch_id)[0]
    assert operation_after.status is OperationStatus.UNDONE
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.UNDONE
    store.close()
