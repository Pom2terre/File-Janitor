from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.application import remote as remote_module
from file_janitor.application import service as service_module
from file_janitor.application.remote import RemoteFolderInfo
from file_janitor.application.service import analyze_empty_directories
from file_janitor.scanner.empty_directories import EmptyDirectoryScan


def test_rclone_listing_prevents_nonempty_fuse_directories_being_proposed(
    tmp_path: Path, monkeypatch,
) -> None:
    mount = tmp_path / "mount"
    root = mount / "DAIKIN"
    (root / "CIF" / "Active").mkdir(parents=True)
    (root / "Documentation").mkdir()
    (root / "Empty").mkdir()
    info = RemoteFolderInfo(True, "fuse.rclone", mount, "drive:")
    entries = iter((
        {"Path": "CIF", "IsDir": True},
        {"Path": "CIF/Active", "IsDir": True},
        {"Path": "CIF/Active/model.xlsx", "IsDir": False},
        {"Path": "Documentation", "IsDir": True},
        {"Path": "Documentation/notes.pdf", "IsDir": False},
        {"Path": "Empty", "IsDir": True},
    ))
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda _path: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda *_args, **_kwargs: entries,
    )

    result = remote_module.scan_rclone_empty_directories(root)

    assert result is not None
    assert [record.path.relative_to(root).as_posix() for record in result.directories] == [
        "Empty",
    ]


def test_rclone_listing_failure_returns_no_deletion_candidates(
    tmp_path: Path, monkeypatch,
) -> None:
    mount = tmp_path / "mount"
    root = mount / "DAIKIN"
    root.mkdir(parents=True)
    info = RemoteFolderInfo(True, "fuse.rclone", mount, "drive:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda _path: info)
    monkeypatch.setattr(remote_module, "_run_rclone_lsjson", lambda *_a, **_kw: None)

    result = remote_module.scan_rclone_empty_directories(root)

    assert result is not None
    assert result.directories == ()
    assert any("aucun dossier proposé" in error for error in result.errors)


def test_mount_content_mismatch_vetoes_rclone_empty_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    mount = tmp_path / "mount"
    root = mount / "DAIKIN"
    suspect = root / "CIF"
    suspect.mkdir(parents=True)
    (suspect / "model.xlsx").write_text("keep")
    info = RemoteFolderInfo(True, "fuse.rclone", mount, "drive:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda _path: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda *_args, **_kwargs: iter(({"Path": "CIF", "IsDir": True},)),
    )

    result = remote_module.scan_rclone_empty_directories(root)

    assert result is not None
    assert result.directories == ()
    assert any("diffère du listing rclone" in error for error in result.errors)


def test_remote_empty_directory_analysis_fails_closed_without_backend_scanner(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "remote"
    root.mkdir()
    info = RemoteFolderInfo(True, "fuse.smb", root, "//server/share")
    monkeypatch.setattr(service_module, "remote_folder_info", lambda _path: info)
    monkeypatch.setattr(
        service_module,
        "scan_rclone_empty_directories",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        service_module,
        "scan_empty_directories",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("un scan FUSE non vérifié ne doit pas être utilisé")
        ),
    )

    result = analyze_empty_directories(root)

    assert result.summary.total_count == 0
    assert result.scan_errors
    assert "aucune suppression proposée" in result.scan_errors[0]


@pytest.mark.parametrize(
    ("filesystem_type", "source"),
    (("fuse", "pCloud.fs"), ("fuse.pcloud", "pcloud")),
)
def test_native_pcloud_scan_finds_empty_directory_trees(
    tmp_path: Path, monkeypatch, filesystem_type: str, source: str,
) -> None:
    root = tmp_path / "pcloud" / "file-janitor-diagnostic"
    (root / "empty-parent" / "empty-child").mkdir(parents=True)
    nonempty = root / "has-content"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep")
    info = RemoteFolderInfo(True, filesystem_type, tmp_path / "pcloud", source)
    monkeypatch.setattr(service_module, "remote_folder_info", lambda _path: info)
    # Le client pCloud natif n'a pas de cible rclone; l'application doit alors
    # utiliser le parcours FUSE identifié, sans activer ce repli pour SMB/NFS.
    monkeypatch.setattr(
        service_module, "scan_rclone_empty_directories", lambda *_a, **_kw: None
    )

    result = analyze_empty_directories(root)
    details = result.details_for("empty_directory")

    assert details is not None
    assert [item.path.relative_to(root).as_posix() for item in details.items] == [
        "empty-parent/empty-child",
        "empty-parent",
    ]
    assert not result.scan_errors
