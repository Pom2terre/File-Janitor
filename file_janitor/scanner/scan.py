"""Parcours d'un dossier : construit un ScanResult sans jamais écrire sur le disque.

Le hash n'est calculé que si demandé (compute_hashes=True), car c'est l'étape
la plus coûteuse en I/O. Pour la détection de doublons, on optimise en ne
hashant que les fichiers dont la taille apparaît plusieurs fois (deux fichiers
de tailles différentes ne peuvent jamais être identiques).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from file_janitor.models import FileIdentity, FileRecord, ScanResult
from file_janitor.scanner.filetype import ContentInfo, sniff_content_type
from file_janitor.scanner.ignore import build_ignore_spec, is_excluded

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 Mo
_CONTENT_WORKERS = 4  # borne les ouvertures simultanées sur un montage distant


def _raise_if_cancelled(
    cancel_callback: Callable[[], bool] | None,
) -> None:
    """Interrompt proprement le scan lorsqu’une annulation est demandée."""

    if cancel_callback is not None and cancel_callback():
        raise CancelledError()


def _hash_file(
    path: Path,
    algorithm: str = "sha256",
    cancel_callback: Callable[[], bool] | None = None,
) -> str | None:
    """Calcule le hash d'un fichier par lecture en flux (pas tout en mémoire)."""
    hasher = hashlib.new(algorithm)
    try:
        with path.open("rb") as f:
            while True:
                _raise_if_cancelled(cancel_callback)
                chunk = f.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as exc:
        logger.warning("Impossible de hasher %s : %s", path, exc)
        return None
    return hasher.hexdigest()


def default_hash_workers() -> int:
    """Nombre de threads par défaut pour le hachage parallèle.

    hashlib (implémentation C) relâche le GIL pendant le calcul sur des blocs
    de taille suffisante (voir _HASH_CHUNK_SIZE = 1 Mo) : des threads
    profitent donc réellement de plusieurs cœurs pour cette tâche, sans le
    coût de sérialisation d'un ProcessPoolExecutor.
    """
    return min(32, (os.cpu_count() or 1) + 4)


def _hash_files(
    paths: list[Path],
    workers: int,
    cancel_callback: Callable[[], bool] | None = None,
    progress_callback: Callable[[object], None] | None = None,
) -> dict[Path, str | None]:
    """Hash une liste de fichiers, séquentiellement (workers<=1) ou en
    parallèle (ThreadPoolExecutor sinon)."""
    if not paths:
        return {}
    if workers <= 1:
        results: dict[Path, str | None] = {}
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            _raise_if_cancelled(cancel_callback)
            results[path] = _hash_file(path, cancel_callback=cancel_callback)
            if (
                progress_callback is not None
                and total >= 25
                and (index == total or index % 25 == 0)
            ):
                progress_callback(("hashes", index, total))
        return results

    results: dict[Path, str | None] = {}
    executor = ThreadPoolExecutor(max_workers=workers)
    future_to_path = {
        executor.submit(_hash_file, path, "sha256", cancel_callback): path
        for path in paths
    }
    try:
        total = len(future_to_path)
        for index, future in enumerate(as_completed(future_to_path), start=1):
            _raise_if_cancelled(cancel_callback)
            results[future_to_path[future]] = future.result()
            if (
                progress_callback is not None
                and total >= 25
                and (index == total or index % 25 == 0)
            ):
                progress_callback(("hashes", index, total))
    except CancelledError:
        for future in future_to_path:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    except Exception:
        executor.shutdown(wait=True)
        raise
    else:
        executor.shutdown(wait=True)
    return results


def _sniff_files(
    paths: list[Path],
    *,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> list[ContentInfo]:
    """Lit les en-têtes par petits groupes, sans saturer un montage cloud."""

    results: list[ContentInfo] = []
    total = len(paths)

    def append_result(info: ContentInfo) -> None:
        results.append(info)
        index = len(results)
        if progress_callback is not None and total >= 10 and (
            index == total or index % 5 == 0
        ):
            progress_callback(("content", index, total))

    if total < 8:
        for path in paths:
            _raise_if_cancelled(cancel_callback)
            append_result(sniff_content_type(path))
    else:
        with ThreadPoolExecutor(max_workers=_CONTENT_WORKERS) as executor:
            # map() sans buffersize soumettrait tout le dossier d'un coup.
            # Les groupes de quatre bornent aussi les I/O déjà en vol lors
            # d'une annulation.
            for offset in range(0, total, _CONTENT_WORKERS):
                _raise_if_cancelled(cancel_callback)
                batch = paths[offset : offset + _CONTENT_WORKERS]
                for info in executor.map(sniff_content_type, batch):
                    _raise_if_cancelled(cancel_callback)
                    append_result(info)

    _raise_if_cancelled(cancel_callback)
    return results


def scan_directory(
    root: Path,
    *,
    compute_hashes: bool = True,
    compute_content_type: bool = True,
    follow_symlinks: bool = False,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    hash_workers: int | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    collect_metadata: bool = True,
) -> ScanResult:
    """Parcourt récursivement `root` et retourne un ScanResult.

    Cette fonction est purement en lecture : aucune suppression, déplacement
    ou modification n'est effectuée ici.

    `compute_content_type` active la détection du type réel de chaque
    fichier par ses premiers octets (magic bytes, voir
    file_janitor.scanner.filetype), utilisée pour le classement et la
    détection d'extensions trompeuses. Désactivable pour un scan plus rapide.

    `hash_workers` contrôle le parallélisme du hachage (candidats doublons
    uniquement) : None = automatique selon les cœurs CPU (voir
    default_hash_workers), 1 = séquentiel.

    Les exclusions combinent (voir file_janitor.scanner.ignore) :
    - des règles toujours actives (état interne du janitor),
    - le fichier `<root>/.janitorignore` (syntaxe gitignore),
    - `exclude_patterns` fournis par l'appelant (ex: --exclude en CLI).
    Un dossier exclu n'est pas descendu (économie d'I/O sur node_modules, etc.).
    """
    _raise_if_cancelled(cancel_callback)
    root = root.expanduser().resolve()
    result = ScanResult(
        root=root,
        metadata_complete=collect_metadata,
        identities_complete=collect_metadata,
    )

    if not root.exists():
        result.errors.append(f"Le dossier {root} n'existe pas.")
        return result
    if not root.is_dir():
        result.errors.append(f"{root} n'est pas un dossier.")
        return result

    ignore_spec = build_ignore_spec(
        root,
        exclude_patterns,
        use_defaults=use_default_excludes,
        use_ignore_file=use_ignore_file,
    )

    if progress_callback is not None:
        progress_callback("metadata")

    raw_files: list[
        tuple[Path, int, datetime, FileIdentity]
    ] = []
    sizes_seen: defaultdict[int, int] = defaultdict(int)

    def _record_walk_error(exc: OSError) -> None:
        error_path = Path(exc.filename) if exc.filename else root
        result.errors.append(f"{error_path}: {exc}")

    discovered_count = 0
    for dirpath, dirnames, filenames in os.walk(
        root,
        followlinks=follow_symlinks,
        onerror=_record_walk_error,
    ):
        _raise_if_cancelled(cancel_callback)
        current_dir = Path(dirpath)

        # Élague les sous-dossiers exclus avant d'y descendre.
        kept_dirnames = []
        for dirname in dirnames:
            dir_path = current_dir / dirname
            rel = dir_path.relative_to(root)
            if is_excluded(ignore_spec, rel, is_dir=True):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in filenames:
            _raise_if_cancelled(cancel_callback)
            entry = current_dir / filename
            rel = entry.relative_to(root)
            if is_excluded(ignore_spec, rel, is_dir=False):
                continue
            if not collect_metadata:
                # os.walk a déjà séparé les noms de fichiers des répertoires.
                # Sur certains montages FUSE/cloud, tout stat/lstat par fichier
                # peut déclencher un aller-retour distant très coûteux.
                size = 0
                mtime = datetime.fromtimestamp(0)
                identity = None
            else:
                try:
                    if entry.is_symlink() and not follow_symlinks:
                        continue
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                except OSError as exc:
                    result.errors.append(f"{entry}: {exc}")
                    continue

                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime)
                identity = FileIdentity(
                    device=stat.st_dev,
                    inode=stat.st_ino,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            raw_files.append(
                (
                    entry,
                    size,
                    mtime,
                    identity,
                )
            )
            sizes_seen[size] += 1
            discovered_count += 1
            if progress_callback is not None and discovered_count % 100 == 0:
                progress_callback(("metadata", discovered_count))

    hashes: dict[Path, str | None] = {}
    if compute_hashes:
        if progress_callback is not None:
            progress_callback("hashes")
        candidates = [
            path
            for path, size, _, _ in raw_files
            if sizes_seen[size] > 1
        ]
        # Seuls les fichiers ayant une taille partagée avec un autre sont
        # candidats à être des doublons : on ne hash qu'eux, en parallèle.
        workers = hash_workers if hash_workers is not None else default_hash_workers()
        hashes = _hash_files(
            candidates,
            workers,
            cancel_callback,
            progress_callback,
        )

    if compute_content_type and progress_callback is not None:
        progress_callback("content")

    content_infos = (
        _sniff_files(
            [path for path, _, _, _ in raw_files],
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        if compute_content_type
        else []
    )
    for content_index, (path, size, mtime, identity) in enumerate(raw_files, start=1):
        _raise_if_cancelled(cancel_callback)
        file_hash = hashes.get(path) if compute_hashes and sizes_seen[size] > 1 else None

        content_family = content_label = None
        if compute_content_type:
            info = content_infos[content_index - 1]
            content_family, content_label = info.family, info.label

        result.files.append(
            FileRecord(
                path=path,
                size=size,
                mtime=mtime,
                extension=path.suffix.lower(),
                hash=file_hash,
                content_family=content_family,
                content_label=content_label,
                identity=identity,
            )
        )

    _raise_if_cancelled(cancel_callback)
    return result
