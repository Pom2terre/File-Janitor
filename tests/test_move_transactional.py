"""Tests 2G-D/2G-E : MOVE atomique et fallback cross-filesystem."""

from __future__ import annotations

import errno
from pathlib import Path

import file_janitor.executor as executor_module
from file_janitor.executor import execute_items
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


def _move_item(
    source: Path,
    destination: Path,
    *,
    policy: ConflictPolicy = ConflictPolicy.RENAME,
) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test MOVE 2G-D",
        destination=destination,
        action=ActionKind.MOVE,
        conflict_policy=policy,
    )


def test_move_uses_rename_noreplace_and_not_shutil_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    real_rename = executor_module._rename_noreplace
    calls: list[tuple[Path, Path]] = []

    def observing_rename(source_path: Path, destination_path: Path) -> None:
        calls.append((source_path, destination_path))
        real_rename(source_path, destination_path)

    def forbidden_shutil_move(*args, **kwargs):
        raise AssertionError("shutil.move ne doit plus exécuter MOVE")

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        observing_rename,
    )
    monkeypatch.setattr(
        executor_module.shutil,
        "move",
        forbidden_shutil_move,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert calls == [(source, destination)]
    assert not source.exists()
    assert destination.read_text() == "SOURCE"

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].stored_path == destination

    store.close()


def test_concurrent_move_destination_is_never_overwritten(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    real_rename = executor_module._rename_noreplace

    def racing_rename(source_path: Path, destination_path: Path) -> None:
        destination_path.write_text("CONCURRENT")
        real_rename(source_path, destination_path)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        racing_rename,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "SOURCE"
    assert destination.read_text() == "CONCURRENT"

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error is not None

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_cross_filesystem_move_falls_back_transactionally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def exdev_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(
            errno.EXDEV,
            "Invalid cross-device link",
            str(source_path),
            str(destination_path),
        )

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        exdev_rename,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.read_text() == "SOURCE"

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].stored_path == destination

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    store.close()


def test_move_fsyncs_destination_then_source_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_dir = tmp_path / "incoming"
    source_dir.mkdir()
    source = source_dir / "source.txt"
    source.write_text("SOURCE")

    destination_dir = tmp_path / "Sorted"
    destination = destination_dir / "source.txt"

    real_fsync_directory = executor_module._fsync_directory
    events: list[Path] = []

    def observing_fsync_directory(directory: Path) -> None:
        events.append(Path(directory))
        real_fsync_directory(directory)

    monkeypatch.setattr(
        executor_module,
        "_fsync_directory",
        observing_fsync_directory,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert events == [destination_dir, source_dir]
    assert destination.read_text() == "SOURCE"
    assert not source.exists()

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.COMPLETED

    store.close()


def test_move_directory_fsync_failure_after_rename_stays_completed(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    source_dir = tmp_path / "incoming"
    source_dir.mkdir()
    source = source_dir / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def failing_fsync_directory(directory: Path) -> None:
        raise OSError("fsync MOVE impossible")

    monkeypatch.setattr(
        executor_module,
        "_fsync_directory",
        failing_fsync_directory,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.read_text() == "SOURCE"
    assert "fsync MOVE impossible" in caplog.text

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    store.close()

def test_cross_filesystem_copy_failure_keeps_source_and_no_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def exdev_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(errno.EXDEV, "EXDEV")

    def failing_copy2(src: str, dst: str):
        Path(dst).write_text("PARTIAL")
        raise OSError("copie fallback impossible")

    monkeypatch.setattr(executor_module, "_rename_noreplace", exdev_rename)
    monkeypatch.setattr(executor_module.shutil, "copy2", failing_copy2)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "copie fallback impossible" in errors[0]
    assert source.read_text() == "SOURCE"
    assert not destination.exists()
    assert list(destination.parent.glob(f"{executor_module._COPY_TEMP_PREFIX}*.tmp")) == []

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.FAILED
    store.close()


def test_cross_filesystem_concurrent_destination_is_never_overwritten(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def exdev_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(errno.EXDEV, "EXDEV")

    real_link = executor_module.os.link

    def racing_link(src, dst):
        Path(dst).write_text("CONCURRENT")
        return real_link(src, dst)

    monkeypatch.setattr(executor_module, "_rename_noreplace", exdev_rename)
    monkeypatch.setattr(executor_module.os, "link", racing_link)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "SOURCE"
    assert destination.read_text() == "CONCURRENT"

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.FAILED
    store.close()


def test_cross_filesystem_source_unlink_failure_rolls_back_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def exdev_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(errno.EXDEV, "EXDEV")

    real_unlink = Path.unlink

    def failing_source_unlink(self: Path, *args, **kwargs):
        if self == source:
            raise OSError("suppression source impossible")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(executor_module, "_rename_noreplace", exdev_rename)
    monkeypatch.setattr(Path, "unlink", failing_source_unlink)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "suppression source impossible" in errors[0]
    assert source.read_text() == "SOURCE"
    assert not destination.exists()

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.FAILED
    store.close()


def test_cross_filesystem_success_is_undoable_with_existing_move_semantics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def exdev_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(errno.EXDEV, "EXDEV")

    monkeypatch.setattr(executor_module, "_rename_noreplace", exdev_rename)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.exists()

    undo_success, undo_errors = executor_module.undo_batch(batch_id, store)

    assert undo_success == 1
    assert not undo_errors
    assert source.read_text() == "SOURCE"
    assert not destination.exists()

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.UNDONE
    store.close()


def test_fuse_renameat2_einval_falls_back_transactionally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """FUSE/rclone peut refuser RENAME_NOREPLACE avec EINVAL."""

    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

    def unsupported_rename(source_path: Path, destination_path: Path) -> None:
        raise OSError(
            errno.EINVAL,
            "Invalid argument",
            str(source_path),
            str(destination_path),
        )

    monkeypatch.setattr(executor_module, "_rename_noreplace", unsupported_rename)

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.read_text() == "SOURCE"
    assert store.get_operations(batch_id)[0].status is OperationStatus.COMPLETED
    store.close()


def test_fuse_move_without_hardlinks_uses_exclusive_no_clobber_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Le fallback reste utilisable quand link(2) n'est pas supporté."""

    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"

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

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert destination.read_text() == "SOURCE"
    assert store.get_operations(batch_id)[0].status is OperationStatus.COMPLETED
    store.close()

def test_fuse_copy_without_hardlinks_uses_exclusive_no_clobber_publish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "archive.zip"
    source.write_bytes(b"verified zip contents")
    destination = tmp_path / "remote" / "archive.zip"

    monkeypatch.setattr(
        executor_module.os,
        "link",
        lambda *_: (_ for _ in ()).throw(
            OSError(errno.EPERM, "hardlink unsupported")
        ),
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, success, errors = execute_items(
        [
            ActionItem(
                category=FileCategory.ARCHIVE,
                path=source,
                size=source.stat().st_size,
                reason="Publier le ZIP vérifié",
                destination=destination,
                action=ActionKind.COPY,
                conflict_policy=ConflictPolicy.SKIP,
            )
        ],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert source.read_bytes() == b"verified zip contents"
    assert destination.read_bytes() == b"verified zip contents"
    assert store.get_operations(batch_id)[0].status is OperationStatus.COMPLETED
    assert not list(destination.parent.glob(".file-janitor-copy-*.tmp"))
    store.close()


def test_fuse_exclusive_publish_never_overwrites_concurrent_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Sorted" / "source.txt"
    destination.parent.mkdir(parents=True)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EINVAL, "EINVAL")),
    )

    real_open = executor_module.os.open
    injected = {"done": False}

    def racing_open(path, flags, *args, **kwargs):
        if (
            Path(path) == destination
            and flags & executor_module.os.O_EXCL
            and not injected["done"]
        ):
            destination.write_text("CONCURRENT")
            injected["done"] = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(executor_module.os, "open", racing_open)

    store = HistoryStore(db_path=tmp_path / "history.db")
    _batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert source.read_text() == "SOURCE"
    assert destination.read_text() == "CONCURRENT"
    store.close()

def test_unsupported_rename_uses_single_direct_copy_without_temp_or_hardlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload")
    destination = tmp_path / "sorted" / "source.txt"
    store = HistoryStore(db_path=tmp_path / "history.db")

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        lambda *_: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )
    monkeypatch.setattr(
        executor_module.os,
        "link",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("hardlink path must not be used for unsupported rename")
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

    batch_id, success, errors = execute_items(
        [_move_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert batch_id > 0
    assert success == 1
    assert not errors
    assert calls["copyfileobj"] == 1
    assert not source.exists()
    assert destination.read_text() == "payload"
    assert not list(destination.parent.glob(".file-janitor-copy-*.tmp"))
    store.close()
