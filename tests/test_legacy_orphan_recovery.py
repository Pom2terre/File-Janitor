"""4E-26C: isolation des orphelins pendant la recovery automatique."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.application.service import run_startup_crash_recovery
from file_janitor.storage.history import HistoryStore, OperationStatus


def _insert_planned_orphan(
    db_path: Path,
    *,
    batch_id: int,
    original_path: Path,
    stored_path: Path,
) -> int:
    initial = HistoryStore(db_path=db_path)
    initial.close()

    legacy = sqlite3.connect(db_path)
    assert legacy.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    cursor = legacy.execute(
        """
        INSERT INTO operations (
            batch_id, kind, original_path, stored_path,
            size, category, status, error,
            recovery_state, resolution_kind, resolved_at
        )
        VALUES (?, 'move', ?, ?, 1, 'documents', 'planned', NULL, NULL, NULL, NULL)
        """,
        (batch_id, str(original_path), str(stored_path)),
    )
    assert cursor.lastrowid is not None
    operation_id = int(cursor.lastrowid)
    legacy.commit()
    legacy.close()
    return operation_id


def _raw_operation(db_path: Path, operation_id: int) -> tuple[object, ...]:
    connection = sqlite3.connect(db_path)
    row = connection.execute(
        """
        SELECT id, batch_id, status, error,
               recovery_state, resolution_kind, resolved_at
        FROM operations
        WHERE id = ?
        """,
        (operation_id,),
    ).fetchone()
    connection.close()
    assert row is not None
    return row


def _filesystem_snapshot(*paths: Path) -> tuple[bytes | None, ...]:
    return tuple(path.read_bytes() if path.exists() else None for path in paths)


@pytest.mark.parametrize(
    ("source_exists", "stored_exists"),
    (
        pytest.param(True, False, id="no-published-object"),
        pytest.param(False, True, id="published-object-unverified"),
        pytest.param(True, True, id="ambiguous-filesystem"),
    ),
)
def test_startup_recovery_ignores_legacy_orphan(
    tmp_path: Path,
    source_exists: bool,
    stored_exists: bool,
) -> None:
    db_path = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    stored = tmp_path / "stored.txt"
    if source_exists:
        source.write_text("source", encoding="utf-8")
    if stored_exists:
        stored.write_text("stored", encoding="utf-8")

    batch_id = 910_000 + int(source_exists) * 10 + int(stored_exists)
    operation_id = _insert_planned_orphan(
        db_path,
        batch_id=batch_id,
        original_path=source,
        stored_path=stored,
    )
    row_before = _raw_operation(db_path, operation_id)
    filesystem_before = _filesystem_snapshot(source, stored)

    report = run_startup_crash_recovery(db_path=db_path)

    assert report.promoted_operation_ids == ()
    assert report.failed_operation_ids == ()
    assert report.annotated_operation_ids == ()
    assert _raw_operation(db_path, operation_id) == row_before
    assert _filesystem_snapshot(source, stored) == filesystem_before

    final = HistoryStore(db_path=db_path)
    assert final.get_batch(batch_id) is None
    operation = final.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is None
    assert operation.recovery_state is None
    assert operation.resolution_kind is None
    with final._cursor() as cursor:
        cursor.execute("PRAGMA foreign_key_check")
        violations = [tuple(row) for row in cursor.fetchall()]
    assert violations == [("operations", operation_id, "batches", 0)]
    final.close()
