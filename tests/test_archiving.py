from __future__ import annotations

import errno
import os
from datetime import datetime
from pathlib import Path
import zipfile

import pytest

from file_janitor.application import (
    ArchiveFormat,
    analyze_archive_folder,
    archive_actions,
    execute_selected_actions,
    undo_execution,
)
from file_janitor.archiving import ArchivePlanError


def _set_mtime(path: Path, timestamp: datetime) -> None:
    value = timestamp.timestamp()
    os.utime(path, (value, value))


def test_archive_preview_preserves_relative_paths_and_blocks_collisions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    nested = source / "photos" / "2020"
    nested.mkdir(parents=True)
    old_file = nested / "old.jpg"
    old_file.write_bytes(b"old")
    recent_file = nested / "recent.jpg"
    recent_file.write_bytes(b"recent")
    now = datetime(2026, 1, 1)
    _set_mtime(old_file, datetime(2024, 1, 1))
    _set_mtime(recent_file, datetime(2025, 12, 1))

    destination = tmp_path / "archive"
    collision = destination / "photos" / "2020" / "old.jpg"
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"keep-existing")

    result = analyze_archive_folder(
        source, destination, 365, now=now
    )

    details = result.details_for("archive")
    assert details is not None
    assert len(details.items) == 1
    assert details.items[0].path == old_file
    assert details.items[0].destination == collision
    assert details.items[0].conflict is True
    assert details.items[0].action == "none"
    assert archive_actions(result) == []
    assert collision.read_bytes() == b"keep-existing"
    assert old_file.exists()


def test_archive_execution_can_be_undone(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "reports"
    nested.mkdir(parents=True)
    original = nested / "annual.pdf"
    original.write_bytes(b"report")
    _set_mtime(original, datetime(2020, 1, 1))
    destination = tmp_path / "archive"
    result = analyze_archive_folder(
        source, destination, 365, now=datetime(2026, 1, 1)
    )

    execution = execute_selected_actions(
        result,
        {("archive", 0)},
        db_path=tmp_path / "history.sqlite3",
    )
    archived = destination / "reports" / "annual.pdf"
    assert execution.success == 1
    assert archived.read_bytes() == b"report"
    assert not original.exists()

    undone = undo_execution(
        execution.batch_id,
        db_path=tmp_path / "history.sqlite3",
    )
    assert undone.success == 1
    assert original.read_bytes() == b"report"
    assert not archived.exists()

def test_archive_scan_uses_mounted_listing_when_rclone_listing_is_partial(
    monkeypatch, tmp_path: Path,
) -> None:
    from file_janitor.application import service
    from file_janitor.models import ScanResult

    source = tmp_path / "TensorFlow 2.0 Bootcamp"
    source.mkdir()
    archive_file = source / "cell_images.zip"
    archive_file.write_bytes(b"zip-content")
    _set_mtime(archive_file, datetime(2022, 6, 2))

    # Simule un listing rclone qui ne retourne pas ce fichier, alors qu'il est
    # visible dans le dossier monté. L'archivage doit utiliser ce dernier.
    monkeypatch.setattr(
        service,
        "scan_rclone_metadata",
        lambda folder, **kwargs: ScanResult(root=folder),
    )

    result = analyze_archive_folder(
        source,
        tmp_path / "archive",
        365,
        now=datetime(2026, 1, 1),
    )

    details = result.details_for("archive")
    assert details is not None
    assert [item.path for item in details.items] == [archive_file]
    assert len(archive_actions(result)) == 1

def test_zip_archive_preserves_tree_and_can_be_undone(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "lectures" / "week-1"
    nested.mkdir(parents=True)
    first = nested / "slides.pdf"
    first.write_bytes(b"slides")
    second = source / "notes.txt"
    second.write_bytes(b"notes")
    for file_path in (first, second):
        _set_mtime(file_path, datetime(2020, 1, 1))

    destination = tmp_path / "archives"
    result = analyze_archive_folder(
        source,
        destination,
        365,
        archive_format=ArchiveFormat.ZIP,
        now=datetime(2026, 1, 1),
    )
    details = result.details_for("archive")
    assert details is not None
    zip_paths = {item.destination for item in details.items}
    assert len(zip_paths) == 1
    zip_path = next(iter(zip_paths))
    assert zip_path is not None
    assert all(item.action == "move" for item in details.items)

    execution = execute_selected_actions(
        result,
        {("archive", index) for index in range(len(details.items))},
        db_path=tmp_path / "history.sqlite3",
    )
    assert execution.success == 3  # publication du ZIP + deux sources mises à la corbeille
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == ["lectures/week-1/slides.pdf", "notes.txt"]
        assert archive.read("lectures/week-1/slides.pdf") == b"slides"
        assert archive.testzip() is None
    assert not first.exists()
    assert not second.exists()

    undone = undo_execution(execution.batch_id, db_path=tmp_path / "history.sqlite3")
    assert undone.success == 3
    assert first.read_bytes() == b"slides"
    assert second.read_bytes() == b"notes"
    assert not zip_path.exists()


def test_zip_archive_retries_transient_remote_read_error(
    monkeypatch, tmp_path: Path,
) -> None:
    from file_janitor.application import service
    from file_janitor.application.remote import RemoteFolderInfo

    source = tmp_path / "remote-source"
    source.mkdir()
    original = source / "old.txt"
    original.write_bytes(b"remote content")
    _set_mtime(original, datetime(2020, 1, 1))
    result = analyze_archive_folder(
        source,
        tmp_path / "archives",
        365,
        archive_format=ArchiveFormat.ZIP,
        now=datetime(2026, 1, 1),
    )
    items = archive_actions(result)
    assert len(items) == 1

    real_open = Path.open
    failed_once = False
    rclone_used = False

    class FailFirstRead:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self.wrapped.__exit__(*exc_info)

        def close(self):
            return self.wrapped.close()

        def read(self, size: int = -1) -> bytes:
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise OSError(errno.EIO, "simulated remote read interruption")
            return self.wrapped.read(size)

    def patched_open(path: Path, mode: str = "r", *args, **kwargs):
        opened = real_open(path, mode, *args, **kwargs)
        if path == original and mode == "rb":
            return FailFirstRead(opened)
        return opened

    def fake_rclone_cat(
        path: Path,
        root: Path,
        remote_info,
        *,
        cancel_callback,
        chunk_size: int = 8 * 1024 * 1024,
    ):
        nonlocal rclone_used
        assert path == original
        assert root == source
        rclone_used = True
        yield b"remote content"

    monkeypatch.setattr(Path, "open", patched_open)
    monkeypatch.setattr(
        service,
        "remote_folder_info",
        lambda folder: RemoteFolderInfo(
            folder == source,
            "fuse.rclone" if folder == source else None,
            source if folder == source else None,
            "remote:" if folder == source else None,
        ),
    )
    monkeypatch.setattr(service, "_iter_rclone_cat_chunks", fake_rclone_cat)

    execution = service._execute_zip_archive(
        items,
        root=source,
        db_path=tmp_path / "history.sqlite3",
        progress_callback=None,
        cancel_callback=None,
    )

    zip_path = items[0].destination
    assert failed_once
    assert rclone_used
    assert execution.success == 2
    assert not execution.errors
    assert zip_path is not None and zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.read("old.txt") == b"remote content"
        assert archive.testzip() is None
    assert not original.exists()


def test_zip_archive_is_kept_until_all_sources_are_restored_on_undo(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "old.txt"
    original.write_text("original")
    _set_mtime(original, datetime(2020, 1, 1))
    history = tmp_path / "history.sqlite3"
    result = analyze_archive_folder(
        source,
        tmp_path / "archives",
        365,
        archive_format=ArchiveFormat.ZIP,
        now=datetime(2026, 1, 1),
    )
    details = result.details_for("archive")
    assert details is not None
    zip_path = details.items[0].destination
    assert zip_path is not None
    execution = execute_selected_actions(result, {("archive", 0)}, db_path=history)
    assert execution.batch_id is not None

    original.write_text("replacement")
    first_undo = undo_execution(execution.batch_id, db_path=history)
    assert first_undo.errors
    assert zip_path.is_file()

    original.unlink()
    second_undo = undo_execution(execution.batch_id, db_path=history)
    assert not second_undo.errors
    assert original.read_text() == "original"
    assert not zip_path.exists()


def test_zip_archive_collision_does_not_remove_sources(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "old.txt"
    original.write_text("original")
    _set_mtime(original, datetime(2020, 1, 1))
    destination = tmp_path / "archives"
    result = analyze_archive_folder(
        source,
        destination,
        365,
        archive_format=ArchiveFormat.ZIP,
        now=datetime(2026, 1, 1),
    )
    details = result.details_for("archive")
    assert details is not None
    zip_path = details.items[0].destination
    assert zip_path is not None
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"keep existing archive")

    execution = execute_selected_actions(result, {("archive", 0)})

    assert execution.success == 0
    assert execution.errors
    assert original.read_text() == "original"
    assert zip_path.read_bytes() == b"keep existing archive"


@pytest.mark.parametrize("age", [0, -1])
def test_archive_rejects_invalid_age(tmp_path: Path, age: int) -> None:
    source = tmp_path / "source"
    source.mkdir()
    from file_janitor.application import scan_folder

    scan = scan_folder(source, compute_hashes=False, compute_content_type=False)
    with pytest.raises(ArchivePlanError, match="au moins un jour"):
        from file_janitor.archiving import build_archive_actions

        build_archive_actions(scan, tmp_path / "archive", age)


def test_archive_rejects_destination_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    from file_janitor.application import scan_folder
    from file_janitor.archiving import build_archive_actions

    scan = scan_folder(source, compute_hashes=False, compute_content_type=False)
    with pytest.raises(ArchivePlanError, match="distinct"):
        build_archive_actions(scan, source / "archive", 30)
