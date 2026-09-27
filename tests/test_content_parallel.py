"""Les lectures d'en-tête restent ordonnées et bornées sur un dossier cloud."""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Lock

from file_janitor.scanner import scan as scan_module
from file_janitor.scanner.filetype import ContentInfo


def test_content_sniff_is_bounded_and_preserves_scan_order(
    tmp_path: Path, monkeypatch,
) -> None:
    paths = [tmp_path / f"video-{index:02}.mp4" for index in range(12)]
    for path in paths:
        path.write_bytes(b"\x00\x00\x00\x14ftypisom")

    first_batch = Barrier(4, timeout=5)
    lock = Lock()
    active = 0
    maximum = 0

    def sniff(path: Path) -> ContentInfo:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        if path in paths[:4]:
            first_batch.wait()
        with lock:
            active -= 1
        return ContentInfo("video", path.name)

    monkeypatch.setattr(scan_module, "sniff_content_type", sniff)
    progress: list[object] = []
    infos = scan_module._sniff_files(paths, progress_callback=progress.append)

    assert maximum == 4
    assert [info.label for info in infos] == [path.name for path in paths]
    assert progress == [("content", 5, 12), ("content", 10, 12), ("content", 12, 12)]
