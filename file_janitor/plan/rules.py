"""Règles personnalisées de classement (`janitor sort --rules rules.json`).

Inspiré des Custom Rules de Qiplex Easy File Organizer : une règle combine
plusieurs conditions (toutes doivent être vraies — ET logique) et associe
les fichiers correspondants à un dossier de destination. Les règles sont
évaluées dans l'ordre du fichier ; la première règle qui correspond à un
fichier l'emporte. Les fichiers qui ne correspondent à aucune règle
retombent sur la stratégie de classement standard (--group-by).

Format du fichier de règles (JSON) :

    [
      {
        "name": "Films",
        "destination": "Films",
        "extension": ["mp4", "mkv", "avi"],
        "name_contains": "movie",
        "size_gt": "100MB"
      },
      {
        "name": "Photos de vacances",
        "destination": "Photos/Vacances",
        "extension": ["jpg", "jpeg"],
        "name_contains": "vacances",
        "date_after": "2024-01-01"
      }
    ]

Conditions disponibles (toutes optionnelles, mais au moins une requise) :
- extension       : liste d'extensions (sans le point, insensible à la casse)
- name_contains   : sous-chaîne recherchée dans le nom de fichier (insensible à la casse)
- size_gt / size_lt : taille en octets, ou chaîne "100MB"/"500KB"/"2GB" (base 1024)
- date_after / date_before : date ISO "AAAA-MM-JJ", comparée à la date de modification
- content_family  : famille de contenu réel (voir file_janitor.scanner.filetype :
  image, video, audio, document, archive, executable, database, text)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from file_janitor.models import FileRecord

_ALLOWED_KEYS = {
    "name",
    "destination",
    "extension",
    "name_contains",
    "size_gt",
    "size_lt",
    "date_after",
    "date_before",
    "content_family",
}
_CONDITION_KEYS = _ALLOWED_KEYS - {"name", "destination"}

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?\s*$", re.IGNORECASE)


class RuleError(ValueError):
    """Erreur de chargement/validation d'un fichier de règles (message destiné à l'utilisateur)."""


@dataclass(frozen=True, slots=True)
class Rule:
    """Une règle : un ensemble de conditions (ET logique) + une destination."""

    name: str
    destination: str
    extension: tuple[str, ...] | None = None
    name_contains: str | None = None
    size_gt: int | None = None
    size_lt: int | None = None
    date_after: datetime | None = None
    date_before: datetime | None = None
    content_family: str | None = None

    def matches(self, record: FileRecord) -> bool:
        if self.extension is not None and record.extension.lstrip(".") not in self.extension:
            return False
        if self.name_contains is not None and self.name_contains.lower() not in record.path.name.lower():
            return False
        if self.size_gt is not None and not (record.size > self.size_gt):
            return False
        if self.size_lt is not None and not (record.size < self.size_lt):
            return False
        if self.date_after is not None and not (record.mtime > self.date_after):
            return False
        if self.date_before is not None and not (record.mtime < self.date_before):
            return False
        if self.content_family is not None and record.content_family != self.content_family:
            return False
        return True


def _parse_size(value: int | str, *, field: str, rule_name: str) -> int:
    if isinstance(value, int):
        return value
    match = _SIZE_PATTERN.match(str(value))
    if not match:
        raise RuleError(
            f"Règle « {rule_name} » : valeur invalide pour « {field} » : {value!r} "
            "(attendu un nombre d'octets ou une chaîne comme '100MB', '500KB', '2GB')."
        )
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    return int(number * _SIZE_UNITS[unit])


def _parse_date(value: str, *, field: str, rule_name: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise RuleError(
            f"Règle « {rule_name} » : date invalide pour « {field} » : {value!r} (format attendu AAAA-MM-JJ)."
        ) from exc


def _parse_rule(raw: dict, index: int) -> Rule:
    unknown_keys = set(raw) - _ALLOWED_KEYS
    if unknown_keys:
        raise RuleError(
            f"Règle #{index + 1} : clé(s) inconnue(s) {sorted(unknown_keys)} — "
            f"clés valides : {sorted(_ALLOWED_KEYS)}."
        )

    name = raw.get("name") or f"Règle #{index + 1}"

    if "destination" not in raw or not str(raw["destination"]).strip():
        raise RuleError(f"Règle « {name} » : le champ « destination » est requis.")
    destination = str(raw["destination"]).strip()
    if destination.startswith(("/", "~")) or ".." in Path(destination).parts:
        raise RuleError(
            f"Règle « {name} » : destination invalide {destination!r} "
            "(doit être un chemin relatif, sans '..' ni chemin absolu)."
        )

    if not (_CONDITION_KEYS & set(raw)):
        raise RuleError(
            f"Règle « {name} » n'a aucune condition — elle correspondrait à tous les fichiers. "
            f"Ajoutez au moins une des clés : {sorted(_CONDITION_KEYS)}."
        )

    extension = None
    if "extension" in raw:
        exts = raw["extension"]
        if not isinstance(exts, list) or not exts:
            raise RuleError(f"Règle « {name} » : « extension » doit être une liste non vide.")
        extension = tuple(str(e).lstrip(".").lower() for e in exts)

    name_contains = str(raw["name_contains"]) if "name_contains" in raw else None

    size_gt = _parse_size(raw["size_gt"], field="size_gt", rule_name=name) if "size_gt" in raw else None
    size_lt = _parse_size(raw["size_lt"], field="size_lt", rule_name=name) if "size_lt" in raw else None

    date_after = _parse_date(raw["date_after"], field="date_after", rule_name=name) if "date_after" in raw else None
    date_before = _parse_date(raw["date_before"], field="date_before", rule_name=name) if "date_before" in raw else None

    content_family = str(raw["content_family"]) if "content_family" in raw else None

    return Rule(
        name=name,
        destination=destination,
        extension=extension,
        name_contains=name_contains,
        size_gt=size_gt,
        size_lt=size_lt,
        date_after=date_after,
        date_before=date_before,
        content_family=content_family,
    )


def load_rules(path: Path) -> list[Rule]:
    """Charge et valide un fichier de règles JSON. Lève RuleError si invalide."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleError(f"Impossible de lire le fichier de règles {path} : {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuleError(f"Fichier de règles {path} : JSON invalide ({exc}).") from exc

    if not isinstance(data, list):
        raise RuleError(f"Fichier de règles {path} : la racine doit être une liste de règles.")

    return [_parse_rule(raw, index) for index, raw in enumerate(data)]


def first_matching_rule(record: FileRecord, rules: list[Rule]) -> Rule | None:
    for rule in rules:
        if rule.matches(record):
            return rule
    return None
