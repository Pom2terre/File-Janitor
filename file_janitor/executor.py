"""Exécution transactionnelle d'un Plan validé par l'utilisateur, et undo.

Règles de sécurité :
- Aucune suppression définitive : les fichiers avec ActionKind.TRASH sont
  déplacés vers une corbeille locale (TRASH_DIR).
- ActionItem.action est l'unique source de vérité de l'exécuteur.
- Toute opération modifiant le disque est enregistrée dans HistoryStore avec
  le statut PLANNED avant l'I/O.
- Après l'I/O, l'opération passe à COMPLETED ou FAILED.
- Le batch passe à COMPLETED, PARTIAL ou FAILED selon le résultat global.
- Les collisions MOVE/COPY respectent ConflictPolicy :
    * SKIP    : refus sans écrasement ;
    * RENAME  : choix d'un nom libre ;
    * REPLACE : refusé en v0.2.
- Les chemins sont revérifiés juste avant l'I/O.
- Lorsqu'une FileIdentity a été capturée au scan, elle est comparée à
  l'identité réelle au début de l'exécution puis de nouveau au dernier moment
  avant MOVE/TRASH.
- COPY ouvre la source avec un descripteur sans suivi de symlink puis valide
  avec fstat le fichier réellement ouvert avant de le lire.
- COPY écrit d'abord dans un fichier temporaire privé du dossier destination ;
  le nom final n'est publié qu'après une copie complète.
- La publication COPY est atomique et no-clobber : une destination créée
  concurremment n'est jamais écrasée.
- COPY synchronise le temporaire avant publication puis le répertoire après
  chaque mutation de nom afin de renforcer la durabilité après crash.
- MOVE utilise sous Linux renameat2(RENAME_NOREPLACE) : déplacement atomique
  same-filesystem sans écrasement concurrent.
- Sur EXDEV ou lorsqu'un filesystem ne supporte pas
  renameat2(RENAME_NOREPLACE), MOVE bascule vers un fallback transactionnel :
  copie privée, publication no-clobber, suppression contrôlée de la source et
  rollback de la destination si la suppression source échoue.
- TRASH réutilise le même moteur transactionnel que MOVE : publication
  atomique/no-clobber sur le même filesystem et fallback transactionnel sur
  EXDEV, tout en restant enregistré comme une opération ``delete``.
- L'undo n'écrase jamais un chemin original recréé entre-temps.
- La restauration MOVE/TRASH de l'undo utilise un rename no-clobber sur le
  même filesystem et le même fallback transactionnel lorsque renameat2 n'est
  pas disponible ou utilisable.
- MOVE, TRASH et COPY persistent l'identité du fichier effectivement publié ;
  leur undo refuse d'agir si cet objet a été remplacé ou modifié depuis
  l'exécution.
- L'undo possède un cycle explicite : UNDOING puis UNDONE,
  UNDO_PARTIAL ou UNDO_FAILED.
- Une opération UNDO_FAILED reste réessayable ; les opérations déjà UNDONE
  ne sont jamais rejouées lors d'une nouvelle tentative.
- Chaque opération annulée passe à UNDONE ou UNDO_FAILED.
- ActionKind.NONE ne produit aucune opération dans l'historique.
- Une erreur sur un fichier n'interrompt pas les opérations indépendantes
  suivantes ; un TRASH explicitement dépendant d'un keeper échoué est en
  revanche bloqué sans I/O et journalisé comme FAILED.

Limite volontaire :
MOVE/TRASH et l'unlink final de l'undo COPY restent des opérations portant
sur un nom de fichier. Les revalidations réduisent fortement la fenêtre TOCTOU,
mais ne constituent pas une garantie POSIX absolue comparable à une architecture
entièrement basée sur dir_fd/openat/renameat.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from file_janitor.models import (
    ActionItem,
    ActionKind,
    ConflictPolicy,
    FileCategory,
    FileIdentity,
)
from file_janitor.path_safety import (
    PathSafetyError,
    open_validated_source,
    validate_destination_path,
    validate_source_file,
    validate_source_empty_directory,
    validate_source_identity,
    validate_undo_restore_path,
)
from file_janitor.storage.history import (
    BatchStatus,
    HistoryStore,
    OperationStatus,
    TRASH_DIR,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KeeperGuard:
    """Identité live d'un keeper dont dépendent des TRASH de doublons."""

    keeper_path: Path
    identity: FileIdentity


_COPY_CHUNK_SIZE = 1024 * 1024
_REMOTE_MOVE_CHUNK_SIZE = 8 * 1024 * 1024
_COPY_TEMP_PREFIX = ".file-janitor-copy-"
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_UNSUPPORTED_ERRNOS = {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}
_RENAME_FALLBACK_ERRNOS = {errno.EXDEV, *_RENAME_UNSUPPORTED_ERRNOS}
_LINK_FALLBACK_ERRNOS = {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM}


def _unique_trash_path(original: Path) -> Path:
    """Retourne un chemin unique dans la corbeille File Janitor."""

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}_{original.name}"
    return TRASH_DIR / unique_name


def _renamed_destination(destination: Path) -> Path:
    """Retourne un chemin libre en ajoutant `` (n)`` avant le suffixe."""

    if not destination.exists():
        return destination

    parent = destination.parent
    stem = destination.stem
    suffix = destination.suffix

    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def resolve_destination_path(
    source: Path,
    destination: Path,
    conflict_policy: ConflictPolicy,
) -> Path:
    """Résout sans mutation une destination selon la politique de collision.

    Le résultat constitue un aperçu instantané. L'appelant qui effectuera une
    I/O doit obligatoirement répéter cette résolution juste avant la mutation.
    """

    if not isinstance(conflict_policy, ConflictPolicy):
        raise ValueError(
            "politique de collision non prise en charge : "
            f"{conflict_policy!r}"
        )

    destination = validate_destination_path(
        source,
        destination,
    )

    if not destination.exists():
        return destination

    if conflict_policy is ConflictPolicy.RENAME:
        candidate = _renamed_destination(destination)
        return validate_destination_path(source, candidate)

    if conflict_policy is ConflictPolicy.SKIP:
        raise FileExistsError(
            f"destination déjà existante, opération ignorée : {destination}"
        )

    if conflict_policy is ConflictPolicy.REPLACE:
        raise FileExistsError(
            "politique REPLACE désactivée en v0.2 pour éviter tout "
            f"écrasement non récupérable : {destination}"
        )

    raise ValueError(
        f"politique de collision non prise en charge : {conflict_policy!r}"
    )


def _resolve_destination(item: ActionItem) -> Path:
    """Résout la destination d'un MOVE/COPY au dernier moment."""

    if item.destination is None:
        raise ValueError("destination manquante")

    return resolve_destination_path(
        item.path,
        item.destination,
        item.conflict_policy,
    )


def _final_batch_status(
    *,
    success: int,
    failed: int,
) -> BatchStatus:
    """Détermine le statut final d'un batch d'exécution."""

    if failed == 0:
        return BatchStatus.COMPLETED

    if success == 0:
        return BatchStatus.FAILED

    return BatchStatus.PARTIAL


def _operation_kind(item: ActionItem) -> str:
    """Traduit une action en type persistant pour les échecs précoces."""

    if item.action is ActionKind.TRASH:
        return "delete"
    if item.action is ActionKind.RMDIR:
        return "rmdir"
    if item.action is ActionKind.MOVE:
        return "move"
    if item.action is ActionKind.COPY:
        return "copy"
    return item.action.value



def _fsync_file(path: Path) -> None:
    """Force sur le stockage les données et métadonnées d'un fichier."""

    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(directory: Path) -> None:
    """Force sur le stockage les mutations de noms d'un répertoire.

    POSIX permet d'ouvrir un répertoire en lecture puis d'appeler ``fsync`` sur
    son descripteur. ``O_DIRECTORY`` est ajouté lorsqu'il est disponible afin
    de refuser explicitement autre chose qu'un répertoire.
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY

    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_noreplace(
    source: Path,
    destination: Path,
) -> None:
    """Déplace atomiquement ``source`` sans jamais écraser ``destination``.

    2G-D cible Linux et utilise ``renameat2(..., RENAME_NOREPLACE)``.
    Contrairement à ``os.rename``/``os.replace``, la collision est arbitrée
    atomiquement par le noyau : si ``destination`` existe au moment exact du
    renommage, l'appel échoue avec ``EEXIST``.

    Aucun fallback cross-filesystem n'est effectué ici : ``EXDEV`` est
    propagé à l'appelant, qui peut alors déclencher le fallback transactionnel
    dédié.
    """

    libc = ctypes.CDLL(None, use_errno=True)

    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(
            errno.ENOSYS,
            "renameat2 indisponible sur cette plateforme",
            str(source),
            str(destination),
        ) from exc

    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int

    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )

    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(source),
            str(destination),
        )


def _move_same_filesystem(
    item: ActionItem,
    destination: Path,
) -> FileIdentity:
    """Effectue un MOVE atomique/no-clobber puis synchronise les répertoires.

    La source est revalidée une dernière fois juste avant ``renameat2``.
    Après succès du rename, une erreur de ``fsync`` est seulement journalisée :
    le déplacement est déjà visible sur le filesystem et ne doit pas devenir
    un faux FAILED dans l'historique.
    """

    validate_source_file(item.path)
    source_fd, stored_identity = open_validated_source(
        item.path,
        item.identity,
    )
    os.close(source_fd)

    validate_destination_path(
        item.path,
        destination,
    )

    # Le chemin source peut évoluer entre l'ouverture et le rename. Revalider
    # contre l'identité effectivement ouverte évite de publier un remplaçant.
    validate_source_file(item.path)
    validate_source_identity(item.path, stored_identity)

    source_parent = item.path.parent
    destination_parent = destination.parent

    _rename_noreplace(
        item.path,
        destination,
    )

    # Le nom final existe et la source n'existe plus à partir d'ici.
    try:
        _fsync_directory(destination_parent)
    except OSError as exc:
        logger.warning(
            "Impossible de synchroniser le répertoire destination après MOVE "
            "%s : %s",
            destination_parent,
            exc,
        )

    # Si les deux noms appartenaient au même répertoire, le fsync précédent
    # couvre les deux mutations de nom.
    if source_parent != destination_parent:
        try:
            _fsync_directory(source_parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire source après MOVE "
                "%s : %s",
                source_parent,
                exc,
            )

    # Un rename same-filesystem conserve l'objet filesystem : l'identité
    # capturée juste avant publication est donc aussi celle du nom final.
    return stored_identity


def _publish_move_temp_noreplace(
    temp_path: Path,
    destination: Path,
) -> None:
    """Publie un temporaire MOVE sans écraser une destination existante.

    Le chemin rapide utilise un hardlink, qui reste atomique et no-clobber.
    Certains filesystems FUSE/cloud refusent toutefois ``link(2)``. Dans ce
    cas, on revendique atomiquement le nom final avec ``O_CREAT|O_EXCL`` puis
    on y copie le contenu du temporaire. Le nom final peut alors être visible
    pendant la copie, mais un fichier préexistant n'est jamais écrasé.
    """

    try:
        os.link(temp_path, destination)
        return
    except OSError as exc:
        if exc.errno not in _LINK_FALLBACK_ERRNOS:
            raise

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    fd = -1
    published = False
    try:
        fd = os.open(destination, flags, 0o600)
        published = True
        with temp_path.open("rb") as source_file:
            with os.fdopen(fd, "wb", closefd=True) as destination_file:
                fd = -1
                shutil.copyfileobj(
                    source_file,
                    destination_file,
                    length=_COPY_CHUNK_SIZE,
                )
                destination_file.flush()
                os.fsync(destination_file.fileno())

        try:
            shutil.copystat(temp_path, destination, follow_symlinks=False)
        except OSError as exc:
            logger.warning(
                "Impossible de recopier toutes les métadonnées MOVE vers %s : %s",
                destination,
                exc,
            )
    except Exception:
        if fd >= 0:
            os.close(fd)
            fd = -1
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError as rollback_exc:
                logger.error(
                    "Rollback publication MOVE impossible pour %s : %s",
                    destination,
                    rollback_exc,
                )
        raise


def _copy_fd_direct_noreplace(
    fd: int,
    source_path: Path,
    destination: Path,
    *,
    progress_callback=None,
) -> None:
    """Copie une source déjà validée directement vers un nom final exclusif.

    Ce chemin est destiné aux filesystems qui refusent RENAME_NOREPLACE
    (notamment certains FUSE/rclone). Contrairement au fallback historique,
    il évite source -> temporaire -> destination et ne transfère les octets
    qu'une seule fois. ``O_CREAT|O_EXCL`` conserve la garantie no-clobber.

    Le nom final peut être visible pendant la copie : c'est la même limite que
    le fallback O_EXCL introduit en 4E-10F, mais sans seconde copie réseau.
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    destination_fd = -1
    published = False
    try:
        destination_fd = os.open(destination, flags, 0o600)
        published = True
        with os.fdopen(os.dup(fd), "rb", closefd=True) as source_file:
            with os.fdopen(destination_fd, "wb", closefd=True) as destination_file:
                destination_fd = -1
                copied = 0
                started_at = monotonic()

                class ProgressWriter:
                    def write(self, chunk: bytes) -> int:
                        nonlocal copied
                        written = destination_file.write(chunk)
                        copied += written
                        if progress_callback is not None:
                            progress_callback(
                                copied,
                                max(monotonic() - started_at, 0.000001),
                            )
                        return written

                shutil.copyfileobj(
                    source_file,
                    ProgressWriter(),
                    length=_REMOTE_MOVE_CHUNK_SIZE,
                )
                destination_file.flush()
                os.fsync(destination_file.fileno())

        try:
            shutil.copystat(source_path, destination, follow_symlinks=False)
        except OSError as exc:
            logger.warning(
                "Impossible de recopier toutes les métadonnées MOVE vers %s : %s",
                destination,
                exc,
            )
    except Exception:
        if destination_fd >= 0:
            os.close(destination_fd)
        if published:
            try:
                destination.unlink(missing_ok=True)
            except OSError as rollback_exc:
                logger.error(
                    "Rollback publication MOVE directe impossible pour %s : %s",
                    destination,
                    rollback_exc,
                )
        raise


def _move_unsupported_rename(
    item: ActionItem,
    destination: Path,
    *,
    progress_callback=None,
) -> FileIdentity:
    """MOVE sûr et mono-copie quand RENAME_NOREPLACE n'est pas supporté."""

    fd, _current_identity = open_validated_source(item.path, item.identity)
    destination_published = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        validate_destination_path(item.path, destination)

        _copy_fd_direct_noreplace(
            fd,
            item.path,
            destination,
            progress_callback=progress_callback,
        )
        destination_published = True

        stored_fd, stored_identity = open_validated_source(destination, None)
        os.close(stored_fd)

        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire destination après "
                "MOVE FUSE %s : %s",
                destination.parent,
                exc,
            )

        # Revalidation destructive identique au fallback transactionnel.
        validate_source_file(item.path)
        validate_source_identity(item.path, item.identity)

        try:
            item.path.unlink()
        except OSError:
            _rollback_published_destination(destination)
            destination_published = False
            raise

        try:
            _fsync_directory(item.path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire source après MOVE "
                "FUSE %s : %s",
                item.path.parent,
                exc,
            )

        destination_published = False
        return stored_identity

    except Exception:
        if destination_published and item.path.exists():
            try:
                _rollback_published_destination(destination)
                destination_published = False
            except OSError as rollback_exc:
                logger.error(
                    "Rollback MOVE FUSE impossible pour %s : %s",
                    destination,
                    rollback_exc,
                )
        raise
    finally:
        os.close(fd)


def _rollback_published_destination(
    destination: Path,
) -> None:
    """Supprime une destination publiée pendant un fallback MOVE avorté.

    Le rollback est utilisé uniquement tant que la source originale existe
    encore. Une erreur de synchronisation du répertoire après unlink reste un
    avertissement : la destination a déjà été retirée du filesystem vivant.
    """

    destination.unlink()

    try:
        _fsync_directory(destination.parent)
    except OSError as exc:
        logger.warning(
            "Impossible de synchroniser le répertoire après rollback MOVE %s : %s",
            destination.parent,
            exc,
        )


def _move_cross_filesystem(
    item: ActionItem,
    destination: Path,
) -> FileIdentity:
    """Fallback transactionnel pour un MOVE ayant échoué avec ``EXDEV``.

    Le contenu est d'abord copié depuis un descripteur source validé vers un
    temporaire privé du filesystem destination. Le temporaire est synchronisé,
    publié no-clobber avec ``os.link``, puis retiré.

    Tant que la source n'a pas été supprimée, toute erreur après publication
    provoque un rollback de la destination finale. La source est revalidée juste
    avant ``unlink`` afin de ne jamais supprimer un fichier remplacé entre-temps.
    """

    fd, _current_identity = open_validated_source(
        item.path,
        item.identity,
    )

    temp_path: Path | None = None
    destination_published = False

    try:
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        validate_destination_path(
            item.path,
            destination,
        )

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=_COPY_TEMP_PREFIX,
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)

        proc_fd_path = Path("/proc/self/fd") / str(fd)

        if proc_fd_path.exists():
            shutil.copy2(
                str(proc_fd_path),
                str(temp_path),
            )
        else:
            with os.fdopen(fd, "rb", closefd=True) as source_file:
                fd = -1
                with temp_path.open("wb") as temp_file:
                    shutil.copyfileobj(
                        source_file,
                        temp_file,
                        length=_COPY_CHUNK_SIZE,
                    )
                    temp_file.flush()

        _fsync_file(temp_path)

        # Publication no-clobber. Les hardlinks sont préférés mais certains
        # montages FUSE/cloud exigent le fallback O_EXCL.
        _publish_move_temp_noreplace(
            temp_path,
            destination,
        )
        destination_published = True

        stored_fd, stored_identity = open_validated_source(destination, None)
        os.close(stored_fd)

        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après publication "
                "MOVE cross-filesystem %s : %s",
                destination.parent,
                exc,
            )

        temp_path.unlink()
        temp_path = None

        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après nettoyage "
                "MOVE cross-filesystem %s : %s",
                destination.parent,
                exc,
            )

        # La source doit toujours être exactement celle du plan au moment où
        # elle devient destructive à supprimer.
        validate_source_file(item.path)
        validate_source_identity(
            item.path,
            item.identity,
        )

        try:
            item.path.unlink()
        except OSError:
            # La source est encore présente : revenir à l'état initial en
            # retirant la destination déjà publiée.
            _rollback_published_destination(destination)
            destination_published = False
            raise

        try:
            _fsync_directory(item.path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire source après MOVE "
                "cross-filesystem %s : %s",
                item.path.parent,
                exc,
            )

        destination_published = False
        return stored_identity

    except Exception:
        # Une erreur peut survenir après publication mais avant la tentative
        # d'unlink source (par exemple lors de la revalidation). Tant que la
        # source existe encore, retirer la destination restaure l'état initial.
        if destination_published and item.path.exists():
            try:
                _rollback_published_destination(destination)
                destination_published = False
            except OSError as rollback_exc:
                logger.error(
                    "Rollback MOVE cross-filesystem impossible pour %s : %s",
                    destination,
                    rollback_exc,
                )
        raise

    finally:
        if fd >= 0:
            os.close(fd)

        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Impossible de supprimer le temporaire MOVE %s",
                    temp_path,
                )


def _move_trusted_plain_rename(
    item: ActionItem,
    destination: Path,
) -> FileIdentity:
    """Déplace rapidement avec ``rename`` après un opt-in explicite.

    Ce chemin est volontairement distinct du chemin sûr : ``rename`` peut
    écraser une destination créée par un tiers entre la validation et l'appel.
    Il n'est donc utilisé que lorsque l'utilisateur a accepté ce risque et que
    ``RENAME_NOREPLACE`` n'est pas pris en charge par le filesystem.
    """

    validate_source_file(item.path)
    source_fd, stored_identity = open_validated_source(
        item.path,
        item.identity,
    )
    os.close(source_fd)

    validate_destination_path(item.path, destination)
    if destination.exists():
        raise FileExistsError(
            f"destination déjà existante, déplacement rapide refusé : {destination}"
        )

    validate_source_file(item.path)
    validate_source_identity(item.path, stored_identity)

    source_parent = item.path.parent
    destination_parent = destination.parent
    os.rename(item.path, destination)

    try:
        _fsync_directory(destination_parent)
    except OSError as exc:
        logger.warning(
            "Impossible de synchroniser le répertoire destination après "
            "MOVE rapide %s : %s",
            destination_parent,
            exc,
        )

    if source_parent != destination_parent:
        try:
            _fsync_directory(source_parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire source après "
                "MOVE rapide %s : %s",
                source_parent,
                exc,
            )

    return stored_identity


def _move_with_cross_filesystem_fallback(
    item: ActionItem,
    destination: Path,
    *,
    allow_unsafe_fast_move: bool = False,
    progress_callback=None,
) -> FileIdentity:
    """Exécute MOVE avec fallback transactionnel si renameat2 est indisponible.

    ``EXDEV`` couvre les changements de filesystem. ``EINVAL``, ``ENOSYS`` et
    ``EOPNOTSUPP`` couvrent les filesystems (notamment certains FUSE/rclone) qui
    acceptent rename(2) mais pas ``renameat2(RENAME_NOREPLACE)``.
    """

    try:
        return _move_same_filesystem(
            item,
            destination,
        )
    except OSError as exc:
        if exc.errno in _RENAME_UNSUPPORTED_ERRNOS:
            if allow_unsafe_fast_move:
                try:
                    return _move_trusted_plain_rename(item, destination)
                except OSError as fast_exc:
                    if fast_exc.errno != errno.EXDEV:
                        raise
                    return _move_cross_filesystem(item, destination)
            return _move_unsupported_rename(
                item,
                destination,
                progress_callback=progress_callback,
            )
        if exc.errno != errno.EXDEV:
            raise

        return _move_cross_filesystem(
            item,
            destination,
        )

def _copy_from_validated_source(
    item: ActionItem,
    destination: Path,
) -> None:
    """Copie transactionnellement, durablement et sans écrasement.

    Étapes 2G-A / 2G-B / 2G-C :
    - la source réellement ouverte est validée via ``fstat`` ;
    - les octets sont écrits dans un temporaire exclusif situé dans le même
      dossier que la destination finale ;
    - le temporaire est ``fsync`` avant toute publication ;
    - la publication finale utilise ``os.link`` et reste no-clobber ;
    - le répertoire est ``fsync`` après création du nom final ;
    - le nom temporaire est supprimé puis le répertoire est ``fsync`` à nouveau.

    Une erreur avant publication reste un échec transactionnel classique. Une
    erreur de synchronisation du répertoire après publication est journalisée
    comme avertissement : le fichier final existe déjà et ne peut pas être
    reclassé FAILED sans rendre l'historique incohérent avec le disque.
    """

    fd, _current_identity = open_validated_source(
        item.path,
        item.identity,
    )

    temp_path: Path | None = None

    try:
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Revalidation après création éventuelle du parent.
        validate_destination_path(
            item.path,
            destination,
        )

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=_COPY_TEMP_PREFIX,
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)

        proc_fd_path = Path("/proc/self/fd") / str(fd)

        if proc_fd_path.exists():
            # Conserve le comportement de copy2 (contenu + métadonnées) tout
            # en écrivant vers le temporaire, jamais vers le nom final.
            shutil.copy2(
                str(proc_fd_path),
                str(temp_path),
            )
        else:
            # Repli portable si /proc n'est pas disponible.
            with os.fdopen(fd, "rb", closefd=True) as source_file:
                fd = -1
                with temp_path.open("wb") as temp_file:
                    shutil.copyfileobj(
                        source_file,
                        temp_file,
                        length=_COPY_CHUNK_SIZE,
                    )
                    temp_file.flush()

        # 2G-C : les données du temporaire doivent atteindre le stockage avant
        # que son inode ne soit rendu visible sous le nom final.
        _fsync_file(temp_path)

        # 2G-B : publication no-clobber. Certains montages FUSE refusent les
        # hardlinks ; le helper bascule alors vers une création exclusive
        # O_CREAT|O_EXCL et copie le temporaire vers le nom final sans
        # écraser une destination existante.
        _publish_move_temp_noreplace(temp_path, destination)

        # À partir d'ici le nom final est publié. Une erreur de fsync du
        # répertoire ne doit pas transformer artificiellement l'opération en
        # FAILED : le fichier final existe déjà sur le système vivant.
        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après publication "
                "COPY %s : %s",
                destination.parent,
                exc,
            )

        # Le fichier possède maintenant deux noms vers le même inode.
        # Retirer le nom temporaire termine la publication sans toucher au
        # contenu désormais accessible par destination.
        temp_path.unlink()
        temp_path = None

        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après nettoyage "
                "COPY %s : %s",
                destination.parent,
                exc,
            )

    finally:
        if fd >= 0:
            os.close(fd)

        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Impossible de supprimer le temporaire COPY %s",
                    temp_path,
                )


def execute_items(
    items: list[ActionItem],
    *,
    root: str,
    store: HistoryStore,
    progress_callback=None,
    cancel_callback=None,
    dependent_trash_keepers: dict[Path, Path] | None = None,
    dependent_trash_keeper_guards: dict[Path, KeeperGuard] | None = None,
    allow_unsafe_fast_move: bool = False,
    existing_batch_id: int | None = None,
    existing_operation_ids: list[int] | None = None,
) -> tuple[int, int, list[str]]:
    """Applique les actions explicites sur le disque.

    ``dependent_trash_keepers`` lie une mise à la corbeille au succès de
    l'action physique qui conserve son keeper. ``dependent_trash_keeper_guards``
    revalide en plus l'objet effectivement conservé immédiatement avant chaque
    TRASH dépendant. Une dépendance non satisfaite ou un keeper devenu invalide
    est enregistré comme échec sans effectuer d'I/O sur le doublon.
    """

    actionable_items = [item for item in items if item.action is not ActionKind.NONE]
    total_actions = len(actionable_items)
    resuming_existing = existing_batch_id is not None
    if resuming_existing != (existing_operation_ids is not None):
        raise ValueError(
            "existing_batch_id et existing_operation_ids doivent être "
            "fournis ensemble"
        )

    if resuming_existing:
        assert existing_batch_id is not None
        assert existing_operation_ids is not None
        batch_id = existing_batch_id
        queued_operation_ids = list(existing_operation_ids)
        batch = store.get_batch(batch_id)
        if batch is None:
            raise ValueError(f"batch d'historique introuvable : {batch_id}")
        if batch.status is not BatchStatus.RUNNING:
            raise ValueError(
                "un batch repris doit être réservé dans l'état RUNNING"
            )
        if batch.root != root:
            raise ValueError("la racine reprise ne correspond pas au batch")
        if len(queued_operation_ids) != total_actions:
            raise ValueError(
                "le nombre d'opérations QUEUED ne correspond pas aux actions"
            )
        operations_by_id = {
            operation.id: operation
            for operation in store.get_operations(batch_id)
        }
        if len(set(queued_operation_ids)) != len(queued_operation_ids):
            raise ValueError("identifiants d'opérations QUEUED dupliqués")
        for operation_id, item in zip(
            queued_operation_ids,
            actionable_items,
            strict=True,
        ):
            operation = operations_by_id.get(operation_id)
            if operation is None or operation.status is not OperationStatus.QUEUED:
                raise ValueError(
                    "la reprise exige des opérations QUEUED du batch réservé"
                )
            expected_stored_path = (
                item.destination
                if item.action in {ActionKind.MOVE, ActionKind.COPY}
                else (item.path if item.action is ActionKind.RMDIR else None)
            )
            if (
                operation.kind != _operation_kind(item)
                or operation.original_path != item.path
                or operation.stored_path != expected_stored_path
                or operation.size != item.size
                or operation.category != item.category.value
                or operation.original_identity != item.identity
                or (
                    item.action in {ActionKind.MOVE, ActionKind.COPY}
                    and operation.conflict_policy != item.conflict_policy.value
                )
            ):
                raise ValueError(
                    "l'action reprise ne correspond pas à l'intention "
                    "persistée"
                )
    else:
        batch_id = store.start_batch(root, planned_count=total_actions)
        queued_operation_ids = store.record_queued_operations(
            batch_id,
            [
                (
                    _operation_kind(item),
                    item.path,
                    (
                        item.destination
                        if item.action in {ActionKind.MOVE, ActionKind.COPY}
                        else (item.path if item.action is ActionKind.RMDIR else None)
                    ),
                    item.size,
                    item.category.value,
                    item.identity,
                    item.conflict_policy.value,
                )
                for item in actionable_items
            ],
        )

    success = 0
    failed = 0
    errors: list[str] = []

    cancelled = False
    dependent_trash_keepers = dependent_trash_keepers or {}
    dependent_trash_keeper_guards = dependent_trash_keeper_guards or {}
    completed_paths: set[Path] = set()
    keeper_guard_states: dict[Path, tuple[Path, FileIdentity]] = {
        guard.keeper_path: (guard.keeper_path, guard.identity)
        for guard in dependent_trash_keeper_guards.values()
    }

    def report_completed(index: int, item: ActionItem) -> None:
        """Signale une action finalisée après sa dernière écriture d'historique."""

        if progress_callback is not None:
            progress_callback(
                ("execution_done", index, total_actions, str(item.path))
            )

    for action_index, item in enumerate(actionable_items, start=1):
        # L'annulation est coopérative : une action déjà commencée termine sa
        # transaction. La demande est honorée avant l'action suivante.
        if cancel_callback is not None and cancel_callback():
            cancelled = True
            break

        operation_id = queued_operation_ids[action_index - 1]

        keeper_path = dependent_trash_keepers.get(item.path)
        if (
            item.action is ActionKind.TRASH
            and keeper_path is not None
            and keeper_path not in completed_paths
        ):
            error = (
                "mise à la corbeille non démarrée : l'opération de conservation "
                f"du keeper « {keeper_path} » n'a pas réussi"
            )
            errors.append(f"{item.path}: {error}")
            failed += 1
            store.set_operation_status(
                operation_id,
                OperationStatus.FAILED,
                error=error,
            )
            logger.warning(
                "TRASH bloqué pour %s : keeper non conservé %s",
                item.path,
                keeper_path,
            )
            report_completed(action_index, item)
            continue

        keeper_guard = dependent_trash_keeper_guards.get(item.path)
        if item.action is ActionKind.TRASH and keeper_guard is not None:
            guard_path, guard_identity = keeper_guard_states[
                keeper_guard.keeper_path
            ]
            try:
                validate_source_file(guard_path)
                validate_source_identity(guard_path, guard_identity)
            except (OSError, PathSafetyError) as exc:
                error = (
                    "mise à la corbeille non démarrée : le keeper "
                    f"« {guard_path} » a changé depuis le préflight ({exc})"
                )
                errors.append(f"{item.path}: {error}")
                failed += 1
                store.set_operation_status(
                    operation_id,
                    OperationStatus.FAILED,
                    error=error,
                )
                logger.warning(
                    "TRASH bloqué pour %s : keeper live invalide %s",
                    item.path,
                    guard_path,
                )
                report_completed(action_index, item)
                continue

        if progress_callback is not None:
            progress_callback(
                ("execution", action_index, total_actions, str(item.path))
            )

        published_path: Path | None = None
        published_identity: FileIdentity | None = None

        try:
            if item.action is ActionKind.RMDIR:
                validate_source_empty_directory(item.path, item.identity)
            else:
                validate_source_file(item.path)
                validate_source_identity(item.path, item.identity)

            if item.action is ActionKind.RMDIR:
                store.start_queued_operation(operation_id, stored_path=item.path)
                # rmdir vérifie une dernière fois l'identité et l'absence
                # d'entrée de façon atomique avec la suppression du nom.
                validate_source_empty_directory(item.path, item.identity)
                os.rmdir(item.path)
                try:
                    _fsync_directory(item.path.parent)
                except OSError as exc:
                    logger.warning(
                        "Impossible de synchroniser le parent après retrait "
                        "du dossier vide %s : %s",
                        item.path.parent,
                        exc,
                    )

            elif item.action is ActionKind.TRASH:
                trash_path = _unique_trash_path(item.path)

                store.start_queued_operation(
                    operation_id,
                    stored_path=trash_path,
                )

                # 2G-F : TRASH possède la même mécanique filesystem que MOVE.
                # Le nom de corbeille est publié atomiquement sans écrasement
                # et EXDEV déclenche le fallback transactionnel de 2G-E.
                stored_identity = _move_with_cross_filesystem_fallback(
                    item,
                    trash_path,
                )
                store.set_operation_stored_identity(
                    operation_id,
                    stored_identity,
                )

            elif item.action is ActionKind.MOVE:
                if item.destination is None:
                    error = "destination manquante pour une opération MOVE"
                    errors.append(f"{item.path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        operation_id,
                        OperationStatus.FAILED,
                        error=error,
                    )
                    report_completed(action_index, item)
                    continue

                try:
                    effective_destination = _resolve_destination(item)
                except (FileExistsError, PathSafetyError) as exc:
                    message = f"{item.path}: {exc}"
                    errors.append(message)
                    failed += 1
                    store.set_operation_status(
                        operation_id,
                        OperationStatus.FAILED,
                        error=str(exc),
                    )
                    report_completed(action_index, item)
                    continue

                store.start_queued_operation(
                    operation_id,
                    stored_path=effective_destination,
                )

                effective_destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                # 2G-D / 2G-E : rename atomique same-filesystem puis fallback
                # transactionnel cross-filesystem uniquement sur EXDEV.
                def report_safe_copy(copied: int, elapsed: float) -> None:
                    if progress_callback is None:
                        return
                    progress_callback(
                        (
                            "execution_copy",
                            action_index,
                            total_actions,
                            str(item.path),
                            copied,
                            item.size,
                            elapsed,
                        )
                    )

                move_kwargs = {}
                if allow_unsafe_fast_move:
                    move_kwargs["allow_unsafe_fast_move"] = True
                if progress_callback is not None:
                    move_kwargs["progress_callback"] = report_safe_copy
                stored_identity = _move_with_cross_filesystem_fallback(
                    item,
                    effective_destination,
                    **move_kwargs,
                )
                store.set_operation_stored_identity(
                    operation_id,
                    stored_identity,
                )
                published_path = effective_destination
                published_identity = stored_identity

            elif item.action is ActionKind.COPY:
                if item.destination is None:
                    error = "destination manquante pour une opération COPY"
                    errors.append(f"{item.path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        operation_id,
                        OperationStatus.FAILED,
                        error=error,
                    )
                    report_completed(action_index, item)
                    continue

                try:
                    effective_destination = _resolve_destination(item)
                except (FileExistsError, PathSafetyError) as exc:
                    message = f"{item.path}: {exc}"
                    errors.append(message)
                    failed += 1
                    store.set_operation_status(
                        operation_id,
                        OperationStatus.FAILED,
                        error=str(exc),
                    )
                    report_completed(action_index, item)
                    continue

                store.start_queued_operation(
                    operation_id,
                    stored_path=effective_destination,
                )

                _copy_from_validated_source(
                    item,
                    effective_destination,
                )

                # 2G-H : persiste l'identité du fichier effectivement publié.
                copy_fd, copy_identity = open_validated_source(
                    effective_destination,
                    None,
                )
                os.close(copy_fd)
                store.set_operation_stored_identity(
                    operation_id,
                    copy_identity,
                )
                published_path = effective_destination
                published_identity = copy_identity

            else:
                message = (
                    f"{item.path}: type d'action non pris en charge "
                    f"({item.action!r})"
                )
                errors.append(message)
                failed += 1
                store.set_operation_status(
                    operation_id,
                    OperationStatus.FAILED,
                    error=message.split(": ", 1)[-1],
                )
                report_completed(action_index, item)
                continue

            store.set_operation_status(
                operation_id,
                OperationStatus.COMPLETED,
            )

            success += 1
            completed_paths.add(item.path)
            if (
                item.path in keeper_guard_states
                and published_path is not None
                and published_identity is not None
            ):
                keeper_guard_states[item.path] = (
                    published_path,
                    published_identity,
                )

        except (OSError, PathSafetyError) as exc:
            message = f"{item.path}: {exc}"
            errors.append(message)
            failed += 1

            store.set_operation_status(
                operation_id,
                OperationStatus.FAILED,
                error=str(exc),
            )

            logger.warning(
                "Échec sur %s : %s",
                item.path,
                exc,
            )

        report_completed(action_index, item)

    if resuming_existing:
        batch = store.get_batch(batch_id)
        assert batch is not None and batch.planned_count is not None
        persisted_operations = store.get_operations(batch_id)
        total_success = sum(
            operation.status is OperationStatus.COMPLETED
            for operation in persisted_operations
        )
        total_failed = sum(
            operation.status is OperationStatus.FAILED
            for operation in persisted_operations
        )
        skipped = max(0, batch.planned_count - total_success - total_failed)
        final_status = (
            BatchStatus.CANCELLED
            if cancelled or skipped
            else _final_batch_status(
                success=total_success,
                failed=total_failed,
            )
        )
    else:
        total_success = success
        total_failed = failed
        final_status = (
            BatchStatus.CANCELLED
            if cancelled
            else _final_batch_status(
                success=success,
                failed=failed,
            )
        )
        skipped = (
            max(0, total_actions - success - failed)
            if cancelled
            else 0
        )
    store.finish_batch_execution(
        batch_id,
        final_status,
        success_count=total_success,
        failed_count=total_failed,
        skipped_count=skipped,
    )

    return batch_id, success, errors



def _restore_same_filesystem(
    stored_path: Path,
    original_path: Path,
    expected_identity: FileIdentity,
) -> None:
    """Restaure par rename atomique/no-clobber après revalidation."""

    fd, _current_identity = open_validated_source(
        stored_path,
        expected_identity,
    )
    os.close(fd)

    original_path.parent.mkdir(parents=True, exist_ok=True)
    validate_undo_restore_path(original_path)
    if original_path.exists():
        raise FileExistsError(
            "chemin original déjà occupé ; restauration refusée pour éviter un écrasement"
        )

    validate_source_file(stored_path)
    validate_source_identity(stored_path, expected_identity)

    stored_parent = stored_path.parent
    original_parent = original_path.parent
    _rename_noreplace(stored_path, original_path)

    for directory in dict.fromkeys((original_parent, stored_parent)):
        try:
            _fsync_directory(directory)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après undo %s : %s",
                directory, exc,
            )


def _restore_cross_filesystem(
    stored_path: Path,
    original_path: Path,
    expected_identity: FileIdentity,
) -> None:
    """Fallback transactionnel d'undo lorsque le rename renvoie EXDEV."""

    fd, _current_identity = open_validated_source(
        stored_path,
        expected_identity,
    )
    temp_path: Path | None = None
    original_published = False

    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        validate_undo_restore_path(original_path)
        if original_path.exists():
            raise FileExistsError(
                "chemin original déjà occupé ; restauration refusée pour éviter un écrasement"
            )

        temp_fd, temp_name = tempfile.mkstemp(
            prefix=_COPY_TEMP_PREFIX, suffix=".tmp", dir=original_path.parent
        )
        os.close(temp_fd)
        temp_path = Path(temp_name)

        proc_fd_path = Path("/proc/self/fd") / str(fd)
        if proc_fd_path.exists():
            shutil.copy2(str(proc_fd_path), str(temp_path))
        else:
            with os.fdopen(fd, "rb", closefd=True) as source_file:
                fd = -1
                with temp_path.open("wb") as temp_file:
                    shutil.copyfileobj(source_file, temp_file, length=_COPY_CHUNK_SIZE)
                    temp_file.flush()

        _fsync_file(temp_path)
        _publish_move_temp_noreplace(temp_path, original_path)
        original_published = True

        try:
            _fsync_directory(original_path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après publication undo %s : %s",
                original_path.parent, exc,
            )

        temp_path.unlink()
        temp_path = None

        validate_source_file(stored_path)
        validate_source_identity(stored_path, expected_identity)

        try:
            stored_path.unlink()
        except OSError:
            _rollback_published_destination(original_path)
            original_published = False
            raise

        try:
            _fsync_directory(stored_path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire de sauvegarde après undo %s : %s",
                stored_path.parent, exc,
            )

        original_published = False

    except Exception:
        if original_published and stored_path.exists():
            try:
                _rollback_published_destination(original_path)
                original_published = False
            except OSError as rollback_exc:
                logger.error(
                    "Rollback undo cross-filesystem impossible pour %s : %s",
                    original_path, rollback_exc,
                )
        raise
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Impossible de supprimer le temporaire undo %s", temp_path)


def _restore_unsupported_rename(
    stored_path: Path,
    original_path: Path,
    expected_identity: FileIdentity,
) -> None:
    """Undo mono-copie pour un filesystem sans RENAME_NOREPLACE."""

    fd, _current_identity = open_validated_source(
        stored_path,
        expected_identity,
    )
    original_published = False
    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        validate_undo_restore_path(original_path)
        if original_path.exists():
            raise FileExistsError(
                "chemin original déjà occupé ; restauration refusée pour éviter un écrasement"
            )

        _copy_fd_direct_noreplace(fd, stored_path, original_path)
        original_published = True

        try:
            _fsync_directory(original_path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire après publication "
                "undo FUSE %s : %s",
                original_path.parent,
                exc,
            )

        validate_source_file(stored_path)
        validate_source_identity(stored_path, expected_identity)

        try:
            stored_path.unlink()
        except OSError:
            _rollback_published_destination(original_path)
            original_published = False
            raise

        try:
            _fsync_directory(stored_path.parent)
        except OSError as exc:
            logger.warning(
                "Impossible de synchroniser le répertoire de sauvegarde après "
                "undo FUSE %s : %s",
                stored_path.parent,
                exc,
            )

        original_published = False

    except Exception:
        if original_published and stored_path.exists():
            try:
                _rollback_published_destination(original_path)
                original_published = False
            except OSError as rollback_exc:
                logger.error(
                    "Rollback undo FUSE impossible pour %s : %s",
                    original_path,
                    rollback_exc,
                )
        raise
    finally:
        os.close(fd)


def _restore_with_cross_filesystem_fallback(
    stored_path: Path,
    original_path: Path,
    expected_identity: FileIdentity,
) -> None:
    """Restaure MOVE/TRASH sans écrasement avec fallback transactionnel."""

    try:
        _restore_same_filesystem(
            stored_path,
            original_path,
            expected_identity,
        )
    except OSError as exc:
        if exc.errno in _RENAME_UNSUPPORTED_ERRNOS:
            _restore_unsupported_rename(
                stored_path,
                original_path,
                expected_identity,
            )
            return
        if exc.errno != errno.EXDEV:
            raise
        _restore_cross_filesystem(
            stored_path,
            original_path,
            expected_identity,
        )


def _undo_destination_directories(
    operations: list,
    *,
    root: Path,
) -> set[Path]:
    """Retourne les dossiers de destination éligibles au nettoyage après undo.

    Seuls les MOVE/COPY situés sous la racine du batch sont considérés. La
    racine elle-même n'est jamais retournée.
    """

    root = root.expanduser().absolute()
    directories: set[Path] = set()

    for op in operations:
        if op.kind not in {"move", "copy"} or op.stored_path is None:
            continue

        current = op.stored_path.expanduser().absolute().parent
        try:
            current.relative_to(root)
        except ValueError:
            continue

        while current != root:
            directories.add(current)
            parent = current.parent
            if parent == current:
                break
            current = parent

    return directories


def _is_safe_undo_cleanup_directory(
    directory: Path,
    *,
    root: Path,
) -> bool:
    """Vérifie qu'un dossier peut être pruné sans sortir de la racine du batch.

    Le contrôle reste volontairement conservateur : tout lien symbolique dans
    la chaîne du chemin fait abandonner le nettoyage de ce dossier. La racine
    du batch elle-même n'est jamais éligible.
    """

    root = root.expanduser().absolute()
    directory = directory.expanduser().absolute()

    if directory == root:
        return False

    try:
        directory.relative_to(root)
    except ValueError:
        return False

    # Refuse toute chaîne de chemin contenant un symlink. On remonte jusqu'à
    # la racine du filesystem afin de rester cohérent avec les validations de
    # destination déjà appliquées ailleurs dans l'exécuteur.
    current = directory
    while True:
        if current.is_symlink():
            return False
        parent = current.parent
        if parent == current:
            break
        current = parent

    return True


def _prune_empty_undo_destinations(
    directories: set[Path],
    *,
    root: Path,
) -> None:
    """Supprime, du plus profond au plus haut, les destinations devenues vides.

    ``rmdir`` est volontairement utilisé : un dossier contenant encore le
    moindre élément est conservé. Les chemins extérieurs à ``root`` et toute
    chaîne contenant un lien symbolique sont ignorés. Les erreurs de nettoyage
    n'annulent pas une restauration de fichiers déjà réussie.
    """

    root = root.expanduser().absolute()

    for directory in sorted(
        directories,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            if not _is_safe_undo_cleanup_directory(directory, root=root):
                logger.warning(
                    "Nettoyage undo ignoré pour chemin non sûr : %s",
                    directory,
                )
                continue
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            # ENOTEMPTY/EEXIST signifient simplement que le dossier doit être
            # conservé. Toute autre erreur reste non destructive et est loggée.
            if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                logger.warning(
                    "Impossible de supprimer le dossier destination vide après undo %s : %s",
                    directory,
                    exc,
                )


def undo_batch(
    batch_id: int,
    store: HistoryStore,
) -> tuple[int, list[str]]:
    """Annule ou réessaie transactionnellement les mutations d'un batch.

    Les opérations ``COMPLETED`` n'ont encore jamais été restaurées. Les
    opérations ``UNDO_FAILED`` ont échoué lors d'une tentative précédente et
    restent explicitement réessayables. Les opérations déjà ``UNDONE`` sont
    exclues du travail courant, mais restent prises en compte pour déterminer
    l'état global du batch et pour le nettoyage final des dossiers créés.
    """

    batch_metadata = store.get_batch(batch_id)
    # 4E-26B: une opération orpheline historique ne doit jamais devenir une
    # cible Undo simplement parce que sa ligne est encore lisible.
    if batch_metadata is None:
        return 0, [
            f"batch #{batch_id}: batch d’historique introuvable ; "
            "undo refusé par sécurité"
        ]
    if not batch_metadata.has_actionable_metadata():
        return 0, [
            f"batch #{batch_id}: métadonnées persistées invalides ; "
            "undo refusé par sécurité"
        ]

    consistency_issues = store.get_batch_consistency_issues(batch_id)
    if consistency_issues:
        return 0, [
            f"batch #{batch_id}: incohérence batch/opérations "
            f"({', '.join(consistency_issues)}) ; undo refusé par sécurité"
        ]

    operations = store.get_operations(batch_id)
    undoable = [
        op
        for op in operations
        if op.status in {
            OperationStatus.COMPLETED,
            OperationStatus.UNDO_FAILED,
        }
    ]
    historical_undo_targets = [
        op
        for op in operations
        if op.status in {
            OperationStatus.COMPLETED,
            OperationStatus.UNDO_FAILED,
            OperationStatus.UNDONE,
        }
    ]

    batch = store.get_batch(batch_id)
    cleanup_directories = (
        _undo_destination_directories(
            historical_undo_targets,
            root=Path(batch.root),
        )
        if batch is not None
        else set()
    )

    success = 0
    failed = 0
    errors: list[str] = []

    if not undoable:
        # Un batch déjà intégralement annulé est idempotent : une nouvelle
        # tentative ne doit surtout pas dégrader son état en UNDO_FAILED.
        if historical_undo_targets and all(
            op.status is OperationStatus.UNDONE
            for op in historical_undo_targets
        ):
            store.set_batch_status(batch_id, BatchStatus.UNDONE)
            return 0, errors

        errors.append(
            f"batch #{batch_id}: aucune opération terminée à annuler"
        )
        store.set_batch_status(
            batch_id,
            BatchStatus.UNDO_FAILED,
        )
        return 0, errors

    store.set_batch_status(
        batch_id,
        BatchStatus.UNDOING,
    )

    for op in reversed(undoable):
        try:
            if (
                op.original_path is None
                or op.original_path_raw is not None
                or op.stored_path_raw is not None
            ):
                error = (
                    "chemin persisté invalide ; undo refusé par sécurité"
                )
                errors.append(f"opération #{op.id}: {error}")
                failed += 1
                store.set_operation_status(
                    op.id,
                    OperationStatus.UNDO_FAILED,
                    error=error,
                )
                continue

            if op.size_raw is not None or op.category_raw is not None:
                error = (
                    "métadonnées scalaires persistées invalides ; "
                    "undo refusé par sécurité"
                )
                errors.append(f"opération #{op.id}: {error}")
                failed += 1
                store.set_operation_status(
                    op.id,
                    OperationStatus.UNDO_FAILED,
                    error=error,
                )
                continue

            # 4E-22A: une valeur persistée inconnue ne doit jamais tomber dans
            # la branche MOVE/TRASH par défaut et déclencher une restauration.
            if op.kind not in {"copy", "move", "delete", "rmdir"}:
                error = (
                    f"type d’opération persisté inconnu ({op.kind!r}) ; "
                    "undo refusé par sécurité"
                )
                errors.append(f"{op.original_path}: {error}")
                failed += 1
                store.set_operation_status(
                    op.id,
                    OperationStatus.UNDO_FAILED,
                    error=error,
                )
                continue

            # Un ZIP d'archivage est le keeper des sources mises à la
            # corbeille dans le même batch. Si une source n'a pas pu être
            # restaurée, conserver le ZIP pour éviter toute perte de données;
            # un nouvel essai pourra le supprimer après restauration complète.
            if (
                op.kind == "copy"
                and op.category == FileCategory.ARCHIVE.value
            ):
                can_remove_archive = failed == 0
                for member_op in store.get_operations(batch_id):
                    if (
                        member_op.category != FileCategory.ARCHIVE.value
                        or member_op.kind != "delete"
                        or member_op.status is OperationStatus.UNDONE
                    ):
                        continue
                    if (
                        member_op.status is not OperationStatus.FAILED
                        or member_op.original_path is None
                        or member_op.original_identity is None
                        or member_op.original_identity_raw is not None
                    ):
                        can_remove_archive = False
                        break
                    try:
                        validate_source_file(member_op.original_path)
                        validate_source_identity(
                            member_op.original_path,
                            member_op.original_identity,
                        )
                    except (OSError, PathSafetyError):
                        can_remove_archive = False
                        break

                if not can_remove_archive:
                    error = (
                        "archive ZIP conservée car une ou plusieurs sources "
                        "n’ont pas pu être restaurées en toute sécurité"
                    )
                    errors.append(f"{op.stored_path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        op.id,
                        OperationStatus.UNDO_FAILED,
                        error=error,
                    )
                    continue

            if op.kind == "rmdir":
                if (
                    op.stored_path != op.original_path
                    or op.category != FileCategory.EMPTY_DIRECTORY.value
                    or op.original_identity is None
                    or op.original_identity_raw is not None
                ):
                    error = "métadonnées du dossier vide invalides ; undo refusé"
                    errors.append(f"{op.original_path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        op.id,
                        OperationStatus.UNDO_FAILED,
                        error=error,
                    )
                    continue

                restore_path = validate_undo_restore_path(op.original_path)
                if restore_path.exists() or restore_path.is_symlink():
                    raise FileExistsError(
                        "le chemin original a été recréé ; restauration refusée"
                    )
                # mkdir ne remplace jamais une entrée existante. Les dossiers
                # parents ont été restaurés avant celui-ci car l'undo parcourt
                # les opérations dans l'ordre inverse du plan enfants->parents.
                os.mkdir(restore_path)
                try:
                    _fsync_directory(restore_path.parent)
                except OSError as exc:
                    logger.warning(
                        "Impossible de synchroniser le parent après restauration "
                        "du dossier vide %s : %s",
                        restore_path.parent,
                        exc,
                    )
                store.set_operation_status(op.id, OperationStatus.UNDONE)
                success += 1
                continue

            if op.stored_path is None or not op.stored_path.exists():
                message = (
                    f"{op.original_path}: "
                    "fichier de sauvegarde introuvable"
                )
                errors.append(message)
                failed += 1
                store.set_operation_status(
                    op.id,
                    OperationStatus.UNDO_FAILED,
                    error="fichier de sauvegarde introuvable",
                )
                continue

            if op.kind == "copy":
                if op.stored_identity is None:
                    # 4E-21C: refuser les deux cas, mais ne jamais masquer une
                    # preuve persistée corrompue sous le diagnostic "absente".
                    error = (
                        "identité de la copie persistée invalide ; undo refusé "
                        "par sécurité"
                        if op.stored_identity_raw is not None
                        else "identité de la copie absente ; undo refusé par sécurité"
                    )
                    errors.append(f"{op.original_path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        op.id,
                        OperationStatus.UNDO_FAILED,
                        error=error,
                    )
                    continue

                if op.stored_path.is_symlink():
                    error = (
                        "copie remplacée par un lien symbolique ; "
                        "undo refusé"
                    )
                    errors.append(f"{op.original_path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        op.id,
                        OperationStatus.UNDO_FAILED,
                        error=error,
                    )
                    continue

                copy_fd, _copy_identity = open_validated_source(
                    op.stored_path,
                    op.stored_identity,
                )
                os.close(copy_fd)

                validate_source_file(op.stored_path)
                validate_source_identity(
                    op.stored_path,
                    op.stored_identity,
                )

                op.stored_path.unlink()

                try:
                    _fsync_directory(op.stored_path.parent)
                except OSError as exc:
                    logger.warning(
                        "Impossible de synchroniser le répertoire après "
                        "undo COPY %s : %s",
                        op.stored_path.parent,
                        exc,
                    )

            else:
                if op.stored_identity is None:
                    # 4E-21C: même politique fail-closed pour MOVE et TRASH.
                    error = (
                        "identité du fichier stocké persistée invalide ; "
                        "undo refusé par sécurité"
                        if op.stored_identity_raw is not None
                        else "identité du fichier stocké absente ; "
                        "undo refusé par sécurité"
                    )
                    errors.append(f"{op.original_path}: {error}")
                    failed += 1
                    store.set_operation_status(
                        op.id,
                        OperationStatus.UNDO_FAILED,
                        error=error,
                    )
                    continue

                _restore_with_cross_filesystem_fallback(
                    op.stored_path,
                    op.original_path,
                    op.stored_identity,
                )

            store.set_operation_status(
                op.id,
                OperationStatus.UNDONE,
            )
            success += 1

        except (OSError, PathSafetyError) as exc:
            message = f"{op.original_path}: {exc}"
            errors.append(message)
            failed += 1

            store.set_operation_status(
                op.id,
                OperationStatus.UNDO_FAILED,
                error=str(exc),
            )

            logger.warning(
                "Échec undo sur %s : %s",
                op.original_path,
                exc,
            )

    # Le statut final doit refléter l'état persistant de l'ensemble du batch,
    # pas uniquement le nombre de réussites de cette tentative. C'est crucial
    # pour un retry après UNDO_PARTIAL : une opération déjà UNDONE reste un
    # succès acquis même si la seule opération retentée échoue à nouveau.
    refreshed = store.get_operations(batch_id)
    undone_count = sum(
        op.status is OperationStatus.UNDONE
        for op in refreshed
    )
    undo_failed_count = sum(
        op.status is OperationStatus.UNDO_FAILED
        for op in refreshed
    )

    if undo_failed_count == 0 and undone_count > 0:
        final_status = BatchStatus.UNDONE
    elif undo_failed_count > 0 and undone_count > 0:
        final_status = BatchStatus.UNDO_PARTIAL
    else:
        final_status = BatchStatus.UNDO_FAILED

    store.set_batch_status(batch_id, final_status)

    # Le nettoyage n'a lieu qu'après un rollback intégralement réussi. Un
    # undo partiel conserve l'arborescence destination afin de ne masquer
    # aucun état intermédiaire nécessitant une intervention. Lors d'un retry,
    # ``cleanup_directories`` contient aussi les destinations des opérations
    # déjà UNDONE afin de pouvoir enfin supprimer leurs dossiers restés vides.
    if final_status is BatchStatus.UNDONE and batch is not None:
        _prune_empty_undo_destinations(
            cleanup_directories,
            root=Path(batch.root),
        )

    return success, errors
