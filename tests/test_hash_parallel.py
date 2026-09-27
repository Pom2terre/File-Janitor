"""Tests du hachage parallèle : correction (identique au séquentiel), gestion
des erreurs de lecture, et prise en compte de --hash-workers.
"""

from __future__ import annotations

from pathlib import Path

from file_janitor.scanner.scan import _hash_files, default_hash_workers, scan_directory


def test_default_hash_workers_is_positive() -> None:
    assert default_hash_workers() >= 1


def test_hash_files_sequential_and_parallel_agree(tmp_path: Path) -> None:
    paths = []
    for i in range(20):
        path = tmp_path / f"file{i}.bin"
        # Deux groupes de contenu partagé, pour avoir de vrais doublons à hasher.
        path.write_bytes(b"contenu A" if i % 2 == 0 else b"contenu B")
        paths.append(path)

    sequential = _hash_files(paths, workers=1)
    parallel = _hash_files(paths, workers=8)

    assert sequential == parallel
    assert all(h is not None for h in sequential.values())
    # Même hash pour tous les fichiers "contenu A" entre eux.
    assert len({sequential[p] for p in paths[::2]}) == 1
    assert len({sequential[p] for p in paths[1::2]}) == 1


def test_hash_files_empty_list() -> None:
    assert _hash_files([], workers=4) == {}


def test_hash_files_handles_unreadable_file(tmp_path: Path) -> None:
    missing = tmp_path / "gone.bin"  # jamais créé : la lecture échoue

    results = _hash_files([missing], workers=4)

    assert results[missing] is None


def test_scan_directory_hash_workers_produces_same_duplicates(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("même contenu")
    (tmp_path / "b.txt").write_text("même contenu")
    (tmp_path / "c.txt").write_text("autre chose")

    sequential_scan = scan_directory(tmp_path, compute_hashes=True, compute_content_type=False, hash_workers=1)
    parallel_scan = scan_directory(tmp_path, compute_hashes=True, compute_content_type=False, hash_workers=4)

    seq_hashes = {f.path.name: f.hash for f in sequential_scan.files}
    par_hashes = {f.path.name: f.hash for f in parallel_scan.files}

    assert seq_hashes == par_hashes
    assert seq_hashes["a.txt"] == seq_hashes["b.txt"]
    assert seq_hashes["a.txt"] != seq_hashes["c.txt"]
