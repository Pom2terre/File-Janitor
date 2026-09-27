"""4E-29C : concurrence SQLite entre plusieurs instances HistoryStore."""

from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
from pathlib import Path

from file_janitor.storage.history import HistoryStore


def test_concurrent_instances_create_unique_batches_without_loss(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    bootstrap = HistoryStore(db_path=db_path)
    bootstrap.close()
    workers = 4
    per_worker = 12
    barrier = threading.Barrier(workers)

    def create_batches(worker: int) -> list[int]:
        store = HistoryStore(db_path=db_path)
        try:
            barrier.wait(timeout=10)
            return [
                store.start_batch(
                    str(tmp_path / f"worker-{worker}"),
                    planned_count=1,
                )
                for _ in range(per_worker)
            ]
        finally:
            store.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(create_batches, worker)
            for worker in range(workers)
        ]
        results = [future.result(timeout=20) for future in futures]

    ids = [batch_id for worker_ids in results for batch_id in worker_ids]
    connection = sqlite3.connect(db_path)
    try:
        total = connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        distinct = connection.execute(
            "SELECT COUNT(DISTINCT id) FROM batches"
        ).fetchone()[0]
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()

    assert len(ids) == workers * per_worker
    assert len(set(ids)) == len(ids)
    assert total == len(ids)
    assert distinct == len(ids)
    assert quick_check == "ok"
    assert not violations


def test_reader_remains_available_during_reserved_writer(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    writer = HistoryStore(db_path=db_path)
    reader = HistoryStore(db_path=db_path)
    try:
        batch_id = writer.start_batch(str(tmp_path), planned_count=1)
        writer._conn.execute("BEGIN IMMEDIATE")
        writer._conn.execute(
            "UPDATE batches SET planned_count = planned_count + 1 WHERE id = ?",
            (batch_id,),
        )
        started = time.monotonic()
        observed = reader.get_batch(batch_id)
        elapsed = time.monotonic() - started
        assert observed is not None
        assert elapsed < 1.0
        writer._conn.rollback()
    finally:
        writer.close()
        reader.close()


def test_writer_timeout_rolls_back_without_partial_batch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    holder = HistoryStore(db_path=db_path)
    contender = HistoryStore(db_path=db_path)
    try:
        before = holder._conn.execute(
            "SELECT COUNT(*) FROM batches"
        ).fetchone()[0]
        holder._conn.execute("BEGIN IMMEDIATE")
        contender._conn.execute("PRAGMA busy_timeout = 100")
        started = time.monotonic()
        try:
            contender.start_batch(str(tmp_path / "blocked"), planned_count=1)
        except sqlite3.OperationalError as error:
            elapsed = time.monotonic() - started
            assert "locked" in str(error).lower()
            assert 0.05 <= elapsed < 2.0
            assert not contender._conn.in_transaction
        else:
            raise AssertionError("le writer concurrent n'a pas expiré")
        finally:
            holder._conn.rollback()

        after = contender._conn.execute(
            "SELECT COUNT(*) FROM batches"
        ).fetchone()[0]
        assert after == before
    finally:
        holder.close()
        contender.close()


def test_waiting_writer_commits_after_lock_release(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    holder = HistoryStore(db_path=db_path)
    holder._conn.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    outcome: dict[str, object] = {}

    def compete() -> None:
        started.set()
        began = time.monotonic()
        try:
            store = HistoryStore(db_path=db_path)
            try:
                outcome["batch_id"] = store.start_batch(
                    str(tmp_path / "contender"),
                    planned_count=1,
                )
            finally:
                store.close()
        except BaseException as error:  # pragma: no cover
            outcome["error"] = repr(error)
        outcome["elapsed"] = time.monotonic() - began

    thread = threading.Thread(target=compete, daemon=True)
    thread.start()
    assert started.wait(timeout=2)
    time.sleep(0.25)
    assert thread.is_alive()
    holder._conn.rollback()
    thread.join(timeout=10)
    try:
        assert not thread.is_alive()
        assert "error" not in outcome
        assert isinstance(outcome.get("batch_id"), int)
        assert 0.15 <= float(outcome["elapsed"]) < 6.0
    finally:
        holder.close()
