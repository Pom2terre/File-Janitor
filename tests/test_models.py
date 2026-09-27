from pathlib import Path

from file_janitor.models import (
    ActionItem,
    ActionKind,
    ActionStatus,
    ConflictPolicy,
    FileCategory,
    FileIdentity,
)


def test_action_item_defaults_are_safe():
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=Path("/tmp/source/report.pdf"),
        size=1234,
        reason="Classement par extension",
        destination=Path("/tmp/destination/pdf/report.pdf"),
    )

    assert item.identity is None
    assert item.action is ActionKind.NONE
    assert item.conflict_policy is ConflictPolicy.RENAME
    assert item.status is ActionStatus.PLANNED
    assert item.conflict is False
    assert item.conflict_reason is None
    assert item.error is None


def test_action_item_source_is_alias_for_path():
    source = Path("/tmp/source/report.pdf")

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=1234,
        reason="Test",
    )

    assert item.source == source
    assert item.source is item.path


def test_action_item_can_describe_move():
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=Path("/tmp/a.txt"),
        size=10,
        reason="Classement",
        destination=Path("/tmp/txt/a.txt"),
        action=ActionKind.MOVE,
    )

    assert item.action is ActionKind.MOVE


def test_action_item_can_describe_conflict():
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=Path("/tmp/a.txt"),
        size=10,
        reason="Classement",
        destination=Path("/tmp/txt/a.txt"),
        action=ActionKind.MOVE,
        conflict=True,
        conflict_reason="La destination existe déjà",
    )

    assert item.conflict is True
    assert item.conflict_reason == "La destination existe déjà"


def test_action_item_can_carry_file_identity():
    identity = FileIdentity(
        device=8,
        inode=12345,
        size=1234,
        mtime_ns=987654321,
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=Path("/tmp/source/report.pdf"),
        size=1234,
        reason="Test identité",
        identity=identity,
    )

    assert item.identity is identity
    assert item.identity.device == 8
    assert item.identity.inode == 12345
    assert item.identity.size == 1234
    assert item.identity.mtime_ns == 987654321
