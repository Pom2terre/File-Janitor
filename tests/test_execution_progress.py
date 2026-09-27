"""Tests 4E-10J : progression d'exécution des lots."""

from __future__ import annotations

from pathlib import Path

from file_janitor.executor import execute_items
from file_janitor.models import ActionItem, ActionKind, FileCategory
from file_janitor.storage.history import HistoryStore


def _move_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="progress test",
        destination=destination,
        action=ActionKind.MOVE,
    )


def test_execute_items_reports_progress_per_action(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one")
    second.write_text("two")
    store = HistoryStore(db_path=tmp_path / "history.db")
    progress = []

    _batch_id, success, errors = execute_items(
        [
            _move_item(first, tmp_path / "sorted" / first.name),
            _move_item(second, tmp_path / "sorted" / second.name),
        ],
        root=str(tmp_path),
        store=store,
        progress_callback=progress.append,
    )

    assert success == 2
    assert not errors
    assert progress == [
        ("execution", 1, 2, str(first)),
        ("execution_done", 1, 2, str(first)),
        ("execution", 2, 2, str(second)),
        ("execution_done", 2, 2, str(second)),
    ]
    store.close()


def test_failed_file_is_counted_after_its_failure_is_recorded(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    valid = tmp_path / "valid.txt"
    valid.write_text("valid")
    actions = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=missing,
            size=1,
            reason="progress test",
            destination=tmp_path / "sorted" / missing.name,
            action=ActionKind.MOVE,
        ),
        _move_item(valid, tmp_path / "sorted" / valid.name),
    ]
    store = HistoryStore(db_path=tmp_path / "history.db")
    progress = []
    _batch_id, success, errors = execute_items(
        actions, root=str(tmp_path), store=store,
        progress_callback=progress.append,
    )
    store.close()

    assert success == 1
    assert len(errors) == 1
    assert progress == [
        ("execution", 1, 2, str(missing)),
        ("execution_done", 1, 2, str(missing)),
        ("execution", 2, 2, str(valid)),
        ("execution_done", 2, 2, str(valid)),
    ]


def test_cancel_does_not_complete_an_unstarted_action(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one")
    second.write_text("two")
    store = HistoryStore(db_path=tmp_path / "history.db")
    progress = []
    _batch_id, success, errors = execute_items(
        [_move_item(first, tmp_path / "sorted" / first.name),
         _move_item(second, tmp_path / "sorted" / second.name)],
        root=str(tmp_path), store=store,
        progress_callback=progress.append,
        cancel_callback=lambda: len(progress) >= 2,
    )
    store.close()

    assert success == 1 and not errors
    assert progress == [
        ("execution", 1, 2, str(first)),
        ("execution_done", 1, 2, str(first)),
    ]
    assert second.is_file()


def test_preflight_rejection_counts_as_handled_without_start(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.txt"
    source.write_text("duplicate")
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="progress test",
        action=ActionKind.TRASH,
    )
    store = HistoryStore(db_path=tmp_path / "history.db")
    progress = []
    _batch_id, success, errors = execute_items(
        [item], root=str(tmp_path), store=store,
        dependent_trash_keepers={source: tmp_path / "missing-keeper.txt"},
        progress_callback=progress.append,
    )
    store.close()

    assert success == 0 and len(errors) == 1
    assert progress == [("execution_done", 1, 1, str(source))]
    assert source.is_file()
