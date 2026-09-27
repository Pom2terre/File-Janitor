"""Tests du module de rapport (JSON et HTML)."""

from __future__ import annotations

from pathlib import Path

from file_janitor.models import FileCategory
from file_janitor.plan.builder import build_plan
from file_janitor.report import build_report_data, render_html_report, write_html_report
from file_janitor.scanner.scan import scan_directory


def _scan_and_plan(tmp_path: Path):
    scan = scan_directory(tmp_path, compute_hashes=True)
    return scan, build_plan(scan)


def test_build_report_data_structure(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    scan, plan = _scan_and_plan(tmp_path)

    data = build_report_data(scan, plan)

    assert data["root"] == str(scan.root)
    assert data["total_count"] == 1
    assert "generated_at" in data
    assert set(data["categories"]) == {c.value for c in FileCategory}
    assert data["categories"]["duplicate"]["label"] == "Doublons"
    assert data["categories"]["duplicate"]["count"] == 0


def test_html_report_escapes_dangerous_filenames(tmp_path: Path) -> None:
    dangerous = tmp_path / "weird<script>&.txt"
    dangerous.write_text("x")
    scan, plan = _scan_and_plan(tmp_path)

    html = render_html_report(scan, plan)

    assert "<script>" not in html
    assert "weird&lt;script&gt;&amp;.txt" in html


def test_html_report_contains_summary_and_counts(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("hello")  # doublon de a.txt
    scan, plan = _scan_and_plan(tmp_path)

    html = render_html_report(scan, plan)

    assert str(scan.root) in html
    assert "Doublons" in html
    assert "<!DOCTYPE html>" in html
    assert "Rien à signaler." in html  # au moins une catégorie vide


def test_html_report_truncates_long_lists(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"file{i}.txt").write_text(f"contenu {i}")  # tous vieux -> old_file
    import os
    import time

    past = time.time() - 400 * 86400
    for i in range(10):
        os.utime(tmp_path / f"file{i}.txt", (past, past))

    scan, plan = _scan_and_plan(tmp_path)
    html = render_html_report(scan, plan, max_items_per_category=3)

    assert "et 7 fichier(s) supplémentaire(s)" in html


def test_write_html_report_creates_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    scan, plan = _scan_and_plan(tmp_path)
    output = tmp_path / "out" / "report.html"

    write_html_report(scan, plan, output)

    assert output.exists()
    assert "<!DOCTYPE html>" in output.read_text(encoding="utf-8")
