"""Tests des préférences persistantes de la GUI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings

from file_janitor.application import ClassificationMode
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.preferences import GuiPreferences


def _preferences(tmp_path: Path) -> GuiPreferences:
    settings = QSettings(
        str(tmp_path / "gui.ini"),
        QSettings.Format.IniFormat,
    )
    return GuiPreferences(settings)


def test_application_declares_stable_qsettings_identity() -> None:
    create_application(["janitor-gui-preferences-identity-test"])

    assert QCoreApplication.organizationName() == "Pom2terre"
    assert QCoreApplication.applicationName() == "File Janitor"


def test_gui_preferences_restore_folder_mode_tab_and_geometry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_application(["janitor-gui-preferences-roundtrip-test"])
    folder = tmp_path / "files"
    folder.mkdir()
    preferences = _preferences(tmp_path)

    first = MainWindow(preferences=preferences)
    first.show()
    app.processEvents()
    first.resize(1234, 777)
    first.folder_edit.setText(str(folder))
    first.classification_mode_combo.setCurrentIndex(1)
    first.secondary_mode_combo.setCurrentIndex(
        first.secondary_mode_combo.findData(ClassificationMode.SIZE)
    )
    first.tabs.setCurrentWidget(first.history_tab)

    first.close()

    stored_geometry = _preferences(tmp_path).geometry()
    assert stored_geometry is not None
    assert not stored_geometry.isEmpty()

    restored_geometries = []

    def restore_geometry(self, geometry):
        restored_geometries.append(geometry)
        return True

    monkeypatch.setattr(
        MainWindow,
        "restoreGeometry",
        restore_geometry,
    )

    restored = MainWindow(preferences=_preferences(tmp_path))

    assert restored_geometries == [stored_geometry]
    assert restored.folder_edit.text() == str(folder)
    assert restored.analyze_button.isEnabled() is True
    assert (
        restored._selected_classification_mode()
        is ClassificationMode.DATE
    )
    assert restored._selected_secondary_mode() is ClassificationMode.SIZE
    assert restored.tabs.currentWidget() is restored.analysis_tab

    restored.close()

def test_saved_analysis_profile_is_restored_as_manual_choice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from file_janitor.application import RemoteFolderInfo
    from file_janitor.gui import main_window as main_window_module

    folder = tmp_path / "remote"
    folder.mkdir()
    preferences = _preferences(tmp_path)

    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()

    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.EXTENSION.value,
        analysis_profile="standard",
        active_tab="analysis",
    )

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(
            is_remote=True,
            filesystem_type="fuse.rclone",
            mount_point=folder,
        ),
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window._selected_analysis_profile() == "standard"
    assert window._analysis_profile_user_override is True
    assert window._analysis_profile_source == folder.absolute()
    assert "restauré" in window.analysis_profile_hint.text()

    window.close()


def test_disabled_analysis_profile_restore_uses_auto_detection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from file_janitor.application import RemoteFolderInfo
    from file_janitor.gui import main_window as main_window_module

    folder = tmp_path / "remote"
    folder.mkdir()
    preferences = _preferences(tmp_path)

    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()

    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.EXTENSION.value,
        analysis_profile="standard",
        active_tab="analysis",
    )
    preferences.save_restore_options(
        folder=True,
        destination=True,
        classification_mode=True,
        analysis_profile=False,
        active_tab=True,
    )

    monkeypatch.setattr(
        main_window_module,
        "remote_folder_info",
        lambda path: RemoteFolderInfo(
            is_remote=True,
            filesystem_type="fuse.rclone",
            mount_point=folder,
        ),
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window._selected_analysis_profile() == "remote"
    assert window._analysis_profile_user_override is False

    window.close()

def test_missing_folder_is_not_restored_and_preview_falls_back_to_analysis(
    tmp_path: Path,
) -> None:
    preferences = _preferences(tmp_path)
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()
    preferences.save(
        geometry=geometry,
        last_folder=str(tmp_path / "missing"),
        last_destination="",
        classification_mode=ClassificationMode.SIZE.value,
        active_tab="preview",
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window.folder_edit.text() == ""
    assert window.analyze_button.isEnabled() is False
    assert window._selected_classification_mode() is ClassificationMode.SIZE
    assert window.tabs.currentWidget() is window.analysis_tab

    window.close()


def test_restore_options_can_disable_folder_mode_and_tab(tmp_path: Path) -> None:
    preferences = _preferences(tmp_path)
    folder = tmp_path / "files"
    folder.mkdir()
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()
    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.SIZE.value,
        active_tab="history",
    )
    preferences.save_restore_options(
        folder=False,
        destination=False,
        classification_mode=False,
        active_tab=False,
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window.folder_edit.text() == ""
    assert window._selected_classification_mode() is ClassificationMode.EXTENSION
    assert window.tabs.currentWidget() is window.analysis_tab
    window.close()


def test_reset_clears_saved_state_and_survives_current_window_close(tmp_path: Path) -> None:
    preferences = _preferences(tmp_path)
    folder = tmp_path / "files"
    folder.mkdir()
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()
    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.DATE.value,
        active_tab="history",
    )

    preferences.reset()
    window = MainWindow(preferences=preferences)
    window.close()

    restored = _preferences(tmp_path)
    assert restored.geometry() is None
    assert restored.last_folder() == ""
    assert restored.classification_mode() == ""
    assert restored.active_tab() == "analysis"


def test_disabling_restore_options_forgets_saved_values_immediately(tmp_path: Path) -> None:
    preferences = _preferences(tmp_path)
    folder = tmp_path / "files"
    folder.mkdir()
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()
    preferences.save(
        geometry=geometry,
        last_folder=str(folder),
        last_destination="",
        classification_mode=ClassificationMode.DATE.value,
        active_tab="history",
    )

    preferences.save_restore_options(
        folder=False,
        destination=False,
        classification_mode=False,
        active_tab=False,
    )

    restored = _preferences(tmp_path)
    assert restored.last_folder() == ""
    assert restored.classification_mode() == ""
    assert restored.active_tab() == "analysis"


def test_window_close_does_not_recreate_disabled_saved_values(tmp_path: Path) -> None:
    preferences = _preferences(tmp_path)
    folder = tmp_path / "files"
    folder.mkdir()
    preferences.save_restore_options(
        folder=False,
        destination=False,
        classification_mode=False,
        active_tab=False,
    )

    window = MainWindow(preferences=preferences)
    window.folder_edit.setText(str(folder))
    window.classification_mode_combo.setCurrentIndex(1)
    window.tabs.setCurrentWidget(window.history_tab)
    window.close()

    restored = _preferences(tmp_path)
    assert restored.last_folder() == ""
    assert restored.classification_mode() == ""
    assert restored.active_tab() == "analysis"


def test_gui_preferences_restore_destination(tmp_path: Path) -> None:
    app = create_application(["janitor-gui-preferences-destination-test"])
    assert app is not None

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    preferences = _preferences(tmp_path)
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()

    preferences.save(
        geometry=geometry,
        last_folder=str(source),
        last_destination=str(destination),
        classification_mode=ClassificationMode.EXTENSION.value,
        active_tab="analysis",
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window.folder_edit.text() == str(source)
    assert window.destination_edit.text() == str(destination)
    assert window._current_analysis_context() == (
    source.absolute(),
    destination.absolute(),
        ClassificationMode.EXTENSION,
        None,
        "standard",
)
    window.close()


def test_missing_destination_is_not_restored(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    preferences = _preferences(tmp_path)
    baseline = MainWindow()
    geometry = baseline.saveGeometry()
    baseline.close()

    preferences.save(
        geometry=geometry,
        last_folder=str(source),
        last_destination=str(tmp_path / "missing-destination"),
        classification_mode=ClassificationMode.EXTENSION.value,
        active_tab="analysis",
    )

    window = MainWindow(preferences=_preferences(tmp_path))

    assert window.folder_edit.text() == str(source)
    assert window.destination_edit.text() == ""
    assert window._current_analysis_context()[1] == source.absolute()
    window.close()


def test_use_source_forgets_saved_destination_immediately(tmp_path: Path) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    preferences = _preferences(tmp_path)
    preferences._settings.setValue("analysis/last_destination", str(destination))
    preferences._settings.sync()

    window = MainWindow(preferences=preferences)
    assert window.destination_edit.text() == str(destination)

    window._use_source_as_destination()

    assert window.destination_edit.text() == ""
    assert _preferences(tmp_path).last_destination() == ""
    window.close()


def test_disabling_destination_restore_forgets_value_immediately(tmp_path: Path) -> None:
    preferences = _preferences(tmp_path)
    preferences._settings.setValue("analysis/last_destination", "/tmp/destination")
    preferences._settings.sync()

    preferences.save_restore_options(
        folder=True,
        destination=False,
        classification_mode=True,
        active_tab=True,
    )

    restored = _preferences(tmp_path)
    assert restored.restore_destination() is False
    assert restored.last_destination() == ""
