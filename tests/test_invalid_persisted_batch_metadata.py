"""4E-25A: lecture tolérante des métadonnées de batch persistées."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore


def _new_batch(store: HistoryStore, root: Path) -> int:
    return store.start_batch(str(root), planned_count=1)


def _update_batch(
    store: HistoryStore,
    batch_id: int,
    updates: dict[str, object],
) -> None:
    allowed = {
        "root",
        "undone",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
    }
    assert set(updates).issubset(allowed)
    assignments = ", ".join(f"{column} = ?" for column in updates)
    with store._cursor() as cursor:
        cursor.execute(
            f"UPDATE batches SET {assignments} WHERE id = ?",
            (*updates.values(), batch_id),
        )


def test_valid_batch_metadata_has_no_raw_values(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.root == str(tmp_path)
    assert batch.undone is False
    assert batch.planned_count == 1
    assert batch.success_count == 0
    assert batch.failed_count == 0
    assert batch.skipped_count == 0
    assert batch.root_raw is None
    assert batch.undone_raw is None
    assert batch.planned_count_raw is None
    assert batch.success_count_raw is None
    assert batch.failed_count_raw is None
    assert batch.skipped_count_raw is None
    store.close()


@pytest.mark.parametrize(
    "invalid_root",
    (
        "",
        "relative/root",
        b"not-text",
        "/tmp/\0invalid",
    ),
)
def test_invalid_batch_root_is_preserved_as_raw_value(
    tmp_path: Path,
    invalid_root: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    _update_batch(store, batch_id, {"root": invalid_root})

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.root is None
    assert batch.root_raw == invalid_root
    store.close()


@pytest.mark.parametrize(
    ("persisted", "expected"),
    ((0, False), (1, True)),
)
def test_valid_undone_values_are_strict_booleans(
    tmp_path: Path,
    persisted: int,
    expected: bool,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    _update_batch(store, batch_id, {"undone": persisted})

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.undone is expected
    assert batch.undone_raw is None
    store.close()


@pytest.mark.parametrize(
    "invalid_undone",
    (-1, 2, "yes", b"not-an-integer"),
)
def test_invalid_undone_is_preserved_without_boolean_coercion(
    tmp_path: Path,
    invalid_undone: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    _update_batch(store, batch_id, {"undone": invalid_undone})

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.undone is None
    assert batch.undone_raw == invalid_undone
    store.close()


@pytest.mark.parametrize(
    ("column", "attribute", "raw_attribute", "invalid_value"),
    (
        ("planned_count", "planned_count", "planned_count_raw", -1),
        ("planned_count", "planned_count", "planned_count_raw", "bad"),
        ("success_count", "success_count", "success_count_raw", -1),
        ("failed_count", "failed_count", "failed_count_raw", "bad"),
        ("skipped_count", "skipped_count", "skipped_count_raw", b"bad"),
    ),
)
def test_invalid_batch_count_is_preserved_as_raw_value(
    tmp_path: Path,
    column: str,
    attribute: str,
    raw_attribute: str,
    invalid_value: object,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    _update_batch(store, batch_id, {column: invalid_value})

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert getattr(batch, attribute) is None
    assert getattr(batch, raw_attribute) == invalid_value
    store.close()


def test_invalid_batch_fields_are_preserved_independently(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    updates = {
        "root": b"invalid-root",
        "undone": 2,
        "planned_count": -1,
        "success_count": "bad-success",
        "failed_count": b"bad-failed",
        "skipped_count": -2,
    }
    _update_batch(store, batch_id, updates)

    batch = store.get_batch(batch_id)

    assert batch is not None
    for attribute in (
        "root",
        "undone",
        "planned_count",
        "success_count",
        "failed_count",
        "skipped_count",
    ):
        assert getattr(batch, attribute) is None
    assert batch.root_raw == updates["root"]
    assert batch.undone_raw == updates["undone"]
    assert batch.planned_count_raw == updates["planned_count"]
    assert batch.success_count_raw == updates["success_count"]
    assert batch.failed_count_raw == updates["failed_count"]
    assert batch.skipped_count_raw == updates["skipped_count"]
    store.close()


def test_individually_valid_inconsistent_counts_remain_visible(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = _new_batch(store, tmp_path)
    _update_batch(
        store,
        batch_id,
        {
            "planned_count": 1,
            "success_count": 2,
            "failed_count": 3,
            "skipped_count": 4,
        },
    )

    batch = store.get_batch(batch_id)

    assert batch is not None
    assert batch.planned_count == 1
    assert batch.success_count == 2
    assert batch.failed_count == 3
    assert batch.skipped_count == 4
    assert batch.planned_count_raw is None
    assert batch.success_count_raw is None
    assert batch.failed_count_raw is None
    assert batch.skipped_count_raw is None
    store.close()
