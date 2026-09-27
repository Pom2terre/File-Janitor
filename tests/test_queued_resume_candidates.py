"""Tests 4E-32B : preuves persistées et qualification des QUEUED."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.application.service import get_history_operation_summary
from file_janitor.models import FileIdentity
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _identity(*, size: int = 4) -> FileIdentity:
    return FileIdentity(device=11, inode=22, size=size, mtime_ns=33)


def _queued_operation(
    db_path: Path,
    *,
    batch_status: BatchStatus = BatchStatus.CANCELLED,
    original_identity: FileIdentity | None = None,
    conflict_policy: str | None = "skip",
    kind: str = "move",
    stored_path: Path | None = None,
) -> tuple[int, int]:
    root = db_path.parent
    source = root / "source.txt"
    destination = (
        stored_path
        if stored_path is not None
        else root / "sorted" / source.name
    )
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(root), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind=kind,
        original_path=source,
        stored_path=destination if kind in {"copy", "move"} else None,
        size=4,
        category="to_sort",
        status=OperationStatus.QUEUED,
        original_identity=original_identity,
        conflict_policy=conflict_policy,
    )
    store.finish_batch_execution(
        batch_id,
        (
            BatchStatus.CANCELLED
            if batch_status is BatchStatus.UNDONE
            else batch_status
        ),
        success_count=0,
        failed_count=0,
        skipped_count=1,
    )
    if batch_status is BatchStatus.UNDONE:
        store.set_batch_status(batch_id, BatchStatus.UNDONE)
    store.close()
    return batch_id, operation_id


def test_queued_plan_round_trips_source_identity_and_conflict_policy(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    expected_identity = FileIdentity(
        device=(1 << 63) + 5,
        inode=(1 << 63) + 7,
        size=4,
        mtime_ns=(1 << 63) + 9,
    )

    operation_ids = store.record_queued_operations(
        batch_id,
        [
            (
                "move",
                tmp_path / "source.txt",
                tmp_path / "sorted" / "source.txt",
                4,
                "to_sort",
                expected_identity,
                "rename",
            )
        ],
    )

    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_ids[0]
    assert operation.original_identity == expected_identity
    assert operation.original_identity_raw is None
    assert operation.conflict_policy == "rename"
    assert operation.conflict_policy_raw is None
    store.close()


def test_queued_plan_rejects_unknown_policy_atomically(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)

    with pytest.raises(ValueError, match="collision inconnue"):
        store.record_queued_operations(
            batch_id,
            [
                (
                    "move",
                    tmp_path / "source.txt",
                    tmp_path / "sorted" / "source.txt",
                    4,
                    "to_sort",
                    _identity(),
                    "overwrite",
                )
            ],
        )

    assert store.get_operations(batch_id) == []
    store.close()


def test_complete_queued_evidence_is_exposed_as_resume_candidate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, _operation_id = _queued_operation(
        db_path,
        original_identity=_identity(),
    )

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.status == "queued"
    assert summary.conflict_policy == "skip"
    assert summary.original_identity_issue is None
    assert summary.resume_candidate is True
    assert summary.resume_blockers == ()


def test_legacy_queued_row_without_resume_proof_is_blocked(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, _operation_id = _queued_operation(
        db_path,
        original_identity=None,
        conflict_policy=None,
    )

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.resume_candidate is False
    assert summary.original_identity_issue == "missing_original_identity"
    assert summary.resume_blockers == (
        "missing_original_identity",
        "missing_conflict_policy",
    )


def test_queued_row_with_mismatched_identity_size_is_blocked(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, _operation_id = _queued_operation(
        db_path,
        original_identity=_identity(size=99),
    )

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.resume_candidate is False
    assert summary.resume_blockers == ("original_identity_size_mismatch",)


def test_queued_row_in_undone_batch_is_not_a_resume_candidate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, _operation_id = _queued_operation(
        db_path,
        batch_status=BatchStatus.UNDONE,
        original_identity=_identity(),
    )

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.resume_candidate is False
    assert "batch_status_not_resumable" in summary.resume_blockers


def test_invalid_persisted_resume_proof_remains_visible_and_blocking(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, operation_id = _queued_operation(
        db_path,
        original_identity=_identity(),
    )
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        UPDATE operations
        SET original_device = ?, conflict_policy = ?
        WHERE id = ?
        """,
        ("corrupt", "overwrite", operation_id),
    )
    connection.commit()
    connection.close()

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.resume_candidate is False
    assert summary.original_identity_issue == "invalid_original_identity"
    assert summary.conflict_policy is None
    assert summary.conflict_policy_raw == "overwrite"
    assert "invalid_original_identity" in summary.resume_blockers
    assert "invalid_conflict_policy" in summary.resume_blockers


def test_queued_delete_does_not_require_a_preallocated_stored_path(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    batch_id, _operation_id = _queued_operation(
        db_path,
        original_identity=_identity(),
        conflict_policy=None,
        kind="delete",
    )

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.stored_path is None
    assert summary.resume_candidate is True
    assert summary.resume_blockers == ()
