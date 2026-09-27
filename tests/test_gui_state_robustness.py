"""Tests 3E-2 : robustesse des états et navigation pendant les workers."""

from __future__ import annotations

from pathlib import Path

from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow


def test_tabs_remain_navigable_while_busy() -> None:
    """Un worker ne doit pas empêcher la consultation des autres onglets."""
    app = create_application(["janitor-gui-busy-tabs-test"])
    window = MainWindow()

    window._set_busy(True)
    window._focus_tab(window.preview_tab)
    app.processEvents()
    assert window.tabs.currentWidget() is window.preview_tab

    window._focus_tab(window.history_tab)
    app.processEvents()
    assert window.tabs.currentWidget() is window.history_tab

    window._set_busy(False)


def test_busy_state_blocks_context_mutations_but_restores_business_state(
    tmp_path: Path,
) -> None:
    """La sortie du busy state recalcule les contrôles selon le contexte réel."""
    create_application(["janitor-gui-busy-restore-test"])
    window = MainWindow()

    window.folder_edit.setText(str(tmp_path))
    window._update_analyze_button()
    assert window.analyze_button.isEnabled() is True

    window._set_busy(True)

    assert window.folder_edit.isEnabled() is True
    assert window.folder_edit.isReadOnly() is True
    assert window.browse_button.isEnabled() is False
    assert window.destination_browse_button.isEnabled() is False
    assert window.destination_source_button.isEnabled() is False
    assert window.classification_mode_combo.isEnabled() is False
    assert window.analyze_button.isEnabled() is False
    assert window.preferences_button.isEnabled() is False

    window._set_busy(False)

    assert window.folder_edit.isEnabled() is True
    assert window.browse_button.isEnabled() is True
    assert window.destination_browse_button.isEnabled() is True
    assert window.destination_source_button.isEnabled() is True
    assert window.classification_mode_combo.isEnabled() is True
    assert window.preferences_button.isEnabled() is True
    assert window.analyze_button.isEnabled() is True
    # Sans résultat d'analyse, Exécuter ne doit pas être réactivé aveuglément.
    assert window.execute_button.isEnabled() is False


def test_repeated_analysis_trigger_is_ignored_while_worker_is_active(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Un second déclenchement ne doit pas remplacer un worker déjà actif."""
    create_application(["janitor-gui-double-analysis-test"])
    window = MainWindow()
    window.folder_edit.setText(str(tmp_path))
    window._update_analyze_button()

    marker = object()
    window._active_worker = marker

    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("une seconde analyse ne doit pas être lancée")

    import file_janitor.gui.main_window as main_window_module
    monkeypatch.setattr(main_window_module, "analyze_folder", fail_if_called)

    window._start_analysis()

    assert window._active_worker is marker
    assert called is False
