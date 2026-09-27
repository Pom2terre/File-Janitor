"""4E-23A: lecture tolérante des chemins persistés invalides."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore, OperationStatus


def _record_valid_operation(
    store: HistoryStore,
    batch_id: int,
    root: Path,
    *,
    stored_path: Path | None = None,
) -> int:
    return store.record_operation(
        batch_id,
        kind="move",
        original_path=root / "original.txt",
        stored_path=(
            root / "stored.txt" if stored_path is None else stored_path
        ),
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


def _corrupt_paths(
    store: HistoryStore,
    operation_id: int,
    *,
    original_path: object,
    stored_path: object,
) -> None:
    with store._cursor() as cursor:
        cursor.execute(
            (
                "UPDATE operations "
                "SET original_path = ?, stored_path = ? "
                "WHERE id = ?"
            ),
            (original_path, stored_path, operation_id),
        )


def test_valid_absolute_paths_have_no_raw_values(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _record_valid_operation(store, batch_id, tmp_path)

    operation = store.get_operations(batch_id)[0]

    assert operation.original_path == tmp_path / "original.txt"
    assert operation.stored_path == tmp_path / "stored.txt"
    assert operation.original_path_raw is None
    assert operation.stored_path_raw is None
    store.close()


def test_absent_stored_path_has_no_raw_value(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = store.record_operation(
        batch_id,
        kind="delete",
        original_path=tmp_path / "original.txt",
        stored_path=None,
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )

    operation = store.get_operations(batch_id)[0]

    assert operation.id == operation_id
    assert operation.stored_path is None
    assert operation.stored_path_raw is None
    store.close()


@pytest.mark.parametrize(
    "invalid_path",
    (
        "",
        "../outside.txt",
        "/tmp/invalid\x00path",
    ),
)
def test_invalid_text_paths_are_preserved_as_raw_values(
    tmp_path: Path,
    invalid_path: str,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_valid_operation(store, batch_id, tmp_path)
    _corrupt_paths(
        store,
        operation_id,
        original_path=invalid_path,
        stored_path=invalid_path,
    )

    operation = store.get_operations(batch_id)[0]

    assert operation.original_path is None
    assert operation.stored_path is None
    assert operation.original_path_raw == invalid_path
    assert operation.stored_path_raw == invalid_path
    store.close()


@pytest.mark.parametrize(
    ("column", "path_attribute", "raw_attribute"),
    (
        ("original_path", "original_path", "original_path_raw"),
        ("stored_path", "stored_path", "stored_path_raw"),
    ),
)
def test_non_text_path_is_preserved_without_breaking_history_read(
    tmp_path: Path,
    column: str,
    path_attribute: str,
    raw_attribute: str,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_valid_operation(store, batch_id, tmp_path)
    query = f"UPDATE operations SET {column} = ? WHERE id = ?"
    with store._cursor() as cursor:
        cursor.execute(query, (b"not-text", operation_id))

    operation = store.get_operations(batch_id)[0]

    assert getattr(operation, path_attribute) is None
    assert getattr(operation, raw_attribute) == b"not-text"
    store.close()
