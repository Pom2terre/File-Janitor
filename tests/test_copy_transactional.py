"""Tests 2G-A/2G-B/2G-C : COPY transactionnel, no-clobber et durable."""

from __future__ import annotations

from pathlib import Path

import file_janitor.executor as executor_module
from file_janitor.executor import execute_items
from file_janitor.models import ActionItem, ActionKind, FileCategory
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
)


def _copy_item(source: Path, destination: Path) -> ActionItem:
    return ActionItem(
        category=FileCategory.TO_SORT,
        path=source,
        size=source.stat().st_size,
        reason="test COPY transactionnel",
        destination=destination,
        action=ActionKind.COPY,
    )


def _temporary_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []

    return list(
        directory.glob(
            f"{executor_module._COPY_TEMP_PREFIX}*.tmp"
        )
    )


def test_copy_is_written_to_temp_before_final_destination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("contenu complet")

    destination = tmp_path / "Copies" / "source.txt"

    real_copy2 = executor_module.shutil.copy2

    def observing_copy2(src: str, dst: str):
        temp_path = Path(dst)

        assert temp_path != destination
        assert temp_path.parent == destination.parent
        assert temp_path.name.startswith(
            executor_module._COPY_TEMP_PREFIX
        )
        assert not destination.exists()

        result = real_copy2(src, dst)

        # Même une fois le temporaire entièrement écrit, le nom final ne doit
        # pas être visible avant la publication.
        assert not destination.exists()

        return result

    monkeypatch.setattr(
        executor_module.shutil,
        "copy2",
        observing_copy2,
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert source.exists()
    assert destination.read_text() == "contenu complet"
    assert _temporary_files(destination.parent) == []

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.COMPLETED

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    store.close()


def test_copy_failure_removes_partial_temp_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("contenu complet")

    destination = tmp_path / "Copies" / "source.txt"

    def failing_copy2(src: str, dst: str):
        temp_path = Path(dst)
        temp_path.write_text("PARTIAL")
        raise OSError("copy temporaire impossible")

    monkeypatch.setattr(
        executor_module.shutil,
        "copy2",
        failing_copy2,
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "copy temporaire impossible" in errors[0]
    assert source.read_text() == "contenu complet"
    assert not destination.exists()
    assert _temporary_files(destination.parent) == []

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error == "copy temporaire impossible"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_publication_failure_removes_completed_temp_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("contenu complet")

    destination = tmp_path / "Copies" / "source.txt"

    def failing_link(src, dst):
        raise OSError("publication impossible")

    monkeypatch.setattr(
        executor_module.os,
        "link",
        failing_link,
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "publication impossible" in errors[0]
    assert source.read_text() == "contenu complet"
    assert not destination.exists()
    assert _temporary_files(destination.parent) == []

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error == "publication impossible"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()

def test_concurrent_destination_is_never_overwritten(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Une destination apparue au dernier moment doit rester intacte."""

    source = tmp_path / "source.txt"
    source.write_text("SOURCE")

    destination = tmp_path / "Copies" / "source.txt"

    real_link = executor_module.os.link

    def racing_link(src, dst):
        final_path = Path(dst)

        # Simule un autre processus qui gagne la course après la validation et
        # après la copie complète dans le temporaire, juste avant publication.
        final_path.write_text("CONCURRENT")

        # La primitive réelle doit alors refuser de créer le hard link.
        return real_link(src, dst)

    monkeypatch.setattr(
        executor_module.os,
        "link",
        racing_link,
    )

    store = HistoryStore(
        db_path=tmp_path / "history.db"
    )

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert destination.read_text() == "CONCURRENT"
    assert source.read_text() == "SOURCE"
    assert _temporary_files(destination.parent) == []

    operations = store.get_operations(batch_id)
    assert len(operations) == 1
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error is not None

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()
def test_copy_fsyncs_temp_before_publication_and_directory_after(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Le temporaire est durable avant link, puis le répertoire est synchronisé."""

    source = tmp_path / "source.txt"
    source.write_text("DURABLE")
    destination = tmp_path / "Copies" / "source.txt"

    events: list[str] = []

    real_fsync_file = executor_module._fsync_file
    real_fsync_directory = executor_module._fsync_directory
    real_link = executor_module.os.link

    def observing_fsync_file(path: Path) -> None:
        assert Path(path).name.startswith(executor_module._COPY_TEMP_PREFIX)
        assert not destination.exists()
        events.append("fsync-file")
        real_fsync_file(path)

    def observing_link(src, dst):
        assert events == ["fsync-file"]
        events.append("link")
        return real_link(src, dst)

    directory_sync_count = 0

    def observing_fsync_directory(directory: Path) -> None:
        nonlocal directory_sync_count
        directory_sync_count += 1
        assert Path(directory) == destination.parent
        assert destination.exists()

        if directory_sync_count == 1:
            events.append("fsync-dir-publish")
        else:
            assert _temporary_files(destination.parent) == []
            events.append("fsync-dir-cleanup")

        real_fsync_directory(directory)

    monkeypatch.setattr(
        executor_module,
        "_fsync_file",
        observing_fsync_file,
    )
    monkeypatch.setattr(
        executor_module.os,
        "link",
        observing_link,
    )
    monkeypatch.setattr(
        executor_module,
        "_fsync_directory",
        observing_fsync_directory,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 1
    assert not errors
    assert destination.read_text() == "DURABLE"
    assert events == [
        "fsync-file",
        "link",
        "fsync-dir-publish",
        "fsync-dir-cleanup",
    ]

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.COMPLETED

    store.close()


def test_temp_fsync_failure_prevents_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Un échec de durabilité avant publication reste un échec sans destination."""

    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Copies" / "source.txt"

    def failing_fsync_file(path: Path) -> None:
        raise OSError("fsync temporaire impossible")

    monkeypatch.setattr(
        executor_module,
        "_fsync_file",
        failing_fsync_file,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert success == 0
    assert len(errors) == 1
    assert "fsync temporaire impossible" in errors[0]
    assert not destination.exists()
    assert _temporary_files(destination.parent) == []

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.FAILED
    assert operations[0].error == "fsync temporaire impossible"

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.FAILED

    store.close()


def test_directory_fsync_failure_after_publication_keeps_completed_state(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    """Après publication, un fsync-dir défaillant ne crée pas un faux FAILED."""

    source = tmp_path / "source.txt"
    source.write_text("SOURCE")
    destination = tmp_path / "Copies" / "source.txt"

    calls = 0

    def failing_directory_fsync(directory: Path) -> None:
        nonlocal calls
        calls += 1
        raise OSError("fsync répertoire impossible")

    monkeypatch.setattr(
        executor_module,
        "_fsync_directory",
        failing_directory_fsync,
    )

    store = HistoryStore(db_path=tmp_path / "history.db")

    batch_id, success, errors = execute_items(
        [_copy_item(source, destination)],
        root=str(tmp_path),
        store=store,
    )

    assert calls == 2
    assert success == 1
    assert not errors
    assert destination.read_text() == "SOURCE"
    assert _temporary_files(destination.parent) == []
    assert "fsync répertoire impossible" in caplog.text

    operations = store.get_operations(batch_id)
    assert operations[0].status is OperationStatus.COMPLETED

    batch = store.get_batch(batch_id)
    assert batch is not None
    assert batch.status is BatchStatus.COMPLETED

    store.close()
