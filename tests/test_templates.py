"""Tests du module de templates (regroupement par motif commun dans le nom)."""

from __future__ import annotations

import pytest

from file_janitor.plan.templates import DEFAULT_TEMPLATE_PATTERN, Template, TemplateError


def test_default_pattern_strips_trailing_counter() -> None:
    template = Template.compile(DEFAULT_TEMPLATE_PATTERN)

    assert template.extract("hello-world-1") == "hello-world"
    assert template.extract("hello-world-23") == "hello-world"
    assert template.extract("shiny_stars_042") == "shiny_stars"
    assert template.extract("space name 7") == "space name"


def test_default_pattern_no_match_without_trailing_number() -> None:
    template = Template.compile(DEFAULT_TEMPLATE_PATTERN)

    assert template.extract("standalone") is None
    assert template.extract("IMG") is None


def test_compile_rejects_pattern_without_capture_group() -> None:
    with pytest.raises(TemplateError, match="groupe de capture"):
        Template.compile("^hello")


def test_compile_rejects_invalid_regex() -> None:
    with pytest.raises(TemplateError, match="invalide"):
        Template.compile("^(hello[")


def test_custom_pattern() -> None:
    # Regroupe par préfixe de 3 caractères avant un underscore.
    template = Template.compile(r"^(\w{3})_.*$")

    assert template.extract("abc_report") == "abc"
    assert template.extract("xy_report") is None  # ne correspond pas (2 caractères)
