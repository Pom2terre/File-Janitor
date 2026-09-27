"""Contrat Preview -> Execute pour les destinations validées par l'utilisateur."""

from __future__ import annotations

from pathlib import Path

from file_janitor.application import (
    AnalysisResult,
    AnalysisSummary,
    DuplicateGroup,
    DuplicateGroupMember,
    execute_actions,
    execute_selected_actions,
)
from file_janitor.application import service
from file_janitor.executor import execute_items
from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
)
from file_janitor.storage.history import BatchStatus, HistoryStore


def _result(tmp_path: Path, *actions: tuple[str, int, ActionItem]) -> AnalysisResult:
    return AnalysisResult(
        summary=AnalysisSummary(
            root=tmp_path,
            total_count=len(actions),
            total_size=sum(action.size for _category, _index, action in actions),
            categories=(),
        ),
        details=(),
        _actions=actions,
    )


def test_selected_move_uses_exact_preview_destination_without_mutating_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "report.pdf",
        size=1,
        reason="preview",
        destination=tmp_path / "pdf" / "report.pdf",
        action=ActionKind.MOVE,
    )
    result = _result(tmp_path, ("to_sort", 0, item))
    captured: list[ActionItem] = []

    def fake_execute(items, *, root, **kwargs):
        captured.extend(items)
        return service.ExecuteResult(batch_id=1, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execute_selected_actions(result, {("to_sort", 0)})

    assert len(captured) == 1
    assert captured[0].destination == item.destination
    assert captured[0].conflict_policy is ConflictPolicy.SKIP
    assert item.conflict_policy is ConflictPolicy.RENAME


def test_selected_copy_uses_exact_preview_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "photo.jpg",
        size=1,
        reason="preview",
        destination=tmp_path / "jpg" / "photo.jpg",
        action=ActionKind.COPY,
    )
    result = _result(tmp_path, ("to_sort", 0, item))
    captured: list[ActionItem] = []

    def fake_execute(items, *, root, **kwargs):
        captured.extend(items)
        return service.ExecuteResult(batch_id=2, success=1, errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execute_selected_actions(result, {("to_sort", 0)})

    assert captured[0].destination == item.destination
    assert captured[0].conflict_policy is ConflictPolicy.SKIP


def test_duplicate_keeper_sort_is_also_bound_to_preview_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    keeper_sort = ActionItem(
        category=FileCategory.TO_SORT,
        path=keeper,
        size=4,
        reason="Classer le fichier conservé",
        destination=tmp_path / "txt" / "keeper.txt",
        action=ActionKind.MOVE,
    )
    duplicate_action = ActionItem(
        category=FileCategory.DUPLICATE,
        path=duplicate,
        size=4,
        reason="Doublon",
        action=ActionKind.TRASH,
    )
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=tmp_path,
            total_count=2,
            total_size=8,
            categories=(),
        ),
        details=(),
        duplicate_groups=(
            DuplicateGroup(
                key="samehash",
                members=(
                    DuplicateGroupMember(path=keeper, size=4),
                    DuplicateGroupMember(path=duplicate, size=4),
                ),
            ),
        ),
        _actions=(
            ("to_sort", 0, keeper_sort),
            ("duplicate", 0, duplicate_action),
        ),
    )
    captured: list[ActionItem] = []

    def fake_execute(items, *, root, **kwargs):
        captured.extend(items)
        return service.ExecuteResult(batch_id=3, success=len(items), errors=())

    monkeypatch.setattr(service, "execute_actions", fake_execute)

    execute_selected_actions(
        result,
        {("duplicate", 0)},
        duplicate_keepers={"samehash": keeper},
    )

    assert [(item.action, item.conflict_policy) for item in captured] == [
        (ActionKind.MOVE, ConflictPolicy.SKIP),
        (ActionKind.TRASH, ConflictPolicy.RENAME),
    ]
    assert captured[0].destination == keeper_sort.destination


def test_generic_execute_actions_keeps_rename_policy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=tmp_path / "source.txt",
        size=1,
        reason="generic",
        destination=tmp_path / "sorted" / "source.txt",
        action=ActionKind.MOVE,
    )
    captured: list[ActionItem] = []

    def fake_execute_items(
        items,
        *,
        root,
        store,
        progress_callback=None,
        cancel_callback=None,
    ):
        captured.extend(items)
        return 1, 1, []

    monkeypatch.setattr(service, "execute_items", fake_execute_items)
    monkeypatch.setattr(
        service.HistoryStore,
        "get_batch",
        lambda self, batch_id: None,
    )

    execution = execute_actions(
        [item],
        root=str(tmp_path),
        db_path=tmp_path / "history.db",
    )

    assert execution.success == 1
    assert captured[0] is item
    assert captured[0].conflict_policy is ConflictPolicy.RENAME


def test_skip_collision_fails_only_conflicting_action_in_batch(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")

    destination_dir = tmp_path / "sorted"
    destination_dir.mkdir()
    occupied = destination_dir / "first.txt"
    occupied.write_text("external")
    second_destination = destination_dir / "second.txt"

    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=first,
            size=first.stat().st_size,
            reason="preview",
            destination=occupied,
            action=ActionKind.MOVE,
            conflict_policy=ConflictPolicy.SKIP,
        ),
        ActionItem(
            category=FileCategory.TO_SORT,
            path=second,
            size=second.stat().st_size,
            reason="preview",
            destination=second_destination,
            action=ActionKind.MOVE,
            conflict_policy=ConflictPolicy.SKIP,
        ),
    ]

    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        batch_id, success, errors = execute_items(
            items,
            root=str(tmp_path),
            store=store,
        )
        batch = store.get_batch(batch_id)
    finally:
        store.close()

    assert success == 1
    assert len(errors) == 1
    assert "destination déjà existante" in errors[0]
    assert first.read_text() == "first"
    assert occupied.read_text() == "external"
    assert not (destination_dir / "first (1).txt").exists()
    assert not second.exists()
    assert second_destination.read_text() == "second"
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
