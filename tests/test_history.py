"""Tests de HistoryStore et de la migration SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from file_janitor.models import FileIdentity
from file_janitor.storage.history import (
    PlannedRecoveryState,
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def test_start_batch_is_running(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id = store.start_batch(
        str(tmp_path)
    )

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.id == batch_id
    assert batch.root == str(tmp_path)
    assert batch.status is BatchStatus.RUNNING
    assert batch.undone is False

    store.close()


def test_record_operation_defaults_to_completed(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id = store.start_batch(
        str(tmp_path)
    )

    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "dest" / "source.txt",
        size=123,
        category="to_sort",
    )

    operations = store.get_operations(
        batch_id
    )

    assert len(operations) == 1

    operation = operations[0]

    assert operation.id == operation_id
    assert operation.status is OperationStatus.COMPLETED
    assert operation.error is None

    store.close()


def test_operation_status_can_change(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id = store.start_batch(
        str(tmp_path)
    )

    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=10,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    store.set_operation_status(
        operation_id,
        OperationStatus.FAILED,
        error="permission denied",
    )

    operation = store.get_operations(
        batch_id
    )[0]

    assert operation.status is OperationStatus.FAILED
    assert operation.error == "permission denied"

    store.close()


def test_batch_status_can_change(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id = store.start_batch(
        str(tmp_path)
    )

    store.set_batch_status(
        batch_id,
        BatchStatus.COMPLETED,
    )

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED
    assert batch.undone is False

    store.close()


def test_mark_undone_sets_new_status_and_legacy_flag(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id = store.start_batch(
        str(tmp_path)
    )

    store.mark_undone(batch_id)

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.UNDONE
    assert batch.undone is True

    store.close()


def test_list_batches_returns_newest_first(
    tmp_path: Path,
) -> None:
    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    first = store.start_batch("/first")
    second = store.start_batch("/second")

    batches = store.list_batches()

    assert [batch.id for batch in batches] == [
        second,
        first,
    ]

    store.close()


def test_old_database_is_migrated(
    tmp_path: Path,
) -> None:
    """Une base pré-v0.2 doit être mise à niveau sans perdre ses données."""

    db_path = tmp_path / "old-history.db"

    conn = sqlite3.connect(db_path)

    conn.executescript(
        """
        CREATE TABLE batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            root TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES batches(id),
            kind TEXT NOT NULL,
            original_path TEXT NOT NULL,
            stored_path TEXT,
            size INTEGER NOT NULL,
            category TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        INSERT INTO batches (
            created_at,
            root,
            undone
        )
        VALUES (?, ?, ?)
        """,
        (
            "2026-01-01T12:00:00",
            "/old/root",
            0,
        ),
    )

    batch_id = conn.execute(
        "SELECT id FROM batches"
    ).fetchone()[0]

    conn.execute(
        """
        INSERT INTO operations (
            batch_id,
            kind,
            original_path,
            stored_path,
            size,
            category
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            "move",
            "/old/root/a.txt",
            "/old/root/Text/a.txt",
            42,
            "to_sort",
        ),
    )

    conn.commit()
    conn.close()

    # L'ouverture doit effectuer la migration.
    store = HistoryStore(db_path=db_path)

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.root == "/old/root"
    assert batch.status is BatchStatus.COMPLETED
    assert batch.undone is False

    operations = store.get_operations(
        batch_id
    )

    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].error is None
    assert operations[0].stored_identity is None

    columns = {
        row[1]
        for row in store._conn.execute(
            "PRAGMA table_info(operations)"
        ).fetchall()
    }
    assert {
        "stored_device",
        "stored_inode",
        "stored_size",
        "stored_mtime_ns",
    } <= columns

    store.close()


def test_old_undone_batch_is_migrated_to_undone(
    tmp_path: Path,
) -> None:
    """Le booléen historique undone doit être converti en statut UNDONE."""

    db_path = tmp_path / "old-undone.db"

    conn = sqlite3.connect(db_path)

    conn.executescript(
        """
        CREATE TABLE batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            root TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES batches(id),
            kind TEXT NOT NULL,
            original_path TEXT NOT NULL,
            stored_path TEXT,
            size INTEGER NOT NULL,
            category TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        INSERT INTO batches (
            created_at,
            root,
            undone
        )
        VALUES (?, ?, ?)
        """,
        (
            "2026-01-01T12:00:00",
            "/old/root",
            1,
        ),
    )

    batch_id = conn.execute(
        "SELECT id FROM batches"
    ).fetchone()[0]

    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db_path)

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.UNDONE
    assert batch.undone is True

    store.close()

def test_operation_stored_identity_round_trip(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path))

    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "copy.txt",
        size=42,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    identity = FileIdentity(
        device=11,
        inode=22,
        size=42,
        mtime_ns=123456789,
    )
    store.set_operation_stored_identity(operation_id, identity)

    operation = store.get_operations(batch_id)[0]

    assert operation.stored_identity == identity
    store.close()


def test_partial_stored_identity_is_loaded_fail_closed_with_raw_values(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path))

    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "copy.txt",
        size=42,
        category="to_sort",
    )

    store._conn.execute(
        """
        UPDATE operations
        SET stored_device = ?,
            stored_inode = ?
        WHERE id = ?
        """,
        (11, 22, operation_id),
    )
    store._conn.commit()

    operation = store.get_operations(batch_id)[0]

    assert operation.stored_identity is None
    assert operation.stored_identity_raw == (
        11,
        22,
        None,
        None,
    )
    store.close()

def test_reconcile_running_batch_with_completed_operation_marks_partial(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"

    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(
        str(tmp_path),
        planned_count=2,
    )
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "a.txt",
        stored_path=tmp_path / "sorted" / "a.txt",
        size=10,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    store.close()

    store = HistoryStore(db_path=db_path)

    reconciled = store.reconcile_running_batches()

    batch = store.get_batch(batch_id)
    operations = store.get_operations(batch_id)

    assert reconciled == [batch_id]
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 1

    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED

    store.close()

def test_operation_recovery_audit_round_trip(tmp_path: Path) -> None:
    from file_janitor.storage.history import RecoveryResolutionKind

    from datetime import datetime, timezone

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=123,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.set_operation_status(operation_id, OperationStatus.FAILED)
    resolved_at = datetime(2026, 9, 13, 10, 30, 45, tzinfo=timezone.utc)

    store.set_operation_recovery_audit(
        operation_id,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        resolved_at=resolved_at,
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    assert operation.resolved_at == resolved_at
    store.close()


def test_operation_recovery_audit_defaults_to_none(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=123,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()


def test_old_database_migrates_recovery_audit_columns(tmp_path: Path) -> None:
    db = tmp_path / "old-history.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            root TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            planned_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES batches(id),
            kind TEXT NOT NULL,
            original_path TEXT NOT NULL,
            stored_path TEXT,
            size INTEGER NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            error TEXT,
            stored_device INTEGER,
            stored_inode INTEGER,
            stored_size INTEGER,
            stored_mtime_ns INTEGER
        );
        """
    )
    conn.execute(
        """
        INSERT INTO batches (
            created_at, root, undone, status, planned_count
        )
        VALUES (?, ?, 0, 'completed', 1)
        """,
        ("2026-09-13T10:00:00", str(tmp_path)),
    )
    conn.execute(
        """
        INSERT INTO operations (
            batch_id, kind, original_path, stored_path, size, category,
            status, error
        )
        VALUES (1, 'move', ?, ?, 12, 'to_sort', 'failed', 'legacy')
        """,
        (str(tmp_path / "source.txt"), str(tmp_path / "destination.txt")),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(1)[0]

    columns = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(operations)").fetchall()
    }
    assert {"recovery_state", "resolution_kind", "resolved_at"} <= columns
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()

def test_existing_text_resolution_kind_loads_as_enum(tmp_path: Path) -> None:
    import sqlite3

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
    )
    store.close()

    connection = sqlite3.connect(db)
    connection.execute(
        """
        UPDATE operations
        SET resolution_kind = ?
        WHERE id = ?
        """,
        ("automatic_no_publication", operation_id),
    )
    connection.commit()
    connection.close()

    reopened = HistoryStore(db_path=db)
    operation = reopened.get_operations(batch_id)[0]
    assert (
        operation.resolution_kind
        is RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION
    )
    reopened.close()

def test_recovery_audit_requires_resolution_kind_and_timestamp_together(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
    )

    with pytest.raises(ValueError, match="doivent être renseignés ensemble"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        )

    with pytest.raises(ValueError, match="doivent être renseignés ensemble"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolved_at=datetime.now(timezone.utc),
        )
    store.close()


def test_recovery_resolution_requires_recovery_state(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
    )
    store.set_operation_status(operation_id, OperationStatus.FAILED)

    with pytest.raises(ValueError, match="exige un recovery_state"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=None,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
            resolved_at=datetime.now(timezone.utc),
        )
    store.close()


def test_planned_operation_cannot_be_marked_recovery_resolved(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(ValueError, match="PLANNED"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
            resolved_at=datetime.now(timezone.utc),
        )
    store.close()


def test_resolved_recovery_operation_cannot_return_to_planned(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
    )
    store.set_operation_status(operation_id, OperationStatus.FAILED)
    store.set_operation_recovery_audit(
        operation_id,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
        resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
        resolved_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError, match="ne peut pas revenir à PLANNED"):
        store.set_operation_status(operation_id, OperationStatus.PLANNED)

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    assert (
        operation.resolution_kind
        is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    )
    store.close()

def test_recovery_resolution_kind_must_match_recovery_state(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
    )

    with pytest.raises(ValueError, match="incompatible"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION,
            resolved_at=datetime.now(timezone.utc),
        )

    with pytest.raises(ValueError, match="incompatible"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
            resolved_at=datetime.now(timezone.utc),
        )
    store.close()


def test_resolve_operation_recovery_persists_terminal_state_atomically(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    resolved_at = datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc)

    store.resolve_operation_recovery(
        operation_id,
        error="recovery resolved",
        recovery_state=PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
        resolution_kind=RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION,
        resolved_at=resolved_at,
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    assert operation.error == "recovery resolved"
    assert operation.recovery_state is PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
    assert (
        operation.resolution_kind
        is RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION
    )
    assert operation.resolved_at == resolved_at
    store.close()


def test_resolve_operation_recovery_rejects_non_planned_without_mutation(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )

    with pytest.raises(ValueError, match="PLANNED"):
        store.resolve_operation_recovery(
            operation_id,
            error="must not persist",
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
            resolved_at=datetime.now(timezone.utc),
        )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.COMPLETED
    assert operation.error is None
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()


def test_resolve_operation_recovery_rejects_invalid_semantics_before_write(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(ValueError, match="incompatible"):
        store.resolve_operation_recovery(
            operation_id,
            error="must not persist",
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION,
            resolved_at=datetime.now(timezone.utc),
        )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is None
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()

def test_annotate_operation_recovery_persists_unresolved_state_atomically(
    tmp_path: Path,
) -> None:
    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    store.annotate_operation_recovery(
        operation_id,
        error="unresolved",
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.error == "unresolved"
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()


def test_annotate_operation_recovery_rejects_terminal_operation_without_mutation(
    tmp_path: Path,
) -> None:
    import pytest

    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
    )

    with pytest.raises(ValueError, match="PLANNED"):
        store.annotate_operation_recovery(
            operation_id,
            error="must not persist",
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
        )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    assert operation.error is None
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()


def test_annotate_operation_recovery_rejects_invalid_unresolved_state(
    tmp_path: Path,
) -> None:
    import pytest

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(ValueError, match="invalide"):
        store.annotate_operation_recovery(
            operation_id,
            error="invalid",
            recovery_state=PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
        )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is None
    assert operation.recovery_state is None
    store.close()

def test_existing_text_recovery_state_loads_as_enum(tmp_path: Path) -> None:
    import sqlite3

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE operations SET recovery_state = ? WHERE id = ?",
        ("ambiguous", operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    store.close()

def test_unknown_persisted_recovery_metadata_loads_fail_closed(tmp_path: Path) -> None:
    import sqlite3

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?
        WHERE id = ?
        """,
        ("future_recovery_state", "future_resolution_kind", operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.UNKNOWN
    assert operation.resolution_kind is RecoveryResolutionKind.UNKNOWN
    store.close()


def test_unknown_recovery_metadata_sentinels_are_read_only(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    import pytest

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(ValueError, match="recovery_state inconnu"):
        store.set_operation_recovery_audit(
            operation_id,
            recovery_state=PlannedRecoveryState.UNKNOWN,
        )

    with pytest.raises(ValueError, match="resolution_kind inconnu"):
        store.resolve_operation_recovery(
            operation_id,
            error="invalid",
            recovery_state=PlannedRecoveryState.AMBIGUOUS,
            resolution_kind=RecoveryResolutionKind.UNKNOWN,
            resolved_at=datetime.now(timezone.utc),
        )

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()

def test_unknown_persisted_recovery_metadata_preserves_raw_values(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?
        WHERE id = ?
        """,
        ("future_recovery_state", "future_resolution_kind", operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.UNKNOWN
    assert operation.recovery_state_raw == "future_recovery_state"
    assert operation.resolution_kind is RecoveryResolutionKind.UNKNOWN
    assert operation.resolution_kind_raw == "future_resolution_kind"
    store.close()

def test_malformed_persisted_resolved_at_loads_fail_closed_and_preserves_raw(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
        RecoveryResolutionKind,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.FAILED,
        error="resolved",
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET recovery_state = ?,
            resolution_kind = ?,
            resolved_at = ?
        WHERE id = ?
        """,
        (
            PlannedRecoveryState.AMBIGUOUS.value,
            RecoveryResolutionKind.MANUAL_NO_FILE_ACTION.value,
            "not-a-valid-timestamp",
            operation_id,
        ),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    assert operation.resolved_at is None
    assert operation.resolved_at_raw == "not-a-valid-timestamp"
    store.close()

def test_unknown_core_statuses_and_malformed_batch_timestamp_load_fail_closed(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.storage.history import (
        BatchStatus,
        HistoryStore,
        OperationStatus,
    )

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE batches SET created_at = ?, status = ? WHERE id = ?",
        ("not-a-timestamp", "future_batch_status", batch_id),
    )
    conn.execute(
        "UPDATE operations SET status = ? WHERE id = ?",
        ("future_operation_status", operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    batch = store.list_batches()[0]
    operation = store.get_operations(batch_id)[0]

    assert batch.created_at is None
    assert batch.created_at_raw == "not-a-timestamp"
    assert batch.status is BatchStatus.UNKNOWN
    assert batch.status_raw == "future_batch_status"

    assert operation.status is OperationStatus.UNKNOWN
    assert operation.status_raw == "future_operation_status"
    store.close()


def test_unknown_core_status_sentinels_cannot_be_persisted(tmp_path: Path) -> None:
    import pytest

    from file_janitor.storage.history import (
        BatchStatus,
        HistoryStore,
        OperationStatus,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)

    with pytest.raises(ValueError, match="BatchStatus inconnu"):
        store.set_batch_status(batch_id, BatchStatus.UNKNOWN)

    with pytest.raises(ValueError, match="BatchStatus inconnu"):
        store.finish_batch_execution(
            batch_id,
            BatchStatus.UNKNOWN,
            success_count=0,
            failed_count=0,
            skipped_count=0,
        )

    with pytest.raises(ValueError, match="OperationStatus inconnu"):
        store.record_operation(
            batch_id,
            kind="move",
            original_path=tmp_path / "source.txt",
            stored_path=tmp_path / "destination.txt",
            size=1,
            category="to_sort",
            status=OperationStatus.UNKNOWN,
        )

    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source-2.txt",
        stored_path=tmp_path / "destination-2.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(ValueError, match="OperationStatus inconnu"):
        store.set_operation_status(operation_id, OperationStatus.UNKNOWN)

    store.close()

def test_invalid_stored_identity_component_is_loaded_fail_closed_with_raw_values(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.storage.history import HistoryStore, OperationStatus

    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE operations
        SET stored_device = ?,
            stored_inode = ?,
            stored_size = ?,
            stored_mtime_ns = ?
        WHERE id = ?
        """,
        (123, 456, "invalid-size", 789, operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    operation = store.get_operations(batch_id)[0]

    assert operation.stored_identity is None
    assert operation.stored_identity_raw == (
        123,
        456,
        "invalid-size",
        789,
    )
    store.close()


def test_absent_stored_identity_has_no_raw_diagnostic_values(
    tmp_path: Path,
) -> None:
    from file_janitor.storage.history import HistoryStore, OperationStatus

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=tmp_path / "destination.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    operation = store.get_operations(batch_id)[0]

    assert operation.stored_identity is None
    assert operation.stored_identity_raw is None
    store.close()
