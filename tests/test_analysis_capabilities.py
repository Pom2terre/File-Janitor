"""Tests 4E-8B : capacités réellement calculées dans AnalysisResult."""

from __future__ import annotations

from pathlib import Path

from file_janitor.application import (
    AnalysisCapabilities,
    analyze_folder,
    build_analysis_result,
    create_plan,
    scan_folder,
)


def test_build_analysis_result_defaults_to_full_capabilities(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")

    scan = scan_folder(
        source,
        compute_hashes=False,
        compute_content_type=False,
    )
    plan = create_plan(scan)
    result = build_analysis_result(scan, plan)

    assert result.capabilities == AnalysisCapabilities(
        hashes_computed=True,
        content_types_computed=True,
    )


def test_analyze_folder_reports_remote_like_capabilities(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")

    result = analyze_folder(
        source,
        compute_hashes=False,
        compute_content_type=False,
    )

    assert result.capabilities.hashes_computed is False
    assert result.capabilities.content_types_computed is False


def test_analyze_folder_preserves_independent_capability_flags(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("alpha", encoding="utf-8")

    hashes_only = analyze_folder(
        source,
        compute_hashes=True,
        compute_content_type=False,
    )
    content_only = analyze_folder(
        source,
        compute_hashes=False,
        compute_content_type=True,
    )

    assert hashes_only.capabilities == AnalysisCapabilities(
        hashes_computed=True,
        content_types_computed=False,
    )
    assert content_only.capabilities == AnalysisCapabilities(
        hashes_computed=False,
        content_types_computed=True,
    )
