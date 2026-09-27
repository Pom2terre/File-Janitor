"""Tests d’annulation de la façade applicative."""

from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from file_janitor.application import analyze_folder


def test_analyze_folder_can_be_cancelled_before_scan(tmp_path: Path) -> None:
    with pytest.raises(CancelledError):
        analyze_folder(
            tmp_path,
            compute_hashes=False,
            compute_content_type=False,
            cancel_callback=lambda: True,
        )
