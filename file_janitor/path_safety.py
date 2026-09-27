"""Validation défensive des chemins utilisés par File Janitor.

Ce module centralise les contrôles appliqués juste avant une opération disque.
Le plan et la preview peuvent dater de plusieurs secondes : les chemins et
l'identité des fichiers doivent donc être revérifiés au moment de l'exécution.

Règles de sécurité :
- la source doit exister, être un fichier normal et ne pas être un lien
  symbolique ;
- si une identité a été capturée au scan, device/inode/size/mtime_ns doivent
  toujours correspondre juste avant l'I/O ;
- pour COPY, la source réellement ouverte est contrôlée avec ``os.fstat`` ;
- une destination ne peut pas désigner le même fichier que la source ;
- une destination existante qui est un dossier est refusée ;
- une destination existante qui est un lien symbolique est refusée ;
- aucun composant parent existant de la destination ne doit être un lien
  symbolique.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path

from file_janitor.models import FileIdentity


class PathSafetyError(OSError):
    """Erreur de validation d'un chemin avant opération disque."""


def _absolute_normalized(path: Path) -> Path:
    """Retourne un chemin absolu normalisé sans exiger son existence."""

    return path.expanduser().resolve(strict=False)


def identity_from_stat(stat_result: os.stat_result) -> FileIdentity:
    """Construit une FileIdentity depuis un résultat stat/fstat."""

    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def current_file_identity(source: Path) -> FileIdentity:
    """Lit l'identité filesystem actuelle d'un fichier."""

    source = source.expanduser()

    try:
        stat_result = source.stat()
    except OSError as exc:
        raise PathSafetyError(
            f"impossible de relire l'identité de la source : {source}: {exc}"
        ) from exc

    return identity_from_stat(stat_result)


def _validate_identity_values(
    current: FileIdentity,
    expected: FileIdentity,
) -> None:
    """Compare une identité actuelle à celle capturée pendant le scan."""

    if current.device != expected.device or current.inode != expected.inode:
        raise PathSafetyError(
            "identité de la source modifiée depuis le scan "
            "(device/inode différents) ; opération refusée"
        )

    if current.size != expected.size or current.mtime_ns != expected.mtime_ns:
        raise PathSafetyError(
            "source modifiée depuis le scan "
            "(taille ou date de modification différente) ; opération refusée"
        )


def validate_source_file(source: Path) -> Path:
    """Valide une source avant MOVE/COPY/TRASH.

    Retourne le chemin normalisé lorsque la source est sûre.
    """

    source = source.expanduser()

    if source.is_symlink():
        raise PathSafetyError(
            f"source lien symbolique refusée : {source}"
        )

    if not source.exists():
        raise PathSafetyError(
            f"source introuvable : {source}"
        )

    if not source.is_file():
        raise PathSafetyError(
            f"source non régulière (fichier attendu) : {source}"
        )

    return _absolute_normalized(source)


def validate_source_empty_directory(
    source: Path,
    expected: FileIdentity | None = None,
) -> Path:
    """Valide un dossier réel encore vide, sans suivre son dernier symlink.

    Pour les dossiers, la taille et la date changent légitimement quand les
    sous-dossiers enfants du même plan sont retirés. L'identité vérifie donc
    device/inode ; ``rmdir`` reste l'arbitre atomique final de la vacuité.
    """

    source = Path(source).expanduser()
    for parent in _iter_existing_parents(source):
        if parent.is_symlink():
            raise PathSafetyError(
                f"parent du dossier source lien symbolique refusé : {parent}"
            )
    try:
        stat_result = source.lstat()
    except OSError as exc:
        raise PathSafetyError(
            f"impossible de relire le dossier source : {source}: {exc}"
        ) from exc

    if not stat_module.S_ISDIR(stat_result.st_mode):
        raise PathSafetyError(f"un vrai dossier est requis : {source}")

    if expected is not None and (
        stat_result.st_dev != expected.device
        or stat_result.st_ino != expected.inode
    ):
        raise PathSafetyError(
            "le dossier source a été remplacé depuis l’analyse ; suppression refusée"
        )

    try:
        with os.scandir(source) as entries:
            if next(entries, None) is not None:
                raise PathSafetyError(
                    f"le dossier n’est plus vide : {source}"
                )
    except PathSafetyError:
        raise
    except OSError as exc:
        raise PathSafetyError(
            f"impossible de vérifier le contenu du dossier : {source}: {exc}"
        ) from exc

    return _absolute_normalized(source)


def validate_source_identity(
    source: Path,
    expected: FileIdentity | None,
) -> FileIdentity | None:
    """Vérifie que la source est toujours celle observée pendant le scan.

    ``expected=None`` conserve la compatibilité avec les ActionItem construits
    manuellement par les anciens tests ou par du code externe.
    """

    if expected is None:
        return None

    current = current_file_identity(source)
    _validate_identity_values(current, expected)
    return current


def open_validated_source(
    source: Path,
    expected: FileIdentity | None,
) -> tuple[int, FileIdentity]:
    """Ouvre la source sans suivre de symlink et valide le fichier réellement ouvert.

    Cette fonction est destinée à COPY. La validation repose sur ``os.fstat``
    appliqué au descripteur ouvert, et non sur une nouvelle résolution du nom.

    Le descripteur retourné appartient à l'appelant, qui doit toujours le fermer.
    """

    source = source.expanduser()

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise PathSafetyError(
            f"impossible d'ouvrir la source de façon sûre : {source}: {exc}"
        ) from exc

    try:
        stat_result = os.fstat(fd)

        if not stat_module.S_ISREG(stat_result.st_mode):
            raise PathSafetyError(
                f"source ouverte non régulière (fichier attendu) : {source}"
            )

        current = identity_from_stat(stat_result)

        if expected is not None:
            _validate_identity_values(current, expected)

        return fd, current

    except Exception:
        os.close(fd)
        raise


def _iter_existing_parents(path: Path):
    """Itère sur les parents existants d'un chemin, du plus proche au plus haut."""

    current = path.parent

    while True:
        if current.exists() or current.is_symlink():
            yield current

        if current == current.parent:
            break

        current = current.parent


def validate_destination_path(
    source: Path,
    destination: Path,
) -> Path:
    """Valide une destination MOVE/COPY avant résolution de collision."""

    source = source.expanduser()
    destination = destination.expanduser()

    source_resolved = _absolute_normalized(source)
    destination_resolved = _absolute_normalized(destination)

    if source_resolved == destination_resolved:
        raise PathSafetyError(
            "source et destination désignent le même chemin"
        )

    if source.exists() and destination.exists():
        try:
            if source.samefile(destination):
                raise PathSafetyError(
                    "source et destination désignent le même fichier"
                )
        except OSError as exc:
            raise PathSafetyError(
                "impossible de vérifier si source et destination "
                f"désignent le même fichier : {source} -> {destination}: {exc}"
            ) from exc

    if destination.is_symlink():
        raise PathSafetyError(
            f"destination lien symbolique refusée : {destination}"
        )

    if destination.exists() and destination.is_dir():
        raise PathSafetyError(
            f"destination est un dossier alors qu'un fichier est attendu : "
            f"{destination}"
        )

    for parent in _iter_existing_parents(destination):
        if parent.is_symlink():
            raise PathSafetyError(
                f"parent de destination lien symbolique refusé : {parent}"
            )

    return destination_resolved


def validate_undo_restore_path(original_path: Path) -> Path:
    """Valide le chemin original avant restauration MOVE/TRASH."""

    original_path = original_path.expanduser()

    if original_path.is_symlink():
        raise PathSafetyError(
            f"chemin original lien symbolique refusé : {original_path}"
        )

    for parent in _iter_existing_parents(original_path):
        if parent.is_symlink():
            raise PathSafetyError(
                f"parent du chemin original lien symbolique refusé : {parent}"
            )

    return _absolute_normalized(original_path)
