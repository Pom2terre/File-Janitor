"""Tests de l’annulation coopérative du scanner."""

from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from file_janitor.scanner.scan import _hash_files, scan_directory


def test_scan_directory_can_be_cancelled_before_io(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content")
    with pytest.raises(CancelledError):
        scan_directory(
            tmp_path,
            compute_hashes=False,
            compute_content_type=False,
            cancel_callback=lambda: True,
        )


def test_scan_directory_can_be_cancelled_during_metadata(tmp_path: Path) -> None:
    for index in range(20):
        (tmp_path / f"{index:02}.txt").write_text("x")

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 6

    with pytest.raises(CancelledError):
        scan_directory(
            tmp_path,
            compute_hashes=False,
            compute_content_type=False,
            cancel_callback=cancelled,
        )


def test_hash_files_honours_cancel_callback(tmp_path: Path) -> None:
    paths = []
    for index in range(4):
        path = tmp_path / f"{index}.bin"
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        paths.append(path)

    with pytest.raises(CancelledError):
        _hash_files(paths, workers=1, cancel_callback=lambda: True)
