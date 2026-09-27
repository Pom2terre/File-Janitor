from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from file_janitor.cli import app


runner = CliRunner()


def test_rename_command_defaults_to_read_only_preview(tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"image")
    expected = (
        datetime.fromtimestamp((tmp_path / "photo.jpg").stat().st_mtime)
        .strftime("%Y-%m-%d")
        + "_001_photo.jpg"
    )

    result = runner.invoke(
        app,
        ["rename", str(tmp_path), "--pattern", "{date}_{counter:03d}_{name}{ext}"],
    )

    assert result.exit_code == 0, result.output
    assert "Prévisualisation des renommages" in result.output
    assert "photo.jpg" in result.output
    assert expected in result.output
    assert not (tmp_path / expected).exists()


def test_rename_command_reports_collisions_and_invalid_templates(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")

    result = runner.invoke(
        app,
        ["rename", str(tmp_path), "--pattern", "same.txt"],
    )
    invalid = runner.invoke(
        app,
        ["rename", str(tmp_path), "--pattern", "{unknown}"],
    )

    assert result.exit_code == 0
    assert "Collision" in result.output
    assert invalid.exit_code == 2
    assert "Champ {unknown} inconnu" in invalid.output
