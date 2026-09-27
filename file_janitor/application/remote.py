"""Détection légère des dossiers situés sur un filesystem distant."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from file_janitor.models import FileRecord, ScanResult
from file_janitor.path_safety import identity_from_stat
from file_janitor.scanner.empty_directories import (
    EmptyDirectoryRecord,
    EmptyDirectoryScan,
)
from file_janitor.scanner.ignore import (
    DEFAULT_EXCLUDES,
    IGNORE_FILENAME,
    build_ignore_spec,
    is_excluded,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class RemoteFolderInfo:
    is_remote: bool
    filesystem_type: str | None
    mount_point: Path | None
    source: str | None = None


_REMOTE_FS_TYPES = {
    "nfs",
    "nfs4",
    "cifs",
    "smb3",
    "smbfs",
    "sshfs",
    "fuse.sshfs",
    "davfs",
    "fuse.davfs",
    "ceph",
    "fuse.ceph",
    "glusterfs",
    "fuse.glusterfs",
    "afs",
    "fuse.afs",
    "fuse.rclone",
    "fuse.pcloud",
    "fuse.s3fs",
    "fuse.gcsfuse",
    "fuse.goofys",
    "fuse.curlftpfs",
    "fuse.ftpfs",
    "fuse.onedrive",
    "fuse.google-drive-ocamlfuse",
    "fuse.gvfsd-fuse",
}


def _unescape_mountinfo_field(value: str) -> str:
    """Décode les séquences octales utilisées dans /proc/self/mountinfo."""

    for escaped, literal in (
        ("\\040", " "),
        ("\\011", "\t"),
        ("\\012", "\n"),
        ("\\134", "\\"),
    ):
        value = value.replace(escaped, literal)
    return value


def _mount_detail_entries(
    mountinfo_text: str,
) -> list[tuple[Path, str, str | None]]:
    """Extrait point de montage, type et source depuis ``mountinfo``."""

    entries: list[tuple[Path, str, str | None]] = []
    for line in mountinfo_text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 5 or not right_fields:
            continue

        mount_point = Path(_unescape_mountinfo_field(left_fields[4]))
        if not mount_point.is_absolute():
            continue

        fs_type = right_fields[0].lower()
        source = (
            _unescape_mountinfo_field(right_fields[1])
            if len(right_fields) >= 2
            else None
        )
        entries.append((mount_point, fs_type, source))
    return entries


def _mount_entries(mountinfo_text: str) -> list[tuple[Path, str]]:
    """Extrait (mount_point, fs_type) depuis le contenu mountinfo."""

    return [
        (mount_point, fs_type)
        for mount_point, fs_type, _source in _mount_detail_entries(mountinfo_text)
    ]


def _mount_detail_for_path(
    path: Path,
    mountinfo_text: str,
) -> tuple[Path, str, str | None] | None:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    best: tuple[int, Path, str, str | None] | None = None
    for mount_point, fs_type, source in _mount_detail_entries(mountinfo_text):
        try:
            absolute.relative_to(mount_point)
        except ValueError:
            continue
        depth = len(mount_point.parts)
        # À profondeur égale, la dernière entrée gagne : cela reflète un
        # overmount plus récent sur exactement le même point de montage.
        if best is None or depth >= best[0]:
            best = (depth, mount_point, fs_type, source)
    return (best[1], best[2], best[3]) if best is not None else None


def _mount_for_path(path: Path, mountinfo_text: str) -> tuple[Path, str] | None:
    match = _mount_detail_for_path(path, mountinfo_text)
    return (match[0], match[1]) if match is not None else None

def _filesystem_type_for_path(path: Path, mountinfo_text: str) -> str | None:
    match = _mount_for_path(path, mountinfo_text)
    return match[1] if match is not None else None


def is_remote_filesystem_type(fs_type: str | None) -> bool:
    """Indique si un type de filesystem correspond à un stockage distant."""

    if not fs_type:
        return False
    normalized = fs_type.strip().lower()
    return normalized in _REMOTE_FS_TYPES or normalized.startswith("fuse.rclone")


def remote_folder_info(
    folder: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> RemoteFolderInfo:
    try:
        text = mountinfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return RemoteFolderInfo(False, None, None)
    match = _mount_detail_for_path(folder, text)
    if match is None:
        return RemoteFolderInfo(False, None, None)
    mount_point, fs_type, source = match
    # Le client pCloud natif publie parfois un type générique "fuse" avec
    # "pCloud.fs" comme source : le type seul ne permet pas de le reconnaître.
    native_pcloud = fs_type == "fuse" and (source or "").casefold() == "pcloud.fs"
    return RemoteFolderInfo(
        is_remote_filesystem_type(fs_type) or native_pcloud,
        fs_type,
        mount_point,
        source,
    )


def is_remote_folder(
    folder: Path,
    *,
    mountinfo_path: Path = Path("/proc/self/mountinfo"),
) -> bool:
    return remote_folder_info(folder, mountinfo_path=mountinfo_path).is_remote


def _rclone_remote_spec(folder: Path, info: RemoteFolderInfo) -> str | None:
    """Convertit un chemin du mount FUSE en ``remote:path`` rclone."""

    if (
        info.mount_point is None
        or info.source is None
        or not (info.filesystem_type or "").lower().startswith("fuse.rclone")
        or ":" not in info.source
    ):
        return None

    absolute = Path(os.path.abspath(os.path.expanduser(str(folder))))
    try:
        relative = absolute.relative_to(info.mount_point)
    except ValueError:
        return None

    if relative == Path("."):
        return info.source

    suffix = relative.as_posix()
    if info.source.endswith(":") or info.source.endswith("/"):
        return f"{info.source}{suffix}"
    return f"{info.source}/{suffix}"


def _has_reinclude_pattern(patterns: list[str]) -> bool:
    """Détecte une négation gitignore qui pourrait réinclure un chemin."""

    return any(
        line.startswith("!")
        for line in patterns
        if line and not line.startswith("#")
    )


def _rclone_default_excludes(
    root: Path,
    *,
    extra_patterns: list[str] | None,
    use_default_excludes: bool,
    use_ignore_file: bool,
) -> tuple[str, ...]:
    """Retourne les exclusions par défaut sûres à pousser dans rclone.

    Les règles utilisateur restent évaluées par ``pathspec`` côté Python car
    la syntaxe gitignore et la syntaxe de filtres rclone ne sont pas
    strictement identiques. Si une règle de négation est présente, aucun
    pushdown n'est effectué : elle pourrait réinclure un fichier situé dans
    un répertoire exclu par défaut.
    """

    if not use_default_excludes:
        return ()

    later_patterns: list[str] = []
    if use_ignore_file:
        try:
            later_patterns.extend(
                (root / IGNORE_FILENAME).read_text(
                    encoding="utf-8"
                ).splitlines()
            )
        except OSError:
            pass
    if extra_patterns:
        later_patterns.extend(extra_patterns)

    if _has_reinclude_pattern(later_patterns):
        return ()

    # Toutes les exclusions par défaut actuelles sont des répertoires. La
    # forme ``dir/**`` est comprise par rclone à n'importe quelle profondeur
    # et permet à son moteur de filtrage d'éviter la descente quand le backend
    # et la stratégie de listing le permettent.
    return tuple(
        f"{pattern}**"
        for pattern in DEFAULT_EXCLUDES
        if pattern.endswith("/") and not pattern.startswith("!")
    )


class _InvalidRcloneListing(ValueError):
    """Le listing distant ne peut pas être utilisé comme source de métadonnées."""


def _iter_json_array(stream, cancel_callback: Callable[[], bool] | None) -> Iterator[dict[str, object]]:
    """Décode un tableau JSON sans retenir les objets déjà consommés."""

    decoder = json.JSONDecoder()
    buffer = ""
    offset = 0
    finished = False

    def more() -> bool:
        nonlocal buffer, offset
        if cancel_callback is not None and cancel_callback():
            raise CancelledError()
        if offset:
            buffer = buffer[offset:]
            offset = 0
        chunk = stream.read(64 * 1024)
        buffer += chunk
        return bool(chunk)

    def token() -> str | None:
        nonlocal offset
        while True:
            while offset < len(buffer) and buffer[offset].isspace():
                offset += 1
            if offset < len(buffer):
                return buffer[offset]
            if not more():
                return None

    if token() != "[":
        raise _InvalidRcloneListing("Réponse rclone : tableau JSON attendu")
    offset += 1
    while True:
        first = token()
        if first == "]":
            offset += 1
            if token() is not None:
                raise _InvalidRcloneListing("Contenu après le tableau rclone")
            return
        if first is None:
            raise _InvalidRcloneListing("Tableau rclone incomplet")
        while True:
            if cancel_callback is not None and cancel_callback():
                raise CancelledError()
            try:
                item, end = decoder.raw_decode(buffer, offset)
                offset = end
                break
            except json.JSONDecodeError as exc:
                if not more():
                    raise _InvalidRcloneListing("Objet JSON rclone invalide") from exc
        if isinstance(item, dict):
            yield item
        separator = token()
        if separator == "]":
            offset += 1
            if token() is not None:
                raise _InvalidRcloneListing("Contenu après le tableau rclone")
            return
        if separator != ",":
            raise _InvalidRcloneListing("Séparateur JSON rclone invalide")
        offset += 1
        if token() in {None, "]"}:
            raise _InvalidRcloneListing("Élément JSON rclone manquant")


def _run_rclone_lsjson(
    remote_spec: str,
    cancel_callback: Callable[[], bool] | None = None,
    *,
    rclone_excludes: tuple[str, ...] = (),
    files_only: bool = True,
) -> Iterator[dict[str, object]] | None:
    """Stocke le listing hors mémoire, puis décode les objets un par un."""

    executable = shutil.which("rclone")
    if executable is None:
        return None

    command = [
        executable,
        "lsjson",
        remote_spec,
        "--recursive",
        "--no-mimetype",
    ]
    if files_only:
        command.append("--files-only")
    for pattern in rclone_excludes:
        command.extend(("--exclude", pattern))
    listing = tempfile.TemporaryFile(mode="w+b")
    errors = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=listing,
            stderr=errors,
        )
    except OSError as exc:
        listing.close()
        errors.close()
        logger.warning("Impossible de lancer rclone pour %s : %s", remote_spec, exc)
        return None

    try:
        while True:
            if cancel_callback is not None and cancel_callback():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise CancelledError()
            try:
                process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        listing.close()
        errors.close()
        raise

    if process.returncode != 0:
        errors.seek(0)
        logger.warning(
            "Listing rclone indisponible pour %s (code %s) : %s",
            remote_spec,
            process.returncode,
            errors.read(4096).decode("utf-8", errors="replace").strip(),
        )
        listing.close()
        errors.close()
        return None
    errors.close()

    def objects() -> Iterator[dict[str, object]]:
        try:
            listing.seek(0)
            from io import TextIOWrapper
            with TextIOWrapper(listing, encoding="utf-8", errors="replace") as stream:
                yield from _iter_json_array(stream, cancel_callback)
        finally:
            listing.close()

    return objects()


def _safe_relative_rclone_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return Path(*pure.parts)


def _rclone_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Le scanner historique utilise des datetimes locales naïves provenant
    # de stat(). Reproduire cette sémantique pour le planner par date.
    return datetime.fromtimestamp(parsed.timestamp())


def scan_rclone_metadata(
    folder: Path,
    *,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ScanResult | None:
    """Fast path métadonnées pour un dossier monté par ``rclone mount``.

    Le listing rclone récupère chemin, taille et date côté backend en lots,
    évitant le ``stat()`` FUSE individuel qui est extrêmement coûteux sur
    certains clouds (notamment pCloud).
    """

    info = remote_folder_info(folder)
    remote_spec = _rclone_remote_spec(folder, info)
    if remote_spec is None:
        return None

    root = Path(os.path.abspath(os.path.expanduser(str(folder))))
    rclone_excludes = _rclone_default_excludes(
        root,
        extra_patterns=exclude_patterns,
        use_default_excludes=use_default_excludes,
        use_ignore_file=use_ignore_file,
    )

    if progress_callback is not None:
        progress_callback("remote_listing")

    objects = _run_rclone_lsjson(
        remote_spec,
        cancel_callback,
        rclone_excludes=rclone_excludes,
    )
    if objects is None:
        return None

    ignore_spec = build_ignore_spec(
        root,
        exclude_patterns,
        use_defaults=use_default_excludes,
        use_ignore_file=use_ignore_file,
    )
    result = ScanResult(
        root=root,
        metadata_complete=True,
        identities_complete=False,
    )
    listed_objects = 0

    try:
        for item in objects:
            listed_objects += 1
            if cancel_callback is not None and cancel_callback():
                raise CancelledError()

            relative = _safe_relative_rclone_path(item.get("Path"))
            if relative is None or is_excluded(ignore_spec, relative, is_dir=False):
                continue

            try:
                size = int(item.get("Size", 0))
            except (TypeError, ValueError):
                return None
            if size < 0:
                return None

            mtime = _rclone_datetime(item.get("ModTime"))
            if mtime is None:
                # Un classement par date ne doit jamais inventer une date.
                return None

            path = root / relative
            result.files.append(
                FileRecord(
                    path=path,
                    size=size,
                    mtime=mtime,
                    extension=path.suffix.lower(),
                    identity=None,
                )
            )
            if progress_callback is not None and len(result.files) % 250 == 0:
                progress_callback(("remote_listing", len(result.files)))
    except _InvalidRcloneListing as exc:
        logger.warning("Réponse rclone JSON invalide pour %s : %s", remote_spec, exc)
        return None
    finally:
        close = getattr(objects, "close", None)
        if close is not None:
            close()

    if listed_objects == 0:
        # Un listing rclone vide peut être temporaire sur un montage cloud.
        # Signaler un fast path non concluant pour que scan_folder vérifie le
        # contenu visible via le montage avant d'afficher un résultat vide.
        logger.info("Listing rclone vide pour %s ; repli vers le scan du montage", remote_spec)
        return None

    return result


def scan_rclone_empty_directories(
    folder: Path,
    *,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> EmptyDirectoryScan | None:
    """Repère les dossiers vides d'un montage rclone avec le listing distant.

    ``scandir`` sur certains montages FUSE peut renvoyer temporairement une
    liste incomplète. Pour une opération de suppression, une entrée absente ne
    doit jamais être interprétée comme un dossier vide : le listing du backend
    rclone fait foi. Si ce listing est indisponible ou incomplet, on retourne
    zéro candidat avec une erreur explicite (et surtout aucun repli FUSE).
    """

    source_arg = Path(folder).expanduser()
    if source_arg.is_symlink():
        raise ValueError(
            f"le dossier source ne peut pas être un lien symbolique : {source_arg}"
        )
    root = source_arg.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"le chemin source n’est pas un dossier : {root}")

    info = remote_folder_info(root)
    if not (info.filesystem_type or "").lower().startswith("fuse.rclone"):
        return None

    remote_spec = _rclone_remote_spec(root, info)
    if remote_spec is None:
        return EmptyDirectoryScan(
            root,
            (),
            (f"{root}: impossible d'identifier la cible rclone ; aucun dossier proposé.",),
        )

    if progress_callback is not None:
        progress_callback("remote_listing")
    objects = _run_rclone_lsjson(
        remote_spec,
        cancel_callback,
        files_only=False,
    )
    if objects is None:
        return EmptyDirectoryScan(
            root,
            (),
            (f"{root}: listing rclone indisponible ; aucun dossier proposé à la suppression.",),
        )

    ignore_spec = build_ignore_spec(
        root,
        exclude_patterns,
        use_defaults=use_default_excludes,
        use_ignore_file=use_ignore_file,
    )
    directories: set[Path] = {Path(".")}
    blocked: set[Path] = set()
    listed = 0

    def add_directory_and_parents(relative: Path) -> None:
        current = relative
        while current != Path("."):
            directories.add(current)
            current = current.parent

    try:
        for item in objects:
            listed += 1
            if cancel_callback is not None and cancel_callback():
                raise CancelledError()
            relative = _safe_relative_rclone_path(item.get("Path"))
            is_dir = item.get("IsDir")
            if relative is None or not isinstance(is_dir, bool):
                return EmptyDirectoryScan(
                    root,
                    (),
                    (f"{root}: réponse rclone incomplète ; aucun dossier proposé.",),
                )

            if is_excluded(ignore_spec, relative, is_dir=is_dir):
                parent = relative if is_dir else relative.parent
                if parent != Path("."):
                    blocked.add(parent.parent if is_dir else parent)
                else:
                    blocked.add(Path("."))
                continue

            if is_dir:
                add_directory_and_parents(relative)
            else:
                add_directory_and_parents(relative.parent)
                blocked.add(relative.parent)

            if progress_callback is not None and listed % 250 == 0:
                progress_callback(("empty_directories", listed))
    except _InvalidRcloneListing:
        return EmptyDirectoryScan(
            root,
            (),
            (f"{root}: réponse JSON rclone invalide ; aucun dossier proposé.",),
        )
    finally:
        close = getattr(objects, "close", None)
        if close is not None:
            close()

    children_by_parent: dict[Path, list[Path]] = {}
    for relative in directories:
        if relative == Path("."):
            continue
        children_by_parent.setdefault(relative.parent, []).append(relative)

    candidates: set[Path] = set()
    records: list[EmptyDirectoryRecord] = []
    ordered = sorted(
        (path for path in directories if path != Path(".")),
        key=lambda path: (-len(path.parts), path.as_posix().casefold(), path.as_posix()),
    )
    errors: list[str] = []
    for relative in ordered:
        children = tuple(sorted(
            children_by_parent.get(relative, ()),
            key=lambda path: (path.as_posix().casefold(), path.as_posix()),
        ))
        if relative in blocked or any(child not in candidates for child in children):
            continue

        path = root / relative
        expected_children = {child.name for child in children}
        try:
            visible_children = list(os.scandir(path))
            visible_names = {entry.name for entry in visible_children}
            visible_directories = {
                entry.name
                for entry in visible_children
                if entry.is_dir(follow_symlinks=False)
            }
        except OSError as exc:
            errors.append(f"{path}: impossible de vérifier le contenu via le montage : {exc}")
            continue
        if (
            visible_names != expected_children
            or visible_directories != expected_children
        ):
            errors.append(
                f"{path}: le contenu visible du montage diffère du listing rclone; "
                "dossier conservé"
            )
            continue

        try:
            current_stat = path.stat(follow_symlinks=False)
            if not stat.S_ISDIR(current_stat.st_mode):
                errors.append(f"{path}: l'entrée n'est plus un dossier réel")
                continue
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue

        candidates.add(relative)
        records.append(
            EmptyDirectoryRecord(
                path=path,
                identity=identity_from_stat(current_stat),
                child_directories=tuple(root / child for child in children),
            )
        )
        if progress_callback is not None:
            progress_callback(("empty_directories", len(records)))

    return EmptyDirectoryScan(root, tuple(records), tuple(errors))
