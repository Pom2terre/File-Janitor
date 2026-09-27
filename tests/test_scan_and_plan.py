"""Tests du scanner et du builder de plan sur une arborescence temporaire."""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

from file_janitor.models import ActionKind, FileCategory
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.scanner.scan import scan_directory


def _touch(
    path: Path,
    content: str = "x",
    days_old: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    if days_old:
        past = time.time() - days_old * 86400
        os.utime(path, (past, past))


def test_scan_finds_all_files(tmp_path: Path) -> None:
    _touch(tmp_path / "a.txt")
    _touch(tmp_path / "sub" / "b.txt")

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    assert result.total_count == 2
    assert not result.errors




def test_scan_reports_directory_traversal_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    visible = tmp_path / "visible.txt"
    _touch(visible)
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    real_walk = os.walk

    def walk_with_blocked_directory_error(
        top,
        *,
        topdown=True,
        onerror=None,
        followlinks=False,
    ):
        top_path = Path(top)
        if top_path != tmp_path:
            yield from real_walk(
                top,
                topdown=topdown,
                onerror=onerror,
                followlinks=followlinks,
            )
            return

        yield str(tmp_path), ["blocked"], ["visible.txt"]
        if onerror is not None:
            onerror(
                PermissionError(
                    errno.EACCES,
                    os.strerror(errno.EACCES),
                    str(blocked),
                )
            )

    monkeypatch.setattr(os, "walk", walk_with_blocked_directory_error)

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    assert result.total_count == 1
    assert len(result.errors) == 1
    assert str(blocked) in result.errors[0]
    assert f"[Errno {errno.EACCES}]" in result.errors[0]


def test_scan_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"

    result = scan_directory(missing)

    assert result.errors
    assert result.total_count == 0


def test_duplicate_detection(tmp_path: Path) -> None:
    _touch(
        tmp_path / "a.txt",
        content="same content",
        days_old=5,
    )
    _touch(
        tmp_path / "b.txt",
        content="same content",
        days_old=1,
    )
    _touch(
        tmp_path / "c.txt",
        content="different",
    )

    scan = scan_directory(
        tmp_path,
        compute_hashes=True,
    )

    plan = build_plan(scan)

    duplicates = plan.items(
        FileCategory.DUPLICATE
    )

    assert len(duplicates) == 1

    # Le plus récent est marqué comme doublon,
    # l'original (plus ancien) est gardé.
    assert duplicates[0].path.name == "b.txt"


def test_large_file_detection(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2048)

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(
        scan,
        PlanConfig(
            large_file_threshold=1024,
        ),
    )

    large_items = plan.items(
        FileCategory.LARGE_FILE
    )

    assert len(large_items) == 1
    assert large_items[0].path.name == "big.bin"


def test_old_archive_detection(tmp_path: Path) -> None:
    _touch(
        tmp_path / "old.zip",
        days_old=400,
    )
    _touch(
        tmp_path / "recent.zip",
        days_old=1,
    )

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(scan)

    old_archives = plan.items(
        FileCategory.OLD_ARCHIVE
    )

    assert {
        item.path.name
        for item in old_archives
    } == {"old.zip"}


def test_to_sort_only_root_level(tmp_path: Path) -> None:
    _touch(tmp_path / "photo.jpg")
    _touch(tmp_path / "sub" / "nested.jpg")

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(scan)

    to_sort = plan.items(
        FileCategory.TO_SORT
    )

    assert len(to_sort) == 1
    assert to_sort[0].path.name == "photo.jpg"


def test_duplicate_is_planned_as_trash(
    tmp_path: Path,
) -> None:
    _touch(
        tmp_path / "a.txt",
        content="same",
        days_old=5,
    )
    _touch(
        tmp_path / "b.txt",
        content="same",
        days_old=1,
    )

    scan = scan_directory(
        tmp_path,
        compute_hashes=True,
    )

    plan = build_plan(scan)

    items = plan.items(
        FileCategory.DUPLICATE
    )

    assert len(items) == 1
    assert items[0].action is ActionKind.TRASH


def test_old_archive_is_planned_as_trash(
    tmp_path: Path,
) -> None:
    _touch(
        tmp_path / "old.zip",
        days_old=400,
    )

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(scan)

    items = plan.items(
        FileCategory.OLD_ARCHIVE
    )

    assert len(items) == 1
    assert items[0].action is ActionKind.TRASH


def test_sort_item_is_planned_as_move(
    tmp_path: Path,
) -> None:
    _touch(tmp_path / "photo.jpg")

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(scan)

    items = plan.items(
        FileCategory.TO_SORT
    )

    assert len(items) == 1
    assert items[0].action is ActionKind.MOVE
    assert items[0].destination is not None


def test_informational_items_have_no_action(
    tmp_path: Path,
) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 2048)

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    plan = build_plan(
        scan,
        PlanConfig(
            large_file_threshold=1024,
        ),
    )

    items = plan.items(
        FileCategory.LARGE_FILE
    )

    assert len(items) == 1
    assert items[0].action is ActionKind.NONE


def test_scan_captures_filesystem_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "identity.txt"
    _touch(source, content="identity")

    expected_stat = source.stat()

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    assert len(scan.files) == 1

    record = scan.files[0]

    assert record.identity is not None
    assert record.identity.device == expected_stat.st_dev
    assert record.identity.inode == expected_stat.st_ino
    assert record.identity.size == expected_stat.st_size
    assert record.identity.mtime_ns == expected_stat.st_mtime_ns


def test_plan_propagates_filesystem_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    _touch(source, content="image")

    scan = scan_directory(
        tmp_path,
        compute_hashes=False,
    )

    record = next(
        record
        for record in scan.files
        if record.path == source
    )

    assert record.identity is not None

    plan = build_plan(scan)

    items = plan.items(
        FileCategory.TO_SORT
    )

    assert len(items) == 1
    assert items[0].identity is record.identity


def test_metadata_light_scan_does_not_stat_each_file(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "remote.txt"
    source.write_text("payload")
    original_stat = Path.stat

    def guarded_stat(self, *args, **kwargs):
        if self == source:
            raise AssertionError("per-file stat must not run in metadata-light mode")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        collect_metadata=False,
    )

    assert result.total_count == 1
    assert result.metadata_complete is False
    assert result.files[0].path == source
    assert result.files[0].size == 0
    assert result.files[0].identity is None


def test_metadata_light_plan_skips_metadata_dependent_categories(tmp_path: Path) -> None:
    source = tmp_path / "archive.zip"
    source.write_text("payload")
    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        collect_metadata=False,
    )
    plan = build_plan(result, PlanConfig(recursive_sort=True))

    assert plan.items(FileCategory.OLD_ARCHIVE) == []
    assert plan.items(FileCategory.LARGE_FILE) == []
    assert plan.items(FileCategory.OLD_FILE) == []


def test_empty_files_are_not_planned_as_duplicates(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"")
    (tmp_path / "b.txt").write_bytes(b"")

    scan = scan_directory(tmp_path, compute_hashes=True)
    plan = build_plan(scan)

    assert plan.items(FileCategory.DUPLICATE) == []


def test_batch_internal_collisions_get_deterministic_preview_destinations(
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.models import FileRecord, ScanResult
    from file_janitor.plan.grouping import GroupBy

    left = tmp_path / "left" / "report.pdf"
    right = tmp_path / "right" / "report.pdf"
    scan = ScanResult(root=tmp_path)
    scan.files.extend([
        FileRecord(left, 1, datetime.now(), ".pdf"),
        FileRecord(right, 1, datetime.now(), ".pdf"),
    ])

    plan = build_plan(
        scan,
        PlanConfig(group_by=GroupBy.EXTENSION, recursive_sort=True),
    )
    items = list(plan.items(FileCategory.TO_SORT))

    assert [item.destination.name for item in items] == [
        "report.pdf",
        "report — right.pdf",
    ]
    assert all(item.conflict for item in items)
    assert "dossier source « right »" in (items[1].conflict_reason or "")


def test_batch_collision_reservation_does_not_steal_contextual_name(
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.models import FileRecord, ScanResult
    from file_janitor.plan.grouping import GroupBy

    paths = [
        tmp_path / "a" / "report.pdf",
        tmp_path / "b" / "report.pdf",
        tmp_path / "c" / "report — b.pdf",
    ]
    scan = ScanResult(root=tmp_path)
    scan.files.extend(
        FileRecord(path, 1, datetime.now(), ".pdf") for path in paths
    )

    plan = build_plan(
        scan,
        PlanConfig(group_by=GroupBy.EXTENSION, recursive_sort=True),
    )
    names = {
        item.path: item.destination.name
        for item in plan.items(FileCategory.TO_SORT)
    }

    assert names[paths[0]] == "report.pdf"
    assert names[paths[1]] == "report — b (2).pdf"
    assert names[paths[2]] == "report — b.pdf"


def test_batch_collision_uses_deeper_source_context_when_parent_names_repeat(
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.models import FileRecord, ScanResult
    from file_janitor.plan.grouping import GroupBy

    paths = [
        tmp_path / "alpha" / "docs" / "report.pdf",
        tmp_path / "beta" / "docs" / "report.pdf",
        tmp_path / "gamma" / "docs" / "report.pdf",
    ]
    scan = ScanResult(root=tmp_path)
    scan.files.extend(
        FileRecord(path, 1, datetime.now(), ".pdf") for path in paths
    )

    plan = build_plan(
        scan,
        PlanConfig(group_by=GroupBy.EXTENSION, recursive_sort=True),
    )
    names = [
        item.destination.name for item in plan.items(FileCategory.TO_SORT)
    ]

    assert names == [
        "report.pdf",
        "report — beta - docs.pdf",
        "report — gamma - docs.pdf",
    ]


def test_batch_collision_sanitizes_context_for_remote_destination_names(
    tmp_path: Path,
) -> None:
    from datetime import datetime
    from file_janitor.models import FileRecord, ScanResult
    from file_janitor.plan.grouping import GroupBy

    paths = [
        tmp_path / "A" / "report.pdf",
        tmp_path / "Project:Two" / "report.pdf",
    ]
    scan = ScanResult(root=tmp_path)
    scan.files.extend(
        FileRecord(path, 1, datetime.now(), ".pdf") for path in paths
    )

    plan = build_plan(
        scan,
        PlanConfig(group_by=GroupBy.EXTENSION, recursive_sort=True),
    )
    names = [
        item.destination.name for item in plan.items(FileCategory.TO_SORT)
    ]

    assert names == ["report.pdf", "report — Project_Two.pdf"]



def test_size_strategy_records_remote_freshness_guard(tmp_path: Path) -> None:
    from file_janitor.plan.grouping import GroupBy

    source = tmp_path / "small.bin"
    source.write_bytes(b"x")
    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(
        scan,
        PlanConfig(group_by=GroupBy.SIZE, recursive_sort=True),
    )

    item = next(
        item
        for item in plan.items(FileCategory.TO_SORT)
        if item.path == source
    )
    assert item.classification_guard is not None
    assert item.classification_guard.group_by == "size"
    assert item.classification_guard.folder == "moins_de_10_Ko"
    assert item.classification_guard.date_granularity is None


def test_date_strategy_records_granularity_in_remote_freshness_guard(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    from file_janitor.plan.grouping import DateGranularity, GroupBy

    source = tmp_path / "dated.txt"
    source.write_text("dated")
    timestamp = datetime(2026, 2, 15, 12, 0).timestamp()
    os.utime(source, (timestamp, timestamp))
    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(
        scan,
        PlanConfig(
            group_by=GroupBy.DATE,
            date_granularity=DateGranularity.MONTH,
            recursive_sort=True,
        ),
    )

    item = next(
        item
        for item in plan.items(FileCategory.TO_SORT)
        if item.path == source
    )
    assert item.classification_guard is not None
    assert item.classification_guard.group_by == "date"
    assert item.classification_guard.folder == "2026-02"
    assert item.classification_guard.date_granularity == "month"
