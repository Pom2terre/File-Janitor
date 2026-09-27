"""4E-31A : identités filesystem 64 bits non signées dans SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.models import FileIdentity
from file_janitor.storage.history import HistoryStore, OperationStatus


SQLITE_MAX = 2**63 - 1
UINT64_MAX = 2**64 - 1


def _operation(store: HistoryStore, root: Path) -> tuple[int, int]:
    batch_id = store.start_batch(str(root), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=root / "source.bin",
        stored_path=root / "destination.bin",
        size=1,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    return batch_id, operation_id


def _round_trip(
    tmp_path: Path,
    identity: FileIdentity,
) -> tuple[FileIdentity | None, tuple[object | None, ...] | None, tuple[object, ...]]:
    database = tmp_path / "history.db"
    store = HistoryStore(db_path=database)
    batch_id, operation_id = _operation(store, tmp_path)
    store.set_operation_stored_identity(operation_id, identity)
    operation = store.get_operations(batch_id)[0]
    store.close()

    connection = sqlite3.connect(database)
    persisted = connection.execute(
        """
        SELECT stored_device, stored_inode, stored_size, stored_mtime_ns
        FROM operations
        WHERE id = ?
        """,
        (operation_id,),
    ).fetchone()
    connection.close()
    assert persisted is not None
    return operation.stored_identity, operation.stored_identity_raw, persisted


def test_signed_identity_components_remain_sqlite_integers(tmp_path: Path) -> None:
    identity = FileIdentity(device=74, inode=123, size=456, mtime_ns=789)

    decoded, raw, persisted = _round_trip(tmp_path, identity)

    assert decoded == identity
    assert raw is None
    assert persisted == (74, 123, 456, 789)


def test_unsigned_inode_round_trips_as_marked_text(tmp_path: Path) -> None:
    identity = FileIdentity(
        device=74,
        inode=18_436_732_983_509_304_792,
        size=560_494_430,
        mtime_ns=1_722_322_940_000_000_000,
    )

    decoded, raw, persisted = _round_trip(tmp_path, identity)

    assert decoded == identity
    assert raw is None
    assert persisted[1] == f"u64:{identity.inode}"


def test_unsigned_device_round_trips_without_precision_loss(tmp_path: Path) -> None:
    identity = FileIdentity(
        device=SQLITE_MAX + 1,
        inode=2,
        size=3,
        mtime_ns=4,
    )

    decoded, raw, persisted = _round_trip(tmp_path, identity)

    assert decoded == identity
    assert raw is None
    assert persisted[0] == f"u64:{SQLITE_MAX + 1}"


def test_unsigned_size_round_trips_without_precision_loss(tmp_path: Path) -> None:
    identity = FileIdentity(
        device=1,
        inode=2,
        size=SQLITE_MAX + 1,
        mtime_ns=4,
    )

    decoded, raw, persisted = _round_trip(tmp_path, identity)

    assert decoded == identity
    assert raw is None
    assert persisted[2] == f"u64:{SQLITE_MAX + 1}"


def test_unsigned_mtime_round_trips_without_precision_loss(tmp_path: Path) -> None:
    identity = FileIdentity(
        device=1,
        inode=2,
        size=3,
        mtime_ns=UINT64_MAX,
    )

    decoded, raw, persisted = _round_trip(tmp_path, identity)

    assert decoded == identity
    assert raw is None
    assert persisted[3] == f"u64:{UINT64_MAX}"


def test_invalid_identity_component_is_rejected_before_update(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id, operation_id = _operation(store, tmp_path)

    for invalid_inode in (-1, True, UINT64_MAX + 1):
        with pytest.raises(ValueError, match="stored_inode"):
            store.set_operation_stored_identity(
                operation_id,
                FileIdentity(
                    device=1,
                    inode=invalid_inode,
                    size=1,
                    mtime_ns=1,
                ),
            )

    operation = store.get_operations(batch_id)[0]
    assert operation.stored_identity is None
    assert operation.stored_identity_raw is None
    store.close()
