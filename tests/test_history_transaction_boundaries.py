"""4E-29A : frontières transactionnelles explicites de l'historique."""

from __future__ import annotations

import ast
import inspect
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import file_janitor.storage.history as history_module
from file_janitor.storage.history import BatchStatus, HistoryStore


EXPECTED_MUTATORS = {
    "annotate_operation_recovery",
    "finish_batch_execution",
    "reconcile_running_batches",
    "record_operation",
    "record_queued_operations",
    "resolve_operation_recovery",
    "set_batch_status",
    "set_operation_recovery_audit",
    "set_operation_status",
    "set_operation_stored_identity",
    "start_queued_operation",
    "start_batch",
    "start_batch_resume",
}
DIRECT_DML_MUTATORS = EXPECTED_MUTATORS - {"reconcile_running_batches"}


def _has_dml(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        normalized = " ".join(child.value.upper().split())
        if re.search(r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\b", normalized):
            return True
    return False


def test_every_public_history_mutation_declares_a_write_transaction() -> None:
    source = inspect.getsource(history_module.HistoryStore)
    tree = ast.parse(source)
    class_node = tree.body[0]
    assert isinstance(class_node, ast.ClassDef)
    methods = {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    mutators = {
        name
        for name, node in methods.items()
        if not name.startswith("_") and _has_dml(node)
    }
    assert mutators == DIRECT_DML_MUTATORS
    for name in sorted(EXPECTED_MUTATORS):
        segment = ast.get_source_segment(source, methods[name]) or ""
        assert "self._cursor(write=True)" in segment, name


def test_write_transaction_begins_immediately_before_insert(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        store.start_batch(str(tmp_path), planned_count=1)
    finally:
        store._conn.set_trace_callback(None)
        store.close()

    normalized = [statement.strip().upper() for statement in statements]
    begin_index = normalized.index("BEGIN IMMEDIATE")
    insert_index = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith("INSERT INTO BATCHES")
    )
    commit_index = normalized.index("COMMIT")
    assert begin_index < insert_index < commit_index


def test_read_transaction_does_not_reserve_write_lock(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        assert store.get_batch(batch_id) is not None
    finally:
        store._conn.set_trace_callback(None)
        store.close()

    assert "BEGIN IMMEDIATE" not in {
        statement.strip().upper() for statement in statements
    }


def test_failed_mutation_rolls_back_and_releases_transaction(tmp_path: Path) -> None:
    store = HistoryStore(db_path=tmp_path / "history.db")
    batch_id = store.start_batch(str(tmp_path), planned_count=1)
    before = store._conn.execute(
        "SELECT status, undone FROM batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    store._conn.executescript(
        """
        CREATE TRIGGER reject_batch_status
        BEFORE UPDATE OF status ON batches
        BEGIN
            SELECT RAISE(ABORT, 'blocked by 4e29a test');
        END;
        """
    )
    store._conn.commit()
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="blocked by 4e29a"):
            store.set_batch_status(batch_id, BatchStatus.FAILED)
        after = store._conn.execute(
            "SELECT status, undone FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        assert tuple(after) == tuple(before)
        assert not store._conn.in_transaction
    finally:
        store._conn.set_trace_callback(None)
        store.close()

    normalized = [statement.strip().upper() for statement in statements]
    assert "BEGIN IMMEDIATE" in normalized
    assert "ROLLBACK" in normalized


def test_competing_store_waits_for_writer_then_commits(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    first = HistoryStore(db_path=db_path)
    batch_id = first.start_batch(str(tmp_path), planned_count=1)
    first._conn.execute("BEGIN IMMEDIATE")

    started = threading.Event()
    errors: list[BaseException] = []

    def compete() -> None:
        started.set()
        try:
            second = HistoryStore(db_path=db_path)
            try:
                second.set_batch_status(batch_id, BatchStatus.FAILED)
            finally:
                second.close()
        except BaseException as exc:  # pragma: no cover - rapporté au thread principal
            errors.append(exc)

    thread = threading.Thread(target=compete, daemon=True)
    thread.start()
    assert started.wait(timeout=2)
    time.sleep(0.1)
    assert thread.is_alive()

    first._conn.rollback()
    thread.join(timeout=10)
    try:
        assert not thread.is_alive()
        assert not errors
        row = first._conn.execute(
            "SELECT status FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        assert row is not None
        assert str(row[0]) == BatchStatus.FAILED.value
    finally:
        first.close()
