"""Palette visuelle centralisée de l'interface graphique File Janitor."""

from __future__ import annotations


APP_STYLESHEET = r"""
QMainWindow, QWidget#appRoot {
    background: #07111f;
    color: #e8eef8;
    font-size: 13px;
}

QWidget {
    color: #e8eef8;
    font-size: 13px;
}

QFrame#appHeader {
    background: #091625;
    border: 1px solid #1d3148;
    border-radius: 10px;
}

QLabel#appTitle {
    color: #f7f9fc;
    font-size: 23px;
    font-weight: 800;
    letter-spacing: 1.6px;
}

QLabel#appSubtitle {
    color: #94a8c2;
    font-size: 13px;
}

QFrame#folderPanel,
QFrame#analysisSummaryPanel,
QFrame#analysisActionsPanel,
QFrame#historyBatchesPanel,
QFrame#historyOperationsPanel {
    background: #0d1928;
    border: 1px solid #20364e;
    border-radius: 9px;
}

QFrame#footerBar {
    background: #0b1827;
    border: 1px solid #1d3148;
    border-radius: 8px;
}

QLabel#versionLabel {
    color: #4d9cff;
    font-weight: 700;
    padding-right: 14px;
    border-right: 1px solid #20364e;
}

QLabel#securityLabel {
    color: #a7b7ca;
}

QLabel#sectionTitle {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.7px;
    padding: 2px 0 5px 0;
}
QLabel#sectionTitle[tone="blue"] { color: #4d9cff; }
QLabel#sectionTitle[tone="green"] { color: #45d66f; }
QLabel#sectionTitle[tone="purple"] { color: #be7cff; }
QLabel#sectionTitle[tone="orange"] { color: #f5a524; }

QTabWidget#mainTabs::pane {
    border: none;
    background: transparent;
    margin-top: 4px;
}

QTabBar#navigationTabs {
    background: transparent;
}
QTabBar#navigationTabs::tab {
    min-width: 190px;
    background: #0f1b2b;
    color: #aebed1;
    border: 1px solid #20364e;
    border-bottom: 2px solid transparent;
    padding: 11px 18px;
    margin-right: 4px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-weight: 700;
}
QTabBar#navigationTabs::tab:selected {
    background: #142338;
    color: #f4f8ff;
    border-bottom: 3px solid #3b82f6;
}
QTabBar#navigationTabs::tab:hover:!selected {
    background: #16263a;
    color: #e3ecf8;
}

QLineEdit {
    min-height: 30px;
    background: #091522;
    color: #e8eef8;
    border: 1px solid #2a4059;
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: #2563eb;
}
QLineEdit:focus { border: 1px solid #4d9cff; }
QLineEdit:disabled {
    color: #6f8197;
    background: #101925;
}


QLabel#fieldLabel {
    color: #9fb3ca;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
}

QLabel#fieldHint {
    color: #7890aa;
    font-size: 12px;
    padding-left: 2px;
}

QComboBox#classificationMode {
    min-height: 30px;
    background: #0f1d2d;
    color: #e8eef8;
    border: 1px solid #344d69;
    border-radius: 6px;
    padding: 5px 10px;
}
QComboBox#classificationMode:hover,
QComboBox#classificationMode:focus {
    border-color: #4d9cff;
    background: #13243a;
}
QComboBox#classificationMode:disabled {
    color: #66788e;
    background: #0b1420;
    border-color: #223247;
}
QComboBox#classificationMode QAbstractItemView {
    background: #0f1d2d;
    color: #e8eef8;
    border: 1px solid #344d69;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QPushButton {
    min-height: 30px;
    background: #1a2a3c;
    color: #dce7f5;
    border: 1px solid #344d69;
    border-radius: 6px;
    padding: 5px 13px;
    font-weight: 650;
}
QPushButton:hover {
    background: #22384f;
    border-color: #4d6a89;
}
QPushButton:pressed { background: #152638; }
QPushButton:disabled {
    color: #687b91;
    background: #15202d;
    border-color: #26384a;
}

QPushButton#primaryButton {
    min-height: 34px;
    background: #1a2a3c;
    color: #dce7f5;
    border-color: #344d69;
    font-weight: 800;
}
QPushButton#primaryButton:hover {
    background: #22384f;
    border-color: #4d6a89;
}
QPushButton#primaryButton:pressed { background: #152638; }
QPushButton#primaryButton:disabled {
    background: #202832;
    border-color: #3a4653;
    color: #91a0b2;
}

QPushButton#primaryButton:checked,
QPushButton#secondaryButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2563eb, stop:1 #3478f6);
    color: #ffffff;
    border-color: #4386ff;
    font-weight: 800;
}
QPushButton#primaryButton:checked:hover,
QPushButton#secondaryButton:checked:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2d6df4, stop:1 #4388ff);
}

QPushButton#primaryButton[analysisAction="true"],
QPushButton#secondaryButton[analysisAction="true"] {
    background: #1a2a3c;
    color: #a9bfd8;
    border-color: #344d69;
}
QPushButton#primaryButton[analysisAction="true"]:disabled,
QPushButton#secondaryButton[analysisAction="true"]:disabled {
    background: #1a2a3c;
    color: #91a9c3;
    border-color: #344d69;
}
QPushButton#primaryButton[analysisAction="true"][analysisSelected="true"],
QPushButton#secondaryButton[analysisAction="true"][analysisSelected="true"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2563eb, stop:1 #3478f6);
    color: #ffffff;
    border-color: #68a0ff;
    font-weight: 800;
}
QPushButton#primaryButton[analysisAction="true"][analysisSelected="true"]:hover,
QPushButton#secondaryButton[analysisAction="true"][analysisSelected="true"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2d6df4, stop:1 #4388ff);
}

QPushButton#successButton {
    min-height: 34px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #176b35, stop:1 #258a46);
    color: #f0fff4;
    border-color: #309e58;
    font-weight: 800;
}
QPushButton#successButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #1f7d40, stop:1 #2b9b50);
}
QPushButton#successButton:pressed { background: #125c2c; }
QPushButton#successButton:disabled {
    background: #101d18;
    border-color: #24382e;
    color: #61766a;
}

QPushButton#warningButton {
    min-height: 34px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #a95608, stop:1 #d07108);
    color: #fff9ed;
    border-color: #e08516;
    font-weight: 800;
    padding-left: 18px;
    padding-right: 18px;
}
QPushButton#warningButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #bd6309, stop:1 #e17b0b);
}
QPushButton#warningButton:pressed { background: #914807; }
QPushButton#warningButton:disabled {
    background: #3a2a18;
    border-color: #554127;
    color: #8d7a63;
}

QLabel#statusText { color: #9bc3f7; }
QLabel#summaryText { color: #d9e5f3; font-weight: 650; }
QLabel#selectionCount { color: #cad7e7; font-weight: 650; }
QLabel#successText { color: #45d66f; font-weight: 700; }
QLabel#warningText { color: #f5b342; font-weight: 700; }
QLabel#errorText { color: #ff6b78; font-weight: 700; }

QLabel#emptyState {
    color: #6f849d;
    background: #0a1624;
    border: 1px dashed #263c55;
    border-radius: 7px;
    padding: 18px;
    font-size: 13px;
}

QTableWidget {
    background: #0a1522;
    alternate-background-color: #0d1b2a;
    color: #dfe9f6;
    gridline-color: #203348;
    border: 1px solid #263c55;
    border-radius: 5px;
    selection-background-color: #245fc4;
    selection-color: #ffffff;
}
QTableWidget::item { padding: 5px; }
QTableWidget::item:selected { color: #ffffff; }

QTableWidget#categoryTable::item:selected {
    background: #185637;
    color: #f1fff6;
}
QTableWidget#historyTable::item:selected {
    background: #235fc9;
    color: #ffffff;
}

QHeaderView::section {
    background: #18283a;
    color: #dce8f6;
    border: none;
    border-right: 1px solid #2a4058;
    border-bottom: 1px solid #2a4058;
    padding: 6px;
    font-weight: 750;
}
QTableCornerButton::section {
    background: #18283a;
    border: none;
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: #091522;
    border: none;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #405a76;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover:vertical, QScrollBar::handle:hover:horizontal {
    background: #587898;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QSplitter::handle { background: #1b2e43; }
QSplitter#analysisSplitter::handle:vertical,
QSplitter#historySplitter::handle:vertical {
    height: 6px;
    margin: 2px 24px;
    border-radius: 3px;
}
QSplitter::handle:hover { background: #3b82f6; }

QToolTip {
    color: #f4f8ff;
    background: #162337;
    border: 1px solid #3a526d;
    padding: 5px;
}
"""


# 3B-2: résumé lisible des groupes calculés par le moteur.
APP_STYLESHEET += r"""
QLabel#classificationGroups {
    color: #b9d5ff;
    background: #0a2137;
    border: 1px solid #214b70;
    border-radius: 6px;
    padding: 7px 10px;
    font-weight: 650;
}
"""


# 3C-1: feedback visuel cohérent pour les opérations asynchrones.
APP_STYLESHEET += r"""
QLabel#statusText[tone="idle"] { color: #9bc3f7; }
QLabel#statusText[tone="busy"] { color: #67b7ff; font-weight: 700; }
QLabel#statusText[tone="success"] { color: #45d66f; font-weight: 700; }
QLabel#statusText[tone="warning"] { color: #f5b342; font-weight: 700; }
QLabel#statusText[tone="error"] { color: #ff6b78; font-weight: 700; }
QLabel#activityLabel {
    min-width: 130px;
    padding: 3px 8px;
    border-radius: 5px;
    font-weight: 700;
}
QLabel#activityLabel[tone="idle"] { color: #9fb3ca; }
QLabel#activityLabel[tone="busy"] { color: #8cc8ff; background: #0d2942; }
QLabel#activityLabel[tone="success"] { color: #62df83; background: #102b1b; }
QLabel#activityLabel[tone="warning"] { color: #f5bd5c; background: #33230e; }
QLabel#activityLabel[tone="error"] { color: #ff7d88; background: #35141a; }
QLabel#activityPercent {
    color: #8cc8ff;
    font-weight: 800;
}
QProgressBar#activityProgress {
    min-height: 8px;
    max-height: 8px;
    border: 1px solid #29425e;
    border-radius: 4px;
    background: #091522;
}
QProgressBar#activityProgress::chunk {
    background: #3b82f6;
    border-radius: 3px;
}
"""


# 4E-10L-r1: meilleure visibilité des cases à cocher de prévisualisation.
APP_STYLESHEET += r"""
QTableWidget#detailsTable::item {
    padding: 6px;
}
QTableWidget#detailsTable::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #5e8fcb;
    background: #17304a;
}
QTableWidget#detailsTable::indicator:hover {
    border-color: #8cc8ff;
    background: #21476d;
}
QTableWidget#detailsTable::indicator:unchecked {
    border: 1px solid #5e8fcb;
    background: #17304a;
}
QTableWidget#detailsTable::indicator:checked {
    border: 1px solid #8ee3a6;
    background: #1f7d40;
}
QTableWidget#detailsTable::indicator:disabled {
    border: 1px solid #3e4f63;
    background: #111c28;
}
"""


# 4E-10P-r1: hiérarchie visuelle explicite pour vue, filtre et sélection.
APP_STYLESHEET += r"""
QLabel#previewScopeSummary {
    color: #d7ebff;
    background: #0d2942;
    border: 1px solid #285579;
    border-radius: 6px;
    padding: 6px 9px;
    font-weight: 700;
}
QLabel#previewFilterLabel {
    color: #8fc7ff;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
    padding-top: 3px;
}
QLabel#previewFilterHint {
    color: #7890aa;
    font-size: 11px;
    padding: 0 2px 3px 2px;
}
QLabel#selectionCount {
    color: #e2edf9;
    font-weight: 750;
}
"""


# 4E-10P-r2: distinction explicite entre périmètre de classement et recherche texte.
APP_STYLESHEET += r"""
QLabel#previewScopeLabel {
    color: #9fcfff;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.5px;
    padding-top: 2px;
}
QComboBox#previewScopeCombo {
    min-height: 30px;
    background: #102239;
    color: #edf6ff;
    border: 1px solid #3d6f9c;
    border-radius: 6px;
    padding: 5px 10px;
    font-weight: 700;
}
QComboBox#previewScopeCombo:hover,
QComboBox#previewScopeCombo:focus {
    border-color: #67b7ff;
    background: #15304e;
}
QComboBox#previewScopeCombo QAbstractItemView {
    background: #0f1d2d;
    color: #e8eef8;
    border: 1px solid #3d6f9c;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
"""


# Espace de travail unifié : navigation persistante et opération clairement active.
APP_STYLESHEET += r"""
QFrame#navigationSidebar {
    background: #0b1725;
    border: 1px solid #20364e;
    border-radius: 9px;
}
QLabel#sidebarSectionTitle {
    color: #7f9ab6;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.8px;
    padding: 2px 7px 6px 7px;
}
QLabel#sidebarHint {
    color: #8ea5bd;
    background: #0e2032;
    border: 1px solid #203d58;
    border-radius: 6px;
    padding: 9px;
    font-size: 11px;
}
QFrame#sidebarSeparator {
    color: #263b51;
    max-height: 1px;
}
QPushButton#navigationOperationButton {
    min-height: 48px;
    background: transparent;
    color: #b7c8db;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 9px;
    text-align: left;
    font-weight: 650;
}
QPushButton#navigationOperationButton:hover {
    background: #13243a;
    color: #edf5ff;
    border-color: #294765;
}
QPushButton#navigationOperationButton[navigationSelected="true"] {
    background: #17385a;
    color: #ffffff;
    border-color: #3479b8;
    font-weight: 800;
}
QPushButton#navigationOperationButton[navigationSelected="true"]:hover {
    background: #1c466f;
}
QPushButton#navigationOperationButton:disabled {
    color: #62768c;
    background: transparent;
    border-color: transparent;
}
QLabel#workspaceOperationTitle {
    color: #f2f7ff;
    font-size: 19px;
    font-weight: 750;
    padding: 0 0 2px 0;
}
QPushButton#primaryButton[workspaceCallToAction="true"] {
    min-height: 42px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2563eb, stop:1 #3478f6);
    color: #ffffff;
    border: 1px solid #4386ff;
    border-radius: 6px;
    font-weight: 800;
}
QPushButton#primaryButton[workspaceCallToAction="true"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #2d6df4, stop:1 #4388ff);
}
QPushButton#primaryButton[workspaceCallToAction="true"]:disabled {
    background: #1a2a3c;
    color: #91a9c3;
    border-color: #344d69;
}
QRadioButton {
    color: #d7e3f1;
    spacing: 7px;
    padding: 3px 4px;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #5d7894;
    border-radius: 8px;
    background: #0b1725;
}
QRadioButton::indicator:checked {
    border: 4px solid #1e9eff;
    background: #0b1725;
}
QRadioButton:hover { color: #ffffff; }
QSplitter#workspaceSplitter::handle:vertical {
    height: 8px;
    margin: 1px 24px;
    border-radius: 4px;
    background: #233a52;
}
QSplitter#workspaceSplitter::handle:vertical:hover {
    background: #3b82f6;
}
"""
