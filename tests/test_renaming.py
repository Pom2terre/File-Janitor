from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from file_janitor.application import (
    analyze_rename_folder,
    execute_actions,
    rename_actions,
    undo_execution,
)
from file_janitor.models import FileIdentity, FileRecord, ScanResult
from file_janitor.renaming import (
    RenameTemplate,
    RenameTemplateError,
    build_rename_actions,
)


def _record(path: Path, *, size: int = 4, day: int = 3) -> FileRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"data")
    return FileRecord(
        path=path,
        size=size,
        mtime=datetime(2024, 5, day, 12, 0),
        extension=path.suffix.lower(),
        identity=FileIdentity(
            device=1,
            inode=hash(path),
            size=size,
            mtime_ns=1,
        ),
    )


def test_template_renders_supported_fields_and_counter_format(tmp_path: Path) -> None:
    path = tmp_path / "Album" / "vacances.jpg"
    template = RenameTemplate.compile("{date}_{parent}_{counter:03d}_{name}{ext}")

    result = template.render(path, date="2024-05-03", counter=7)

    assert result == "2024-05-03_Album_007_vacances.jpg"


@pytest.mark.parametrize(
    "pattern",
    ["", "{unknown}.jpg", "{name}/{counter}{ext}", "{name!r}{ext}", "{counter:{name}}"],
)
def test_invalid_templates_are_rejected(pattern: str) -> None:
    with pytest.raises(RenameTemplateError):
        RenameTemplate.compile(pattern)


def test_rename_plan_is_deterministic_recursive_and_marks_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "photos"
    first = _record(root / "b.jpg", day=3)
    second = _record(root / "a.jpg", day=3)
    nested = _record(root / "set" / "c.jpg", day=3)
    scan = ScanResult(root=root, files=[first, second, nested])

    actions = build_rename_actions(
        scan,
        "{date}{ext}",
        recursive=True,
    )

    assert [item.path for item in actions] == [second.path, first.path, nested.path]
    assert [item.conflict for item in actions] == [True, True, False]
    assert [item.action.value for item in actions] == ["none", "none", "move"]
    assert all(
        item.conflict_reason == "plusieurs fichiers produisent ce même nom"
        for item in actions[:2]
    )
    assert not (root / "2024-05-03.jpg").exists()


def test_rename_plan_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    source = _record(root / "source.txt")
    _record(root / "target.txt")
    scan = ScanResult(root=root, files=[source])

    action = build_rename_actions(scan, "target{ext}")[0]

    assert action.conflict
    assert action.action.value == "none"
    assert "existe déjà" in (action.conflict_reason or "")


def test_analyze_rename_folder_execute_and_undo(tmp_path: Path) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"image")
    db_path = tmp_path / "history.sqlite"

    result = analyze_rename_folder(root, "{name}_done{ext}")
    actions = rename_actions(result)

    assert len(actions) == 1
    assert result.details_for("rename") is not None
    assert result.summary.total_count == 1
    assert not (root / "photo_done.jpg").exists()

    execution = execute_actions(actions, root=str(root), db_path=db_path)
    assert execution.success == 1
    assert (root / "photo_done.jpg").read_bytes() == b"image"

    undone = undo_execution(execution.batch_id, db_path=db_path)
    assert undone.success == 1
    assert (root / "photo.jpg").read_bytes() == b"image"
    assert not (root / "photo_done.jpg").exists()


def test_destination_created_after_preview_is_never_overwritten(
    tmp_path: Path,
) -> None:
    root = tmp_path / "photos"
    root.mkdir()
    source = root / "photo.jpg"
    source.write_bytes(b"original")
    db_path = tmp_path / "history.sqlite"
    actions = rename_actions(
        analyze_rename_folder(root, "{name}_new{ext}")
    )
    destination = root / "photo_new.jpg"
    destination.write_bytes(b"concurrent file")

    execution = execute_actions(actions, root=str(root), db_path=db_path)

    assert execution.success == 0
    assert execution.errors
    assert source.read_bytes() == b"original"
    assert destination.read_bytes() == b"concurrent file"
