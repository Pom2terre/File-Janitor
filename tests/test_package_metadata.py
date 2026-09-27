"""Tests de cohérence des métadonnées du package."""

from __future__ import annotations

from pathlib import Path
import tomllib

from file_janitor import __version__


def _project_metadata() -> tuple[Path, dict]:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        return root, tomllib.load(stream)["project"]


def test_package_version_matches_pyproject() -> None:
    _, project = _project_metadata()

    assert project["version"] == __version__ == "0.4.0"


def test_distribution_metadata_declares_readme_and_supported_python() -> None:
    """Les métadonnées publiques minimales doivent rester déclarées."""

    root, project = _project_metadata()

    assert project["readme"] == "README.md"
    assert (root / project["readme"]).is_file()
    assert project["requires-python"] == ">=3.10"

    classifiers = set(project["classifiers"])
    assert "Development Status :: 4 - Beta" in classifiers
    assert "Programming Language :: Python :: 3" in classifiers
    assert "Programming Language :: Python :: 3 :: Only" in classifiers


def test_distribution_declares_mit_license_with_spdx_metadata() -> None:
    """La licence MIT utilise les métadonnées SPDX modernes."""

    root, project = _project_metadata()

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert (root / "LICENSE").is_file()

    classifiers = set(project["classifiers"])
    assert "License :: OSI Approved :: MIT License" not in classifiers
