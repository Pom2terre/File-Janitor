"""Tests unitaires de la détection de contenu réel (magic bytes)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from file_janitor.scanner.filetype import sniff_content_type


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_png_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    info = sniff_content_type(path)
    assert info.family == "image"
    assert "PNG" in info.label


def test_jpeg_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    info = sniff_content_type(path)
    assert info.family == "image"


def test_pdf_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"%PDF-1.7\n%...")
    info = sniff_content_type(path)
    assert info.family == "document"


def test_gzip_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"\x1f\x8b\x08\x00" + b"\x00" * 10)
    info = sniff_content_type(path)
    assert info.family == "archive"


def test_elf_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"\x7fELF" + b"\x00" * 20)
    info = sniff_content_type(path)
    assert info.family == "executable"


def test_sqlite_signature(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"SQLite format 3\x00" + b"\x00" * 20)
    info = sniff_content_type(path)
    assert info.family == "database"


def test_plain_text(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", "Bonjour, ceci est du texte.".encode("utf-8"))
    info = sniff_content_type(path)
    assert info.family == "text"


def test_unknown_binary(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", bytes(range(256)))
    info = sniff_content_type(path)
    assert info.family == "unknown"


def test_empty_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.bin", b"")
    info = sniff_content_type(path)
    assert info.family == "unknown"
    assert "vide" in info.label.lower()


def test_docx_detected_via_zip_inspection(tmp_path: Path) -> None:
    """Un .docx est un ZIP contenant des entrées spécifiques : on doit le
    reconnaître comme document Word, pas comme une simple archive."""
    path = tmp_path / "fake.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")

    info = sniff_content_type(path)
    assert info.family == "document"
    assert "Word" in info.label


def test_plain_zip_detected_as_archive(tmp_path: Path) -> None:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hello")

    info = sniff_content_type(path)
    assert info.family == "archive"
