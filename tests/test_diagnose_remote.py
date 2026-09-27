"""Le diagnostic reste utilisable si un listing distant voit le fichier en retard."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import diagnose_remote
from file_janitor.application import ClassificationMode


def test_mount_temp_cleanup_retries_transient_rmtree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "remote-temp"
    folder.mkdir()
    original_rmtree = diagnose_remote.shutil.rmtree
    attempted_paths = []

    def flaky_rmtree(path: Path) -> None:
        attempted_paths.append(path)
        if len(attempted_paths) <= 7:
            raise OSError("remote mount still publishing a partial file")
        original_rmtree(path)

    monkeypatch.setattr(diagnose_remote.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(diagnose_remote, "sleep", lambda seconds: None)

    assert diagnose_remote.cleanup_mount_temp(folder)
    assert len(attempted_paths) == 8
    assert not folder.exists()


@pytest.mark.parametrize("date_counts", [(0,), (0, 1)])
def test_diagnostic_continues_when_metadata_listing_is_missing_or_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, date_counts: tuple[int, ...],
) -> None:
    observed = iter(date_counts)
    moves = []
    monkeypatch.setattr(
        diagnose_remote, "remote_folder_info",
        lambda folder: SimpleNamespace(filesystem_type="fuse.rclone"),
    )
    monkeypatch.setattr(diagnose_remote, "sleep", lambda seconds: None)

    def fake_analysis(folder: Path, mode: ClassificationMode, *, metadata: bool):
        count = next(observed) if metadata else 1
        return 0.1, SimpleNamespace(
            summary=SimpleNamespace(total_count=count), scan_errors=(),
        )

    monkeypatch.setattr(diagnose_remote, "timed_analysis", fake_analysis)
    monkeypatch.setattr(
        diagnose_remote, "measure_move",
        lambda *args, **kwargs: moves.append(kwargs) or (0.1, 0.2, True),
    )
    monkeypatch.setattr(sys, "argv", [
        "diagnose_remote", str(tmp_path), "--size-mib", "1",
        "--visibility-seconds", "0" if len(date_counts) == 1 else "1",
    ])

    diagnose_remote.main()

    output = capsys.readouterr().out
    assert f"{date_counts[-1]} fichier(s) vu(s) après {len(date_counts)} essai(s)" in output
    assert "Mode sûr :" in output
    assert len(moves) == 1
