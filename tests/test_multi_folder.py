"""Tests unitaires de PlanConfig.destination_root (destination commune) et
tests CLI de `sort`/`clean` avec plusieurs dossiers sources."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from file_janitor.cli import app
from file_janitor.models import FileCategory
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.scanner.scan import scan_directory

runner = CliRunner()


def test_destination_root_overrides_scan_root(tmp_path: Path) -> None:
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "report.pdf").write_text("x")
    dropbox = tmp_path / "Dropbox"

    scan = scan_directory(source, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, PlanConfig(destination_root=dropbox))

    items = plan.items(FileCategory.TO_SORT)
    assert len(items) == 1
    # Le fichier vient de Downloads/ mais doit être classé sous Dropbox/, pas Downloads/.
    assert items[0].destination == dropbox / "Documents" / "report.pdf"


def test_destination_root_none_uses_scan_root(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x")

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan, PlanConfig())  # destination_root=None par défaut

    items = plan.items(FileCategory.TO_SORT)
    assert items[0].destination == tmp_path / "Texte" / "notes.txt"


def test_cli_sort_multiple_folders_combines_preview(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    downloads.mkdir()
    documents.mkdir()
    (downloads / "photo.jpg").write_text("x")
    (documents / "invoice.pdf").write_text("x")

    result = runner.invoke(
        app,
        ["sort", str(downloads), str(documents), "--group-by", "extension", "--no-content-check"],
    )

    assert result.exit_code == 0
    # Le tableau Rich peut tronquer les chemins longs sur une largeur de
    # terminal réduite (CliRunner) : on vérifie le compte plutôt que le
    # texte exact des chemins.
    assert "Total : 2 fichiers" in result.stdout
    assert "--dry-run" in result.stdout


def test_cli_sort_multiple_folders_with_to_shared_destination(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    documents = tmp_path / "Documents"
    dropbox = tmp_path / "Dropbox"
    downloads.mkdir()
    documents.mkdir()
    (downloads / "photo.jpg").write_text("x")
    (documents / "invoice.pdf").write_text("x")

    result = runner.invoke(
        app,
        [
            "sort",
            str(downloads),
            str(documents),
            "--to",
            str(dropbox),
            "--group-by",
            "extension",
            "--no-content-check",
            "--apply",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert (dropbox / "jpg" / "photo.jpg").exists()
    assert (dropbox / "pdf" / "invoice.pdf").exists()
    assert not (downloads / "photo.jpg").exists()
    assert not (documents / "invoice.pdf").exists()


def test_cli_clean_multiple_folders_combines_preview(tmp_path: Path) -> None:
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir()
    folder_b.mkdir()
    (folder_a / "x.txt").write_text("dup")
    (folder_a / "y.txt").write_text("dup")  # doublon détecté dans folder_a

    result = runner.invoke(app, ["clean", str(folder_a), str(folder_b)])

    assert result.exit_code == 0
    assert "Doublon de" in result.stdout
