"""Tests 4E-32A : persistance du plan complet avant exécution."""

from __future__ import annotations

from pathlib import Path

from file_janitor.executor import execute_items
from file_janitor.models import ActionItem, ActionKind, FileCategory, FileIdentity
from file_janitor.path_safety import current_file_identity
from file_janitor.recovery import run_crash_recovery
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _move_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="persisted plan",
        identity=current_file_identity(source),
        destination=destination,
        action=ActionKind.MOVE,
    )


def test_complete_plan_exists_before_first_filesystem_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module

    sources = [tmp_path / f"track-{index}.txt" for index in range(1, 4)]
    for source in sources:
        source.write_text(source.stem)
    expected_identities = [current_file_identity(source) for source in sources]
    destinations = [tmp_path / "sorted" / source.name for source in sources]
    store = HistoryStore(db_path=tmp_path / "history.db")
    real_rename = executor_module._rename_noreplace
    observed: list[
        tuple[
            list[Path | None],
            list[OperationStatus],
            list[FileIdentity | None],
            list[str | None],
        ]
    ] = []

    def inspect_first_mutation(source: Path, destination: Path) -> None:
        if not observed:
            operations = store.get_operations(1)
            observed.append(
                (
                    [operation.original_path for operation in operations],
                    [operation.status for operation in operations],
                    [operation.original_identity for operation in operations],
                    [operation.conflict_policy for operation in operations],
                )
            )
        real_rename(source, destination)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        inspect_first_mutation,
    )

    batch_id, success, errors = execute_items(
        [
            _move_item(source, destination)
            for source, destination in zip(sources, destinations, strict=True)
        ],
        root=str(tmp_path),
        store=store,
    )

    assert batch_id == 1
    assert success == 3
    assert errors == []
    assert observed == [
        (
            sources,
            [
                OperationStatus.PLANNED,
                OperationStatus.QUEUED,
                OperationStatus.QUEUED,
            ],
            expected_identities,
            ["rename", "rename", "rename"],
        )
    ]
    store.close()


def test_complete_plan_is_inserted_in_one_write_transaction(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=2)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        operation_ids = store.record_queued_operations(
            batch_id,
            [
                (
                    "move",
                    tmp_path / "first.txt",
                    tmp_path / "sorted" / "first.txt",
                    1,
                    "to_sort",
                    None,
                    "rename",
                ),
                (
                    "copy",
                    tmp_path / "second.txt",
                    tmp_path / "sorted" / "second.txt",
                    2,
                    "to_sort",
                    None,
                    "rename",
                ),
            ],
        )
    finally:
        store._conn.set_trace_callback(None)

    normalized = [statement.strip().upper() for statement in statements]
    assert len(operation_ids) == 2
    assert normalized.count("BEGIN IMMEDIATE") == 1
    assert normalized.count("COMMIT") == 1
    assert sum(
        statement.startswith("INSERT INTO OPERATIONS")
        for statement in normalized
    ) == 2
    store.close()


def test_cancelled_plan_preserves_every_unstarted_action(tmp_path: Path) -> None:
    sources = [tmp_path / f"track-{index}.txt" for index in range(1, 4)]
    for source in sources:
        source.write_text(source.stem)
    destinations = [tmp_path / "sorted" / source.name for source in sources]
    store = HistoryStore(db_path=tmp_path / "history.db")
    checks = iter((False, True))

    batch_id, success, errors = execute_items(
        [
            _move_item(source, destination)
            for source, destination in zip(sources, destinations, strict=True)
        ],
        root=str(tmp_path),
        store=store,
        cancel_callback=lambda: next(checks),
    )

    assert success == 1
    assert errors == []
    operations = store.get_operations(batch_id)
    assert [operation.original_path for operation in operations] == sources
    assert [operation.stored_path for operation in operations] == destinations
    assert [operation.status for operation in operations] == [
        OperationStatus.COMPLETED,
        OperationStatus.QUEUED,
        OperationStatus.QUEUED,
    ]
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.CANCELLED
    assert batch.skipped_count == 2
    assert store.get_batch_consistency_issues(batch_id) == ()
    store.close()


def test_preflight_failure_updates_queued_row_without_duplicate(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.txt"
    destination = tmp_path / "sorted" / missing.name
    store = HistoryStore(db_path=tmp_path / "history.db")
    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=missing,
        size=7,
        reason="persisted plan",
        destination=destination,
        action=ActionKind.MOVE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].original_path == missing
    assert operations[0].stored_path == destination
    store.close()


def test_reconcile_running_batch_counts_queued_rows_as_unstarted(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=2)
    completed = tmp_path / "completed.txt"
    queued = tmp_path / "queued.txt"
    store.record_operation(
        batch_id,
        kind="move",
        original_path=completed,
        stored_path=tmp_path / "sorted" / completed.name,
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    store.record_operation(
        batch_id,
        kind="move",
        original_path=queued,
        stored_path=tmp_path / "sorted" / queued.name,
        size=1,
        category="to_sort",
        status=OperationStatus.QUEUED,
    )

    assert store.reconcile_running_batches() == [batch_id]
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 1
    assert store.get_batch_consistency_issues(batch_id) == ()
    store.close()


def test_crash_recovery_never_treats_queued_action_as_started(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("untouched")
    destination = tmp_path / "sorted" / source.name
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.QUEUED,
    )

    report = run_crash_recovery(store)

    assert report.changed_operation_ids == ()
    operation = store.get_operations(batch_id)[0]
    assert operation.id == operation_id
    assert operation.status is OperationStatus.QUEUED
    assert operation.error is None
    assert source.read_text() == "untouched"
    assert not destination.exists()
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED
    assert batch.skipped_count == 1
    assert store.get_batch_consistency_issues(batch_id) == ()
    store.close()
