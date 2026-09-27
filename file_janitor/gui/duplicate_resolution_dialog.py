"""Dialogue de résolution interactive des groupes de doublons exacts."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from file_janitor.application import DuplicateGroup
from file_janitor.formatting import human_count, human_size


class DuplicateResolutionDialog(QDialog):
    """Permet de choisir exactement un keeper pour chaque groupe de doublons."""

    def __init__(
        self,
        groups: tuple[DuplicateGroup, ...],
        *,
        keepers: dict[str, Path] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._groups = groups
        self._keepers = dict(keepers or {})
        self._button_group: QButtonGroup | None = None

        self.setWindowTitle("Résoudre les doublons exacts")
        self.resize(1100, 620)
        self.setModal(True)

        intro = QLabel(
            "Pour chaque groupe de fichiers strictement identiques, choisissez "
            "l’unique fichier à conserver. Les autres seront envoyés dans la "
            "corbeille File Janitor lors de l’exécution."
        )
        intro.setWordWrap(True)

        selector_row = QHBoxLayout()
        selector_label = QLabel("Groupe :")
        self.group_combo = QComboBox()
        self.group_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.group_combo.setMinimumContentsLength(40)
        selector_row.addWidget(selector_label)
        selector_row.addWidget(self.group_combo, 1)

        self.progress_label = QLabel("")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        selector_row.addWidget(self.progress_label)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("duplicateResolutionTable")
        self.table.setHorizontalHeaderLabels(
            ["Conserver", "Fichier", "Dossier", "Extension", "Taille", "Modifié"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        # Le thème principal est sombre : le radio keeper doit rester nettement
        # identifiable même quand la ligne n'a pas le focus clavier.
        self.setStyleSheet(
            """
            QRadioButton#keeperRadio {
                color: #eaffef;
                font-weight: 700;
                spacing: 8px;
            }
            QRadioButton#keeperRadio::indicator {
                width: 18px;
                height: 18px;
                border-radius: 10px;
                border: 2px solid #7fa7d8;
                background: #0d1928;
            }
            QRadioButton#keeperRadio::indicator:hover {
                border-color: #b8d4f5;
                background: #15283c;
            }
            QRadioButton#keeperRadio::indicator:checked {
                border: 5px solid #45d66f;
                background: #f0fff4;
            }
            """
        )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Valider les fichiers à conserver"
        )
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(selector_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.buttons)

        for index, group in enumerate(groups, start=1):
            self.group_combo.addItem(
                self._group_label(index, group),
                group.key,
            )

        self.group_combo.currentIndexChanged.connect(self._show_current_group)
        if groups:
            self.group_combo.setCurrentIndex(0)
            self._show_current_group()
        self._update_progress()

    @staticmethod
    def _group_label(index: int, group: DuplicateGroup) -> str:
        return (
            f"{index} — {human_count(len(group.members))} fichiers — "
            f"hash {group.key[:12]}"
        )

    def _current_group(self) -> DuplicateGroup | None:
        index = self.group_combo.currentIndex()
        if index < 0 or index >= len(self._groups):
            return None
        return self._groups[index]

    def _show_current_group(self, _index: int = -1) -> None:
        group = self._current_group()
        self.table.clearContents()
        if group is None:
            self.table.setRowCount(0)
            return

        self.table.setRowCount(len(group.members))
        button_group = QButtonGroup(self)
        button_group.setExclusive(True)
        self._button_group = button_group

        current_keeper = self._keepers.get(group.key)
        for row, member in enumerate(group.members):
            radio = QRadioButton("Conserver")
            radio.setObjectName("keeperRadio")
            radio.setToolTip("Conserver ce fichier et envoyer les autres à la corbeille")
            radio.setChecked(member.path == current_keeper)
            radio.toggled.connect(
                lambda checked, key=group.key, path=member.path: (
                    self._keeper_toggled(key, path, checked)
                )
            )
            button_group.addButton(radio)
            radio_cell = QWidget()
            radio_layout = QHBoxLayout(radio_cell)
            radio_layout.setContentsMargins(0, 0, 0, 0)
            radio_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            radio_layout.addWidget(radio)
            self.table.setCellWidget(row, 0, radio_cell)

            values = (
                member.path.name,
                str(member.path.parent),
                member.path.suffix.lower() or "—",
                human_size(member.size),
                (member.mtime.strftime("%Y-%m-%d %H:%M:%S") if member.mtime else "—"),
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)

        self._apply_keeper_highlight(group.key)

    def _apply_keeper_highlight(self, key: str) -> None:
        """Rend le keeper immédiatement visible sur toute la ligne."""

        group = self._current_group()
        if group is None or group.key != key:
            return
        keeper = self._keepers.get(key)
        selected_background = QBrush(QColor("#143a2a"))
        selected_foreground = QBrush(QColor("#f0fff4"))
        normal_background = QBrush()
        normal_foreground = QBrush()

        for row, member in enumerate(group.members):
            selected = member.path == keeper
            cell = self.table.cellWidget(row, 0)
            if cell is not None:
                cell.setStyleSheet(
                    "background: #143a2a; border-radius: 4px;"
                    if selected
                    else ""
                )
            for column in range(1, self.table.columnCount()):
                item = self.table.item(row, column)
                if item is None:
                    continue
                item.setBackground(
                    selected_background if selected else normal_background
                )
                item.setForeground(
                    selected_foreground if selected else normal_foreground
                )
                font = item.font()
                font.setBold(selected)
                item.setFont(font)

    def _keeper_toggled(self, key: str, path: Path, checked: bool) -> None:
        if checked:
            self._keepers[key] = path
            self._apply_keeper_highlight(key)
            self._update_progress()

    def _update_progress(self) -> None:
        resolved = sum(group.key in self._keepers for group in self._groups)
        total = len(self._groups)
        self.progress_label.setText(
            f"{human_count(resolved)} / {human_count(total)} groupes résolus"
        )
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(total > 0 and resolved == total)

    def keepers(self) -> dict[str, Path]:
        """Retourne une copie des choix validables du dialogue."""

        return dict(self._keepers)
