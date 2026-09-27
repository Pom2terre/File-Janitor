"""Tests de la détection générique des filesystems distants."""

from pathlib import Path

from file_janitor.application.remote import (
    _filesystem_type_for_path,
    _mount_entries,
    is_remote_filesystem_type,
    is_remote_folder,
)


def test_mount_entries_decode_mount_points_with_spaces() -> None:
    text = (
        "36 25 0:32 / /home/test-user/Google\\040Drive rw - "
        "fuse.rclone gdrive: rw\n"
    )

    assert _mount_entries(text) == [
        (Path("/home/test-user/Google Drive"), "fuse.rclone"),
    ]


def test_filesystem_type_uses_most_specific_mount() -> None:
    text = "\n".join(
        (
            "1 0 8:1 / / rw - ext4 /dev/sda1 rw",
            "2 1 0:50 / /home/test-user/OneDrive rw - fuse.rclone onedrive: rw",
        )
    )

    assert (
        _filesystem_type_for_path(
            Path("/home/test-user/OneDrive/Pictures"),
            text,
        )
        == "fuse.rclone"
    )
    assert _filesystem_type_for_path(Path("/tmp/local"), text) == "ext4"


def test_remote_filesystem_types_are_provider_agnostic() -> None:
    assert is_remote_filesystem_type("fuse.rclone") is True
    assert is_remote_filesystem_type("nfs4") is True
    assert is_remote_filesystem_type("cifs") is True
    assert is_remote_filesystem_type("ext4") is False
    assert is_remote_filesystem_type("btrfs") is False


def test_is_remote_folder_falls_back_to_local_when_mountinfo_unavailable(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-mountinfo"
    assert is_remote_folder(tmp_path, mountinfo_path=missing) is False


def test_is_remote_folder_detects_rclone_mount(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "\n".join(
            (
                "1 0 8:1 / / rw - ext4 /dev/sda1 rw",
                "2 1 0:50 / /home/test-user/pcloud rw - fuse.rclone pcloud: rw",
            )
        ),
        encoding="utf-8",
    )

    assert (
        is_remote_folder(
            Path("/home/test-user/pcloud/My Pictures"),
            mountinfo_path=mountinfo,
        )
        is True
    )


def test_native_pcloud_fuse_source_selects_remote_profile(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n"
        "2 1 0:50 / /home/test-user/pcloud rw - fuse pCloud.fs rw\n",
        encoding="utf-8",
    )
    assert is_remote_folder(
        Path("/home/test-user/pcloud/My Videos/MDV"),
        mountinfo_path=mountinfo,
    )
