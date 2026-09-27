"""Regroupement par motif commun dans le nom de fichier (`janitor sort --template`).

Reproduit les « Templates » de Qiplex Easy File Organizer : des fichiers
nommés selon un schéma commun (ex: hello-world-1.jpg, hello-world-2.jpg,
hello-world-3.jpg) sont regroupés dans un même dossier généré à partir de
la partie commune de leur nom (ex: hello-world/), sans qu'il soit
nécessaire de définir manuellement chaque groupe.

Le motif par défaut retire un compteur numérique final (avec séparateur
optionnel -, _ ou espace) : "hello-world-1" -> "hello-world". Un motif regex
personnalisé (avec un groupe de capture) peut être fourni via
--template-pattern pour d'autres schémas de nommage (ex: un préfixe fixe de
longueur donnée, une date, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Nom de base + séparateur optionnel (-, _, espace) + compteur numérique final.
# "hello-world-1" -> "hello-world" ; "shiny_stars_042" -> "shiny_stars".
DEFAULT_TEMPLATE_PATTERN = r"^(.+?)[-_ ]*\d+$"


class TemplateError(ValueError):
    """Motif de template invalide (regex non compilable ou sans groupe de capture)."""


@dataclass(frozen=True, slots=True)
class Template:
    """Un motif regex compilé, avec un groupe de capture qui donne le nom du
    dossier de destination pour chaque fichier correspondant."""

    pattern: re.Pattern[str]

    @classmethod
    def compile(cls, pattern: str) -> "Template":
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise TemplateError(f"Motif de template invalide {pattern!r} : {exc}") from exc
        if compiled.groups < 1:
            raise TemplateError(
                f"Motif de template {pattern!r} : au moins un groupe de capture est requis, "
                "par exemple '(.+?)-\\d+' pour retirer un compteur final."
            )
        return cls(pattern=compiled)

    def extract(self, stem: str) -> str | None:
        """Retourne le nom de dossier extrait du nom de fichier (sans
        extension), ou None si le motif ne correspond pas ou si le groupe
        capturé est vide après nettoyage des séparateurs."""
        match = self.pattern.match(stem)
        if not match:
            return None
        group = match.group(1).strip(" -_")
        return group or None
