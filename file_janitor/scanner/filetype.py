"""Détection du type réel d'un fichier par son contenu (magic bytes), plutôt
que par sa seule extension.

Une extension ment facilement (renommage, erreur, malveillance) ; les
premiers octets d'un fichier (sa « signature ») sont beaucoup plus fiables.
On regroupe les types détectés en « familles » (image, vidéo, document, ...)
utilisées à la fois pour :
  - classer les fichiers dans des dossiers pertinents (plan/builder.py),
  - détecter les fichiers dont l'extension ne correspond pas au contenu réel.

Volontairement sans dépendance externe (pas de libmagic) : on reste portable
et cela reste un bon exercice de lecture binaire avec `pathlib`/`open("rb")`.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

_HEADER_SIZE = 4096
_TAR_OFFSET = 257


@dataclass(frozen=True, slots=True)
class ContentInfo:
    """Résultat de la détection : famille (pour le classement) + libellé humain."""

    family: str
    label: str


UNKNOWN = ContentInfo("unknown", "Binaire inconnu")
EMPTY = ContentInfo("unknown", "Fichier vide")
READ_ERROR = ContentInfo("unknown", "Illisible")

# Familles utilisées pour le classement (plan/builder.py) et les libellés
# affichés à l'utilisateur.
FAMILY_LABELS = {
    "image": "Images",
    "video": "Vidéos",
    "audio": "Audio",
    "document": "Documents",
    "archive": "Archives",
    "executable": "Exécutables",
    "database": "Bases de données",
    "text": "Texte",
}

# Extensions couramment associées à chaque famille : sert de repli quand le
# contenu est vide/illisible, et de référence pour détecter les extensions
# trompeuses (extension déclarée vs contenu détecté).
EXTENSION_FAMILY: dict[str, str] = {}
_EXTENSIONS_BY_FAMILY = {
    "image": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "ico", "tif", "tiff"],
    "video": ["mp4", "mov", "mkv", "avi", "webm", "flv", "wmv"],
    "audio": ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma"],
    # Formats binaires réels : leur contenu n'est jamais du texte brut.
    "document": ["pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ppt", "pptx"],
    # Formats lisibles tels quels : leur contenu détecté est "text" (voir
    # _looks_like_text), pas "document" — ils doivent donc rester ici pour
    # éviter de faux positifs d'"extension trompeuse" sur un simple .txt.
    "text": ["txt", "md", "rst", "csv", "log", "ini", "cfg"],
    "archive": ["zip", "tar", "gz", "rar", "7z", "bz2", "xz", "tgz", "jar", "apk"],
    "executable": ["exe", "msi", "bin", "elf", "app", "deb", "rpm"],
    "database": ["db", "sqlite", "sqlite3"],
}
for _family, _exts in _EXTENSIONS_BY_FAMILY.items():
    for _ext in _exts:
        EXTENSION_FAMILY[f".{_ext}"] = _family


def _inspect_zip(path: Path) -> ContentInfo:
    """Un ZIP peut être un simple ZIP, mais aussi un .docx/.xlsx/.pptx (Office
    Open XML), un .jar (Java) ou un .apk (Android) : tous partagent la même
    signature PK, seule la liste des fichiers internes permet de trancher.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return ContentInfo("archive", "Archive ZIP (corrompue ou illisible)")

    if "AndroidManifest.xml" in names:
        return ContentInfo("archive", "Application Android (APK)")
    if "META-INF/MANIFEST.MF" in names:
        return ContentInfo("archive", "Archive Java (JAR)")
    if "[Content_Types].xml" in names:
        if any(n.startswith("word/") for n in names):
            return ContentInfo("document", "Document Word (.docx)")
        if any(n.startswith("xl/") for n in names):
            return ContentInfo("document", "Feuille de calcul Excel (.xlsx)")
        if any(n.startswith("ppt/") for n in names):
            return ContentInfo("document", "Présentation PowerPoint (.pptx)")
        return ContentInfo("document", "Document Office Open XML")
    return ContentInfo("archive", "Archive ZIP")


def _looks_like_text(header: bytes) -> bool:
    if b"\x00" in header:
        return False
    try:
        header.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def sniff_content_type(path: Path) -> ContentInfo:
    """Détecte le type réel d'un fichier à partir de ses premiers octets.

    Ne lève jamais d'exception : retourne UNKNOWN/EMPTY/READ_ERROR en cas de
    fichier vide, illisible ou de type non reconnu.
    """
    try:
        with path.open("rb") as f:
            header = f.read(_HEADER_SIZE)
    except OSError:
        return READ_ERROR

    if not header:
        return EMPTY

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ContentInfo("image", "Image PNG")
    if header.startswith(b"\xff\xd8\xff"):
        return ContentInfo("image", "Image JPEG")
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ContentInfo("image", "Image GIF")
    if header.startswith(b"BM"):
        return ContentInfo("image", "Image BMP")
    if header.startswith(b"\x00\x00\x01\x00"):
        return ContentInfo("image", "Icône ICO")

    if header.startswith(b"RIFF") and len(header) >= 12:
        riff_type = header[8:12]
        if riff_type == b"WEBP":
            return ContentInfo("image", "Image WEBP")
        if riff_type == b"WAVE":
            return ContentInfo("audio", "Audio WAV")
        if riff_type == b"AVI ":
            return ContentInfo("video", "Vidéo AVI")

    if header.startswith(b"%PDF-"):
        return ContentInfo("document", "Document PDF")
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ContentInfo("document", "Document Office legacy (.doc/.xls/.ppt)")
    if header.startswith(b"{\\rtf1"):
        return ContentInfo("document", "Document RTF")

    if header.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _inspect_zip(path)
    if header.startswith(b"\x1f\x8b"):
        return ContentInfo("archive", "Archive GZIP")
    if header.startswith(b"Rar!\x1a\x07"):
        return ContentInfo("archive", "Archive RAR")
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ContentInfo("archive", "Archive 7-Zip")
    if len(header) >= _TAR_OFFSET + 5 and header[_TAR_OFFSET : _TAR_OFFSET + 5] == b"ustar":
        return ContentInfo("archive", "Archive TAR")

    if header.startswith(b"fLaC"):
        return ContentInfo("audio", "Audio FLAC")
    if header.startswith(b"ID3") or header.startswith(b"\xff\xfb"):
        return ContentInfo("audio", "Audio MP3")
    if header.startswith(b"OggS"):
        return ContentInfo("audio", "Audio/Vidéo OGG")
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return ContentInfo("video", "Conteneur MP4/MOV/M4A")
    if header.startswith(b"\x1aE\xdf\xa3"):
        return ContentInfo("video", "Vidéo Matroska (MKV/WEBM)")

    if header.startswith(b"MZ"):
        return ContentInfo("executable", "Exécutable Windows (PE)")
    if header.startswith(b"\x7fELF"):
        return ContentInfo("executable", "Exécutable Linux (ELF)")

    if header.startswith(b"SQLite format 3\x00"):
        return ContentInfo("database", "Base de données SQLite")

    if _looks_like_text(header):
        return ContentInfo("text", "Texte brut")

    return UNKNOWN
