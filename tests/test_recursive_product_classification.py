"""Tests 4E-10C : classement récursif pour les quatre modes produit."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from file_janitor.application import (
    ClassificationMode,
    classification_plan_config,
    create_plan,
)
from file_janitor.models import FileCategory, FileRecord, ScanResult


def _record(
    root: Path,
    relative: str,
    *,
    size: int = 10,
    mtime: datetime | None = None,
) -> FileRecord:
    path = root / relative
    return FileRecord(
        path=path,
        size=size,
        mtime=mtime or datetime(2026, 7, 15, 12, 0),
        extension=path.suffix.lower(),
    )


@pytest.mark.parametrize("mode", tuple(ClassificationMode))
def test_product_classification_modes_are_recursive(mode: ClassificationMode) -> None:
    config = classification_plan_config(mode)
    assert config.recursive_sort is True


@pytest.mark.parametrize(
    ("mode", "relative", "expected_parent"),
    (
        (ClassificationMode.EXTENSION, "nested/deep/photo.jpg", "jpg"),
        (ClassificationMode.DATE, "nested/deep/photo.jpg", "2026-07"),
        (ClassificationMode.SIZE, "nested/deep/blob.bin", "moins_de_10_Ko"),
    ),
)
def test_nested_files_are_classified_by_selected_product_mode(
    tmp_path: Path,
    mode: ClassificationMode,
    relative: str,
    expected_parent: str,
) -> None:
    scan = ScanResult(
        root=tmp_path,
        files=[_record(tmp_path, relative, size=1024)],
    )

    plan = create_plan(scan, classification_plan_config(mode))
    items = plan.items(FileCategory.TO_SORT)

    assert len(items) == 1
    assert items[0].path == tmp_path / relative
    assert items[0].destination is not None
    assert items[0].destination.parent.name == expected_parent


def test_common_name_mode_only_classifies_shared_roots(tmp_path: Path) -> None:
    grouped_a = _record(tmp_path, "nested/holiday_001.jpg", size=1024)
    grouped_b = _record(tmp_path, "nested/holiday_002.jpg", size=1024)
    isolated = _record(tmp_path, "nested/portrait_001.jpg", size=1024)
    unrelated = _record(tmp_path, "nested/readme.txt", size=1024)
    scan = ScanResult(
        root=tmp_path,
        files=[grouped_a, grouped_b, isolated, unrelated],
    )

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.COMMON_NAME),
    )
    items = plan.items(FileCategory.TO_SORT)

    assert {item.path for item in items} == {grouped_a.path, grouped_b.path}
    assert {item.destination.parent.name for item in items if item.destination} == {
        "holiday"
    }



def test_common_name_mode_infers_shared_token_prefixes(tmp_path: Path) -> None:
    files = [
        _record(tmp_path, "nested/Fishing_2018.pdf"),
        _record(tmp_path, "nested/Fishing_2019.pdf"),
        _record(tmp_path, "nested/Guide-final.pdf"),
        _record(tmp_path, "nested/Guide-draft.pdf"),
        _record(tmp_path, "nested/unrelated.pdf"),
    ]
    scan = ScanResult(root=tmp_path, files=files)

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.COMMON_NAME),
    )
    items = plan.items(FileCategory.TO_SORT)

    destinations = {
        item.path.name: item.destination.parent.name
        for item in items
        if item.destination is not None
    }
    assert destinations == {
        "Fishing_2018.pdf": "Fishing",
        "Fishing_2019.pdf": "Fishing",
        "Guide-final.pdf": "Guide",
        "Guide-draft.pdf": "Guide",
    }


def test_common_name_mode_prefers_most_specific_shared_root(tmp_path: Path) -> None:
    files = [
        _record(tmp_path, "project_alpha_draft.txt"),
        _record(tmp_path, "project_alpha_final.txt"),
        _record(tmp_path, "project_beta_notes.txt"),
    ]
    scan = ScanResult(root=tmp_path, files=files)

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.COMMON_NAME),
    )
    items = plan.items(FileCategory.TO_SORT)

    assert {item.path.name for item in items} == {
        "project_alpha_draft.txt",
        "project_alpha_final.txt",
    }
    assert {
        item.destination.parent.name for item in items if item.destination
    } == {"project_alpha"}


def test_common_name_mode_does_not_use_arbitrary_character_prefixes(
    tmp_path: Path,
) -> None:
    files = [
        _record(tmp_path, "alpha.txt"),
        _record(tmp_path, "alphabet.txt"),
        _record(tmp_path, "report.txt"),
    ]
    scan = ScanResult(root=tmp_path, files=files)

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.COMMON_NAME),
    )

    assert plan.items(FileCategory.TO_SORT) == []


def test_common_name_mode_normalizes_separator_style_for_matching(
    tmp_path: Path,
) -> None:
    files = [
        _record(tmp_path, "client-report-final.pdf"),
        _record(tmp_path, "client_report_draft.pdf"),
    ]
    scan = ScanResult(root=tmp_path, files=files)

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.COMMON_NAME),
    )
    items = plan.items(FileCategory.TO_SORT)

    assert len(items) == 2
    assert {
        item.destination.parent.name for item in items if item.destination
    } == {"client-report"}



def test_date_mode_can_produce_120_month_groups_for_ten_years(
    tmp_path: Path,
) -> None:
    files: list[FileRecord] = []
    year = 2016
    month = 1
    for index in range(120):
        files.append(
            _record(
                tmp_path,
                f"archive/source-{index:03d}.dat",
                mtime=datetime(year, month, 15, 12, 0),
            )
        )
        month += 1
        if month == 13:
            month = 1
            year += 1

    scan = ScanResult(root=tmp_path, files=files)
    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.DATE),
    )
    items = plan.items(FileCategory.TO_SORT)

    assert len(items) == 120
    groups = {
        item.destination.parent.name
        for item in items
        if item.destination is not None
    }
    assert len(groups) == 120
    assert "2016-01" in groups
    assert "2025-12" in groups


def test_age_and_size_diagnostics_do_not_exclude_classification(
    tmp_path: Path,
) -> None:
    old = datetime.now() - timedelta(days=800)
    record = _record(
        tmp_path,
        "nested/huge-old.iso",
        size=800 * 1024 * 1024,
        mtime=old,
    )
    scan = ScanResult(root=tmp_path, files=[record])

    plan = create_plan(
        scan,
        classification_plan_config(ClassificationMode.EXTENSION),
    )

    assert [item.path for item in plan.items(FileCategory.OLD_FILE)] == [record.path]
    assert [item.path for item in plan.items(FileCategory.LARGE_FILE)] == [record.path]
    assert [item.path for item in plan.items(FileCategory.TO_SORT)] == [record.path]


@pytest.mark.parametrize(
    ("mode", "relative"),
    (
        (ClassificationMode.EXTENSION, "jpg/photo.jpg"),
        (ClassificationMode.DATE, "2026-07/photo.jpg"),
        (ClassificationMode.COMMON_NAME, "holiday/holiday_001.jpg"),
        (ClassificationMode.SIZE, "moins_de_10_Ko/blob.bin"),
    ),
)
def test_already_classified_file_is_not_planned_as_self_move(
    tmp_path: Path,
    mode: ClassificationMode,
    relative: str,
) -> None:
    scan = ScanResult(
        root=tmp_path,
        files=[_record(tmp_path, relative, size=1024)],
    )

    plan = create_plan(scan, classification_plan_config(mode))

    assert plan.items(FileCategory.TO_SORT) == []
