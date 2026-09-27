"""Repérage read-only des dossiers vides ou composés d'autres dossiers vides."""

from __future__ import annotations

import os
import stat
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from file_janitor.models import FileIdentity
from file_janitor.path_safety import identity_from_stat
from file_janitor.scanner.ignore import build_ignore_spec, is_excluded


@dataclass(frozen=True, slots=True)
class EmptyDirectoryRecord:
    """Dossier qui deviendra vide après retrait des candidats enfants."""

    path: Path
    identity: FileIdentity
    child_directories: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class EmptyDirectoryScan:
    """Résultat read-only du parcours des dossiers."""

    root: Path
    directories: tuple[EmptyDirectoryRecord, ...]
    errors: tuple[str, ...]


def scan_empty_directories(
    folder: Path,
    *,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> EmptyDirectoryScan:
    """Parcourt sans suivre les liens et conserve l'ordre enfants puis parents."""

    source_arg = Path(folder).expanduser()
    if source_arg.is_symlink():
        raise ValueError(f"le dossier source ne peut pas être un lien symbolique : {source_arg}")
    root = source_arg.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"le chemin source n’est pas un dossier : {root}")

    spec = build_ignore_spec(
        root,
        exclude_patterns,
        use_defaults=use_default_excludes,
        use_ignore_file=use_ignore_file,
    )
    # path -> (identity, child directories, blocked by a file/excluded/unreadable child)
    inventory: dict[Path, tuple[FileIdentity, list[Path], bool]] = {}
    errors: list[str] = []
    pending = [root]
    visited = 0

    while pending:
        if cancel_callback is not None and cancel_callback():
            raise CancelledError()
        current = pending.pop()
        try:
            current_stat = current.stat(follow_symlinks=False)
            if not stat.S_ISDIR(current_stat.st_mode):
                errors.append(f"{current}: l’entrée n’est plus un dossier réel")
                continue
            entries = list(os.scandir(current))
        except OSError as exc:
            errors.append(f"{current}: {exc}")
            continue

        children: list[Path] = []
        blocked = False
        for entry in entries:
            child = Path(entry.path)
            relative = child.relative_to(root)
            try:
                if entry.is_dir(follow_symlinks=False):
                    if is_excluded(spec, relative, is_dir=True):
                        # Un dossier ignoré ne doit pas rendre son parent
                        # candidat à la suppression.
                        blocked = True
                        continue
                    children.append(child)
                    pending.append(child)
                else:
                    # Fichier, lien symbolique ou entrée spéciale : le dossier
                    # n'est pas vide et ne sera jamais supprimé dans ce plan.
                    blocked = True
            except OSError as exc:
                errors.append(f"{child}: {exc}")
                blocked = True

        inventory[current] = (identity_from_stat(current_stat), children, blocked)
        visited += 1
        if progress_callback is not None:
            progress_callback(("empty_directories", visited))

    candidates: set[Path] = set()
    ordered_paths = sorted(
        inventory,
        key=lambda path: (-len(path.parts), path.as_posix().casefold(), path.as_posix()),
    )
    records: list[EmptyDirectoryRecord] = []
    for path in ordered_paths:
        identity, children, blocked = inventory[path]
        if (
            path == root
            or blocked
            or any(child not in candidates for child in children)
        ):
            continue
        candidates.add(path)
        records.append(
            EmptyDirectoryRecord(
                path=path,
                identity=identity,
                child_directories=tuple(children),
            )
        )

    return EmptyDirectoryScan(root, tuple(records), tuple(errors))
