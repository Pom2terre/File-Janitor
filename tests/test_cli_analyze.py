"""Tests de la commande `analyze` : le menu interactif ne doit jamais
bloquer une exécution non interactive (scripts, CI, tests)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from file_janitor.cli import app

runner = CliRunner()


def test_analyze_non_interactive_does_not_hang(tmp_path: Path) -> None:
    """CliRunner simule un stdin non-tty : la commande doit se terminer
    sans jamais attendre de saisie utilisateur."""
    (tmp_path / "a.txt").write_text("hello")

    result = runner.invoke(app, ["analyze", str(tmp_path), "--no-hash"])

    assert result.exit_code == 0
    assert "Actions proposées" in result.stdout
    assert "Votre choix" not in result.stdout


def test_analyze_shows_summary_categories(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")

    result = runner.invoke(app, ["analyze", str(tmp_path), "--no-hash"])

    assert result.exit_code == 0
    assert "Doublons" in result.stdout
    assert "Extensions trompeuses" in result.stdout
