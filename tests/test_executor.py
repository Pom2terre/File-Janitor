"""Tests de l'exécuteur transactionnel.

Ces tests vérifient :

- ActionItem.action est l'unique source de vérité ;
- les opérations sont enregistrées avant l'I/O ;
- leur statut devient COMPLETED ou FAILED ;
- les batches deviennent COMPLETED, PARTIAL ou FAILED ;
- les erreurs d'I/O restent dans l'historique ;
- MOVE, COPY, TRASH et undo conservent leur comportement.
"""

from __future__ import annotations

from pathlib import Path

from file_janitor.executor import execute_items, undo_batch
from file_janitor.models import (
    ActionItem,
    ActionKind,
    FileCategory,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def test_execute_delete_and_undo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module

    trash_dir = tmp_path / "trash"

    monkeypatch.setattr(
        executor_module,
        "TRASH_DIR",
        trash_dir,
    )

    target = tmp_path / "dup.txt"
    target.write_text("contenu")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=target,
        size=target.stat().st_size,
        reason="test",
        action=ActionKind.TRASH,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not target.exists()
    assert any(trash_dir.iterdir())

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].kind == "delete"

    undo_success, undo_errors = undo_batch(
        batch_id,
        store,
    )

    assert undo_success == 1
    assert not undo_errors
    assert target.exists()
    assert target.read_text() == "contenu"

    store.close()


def test_execute_move(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_text("img")

    destination = (
        tmp_path
        / "JPG"
        / "photo.jpg"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test",
        destination=destination,
        action=ActionKind.MOVE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert destination.exists()
    assert not source.exists()

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].kind == "move"

    undo_success, undo_errors = undo_batch(
        batch_id,
        store,
    )

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not destination.exists()

    store.close()


def test_explicit_copy_keeps_original_and_undo_removes_copy_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_text("img")

    destination = (
        tmp_path
        / "JPG"
        / "photo.jpg"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test",
        destination=destination,
        action=ActionKind.COPY,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert source.exists()
    assert destination.exists()
    assert destination.read_text() == "img"

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].kind == "copy"
    assert operations[0].status is OperationStatus.COMPLETED

    undo_success, undo_errors = undo_batch(
        batch_id,
        store,
    )

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not destination.exists()

    store.close()


def test_trash_is_never_implicitly_converted_to_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module

    trash_dir = tmp_path / "trash"

    monkeypatch.setattr(
        executor_module,
        "TRASH_DIR",
        trash_dir,
    )

    target = tmp_path / "dup.txt"
    target.write_text("contenu")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=target,
        size=target.stat().st_size,
        reason="test",
        action=ActionKind.TRASH,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not target.exists()
    assert any(trash_dir.iterdir())

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].kind == "delete"

    store.close()


def test_explicit_copy_action_does_not_depend_on_category(
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.txt"
    source.write_text("original")

    destination = (
        tmp_path
        / "Documents"
        / "document.txt"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.LARGE_FILE,
        path=source,
        size=source.stat().st_size,
        reason="copie explicite",
        destination=destination,
        action=ActionKind.COPY,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert source.exists()
    assert destination.exists()

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].kind == "copy"

    undo_success, undo_errors = undo_batch(
        batch_id,
        store,
    )

    assert undo_success == 1
    assert not undo_errors
    assert source.exists()
    assert not destination.exists()

    store.close()


def test_none_action_does_nothing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large.bin"
    source.write_text("important")

    destination = (
        tmp_path
        / "Elsewhere"
        / "large.bin"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="information seulement",
        destination=destination,
        action=ActionKind.NONE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert not errors
    assert source.exists()
    assert source.read_text() == "important"
    assert not destination.exists()

    # NONE n'est pas une opération disque.
    assert store.get_operations(batch_id) == []

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    store.close()


def test_trash_action_does_not_depend_on_category(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module

    trash_dir = tmp_path / "trash"

    monkeypatch.setattr(
        executor_module,
        "TRASH_DIR",
        trash_dir,
    )

    source = tmp_path / "old.bin"
    source.write_text("data")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.LARGE_FILE,
        path=source,
        size=source.stat().st_size,
        reason="test séparation catégorie/action",
        action=ActionKind.TRASH,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert not source.exists()
    assert any(trash_dir.iterdir())

    operations = store.get_operations(batch_id)

    assert len(operations) == 1
    assert operations[0].kind == "delete"

    store.close()


def test_move_without_destination_is_rejected(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_text("img")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="destination absente",
        action=ActionKind.MOVE,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "destination manquante" in errors[0]
    assert source.exists()

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    # L'échec logique est désormais conservé dans l'historique même si
    # aucune I/O n'a pu démarrer.
    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].kind == "move"
    assert operations[0].stored_path is None
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error == (
        "destination manquante pour une opération MOVE"
    )

    store.close()


def test_copy_without_destination_is_rejected(
    tmp_path: Path,
) -> None:
    source = tmp_path / "document.txt"
    source.write_text("data")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="destination absente",
        action=ActionKind.COPY,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "destination manquante" in errors[0]
    assert source.exists()

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].kind == "copy"
    assert operations[0].stored_path is None
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error == (
        "destination manquante pour une opération COPY"
    )

    store.close()


def test_move_failure_is_recorded_as_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """L'intention MOVE doit exister en base même si shutil.move échoue."""

    import file_janitor.executor as executor_module

    source = tmp_path / "source.txt"
    source.write_text("data")

    destination = (
        tmp_path
        / "destination"
        / "source.txt"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test échec",
        destination=destination,
        action=ActionKind.MOVE,
    )

    def failing_move(
        src: Path,
        dst: Path,
    ) -> None:
        raise OSError("move impossible")

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        failing_move,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "move impossible" in errors[0]

    operations = store.get_operations(batch_id)

    assert len(operations) == 1

    operation = operations[0]

    assert operation.kind == "move"
    assert operation.status is OperationStatus.FAILED
    assert operation.error == "move impossible"
    assert operation.original_path == source
    assert operation.stored_path == destination

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    assert source.exists()

    store.close()


def test_copy_failure_is_recorded_as_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Une erreur de copy2 doit être persistée dans l'historique."""

    import file_janitor.executor as executor_module

    source = tmp_path / "source.txt"
    source.write_text("data")

    destination = (
        tmp_path
        / "destination"
        / "source.txt"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test échec copy",
        destination=destination,
        action=ActionKind.COPY,
    )

    def failing_copy(
        src: str,
        dst: str,
    ) -> None:
        raise OSError("copy impossible")

    monkeypatch.setattr(
        executor_module.shutil,
        "copy2",
        failing_copy,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "copy impossible" in errors[0]

    operations = store.get_operations(batch_id)

    assert len(operations) == 1

    operation = operations[0]

    assert operation.kind == "copy"
    assert operation.status is OperationStatus.FAILED
    assert operation.error == "copy impossible"

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    assert source.exists()
    assert not destination.exists()

    store.close()


def test_trash_failure_is_recorded_as_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Une erreur d'envoi à la corbeille doit rester visible en base."""

    import file_janitor.executor as executor_module

    trash_dir = tmp_path / "trash"

    monkeypatch.setattr(
        executor_module,
        "TRASH_DIR",
        trash_dir,
    )

    source = tmp_path / "duplicate.txt"
    source.write_text("data")

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=source,
        size=source.stat().st_size,
        reason="doublon",
        action=ActionKind.TRASH,
    )

    def failing_trash_move(
        item: ActionItem,
        destination: Path,
    ) -> None:
        raise OSError("trash impossible")

    monkeypatch.setattr(
        executor_module,
        "_move_with_cross_filesystem_fallback",
        failing_trash_move,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "trash impossible" in errors[0]

    operations = store.get_operations(batch_id)

    assert len(operations) == 1

    operation = operations[0]

    assert operation.kind == "delete"
    assert operation.status is OperationStatus.FAILED
    assert operation.error == "trash impossible"

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    assert source.exists()

    store.close()


def test_early_source_failure_is_persisted_in_partial_batch(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    present = tmp_path / "present.txt"
    present.write_text("present")

    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=missing,
            size=7,
            reason="missing before execution",
            destination=tmp_path / "sorted" / missing.name,
            action=ActionKind.MOVE,
        ),
        ActionItem(
            category=FileCategory.TO_SORT,
            path=present,
            size=present.stat().st_size,
            reason="safe action",
            destination=tmp_path / "sorted" / present.name,
            action=ActionKind.MOVE,
        ),
    ]
    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        items,
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert len(errors) == 1
    assert "source introuvable" in errors[0]

    operations = store.get_operations(batch_id)
    assert len(operations) == 2
    assert operations[0].original_path == missing
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].stored_path == tmp_path / "sorted" / missing.name
    assert operations[0].error is not None
    assert "source introuvable" in operations[0].error
    assert operations[1].status is OperationStatus.COMPLETED

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert batch.planned_count == 2
    assert batch.success_count == 1
    assert batch.failed_count == 1
    assert batch.skipped_count == 0
    store.close()


def test_cancelled_batch_counts_survive_history_reopen(tmp_path: Path) -> None:
    sources = [tmp_path / f"{name}.txt" for name in ("a", "b", "c")]
    for source in sources:
        source.write_text(source.stem)

    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=source,
            size=source.stat().st_size,
            reason="cancel persistence",
            destination=tmp_path / "sorted" / source.name,
            action=ActionKind.MOVE,
        )
        for source in sources
    ]
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    checks = iter((False, True))

    batch_id, success, errors = execute_items(
        items,
        root=str(tmp_path),
        store=store,
        cancel_callback=lambda: next(checks),
    )

    assert success == 1
    assert errors == []
    store.close()

    reopened = HistoryStore(db_path=db_path)
    batch = reopened.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.CANCELLED
    assert batch.planned_count == 3
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 2
    operations = reopened.get_operations(batch_id)
    assert len(operations) == 3
    assert [operation.original_path for operation in operations] == sources
    assert [operation.status for operation in operations] == [
        OperationStatus.COMPLETED,
        OperationStatus.QUEUED,
        OperationStatus.QUEUED,
    ]
    reopened.close()


def test_history_store_migrates_execution_count_columns(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "legacy-history.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            root TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO batches (created_at, root, undone, status)
        VALUES ('2026-09-10T12:00:00', '/tmp/legacy', 0, 'completed')
        """
    )
    connection.commit()
    connection.close()

    store = HistoryStore(db_path=db_path)
    batch = store.get_batch(1)

    assert batch is not None
    assert batch.planned_count == 0
    assert batch.success_count == 0
    assert batch.failed_count == 0
    assert batch.skipped_count == 0
    store.close()


def test_mixed_success_and_failure_produces_partial_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Un batch avec succès + échec doit finir en PARTIAL."""

    import file_janitor.executor as executor_module

    first_source = tmp_path / "first.txt"
    first_source.write_text("first")

    second_source = tmp_path / "second.txt"
    second_source.write_text("second")

    first_destination = (
        tmp_path
        / "sorted"
        / "first.txt"
    )

    second_destination = (
        tmp_path
        / "sorted"
        / "second.txt"
    )

    first_item = ActionItem(
        category=FileCategory.TO_SORT,
        path=first_source,
        size=first_source.stat().st_size,
        reason="premier",
        destination=first_destination,
        action=ActionKind.MOVE,
    )

    second_item = ActionItem(
        category=FileCategory.TO_SORT,
        path=second_source,
        size=second_source.stat().st_size,
        reason="second",
        destination=second_destination,
        action=ActionKind.MOVE,
    )

    real_move = executor_module._rename_noreplace

    def move_with_second_failure(
        src: Path,
        dst: Path,
    ) -> None:
        if src == second_source:
            raise OSError("second move impossible")

        real_move(src, dst)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        move_with_second_failure,
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id, success, errors = execute_items(
        [first_item, second_item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert len(errors) == 1
    assert "second move impossible" in errors[0]

    assert first_destination.exists()
    assert not first_source.exists()

    assert second_source.exists()
    assert not second_destination.exists()

    operations = store.get_operations(batch_id)

    assert len(operations) == 2

    assert operations[0].status is OperationStatus.COMPLETED
    assert operations[0].error is None

    assert operations[1].status is OperationStatus.FAILED
    assert operations[1].error == "second move impossible"

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL

    store.close()


def test_operation_is_planned_before_move_io(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Au moment où shutil.move démarre, l'opération doit déjà être PLANNED."""

    import file_janitor.executor as executor_module

    source = tmp_path / "source.txt"
    source.write_text("data")

    destination = (
        tmp_path
        / "sorted"
        / "source.txt"
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    item = ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="ordre transactionnel",
        destination=destination,
        action=ActionKind.MOVE,
    )

    real_move = executor_module._rename_noreplace
    observed_statuses: list[OperationStatus] = []

    def inspecting_move(
        src: Path,
        dst: Path,
    ) -> None:
        operations = store.get_operations(1)

        assert len(operations) == 1

        observed_statuses.append(
            operations[0].status
        )

        real_move(src, dst)

    monkeypatch.setattr(
        executor_module,
        "_rename_noreplace",
        inspecting_move,
    )

    batch_id, success, errors = execute_items(
        [item],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert observed_statuses == [
        OperationStatus.PLANNED
    ]

    operations = store.get_operations(batch_id)

    assert operations[0].status is OperationStatus.COMPLETED

    store.close()


def test_execute_can_cancel_between_actions_and_undo_completed_subset(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one")
    second.write_text("two")
    store = HistoryStore(db_path=tmp_path / "history.db")
    checks = iter((False, True))

    batch_id, success, errors = execute_items(
        [
            ActionItem(
                category=FileCategory.TO_SORT,
                path=first,
                size=3,
                reason="sort",
                destination=tmp_path / "sorted" / first.name,
                action=ActionKind.MOVE,
            ),
            ActionItem(
                category=FileCategory.TO_SORT,
                path=second,
                size=3,
                reason="sort",
                destination=tmp_path / "sorted" / second.name,
                action=ActionKind.MOVE,
            ),
        ],
        root=str(tmp_path),
        store=store,
        cancel_callback=lambda: next(checks),
    )

    assert success == 1
    assert errors == []
    assert not first.exists()
    assert (tmp_path / "sorted" / "first.txt").exists()
    assert second.exists()
    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.CANCELLED
    assert batch.planned_count == 2
    assert batch.success_count == 1
    assert batch.failed_count == 0
    assert batch.skipped_count == 1

    undo_success, undo_errors = undo_batch(batch_id, store)
    assert undo_success == 1
    assert undo_errors == []
    assert first.exists()
    assert second.exists()
    store.close()


def test_keeper_failure_blocks_dependent_duplicate_trash_actions(
    tmp_path: Path,
) -> None:
    from file_janitor.models import ConflictPolicy

    keeper = tmp_path / "keeper.txt"
    duplicate_a = tmp_path / "duplicate-a.txt"
    duplicate_b = tmp_path / "duplicate-b.txt"
    keeper.write_text("same")
    duplicate_a.write_text("same")
    duplicate_b.write_text("same")

    destination = tmp_path / "sorted" / keeper.name
    destination.parent.mkdir()
    destination.write_text("external")

    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=keeper,
            size=keeper.stat().st_size,
            reason="Classer le keeper",
            destination=destination,
            action=ActionKind.MOVE,
            conflict_policy=ConflictPolicy.SKIP,
        ),
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate_a,
            size=duplicate_a.stat().st_size,
            reason="Doublon",
            action=ActionKind.TRASH,
        ),
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate_b,
            size=duplicate_b.stat().st_size,
            reason="Doublon",
            action=ActionKind.TRASH,
        ),
    ]

    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        batch_id, success, errors = execute_items(
            items,
            root=str(tmp_path),
            store=store,
            dependent_trash_keepers={
                duplicate_a: keeper,
                duplicate_b: keeper,
            },
        )
        batch = store.get_batch(batch_id)
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    assert success == 0
    assert len(errors) == 3
    assert "destination déjà existante" in errors[0]
    assert "keeper" in errors[1]
    assert "keeper" in errors[2]

    assert keeper.read_text() == "same"
    assert duplicate_a.read_text() == "same"
    assert duplicate_b.read_text() == "same"
    assert destination.read_text() == "external"

    assert batch is not None
    assert batch.status is BatchStatus.FAILED
    assert [operation.kind for operation in operations] == [
        "move",
        "delete",
        "delete",
    ]
    assert all(
        operation.status is OperationStatus.FAILED
        for operation in operations
    )
    assert operations[1].stored_path is None
    assert operations[2].stored_path is None


def test_successful_keeper_action_allows_dependent_duplicate_trash_actions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module

    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    keeper = tmp_path / "keeper.txt"
    duplicate_a = tmp_path / "duplicate-a.txt"
    duplicate_b = tmp_path / "duplicate-b.txt"
    keeper.write_text("same")
    duplicate_a.write_text("same")
    duplicate_b.write_text("same")

    destination = tmp_path / "sorted" / keeper.name
    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=keeper,
            size=keeper.stat().st_size,
            reason="Classer le keeper",
            destination=destination,
            action=ActionKind.MOVE,
        ),
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate_a,
            size=duplicate_a.stat().st_size,
            reason="Doublon",
            action=ActionKind.TRASH,
        ),
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate_b,
            size=duplicate_b.stat().st_size,
            reason="Doublon",
            action=ActionKind.TRASH,
        ),
    ]

    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        from file_janitor.executor import KeeperGuard
        from file_janitor.path_safety import current_file_identity

        keeper_guard = KeeperGuard(keeper, current_file_identity(keeper))
        batch_id, success, errors = execute_items(
            items,
            root=str(tmp_path),
            store=store,
            dependent_trash_keepers={
                duplicate_a: keeper,
                duplicate_b: keeper,
            },
            dependent_trash_keeper_guards={
                duplicate_a: keeper_guard,
                duplicate_b: keeper_guard,
            },
        )
        batch = store.get_batch(batch_id)
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    assert success == 3
    assert errors == []
    assert not keeper.exists()
    assert destination.read_text() == "same"
    assert not duplicate_a.exists()
    assert not duplicate_b.exists()
    assert len(list(trash_dir.iterdir())) == 2

    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED
    assert [operation.kind for operation in operations] == [
        "move",
        "delete",
        "delete",
    ]
    assert all(
        operation.status is OperationStatus.COMPLETED
        for operation in operations
    )


def test_live_keeper_guard_blocks_trash_when_static_keeper_changes(
    tmp_path: Path,
) -> None:
    from file_janitor.executor import KeeperGuard
    from file_janitor.path_safety import current_file_identity

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    guard = KeeperGuard(keeper, current_file_identity(keeper))
    keeper.write_text("changed-after-preflight")

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=duplicate,
        size=duplicate.stat().st_size,
        reason="Doublon",
        action=ActionKind.TRASH,
    )
    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        batch_id, success, errors = execute_items(
            [item],
            root=str(tmp_path),
            store=store,
            dependent_trash_keeper_guards={duplicate: guard},
        )
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    assert success == 0
    assert len(errors) == 1
    assert "keeper" in errors[0]
    assert "changé depuis le préflight" in errors[0]
    assert duplicate.exists()
    assert keeper.read_text() == "changed-after-preflight"
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED



def test_live_keeper_guard_allows_trash_when_static_keeper_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module
    from file_janitor.executor import KeeperGuard
    from file_janitor.path_safety import current_file_identity

    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    guard = KeeperGuard(keeper, current_file_identity(keeper))

    item = ActionItem(
        category=FileCategory.DUPLICATE,
        path=duplicate,
        size=duplicate.stat().st_size,
        reason="Doublon",
        action=ActionKind.TRASH,
    )
    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        batch_id, success, errors = execute_items(
            [item],
            root=str(tmp_path),
            store=store,
            dependent_trash_keeper_guards={duplicate: guard},
        )
        batch = store.get_batch(batch_id)
    finally:
        store.close()

    assert success == 1
    assert errors == []
    assert keeper.read_text() == "same"
    assert not duplicate.exists()
    assert len(list(trash_dir.iterdir())) == 1
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

def test_live_keeper_guard_revalidates_published_keeper_before_trash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import file_janitor.executor as executor_module
    from file_janitor.executor import KeeperGuard
    from file_janitor.path_safety import current_file_identity

    trash_dir = tmp_path / "trash"
    monkeypatch.setattr(executor_module, "TRASH_DIR", trash_dir)

    keeper = tmp_path / "keeper.txt"
    duplicate = tmp_path / "duplicate.txt"
    keeper.write_text("same")
    duplicate.write_text("same")
    destination = tmp_path / "sorted" / "keeper.txt"
    guard = KeeperGuard(keeper, current_file_identity(keeper))

    items = [
        ActionItem(
            category=FileCategory.TO_SORT,
            path=keeper,
            size=keeper.stat().st_size,
            reason="Classer le keeper",
            destination=destination,
            action=ActionKind.MOVE,
        ),
        ActionItem(
            category=FileCategory.DUPLICATE,
            path=duplicate,
            size=duplicate.stat().st_size,
            reason="Doublon",
            action=ActionKind.TRASH,
        ),
    ]

    real_move = executor_module._move_with_cross_filesystem_fallback

    def mutate_published_keeper(item, target):
        identity = real_move(item, target)
        if item.path == keeper:
            target.write_text("changed-after-keeper-move")
        return identity

    monkeypatch.setattr(
        executor_module,
        "_move_with_cross_filesystem_fallback",
        mutate_published_keeper,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")
    try:
        batch_id, success, errors = execute_items(
            items,
            root=str(tmp_path),
            store=store,
            dependent_trash_keepers={duplicate: keeper},
            dependent_trash_keeper_guards={duplicate: guard},
        )
        batch = store.get_batch(batch_id)
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    assert success == 1
    assert len(errors) == 1
    assert "keeper" in errors[0]
    assert "changé depuis le préflight" in errors[0]
    assert destination.read_text() == "changed-after-keeper-move"
    assert duplicate.exists()
    assert batch is not None
    assert batch.status is BatchStatus.PARTIAL
    assert [op.status for op in operations] == [
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
    ]
