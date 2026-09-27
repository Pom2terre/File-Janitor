"""4E-24A: lecture tolérante des métadonnées scalaires persistées."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore, OperationStatus


def _record_valid_operation(
    store: HistoryStore,
    batch_id: int,
    root: Path,
) -> int:
    return store.record_operation(
        batch_id,
        kind="move",
        original_path=root / "original.txt",
        stored_path=root / "stored.txt",
        size=123,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


def _corrupt_scalars(
    store: HistoryStore,
    operation_id: int,
    *,
    size: object = 123,
    category: object = "to_sort",
) -> None:
    with store._cursor() as cursor:
        cursor.execute(
            "UPDATE operations SET size = ?, category = ? WHERE id = ?",
            (size, category, operation_id),
        )


def test_valid_scalars_have_no_raw_values(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _record_valid_operation(store, batch_id, tmp_path)

    operation = store.get_operations(batch_id)[0]

    assert operation.size == 123
    assert operation.category == "to_sort"
    assert operation.size_raw is None
    assert operation.category_raw is None
    store.close()


@pytest.mark.parametrize(
    "invalid_size",
    (
        -1,
        "not-an-integer",
        1.5,
        b"not-an-integer",
    ),
)
def test_invalid_size_is_preserved_as_raw_value(
    tmp_path: Path,
    invalid_size: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_valid_operation(store, batch_id, tmp_path)
    _corrupt_scalars(store, operation_id, size=invalid_size)

    operation = store.get_operations(batch_id)[0]

    assert operation.size is None
    assert operation.size_raw == invalid_size
    assert operation.category == "to_sort"
    assert operation.category_raw is None
    store.close()


@pytest.mark.parametrize(
    "invalid_category",
    (
        "",
        "   ",
        b"not-text",
    ),
)
def test_invalid_category_is_preserved_as_raw_value(
    tmp_path: Path,
    invalid_category: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_valid_operation(store, batch_id, tmp_path)
    _corrupt_scalars(store, operation_id, category=invalid_category)

    operation = store.get_operations(batch_id)[0]

    assert operation.size == 123
    assert operation.size_raw is None
    assert operation.category is None
    assert operation.category_raw == invalid_category
    store.close()


def test_invalid_size_and_category_are_preserved_independently(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_valid_operation(store, batch_id, tmp_path)
    _corrupt_scalars(
        store,
        operation_id,
        size=b"invalid-size",
        category=b"invalid-category",
    )

    operation = store.get_operations(batch_id)[0]

    assert operation.size is None
    assert operation.size_raw == b"invalid-size"
    assert operation.category is None
    assert operation.category_raw == b"invalid-category"
    store.close()
