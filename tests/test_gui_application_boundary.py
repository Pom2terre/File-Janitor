"""Tests d'architecture de la frontière GUI -> application."""

from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_PREFIXES = (
    "file_janitor.executor",
    "file_janitor.models",
    "file_janitor.path_safety",
    "file_janitor.plan",
    "file_janitor.report",
    "file_janitor.scanner",
    "file_janitor.scheduler",
    "file_janitor.storage",
)


def _python_files() -> list[Path]:
    gui_root = Path(__file__).parents[1] / "file_janitor" / "gui"
    return sorted(gui_root.rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    return modules


def test_gui_does_not_import_engine_internals_directly() -> None:
    violations: list[str] = []

    for path in _python_files():
        for module in _imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                relative_path = path.relative_to(Path(__file__).parents[1])
                violations.append(f"{relative_path}: {module}")

    assert violations == []
