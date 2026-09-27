"""Construction sûre de plans d'archivage selon la date de modification."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
    ScanResult,
)


class ArchivePlanError(ValueError):
    """Paramètres d'archivage invalides ou périmètres qui se chevauchent."""


class ArchiveFormat(str, Enum):
    """Format de sortie de l'archivage selon l'ancienneté."""

    FOLDERS = "folders"
    ZIP = "zip"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser())).resolve(strict=False)


def _overlap(first: Path, second: Path) -> bool:
    try:
        common = Path(os.path.commonpath((str(first), str(second))))
    except ValueError:
        return False
    return common == first or common == second


def build_archive_actions(
    scan: ScanResult,
    destination_root: Path,
    older_than_days: int,
    *,
    archive_format: ArchiveFormat | str = ArchiveFormat.FOLDERS,
    now: datetime | None = None,
) -> list[ActionItem]:
    """Prépare le déplacement des fichiers assez anciens, sans écrire.

    La destination reprend le chemin relatif de chaque fichier. Les deux
    racines doivent être disjointes afin que l'archive ne soit pas rescannée
    comme source lors d'une exécution ultérieure.
    """

    if isinstance(older_than_days, bool) or older_than_days < 1:
        raise ArchivePlanError("L’ancienneté doit être d’au moins un jour.")

    try:
        archive_format = ArchiveFormat(archive_format)
    except ValueError as exc:
        raise ArchivePlanError("Format d’archive non pris en charge.") from exc

    source_root = _absolute(scan.root)
    archive_root = _absolute(destination_root)
    if _overlap(source_root, archive_root):
        raise ArchivePlanError(
            "Le dossier d’archive doit être distinct du dossier source et de "
            "ses sous-dossiers."
        )

    current_time = now or datetime.now()
    cutoff = current_time - timedelta(days=older_than_days)
    zip_destination = None
    if archive_format is ArchiveFormat.ZIP:
        source_name = source_root.name or "archive"
        stem = f"{source_name}-{current_time:%Y%m%d-%H%M%S}"
        zip_destination = archive_root / f"{stem}.zip"
        suffix = 2
        while zip_destination.exists() or zip_destination.is_symlink():
            zip_destination = archive_root / f"{stem}-{suffix}.zip"
            suffix += 1
    records = sorted(scan.files, key=lambda record: str(record.path))
    actions: list[ActionItem] = []
    for record in records:
        if record.mtime > cutoff:
            continue
        source = _absolute(record.path)
        try:
            relative_path = source.relative_to(source_root)
        except ValueError as exc:
            raise ArchivePlanError(
                f"Un chemin scanné sort du dossier source : {record.path}"
            ) from exc

        destination = (
            archive_root / relative_path
            if archive_format is ArchiveFormat.FOLDERS
            else zip_destination
        )
        assert destination is not None
        conflict = destination.exists()
        actions.append(
            ActionItem(
                category=FileCategory.ARCHIVE,
                path=record.path,
                size=record.size,
                reason=f"Modifié le {record.mtime:%Y-%m-%d} (seuil : {older_than_days} jours)",
                identity=record.identity,
                destination=destination,
                action=ActionKind.NONE if conflict else ActionKind.MOVE,
                conflict_policy=ConflictPolicy.SKIP,
                conflict=conflict,
                conflict_reason=(
                    "une cible existe déjà ; elle ne sera pas écrasée"
                    if conflict
                    else None
                ),
            )
        )
    return actions
