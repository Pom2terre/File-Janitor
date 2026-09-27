"""Tests 4E-34A : reprise contrôlée depuis la CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_janitor.application import (
    ExecuteResult,
    HistoryQueuedResumeValidation,
)
from file_janitor import cli as cli_module
from file_janitor.cli import app


runner = CliRunner()


def _validation(
    operation_id: int,
    *,
    ready: bool,
    blocker: str = "source_identity_changed",
) -> HistoryQueuedResumeValidation:
    return HistoryQueuedResumeValidation(
        operation_id=operation_id,
        kind="move",
        original_path=Path(f"/source-{operation_id}.txt"),
        stored_path=Path(f"/sorted/source-{operation_id}.txt"),
        conflict_policy="rename",
        metadata_candidate=True,
        live_checked=True,
        live_ready=ready,
        effective_destination=Path(f"/sorted/source-{operation_id}.txt"),
        blockers=() if ready else (blocker,),
        diagnostic=None if ready else "source modifiée",
    )


def test_resume_command_is_exposed_in_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "resume" in result.stdout


def test_resume_defaults_to_read_only_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (
            _validation(1, ready=True),
            _validation(2, ready=False),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "resume_queued_operations",
        lambda _batch_id: pytest.fail("l'aperçu ne doit exécuter aucune I/O"),
    )

    result = runner.invoke(app, ["resume", "42"])

    assert result.exit_code == 0
    assert "Reprise du batch #42" in result.stdout
    assert "Prête" in result.stdout
    assert "Bloquée" in result.stdout
    assert "source_identity_changed" in result.stdout.replace("\n", "")
    assert "Aperçu uniquement" in result.stdout


def test_resume_apply_refuses_a_partially_blocked_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (
            _validation(1, ready=True),
            _validation(2, ready=False),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "resume_queued_operations",
        lambda _batch_id: pytest.fail("un batch bloqué ne doit pas être repris"),
    )

    result = runner.invoke(app, ["resume", "42", "--apply", "--yes"])

    assert result.exit_code == 1
    assert "Reprise refusée" in result.stdout


def test_resume_apply_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (_validation(1, ready=True),),
    )
    monkeypatch.setattr(
        cli_module,
        "resume_queued_operations",
        lambda _batch_id: pytest.fail("le refus doit empêcher la reprise"),
    )

    result = runner.invoke(app, ["resume", "42", "--apply"], input="n\n")

    assert result.exit_code == 1
    assert "Reprendre 1 opération(s) en mode sûr" in result.stdout
    assert "Reprise annulée : aucune modification effectuée" in result.stdout


def test_resume_apply_executes_ready_batch_in_safe_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (
            _validation(1, ready=True),
            _validation(2, ready=True),
        ),
    )

    def execute(batch_id: int) -> ExecuteResult:
        calls.append(batch_id)
        return ExecuteResult(
            batch_id=batch_id,
            success=2,
            errors=(),
            cancelled=False,
            skipped=0,
        )

    monkeypatch.setattr(cli_module, "resume_queued_operations", execute)

    result = runner.invoke(app, ["resume", "42", "--apply", "--yes"])

    assert result.exit_code == 0
    assert calls == [42]
    assert "2 opération(s) reprise(s)" in result.stdout
    assert "batch #42" in result.stdout


def test_resume_returns_failure_when_live_execution_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (_validation(1, ready=True),),
    )
    monkeypatch.setattr(
        cli_module,
        "resume_queued_operations",
        lambda batch_id: ExecuteResult(
            batch_id=batch_id,
            success=0,
            errors=("reprise bloquée après revalidation",),
            cancelled=False,
            skipped=1,
        ),
    )

    result = runner.invoke(app, ["resume", "42", "--apply", "--yes"])

    assert result.exit_code == 1
    assert "1 opération(s) non démarrée(s)" in result.stdout
    assert "reprise bloquée après revalidation" in result.stdout


def test_resume_rejects_batch_without_queued_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "validate_queued_resume_candidates",
        lambda batch_id: (),
    )

    result = runner.invoke(app, ["resume", "42"])

    assert result.exit_code == 1
    assert "Aucune opération non démarrée" in result.stdout
