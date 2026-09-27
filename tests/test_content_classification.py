"""Tests d'intégration : classement par contenu réel + extensions trompeuses."""

from __future__ import annotations

import zipfile
from pathlib import Path

from file_janitor.models import FileCategory
from file_janitor.plan.builder import build_plan
from file_janitor.scanner.scan import scan_directory


def test_extensionless_file_sorted_by_content(tmp_path: Path) -> None:
    """Un fichier sans extension mais dont le contenu est une image PNG doit
    être proposé au classement, dans un dossier « Images »."""
    (tmp_path / "IMG_1234").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(scan)

    to_sort = plan.items(FileCategory.TO_SORT)
    assert len(to_sort) == 1
    assert to_sort[0].destination == tmp_path / "Images" / "IMG_1234"


def test_mismatched_extension_detected_and_sorted_by_real_content(tmp_path: Path) -> None:
    """Un fichier .txt qui est en fait une image PNG doit être signalé comme
    extension trompeuse, et classé selon son vrai contenu (Images), pas Texte."""
    (tmp_path / "notes.txt").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(scan)

    mismatches = plan.items(FileCategory.EXTENSION_MISMATCH)
    assert len(mismatches) == 1
    assert mismatches[0].path.name == "notes.txt"

    to_sort = plan.items(FileCategory.TO_SORT)
    assert len(to_sort) == 1
    assert to_sort[0].destination == tmp_path / "Images" / "notes.txt"


def test_docx_disguised_as_zip_flagged_mismatch(tmp_path: Path) -> None:
    """Un .zip qui est en fait un .docx (contenu Office) doit être signalé,
    grâce à l'inspection du contenu interne du ZIP."""
    path = tmp_path / "rapport.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")

    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(scan)

    mismatches = plan.items(FileCategory.EXTENSION_MISMATCH)
    assert len(mismatches) == 1
    assert "Word" in mismatches[0].reason


def test_matching_extension_not_flagged(tmp_path: Path) -> None:
    """Un vrai PNG avec extension .png ne doit pas être signalé comme
    trompeur, même s'il est bien classé au tri."""
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(scan)

    assert plan.items(FileCategory.EXTENSION_MISMATCH) == []
    assert len(plan.items(FileCategory.TO_SORT)) == 1


def test_no_content_check_falls_back_to_extension(tmp_path: Path) -> None:
    """Avec compute_content_type=False, le classement retombe sur
    l'extension seule (comportement dégradé mais fonctionnel)."""
    (tmp_path / "photo.jpg").write_text("pas vraiment une image")

    scan = scan_directory(tmp_path, compute_hashes=False, compute_content_type=False)
    plan = build_plan(scan)

    to_sort = plan.items(FileCategory.TO_SORT)
    assert len(to_sort) == 1
    assert to_sort[0].destination == tmp_path / "Images" / "photo.jpg"
    # Sans détection de contenu, aucune extension trompeuse ne peut être vue.
    assert plan.items(FileCategory.EXTENSION_MISMATCH) == []


def test_archive_detected_by_content_despite_unknown_extension(tmp_path: Path) -> None:
    """Une archive gzip renommée avec une extension inconnue (.bak) et vieille
    de plus de 180 jours doit quand même être détectée comme archive ancienne."""
    import os
    import time

    path = tmp_path / "old_backup.bak"
    path.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 10)
    past = time.time() - 400 * 86400
    os.utime(path, (past, past))

    scan = scan_directory(tmp_path, compute_hashes=False)
    plan = build_plan(scan)

    old_archives = plan.items(FileCategory.OLD_ARCHIVE)
    assert len(old_archives) == 1
    assert old_archives[0].path.name == "old_backup.bak"
