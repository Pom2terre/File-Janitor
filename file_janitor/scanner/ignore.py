"""Gestion des exclusions de scan : patterns CLI + fichier `.janitorignore`.

Utilise la syntaxe gitignore (via la librairie `pathspec`) pour rester
familière aux utilisateurs :

    *.log
    build/
    !keep.log      # négation : ré-inclut un fichier normalement exclu

Trois sources de patterns sont combinées, dans cet ordre (le dernier gagne
en cas de négation, comme pour un .gitignore) :
  1. DEFAULT_EXCLUDES (toujours actifs, sauf --no-default-excludes)
  2. le fichier `<root>/.janitorignore` s'il existe
  3. les patterns passés en ligne de commande (--exclude / -e)
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from file_janitor.storage.history import DEFAULT_STATE_DIR

IGNORE_FILENAME = ".janitorignore"

# Le propre état du janitor (corbeille + historique) ne doit jamais être
# traité comme faisant partie du dossier scanné, au cas où celui-ci
# contiendrait le dossier home (ex: scan de ~ directement).
DEFAULT_EXCLUDES: list[str] = [
    f"{DEFAULT_STATE_DIR.name}/",
    ".dtrash/",
    ".thumbnails/",
    # Répertoires techniques/régénérables courants. Les analyser produit
    # surtout des doublons de dépendances, caches et artefacts de build.
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    # Métadonnées d'IDE : régénérables et extrêmement répétitives dans
    # les sauvegardes de projets (workspace.xml, modules.xml, etc.).
    ".idea/",
    ".vscode/",
    ".vs/",
    "build/",
    "dist/",
    "*.egg-info/",
]


def _read_ignore_file(root: Path) -> list[str]:
    ignore_file = root / IGNORE_FILENAME
    if not ignore_file.is_file():
        return []
    try:
        return ignore_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def build_ignore_spec(
    root: Path,
    extra_patterns: list[str] | None = None,
    *,
    use_defaults: bool = True,
    use_ignore_file: bool = True,
) -> pathspec.PathSpec:
    """Construit le PathSpec combinant defaults + .janitorignore + CLI."""
    lines: list[str] = []
    if use_defaults:
        lines.extend(DEFAULT_EXCLUDES)
    if use_ignore_file:
        lines.extend(_read_ignore_file(root))
    if extra_patterns:
        lines.extend(extra_patterns)
    return pathspec.PathSpec.from_lines("gitignore", lines)


def is_excluded(spec: pathspec.PathSpec, relative_path: Path, *, is_dir: bool = False) -> bool:
    """Teste un chemin relatif (posix, sans le root) contre le PathSpec.

    Pour un dossier, on ajoute un `/` final : c'est la convention gitignore
    pour matcher spécifiquement des règles comme `build/`.
    """
    posix = relative_path.as_posix()
    if is_dir:
        posix += "/"
    return spec.match_file(posix)
