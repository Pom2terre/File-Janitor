"""Journal persistant des opérations File Janitor.

HistoryStore conserve dans SQLite les batches d'exécution et les opérations
individuelles afin de permettre :

- l'affichage de l'historique ;
- l'annulation d'un batch ;
- le suivi explicite de l'état d'une exécution ;
- à terme, la récupération après interruption ou crash.

Le schéma est migré automatiquement lors de l'ouverture d'une base créée par
une version antérieure de File Janitor.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator

from file_janitor.models import FileIdentity

DEFAULT_STATE_DIR = Path.home() / ".file_janitor"
DEFAULT_DB_PATH = DEFAULT_STATE_DIR / "history.db"

_SCHEMA_VERSION = 2
_SCHEMA_V1_COLUMNS: dict[str, frozenset[str]] = {
    "batches": frozenset(
        {
            "id",
            "created_at",
            "root",
            "undone",
            "status",
            "planned_count",
            "success_count",
            "failed_count",
            "skipped_count",
        }
    ),
    "operations": frozenset(
        {
            "id",
            "batch_id",
            "kind",
            "original_path",
            "stored_path",
            "size",
            "category",
            "status",
            "error",
            "recovery_state",
            "resolution_kind",
            "resolved_at",
            "stored_device",
            "stored_inode",
            "stored_size",
            "stored_mtime_ns",
        }
    ),
}
_CURRENT_SCHEMA_COLUMNS: dict[str, frozenset[str]] = {
    "batches": frozenset(
        {
            "id",
            "created_at",
            "root",
            "undone",
            "status",
            "planned_count",
            "success_count",
            "failed_count",
            "skipped_count",
        }
    ),
    "operations": frozenset(
        {
            "id",
            "batch_id",
            "kind",
            "original_path",
            "stored_path",
            "size",
            "category",
            "status",
            "error",
            "recovery_state",
            "resolution_kind",
            "resolved_at",
            "stored_device",
            "stored_inode",
            "stored_size",
            "stored_mtime_ns",
            "original_device",
            "original_inode",
            "original_size",
            "original_mtime_ns",
            "conflict_policy",
        }
    ),
}
_SQLITE_TIMEOUT_SECONDS = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5_000
_REQUIRED_LEGACY_SCHEMA_COLUMNS: dict[str, frozenset[str]] = {
    "batches": frozenset({"id", "created_at", "root", "undone"}),
    "operations": frozenset(
        {
            "id",
            "batch_id",
            "kind",
            "original_path",
            "stored_path",
            "size",
            "category",
        }
    ),
}
TRASH_DIR = DEFAULT_STATE_DIR / "trash"


class BatchStatus(str, Enum):
    """État d'un batch d'exécution."""

    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNDOING = "undoing"
    UNDONE = "undone"
    UNDO_PARTIAL = "undo_partial"
    UNDO_FAILED = "undo_failed"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "BatchStatus":
        """Dégrade une valeur persistée inconnue sans casser la lecture."""
        return cls.UNKNOWN


class OperationStatus(str, Enum):
    """État d'une opération enregistrée dans l'historique."""

    QUEUED = "queued"
    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"
    UNDONE = "undone"
    UNDO_FAILED = "undo_failed"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "OperationStatus":
        """Dégrade une valeur persistée inconnue sans casser la lecture."""
        return cls.UNKNOWN


_OPERATION_KINDS = frozenset({"copy", "move", "delete", "rmdir"})
_CONFLICT_POLICIES = frozenset({"skip", "rename", "replace"})
_SQLITE_SIGNED_INTEGER_MAX = (1 << 63) - 1
_FILESYSTEM_UNSIGNED_INTEGER_MAX = (1 << 64) - 1
_UNSIGNED_IDENTITY_PREFIX = "u64:"


def _encode_persisted_identity_component(
    value: int,
    *,
    field: str,
) -> int | str:
    """Encode un entier filesystem non signé sans perte dans SQLite.

    sqlite3 refuse les ``int`` Python supérieurs à ``2**63 - 1``. Les
    filesystems FUSE/rclone exposent pourtant des inodes 64 bits non signés.
    Une valeur hors plage signée est donc stockée sous forme de texte marqué,
    ce qui évite aussi la conversion SQLite implicite en REAL et sa perte de
    précision.
    """

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} doit être un entier non négatif")
    if value <= _SQLITE_SIGNED_INTEGER_MAX:
        return value
    if value <= _FILESYSTEM_UNSIGNED_INTEGER_MAX:
        return f"{_UNSIGNED_IDENTITY_PREFIX}{value}"
    raise ValueError(f"{field} dépasse la plage filesystem 64 bits")


def _decode_persisted_identity_component(
    value: object,
) -> tuple[int | None, object | None]:
    """Décode un entier signé SQLite ou notre représentation u64 marquée."""

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _SQLITE_SIGNED_INTEGER_MAX
    ):
        return value, None
    if isinstance(value, str) and value.startswith(_UNSIGNED_IDENTITY_PREFIX):
        digits = value.removeprefix(_UNSIGNED_IDENTITY_PREFIX)
        if digits.isascii() and digits.isdecimal():
            decoded = int(digits)
            if (
                _SQLITE_SIGNED_INTEGER_MAX < decoded
                <= _FILESYSTEM_UNSIGNED_INTEGER_MAX
            ):
                return decoded, None
    return None, value


def _encode_persisted_identity(
    identity: FileIdentity | None,
    *,
    prefix: str,
) -> tuple[int | str | None, int | str | None, int | str | None, int | str | None]:
    """Encode une identité optionnelle avec les mêmes règles pour chaque rôle."""

    if identity is None:
        return None, None, None, None
    if not isinstance(identity, FileIdentity):
        raise ValueError(f"{prefix}_identity doit être une FileIdentity")
    return (
        _encode_persisted_identity_component(
            identity.device,
            field=f"{prefix}_device",
        ),
        _encode_persisted_identity_component(
            identity.inode,
            field=f"{prefix}_inode",
        ),
        _encode_persisted_identity_component(
            identity.size,
            field=f"{prefix}_size",
        ),
        _encode_persisted_identity_component(
            identity.mtime_ns,
            field=f"{prefix}_mtime_ns",
        ),
    )


def _decode_persisted_identity(
    values: tuple[object | None, object | None, object | None, object | None],
) -> tuple[
    FileIdentity | None,
    tuple[object | None, object | None, object | None, object | None] | None,
]:
    """Décode une identité complète ou conserve ses quatre valeurs brutes."""

    if not any(value is not None for value in values):
        return None, None
    decoded_identity = tuple(
        _decode_persisted_identity_component(value)
        for value in values
    )
    if all(
        decoded is not None and raw is None
        for decoded, raw in decoded_identity
    ):
        return (
            FileIdentity(
                device=decoded_identity[0][0],
                inode=decoded_identity[1][0],
                size=decoded_identity[2][0],
                mtime_ns=decoded_identity[3][0],
            ),
            None,
        )
    return None, values


def _encode_persisted_conflict_policy(value: str | None) -> str | None:
    """Valide une politique de collision optionnelle avant écriture."""

    if value is None:
        return None
    if not isinstance(value, str) or value not in _CONFLICT_POLICIES:
        raise ValueError(
            f"politique de collision inconnue : {value!r}"
        )
    return value


def _decode_persisted_conflict_policy(
    value: object,
) -> tuple[str | None, object | None]:
    """Décode une politique connue ou conserve sa valeur brute invalide."""

    if value is None:
        return None, None
    if isinstance(value, str) and value in _CONFLICT_POLICIES:
        return value, None
    return None, value


def _decode_persisted_path(
    value: object,
    *,
    optional: bool,
) -> tuple[Path | None, object | None]:
    """Décode un chemin absolu ou conserve sa valeur brute invalide."""

    if optional and value is None:
        return None, None
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
    ):
        return None, value

    path = Path(value)
    if not path.is_absolute():
        return None, value
    return path, None


def _encode_persisted_path(
    value: Path | None,
    *,
    optional: bool,
    field: str,
) -> str | None:
    """Refuse d’écrire un chemin que la lecture classerait invalide."""

    if optional and value is None:
        return None
    if not isinstance(value, Path):
        raise ValueError(f"{field} doit être un Path absolu")

    encoded = str(value)
    if not encoded or "\x00" in encoded or not value.is_absolute():
        raise ValueError(f"{field} doit être un chemin absolu valide")
    return encoded


def _decode_persisted_size(
    value: object,
) -> tuple[int | None, object | None]:
    """Décode une taille non négative ou conserve sa valeur brute."""

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    ):
        return value, None
    return None, value


def _decode_persisted_category(
    value: object,
) -> tuple[str | None, object | None]:
    """Décode une catégorie textuelle non vide ou conserve le brut."""

    if isinstance(value, str) and value.strip():
        return value, None
    return None, value


def _encode_persisted_size(value: int) -> int:
    """Refuse d’écrire une taille que la lecture classerait invalide."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError("size doit être un entier non négatif")
    return value


def _encode_persisted_category(value: str) -> str:
    """Refuse d’écrire une catégorie vide ou non textuelle."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("category doit être un texte non vide")
    return value


def _decode_persisted_batch_root(
    value: object,
) -> tuple[str | None, object | None]:
    """Décode une racine absolue ou conserve sa valeur brute."""

    if (
        isinstance(value, str)
        and value
        and "\0" not in value
        and Path(value).is_absolute()
    ):
        return value, None
    return None, value


def _decode_persisted_undone(
    value: object,
) -> tuple[bool | None, object | None]:
    """Décode le booléen SQLite strict 0/1 ou conserve le brut."""

    if type(value) is int and value in {0, 1}:
        return bool(value), None
    return None, value


def _decode_persisted_count(
    value: object,
) -> tuple[int | None, object | None]:
    """Décode un compteur non négatif ou conserve sa valeur brute."""

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    ):
        return value, None
    return None, value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    root TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    planned_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    kind TEXT NOT NULL,
    original_path TEXT NOT NULL,
    stored_path TEXT,
    size INTEGER NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    error TEXT,
    recovery_state TEXT,
    resolution_kind TEXT,
    resolved_at TEXT,
    stored_device INTEGER,
    stored_inode INTEGER,
    stored_size INTEGER,
    stored_mtime_ns INTEGER,
    original_device INTEGER,
    original_inode INTEGER,
    original_size INTEGER,
    original_mtime_ns INTEGER,
    conflict_policy TEXT
);
"""


class RecoveryResolutionKind(str, Enum):
    """Nature structurée de la résolution d'un recovery après crash."""

    AUTOMATIC_NO_PUBLICATION = "automatic_no_publication"
    MANUAL_NO_FILE_ACTION = "manual_no_file_action"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "RecoveryResolutionKind":
        """Dégrade une valeur persistée inconnue sans casser la lecture."""
        return cls.UNKNOWN

class PlannedRecoveryState(str, Enum):
    """État structuré d'une opération PLANNED examinée après crash."""

    NO_PUBLISHED_OBJECT_OBSERVED = "no_published_object_observed"
    PUBLISHED_OBJECT_UNVERIFIED = "published_object_unverified"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "PlannedRecoveryState":
        """Dégrade une valeur persistée inconnue sans casser la lecture."""
        return cls.UNKNOWN



@dataclass(slots=True)
class Batch:
    """Batch d'opérations enregistré dans SQLite."""

    id: int
    created_at: datetime | None
    root: str | None
    undone: bool | None
    status: BatchStatus = BatchStatus.RUNNING
    created_at_raw: str | None = None
    status_raw: str | None = None
    root_raw: object | None = None
    undone_raw: object | None = None
    planned_count: int | None = 0
    success_count: int | None = 0
    failed_count: int | None = 0
    skipped_count: int | None = 0
    planned_count_raw: object | None = None
    success_count_raw: object | None = None
    failed_count_raw: object | None = None
    skipped_count_raw: object | None = None


    def has_actionable_metadata(self) -> bool:
        """Vérifie qu'un batch persisté peut piloter une action sûre."""

        if (
            self.created_at is None
            or self.created_at_raw is not None
            or self.status is BatchStatus.UNKNOWN
            or self.status_raw is not None
            or self.root is None
            or self.root_raw is not None
            or self.undone is None
            or self.undone_raw is not None
            or self.planned_count is None
            or self.planned_count_raw is not None
            or self.success_count is None
            or self.success_count_raw is not None
            or self.failed_count is None
            or self.failed_count_raw is not None
            or self.skipped_count is None
            or self.skipped_count_raw is not None
        ):
            return False

        return (
            self.success_count + self.failed_count + self.skipped_count
            <= self.planned_count
        )


@dataclass(slots=True)
class Operation:
    """Opération individuelle appartenant à un batch."""

    id: int
    batch_id: int
    kind: str
    original_path: Path | None
    stored_path: Path | None
    size: int | None
    category: str | None
    status: OperationStatus = OperationStatus.PLANNED
    status_raw: str | None = None
    error: str | None = None
    recovery_state: PlannedRecoveryState | None = None
    recovery_state_raw: str | None = None
    resolution_kind: RecoveryResolutionKind | None = None
    resolution_kind_raw: str | None = None
    resolved_at: datetime | None = None
    resolved_at_raw: str | None = None
    stored_identity: FileIdentity | None = None
    stored_identity_raw: tuple[object | None, object | None, object | None, object | None] | None = None
    original_identity: FileIdentity | None = None
    original_identity_raw: tuple[object | None, object | None, object | None, object | None] | None = None
    conflict_policy: str | None = None
    conflict_policy_raw: object | None = None
    original_path_raw: object | None = None
    stored_path_raw: object | None = None
    size_raw: object | None = None
    category_raw: object | None = None


class HistoryStore:
    """Couche d'accès SQLite pour l'historique et l'undo."""

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._conn = sqlite3.connect(
            self.db_path,
            timeout=_SQLITE_TIMEOUT_SECONDS,
        )
        self._conn.row_factory = sqlite3.Row

        try:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(
                f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}"
            )

            foreign_keys = self._conn.execute(
                "PRAGMA foreign_keys"
            ).fetchone()
            busy_timeout = self._conn.execute(
                "PRAGMA busy_timeout"
            ).fetchone()

            if foreign_keys is None or int(foreign_keys[0]) != 1:
                raise RuntimeError(
                    "SQLite n'a pas activé l'intégrité référentielle"
                )
            if (
                busy_timeout is None
                or int(busy_timeout[0]) != _SQLITE_BUSY_TIMEOUT_MS
            ):
                raise RuntimeError(
                    "SQLite n'a pas appliqué le délai d'attente"
                )

            self._validate_existing_database()
            schema_version = self._validate_schema_version()
            self._conn.executescript(
                "BEGIN IMMEDIATE;\n" + _SCHEMA
            )
            if schema_version < _SCHEMA_VERSION:
                self._migrate_schema()
                self._conn.execute(
                    f"PRAGMA user_version = {_SCHEMA_VERSION}"
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            self._conn.close()
            raise

    def close(self) -> None:
        """Ferme la connexion SQLite."""

        self._conn.close()

    @contextmanager
    def _cursor(
        self,
        *,
        write: bool = False,
    ) -> Iterator[sqlite3.Cursor]:
        """Fournit un curseur avec une frontière transactionnelle explicite.

        Les mutations réservent le verrou d'écriture avant leur première
        lecture ou écriture. Les lectures conservent le comportement existant
        sans prendre de verrou d'écriture.
        """

        cur = self._conn.cursor()
        try:
            if write:
                cur.execute("BEGIN IMMEDIATE")
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def _validate_existing_database(self) -> None:
        """Valide une base existante sans la modifier."""

        quick_check = tuple(
            str(row[0])
            for row in self._conn.execute(
                "PRAGMA quick_check"
            ).fetchall()
        )
        if quick_check != ("ok",):
            raise sqlite3.DatabaseError(
                f"échec du contrôle SQLite: {quick_check!r}"
            )

        object_rows = self._conn.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        if not object_rows:
            return

        table_names = {
            str(row["name"])
            for row in object_rows
            if str(row["type"]) == "table"
        }
        history_tables = table_names & set(
            _REQUIRED_LEGACY_SCHEMA_COLUMNS
        )
        if not history_tables:
            return
        if "operations" in history_tables and "batches" not in history_tables:
            raise sqlite3.DatabaseError(
                "schéma SQLite incompatible: table batches absente"
            )

        for table_name in sorted(history_tables):
            required_columns = _REQUIRED_LEGACY_SCHEMA_COLUMNS[
                table_name
            ]
            missing_columns = sorted(
                required_columns - self._column_names(table_name)
            )
            if missing_columns:
                raise sqlite3.DatabaseError(
                    "schéma SQLite incompatible: "
                    f"{table_name} sans {missing_columns!r}"
                )

    def _column_names(
        self,
        table: str,
    ) -> set[str]:
        """Retourne les noms de colonnes d'une table SQLite."""

        cur = self._conn.execute(
            f"PRAGMA table_info({table})"
        )

        return {
            str(row["name"])
            for row in cur.fetchall()
        }

    def _validate_schema_version(self) -> int:
        """Valide la version applicative avant toute migration."""

        row = self._conn.execute(
            "PRAGMA user_version"
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError(
                "SQLite n'a pas retourné de version de schéma"
            )
        try:
            version = int(row[0])
        except (TypeError, ValueError, OverflowError) as error:
            raise sqlite3.DatabaseError(
                "Version de schéma SQLite invalide"
            ) from error

        if version not in {0, 1, _SCHEMA_VERSION}:
            raise sqlite3.DatabaseError(
                f"Version de schéma SQLite non prise en charge: "
                f"{version} (maximum: {_SCHEMA_VERSION})"
            )

        expected_schema = {
            1: _SCHEMA_V1_COLUMNS,
            _SCHEMA_VERSION: _CURRENT_SCHEMA_COLUMNS,
        }.get(version)
        if expected_schema is not None:
            for table, expected_columns in (
                expected_schema.items()
            ):
                missing = expected_columns - self._column_names(table)
                if missing:
                    missing_text = ", ".join(sorted(missing))
                    raise sqlite3.DatabaseError(
                        f"Schéma SQLite version {version} incomplet "
                        f"pour {table}: {missing_text}"
                    )

        return version


    def _migrate_schema(self) -> None:
        """Met à niveau une ancienne base sans supprimer son historique."""

        batch_columns = self._column_names("batches")

        if "status" not in batch_columns:
            self._conn.execute(
                """
                ALTER TABLE batches
                ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'
                """
            )

        batch_count_columns = {
            "planned_count": "INTEGER NOT NULL DEFAULT 0",
            "success_count": "INTEGER NOT NULL DEFAULT 0",
            "failed_count": "INTEGER NOT NULL DEFAULT 0",
            "skipped_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in batch_count_columns.items():
            if column_name not in batch_columns:
                self._conn.execute(
                    f"""
                    ALTER TABLE batches
                    ADD COLUMN {column_name} {column_type}
                    """
                )

        operation_columns = self._column_names(
            "operations"
        )

        if "status" not in operation_columns:
            self._conn.execute(
                """
                ALTER TABLE operations
                ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'
                """
            )

        if "error" not in operation_columns:
            self._conn.execute(
                """
                ALTER TABLE operations
                ADD COLUMN error TEXT
                """
            )

        recovery_audit_columns = {
            "recovery_state": "TEXT",
            "resolution_kind": "TEXT",
            "resolved_at": "TEXT",
        }
        for column_name, column_type in recovery_audit_columns.items():
            if column_name not in operation_columns:
                self._conn.execute(
                    f"""
                    ALTER TABLE operations
                    ADD COLUMN {column_name} {column_type}
                    """
                )

        identity_columns = {
            "stored_device": "INTEGER",
            "stored_inode": "INTEGER",
            "stored_size": "INTEGER",
            "stored_mtime_ns": "INTEGER",
            "original_device": "INTEGER",
            "original_inode": "INTEGER",
            "original_size": "INTEGER",
            "original_mtime_ns": "INTEGER",
        }

        for column_name, column_type in identity_columns.items():
            if column_name not in operation_columns:
                self._conn.execute(
                    f"""
                    ALTER TABLE operations
                    ADD COLUMN {column_name} {column_type}
                    """
                )

        if "conflict_policy" not in operation_columns:
            self._conn.execute(
                """
                ALTER TABLE operations
                ADD COLUMN conflict_policy TEXT
                """
            )

        # Cette conversion ne concerne que les bases historiques auxquelles
        # la colonne status vient d'être ajoutée. Sur une base actuelle, une
        # divergence status/undone doit rester observable et fail-closed.
        if "status" not in batch_columns:
            self._conn.execute(
                """
                UPDATE batches
                SET status = ?
                WHERE undone = 1
                  AND status != ?
                """,
                (
                    BatchStatus.UNDONE.value,
                    BatchStatus.UNDONE.value,
                ),
            )

    def start_batch(
        self,
        root: str,
        *,
        planned_count: int = 0,
    ) -> int:
        """Crée un batch RUNNING avec le nombre d'actions prévues."""

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO batches (
                    created_at, root, undone, status, planned_count
                )
                VALUES (?, ?, 0, ?, ?)
                """,
                (
                    datetime.now().isoformat(),
                    root,
                    BatchStatus.RUNNING.value,
                    planned_count,
                ),
            )

            batch_id = cur.lastrowid

        if batch_id is None:
            raise RuntimeError(
                "SQLite n'a pas retourné d'identifiant de batch"
            )

        return int(batch_id)

    def set_batch_status(
        self,
        batch_id: int,
        status: BatchStatus,
    ) -> None:
        """Met à jour explicitement l'état d'un batch."""

        if status is BatchStatus.UNKNOWN:
            raise ValueError("un BatchStatus inconnu ne peut pas être persisté")

        undone = 1 if status is BatchStatus.UNDONE else 0

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE batches
                SET status = ?, undone = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    undone,
                    batch_id,
                ),
            )

    def start_batch_resume(self, batch_id: int) -> None:
        """Réserve atomiquement un batch terminal pour une reprise QUEUED.

        Le compare-and-set empêche deux processus de reprendre simultanément
        le même plan. Les compteurs restent inchangés jusqu'à la clôture de la
        tentative ; un crash laisse un batch RUNNING que la recovery existante
        sait réconcilier sans rejouer d'I/O.
        """

        resumable_statuses = (
            BatchStatus.CANCELLED.value,
            BatchStatus.PARTIAL.value,
            BatchStatus.FAILED.value,
        )
        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE batches
                SET status = ?, undone = 0
                WHERE id = ?
                  AND status IN (?, ?, ?)
                  AND undone = 0
                """,
                (
                    BatchStatus.RUNNING.value,
                    batch_id,
                    *resumable_statuses,
                ),
            )
            if cur.rowcount == 1:
                return

            cur.execute(
                """
                SELECT status, undone
                FROM batches
                WHERE id = ?
                """,
                (batch_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(f"batch d'historique introuvable : {batch_id}")
        raise ValueError(
            "ce batch ne peut pas être repris ou une autre reprise est "
            f"déjà active (état actuel : {row['status']!r})"
        )

    def finish_batch_execution(
        self,
        batch_id: int,
        status: BatchStatus,
        *,
        success_count: int,
        failed_count: int,
        skipped_count: int,
    ) -> None:
        """Persiste atomiquement l'état et le bilan initial d'exécution."""

        if status is BatchStatus.UNKNOWN:
            raise ValueError("un BatchStatus inconnu ne peut pas être persisté")

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE batches
                SET status = ?,
                    undone = 0,
                    success_count = ?,
                    failed_count = ?,
                    skipped_count = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    success_count,
                    failed_count,
                    skipped_count,
                    batch_id,
                ),
            )

    def reconcile_running_batches(self) -> list[int]:
        """Réconcilie les batches RUNNING dont l'état journalisé est certain.

        4E-12A reste volontairement fail-closed :
        - aucune mutation filesystem n'est rejouée ;
        - les opérations PLANNED restent ambiguës et sont laissées RUNNING
          pour une réconciliation filesystem ultérieure (4E-12B) ;
        - seuls les statuts d'exécution terminaux COMPLETED/FAILED sont
          agrégés pour reconstruire l'état du batch.
        """

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                SELECT *
                FROM batches
                WHERE status = ?
                ORDER BY id
                """,
                (BatchStatus.RUNNING.value,),
            )
            rows = cur.fetchall()

        reconciled: list[int] = []

        for row in rows:
            batch = self._row_to_batch(row)
            if (
                not batch.has_actionable_metadata()
                or self.get_batch_consistency_issues(batch.id)
            ):
                continue

            batch_id = batch.id
            planned_count = batch.planned_count
            assert planned_count is not None

            operations = self.get_operations(batch_id)
            statuses = {operation.status for operation in operations}

            if OperationStatus.PLANNED in statuses:
                continue

            if statuses.intersection(
                {OperationStatus.UNDONE, OperationStatus.UNDO_FAILED}
            ):
                continue

            success_count = sum(
                operation.status is OperationStatus.COMPLETED
                for operation in operations
            )
            failed_count = sum(
                operation.status is OperationStatus.FAILED
                for operation in operations
            )
            skipped_count = max(
                0,
                planned_count - success_count - failed_count,
            )

            if (
                success_count == planned_count
                and failed_count == 0
                and skipped_count == 0
            ):
                status = BatchStatus.COMPLETED
            elif success_count > 0:
                status = BatchStatus.PARTIAL
            else:
                status = BatchStatus.FAILED

            self.finish_batch_execution(
                batch_id,
                status,
                success_count=success_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
            )
            reconciled.append(batch_id)

        return reconciled

    def get_batch(
        self,
        batch_id: int,
    ) -> Batch | None:
        """Retourne un batch par identifiant."""

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM batches
                WHERE id = ?
                """,
                (batch_id,),
            )

            row = cur.fetchone()

        if row is None:
            return None

        return self._row_to_batch(row)

    def record_operation(
        self,
        batch_id: int,
        *,
        kind: str,
        original_path: Path,
        stored_path: Path | None,
        size: int,
        category: str,
        status: OperationStatus = OperationStatus.COMPLETED,
        error: str | None = None,
        original_identity: FileIdentity | None = None,
        conflict_policy: str | None = None,
    ) -> int:
        """Enregistre une opération et retourne son identifiant."""

        if (
            not isinstance(kind, str)
            or kind not in _OPERATION_KINDS
        ):
            raise ValueError(
                f"un type d’opération inconnu ne peut pas être "
                f"persisté: {kind!r}"
            )

        if status is OperationStatus.UNKNOWN:
            raise ValueError("un OperationStatus inconnu ne peut pas être persisté")

        original_path_value = _encode_persisted_path(
            original_path,
            optional=False,
            field="original_path",
        )
        stored_path_value = _encode_persisted_path(
            stored_path,
            optional=True,
            field="stored_path",
        )
        size_value = _encode_persisted_size(size)
        category_value = _encode_persisted_category(category)
        original_identity_values = _encode_persisted_identity(
            original_identity,
            prefix="original",
        )
        conflict_policy_value = _encode_persisted_conflict_policy(
            conflict_policy
        )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                INSERT INTO operations (
                    batch_id,
                    kind,
                    original_path,
                    stored_path,
                    size,
                    category,
                    status,
                    error,
                    original_device,
                    original_inode,
                    original_size,
                    original_mtime_ns,
                    conflict_policy
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    kind,
                    original_path_value,
                    stored_path_value,
                    size_value,
                    category_value,
                    status.value,
                    error,
                    *original_identity_values,
                    conflict_policy_value,
                ),
            )

            operation_id = cur.lastrowid

        if operation_id is None:
            raise RuntimeError(
                "SQLite n'a pas retourné d'identifiant d'opération"
            )

        return int(operation_id)

    def record_queued_operations(
        self,
        batch_id: int,
        operations: list[
            tuple[
                str,
                Path,
                Path | None,
                int,
                str,
                FileIdentity | None,
                str,
            ]
        ],
    ) -> list[int]:
        """Persiste atomiquement toutes les intentions QUEUED d'un lot."""

        prepared: list[
            tuple[
                str,
                str,
                str | None,
                int,
                str,
                int | str | None,
                int | str | None,
                int | str | None,
                int | str | None,
                str,
            ]
        ] = []
        for (
            kind,
            original_path,
            stored_path,
            size,
            category,
            original_identity,
            conflict_policy,
        ) in operations:
            if not isinstance(kind, str) or kind not in _OPERATION_KINDS:
                raise ValueError(
                    f"un type d’opération inconnu ne peut pas être "
                    f"persisté: {kind!r}"
                )
            prepared.append(
                (
                    kind,
                    _encode_persisted_path(
                        original_path,
                        optional=False,
                        field="original_path",
                    ),
                    _encode_persisted_path(
                        stored_path,
                        optional=True,
                        field="stored_path",
                    ),
                    _encode_persisted_size(size),
                    _encode_persisted_category(category),
                    *_encode_persisted_identity(
                        original_identity,
                        prefix="original",
                    ),
                    _encode_persisted_conflict_policy(conflict_policy),
                )
            )

        if not prepared:
            return []

        operation_ids: list[int] = []
        with self._cursor(write=True) as cur:
            for (
                kind,
                original_path,
                stored_path,
                size,
                category,
                original_device,
                original_inode,
                original_size,
                original_mtime_ns,
                conflict_policy,
            ) in prepared:
                cur.execute(
                    """
                    INSERT INTO operations (
                        batch_id,
                        kind,
                        original_path,
                        stored_path,
                        size,
                        category,
                        status,
                        error,
                        original_device,
                        original_inode,
                        original_size,
                        original_mtime_ns,
                        conflict_policy
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        kind,
                        original_path,
                        stored_path,
                        size,
                        category,
                        OperationStatus.QUEUED.value,
                        original_device,
                        original_inode,
                        original_size,
                        original_mtime_ns,
                        conflict_policy,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError(
                        "SQLite n'a pas retourné d'identifiant d'opération"
                    )
                operation_ids.append(int(cur.lastrowid))

        return operation_ids

    def start_queued_operation(
        self,
        operation_id: int,
        *,
        stored_path: Path | None,
    ) -> None:
        """Marque une intention QUEUED comme démarrée avant toute I/O.

        Le chemin de stockage exact n'est fixé qu'à cet instant : une
        politique RENAME dépend de l'état live du répertoire destination et
        un chemin de corbeille est alloué juste avant la mutation.
        """

        stored_path_value = _encode_persisted_path(
            stored_path,
            optional=True,
            field="stored_path",
        )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET status = ?, stored_path = ?, error = NULL
                WHERE id = ? AND status = ?
                """,
                (
                    OperationStatus.PLANNED.value,
                    stored_path_value,
                    operation_id,
                    OperationStatus.QUEUED.value,
                ),
            )

            if cur.rowcount == 1:
                return

            cur.execute(
                """
                SELECT status
                FROM operations
                WHERE id = ?
                """,
                (operation_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(
                f"opération d'historique introuvable : {operation_id}"
            )
        raise ValueError(
            "seule une opération QUEUED peut être démarrée "
            f"(état actuel : {row['status']!r})"
        )

    @staticmethod
    def _validate_recovery_resolution_metadata(
        *,
        recovery_state: PlannedRecoveryState | None,
        resolution_kind: RecoveryResolutionKind | None,
        resolved_at: datetime | None,
    ) -> None:
        """Valide la cohérence structurelle et sémantique d'une résolution."""

        has_resolution_kind = resolution_kind is not None
        has_resolved_at = resolved_at is not None

        if has_resolution_kind != has_resolved_at:
            raise ValueError(
                "resolution_kind et resolved_at doivent être renseignés ensemble"
            )

        if resolution_kind is None:
            return

        if recovery_state is None:
            raise ValueError(
                "une résolution recovery exige un recovery_state"
            )

        if recovery_state is PlannedRecoveryState.UNKNOWN:
            raise ValueError(
                "un recovery_state inconnu ne peut pas être persisté"
            )

        if resolution_kind is RecoveryResolutionKind.UNKNOWN:
            raise ValueError(
                "un resolution_kind inconnu ne peut pas être persisté"
            )

        allowed_states = {
            RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION: {
                PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
            },
            RecoveryResolutionKind.MANUAL_NO_FILE_ACTION: {
                PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
                PlannedRecoveryState.AMBIGUOUS,
            },
        }
        if recovery_state not in allowed_states[resolution_kind]:
            raise ValueError(
                "resolution_kind incompatible avec recovery_state"
            )

    def _validate_recovery_audit_update(
        self,
        operation_id: int,
        *,
        recovery_state: PlannedRecoveryState | None,
        resolution_kind: RecoveryResolutionKind | None,
        resolved_at: datetime | None,
    ) -> None:
        """Valide les invariants d'écriture de l'audit recovery.

        Les anciennes lignes SQLite restent lisibles même si elles sont
        incomplètes ; ces règles s'appliquent uniquement aux nouvelles écritures.
        """

        self._validate_recovery_resolution_metadata(
            recovery_state=recovery_state,
            resolution_kind=resolution_kind,
            resolved_at=resolved_at,
        )

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT status
                FROM operations
                WHERE id = ?
                """,
                (operation_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise ValueError(f"opération d'historique introuvable : {operation_id}")

        if (
            OperationStatus(row["status"]) is OperationStatus.PLANNED
            and resolution_kind is not None
        ):
            raise ValueError(
                "une opération PLANNED ne peut pas être marquée comme résolue"
            )

    def set_operation_status(
        self,
        operation_id: int,
        status: OperationStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Met à jour l'état et l'erreur éventuelle d'une opération."""

        if status is OperationStatus.UNKNOWN:
            raise ValueError("un OperationStatus inconnu ne peut pas être persisté")

        if status is OperationStatus.PLANNED:
            with self._cursor(write=True) as cur:
                cur.execute(
                    """
                    SELECT resolution_kind, resolved_at
                    FROM operations
                    WHERE id = ?
                    """,
                    (operation_id,),
                )
                row = cur.fetchone()

            if row is None:
                raise ValueError(
                    f"opération d'historique introuvable : {operation_id}"
                )
            if row["resolution_kind"] is not None or row["resolved_at"] is not None:
                raise ValueError(
                    "une opération recovery résolue ne peut pas revenir à PLANNED"
                )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET status = ?, error = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    error,
                    operation_id,
                ),
            )

    def set_operation_recovery_audit(
        self,
        operation_id: int,
        *,
        recovery_state: PlannedRecoveryState | None,
        resolution_kind: RecoveryResolutionKind | None = None,
        resolved_at: datetime | None = None,
    ) -> None:
        """Persiste les métadonnées structurées de recovery d'une opération."""

        if recovery_state is PlannedRecoveryState.UNKNOWN:
            raise ValueError(
                "un recovery_state inconnu ne peut pas être persisté"
            )

        if resolution_kind is RecoveryResolutionKind.UNKNOWN:
            raise ValueError(
                "un resolution_kind inconnu ne peut pas être persisté"
            )

        self._validate_recovery_audit_update(
            operation_id,
            recovery_state=recovery_state,
            resolution_kind=resolution_kind,
            resolved_at=resolved_at,
        )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET recovery_state = ?,
                    resolution_kind = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                (
                    (recovery_state.value if recovery_state is not None else None),
                    (resolution_kind.value if resolution_kind is not None else None),
                    resolved_at.isoformat() if resolved_at is not None else None,
                    operation_id,
                ),
            )

    def annotate_operation_recovery(
        self,
        operation_id: int,
        *,
        error: str,
        recovery_state: PlannedRecoveryState,
    ) -> None:
        """Annote atomiquement une opération PLANNED encore non résolue.

        Le statut reste PLANNED ; error et recovery_state sont persistés dans le
        même UPDATE. Toute métadonnée terminale de résolution est explicitement
        laissée à NULL.
        """

        if recovery_state not in {
            PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
            PlannedRecoveryState.AMBIGUOUS,
        }:
            raise ValueError(
                "recovery_state invalide pour une annotation unresolved"
            )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET error = ?,
                    recovery_state = ?,
                    resolution_kind = NULL,
                    resolved_at = NULL
                WHERE id = ?
                  AND status = ?
                """,
                (
                    error,
                    recovery_state.value,
                    operation_id,
                    OperationStatus.PLANNED.value,
                ),
            )

            if cur.rowcount == 1:
                return

            cur.execute(
                """
                SELECT status
                FROM operations
                WHERE id = ?
                """,
                (operation_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise ValueError(
                    f"opération d'historique introuvable : {operation_id}"
                )

            raise ValueError(
                "seule une opération PLANNED peut être annotée comme unresolved"
            )

    def resolve_operation_recovery(
        self,
        operation_id: int,
        *,
        error: str,
        recovery_state: PlannedRecoveryState,
        resolution_kind: RecoveryResolutionKind,
        resolved_at: datetime,
    ) -> None:
        """Clôture atomiquement une opération PLANNED après recovery.

        Le statut FAILED et toutes les métadonnées de résolution sont publiés
        dans le même UPDATE SQLite afin d'éliminer la fenêtre de crash entre
        changement de statut et audit structuré.
        """

        self._validate_recovery_resolution_metadata(
            recovery_state=recovery_state,
            resolution_kind=resolution_kind,
            resolved_at=resolved_at,
        )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET status = ?,
                    error = ?,
                    recovery_state = ?,
                    resolution_kind = ?,
                    resolved_at = ?
                WHERE id = ?
                  AND status = ?
                """,
                (
                    OperationStatus.FAILED.value,
                    error,
                    recovery_state.value,
                    resolution_kind.value,
                    resolved_at.isoformat(),
                    operation_id,
                    OperationStatus.PLANNED.value,
                ),
            )

            if cur.rowcount == 1:
                return

            cur.execute(
                """
                SELECT status
                FROM operations
                WHERE id = ?
                """,
                (operation_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise ValueError(
                    f"opération d'historique introuvable : {operation_id}"
                )

            raise ValueError(
                "seule une opération PLANNED peut être clôturée par recovery"
            )

    def set_operation_stored_identity(
        self,
        operation_id: int,
        identity: FileIdentity,
    ) -> None:
        """Persiste l'identité du fichier effectivement stocké/publié."""

        encoded_identity = (
            _encode_persisted_identity_component(
                identity.device,
                field="stored_device",
            ),
            _encode_persisted_identity_component(
                identity.inode,
                field="stored_inode",
            ),
            _encode_persisted_identity_component(
                identity.size,
                field="stored_size",
            ),
            _encode_persisted_identity_component(
                identity.mtime_ns,
                field="stored_mtime_ns",
            ),
        )

        with self._cursor(write=True) as cur:
            cur.execute(
                """
                UPDATE operations
                SET stored_device = ?,
                    stored_inode = ?,
                    stored_size = ?,
                    stored_mtime_ns = ?
                WHERE id = ?
                """,
                (
                    *encoded_identity,
                    operation_id,
                ),
            )

    def get_batch_consistency_issues(
        self,
        batch_id: int,
    ) -> tuple[str, ...]:
        """Classe les incohérences entre un batch et ses opérations.

        La classification est strictement read-only : aucune valeur persistée
        n'est corrigée et l'actionnabilité reste inchangée à ce jalon.
        """

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT status, undone
                FROM batches
                WHERE id = ?
                """,
                (batch_id,),
            )
            persisted_state = cur.fetchone()

        if persisted_state is None:
            return ("missing_batch",)

        batch = self.get_batch(batch_id)
        assert batch is not None

        if (
            batch.status is BatchStatus.UNKNOWN
            or batch.status_raw is not None
            or batch.undone is None
            or batch.undone_raw is not None
            or batch.planned_count is None
            or batch.planned_count_raw is not None
            or batch.success_count is None
            or batch.success_count_raw is not None
            or batch.failed_count is None
            or batch.failed_count_raw is not None
            or batch.skipped_count is None
            or batch.skipped_count_raw is not None
        ):
            return ()

        operations = self.get_operations(batch_id)
        issues: list[str] = []

        # Comparer les colonnes persistées avant la normalisation du modèle :
        # undone=1 rend l'état applicatif effectif UNDONE et masquerait sinon
        # une contradiction telle que status='completed' ou status='running'.
        persisted_status_is_undone = (
            persisted_state["status"] == BatchStatus.UNDONE.value
        )
        if persisted_status_is_undone != bool(persisted_state["undone"]):
            issues.append("undo_state_mismatch")

        if batch.status in {BatchStatus.RUNNING, BatchStatus.UNDOING}:
            return tuple(issues)

        operation_count = len(operations)
        queued_count = sum(
            operation.status is OperationStatus.QUEUED
            for operation in operations
        )
        result_count = batch.success_count + batch.failed_count
        if (
            queued_count > batch.skipped_count
            or operation_count + batch.skipped_count - queued_count
            != batch.planned_count
        ):
            issues.append("operation_count_mismatch")
        if (
            result_count != operation_count - queued_count
            or result_count + batch.skipped_count != batch.planned_count
        ):
            issues.append("result_count_mismatch")
        if any(
            operation.status is OperationStatus.PLANNED
            for operation in operations
        ):
            issues.append("pending_operation_in_terminal_batch")

        return tuple(issues)

    def list_batches(
        self,
        limit: int = 20,
    ) -> list[Batch]:
        """Retourne les batches les plus récents."""

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM batches
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )

            rows = cur.fetchall()

        return [
            self._row_to_batch(row)
            for row in rows
        ]

    def latest_undoable_batch_id(self) -> int | None:
        """Retourne le batch le plus récent encore annulable ou réessayable.

        Les batches RUNNING/UNDOING sont volontairement exclus : un état laissé
        par un crash doit être réconcilié séparément avant de devenir une cible
        d'Undo. L'ordre par identifiant impose une sémantique LIFO stable.
        """

        undoable_batch_statuses = (
            BatchStatus.COMPLETED.value,
            BatchStatus.PARTIAL.value,
            BatchStatus.CANCELLED.value,
            BatchStatus.UNDO_PARTIAL.value,
            BatchStatus.UNDO_FAILED.value,
        )
        undoable_operation_statuses = (
            OperationStatus.COMPLETED.value,
            OperationStatus.UNDO_FAILED.value,
        )
        undoable_operation_kinds = ("copy", "move", "delete", "rmdir")

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT b.*
                FROM batches AS b
                WHERE b.status IN (?, ?, ?, ?, ?)
                  AND EXISTS (
                      SELECT 1
                      FROM operations AS o
                      WHERE o.batch_id = b.id
                        AND o.status IN (?, ?)
                        AND o.kind IN (?, ?, ?, ?)
                        AND typeof(o.original_path) = 'text'
                        AND o.original_path <> ''
                        AND substr(o.original_path, 1, 1) = '/'
                        AND instr(o.original_path, char(0)) = 0
                        AND typeof(o.stored_path) = 'text'
                        AND o.stored_path <> ''
                        AND substr(o.stored_path, 1, 1) = '/'
                        AND instr(o.stored_path, char(0)) = 0
                        AND typeof(o.size) = 'integer'
                        AND o.size >= 0
                        AND typeof(o.category) = 'text'
                        AND trim(o.category) <> ''
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM operations AS invalid_o
                      WHERE invalid_o.batch_id = b.id
                        AND invalid_o.status IN (?, ?)
                        AND (
                            invalid_o.kind NOT IN (?, ?, ?, ?)
                            OR typeof(invalid_o.original_path) <> 'text'
                            OR invalid_o.original_path = ''
                            OR substr(invalid_o.original_path, 1, 1) <> '/'
                            OR instr(invalid_o.original_path, char(0)) <> 0
                            OR typeof(invalid_o.stored_path) <> 'text'
                            OR invalid_o.stored_path = ''
                            OR substr(invalid_o.stored_path, 1, 1) <> '/'
                            OR instr(invalid_o.stored_path, char(0)) <> 0
                            OR typeof(invalid_o.size) <> 'integer'
                            OR invalid_o.size < 0
                            OR typeof(invalid_o.category) <> 'text'
                            OR trim(invalid_o.category) = ''
                        )
                  )
                ORDER BY b.id DESC
                """,
                (
                    *undoable_batch_statuses,
                    *undoable_operation_statuses,
                    *undoable_operation_kinds,
                    *undoable_operation_statuses,
                    *undoable_operation_kinds,
                ),
            )
            rows = cur.fetchall()

        for row in rows:
            batch = self._row_to_batch(row)
            if (
                batch.has_actionable_metadata()
                and not self.get_batch_consistency_issues(batch.id)
            ):
                return batch.id
        return None

    def get_operations(
        self,
        batch_id: int,
    ) -> list[Operation]:
        """Retourne les opérations d'un batch dans l'ordre d'exécution."""

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM operations
                WHERE batch_id = ?
                ORDER BY id
                """,
                (batch_id,),
            )

            rows = cur.fetchall()

        return [
            self._row_to_operation(row)
            for row in rows
        ]

    def mark_undone(
        self,
        batch_id: int,
    ) -> None:
        """Marque un batch comme entièrement annulé.

        Méthode conservée pour compatibilité avec le code existant.
        """

        self.set_batch_status(
            batch_id,
            BatchStatus.UNDONE,
        )

    @staticmethod
    def _row_to_batch(
        row: sqlite3.Row,
    ) -> Batch:
        """Convertit une ligne SQLite en Batch."""

        created_at_value = row["created_at"]
        created_at = None
        created_at_raw = None
        try:
            created_at = datetime.fromisoformat(created_at_value)
        except (TypeError, ValueError):
            created_at_raw = created_at_value

        status_value = row["status"]
        status = BatchStatus(status_value)

        root, root_raw = _decode_persisted_batch_root(row["root"])
        undone, undone_raw = _decode_persisted_undone(row["undone"])
        planned_count, planned_count_raw = _decode_persisted_count(
            row["planned_count"]
        )
        success_count, success_count_raw = _decode_persisted_count(
            row["success_count"]
        )
        failed_count, failed_count_raw = _decode_persisted_count(
            row["failed_count"]
        )
        skipped_count, skipped_count_raw = _decode_persisted_count(
            row["skipped_count"]
        )

        return Batch(
            id=row["id"],
            created_at=created_at,
            root=root,
            undone=undone,
            status=status,
            created_at_raw=created_at_raw,
            status_raw=(
                status_value
                if status is BatchStatus.UNKNOWN
                else None
            ),
            root_raw=root_raw,
            undone_raw=undone_raw,
            planned_count=planned_count,
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            planned_count_raw=planned_count_raw,
            success_count_raw=success_count_raw,
            failed_count_raw=failed_count_raw,
            skipped_count_raw=skipped_count_raw,
        )

    @staticmethod
    def _row_to_operation(
        row: sqlite3.Row,
    ) -> Operation:
        """Convertit une ligne SQLite en Operation."""

        recovery_state_value = row["recovery_state"]
        recovery_state = (
            PlannedRecoveryState(recovery_state_value)
            if recovery_state_value
            else None
        )
        resolution_kind_value = row["resolution_kind"]
        resolution_kind = (
            RecoveryResolutionKind(resolution_kind_value)
            if resolution_kind_value
            else None
        )

        status_value = row["status"]
        status = OperationStatus(status_value)

        resolved_at_value = row["resolved_at"]
        resolved_at = None
        resolved_at_raw = None
        if resolved_at_value:
            try:
                resolved_at = datetime.fromisoformat(resolved_at_value)
            except (TypeError, ValueError):
                resolved_at_raw = resolved_at_value

        stored_identity_values = (
            row["stored_device"],
            row["stored_inode"],
            row["stored_size"],
            row["stored_mtime_ns"],
        )
        stored_identity, stored_identity_raw = _decode_persisted_identity(
            stored_identity_values
        )
        original_identity_values = (
            row["original_device"],
            row["original_inode"],
            row["original_size"],
            row["original_mtime_ns"],
        )
        original_identity, original_identity_raw = (
            _decode_persisted_identity(original_identity_values)
        )
        conflict_policy, conflict_policy_raw = (
            _decode_persisted_conflict_policy(row["conflict_policy"])
        )

        original_path, original_path_raw = _decode_persisted_path(
            row["original_path"],
            optional=False,
        )
        stored_path, stored_path_raw = _decode_persisted_path(
            row["stored_path"],
            optional=True,
        )
        size, size_raw = _decode_persisted_size(row["size"])
        category, category_raw = _decode_persisted_category(
            row["category"]
        )

        return Operation(
            id=row["id"],
            batch_id=row["batch_id"],
            kind=row["kind"],
            original_path=original_path,
            stored_path=stored_path,
            size=size,
            category=category,
            status=status,
            status_raw=(
                status_value
                if status is OperationStatus.UNKNOWN
                else None
            ),
            error=row["error"],
            recovery_state=recovery_state,
            recovery_state_raw=(
                recovery_state_value
                if recovery_state is PlannedRecoveryState.UNKNOWN
                else None
            ),
            resolution_kind=resolution_kind,
            resolution_kind_raw=(
                resolution_kind_value
                if resolution_kind is RecoveryResolutionKind.UNKNOWN
                else None
            ),
            resolved_at=resolved_at,
            resolved_at_raw=resolved_at_raw,
            stored_identity=stored_identity,
            stored_identity_raw=stored_identity_raw,
            original_identity=original_identity,
            original_identity_raw=original_identity_raw,
            conflict_policy=conflict_policy,
            conflict_policy_raw=conflict_policy_raw,
            original_path_raw=original_path_raw,
            stored_path_raw=stored_path_raw,
            size_raw=size_raw,
            category_raw=category_raw,
        )
