from __future__ import annotations

from pathlib import Path

from file_janitor.application import (
    analyze_empty_directories,
    empty_directory_actions,
    execute_selected_actions,
    get_latest_undoable_batch_id,
    undo_execution,
)
from file_janitor import executor as executor_module


def test_scan_finds_empty_directory_trees_bottom_up_and_respects_excludes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    (root / "empty-tree" / "nested" / "leaf").mkdir(parents=True)
    nonempty = root / "not-empty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep")
    ignored = root / "cache"
    ignored.mkdir()
    (root / ".janitorignore").write_text("cache/\n")

    result = analyze_empty_directories(root)
    paths = [item.path.relative_to(root).as_posix() for item in empty_directory_actions(result)]

    assert paths == [
        "empty-tree/nested/leaf",
        "empty-tree/nested",
        "empty-tree",
    ]
    details = result.details_for("empty_directory")
    assert details is not None
    assert details.items[0].reason == "Dossier vide"
    assert "sous-dossiers vides" in details.items[-1].reason


def test_empty_directory_removal_is_undoable_and_restores_parent_first(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    result = analyze_empty_directories(root)
    actions = empty_directory_actions(result)
    db_path = tmp_path / "history.sqlite3"

    execution = execute_selected_actions(
        result,
        {("empty_directory", index) for index in range(len(actions))},
        db_path=db_path,
    )

    assert execution.success == 2
    assert not (root / "a").exists()
    assert get_latest_undoable_batch_id(db_path=db_path) == execution.batch_id

    undone = undo_execution(execution.batch_id, db_path=db_path)

    assert undone.success == 2
    assert (root / "a" / "b").is_dir()
    assert get_latest_undoable_batch_id(db_path=db_path) is None


def test_directory_that_becomes_nonempty_after_preview_is_kept(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    empty = root / "will-change"
    empty.mkdir(parents=True)
    result = analyze_empty_directories(root)
    (empty / "new-file.txt").write_text("keep")

    execution = execute_selected_actions(
        result,
        {("empty_directory", 0)},
        db_path=tmp_path / "history.sqlite3",
    )

    assert execution.success == 0
    assert execution.errors
    assert (empty / "new-file.txt").read_text() == "keep"


def test_rmdir_refuses_a_file_created_in_the_last_moment(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "source"
    empty = root / "racing"
    empty.mkdir(parents=True)
    result = analyze_empty_directories(root)
    real_rmdir = executor_module.os.rmdir

    def add_file_then_rmdir(path: Path) -> None:
        (Path(path) / "late.txt").write_text("keep")
        real_rmdir(path)

    monkeypatch.setattr(executor_module.os, "rmdir", add_file_then_rmdir)
    execution = execute_selected_actions(
        result,
        {("empty_directory", 0)},
        db_path=tmp_path / "history.sqlite3",
    )

    assert execution.success == 0
    assert execution.errors
    assert (empty / "late.txt").read_text() == "keep"


def test_parent_is_not_removed_when_only_some_child_directories_are_selected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    nested = root / "parent" / "child"
    nested.mkdir(parents=True)
    result = analyze_empty_directories(root)
    actions = empty_directory_actions(result)
    parent_index = next(i for i, item in enumerate(actions) if item.path.name == "parent")

    execution = execute_selected_actions(
        result,
        {("empty_directory", parent_index)},
        db_path=tmp_path / "history.sqlite3",
    )

    assert execution.success == 0
    assert (root / "parent" / "child").is_dir()
