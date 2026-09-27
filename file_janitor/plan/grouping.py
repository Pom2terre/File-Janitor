"""Stratégies de regroupement pour le classement (`janitor sort --group-by`).

Inspiré des options de Qiplex Easy File Organizer : au-delà du classement
par « Kind » (famille de contenu réel, la stratégie par défaut, gérée à part
dans plan/builder.py car elle utilise les libellés humains de
scanner.filetype), quatre autres stratégies purement mécaniques permettent
de classer *tous* les fichiers d'un dossier, sans se soucier de reconnaître
leur type :

- Extension : un dossier par extension (mp3/, png/, ...).
- Alphabet : un dossier par première lettre du nom de fichier (a/, b/, ...).
- Taille : classes de taille explicites et non chevauchantes.
- Date : dossier basé sur la date de modification, à la granularité
  choisie (jour, mois ou année).
"""

from __future__ import annotations

from enum import Enum

from file_janitor.models import FileRecord

_SIZE_KB = 1024
_SIZE_MB = 1024 * _SIZE_KB

_SIZE_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (10 * _SIZE_KB, "moins_de_10_Ko"),
    (50 * _SIZE_KB, "10_a_50_Ko"),
    (100 * _SIZE_KB, "50_a_100_Ko"),
    (500 * _SIZE_KB, "100_a_500_Ko"),
    (_SIZE_MB, "500_Ko_a_1_Mo"),
    (10 * _SIZE_MB, "1_a_10_Mo"),
    (100 * _SIZE_MB, "10_a_100_Mo"),
    (500 * _SIZE_MB, "100_a_500_Mo"),
    (None, "500_Mo_et_plus"),
)

NO_EXTENSION_FOLDER = "sans_extension"
NON_ALPHA_FOLDER = "#"


class GroupBy(str, Enum):
    """Stratégie de classement utilisée par `janitor sort`."""

    KIND = "kind"
    EXTENSION = "extension"
    ALPHABET = "alphabet"
    SIZE = "size"
    DATE = "date"
    COMMON_NAME = "common_name"


class DateGranularity(str, Enum):
    """Granularité du regroupement par date (GroupBy.DATE uniquement)."""

    DAY = "day"
    MONTH = "month"
    YEAR = "year"


def _folder_by_extension(record: FileRecord) -> str:
    ext = record.extension.lstrip(".")
    return ext if ext else NO_EXTENSION_FOLDER


def _folder_by_alphabet(record: FileRecord) -> str:
    name = record.path.stem or record.path.name
    first = name[0].lower() if name else ""
    return first if first.isalpha() else NON_ALPHA_FOLDER


def _folder_by_size(record: FileRecord) -> str:
    """Retourne une classe de taille portable et non chevauchante."""

    for upper_bound, folder in _SIZE_BUCKETS:
        if upper_bound is None or record.size < upper_bound:
            return folder
    raise AssertionError("classe de taille introuvable")


def _folder_by_date(record: FileRecord, granularity: DateGranularity) -> str:
    if granularity == DateGranularity.YEAR:
        return record.mtime.strftime("%Y")
    if granularity == DateGranularity.MONTH:
        return record.mtime.strftime("%Y-%m")
    return record.mtime.strftime("%Y-%m-%d")


def destination_folder(
    record: FileRecord,
    group_by: GroupBy,
    date_granularity: DateGranularity = DateGranularity.DAY,
) -> str | None:
    """Nom du dossier de destination (relatif à la racine) pour une stratégie
    mécanique. Retourne None pour GroupBy.KIND : cette stratégie est gérée
    séparément dans plan/builder.py, car elle a besoin des libellés humains
    de familles de contenu (FAMILY_LABELS) plutôt que d'un simple calcul.
    """
    if group_by == GroupBy.EXTENSION:
        return _folder_by_extension(record)
    if group_by == GroupBy.ALPHABET:
        return _folder_by_alphabet(record)
    if group_by == GroupBy.SIZE:
        return _folder_by_size(record)
    if group_by == GroupBy.DATE:
        return _folder_by_date(record, date_granularity)
    return None


def describe_destination(record: FileRecord, group_by: GroupBy, folder: str) -> str:
    """Raison humaine affichée dans l'aperçu, pour une stratégie mécanique."""
    if group_by == GroupBy.EXTENSION:
        label = record.extension if record.extension else "(aucune extension)"
        return f"Extension {label}, à classer"
    if group_by == GroupBy.ALPHABET:
        return f"Commence par « {folder.upper()} », à classer"
    if group_by == GroupBy.SIZE:
        return f"Taille : {folder}, à classer"
    if group_by == GroupBy.DATE:
        return f"Modifié le {folder}, à classer"
    return "à classer"
