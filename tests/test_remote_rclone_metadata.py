from __future__ import annotations

import json
from concurrent.futures import CancelledError
from datetime import datetime
from pathlib import Path

import pytest

import file_janitor.application.remote as remote_module
from file_janitor.application.remote import (
    RemoteFolderInfo,
    _rclone_default_excludes,
    _rclone_remote_spec,
    _run_rclone_lsjson,
    _iter_json_array,
    _InvalidRcloneListing,
    scan_rclone_metadata,
)


def test_rclone_remote_spec_maps_subfolder_under_mount() -> None:
    info = RemoteFolderInfo(
        True,
        "fuse.rclone",
        Path("/home/test-user/pcloud"),
        "pcloud:",
    )
    assert (
        _rclone_remote_spec(
            Path("/home/test-user/pcloud/Documents/Example"),
            info,
        )
        == "pcloud:Documents/Example"
    )


def test_rclone_remote_spec_preserves_mounted_remote_subpath() -> None:
    info = RemoteFolderInfo(
        True,
        "fuse.rclone",
        Path("/mnt/cloud"),
        "remote:base/path",
    )
    assert _rclone_remote_spec(Path("/mnt/cloud/photos"), info) == "remote:base/path/photos"


def test_scan_rclone_metadata_builds_records_without_per_file_stat(
    monkeypatch, tmp_path: Path
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:root")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda spec, cancel_callback=None, **kwargs: [
            {
                "Path": "photos/a.JPG",
                "Size": 123,
                "ModTime": "2026-08-17T15:45:30Z",
                "IsDir": False,
            },
            {
                "Path": ".venv/cache.bin",
                "Size": 999,
                "ModTime": "2026-08-17T15:45:30Z",
                "IsDir": False,
            },
            {
                "Path": "project/.idea/workspace.xml",
                "Size": 456,
                "ModTime": "2026-08-17T15:45:30Z",
                "IsDir": False,
            },
        ],
    )

    result = scan_rclone_metadata(tmp_path, use_ignore_file=False)

    assert result is not None
    assert result.metadata_complete is True
    assert result.identities_complete is False
    assert len(result.files) == 1
    record = result.files[0]
    assert record.path == tmp_path / "photos" / "a.JPG"
    assert record.size == 123
    assert record.extension == ".jpg"
    assert record.identity is None
    assert isinstance(record.mtime, datetime)


def test_scan_rclone_metadata_reports_remote_listing_stage(
    monkeypatch, tmp_path: Path
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda spec, cancel_callback=None, **kwargs: [],
    )
    stages = []

    result = scan_rclone_metadata(
        tmp_path,
        use_ignore_file=False,
        progress_callback=stages.append,
    )

    assert stages == ["remote_listing"]
    # Une réponse vide n'est pas concluante : le service doit alors essayer
    # le parcours du montage, qui peut déjà voir des objets récents.
    assert result is None


def test_scan_rclone_metadata_keeps_valid_empty_result_when_objects_are_excluded(
    monkeypatch, tmp_path: Path,
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda *args, **kwargs: [
            {"Path": ".git/config", "Size": 12, "ModTime": "2026-08-17T15:45:30Z"}
        ],
    )

    result = scan_rclone_metadata(tmp_path, use_ignore_file=False)

    assert result is not None
    assert result.total_count == 0


def test_scan_rclone_metadata_falls_back_if_modtime_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    monkeypatch.setattr(
        remote_module,
        "_run_rclone_lsjson",
        lambda spec, cancel_callback=None, **kwargs: [
            {"Path": "a.txt", "Size": 1, "IsDir": False},
        ],
    )

    assert scan_rclone_metadata(tmp_path, use_ignore_file=False) is None


def test_rclone_default_excludes_cover_nested_technical_directories(tmp_path: Path) -> None:
    patterns = _rclone_default_excludes(
        tmp_path,
        extra_patterns=None,
        use_default_excludes=True,
        use_ignore_file=False,
    )

    assert ".idea/**" in patterns
    assert ".vscode/**" in patterns
    assert ".vs/**" in patterns
    assert ".venv/**" in patterns
    assert "*.egg-info/**" in patterns


def test_rclone_default_excludes_are_disabled_when_defaults_are_disabled(
    tmp_path: Path,
) -> None:
    assert _rclone_default_excludes(
        tmp_path,
        extra_patterns=None,
        use_default_excludes=False,
        use_ignore_file=False,
    ) == ()


def test_rclone_default_excludes_are_disabled_when_user_can_reinclude(
    tmp_path: Path,
) -> None:
    (tmp_path / ".janitorignore").write_text("!.idea/keep.xml\n")

    assert _rclone_default_excludes(
        tmp_path,
        extra_patterns=None,
        use_default_excludes=True,
        use_ignore_file=True,
    ) == ()


def test_run_rclone_lsjson_pushes_excludes_into_command(monkeypatch) -> None:
    commands = []

    class FakeProcess:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        remote_module.shutil,
        "which",
        lambda executable: "/usr/bin/rclone",
    )

    def fake_popen(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(b"[]")
        return FakeProcess()

    monkeypatch.setattr(remote_module.subprocess, "Popen", fake_popen)

    assert list(_run_rclone_lsjson(
        "pcloud:backup",
        rclone_excludes=(".idea/**", ".vscode/**"),
    )) == []

    command = commands[0]
    assert command.count("--exclude") == 2
    assert ".idea/**" in command
    assert ".vscode/**" in command


def test_streamed_rclone_json_handles_chunk_boundaries_and_rejects_partial_data() -> None:
    class SmallChunks:
        def __init__(self, text: str) -> None:
            self.text = text
            self.position = 0

        def read(self, size: int) -> str:
            chunk = self.text[self.position:self.position + 3]
            self.position += len(chunk)
            return chunk

    listing = '[ {"Path": "dossier/vidéo.mp4", "Size": 3}, null, {"Path":"b"} ]'
    assert [item["Path"] for item in _iter_json_array(SmallChunks(listing), None)] == [
        "dossier/vidéo.mp4", "b",
    ]
    with pytest.raises(_InvalidRcloneListing):
        list(_iter_json_array(SmallChunks('[{"Path":"a"},]'), None))


def test_streamed_rclone_listing_reports_counts_and_rejects_truncated_json(
    monkeypatch, tmp_path: Path,
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    monkeypatch.setattr(remote_module.shutil, "which", lambda name: "/usr/bin/rclone")
    payload = json.dumps([
        {"Path": f"sub/{index}.jpg", "Size": 1, "ModTime": "2026-08-17T15:45:30Z"}
        for index in range(300)
    ]).encode()

    def fake_process(command, **kwargs):
        kwargs["stdout"].write(payload)

        class Process:
            returncode = 0

            def wait(self, timeout=None):
                return 0

        return Process()

    monkeypatch.setattr(remote_module.subprocess, "Popen", fake_process)
    stages = []
    result = scan_rclone_metadata(
        tmp_path, use_ignore_file=False, progress_callback=stages.append,
    )
    assert len(result.files) == 300
    assert stages == ["remote_listing", ("remote_listing", 250)]

    payload = b'[{"Path": "a.jpg", "Size": 1, "ModTime": "2026-08-17T15:45:30Z"},'
    assert scan_rclone_metadata(tmp_path, use_ignore_file=False) is None


def test_rclone_listing_cancellation_stops_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(remote_module.shutil, "which", lambda name: "/usr/bin/rclone")
    cancelled = []

    class FakeProcess:
        returncode = -15

        def terminate(self):
            cancelled.append("terminate")

        def wait(self, timeout=None):
            cancelled.append("wait")
            return self.returncode

    monkeypatch.setattr(
        remote_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess(),
    )
    with pytest.raises(CancelledError):
        _run_rclone_lsjson("pcloud:backup", cancel_callback=lambda: True)
    assert cancelled == ["terminate", "wait"]


def test_scan_rclone_metadata_forwards_default_excludes_before_listing(
    monkeypatch, tmp_path: Path
) -> None:
    info = RemoteFolderInfo(True, "fuse.rclone", tmp_path, "pcloud:")
    monkeypatch.setattr(remote_module, "remote_folder_info", lambda folder: info)
    captured = {}

    def fake_lsjson(spec, cancel_callback=None, *, rclone_excludes=()):
        captured["excludes"] = rclone_excludes
        return []

    monkeypatch.setattr(remote_module, "_run_rclone_lsjson", fake_lsjson)

    result = scan_rclone_metadata(tmp_path, use_ignore_file=False)

    assert result is None
    assert ".idea/**" in captured["excludes"]
    assert ".vscode/**" in captured["excludes"]
