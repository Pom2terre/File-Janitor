"""Tests d'intégration : template + repli sur --group-by, et interaction
avec les règles personnalisées (rules > template > group_by)."""

from __future__ import annotations

from pathlib import Path

from file_janitor.models import FileCategory
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.plan.grouping import GroupBy
from file_janitor.plan.rules import Rule
from file_janitor.plan.templates import DEFAULT_TEMPLATE_PATTERN, Template
from file_janitor.scanner.scan import scan_directory


def test_template_groups_matching_files_reproducing_qiplex_example(tmp_path: Path) -> None:
    for name in ["hello-world-1.jpg", "hello-world-2.jpg", "hello-world-3.jpg"]:
        (tmp_path / name).write_text("x")
    for name in ["shiny-stars-1.jpg", "shiny-stars-2.jpg", "shiny-stars-3.jpg"]:
        (tmp_path / name).write_text("x")
    (tmp_path / "standalone.txt").write_text("x")

    template = Template.compile(DEFAULT_TEMPLATE_PATTERN)
    config = PlanConfig(group_by=GroupBy.EXTENSION, template=template)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, config)

    items = {item.path.name: item for item in plan.items(FileCategory.TO_SORT)}

    for name in ["hello-world-1.jpg", "hello-world-2.jpg", "hello-world-3.jpg"]:
        assert items[name].destination == tmp_path / "hello-world" / name
    for name in ["shiny-stars-1.jpg", "shiny-stars-2.jpg", "shiny-stars-3.jpg"]:
        assert items[name].destination == tmp_path / "shiny-stars" / name

    # Pas de compteur final -> ne correspond pas au template -> repli sur --group-by extension.
    assert items["standalone.txt"].destination == tmp_path / "txt" / "standalone.txt"


def test_rules_take_priority_over_template(tmp_path: Path) -> None:
    (tmp_path / "hello-world-1.jpg").write_text("x")

    rules = [Rule(name="Prioritaire", destination="Prioritaire", extension=("jpg",))]
    template = Template.compile(DEFAULT_TEMPLATE_PATTERN)
    config = PlanConfig(rules=rules, template=template)

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, config)

    items = plan.items(FileCategory.TO_SORT)
    assert len(items) == 1
    # La règle l'emporte sur le template, même si le nom matcherait aussi le template.
    assert items[0].destination == tmp_path / "Prioritaire" / "hello-world-1.jpg"


def test_no_template_behaves_like_before(tmp_path: Path) -> None:
    (tmp_path / "hello-world-1.jpg").write_text("x")

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, PlanConfig(group_by=GroupBy.EXTENSION))  # template=None par défaut

    items = plan.items(FileCategory.TO_SORT)
    assert items[0].destination == tmp_path / "jpg" / "hello-world-1.jpg"
