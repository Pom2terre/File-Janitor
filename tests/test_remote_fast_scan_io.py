"""Tests 4E-7B : le profil rapide reste strictement metadata-only."""

from __future__ import annotations

from pathlib import Path

import pytest

import file_janitor.scanner.scan as scan_module


def _unexpected_hash(*args: object, **kwargs: object) -> object:
    pytest.fail("le scan rapide ne doit jamais calculer de hash")


def _unexpected_sniff(*args: object, **kwargs: object) -> object:
    pytest.fail("le scan rapide ne doit jamais lire les magic bytes")


def test_fast_scan_never_reads_file_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """False/False doit interdire les deux chemins de lecture du contenu."""

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"

    # Même taille volontairement : si compute_hashes=False était ignoré,
    # les deux fichiers seraient candidats au hachage.
    first.write_bytes(b"aaaa")
    second.write_bytes(b"bbbb")

    monkeypatch.setattr(scan_module, "_hash_files", _unexpected_hash)
    monkeypatch.setattr(scan_module, "sniff_content_type", _unexpected_sniff)

    stages: list[str] = []
    result = scan_module.scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        progress_callback=stages.append,
    )

    assert {record.path for record in result.files} == {first, second}
    assert all(record.hash is None for record in result.files)
    assert all(record.content_family is None for record in result.files)
    assert all(record.content_label is None for record in result.files)
    assert stages == ["metadata"]


def test_fast_scan_skips_hash_workers_even_for_duplicate_size_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le nombre de workers ne doit avoir aucun effet si le hash est désactivé."""

    (tmp_path / "a.dat").write_bytes(b"1111")
    (tmp_path / "b.dat").write_bytes(b"2222")

    monkeypatch.setattr(scan_module, "_hash_files", _unexpected_hash)

    result = scan_module.scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        hash_workers=8,
    )

    assert len(result.files) == 2
    assert all(record.hash is None for record in result.files)


def test_fast_scan_still_honours_cooperative_cancellation(
    tmp_path: Path,
) -> None:
    """Metadata-only ne doit pas désactiver le contrat d'annulation."""

    (tmp_path / "file.txt").write_text("content", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        scan_module.scan_directory(
            tmp_path,
            compute_hashes=False,
            compute_content_type=False,
            cancel_callback=lambda: True,
        )

    # Le scanner utilise concurrent.futures.CancelledError.
    from concurrent.futures import CancelledError

    assert isinstance(exc_info.value, CancelledError)
