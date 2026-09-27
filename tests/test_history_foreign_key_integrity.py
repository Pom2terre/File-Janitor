"""4E-26A: intégrité référentielle batch/opération sur chaque connexion."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore, OperationStatus


def _foreign_keys(store: HistoryStore) -> int:
    with store._cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _insert_raw_operation(
    store: HistoryStore,
    batch_id: object,
    root: Path,
) -> None:
    with store._cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO operations (
                batch_id,
                kind,
                original_path,
                stored_path,
                size,
                category,
                status
            )
            VALUES (?, 'copy', ?, ?, 1, 'documents', 'planned')
            """,
            (
                batch_id,
                str(root / "original.txt"),
                str(root / "stored.txt"),
            ),
        )


def test_history_store_enables_foreign_keys(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    assert _foreign_keys(store) == 1
    store.close()


@pytest.mark.parametrize("missing_batch_id", (999_999, "invalid-batch-id"))
def test_raw_insert_rejects_orphan_operation(
    tmp_path: Path,
    missing_batch_id: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw_operation(store, missing_batch_id, tmp_path)

    with store._cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM operations")
        assert cursor.fetchone()[0] == 0
    store.close()


def test_record_operation_rejects_missing_batch(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    with pytest.raises(sqlite3.IntegrityError):
        store.record_operation(
            999_999,
            kind="copy",
            original_path=tmp_path / "original.txt",
            stored_path=tmp_path / "stored.txt",
            size=1,
            category="documents",
            status=OperationStatus.PLANNED,
        )

    assert store.get_operations(999_999) == []
    store.close()


def test_update_rejects_orphaning_existing_operation(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "original.txt",
        stored_path=tmp_path / "stored.txt",
        size=1,
        category="documents",
        status=OperationStatus.PLANNED,
    )

    with pytest.raises(sqlite3.IntegrityError):
        with store._cursor() as cursor:
            cursor.execute(
                "UPDATE operations SET batch_id = ? WHERE id = ?",
                (999_999, operation_id),
            )

    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.batch_id == batch_id
    store.close()


def test_existing_orphan_is_preserved_but_new_orphan_is_rejected(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    store.close()

    legacy = sqlite3.connect(db_path)
    assert legacy.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    legacy.execute(
        """
        INSERT INTO operations (
            batch_id,
            kind,
            original_path,
            stored_path,
            size,
            category,
            status
        )
        VALUES (999999, 'copy', ?, ?, 1, 'documents', 'planned')
        """,
        (
            str(tmp_path / "legacy-original.txt"),
            str(tmp_path / "legacy-stored.txt"),
        ),
    )
    legacy.commit()
    legacy.close()

    reopened = HistoryStore(db_path=db_path)
    assert _foreign_keys(reopened) == 1
    with reopened._cursor() as cursor:
        cursor.execute("PRAGMA foreign_key_check")
        violations = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) FROM operations")
        operation_count = cursor.fetchone()[0]

    assert len(violations) == 1
    assert operation_count == 1
    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw_operation(reopened, 888_888, tmp_path)
    reopened.close()
