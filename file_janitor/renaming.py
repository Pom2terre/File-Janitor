"""Construction sûre de plans de renommage par modèle."""

from __future__ import annotations

import os
import string
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
    FileRecord,
    ScanResult,
)


class RenameTemplateError(ValueError):
    """Modèle de renommage invalide ou produisant un nom de fichier invalide."""


_ALLOWED_FIELDS = {"name", "ext", "parent", "date", "counter"}


@dataclass(frozen=True, slots=True)
class RenameTemplate:
    """Modèle de nom limité aux champs explicitement pris en charge."""

    source: str
    parts: tuple[tuple[str, str | None, str | None, str | None], ...]

    @classmethod
    def compile(cls, source: str) -> "RenameTemplate":
        if not source:
            raise RenameTemplateError("Le modèle de renommage ne peut pas être vide.")
        if "/" in source or "\\" in source:
            raise RenameTemplateError("Le modèle ne peut pas créer de sous-dossiers.")

        try:
            parts = tuple(string.Formatter().parse(source))
        except ValueError as exc:
            raise RenameTemplateError(f"Modèle de renommage invalide : {exc}") from exc

        for _literal, field_name, format_spec, conversion in parts:
            if field_name is None:
                continue
            if field_name not in _ALLOWED_FIELDS:
                allowed = ", ".join(f"{{{field}}}" for field in sorted(_ALLOWED_FIELDS))
                raise RenameTemplateError(
                    f"Champ {{{field_name}}} inconnu. Champs disponibles : {allowed}."
                )
            if conversion is not None:
                raise RenameTemplateError("Les conversions de champ (!r, !s…) ne sont pas prises en charge.")
            if "{" in (format_spec or "") or "}" in (format_spec or ""):
                raise RenameTemplateError("Les champs de format imbriqués ne sont pas pris en charge.")
            if format_spec and field_name != "counter":
                raise RenameTemplateError(
                    "Seul le compteur accepte un format, par exemple {counter:03d}."
                )
        return cls(source=source, parts=parts)

    def render(self, path: Path, *, date: str, counter: int) -> str:
        values = {
            "name": path.stem,
            "ext": path.suffix,
            "parent": path.parent.name,
            "date": date,
            "counter": counter,
        }
        pieces: list[str] = []
        for literal, field_name, format_spec, _conversion in self.parts:
            pieces.append(literal)
            if field_name is not None:
                value = values[field_name]
                try:
                    pieces.append(format(value, format_spec or ""))
                except ValueError as exc:
                    raise RenameTemplateError(
                        f"Format invalide pour {{{field_name}}} : {exc}"
                    ) from exc
        result = "".join(pieces)
        _validate_filename(result)
        return result


def _validate_filename(name: str) -> None:
    if name in {"", ".", ".."}:
        raise RenameTemplateError("Le modèle doit produire un nom de fichier non vide.")
    if "/" in name or "\\" in name or "\0" in name:
        raise RenameTemplateError("Le modèle ne peut pas créer de sous-dossiers.")
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise RenameTemplateError("Le nom produit contient un caractère de contrôle.")
    if os.name == "nt" and any(char in name for char in '<>:"|?*'):
        raise RenameTemplateError("Le nom produit contient un caractère interdit sous Windows.")


def build_rename_actions(
    scan: ScanResult,
    template_source: str,
    *,
    recursive: bool = False,
) -> list[ActionItem]:
    """Construit une prévisualisation déterministe, sans modifier les fichiers.

    Les cibles déjà présentes sont signalées comme collisions, y compris si
    elles appartiennent elles-mêmes à la sélection. Les cycles de noms ne sont
    donc pas devinés ni exécutés dans un ordre arbitraire.
    """

    template = RenameTemplate.compile(template_source)
    records = [
        record
        for record in scan.files
        if recursive or record.path.parent == scan.root
    ]
    records.sort(key=lambda record: str(record.path.relative_to(scan.root)))

    counters: defaultdict[Path, int] = defaultdict(int)
    candidates: list[tuple[FileRecord, Path]] = []
    for record in records:
        counter_scope = record.path.parent if recursive else scan.root
        counters[counter_scope] += 1
        target_name = template.render(
            record.path,
            date=record.mtime.strftime("%Y-%m-%d"),
            counter=counters[counter_scope],
        )
        candidates.append((record, record.path.with_name(target_name)))

    destination_counts = Counter(destination for _record, destination in candidates)
    actions: list[ActionItem] = []
    for record, destination in candidates:
        conflict_reason = None
        if destination != record.path:
            if destination_counts[destination] > 1:
                conflict_reason = "plusieurs fichiers produisent ce même nom"
            elif destination.exists():
                conflict_reason = "ce nom existe déjà dans le dossier"

        unchanged = destination == record.path
        actions.append(
            ActionItem(
                category=FileCategory.RENAME,
                path=record.path,
                size=record.size,
                reason=(
                    "Nom inchangé par le modèle"
                    if unchanged
                    else "Renommage selon le modèle"
                ),
                identity=record.identity,
                destination=destination,
                action=(
                    ActionKind.MOVE
                    if not unchanged and conflict_reason is None
                    else ActionKind.NONE
                ),
                conflict=conflict_reason is not None,
                conflict_reason=conflict_reason,
                conflict_policy=ConflictPolicy.SKIP,
            )
        )
    return actions
