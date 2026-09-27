"""Destinations et garde de fraîcheur pour les tris composés."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from file_janitor.application import (
    ClassificationMode,
    analyze_folder,
    classification_plan_config,
    execute_selected_actions,
    summarize_classification_groups,
)
from file_janitor.plan.grouping import GroupBy


def _file(folder: Path, name: str, content: bytes, month: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    timestamp = datetime(2025, month, 15, 12).timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


@pytest.mark.parametrize(
    ("primary", "secondary", "expected"),
    [
        (ClassificationMode.DATE, ClassificationMode.EXTENSION, "2025-01/mp4/clip.MP4"),
        (ClassificationMode.EXTENSION, ClassificationMode.DATE, "mp4/2025-01/clip.MP4"),
        (ClassificationMode.DATE, ClassificationMode.SIZE, "2025-01/moins_de_10_Ko/clip.MP4"),
        (ClassificationMode.SIZE, ClassificationMode.DATE, "moins_de_10_Ko/2025-01/clip.MP4"),
        (ClassificationMode.EXTENSION, ClassificationMode.SIZE, "mp4/moins_de_10_Ko/clip.MP4"),
    ],
)
def test_two_criteria_produce_one_final_destination(
    tmp_path: Path, primary: ClassificationMode, secondary: ClassificationMode, expected: str,
) -> None:
    root = tmp_path / "source"
    source = _file(root / "old", "clip.MP4", b"video", 1)
    config = classification_plan_config(primary, secondary_mode=secondary)
    result = analyze_folder(root, config=config, compute_hashes=False, compute_content_type=False)
    details = result.details_for("to_sort")
    assert details is not None
    assert len(details.items) == 1
    assert details.items[0].destination == root / expected
    assert [(group.name, group.item_count) for group in summarize_classification_groups(result, primary, secondary)] == [
        (expected.rsplit("/", 1)[0], 1),
    ]

    execution = execute_selected_actions(result, {("to_sort", 0)}, db_path=tmp_path / "history.db")
    assert execution.success == 1
    assert not source.exists()
    assert (root / expected).read_bytes() == b"video"
    again = analyze_folder(root, config=config, compute_hashes=False, compute_content_type=False)
    assert again.details_for("to_sort").items == ()


@pytest.mark.parametrize("primary,secondary", [
    (ClassificationMode.COMMON_NAME, ClassificationMode.DATE),
    (ClassificationMode.DATE, ClassificationMode.COMMON_NAME),
])
def test_common_name_groups_only_matching_files_within_first_criterion(
    tmp_path: Path, primary: ClassificationMode, secondary: ClassificationMode,
) -> None:
    root = tmp_path / "files"
    _file(root, "series-1.jpg", b"a", 2)
    _file(root, "series-2.jpg", b"b", 2)
    _file(root, "single.jpg", b"c", 2)
    result = analyze_folder(
        root, config=classification_plan_config(primary, secondary_mode=secondary),
        compute_hashes=False, compute_content_type=False,
    )
    items = result.details_for("to_sort").items
    expected = "series/2025-02" if primary is ClassificationMode.COMMON_NAME else "2025-02/series"
    assert {item.destination.parent.relative_to(root).as_posix() for item in items} == {expected}
    assert {item.path.name for item in items} == {"series-1.jpg", "series-2.jpg"}


def test_common_name_uses_members_already_in_a_previous_simple_folder(tmp_path: Path) -> None:
    root = tmp_path / "files"
    source = _file(root / "series", "series-1.jpg", b"a", 2)
    _file(root, "series-2.jpg", b"b", 2)
    result = analyze_folder(
        root,
        config=classification_plan_config(
            ClassificationMode.COMMON_NAME,
            secondary_mode=ClassificationMode.DATE,
        ),
        compute_hashes=False, compute_content_type=False,
    )
    items = result.details_for("to_sort").items
    assert {item.path.name for item in items} == {"series-1.jpg", "series-2.jpg"}
    assert next(item for item in items if item.path == source).destination == (
        root / "series" / "2025-02" / "series-1.jpg"
    )


def test_date_and_size_require_metadata_even_when_secondary(tmp_path: Path) -> None:
    root = tmp_path / "source"
    _file(root, "clip.mp4", b"x", 3)
    config = classification_plan_config(
        ClassificationMode.EXTENSION, secondary_mode=ClassificationMode.DATE,
    )
    result = analyze_folder(
        root, config=config, compute_hashes=False, compute_content_type=False,
        collect_metadata=False,
    )
    assert result.details_for("to_sort").items == ()


def test_combinations_must_have_distinct_criteria() -> None:
    with pytest.raises(ValueError, match="distincts"):
        classification_plan_config(
            ClassificationMode.DATE, secondary_mode=ClassificationMode.DATE,
        )
    assert classification_plan_config(
        ClassificationMode.DATE, secondary_mode=ClassificationMode.SIZE,
    ).secondary_group_by is GroupBy.SIZE
