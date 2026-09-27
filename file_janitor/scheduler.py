"""Auto-organizing : planifie l'exécution périodique de `janitor sort`/`clean`
via le planificateur du système d'exploitation (cron sur Linux/Mac).

File Janitor est un outil en ligne de commande, pas un service qui tourne en
arrière-plan : plutôt que de réinventer un ordonnanceur (fragile, à
maintenir, à démarrer au boot...), on délègue au planificateur déjà présent
sur le système — cron sur Linux/Mac. C'est plus robuste, standard, et
n'ajoute aucune dépendance.

Chaque tâche installée est identifiée par un tag unique dans le commentaire
de la ligne crontab (`# file-janitor:<id> ...`), ce qui permet de la
retrouver et de la supprimer précisément, sans jamais toucher au reste du
crontab de l'utilisateur.

Sur Windows (pas de `crontab`), la commande équivalente `schtasks` est
affichée à titre indicatif : elle n'est pas exécutée automatiquement (non
testable depuis cet environnement Linux), mais reste copiable-collable.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

TAG_PREFIX = "file-janitor"


class ScheduleError(RuntimeError):
    """Erreur lors de la manipulation du planificateur (message destiné à l'utilisateur)."""


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    job_id: str
    description: str
    cron_expression: str
    command: str

    @property
    def full_line(self) -> str:
        return f"{self.cron_expression} {self.command} # {TAG_PREFIX}:{self.job_id} desc={self.description}"


def cron_available() -> bool:
    return shutil.which("crontab") is not None


def build_cron_expression(every: int, unit: str) -> str:
    """Traduit un intervalle (every N unit) en expression cron à 5 champs.

    Limites documentées : cron n'exprime nativement que des pas réguliers
    (`*/N`) qui repartent de zéro à chaque nouvelle heure/jour/mois — un
    `--every 5 --unit days` s'exécutera donc aux jours 1, 6, 11, 16, 21, 26
    du mois (repart à 1 le mois suivant), pas exactement tous les 5 jours en
    continu.
    """
    if every <= 0:
        raise ScheduleError("--every doit être un entier positif.")

    if unit == "minutes":
        if every > 59:
            raise ScheduleError("--every ne peut pas dépasser 59 pour l'unité 'minutes'.")
        return f"*/{every} * * * *"
    if unit == "hours":
        if every > 23:
            raise ScheduleError("--every ne peut pas dépasser 23 pour l'unité 'hours'.")
        return f"0 */{every} * * *"
    if unit == "days":
        if every > 27:
            raise ScheduleError("--every ne peut pas dépasser 27 pour l'unité 'days' (limite du mois).")
        return f"0 0 */{every} * *"
    raise ScheduleError(f"Unité inconnue {unit!r} (attendu : minutes, hours ou days).")


def build_command(folder: Path, action: str, extra_args: str | None) -> str:
    """Construit la commande complète à exécuter par cron.

    Utilise `sys.executable -m file_janitor.cli` plutôt que le nom de
    commande `janitor` seul : cron s'exécute avec un PATH minimal qui ne
    contient généralement pas l'environnement virtuel actif, alors que le
    chemin de l'interpréteur Python courant, lui, est toujours valide.
    """
    parts = [sys.executable, "-m", "file_janitor.cli", action, shlex.quote(str(folder)), "--apply", "--yes"]
    if extra_args:
        parts.append(extra_args)
    return " ".join(parts)


def _read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        # "no crontab for <user>" : pas d'erreur, juste un crontab vide.
        return []
    return result.stdout.splitlines()


def _write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    result = subprocess.run(["crontab", "-"], input=content, text=True, capture_output=True)
    if result.returncode != 0:
        raise ScheduleError(f"Échec de l'écriture du crontab : {result.stderr.strip()}")


def install_job(folder: Path, *, every: int, unit: str, action: str, extra_args: str | None) -> ScheduledJob:
    if not cron_available():
        raise ScheduleError(
            "'crontab' est introuvable sur ce système (normal sous Windows : "
            "utilisez le Planificateur de tâches à la place, voir la commande équivalente ci-dessous)."
        )
    cron_expression = build_cron_expression(every, unit)
    command = build_command(folder, action, extra_args)
    job = ScheduledJob(
        job_id=uuid.uuid4().hex[:8],
        description=f"{action}:{folder}",
        cron_expression=cron_expression,
        command=command,
    )
    lines = _read_crontab()
    lines.append(job.full_line)
    _write_crontab(lines)
    return job


def list_jobs() -> list[ScheduledJob]:
    jobs = []
    for line in _read_crontab():
        if f"# {TAG_PREFIX}:" not in line:
            continue
        cron_part, _, rest = line.partition(f" # {TAG_PREFIX}:")
        job_id, _, description_part = rest.partition(" desc=")
        cron_fields = cron_part.split(None, 5)
        cron_expression = " ".join(cron_fields[:5])
        command = cron_fields[5] if len(cron_fields) > 5 else ""
        jobs.append(
            ScheduledJob(
                job_id=job_id.strip(),
                description=description_part.strip(),
                cron_expression=cron_expression,
                command=command,
            )
        )
    return jobs


def remove_job(job_id: str) -> bool:
    """Supprime la tâche portant cet identifiant. Retourne False si introuvable."""
    lines = _read_crontab()
    marker = f"# {TAG_PREFIX}:{job_id} "
    remaining = [line for line in lines if marker not in line]
    if len(remaining) == len(lines):
        return False
    _write_crontab(remaining)
    return True


def schtasks_hint(folder: Path, *, every: int, unit: str, action: str, extra_args: str | None) -> str:
    """Commande schtasks équivalente (Windows), affichée à titre indicatif
    uniquement — jamais exécutée automatiquement depuis cet environnement."""
    command = build_command(folder, action, extra_args)
    unit_map = {"minutes": "MINUTE", "hours": "HOURLY", "days": "DAILY"}
    schedule_type = unit_map.get(unit, "DAILY")
    modifier = f" /MO {every}" if every > 1 else ""
    return (
        f'schtasks /Create /SC {schedule_type}{modifier} /TN "FileJanitor_{action}" '
        f'/TR "{command}"'
    )
