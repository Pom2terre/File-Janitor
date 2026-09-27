"""4E-23B: propagation et rendu des chemins persistés invalides."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from file_janitor.application.service import (
    _history_operation_core_metadata_issues,
    get_history_operation_summary,
)
from file_janitor.gui.main_window import MainWindow
from file_janitor.storage.history import HistoryStore, OperationStatus


def _record_operation(
    store: HistoryStore,
    batch_id: int,
    root: Path,
    *,
    kind: str = "move",
    stored_path: Path | None = None,
) -> int:
    return store.record_operation(
        batch_id,
        kind=kind,
        original_path=root / "original.txt",
        stored_path=(
            root / "stored.txt"
            if stored_path is None and kind != "delete"
            else stored_path
        ),
        size=1,
        category="to_sort",
        status=OperationStatus.COMPLETED,
    )


@pytest.mark.parametrize(
    "invalid_path",
    (
        "",
        "../outside.txt",
        "/tmp/invalid\x00path",
        b"not-text",
    ),
)
def test_history_summary_preserves_and_classifies_invalid_paths(
    tmp_path: Path,
    invalid_path: object,
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    operation_id = _record_operation(store, batch_id, tmp_path)
    with store._cursor() as cursor:
        cursor.execute(
            (
                "UPDATE operations "
                "SET original_path = ?, stored_path = ? "
                "WHERE id = ?"
            ),
            (invalid_path, invalid_path, operation_id),
        )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.original_path is None
    assert summary.stored_path is None
    assert summary.original_path_raw == invalid_path
    assert summary.stored_path_raw == invalid_path
    assert summary.core_metadata_issues == (
        "invalid_original_path",
        "invalid_stored_path",
    )


def test_history_summary_distinguishes_absent_stored_path(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=db_path)
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    _record_operation(
        store,
        batch_id,
        tmp_path,
        kind="delete",
        stored_path=None,
    )
    store.close()

    summary = get_history_operation_summary(batch_id, db_path=db_path)[0]

    assert summary.original_path == tmp_path / "original.txt"
    assert summary.original_path_raw is None
    assert summary.stored_path is None
    assert summary.stored_path_raw is None
    assert summary.core_metadata_issues == ()


def test_path_issues_compose_with_existing_core_metadata_issues() -> None:
    operation = SimpleNamespace(
        status_raw="future-status",
        kind="future-kind",
        original_path_raw="",
        stored_path_raw=b"not-text",
    )

    assert _history_operation_core_metadata_issues(operation) == (
        "unknown_status",
        "unknown_kind",
        "invalid_original_path",
        "invalid_stored_path",
    )


def test_gui_labels_invalid_path_metadata() -> None:
    assert MainWindow._history_core_metadata_issue_text(
        ("invalid_original_path", "invalid_stored_path")
    ) == (
        "Métadonnées cœur incohérentes : chemin d’origine invalide, "
        "chemin stocké invalide"
    )


def test_gui_path_text_distinguishes_valid_invalid_and_absent_paths() -> None:
    valid = Path("/tmp/valid.txt")

    assert MainWindow._history_path_text(
        valid,
        None,
        absent_text="—",
    ) == str(valid)
    assert MainWindow._history_path_text(
        None,
        "",
        absent_text="Chemin indisponible",
    ) == "Valeur persistée invalide ('')"
    assert MainWindow._history_path_text(
        None,
        b"not-text",
        absent_text="—",
    ) == "Valeur persistée invalide (b'not-text')"
    assert MainWindow._history_path_text(
        None,
        None,
        absent_text="—",
    ) == "—"
