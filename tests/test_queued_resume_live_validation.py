"""Tests 4E-32C : revalidation live read-only des opérations QUEUED."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_janitor.application import validate_queued_resume_candidates
from file_janitor.models import FileIdentity
from file_janitor.path_safety import current_file_identity
from file_janitor.storage.history import BatchStatus, HistoryStore, OperationStatus


def _persist_queued(
    tmp_path: Path,
    *,
    kind: str = "move",
    conflict_policy: str | None = "rename",
    persist_identity: bool = True,
) -> tuple[Path, int, Path, Path, FileIdentity]:
    source = tmp_path / f"{kind}-source.txt"
    source.write_bytes(b"source")
    destination = tmp_path / "sorted" / source.name
    identity = current_file_identity(source)
    database = tmp_path / "history.db"
    store = HistoryStore(db_path=database)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind=kind,
        original_path=source,
        stored_path=destination if kind in {"copy", "move"} else None,
        size=identity.size,
        category="to_sort",
        status=OperationStatus.QUEUED,
        original_identity=identity if persist_identity else None,
        conflict_policy=(
            conflict_policy if kind in {"copy", "move"} else None
        ),
    )
    store.finish_batch_execution(
        batch_id,
        BatchStatus.CANCELLED,
        success_count=0,
        failed_count=0,
        skipped_count=1,
    )
    store.close()
    return database, batch_id, source, destination, identity


def _persisted_rows(database: Path) -> tuple[list[tuple], list[tuple]]:
    connection = sqlite3.connect(database)
    try:
        batches = connection.execute(
            "SELECT * FROM batches ORDER BY id"
        ).fetchall()
        operations = connection.execute(
            "SELECT * FROM operations ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return batches, operations


def test_live_validation_accepts_unchanged_move_without_mutation(
    tmp_path: Path,
) -> None:
    database, batch_id, source, destination, _identity = _persist_queued(
        tmp_path
    )
    rows_before = _persisted_rows(database)
    source_before = source.read_bytes()

    result = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )

    assert len(result) == 1
    validation = result[0]
    assert validation.metadata_candidate is True
    assert validation.live_checked is True
    assert validation.live_ready is True
    assert validation.effective_destination == destination.resolve()
    assert validation.blockers == ()
    assert validation.diagnostic is None
    assert validation.revalidation_required_before_io is True
    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert _persisted_rows(database) == rows_before


def test_live_validation_blocks_changed_source_identity(tmp_path: Path) -> None:
    database, batch_id, source, destination, _identity = _persist_queued(
        tmp_path
    )
    source.write_bytes(b"changed source")

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.live_checked is True
    assert validation.live_ready is False
    assert validation.blockers == ("source_identity_changed",)
    assert "source modifiée" in (validation.diagnostic or "")
    assert not destination.exists()


def test_live_validation_blocks_missing_source(tmp_path: Path) -> None:
    database, batch_id, source, destination, _identity = _persist_queued(
        tmp_path
    )
    source.unlink()

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.live_checked is True
    assert validation.live_ready is False
    assert validation.blockers == ("source_unavailable_or_unsafe",)
    assert "source introuvable" in (validation.diagnostic or "")
    assert not destination.exists()


def test_live_validation_blocks_skip_collision_without_mutation(
    tmp_path: Path,
) -> None:
    database, batch_id, source, destination, _identity = _persist_queued(
        tmp_path,
        conflict_policy="skip",
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")
    rows_before = _persisted_rows(database)

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.live_ready is False
    assert validation.blockers == ("destination_conflict_skip",)
    assert destination.read_bytes() == b"existing"
    assert source.read_bytes() == b"source"
    assert _persisted_rows(database) == rows_before


def test_live_validation_previews_rename_collision_without_creating_target(
    tmp_path: Path,
) -> None:
    database, batch_id, source, destination, _identity = _persist_queued(
        tmp_path,
        conflict_policy="rename",
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    expected = destination.with_name(f"{destination.stem} (1){destination.suffix}")
    assert validation.live_ready is True
    assert validation.effective_destination == expected.resolve()
    assert destination.read_bytes() == b"existing"
    assert source.read_bytes() == b"source"
    assert not expected.exists()


def test_live_validation_blocks_disabled_replace_collision(
    tmp_path: Path,
) -> None:
    database, batch_id, _source, destination, _identity = _persist_queued(
        tmp_path,
        conflict_policy="replace",
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing")

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.live_ready is False
    assert validation.blockers == (
        "destination_conflict_replace_disabled",
    )
    assert destination.read_bytes() == b"existing"


def test_live_validation_blocks_unsafe_destination_symlink(
    tmp_path: Path,
) -> None:
    database, batch_id, _source, destination, _identity = _persist_queued(
        tmp_path
    )
    target = tmp_path / "existing.txt"
    target.write_bytes(b"existing")
    destination.parent.mkdir(parents=True)
    destination.symlink_to(target)

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.live_ready is False
    assert validation.blockers == ("destination_unsafe",)
    assert target.read_bytes() == b"existing"


def test_metadata_blockers_prevent_live_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import file_janitor.application.service as service_module

    database, batch_id, _source, _destination, _identity = _persist_queued(
        tmp_path,
        conflict_policy=None,
        persist_identity=False,
    )

    def unexpected_live_access(_path: Path) -> Path:
        raise AssertionError("les preuves persistées invalides doivent bloquer avant stat")

    monkeypatch.setattr(
        service_module,
        "validate_source_file",
        unexpected_live_access,
    )

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.metadata_candidate is False
    assert validation.live_checked is False
    assert validation.live_ready is False
    assert validation.blockers == (
        "missing_original_identity",
        "missing_conflict_policy",
    )


def test_live_validation_accepts_delete_without_destination_policy(
    tmp_path: Path,
) -> None:
    database, batch_id, source, _destination, _identity = _persist_queued(
        tmp_path,
        kind="delete",
        conflict_policy=None,
    )

    validation = validate_queued_resume_candidates(
        batch_id,
        db_path=database,
    )[0]

    assert validation.kind == "delete"
    assert validation.live_checked is True
    assert validation.live_ready is True
    assert validation.effective_destination is None
    assert source.read_bytes() == b"source"
