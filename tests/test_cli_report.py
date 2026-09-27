"""Tests CLI de la commande `report` : inférence du format JSON/HTML.

On évite volontairement --open ici : il ouvrirait un vrai navigateur.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from file_janitor.cli import app

runner = CliRunner()


def test_report_defaults_to_json_stdout(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["total_count"] == 1


def test_report_json_stdout_preserves_long_paths(tmp_path: Path) -> None:
    long_folder = tmp_path / ("dossier_long_" * 7)
    long_folder.mkdir()
    (long_folder / "fichier.txt").write_text("bonjour")

    result = runner.invoke(app, ["report", str(long_folder)])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["root"] == str(long_folder)


def test_report_json_to_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    output = tmp_path / "out.json"

    result = runner.invoke(app, ["report", str(tmp_path), "-o", str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert json.loads(output.read_text())["total_count"] == 1


def test_report_html_inferred_from_extension(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    output = tmp_path / "out.html"

    result = runner.invoke(app, ["report", str(tmp_path), "-o", str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert "<!DOCTYPE html>" in output.read_text(encoding="utf-8")


def test_report_explicit_html_format_without_output_uses_default_name(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.txt").write_text("hello")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["report", str(tmp_path), "--format", "html"])

    assert result.exit_code == 0
    generated = list(tmp_path.glob("*_janitor_report.html"))
    assert len(generated) == 1


def test_report_explicit_json_format_overrides_html_extension(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    output = tmp_path / "out.html"  # extension html mais on force json

    result = runner.invoke(app, ["report", str(tmp_path), "-o", str(output), "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(output.read_text())["total_count"] == 1
