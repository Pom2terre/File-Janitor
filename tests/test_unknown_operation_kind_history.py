"""4E-22B: exposition et exclusion d'un kind persisté inconnu."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from file_janitor.application.service import (
    _history_operation_core_metadata_issues,
    get_history_operation_summary,
)
from file_janitor.gui.main_window import MainWindow
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _record_completed(
    store: HistoryStore,
    batch_id: int,
    *,
    root: Path,
    name: str,
    kind: str,
) -> int:
    return store.record_operation(
        batch_id,
        kind=kind,
        original_path=root / f"{name}-original.txt",
        stored_path=root / f"{name}-stored.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


def _corrupt_operation_kind(
    store: HistoryStore,
    operation_id: int,
    *,
    kind: str = "rename",
) -> None:
    with store._cursor() as cursor:
        cursor.execute(
            "UPDATE operations SET kind = ? WHERE id = ?",
            (kind, operation_id),
        )


def test_operation_issue_classifier_distinguishes_unknown_kind() -> None:
    valid = SimpleNamespace(status_raw=None, kind="move")
    unknown = SimpleNamespace(status_raw=None, kind="rename")

    assert _history_operation_core_metadata_issues(valid) == ()
    assert _history_operation_core_metadata_issues(unknown) == ("unknown_kind",)


def test_history_summary_exposes_unknown_kind_issue(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    store = HistoryStore(db_path=db)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_completed(
        store,
        batch_id,
        root=tmp_path,
        name="unknown",
        kind="move",
    )
    _corrupt_operation_kind(store, operation_id)
    store.finish_batch_execution(
        batch_id,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db)[0]

    assert summary.kind == "rename"
    assert summary.core_metadata_issues == ("unknown_kind",)


def test_gui_renders_unknown_kind_issue() -> None:
    assert MainWindow._history_core_metadata_issue_text(("unknown_kind",)) == (
        "Métadonnées cœur incohérentes : type d’opération inconnu"
    )


def test_latest_undoable_batch_skips_unknown_kind(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    valid = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, valid, root=tmp_path, name="valid", kind="move")
    store.finish_batch_execution(
        valid,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    invalid = store.start_batch(str(tmp_path), planned_count=1)
    invalid_operation = _record_completed(
        store, invalid, root=tmp_path, name="invalid", kind="move"
    )
    _corrupt_operation_kind(store, invalid_operation)
    store.finish_batch_execution(
        invalid,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    assert invalid > valid
    assert store.latest_undoable_batch_id() == valid
    store.close()


def test_latest_undoable_batch_skips_mixed_unknown_kind(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")

    valid = store.start_batch(str(tmp_path), planned_count=1)
    _record_completed(store, valid, root=tmp_path, name="older", kind="copy")
    store.finish_batch_execution(
        valid,
        BatchStatus.COMPLETED,
        success_count=1,
        failed_count=0,
        skipped_count=0,
    )

    mixed = store.start_batch(str(tmp_path), planned_count=2)
    _record_completed(store, mixed, root=tmp_path, name="known", kind="move")
    unknown_operation = _record_completed(
        store, mixed, root=tmp_path, name="unknown", kind="delete"
    )
    _corrupt_operation_kind(store, unknown_operation)
    store.finish_batch_execution(
        mixed,
        BatchStatus.COMPLETED,
        success_count=2,
        failed_count=0,
        skipped_count=0,
    )

    assert mixed > valid
    assert store.latest_undoable_batch_id() == valid
    store.close()
