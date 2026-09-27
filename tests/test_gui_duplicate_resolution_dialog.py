"""Tests du dialogue 4E-10D-r2 de résolution des doublons."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QDialogButtonBox, QRadioButton

from file_janitor.application import DuplicateGroup, DuplicateGroupMember
from file_janitor.gui.app import create_application
from file_janitor.gui.duplicate_resolution_dialog import DuplicateResolutionDialog


def _group(tmp_path: Path, key: str, names: tuple[str, ...]) -> DuplicateGroup:
    return DuplicateGroup(
        key=key,
        members=tuple(
            DuplicateGroupMember(
                path=tmp_path / name,
                size=1024 + index,
                mtime=datetime(2026, 9, index + 1, 12, 0),
            )
            for index, name in enumerate(names)
        ),
    )


def _radio(dialog: DuplicateResolutionDialog, row: int) -> QRadioButton:
    cell = dialog.table.cellWidget(row, 0)
    assert cell is not None
    radio = cell.findChild(QRadioButton)
    assert radio is not None
    return radio


def test_dialog_requires_one_keeper_per_group(tmp_path: Path) -> None:
    create_application(["janitor-gui-duplicate-dialog-required-test"])
    groups = (
        _group(tmp_path, "hash-a", ("a1.txt", "a2.txt")),
        _group(tmp_path, "hash-b", ("b1.pdf", "b2.pdf", "b3.pdf")),
    )
    dialog = DuplicateResolutionDialog(groups)

    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled() is False

    _radio(dialog, 1).click()
    assert dialog.keepers() == {"hash-a": groups[0].members[1].path}
    assert ok.isEnabled() is False

    dialog.group_combo.setCurrentIndex(1)
    _radio(dialog, 2).click()
    assert dialog.keepers() == {
        "hash-a": groups[0].members[1].path,
        "hash-b": groups[1].members[2].path,
    }
    assert ok.isEnabled() is True
    dialog.close()


def test_dialog_preserves_exclusive_keeper_when_revisiting_group(tmp_path: Path) -> None:
    create_application(["janitor-gui-duplicate-dialog-exclusive-test"])
    group = _group(tmp_path, "hash-a", ("a.txt", "b.txt", "c.txt"))
    dialog = DuplicateResolutionDialog(
        (group,),
        keepers={"hash-a": group.members[0].path},
    )

    assert _radio(dialog, 0).isChecked() is True
    _radio(dialog, 2).click()

    assert _radio(dialog, 0).isChecked() is False
    assert _radio(dialog, 2).isChecked() is True
    assert dialog.keepers() == {"hash-a": group.members[2].path}
    dialog.close()
