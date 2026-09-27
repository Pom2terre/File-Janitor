"""4E-22C: les APIs normales refusent un kind inconnu en écriture."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_janitor.storage.history import HistoryStore, OperationStatus


def _record(store: HistoryStore, batch_id: int, root: Path, kind: str) -> int:
    return store.record_operation(
        batch_id,
        kind=kind,
        original_path=root / f"{kind or 'empty'}-original.txt",
        stored_path=root / f"{kind or 'empty'}-stored.txt",
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


def test_record_operation_accepts_only_canonical_kinds(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=3)

    operation_ids = [_record(store, batch_id, tmp_path, kind) for kind in ("copy", "move", "delete")]

    assert len(operation_ids) == 3
    assert [operation.kind for operation in store.get_operations(batch_id)] == ["copy", "move", "delete"]
    store.close()


def test_record_operation_rejects_unknown_kinds_without_insert(
    tmp_path: Path,
) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=3)

    for kind in ("rename", "COPY", ""):
        with pytest.raises(ValueError, match="type d’opération inconnu"):
            _record(store, batch_id, tmp_path, kind)
        assert store.get_operations(batch_id) == []

    store.close()
