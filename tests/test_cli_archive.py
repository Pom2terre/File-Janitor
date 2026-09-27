from datetime import datetime
import os
from pathlib import Path

from typer.testing import CliRunner

from file_janitor.cli import app


runner = CliRunner()


def test_archive_command_defaults_to_read_only_preview(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    old_file = source / "old.txt"
    old_file.write_text("old")
    old_time = datetime(2020, 1, 1).timestamp()
    os.utime(old_file, (old_time, old_time))
    destination = tmp_path / "archive"
    destination.mkdir()

    result = runner.invoke(
        app,
        [
            "archive", str(source), "--to", str(destination),
            "--older-than-days", "365",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Prévisualisation de l’archivage" in result.output
    assert "old.txt" in result.output
    assert old_file.exists()
    assert not (destination / "old.txt").exists()


def test_archive_command_reports_overlapping_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    result = runner.invoke(
        app,
        [
            "archive", str(source), "--to", str(source / "archive"),
            "--older-than-days", "30",
        ],
    )
    assert result.exit_code == 2
    assert "doit être distinct" in result.output
