"""Persistance légère des préférences de l'interface graphique."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings


class GuiPreferences:
    """Façade typée autour de ``QSettings`` pour les préférences GUI."""

    _GEOMETRY = "window/geometry"
    _LAST_FOLDER = "analysis/last_folder"
    _LAST_DESTINATION = "analysis/last_destination"
    _CLASSIFICATION_MODE = "analysis/classification_mode"
    _SECONDARY_CLASSIFICATION_MODE = "analysis/secondary_classification_mode"
    _ANALYSIS_PROFILE = "analysis/profile"
    _ACTIVE_TAB = "navigation/active_tab"
    _RESTORE_FOLDER = "restore/folder"
    _RESTORE_DESTINATION = "restore/destination"
    _RESTORE_CLASSIFICATION_MODE = "restore/classification_mode"
    _RESTORE_ANALYSIS_PROFILE = "restore/analysis_profile"
    _RESTORE_ACTIVE_TAB = "restore/active_tab"
    _ALLOW_UNSAFE_FAST_MOVE = "execution/allow_unsafe_fast_move"
    _RESET_PENDING = "restore/reset_pending"

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings if settings is not None else QSettings()

    @staticmethod
    def _as_bool(value: object, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    def geometry(self) -> QByteArray | None:
        value = self._settings.value(self._GEOMETRY)
        if isinstance(value, QByteArray) and not value.isEmpty():
            return value
        if isinstance(value, bytes) and value:
            return QByteArray(value)
        return None

    def last_folder(self) -> str:
        value = self._settings.value(self._LAST_FOLDER, "")
        return value if isinstance(value, str) else ""

    def last_destination(self) -> str:
        value = self._settings.value(self._LAST_DESTINATION, "")
        return value if isinstance(value, str) else ""

    def classification_mode(self) -> str:
        value = self._settings.value(self._CLASSIFICATION_MODE, "")
        return value if isinstance(value, str) else ""

    def secondary_classification_mode(self) -> str:
        value = self._settings.value(self._SECONDARY_CLASSIFICATION_MODE, "")
        return value if isinstance(value, str) else ""

    def analysis_profile(self) -> str:
        value = self._settings.value(self._ANALYSIS_PROFILE, "")
        return value if value in {"standard", "remote"} else ""

    def active_tab(self) -> str:
        return "analysis"

    def restore_folder(self) -> bool:
        return self._as_bool(self._settings.value(self._RESTORE_FOLDER, True))

    def restore_destination(self) -> bool:
        return self._as_bool(self._settings.value(self._RESTORE_DESTINATION, True))

    def restore_classification_mode(self) -> bool:
        return self._as_bool(
            self._settings.value(self._RESTORE_CLASSIFICATION_MODE, True)
        )

    def restore_analysis_profile(self) -> bool:
        return self._as_bool(
            self._settings.value(self._RESTORE_ANALYSIS_PROFILE, True)
        )

    def restore_active_tab(self) -> bool:
        return False

    def fast_remote_move_enabled(self) -> bool:
        """Indique si le MOVE rapide non atomique a été explicitement activé."""

        return self._as_bool(
            self._settings.value(self._ALLOW_UNSAFE_FAST_MOVE, False),
            False,
        )

    def set_fast_remote_move_enabled(self, enabled: bool) -> None:
        """Persiste l'opt-in au MOVE rapide ; la valeur par défaut reste sûre."""

        self._settings.setValue(self._ALLOW_UNSAFE_FAST_MOVE, bool(enabled))
        self._settings.sync()

    def save_restore_options(
        self,
        *,
        folder: bool,
        destination: bool,
        classification_mode: bool,
        analysis_profile: bool = True,
        active_tab: bool,
    ) -> None:
        """Enregistre les choix et oublie immédiatement les valeurs désactivées."""

        self._settings.setValue(self._RESTORE_FOLDER, folder)
        self._settings.setValue(self._RESTORE_DESTINATION, destination)
        self._settings.setValue(self._RESTORE_CLASSIFICATION_MODE, classification_mode)
        self._settings.setValue(self._RESTORE_ANALYSIS_PROFILE, analysis_profile)
        self._settings.remove(self._RESTORE_ACTIVE_TAB)
        self._settings.remove(self._ACTIVE_TAB)

        if not folder:
            self._settings.remove(self._LAST_FOLDER)
        if not destination:
            self._settings.remove(self._LAST_DESTINATION)
        if not classification_mode:
            self._settings.remove(self._CLASSIFICATION_MODE)
            self._settings.remove(self._SECONDARY_CLASSIFICATION_MODE)
        if not analysis_profile:
            self._settings.remove(self._ANALYSIS_PROFILE)

        self._settings.sync()

    def forget_destination(self) -> None:
        """Oublie immédiatement la destination externe mémorisée."""

        self._settings.remove(self._LAST_DESTINATION)
        self._settings.sync()

    def reset(self) -> None:
        """Efface l'état GUI mémorisé sans toucher aux données File Janitor."""
        self._settings.clear()
        # Empêche la fermeture de la session courante de recréer aussitôt l'état.
        self._settings.setValue(self._RESET_PENDING, True)
        self._settings.sync()

    def consume_reset_pending(self) -> bool:
        pending = self._as_bool(self._settings.value(self._RESET_PENDING, False), False)
        if pending:
            self._settings.remove(self._RESET_PENDING)
            self._settings.sync()
        return pending

    def save(
        self,
        *,
        geometry: QByteArray,
        last_folder: str,
        last_destination: str,
        classification_mode: str,
        secondary_classification_mode: str = "",
        analysis_profile: str = "",
        active_tab: str,
    ) -> None:
        """Sauvegarde l'état courant sans recréer les valeurs désactivées."""

        self._settings.setValue(self._GEOMETRY, geometry)

        if self.restore_folder():
            self._settings.setValue(self._LAST_FOLDER, last_folder)
        else:
            self._settings.remove(self._LAST_FOLDER)

        if self.restore_destination():
            if last_destination:
                self._settings.setValue(self._LAST_DESTINATION, last_destination)
            else:
                self._settings.remove(self._LAST_DESTINATION)
        else:
            self._settings.remove(self._LAST_DESTINATION)

        if self.restore_classification_mode():
            self._settings.setValue(self._CLASSIFICATION_MODE, classification_mode)
            self._settings.setValue(self._SECONDARY_CLASSIFICATION_MODE, secondary_classification_mode)
        else:
            self._settings.remove(self._CLASSIFICATION_MODE)
            self._settings.remove(self._SECONDARY_CLASSIFICATION_MODE)

        if self.restore_analysis_profile():
            if analysis_profile in {"standard", "remote"}:
                self._settings.setValue(self._ANALYSIS_PROFILE, analysis_profile)
            else:
                self._settings.remove(self._ANALYSIS_PROFILE)
        else:
            self._settings.remove(self._ANALYSIS_PROFILE)

        # Les clés héritées ne doivent jamais rouvrir un autre onglet.
        self._settings.remove(self._ACTIVE_TAB)
        self._settings.remove(self._RESTORE_ACTIVE_TAB)

        self._settings.sync()
