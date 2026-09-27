"""Tests du module de planification (auto-organizing via cron).

Le crontab réel du système n'est jamais touché : `subprocess.run` est
remplacé par un faux crontab en mémoire, aussi bien pour les tests unitaires
du module que pour les tests CLI de bout en bout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_janitor import scheduler
from file_janitor.cli import app

runner = CliRunner()


class _FakeResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_crontab(monkeypatch):
    """Simule `crontab -l` / `crontab -` en mémoire, sans toucher au système."""
    state = {"lines": []}

    def fake_run(cmd, **kwargs):
        if cmd == ["crontab", "-l"]:
            if not state["lines"]:
                return _FakeResult(returncode=1, stderr="no crontab for testuser\n")
            return _FakeResult(returncode=0, stdout="\n".join(state["lines"]) + "\n")
        if cmd == ["crontab", "-"]:
            text = kwargs.get("input", "")
            state["lines"] = [line for line in text.splitlines() if line.strip()]
            return _FakeResult(returncode=0)
        raise AssertionError(f"Commande crontab inattendue : {cmd}")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/usr/bin/crontab" if name == "crontab" else None)
    return state


@pytest.mark.parametrize(
    "every,unit,expected",
    [
        (1, "minutes", "*/1 * * * *"),
        (30, "minutes", "*/30 * * * *"),
        (2, "hours", "0 */2 * * *"),
        (1, "days", "0 0 */1 * *"),
    ],
)
def test_build_cron_expression(every: int, unit: str, expected: str) -> None:
    assert scheduler.build_cron_expression(every, unit) == expected


@pytest.mark.parametrize(
    "every,unit",
    [(60, "minutes"), (24, "hours"), (28, "days"), (0, "hours"), (-1, "hours")],
)
def test_build_cron_expression_rejects_out_of_range(every: int, unit: str) -> None:
    with pytest.raises(scheduler.ScheduleError):
        scheduler.build_cron_expression(every, unit)


def test_build_cron_expression_rejects_unknown_unit() -> None:
    with pytest.raises(scheduler.ScheduleError, match="Unité inconnue"):
        scheduler.build_cron_expression(1, "fortnights")


def test_build_command_uses_current_interpreter(tmp_path: Path) -> None:
    command = scheduler.build_command(tmp_path, "sort", "--group-by extension")

    assert "file_janitor.cli" in command
    assert "sort" in command
    assert "--apply --yes" in command
    assert "--group-by extension" in command
    assert str(tmp_path) in command


def test_install_list_remove_job_roundtrip(fake_crontab, tmp_path: Path) -> None:
    job = scheduler.install_job(tmp_path, every=2, unit="hours", action="sort", extra_args=None)

    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job.job_id
    assert jobs[0].cron_expression == "0 */2 * * *"

    removed = scheduler.remove_job(job.job_id)
    assert removed is True
    assert scheduler.list_jobs() == []


def test_remove_unknown_job_returns_false(fake_crontab) -> None:
    assert scheduler.remove_job("doesnotexist") is False


def test_install_preserves_unrelated_crontab_lines(fake_crontab, tmp_path: Path) -> None:
    fake_crontab["lines"] = ["0 3 * * * /usr/bin/echo unrelated"]

    job = scheduler.install_job(tmp_path, every=1, unit="days", action="clean", extra_args=None)

    lines = fake_crontab["lines"]
    assert "0 3 * * * /usr/bin/echo unrelated" in lines
    assert any(job.job_id in line for line in lines)

    scheduler.remove_job(job.job_id)
    assert fake_crontab["lines"] == ["0 3 * * * /usr/bin/echo unrelated"]


def test_cron_unavailable_raises_on_install(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler.shutil, "which", lambda name: None)
    with pytest.raises(scheduler.ScheduleError, match="crontab"):
        scheduler.install_job(tmp_path, every=1, unit="hours", action="sort", extra_args=None)


def test_schtasks_hint_is_indicative_only(tmp_path: Path) -> None:
    hint = scheduler.schtasks_hint(tmp_path, every=2, unit="hours", action="sort", extra_args=None)
    assert "schtasks" in hint
    assert str(tmp_path) in hint


def test_cli_schedule_add_and_list(fake_crontab, tmp_path: Path) -> None:
    result = runner.invoke(app, ["schedule", "add", str(tmp_path), "--every", "1", "--unit", "hours", "--yes"])
    assert result.exit_code == 0
    assert "Tâche planifiée" in result.stdout

    result = runner.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0
    # Le tableau Rich peut tronquer les chemins longs : on vérifie la
    # présence de la planification plutôt que le chemin complet.
    assert "0 */1 * * *" in result.stdout


def test_cli_schedule_remove_unknown_id_exits_nonzero(fake_crontab) -> None:
    result = runner.invoke(app, ["schedule", "remove", "unknown"])
    assert result.exit_code == 1


def test_cli_schedule_add_invalid_action(fake_crontab, tmp_path: Path) -> None:
    result = runner.invoke(app, ["schedule", "add", str(tmp_path), "--every", "1", "--action", "bogus"])
    assert result.exit_code == 1
