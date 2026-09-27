"""Tests des stratégies de classement (--group-by extension/alphabet/size/date)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from file_janitor.models import FileCategory
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.plan.grouping import DateGranularity, GroupBy
from file_janitor.scanner.scan import scan_directory


def _destinations(tmp_path: Path, group_by: GroupBy, **config_kwargs) -> dict[str, Path]:
    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, PlanConfig(group_by=group_by, **config_kwargs))
    return {item.path.name: item.destination for item in plan.items(FileCategory.TO_SORT)}


def test_group_by_extension(tmp_path: Path) -> None:
    (tmp_path / "song.mp3").write_text("x")
    (tmp_path / "photo.jpg").write_text("x")
    (tmp_path / "noext").write_text("x")

    dest = _destinations(tmp_path, GroupBy.EXTENSION)

    assert dest["song.mp3"] == tmp_path / "mp3" / "song.mp3"
    assert dest["photo.jpg"] == tmp_path / "jpg" / "photo.jpg"
    assert dest["noext"] == tmp_path / "sans_extension" / "noext"


def test_group_by_alphabet(tmp_path: Path) -> None:
    (tmp_path / "Apple.txt").write_text("x")
    (tmp_path / "banana.txt").write_text("x")
    (tmp_path / "123numbers.txt").write_text("x")

    dest = _destinations(tmp_path, GroupBy.ALPHABET)

    assert dest["Apple.txt"] == tmp_path / "a" / "Apple.txt"
    assert dest["banana.txt"] == tmp_path / "b" / "banana.txt"
    assert dest["123numbers.txt"] == tmp_path / "#" / "123numbers.txt"


def test_group_by_size(tmp_path: Path) -> None:
    sizes = {
        "tiny.bin": 9 * 1024,
        "ten.bin": 10 * 1024,
        "fifty.bin": 50 * 1024,
        "hundred.bin": 100 * 1024,
        "five_hundred_k.bin": 500 * 1024,
        "one_mb.bin": 1024 * 1024,
        "ten_mb.bin": 10 * 1024 * 1024,
        "hundred_mb.bin": 100 * 1024 * 1024,
        "five_hundred_mb.bin": 500 * 1024 * 1024,
    }
    for name, size in sizes.items():
        path = tmp_path / name
        with path.open("wb") as handle:
            handle.truncate(size)

    dest = _destinations(tmp_path, GroupBy.SIZE)

    assert dest["tiny.bin"] == tmp_path / "moins_de_10_Ko" / "tiny.bin"
    assert dest["ten.bin"] == tmp_path / "10_a_50_Ko" / "ten.bin"
    assert dest["fifty.bin"] == tmp_path / "50_a_100_Ko" / "fifty.bin"
    assert dest["hundred.bin"] == tmp_path / "100_a_500_Ko" / "hundred.bin"
    assert dest["five_hundred_k.bin"] == (
        tmp_path / "500_Ko_a_1_Mo" / "five_hundred_k.bin"
    )
    assert dest["one_mb.bin"] == tmp_path / "1_a_10_Mo" / "one_mb.bin"
    assert dest["ten_mb.bin"] == tmp_path / "10_a_100_Mo" / "ten_mb.bin"
    assert dest["hundred_mb.bin"] == (
        tmp_path / "100_a_500_Mo" / "hundred_mb.bin"
    )
    assert dest["five_hundred_mb.bin"] == (
        tmp_path / "500_Mo_et_plus" / "five_hundred_mb.bin"
    )


def test_group_by_date_day_month_year(tmp_path: Path) -> None:
    path = tmp_path / "old.txt"
    path.write_text("x")
    target = time.mktime((2023, 5, 15, 12, 0, 0, 0, 0, -1))
    os.utime(path, (target, target))

    day = _destinations(tmp_path, GroupBy.DATE, date_granularity=DateGranularity.DAY)
    month = _destinations(tmp_path, GroupBy.DATE, date_granularity=DateGranularity.MONTH)
    year = _destinations(tmp_path, GroupBy.DATE, date_granularity=DateGranularity.YEAR)

    assert day["old.txt"] == tmp_path / "2023-05-15" / "old.txt"
    assert month["old.txt"] == tmp_path / "2023-05" / "old.txt"
    assert year["old.txt"] == tmp_path / "2023" / "old.txt"


def test_group_by_mechanical_strategies_include_all_files_regardless_of_content(tmp_path: Path) -> None:
    """Contrairement à GroupBy.KIND, les stratégies mécaniques classent tous
    les fichiers de la racine, y compris ceux au contenu non reconnu."""
    (tmp_path / "weird.xyz").write_bytes(bytes(range(256)))  # extension et contenu inconnus

    dest_kind = _destinations(tmp_path, GroupBy.KIND)
    dest_ext = _destinations(tmp_path, GroupBy.EXTENSION)

    assert "weird.xyz" not in dest_kind
    assert dest_ext["weird.xyz"] == tmp_path / "xyz" / "weird.xyz"


def test_group_by_kind_is_default(tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=True)
    plan = build_plan(scan)  # PlanConfig par défaut : group_by=KIND

    items = plan.items(FileCategory.TO_SORT)
    assert len(items) == 1
    assert items[0].destination == tmp_path / "Images" / "photo.jpg"
