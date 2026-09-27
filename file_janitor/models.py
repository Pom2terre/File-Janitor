"""Modèles de données partagés entre le scan, le plan et l'exécution.

Toute la logique du File Janitor repose sur ce pipeline :

    scan -> plan -> preview -> execute -> undo

Ces dataclasses représentent l'état à chaque étape, afin que chaque module
puisse être testé indépendamment (pas de couplage direct entre le scanner
et l'exécuteur, par exemple).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class FileCategory(str, Enum):
    """Catégories détectées lors du scan, utilisées pour construire le plan."""

    DUPLICATE = "duplicate"
    OLD_ARCHIVE = "old_archive"
    LARGE_FILE = "large_file"
    OLD_FILE = "old_file"
    TO_SORT = "to_sort"
    RENAME = "rename"
    ARCHIVE = "archive"
    EMPTY_DIRECTORY = "empty_directory"
    EXTENSION_MISMATCH = "extension_mismatch"


class ActionKind(str, Enum):
    """Type d'opération à effectuer sur un fichier."""

    NONE = "none"
    MOVE = "move"
    COPY = "copy"
    TRASH = "trash"
    RMDIR = "rmdir"


class ConflictPolicy(str, Enum):
    """Politique appliquée lorsqu'une destination existe déjà."""

    SKIP = "skip"
    RENAME = "rename"
    REPLACE = "replace"


class ActionStatus(str, Enum):
    """État d'une opération pendant son cycle de vie."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Libellés humains partagés par la CLI et les rapports (JSON/HTML), pour ne
# pas dupliquer cette correspondance dans plusieurs modules.
CATEGORY_LABELS: dict[FileCategory, str] = {
    FileCategory.DUPLICATE: "Doublons",
    FileCategory.OLD_ARCHIVE: "Archives anciennes",
    FileCategory.LARGE_FILE: "Fichiers > 500 Mo",
    FileCategory.OLD_FILE: "Fichiers > 1 an",
    FileCategory.TO_SORT: "Fichiers à classer",
    FileCategory.RENAME: "Renommage par lot",
    FileCategory.ARCHIVE: "Archivage selon l’ancienneté",
    FileCategory.EMPTY_DIRECTORY: "Dossiers vides",
    FileCategory.EXTENSION_MISMATCH: "Extensions trompeuses",
}


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Identité légère d'un fichier capturée au moment du scan.

    Cette empreinte permet de vérifier ultérieurement qu'un fichier présent
    au même chemin est toujours celui qui a été observé pendant le scan.

    Elle ne constitue pas un hash de contenu :
    - device + inode identifient l'objet filesystem ;
    - size + mtime_ns permettent de détecter une modification du même objet.
    """

    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class FileRecord:
    """Métadonnées d'un fichier tel que rencontré pendant le scan."""

    path: Path
    size: int
    mtime: datetime
    extension: str
    hash: str | None = None  # calculé seulement si nécessaire (coûteux en I/O)
    content_family: str | None = None  # famille détectée par magic bytes
    content_label: str | None = None  # libellé humain (ex: "Image PNG")
    identity: FileIdentity | None = None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(slots=True)
class ScanResult:
    """Résultat brut d'un scan : tous les fichiers trouvés et leurs métadonnées."""

    root: Path
    files: list[FileRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata_complete: bool = True
    identities_complete: bool = True

    @property
    def total_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True, slots=True)
class ClassificationGuard:
    """Empreinte logique d'un classement dépendant des métadonnées.

    Pour un scan Remote / rapide, rclone fournit taille et date mais pas
    l'identité filesystem forte. Le plan mémorise donc la classe qui a
    déterminé la destination (tranche de taille ou période) afin de pouvoir
    la recalculer juste avant l'exécution.
    """

    group_by: str
    folder: str
    date_granularity: str | None = None
    secondary_group_by: str | None = None
    secondary_folder: str | None = None


@dataclass(slots=True)
class ActionItem:
    """Opération proposée sur un fichier.

    Cet objet décrit l'intention du planificateur. Il ne modifie jamais
    lui-même le système de fichiers.

    ``path`` est conservé pour compatibilité avec l'API existante.
    ``source`` fournit le nom explicite à utiliser dans le nouveau code.
    """

    category: FileCategory
    path: Path
    size: int
    reason: str
    identity: FileIdentity | None = None
    destination: Path | None = None
    classification_guard: ClassificationGuard | None = None
    archive_format: str | None = None

    action: ActionKind = ActionKind.NONE
    conflict_policy: ConflictPolicy = ConflictPolicy.RENAME

    conflict: bool = False
    conflict_reason: str | None = None

    status: ActionStatus = ActionStatus.PLANNED
    error: str | None = None

    @property
    def source(self) -> Path:
        """Chemin source de l'opération."""
        return self.path


@dataclass(slots=True)
class Plan:
    """Ensemble des actions proposées après analyse d'un ScanResult.

    Le plan ne modifie rien : il est uniquement construit à partir des
    données du scan, puis présenté à l'utilisateur (preview) avant toute
    exécution éventuelle.
    """

    scan: ScanResult
    items_by_category: dict[FileCategory, list[ActionItem]] = field(
        default_factory=dict
    )

    def items(self, category: FileCategory) -> list[ActionItem]:
        return self.items_by_category.get(category, [])

    def total_size(self, category: FileCategory) -> int:
        return sum(item.size for item in self.items(category))

    def add(self, item: ActionItem) -> None:
        self.items_by_category.setdefault(item.category, []).append(item)
