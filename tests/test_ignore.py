"""Tests des exclusions de scan : patterns CLI, .janitorignore, defaults."""

from __future__ import annotations

from pathlib import Path

from file_janitor.scanner.scan import scan_directory


def _make_tree(root: Path) -> None:
    (root / "keep.txt").write_text("a")
    (root / "ignore.log").write_text("b")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.js").write_text("c")
    (root / "build").mkdir()
    (root / "build" / "out.bin").write_text("d")
    (root / "keep.log").write_text("e")  # sera ré-inclus via négation


def test_exclude_patterns_from_cli(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        exclude_patterns=["*.log", "node_modules/"],
    )

    names = {f.path.name for f in result.files}
    assert "keep.txt" in names
    assert "ignore.log" not in names
    assert "pkg.js" not in names  # tout node_modules/ élagué
    assert "out.bin" not in names  # build/ fait partie des exclusions techniques par défaut


def test_janitorignore_file(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".janitorignore").write_text("*.log\nbuild/\n!keep.log\n")

    result = scan_directory(tmp_path, compute_hashes=False)

    names = {f.path.name for f in result.files}
    assert "keep.txt" in names
    assert "ignore.log" not in names
    assert "out.bin" not in names  # build/ exclu par .janitorignore
    assert "keep.log" in names  # ré-inclus par la négation !keep.log
    # Le fichier .janitorignore lui-même n'apparaît pas dans les résultats
    # de scan car ce n'est pas exclu explicitement, il est bien listé :
    assert ".janitorignore" in names


def test_no_ignore_file_flag_disables_janitorignore(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / ".janitorignore").write_text("*.log\n")

    result = scan_directory(tmp_path, compute_hashes=False, use_ignore_file=False)

    names = {f.path.name for f in result.files}
    assert "ignore.log" in names  # plus exclu, le fichier ignore est désactivé


def test_default_excludes_state_dir(tmp_path: Path, monkeypatch) -> None:
    import file_janitor.scanner.ignore as ignore_module

    state_dir = tmp_path / ".file_janitor"
    state_dir.mkdir()
    (state_dir / "history.db").write_text("fake")
    (tmp_path / "real_file.txt").write_text("x")

    monkeypatch.setattr(ignore_module, "DEFAULT_STATE_DIR", state_dir)
    monkeypatch.setattr(ignore_module, "DEFAULT_EXCLUDES", [f"{state_dir.name}/"])

    result = scan_directory(tmp_path, compute_hashes=False)

    names = {f.path.name for f in result.files}
    assert "real_file.txt" in names
    assert "history.db" not in names


def test_no_default_excludes_flag(tmp_path: Path, monkeypatch) -> None:
    import file_janitor.scanner.ignore as ignore_module

    state_dir = tmp_path / ".file_janitor"
    state_dir.mkdir()
    (state_dir / "history.db").write_text("fake")

    monkeypatch.setattr(ignore_module, "DEFAULT_EXCLUDES", [f"{state_dir.name}/"])

    result = scan_directory(tmp_path, compute_hashes=False, use_default_excludes=False)

    names = {f.path.name for f in result.files}
    assert "history.db" in names


def test_default_excludes_technical_trash_and_thumbnails(tmp_path: Path) -> None:
    dtrash = tmp_path / ".dtrash"
    thumbnails = tmp_path / "album" / ".thumbnails"
    comments = tmp_path / ".comments"

    dtrash.mkdir()
    thumbnails.mkdir(parents=True)
    comments.mkdir()

    (dtrash / "deleted.jpg").write_text("deleted")
    (thumbnails / "preview.jpg").write_text("preview")
    (comments / "metadata.txt").write_text("keep")

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )

    relative_paths = {
        record.path.relative_to(tmp_path).as_posix()
        for record in result.files
    }

    assert ".dtrash/deleted.jpg" not in relative_paths
    assert "album/.thumbnails/preview.jpg" not in relative_paths
    assert ".comments/metadata.txt" in relative_paths


def test_no_default_excludes_restores_technical_directories(tmp_path: Path) -> None:
    dtrash = tmp_path / ".dtrash"
    thumbnails = tmp_path / "album" / ".thumbnails"

    dtrash.mkdir()
    thumbnails.mkdir(parents=True)

    (dtrash / "deleted.jpg").write_text("deleted")
    (thumbnails / "preview.jpg").write_text("preview")

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        use_default_excludes=False,
    )

    relative_paths = {
        record.path.relative_to(tmp_path).as_posix()
        for record in result.files
    }

    assert ".dtrash/deleted.jpg" in relative_paths
    assert "album/.thumbnails/preview.jpg" in relative_paths



def test_default_excludes_python_technical_directories(tmp_path: Path) -> None:
    technical_paths = (
        ".git/objects/object",
        ".venv/lib/site-packages/package.py",
        "venv/lib/site-packages/package.py",
        "__pycache__/module.pyc",
        ".pytest_cache/v/cache/nodeids",
        ".mypy_cache/3.12/module.meta.json",
        ".ruff_cache/cache.bin",
        "project/.idea/workspace.xml",
        "project/.vscode/settings.json",
        "solution/.vs/Project/v17/.suo",
        "build/lib/module.py",
        "dist/package.whl",
        "package.egg-info/PKG-INFO",
    )
    for relative in technical_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("technical")

    real = tmp_path / "src" / "module.py"
    real.parent.mkdir()
    real.write_text("keep")

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
    )

    relative_paths = {
        record.path.relative_to(tmp_path).as_posix()
        for record in result.files
    }
    assert relative_paths == {"src/module.py"}


def test_no_default_excludes_restores_python_technical_directories(
    tmp_path: Path,
) -> None:
    technical = tmp_path / ".venv" / "lib" / "package.py"
    technical.parent.mkdir(parents=True)
    technical.write_text("dependency")

    egg_info = tmp_path / "package.egg-info" / "PKG-INFO"
    egg_info.parent.mkdir()
    egg_info.write_text("metadata")

    idea = tmp_path / ".idea" / "workspace.xml"
    idea.parent.mkdir()
    idea.write_text("ide metadata")

    result = scan_directory(
        tmp_path,
        compute_hashes=False,
        compute_content_type=False,
        use_default_excludes=False,
    )

    relative_paths = {
        record.path.relative_to(tmp_path).as_posix()
        for record in result.files
    }
    assert ".venv/lib/package.py" in relative_paths
    assert "package.egg-info/PKG-INFO" in relative_paths
    assert ".idea/workspace.xml" in relative_paths
