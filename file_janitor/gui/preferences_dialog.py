"""Dialogue de contrôle des préférences persistantes de la GUI."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from file_janitor.gui.preferences import GuiPreferences


class PreferencesDialog(QDialog):
    """Expose les préférences de présentation et de MOVE explicitement sûres."""

    def __init__(self, preferences: GuiPreferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preferences = preferences
        self.was_reset = False
        self.setWindowTitle("Préférences")
        self.setModal(True)
        self.setMinimumWidth(430)

        intro = QLabel(
            "Choisissez les éléments d’interface à restaurer au prochain démarrage. "
            "Décocher une option oublie immédiatement la valeur enregistrée correspondante."
        )
        intro.setWordWrap(True)

        self.restore_folder_checkbox = QCheckBox("Restaurer le dernier dossier")
        self.restore_folder_checkbox.setChecked(preferences.restore_folder())
        self.restore_destination_checkbox = QCheckBox(
            "Restaurer la dernière destination"
        )
        self.restore_destination_checkbox.setChecked(
            preferences.restore_destination()
        )
        self.restore_mode_checkbox = QCheckBox("Restaurer le mode de classement")
        self.restore_mode_checkbox.setChecked(preferences.restore_classification_mode())
        self.restore_profile_checkbox = QCheckBox("Restaurer le profil d’analyse")
        self.restore_profile_checkbox.setChecked(
            preferences.restore_analysis_profile()
        )

        self.fast_move_checkbox = QCheckBox(
            "Autoriser le déplacement rapide sur rclone/FUSE"
        )
        self.fast_move_checkbox.setChecked(
            preferences.fast_remote_move_enabled()
        )
        self.fast_move_checkbox.setToolTip(
            "Évite la copie complète lorsque RENAME_NOREPLACE n'est pas "
            "disponible, mais ne protège pas atomiquement contre une cible "
            "créée au même instant par un autre programme."
        )

        fast_move_note = QLabel(
            "Désactivé par défaut. Ce mode accélère fortement les déplacements "
            "sur un même partage rclone, mais une écriture externe concurrente "
            "pourrait être écrasée."
        )
        fast_move_note.setWordWrap(True)
        fast_move_note.setObjectName("fieldHint")

        note = QLabel(
            "La géométrie de la fenêtre est restaurée automatiquement. "
            "La réinitialisation n’efface ni l’historique ni vos fichiers."
        )
        note.setWordWrap(True)
        note.setObjectName("fieldHint")

        self.reset_button = QPushButton("Réinitialiser les préférences…")
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.clicked.connect(self._reset_preferences)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(intro)
        layout.addWidget(self.restore_folder_checkbox)
        layout.addWidget(self.restore_destination_checkbox)
        layout.addWidget(self.restore_mode_checkbox)
        layout.addWidget(self.restore_profile_checkbox)
        layout.addSpacing(6)
        layout.addWidget(note)
        layout.addSpacing(6)
        layout.addWidget(self.fast_move_checkbox)
        layout.addWidget(fast_move_note)
        layout.addWidget(self.reset_button)
        layout.addSpacing(8)
        layout.addWidget(buttons)

    def _save(self) -> None:
        fast_move_enabled = self.fast_move_checkbox.isChecked()
        if (
            fast_move_enabled
            and not self._preferences.fast_remote_move_enabled()
        ):
            answer = QMessageBox.question(
                self,
                "Activer le déplacement rapide ?",
                "Ce mode utilise rename() lorsque rclone/FUSE refuse "
                "RENAME_NOREPLACE. Le déplacement devient presque instantané, "
                "mais une destination créée au même moment par un autre "
                "programme pourrait être écrasée.\n\n"
                "Activer ce mode malgré ce risque ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return

        self._preferences.save_restore_options(
            folder=self.restore_folder_checkbox.isChecked(),
            destination=self.restore_destination_checkbox.isChecked(),
            classification_mode=self.restore_mode_checkbox.isChecked(),
            analysis_profile=self.restore_profile_checkbox.isChecked(),
            active_tab=False,
        )
        self._preferences.set_fast_remote_move_enabled(fast_move_enabled)
        self.accept()

    def _reset_preferences(self) -> None:
        answer = QMessageBox.question(
            self,
            "Réinitialiser les préférences",
            "Effacer les préférences d’interface enregistrées ?\n\n"
            "L’historique File Janitor et vos fichiers ne seront pas modifiés.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._preferences.reset()
        self.was_reset = True
        self.restore_folder_checkbox.setChecked(True)
        self.restore_destination_checkbox.setChecked(True)
        self.restore_mode_checkbox.setChecked(True)
        self.restore_profile_checkbox.setChecked(True)
        self.fast_move_checkbox.setChecked(False)
        self.accept()
