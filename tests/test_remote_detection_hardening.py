"""Tests 4E-5A : durcissement de la détection des filesystems distants."""

from pathlib import Path

from file_janitor.application.remote import (
    _filesystem_type_for_path,
    _mount_entries,
    is_remote_filesystem_type,
    remote_folder_info,
)


def test_nested_remote_mount_overrides_local_parent() -> None:
    text = "\n".join(
        (
            "1 0 8:1 / / rw - ext4 /dev/root rw",
            "2 1 0:50 / /srv/cloud rw - fuse.rclone cloud: rw",
        )
    )
    assert _filesystem_type_for_path(Path("/srv/cloud/photos/a.jpg"), text) == "fuse.rclone"


def test_nested_local_mount_overrides_remote_parent() -> None:
    text = "\n".join(
        (
            "1 0 8:1 / / rw - ext4 /dev/root rw",
            "2 1 0:50 / /srv/cloud rw - fuse.rclone cloud: rw",
            "3 2 8:2 / /srv/cloud/cache rw - ext4 /dev/sdb1 rw",
        )
    )
    assert _filesystem_type_for_path(Path("/srv/cloud/cache/index.db"), text) == "ext4"


def test_later_overmount_wins_for_identical_mount_point() -> None:
    text = "\n".join(
        (
            "1 0 8:1 / / rw - ext4 /dev/root rw",
            "2 1 8:2 / /mnt/data rw - ext4 /dev/sdb1 rw",
            "3 1 0:50 / /mnt/data rw - fuse.rclone cloud: rw",
        )
    )
    assert _filesystem_type_for_path(Path("/mnt/data/photos"), text) == "fuse.rclone"


def test_later_local_overmount_can_hide_remote_mount() -> None:
    text = "\n".join(
        (
            "1 0 8:1 / / rw - ext4 /dev/root rw",
            "2 1 0:50 / /mnt/data rw - fuse.rclone cloud: rw",
            "3 1 8:2 / /mnt/data rw - ext4 /dev/sdb1 rw",
        )
    )
    assert _filesystem_type_for_path(Path("/mnt/data/photos"), text) == "ext4"


def test_mount_entries_decode_tab_and_backslash_escapes() -> None:
    text = (
        r"2 1 0:50 / /mnt/Tab\011Name rw - fuse.rclone cloud: rw" "\n"
        r"3 1 0:51 / /mnt/Slash\134Name rw - fuse.sshfs ssh: rw" "\n"
    )
    assert _mount_entries(text) == [
        (Path("/mnt/Tab\tName"), "fuse.rclone"),
        (Path(r"/mnt/Slash\Name"), "fuse.sshfs"),
    ]


def test_mount_entries_ignore_malformed_and_relative_mount_points() -> None:
    text = "\n".join(
        (
            "garbage",
            "1 0 8:1 / relative rw - ext4 /dev/root rw",
            "2 1 0:50 / /valid rw - nfs4 server:/share rw",
            "3 1 0:51 / /broken rw no-separator-fields",
        )
    )
    assert _mount_entries(text) == [(Path("/valid"), "nfs4")]


def test_additional_remote_filesystem_types_are_recognized() -> None:
    for fs_type in (
        "smbfs",
        "ceph",
        "fuse.ceph",
        "glusterfs",
        "fuse.glusterfs",
        "afs",
        "fuse.afs",
        "fuse.curlftpfs",
        "fuse.ftpfs",
        "fuse.onedrive",
        "fuse.google-drive-ocamlfuse",
        "fuse.gvfsd-fuse",
    ):
        assert is_remote_filesystem_type(fs_type) is True, fs_type


def test_remote_filesystem_type_normalizes_case_and_whitespace() -> None:
    assert is_remote_filesystem_type("  NFS4  ") is True
    assert is_remote_filesystem_type("  FUSE.RCLONE  ") is True
    assert is_remote_filesystem_type("  EXT4  ") is False


def test_remote_folder_info_uses_effective_nested_local_mount(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "\n".join(
            (
                "1 0 8:1 / / rw - ext4 /dev/root rw",
                "2 1 0:50 / /srv/cloud rw - fuse.rclone cloud: rw",
                "3 2 8:2 / /srv/cloud/local rw - ext4 /dev/sdb1 rw",
            )
        ),
        encoding="utf-8",
    )
    info = remote_folder_info(
        Path("/srv/cloud/local/missing-file"),
        mountinfo_path=mountinfo,
    )
    assert info.is_remote is False
    assert info.filesystem_type == "ext4"
    assert info.mount_point == Path("/srv/cloud/local")


def test_remote_folder_info_does_not_require_target_to_exist(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "2 1 0:50 / /cloud rw - fuse.rclone cloud: rw\n",
        encoding="utf-8",
    )
    target = Path("/cloud/path/that/does/not/exist")
    assert target.exists() is False
    info = remote_folder_info(target, mountinfo_path=mountinfo)
    assert info.is_remote is True
    assert info.filesystem_type == "fuse.rclone"
    assert info.mount_point == Path("/cloud")
    assert info.source == "cloud:"
