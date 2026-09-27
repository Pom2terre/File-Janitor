from pathlib import Path

from file_janitor.path_safety import current_file_identity
from file_janitor.recovery import reconcile_published_planned_operations
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
    PlannedRecoveryState,
)


def test_recovery_promotes_matching_published_identity(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "a.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(op_id, current_file_identity(destination))
    store.close()

    store = HistoryStore(db_path=db)
    assert reconcile_published_planned_operations(store) == [op_id]
    assert store.get_operations(batch_id)[0].status is OperationStatus.COMPLETED
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 0
    store.close()


def test_recovery_leaves_planned_without_stored_identity(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("maybe published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "a.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    store = HistoryStore(db_path=db)
    assert reconcile_published_planned_operations(store) == []
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()


def test_recovery_leaves_planned_when_identity_changed(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "a.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(op_id, current_file_identity(destination))
    store.close()

    destination.write_text("changed", encoding="utf-8")

    store = HistoryStore(db_path=db)
    assert reconcile_published_planned_operations(store) == []
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()

def test_inspect_unidentified_planned_move_observes_not_published(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        inspect_unidentified_planned_operations,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    inspection = inspections[0]
    assert inspection.operation_id == operation_id
    assert inspection.state is PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
    assert inspection.original_exists is True
    assert inspection.stored_exists is False
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING

    store.close()


def test_inspect_unidentified_planned_move_observes_unverified_publication(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        inspect_unidentified_planned_operations,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("maybe published", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    inspection = inspections[0]
    assert inspection.operation_id == operation_id
    assert inspection.state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    assert inspection.original_exists is False
    assert inspection.stored_exists is True
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING

    store.close()


def test_inspect_unidentified_planned_copy_with_both_paths_is_unverified(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        inspect_unidentified_planned_operations,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("maybe copy", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    inspection = inspections[0]
    assert inspection.operation_id == operation_id
    assert inspection.state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    assert inspection.original_exists is True
    assert inspection.stored_exists is True
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING

    store.close()


def test_inspect_unidentified_planned_move_with_both_paths_is_ambiguous(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        inspect_unidentified_planned_operations,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("other object", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    inspections = inspect_unidentified_planned_operations(store)

    assert len(inspections) == 1
    assert inspections[0].state is PlannedRecoveryState.AMBIGUOUS
    assert store.get_operations(batch_id)[0].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING

    store.close()

def test_resolve_no_published_planned_marks_failed_and_batch_failed(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import resolve_no_published_planned_operations

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert resolve_no_published_planned_operations(store) == [op_id]

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)

    assert operation.status is OperationStatus.FAILED
    assert operation.error is not None
    assert "récupération après crash" in operation.error
    assert "non rejouée automatiquement" in operation.error

    assert batch is not None
    assert batch.status is BatchStatus.FAILED
    assert batch.success_count == 0
    assert batch.failed_count == 1
    assert batch.skipped_count == 0

    assert source.read_text(encoding="utf-8") == "source"
    assert not destination.exists()
    store.close()


def test_resolve_no_published_after_completed_marks_batch_partial(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import resolve_no_published_planned_operations

    db = tmp_path / "history.db"
    done_destination = tmp_path / "sorted" / "done.txt"
    pending_source = tmp_path / "pending.txt"
    pending_destination = tmp_path / "sorted" / "pending.txt"

    done_destination.parent.mkdir()
    done_destination.write_text("done", encoding="utf-8")
    pending_source.write_text("pending", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=2)

    store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "done-source.txt",
        stored_path=done_destination,
        size=done_destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )
    failed_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=pending_source,
        stored_path=pending_destination,
        size=pending_source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert resolve_no_published_planned_operations(store) == [failed_id]

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert batch.success_count == 1
    assert batch.failed_count == 1
    assert batch.skipped_count == 0
    store.close()


def test_resolve_no_published_leaves_unverified_planned(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import resolve_no_published_planned_operations

    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("unverified", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert resolve_no_published_planned_operations(store) == []

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)
    assert operation.id == op_id
    assert operation.status is OperationStatus.PLANNED
    assert batch is not None
    assert batch.status is BatchStatus.RUNNING
    store.close()


def test_resolve_no_published_keeps_batch_running_with_other_ambiguous(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import resolve_no_published_planned_operations

    db = tmp_path / "history.db"
    safe_source = tmp_path / "safe.txt"
    safe_destination = tmp_path / "sorted" / "safe.txt"
    safe_source.write_text("safe", encoding="utf-8")

    ambiguous_source = tmp_path / "ambiguous.txt"
    ambiguous_destination = tmp_path / "sorted" / "ambiguous.txt"
    ambiguous_source.write_text("source", encoding="utf-8")
    ambiguous_destination.parent.mkdir()
    ambiguous_destination.write_text("dest", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=2)

    safe_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=safe_source,
        stored_path=safe_destination,
        size=safe_source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    ambiguous_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=ambiguous_source,
        stored_path=ambiguous_destination,
        size=ambiguous_source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert resolve_no_published_planned_operations(store) == [safe_id]

    operations = {op.id: op for op in store.get_operations(batch_id)}
    assert operations[safe_id].status is OperationStatus.FAILED
    assert operations[ambiguous_id].status is OperationStatus.PLANNED
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()

def test_annotate_unverified_planned_persists_diagnostic_and_keeps_running(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations

    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("unverified", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "source.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    annotated = annotate_unresolved_planned_operations(store)

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)

    assert annotated == [operation_id]
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is not None
    assert "identité non vérifiable" in operation.error
    assert "aucune action automatique" in operation.error

    assert batch is not None
    assert batch.status is BatchStatus.RUNNING
    assert destination.read_text(encoding="utf-8") == "unverified"

    store.close()


def test_annotate_ambiguous_planned_persists_diagnostic_and_keeps_running(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    annotated = annotate_unresolved_planned_operations(store)

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)

    assert annotated == [operation_id]
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is not None
    assert "état filesystem ambigu" in operation.error
    assert "aucune action automatique" in operation.error

    assert batch is not None
    assert batch.status is BatchStatus.RUNNING
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"

    store.close()


def test_annotate_unresolved_does_not_touch_no_published_case(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    annotated = annotate_unresolved_planned_operations(store)

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)

    assert annotated == []
    assert operation.id == operation_id
    assert operation.status is OperationStatus.PLANNED
    assert operation.error is None
    assert batch is not None
    assert batch.status is BatchStatus.RUNNING

    store.close()


def test_annotate_unresolved_is_idempotent(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations

    db = tmp_path / "history.db"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("unverified", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="copy",
        original_path=tmp_path / "source.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    first = annotate_unresolved_planned_operations(store)
    first_error = store.get_operations(batch_id)[0].error

    second = annotate_unresolved_planned_operations(store)
    second_operation = store.get_operations(batch_id)[0]

    assert first == [operation_id]
    assert second == [operation_id]
    assert second_operation.status is OperationStatus.PLANNED
    assert second_operation.error == first_error
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING

    store.close()

def test_run_crash_recovery_orchestrates_all_safe_passes(tmp_path: Path) -> None:
    from file_janitor.path_safety import current_file_identity
    from file_janitor.recovery import run_crash_recovery

    db = tmp_path / 'history.db'
    published = tmp_path / 'sorted' / 'published.txt'
    published.parent.mkdir()
    published.write_text('published', encoding='utf-8')

    unpublished_source = tmp_path / 'unpublished.txt'
    unpublished_destination = tmp_path / 'sorted' / 'unpublished.txt'
    unpublished_source.write_text('unpublished', encoding='utf-8')

    ambiguous_source = tmp_path / 'ambiguous.txt'
    ambiguous_destination = tmp_path / 'sorted' / 'ambiguous.txt'
    ambiguous_source.write_text('source', encoding='utf-8')
    ambiguous_destination.write_text('destination', encoding='utf-8')

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=3)

    promoted_id = store.record_operation(
        batch_id, kind='copy', original_path=tmp_path / 'published-source.txt',
        stored_path=published, size=published.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(promoted_id, current_file_identity(published))

    failed_id = store.record_operation(
        batch_id, kind='move', original_path=unpublished_source,
        stored_path=unpublished_destination, size=unpublished_source.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )

    annotated_id = store.record_operation(
        batch_id, kind='move', original_path=ambiguous_source,
        stored_path=ambiguous_destination, size=ambiguous_source.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )

    report = run_crash_recovery(store)

    assert report.promoted_operation_ids == (promoted_id,)
    assert report.failed_operation_ids == (failed_id,)
    assert report.annotated_operation_ids == (annotated_id,)
    assert report.changed_operation_ids == (promoted_id, failed_id, annotated_id)

    operations = {op.id: op for op in store.get_operations(batch_id)}
    assert operations[promoted_id].status is OperationStatus.COMPLETED
    assert operations[failed_id].status is OperationStatus.FAILED
    assert operations[annotated_id].status is OperationStatus.PLANNED
    assert operations[annotated_id].error is not None
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()


def test_run_crash_recovery_finalizes_batch_when_no_ambiguity_remains(tmp_path: Path) -> None:
    from file_janitor.path_safety import current_file_identity
    from file_janitor.recovery import run_crash_recovery

    db = tmp_path / 'history.db'
    published = tmp_path / 'sorted' / 'published.txt'
    published.parent.mkdir()
    published.write_text('published', encoding='utf-8')
    pending_source = tmp_path / 'pending.txt'
    pending_destination = tmp_path / 'sorted' / 'pending.txt'
    pending_source.write_text('pending', encoding='utf-8')

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=2)
    promoted_id = store.record_operation(
        batch_id, kind='copy', original_path=tmp_path / 'source.txt',
        stored_path=published, size=published.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(promoted_id, current_file_identity(published))
    failed_id = store.record_operation(
        batch_id, kind='move', original_path=pending_source,
        stored_path=pending_destination, size=pending_source.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )

    report = run_crash_recovery(store)
    assert report.promoted_operation_ids == (promoted_id,)
    assert report.failed_operation_ids == (failed_id,)
    assert report.annotated_operation_ids == ()

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert batch.success_count == 1
    assert batch.failed_count == 1
    assert batch.skipped_count == 0
    store.close()


def test_run_crash_recovery_is_idempotent_for_resolved_operations(tmp_path: Path) -> None:
    from file_janitor.path_safety import current_file_identity
    from file_janitor.recovery import run_crash_recovery

    db = tmp_path / 'history.db'
    destination = tmp_path / 'sorted' / 'a.txt'
    destination.parent.mkdir()
    destination.write_text('published', encoding='utf-8')

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id, kind='copy', original_path=tmp_path / 'a.txt',
        stored_path=destination, size=destination.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )
    store.set_operation_stored_identity(op_id, current_file_identity(destination))

    first = run_crash_recovery(store)
    second = run_crash_recovery(store)

    assert first.promoted_operation_ids == (op_id,)
    assert second.promoted_operation_ids == ()
    assert second.failed_operation_ids == ()
    assert second.annotated_operation_ids == ()
    assert store.get_operations(batch_id)[0].status is OperationStatus.COMPLETED
    assert store.get_batch(batch_id).status is BatchStatus.COMPLETED
    store.close()


def test_run_crash_recovery_reannotates_unresolved_case_idempotently(tmp_path: Path) -> None:
    from file_janitor.recovery import run_crash_recovery

    db = tmp_path / 'history.db'
    destination = tmp_path / 'sorted' / 'a.txt'
    destination.parent.mkdir()
    destination.write_text('unverified', encoding='utf-8')

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    op_id = store.record_operation(
        batch_id, kind='move', original_path=tmp_path / 'a.txt',
        stored_path=destination, size=destination.stat().st_size,
        category='to_sort', status=OperationStatus.PLANNED,
    )

    first = run_crash_recovery(store)
    first_error = store.get_operations(batch_id)[0].error
    second = run_crash_recovery(store)
    second_operation = store.get_operations(batch_id)[0]

    assert first.annotated_operation_ids == (op_id,)
    assert second.annotated_operation_ids == (op_id,)
    assert second_operation.status is OperationStatus.PLANNED
    assert second_operation.error == first_error
    assert store.get_batch(batch_id).status is BatchStatus.RUNNING
    store.close()


def test_manual_recovery_resolution_closes_ambiguous_without_filesystem_io(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        recovery_state_from_persisted_error,
        resolve_unresolved_planned_operations_without_file_action,
    )

    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir()
    destination.write_text("destination", encoding="utf-8")
    source_before = source.stat()
    destination_before = destination.stat()

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error=(
            "récupération après crash : état filesystem ambigu ; "
            "opération laissée PLANNED, aucune action automatique"
        ),
    )

    assert resolve_unresolved_planned_operations_without_file_action(
        store, batch_id
    ) == [operation_id]

    operation = store.get_operations(batch_id)[0]
    batch = store.get_batch(batch_id)
    assert operation.status is OperationStatus.FAILED
    assert recovery_state_from_persisted_error(operation.error) is (
        PlannedRecoveryState.AMBIGUOUS
    )
    assert batch is not None and batch.status is BatchStatus.FAILED
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"
    assert source.stat().st_ino == source_before.st_ino
    assert source.stat().st_mtime_ns == source_before.st_mtime_ns
    assert destination.stat().st_ino == destination_before.st_ino
    assert destination.stat().st_mtime_ns == destination_before.st_mtime_ns
    assert resolve_unresolved_planned_operations_without_file_action(
        store, batch_id
    ) == []
    store.close()


def test_manual_recovery_resolution_closes_unverified_as_failed(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        PlannedRecoveryState,
        recovery_state_from_persisted_error,
        resolve_unresolved_planned_operations_without_file_action,
    )

    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    destination.parent.mkdir()
    destination.write_text("published", encoding="utf-8")
    destination_before = destination.stat()

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error=(
            "récupération après crash : objet publié observé mais identité non vérifiable ; "
            "opération laissée PLANNED, aucune action automatique"
        ),
    )

    assert resolve_unresolved_planned_operations_without_file_action(
        store, batch_id
    ) == [operation_id]
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    assert recovery_state_from_persisted_error(operation.error) is (
        PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    )
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "published"
    assert destination.stat().st_ino == destination_before.st_ino
    assert destination.stat().st_mtime_ns == destination_before.st_mtime_ns
    store.close()

def test_unresolved_recovery_persists_structured_state_without_resolution(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations

    destination = tmp_path / "published.txt"
    destination.write_text("published", encoding="utf-8")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=tmp_path / "missing.txt",
        stored_path=destination,
        size=destination.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert annotate_unresolved_planned_operations(store)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    assert operation.status is OperationStatus.PLANNED
    store.close()


def test_no_publication_recovery_persists_automatic_resolution_audit(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import resolve_no_published_planned_operations
    from file_janitor.storage.history import RecoveryResolutionKind

    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=tmp_path / "destination.txt",
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert resolve_no_published_planned_operations(store)
    operation = store.get_operations(batch_id)[0]
    assert operation.recovery_state is PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
    assert operation.resolution_kind is RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION
    assert operation.resolved_at is not None
    assert operation.status is OperationStatus.FAILED
    store.close()


def test_manual_recovery_uses_structured_state_without_parsing_error(
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        resolve_unresolved_planned_operations_without_file_action,
    )
    from file_janitor.storage.history import RecoveryResolutionKind

    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
        error="texte volontairement non reconnu",
    )
    store.set_operation_recovery_audit(
        operation_id,
        recovery_state=PlannedRecoveryState.AMBIGUOUS,
    )

    assert resolve_unresolved_planned_operations_without_file_action(
        store, batch_id
    ) == [operation_id]
    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.FAILED
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
    assert operation.resolved_at is not None
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"
    store.close()

def test_terminal_recovery_paths_use_atomic_store_api(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import (
        annotate_unresolved_planned_operations,
        resolve_no_published_planned_operations,
        resolve_unresolved_planned_operations_without_file_action,
    )
    from file_janitor.storage.history import (
    PlannedRecoveryState,
        HistoryStore,
        OperationStatus,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")

    automatic_source = tmp_path / "automatic.txt"
    automatic_destination = tmp_path / "sorted" / "automatic.txt"
    automatic_source.write_text("source", encoding="utf-8")

    automatic_batch = store.start_batch(str(tmp_path), planned_count=1)
    automatic_id = store.record_operation(
        automatic_batch,
        kind="move",
        original_path=automatic_source,
        stored_path=automatic_destination,
        size=automatic_source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    calls: list[int] = []
    real_atomic = store.resolve_operation_recovery

    def recording_atomic(operation_id: int, **kwargs) -> None:
        calls.append(operation_id)
        real_atomic(operation_id, **kwargs)

    def forbidden_status(*args, **kwargs):
        raise AssertionError(
            "un chemin terminal recovery ne doit plus appeler set_operation_status"
        )

    def forbidden_audit(*args, **kwargs):
        raise AssertionError(
            "un chemin terminal recovery ne doit plus appeler set_operation_recovery_audit"
        )

    monkeypatch.setattr(store, "resolve_operation_recovery", recording_atomic)
    monkeypatch.setattr(store, "set_operation_status", forbidden_status)
    monkeypatch.setattr(store, "set_operation_recovery_audit", forbidden_audit)

    assert resolve_no_published_planned_operations(store) == [automatic_id]
    assert calls == [automatic_id]

    # Recréer un cas unresolved manuel dans une nouvelle connexion non monkeypatchée,
    # car annotate_unresolved_planned_operations doit continuer à utiliser les APIs
    # non terminales pour laisser l'opération PLANNED.
    store.close()

    store = HistoryStore(db_path=tmp_path / "history.db")
    manual_source = tmp_path / "manual.txt"
    manual_destination = tmp_path / "sorted" / "manual.txt"
    manual_source.write_text("source", encoding="utf-8")
    manual_destination.parent.mkdir(parents=True, exist_ok=True)
    manual_destination.write_text("destination", encoding="utf-8")

    manual_batch = store.start_batch(str(tmp_path), planned_count=1)
    manual_id = store.record_operation(
        manual_batch,
        kind="move",
        original_path=manual_source,
        stored_path=manual_destination,
        size=manual_source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    assert annotate_unresolved_planned_operations(store) == [manual_id]

    calls = []
    real_atomic = store.resolve_operation_recovery

    def recording_atomic_manual(operation_id: int, **kwargs) -> None:
        calls.append(operation_id)
        real_atomic(operation_id, **kwargs)

    monkeypatch.setattr(store, "resolve_operation_recovery", recording_atomic_manual)
    monkeypatch.setattr(store, "set_operation_status", forbidden_status)
    monkeypatch.setattr(store, "set_operation_recovery_audit", forbidden_audit)

    assert resolve_unresolved_planned_operations_without_file_action(
        store,
        manual_batch,
    ) == [manual_id]
    assert calls == [manual_id]
    store.close()

def test_unresolved_annotation_uses_atomic_store_api(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from file_janitor.recovery import annotate_unresolved_planned_operations
    from file_janitor.storage.history import HistoryStore, OperationStatus

    store = HistoryStore(db_path=tmp_path / "history.db")
    source = tmp_path / "source.txt"
    destination = tmp_path / "sorted" / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination.parent.mkdir(parents=True)
    destination.write_text("destination", encoding="utf-8")

    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )

    calls: list[int] = []
    real_atomic = store.annotate_operation_recovery

    def recording_atomic(operation_id: int, **kwargs) -> None:
        calls.append(operation_id)
        real_atomic(operation_id, **kwargs)

    def forbidden_status(*args, **kwargs):
        raise AssertionError(
            "l'annotation unresolved ne doit plus appeler set_operation_status"
        )

    def forbidden_audit(*args, **kwargs):
        raise AssertionError(
            "l'annotation unresolved ne doit plus appeler set_operation_recovery_audit"
        )

    monkeypatch.setattr(store, "annotate_operation_recovery", recording_atomic)
    monkeypatch.setattr(store, "set_operation_status", forbidden_status)
    monkeypatch.setattr(store, "set_operation_recovery_audit", forbidden_audit)

    assert annotate_unresolved_planned_operations(store) == [operation_id]
    assert calls == [operation_id]

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.recovery_state is PlannedRecoveryState.AMBIGUOUS
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    store.close()

def test_manual_recovery_refuses_unknown_structured_state(
    tmp_path: Path,
) -> None:
    import sqlite3

    from file_janitor.recovery import (
        resolve_unresolved_planned_operations_without_file_action,
    )
    from file_janitor.storage.history import (
        HistoryStore,
        OperationStatus,
        PlannedRecoveryState,
    )

    db = tmp_path / "history.db"
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")

    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="move",
        original_path=source,
        stored_path=destination,
        size=source.stat().st_size,
        category="to_sort",
        status=OperationStatus.PLANNED,
    )
    store.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE operations SET recovery_state = ? WHERE id = ?",
        ("future_recovery_state", operation_id),
    )
    conn.commit()
    conn.close()

    store = HistoryStore(db_path=db)
    assert resolve_unresolved_planned_operations_without_file_action(
        store,
        batch_id,
    ) == []

    operation = store.get_operations(batch_id)[0]
    assert operation.status is OperationStatus.PLANNED
    assert operation.recovery_state is PlannedRecoveryState.UNKNOWN
    assert operation.resolution_kind is None
    assert operation.resolved_at is None
    assert source.read_text(encoding="utf-8") == "source"
    assert destination.read_text(encoding="utf-8") == "destination"
    store.close()
