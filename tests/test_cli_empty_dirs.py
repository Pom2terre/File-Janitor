from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from file_janitor import cli as cli_module
from file_janitor.cli import app
from file_janitor.application import execute_actions, undo_execution


runner = CliRunner()


def test_empty_dirs_cli_defaults_to_preview(tmp_path: Path) -> None:
    root = tmp_path / "source"
    empty = root / "empty" / "nested"
    empty.mkdir(parents=True)

    result = runner.invoke(app, ["empty-dirs", str(root)])

    assert result.exit_code == 0, result.output
    assert "Prévisualisation des dossiers vides" in result.output
    assert "empty/nested" in result.output
    assert "Mode --dry-run" in result.output
    assert empty.is_dir()


def test_empty_dirs_cli_apply_is_undoable(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "source"
    empty = root / "empty" / "nested"
    empty.mkdir(parents=True)
    db_path = tmp_path / "history.sqlite3"
    real_execute = execute_actions

    def execute_with_test_database(actions, *, root: str):
        return real_execute(actions, root=root, db_path=db_path)

    monkeypatch.setattr(cli_module, "execute_actions", execute_with_test_database)

    result = runner.invoke(app, ["empty-dirs", str(root), "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    assert "2 dossier(s) vide(s) supprimé(s)" in result.output
    assert not (root / "empty").exists()
    batch_id = int(result.output.split("batch #", 1)[1].split(")", 1)[0])

    undone = undo_execution(batch_id, db_path=db_path)
    assert undone.success == 2
    assert (root / "empty" / "nested").is_dir()
