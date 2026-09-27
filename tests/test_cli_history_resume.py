"""Tests 4E-34B : découvrabilité CLI de la reprise QUEUED."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from file_janitor import cli as cli_module
from file_janitor.application import HistoryBatchSummary
from file_janitor.cli import app


runner = CliRunner()


def _batch(
    batch_id: int = 42,
    *,
    created_at: str = "2026-09-22 12:00",
    root: str | None = "/remote/music",
    undone: bool | None = False,
) -> HistoryBatchSummary:
    return HistoryBatchSummary(
        id=batch_id,
        created_at=created_at,
        root=root,
        status="failed",
        undone=undone,
    )


def _operation(
    *,
    status: str = "queued",
    candidate: bool = False,
    blockers: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        resume_candidate=candidate,
        resume_blockers=blockers,
    )


def _run_history(monkeypatch: pytest.MonkeyPatch, operations: tuple[object, ...]):
    monkeypatch.setattr(cli_module, "get_history_summary", lambda limit=20: (_batch(),))
    monkeypatch.setattr(
        cli_module,
        "get_history_operation_summary",
        lambda batch_id: operations,
    )
    return runner.invoke(app, ["history"], terminal_width=240)


def test_history_keeps_empty_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "get_history_summary", lambda limit=20: ())

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert result.stdout == "Aucun historique.\n"


def test_history_exposes_fully_qualified_queued_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_history(
        monkeypatch,
        (
            _operation(candidate=True),
            _operation(candidate=True),
        ),
    )

    assert result.exit_code == 0
    assert "2 candidates" in result.stdout
    assert "janitor resume 42" in result.stdout
    assert "Qualification métadonnée uniquement" in result.stdout
    assert "revalidation live" in result.stdout


def test_history_exposes_partially_blocked_queued_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_history(
        monkeypatch,
        (
            _operation(candidate=True),
            _operation(blockers=("source_identity_changed",)),
        ),
    )

    assert result.exit_code == 0
    assert "1 candidate" in result.stdout
    assert "1 bloquée" in result.stdout
    assert "janitor resume 42" not in result.stdout


def test_history_fails_closed_for_unqualified_queued_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_history(monkeypatch, (_operation(),))

    assert result.exit_code == 0
    assert "1 bloquée" in result.stdout
    assert "janitor resume 42" not in result.stdout


def test_history_omits_resume_hint_without_queued_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_history(
        monkeypatch,
        (_operation(status="completed", candidate=False),),
    )

    assert result.exit_code == 0
    assert "Qualification métadonnée uniquement" not in result.stdout
    assert "janitor resume 42" not in result.stdout


def test_history_never_performs_live_resume_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda _batch_id: pytest.fail("history doit rester métadonnée uniquement"),
    )

    result = _run_history(monkeypatch, (_operation(candidate=True),))

    assert result.exit_code == 0
    assert "janitor resume 42" in result.stdout


def test_history_renders_invalid_batch_metadata_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "get_history_summary",
        lambda limit=20: (
            _batch(
                created_at="Horodatage invalide",
                root=None,
                undone=None,
            ),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "get_history_operation_summary",
        lambda batch_id: (),
    )

    result = runner.invoke(app, ["history"], terminal_width=240)

    assert result.exit_code == 0
    assert "Horodatage invalide" in result.stdout
    assert "Dossier invalide" in result.stdout
    assert "invalide" in result.stdout


def test_history_forwards_limit_and_loads_each_batch_on_demand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_limits: list[int] = []
    loaded_batches: list[int] = []

    def summaries(limit: int = 20) -> tuple[HistoryBatchSummary, ...]:
        received_limits.append(limit)
        return (_batch(41), _batch(42))

    def operations(batch_id: int) -> tuple[object, ...]:
        loaded_batches.append(batch_id)
        return ()

    monkeypatch.setattr(cli_module, "get_history_summary", summaries)
    monkeypatch.setattr(cli_module, "get_history_operation_summary", operations)

    result = runner.invoke(
        app,
        ["history", "--limit", "2"],
        terminal_width=240,
    )

    assert result.exit_code == 0
    assert received_limits == [2]
    assert loaded_batches == [41, 42]
