"""Tests du module de règles personnalisées : parsing, validation, matching."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from file_janitor.models import FileRecord
from file_janitor.plan.rules import Rule, RuleError, first_matching_rule, load_rules


def _record(
    name: str,
    *,
    size: int = 100,
    mtime: datetime = datetime(2024, 1, 1),
    content_family: str | None = None,
) -> FileRecord:
    return FileRecord(
        path=Path(f"/tmp/{name}"),
        size=size,
        mtime=mtime,
        extension=Path(name).suffix.lower(),
        content_family=content_family,
    )


def _write_rules(tmp_path: Path, rules: list[dict]) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


def test_load_valid_rules(tmp_path: Path) -> None:
    path = _write_rules(
        tmp_path,
        [
            {
                "name": "Films",
                "destination": "Films",
                "extension": ["mp4", "mkv"],
                "name_contains": "movie",
                "size_gt": "100MB",
            }
        ],
    )

    rules = load_rules(path)

    assert len(rules) == 1
    rule = rules[0]
    assert rule.name == "Films"
    assert rule.destination == "Films"
    assert rule.extension == ("mp4", "mkv")
    assert rule.size_gt == 100 * 1024 * 1024


def test_rule_matches_all_conditions_required() -> None:
    rule = Rule(name="Films", destination="Films", extension=("mp4",), name_contains="movie", size_gt=100)

    matching = _record("my-movie.mp4", size=200)
    wrong_ext = _record("my-movie.mkv", size=200)
    wrong_name = _record("clip.mp4", size=200)
    too_small = _record("my-movie.mp4", size=50)

    assert rule.matches(matching)
    assert not rule.matches(wrong_ext)
    assert not rule.matches(wrong_name)
    assert not rule.matches(too_small)


def test_rule_date_conditions() -> None:
    rule = Rule(name="Recent", destination="Recent", date_after=datetime(2024, 1, 1))

    recent = _record("a.txt", mtime=datetime(2024, 6, 1))
    old = _record("b.txt", mtime=datetime(2023, 1, 1))

    assert rule.matches(recent)
    assert not rule.matches(old)


def test_rule_content_family_condition() -> None:
    rule = Rule(name="Images", destination="Images", content_family="image")

    image_record = _record("a.bin", content_family="image")
    text_record = _record("b.bin", content_family="text")

    assert rule.matches(image_record)
    assert not rule.matches(text_record)


def test_first_matching_rule_returns_first_in_order() -> None:
    rule_a = Rule(name="A", destination="A", extension=("txt",))
    rule_b = Rule(name="B", destination="B", extension=("txt",))

    record = _record("a.txt")

    assert first_matching_rule(record, [rule_a, rule_b]) is rule_a
    assert first_matching_rule(record, [rule_b, rule_a]) is rule_b


def test_first_matching_rule_returns_none_when_no_match() -> None:
    rule = Rule(name="A", destination="A", extension=("mp3",))
    record = _record("a.txt")

    assert first_matching_rule(record, [rule]) is None


@pytest.mark.parametrize(
    "size_str,expected",
    [
        ("100", 100),
        ("1KB", 1024),
        ("2MB", 2 * 1024 * 1024),
        ("1GB", 1024 * 1024 * 1024),
        ("1.5MB", int(1.5 * 1024 * 1024)),
    ],
)
def test_size_parsing(tmp_path: Path, size_str: str, expected: int) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": "d", "size_gt": size_str}])
    rules = load_rules(path)
    assert rules[0].size_gt == expected


def test_invalid_size_raises(tmp_path: Path) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": "d", "size_gt": "not-a-size"}])
    with pytest.raises(RuleError, match="valeur invalide"):
        load_rules(path)


def test_invalid_date_raises(tmp_path: Path) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": "d", "date_after": "not-a-date"}])
    with pytest.raises(RuleError, match="date invalide"):
        load_rules(path)


def test_missing_destination_raises(tmp_path: Path) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "extension": ["txt"]}])
    with pytest.raises(RuleError, match="destination"):
        load_rules(path)


def test_no_condition_raises(tmp_path: Path) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": "d"}])
    with pytest.raises(RuleError, match="aucune condition"):
        load_rules(path)


def test_unknown_key_raises(tmp_path: Path) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": "d", "sizegt": "1MB"}])
    with pytest.raises(RuleError, match="inconnue"):
        load_rules(path)


@pytest.mark.parametrize("bad_destination", ["../escape", "/absolute/path", "~/home"])
def test_unsafe_destination_raises(tmp_path: Path, bad_destination: str) -> None:
    path = _write_rules(tmp_path, [{"name": "R", "destination": bad_destination, "extension": ["txt"]}])
    with pytest.raises(RuleError, match="invalide"):
        load_rules(path)


def test_root_must_be_a_list(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"name": "not-a-list"}), encoding="utf-8")
    with pytest.raises(RuleError, match="liste"):
        load_rules(path)


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RuleError, match="JSON invalide"):
        load_rules(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RuleError):
        load_rules(tmp_path / "does_not_exist.json")
