"""Tests d'architecture de la frontière CLI -> application."""

from __future__ import annotations

import ast
from pathlib import Path


def test_cli_does_not_import_engine_internals_directly() -> None:
    cli_path = Path(__file__).parents[1] / "file_janitor" / "cli.py"
    tree = ast.parse(cli_path.read_text(encoding="utf-8"))

    forbidden_prefixes = (
        "file_janitor.executor",
        "file_janitor.scanner",
        "file_janitor.storage",
        "file_janitor.plan",
        "file_janitor.report",
        "file_janitor.scheduler",
        "file_janitor.models",
    )
    imported_modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    forbidden = [
        module
        for module in imported_modules
        if module.startswith(forbidden_prefixes)
    ]

    assert forbidden == []


def test_cli_does_not_access_plan_internals_directly() -> None:
    """La CLI consomme les DTO et cas d’usage de la couche application."""

    cli_path = Path(__file__).parents[1] / "file_janitor" / "cli.py"
    tree = ast.parse(cli_path.read_text(encoding="utf-8"))

    direct_plan_access = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"items", "total_size"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "plan"
    ]

    assert direct_plan_access == []
