"""Tests d'intégration : règles personnalisées + repli sur --group-by pour
les fichiers non couverts par une règle."""

from __future__ import annotations

from pathlib import Path

from file_janitor.models import FileCategory
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.plan.grouping import GroupBy
from file_janitor.plan.rules import Rule
from file_janitor.scanner.scan import scan_directory


def test_matched_files_use_rule_destination_unmatched_fall_back_to_group_by(tmp_path: Path) -> None:
    (tmp_path / "my-movie.mp4").write_bytes(b"0" * 200)  # matche la règle
    (tmp_path / "clip.mp4").write_bytes(b"0" * 200)  # même extension, ne matche pas (pas "movie" dans le nom)
    (tmp_path / "notes.txt").write_text("x")  # aucune règle ne s'applique

    rules = [Rule(name="Films", destination="Films", extension=("mp4",), name_contains="movie")]
    config = PlanConfig(group_by=GroupBy.EXTENSION, rules=rules)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, config)

    items = {item.path.name: item for item in plan.items(FileCategory.TO_SORT)}

    assert items["my-movie.mp4"].destination == tmp_path / "Films" / "my-movie.mp4"
    assert "Règle « Films »" in items["my-movie.mp4"].reason

    # Non matché par la règle -> repli sur --group-by extension.
    assert items["clip.mp4"].destination == tmp_path / "mp4" / "clip.mp4"
    assert items["notes.txt"].destination == tmp_path / "txt" / "notes.txt"


def test_first_matching_rule_wins_over_later_rules(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_text("x")

    rules = [
        Rule(name="First", destination="First", extension=("pdf",)),
        Rule(name="Second", destination="Second", extension=("pdf",)),
    ]
    config = PlanConfig(rules=rules)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, config)

    items = plan.items(FileCategory.TO_SORT)
    assert len(items) == 1
    assert items[0].destination == tmp_path / "First" / "report.pdf"


def test_rules_with_nested_destination(tmp_path: Path) -> None:
    (tmp_path / "vacances_paris.jpg").write_text("x")

    rules = [Rule(name="Vacances", destination="Photos/Vacances", extension=("jpg",))]
    config = PlanConfig(rules=rules)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, config)

    items = plan.items(FileCategory.TO_SORT)
    assert items[0].destination == tmp_path / "Photos" / "Vacances" / "vacances_paris.jpg"


def test_no_rules_behaves_like_before(tmp_path: Path) -> None:
    """PlanConfig() sans rules doit se comporter exactement comme avant
    l'introduction des règles (pas de régression)."""
    (tmp_path / "photo.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=True)
    plan = build_plan(scan, PlanConfig())

    items = plan.items(FileCategory.TO_SORT)
    assert len(items) == 1
    assert items[0].destination == tmp_path / "Images" / "photo.jpg"
