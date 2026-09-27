"""Fenêtre principale de l'interface graphique File Janitor."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QThreadPool, QTimer, QSize
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QProgressBar,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from file_janitor import __version__
from file_janitor.application import (
    ArchiveFormat,
    PlannedRecoveryState,
    AnalysisResult,
    ClassificationMode,
    classification_group_name,
    ExecuteResult,
    HistoryBatchSummary,
    HistoryOperationSummary,
    RecoveryResolutionKind,
    UndoResult,
    analyze_folder,
    analyze_rename_folder,
    analyze_archive_folder,
    analyze_empty_directories,
    classification_plan_config,
    duplicate_groups_for_selection,
    resolve_duplicate_groups_for_selection,
    resolve_selected_actions,
    summarize_classification_groups,
    execute_selected_actions,
    get_history_operation_summary,
    resolve_history_recovery_without_file_action,
    resume_queued_operations,
    get_history_summary,
    get_latest_undoable_batch_id,
    run_startup_crash_recovery,
    remote_folder_info,
    undo_execution,
    RenameTemplate,
    RenameTemplateError,
)
from file_janitor.formatting import human_count, human_size
from file_janitor.gui.duplicate_resolution_dialog import DuplicateResolutionDialog
from file_janitor.gui.preferences import GuiPreferences
from file_janitor.gui.preferences_dialog import PreferencesDialog
from file_janitor.gui.theme import APP_STYLESHEET
from file_janitor.gui.workers import Worker


class MainWindow(QMainWindow):
    """Fenêtre racine de la GUI File Janitor."""

    def __init__(self, *, preferences: GuiPreferences | None = None) -> None:
        super().__init__()
        self._preferences = preferences
        self.setWindowTitle("File Janitor")
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(APP_STYLESHEET)

        self._thread_pool = QThreadPool(self)
        self._workers: set[Worker] = set()
        self._closing = False
        self._active_worker: Worker | None = None
        self._active_analysis_profile: str | None = None
        self._active_operation: str | None = None
        self._analysis_result: AnalysisResult | None = None
        self._last_analysis_context: tuple[Path, Path, ClassificationMode, ClassificationMode | None, str] | None = None
        self._selected_actions: set[tuple[str, int]] = set()
        self._details_category_key: str | None = None
        self._details_scope_label = ""
        self._duplicate_keepers: dict[str, Path] = {}
        self._last_batch_id: int | None = None
        self._persistent_undo_target_loaded = False
        self._history_batches: tuple[HistoryBatchSummary, ...] = ()
        self._history_operation_worker: Worker | None = None
        self._history_generation = 0
        self._analysis_profile_source: Path | None = None
        self._analysis_profile_user_override = False
        self._analysis_profile_change_is_automatic = False
        self._last_rename_pattern = "{name}_{counter:03d}{ext}"
        self._selected_operation = "sort"
        self._history_refresh_selection_id: int | None = None
        self._pending_history_refresh = False
        self._pending_history_batch_id: int | None = None
        self._execution_completed = 0
        self._analysis_started_at: float | None = None
        self._analysis_activity_phase = ""
        self._analysis_elapsed_timer = QTimer(self)
        self._analysis_elapsed_timer.setInterval(1000)
        self._analysis_elapsed_timer.timeout.connect(self._update_analysis_elapsed)

        self.app_header = QFrame()
        self.app_header.setObjectName("appHeader")
        header_layout = QHBoxLayout(self.app_header)
        header_layout.setContentsMargins(16, 8, 16, 8)
        header_layout.setSpacing(14)

        app_icon = QLabel()
        app_icon.setObjectName("appIcon")
        app_icon.setPixmap(
            self.style()
            .standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            .pixmap(38, 38)
        )
        app_icon.setFixedSize(42, 42)

        title_column = QVBoxLayout()
        title_column.setSpacing(1)
        title = QLabel("FILE JANITOR")
        title.setObjectName("appTitle")
        subtitle = QLabel("Analyse et organisation sécurisée de vos fichiers")
        subtitle.setObjectName("appSubtitle")
        title_column.addWidget(title)
        title_column.addWidget(subtitle)

        header_layout.addWidget(app_icon)
        header_layout.addLayout(title_column)
        header_layout.addStretch()

        self.preferences_button = QPushButton("Préférences…")
        self.preferences_button.setObjectName("secondaryButton")
        self.preferences_button.setToolTip("Configurer les préférences de démarrage")
        self.preferences_button.setAccessibleName("Préférences")
        self.preferences_button.clicked.connect(self._open_preferences)
        header_layout.addWidget(self.preferences_button)

        self.folder_edit = QLineEdit()
        self.folder_edit.setObjectName("pathField")
        self.folder_edit.setPlaceholderText("Choisissez un dossier à analyser")
        self.folder_edit.setAccessibleName("Dossier à analyser")
        self.folder_edit.setReadOnly(True)

        self.browse_button = QPushButton("Choisir un dossier…")
        self.browse_button.setObjectName("secondaryButton")
        self.browse_button.setAccessibleName("Choisir un dossier à analyser")
        self.browse_button.clicked.connect(self._choose_folder)

        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)

        self.destination_edit = QLineEdit()
        self.destination_edit.setObjectName("destinationPathField")
        self.destination_edit.setPlaceholderText("Même dossier que la source")
        self.destination_edit.setAccessibleName("Dossier de destination")
        self.destination_edit.setReadOnly(True)

        self.destination_browse_button = QPushButton("Choisir…")
        self.destination_browse_button.setObjectName("secondaryButton")
        self.destination_browse_button.setAccessibleName(
            "Choisir le dossier de destination"
        )
        self.destination_browse_button.clicked.connect(self._choose_destination)

        self.destination_source_button = QPushButton("Utiliser la source")
        self.destination_source_button.setObjectName("secondaryButton")
        self.destination_source_button.setAccessibleName(
            "Utiliser le dossier source comme destination"
        )
        self.destination_source_button.clicked.connect(
            self._use_source_as_destination
        )

        destination_row = QHBoxLayout()
        destination_row.addWidget(self.destination_edit, 1)
        destination_row.addWidget(self.destination_browse_button)
        destination_row.addWidget(self.destination_source_button)

        self.classification_mode_label = QLabel("TRI PRINCIPAL")
        self.classification_mode_label.setObjectName("fieldLabel")
        self.classification_mode_combo = QComboBox()
        self.classification_mode_combo.setObjectName("classificationMode")
        self.classification_mode_combo.setAccessibleName("Mode de classement")
        self.classification_mode_label.setBuddy(self.classification_mode_combo)
        for label, mode in (
            ("Extension", ClassificationMode.EXTENSION),
            ("Date (AAAA-MM)", ClassificationMode.DATE),
            ("Nom commun", ClassificationMode.COMMON_NAME),
            ("Taille", ClassificationMode.SIZE),
        ):
            self.classification_mode_combo.addItem(label, mode)
        self.classification_mode_combo.setToolTip(
            "Choisit comment les fichiers à classer seront regroupés."
        )
        self.secondary_mode_label = QLabel("PUIS")
        self.secondary_mode_label.setObjectName("fieldLabel")
        self.secondary_mode_combo = QComboBox()
        self.secondary_mode_combo.setObjectName("secondaryClassificationMode")
        self.secondary_mode_combo.setAccessibleName("Second critère de classement")
        self.secondary_mode_label.setBuddy(self.secondary_mode_combo)
        self.secondary_mode_combo.addItem("Aucun", None)
        for label, mode in (
            ("Extension", ClassificationMode.EXTENSION),
            ("Date (AAAA-MM)", ClassificationMode.DATE),
            ("Nom commun", ClassificationMode.COMMON_NAME),
            ("Taille", ClassificationMode.SIZE),
        ):
            self.secondary_mode_combo.addItem(label, mode)
        self.secondary_mode_combo.setToolTip("Classe chaque groupe principal selon ce second critère.")
        self.secondary_mode_combo.currentIndexChanged.connect(self._classification_mode_changed)

        self.classification_mode_hint = QLabel(
            "Classe les fichiers dans des dossiers correspondant à leur extension."
        )
        self.classification_mode_hint.setObjectName("fieldHint")
        self.classification_mode_hint.setWordWrap(True)
        self.classification_mode_combo.currentIndexChanged.connect(
            self._classification_mode_changed
        )

        classification_row = QHBoxLayout()
        classification_row.setSpacing(10)
        classification_row.addWidget(self.classification_mode_label)
        classification_row.addWidget(self.classification_mode_combo, 1)
        classification_row.addWidget(self.secondary_mode_label)
        classification_row.addWidget(self.secondary_mode_combo, 1)

        self.analysis_profile_label = QLabel("PROFIL D'ANALYSE")
        self.analysis_profile_label.setObjectName("fieldLabel")
        self.analysis_profile_combo = QComboBox()
        self.analysis_profile_combo.setObjectName("analysisProfile")
        self.analysis_profile_combo.setAccessibleName("Profil d'analyse")
        self.analysis_profile_label.setBuddy(self.analysis_profile_combo)
        self.analysis_profile_combo.addItem("Standard", "standard")
        self.analysis_profile_combo.addItem("Remote / rapide", "remote")
        self.analysis_profile_combo.setToolTip(
            "Standard lit les premiers octets de chaque fichier. "
            "Remote / rapide évite ces lectures coûteuses sur clouds/FUSE."
        )
        self.analysis_profile_combo.currentIndexChanged.connect(
            self._analysis_profile_changed
        )

        self.analysis_profile_hint = QLabel(
            "Standard : analyse complète avec détection par contenu."
        )
        self.analysis_profile_hint.setObjectName("fieldHint")
        self.analysis_profile_hint.setWordWrap(True)

        analysis_profile_row = QHBoxLayout()
        analysis_profile_row.setSpacing(10)
        analysis_profile_row.addWidget(self.analysis_profile_label)
        analysis_profile_row.addWidget(self.analysis_profile_combo, 1)

        self.analyze_button = QPushButton("Analyser")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.setProperty("workspaceCallToAction", "true")
        self.analyze_button.setAccessibleName("Analyser le dossier")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self._start_selected_operation)

        # Options propres à chaque opération : elles restent dans l'espace de
        # travail, au lieu d'être demandées dans une succession de fenêtres.
        self.operation_options_stack = QStackedWidget()
        self.operation_options_stack.setObjectName("operationOptionsStack")

        sort_options_page = QWidget()
        sort_options_layout = QVBoxLayout(sort_options_page)
        sort_options_layout.setContentsMargins(0, 0, 0, 0)
        sort_options_layout.setSpacing(8)
        destination_label = QLabel("DESTINATION")
        destination_label.setObjectName("fieldLabel")
        destination_label.setBuddy(self.destination_edit)
        sort_options_layout.addWidget(destination_label)
        sort_options_layout.addLayout(destination_row)
        sort_options_layout.addLayout(classification_row)
        sort_options_layout.addWidget(self.classification_mode_hint)
        sort_options_layout.addLayout(analysis_profile_row)
        sort_options_layout.addWidget(self.analysis_profile_hint)
        self.operation_options_stack.addWidget(sort_options_page)

        rename_options_page = QWidget()
        rename_options_layout = QVBoxLayout(rename_options_page)
        rename_options_layout.setContentsMargins(0, 0, 0, 0)
        rename_options_layout.setSpacing(6)
        rename_pattern_label = QLabel("MODÈLE DE NOM")
        rename_pattern_label.setObjectName("fieldLabel")
        self.rename_pattern_edit = QLineEdit("{name}_{counter:03d}{ext}")
        self.rename_pattern_edit.setObjectName("renamePattern")
        self.rename_pattern_edit.setAccessibleName("Modèle de nom des fichiers")
        self.rename_pattern_edit.setToolTip(
            "Champs disponibles : {name}, {ext}, {parent}, {date}, {counter}."
        )
        rename_pattern_label.setBuddy(self.rename_pattern_edit)
        rename_options_layout.addWidget(rename_pattern_label)
        rename_options_layout.addWidget(self.rename_pattern_edit)
        rename_hint = QLabel(
            "Champs : {name}, {ext}, {parent}, {date}, {counter}. "
            "Les noms proposés seront vérifiés avant toute modification."
        )
        rename_hint.setObjectName("fieldHint")
        rename_hint.setWordWrap(True)
        rename_options_layout.addWidget(rename_hint)
        self.operation_options_stack.addWidget(rename_options_page)

        archive_options_page = QWidget()
        archive_options_layout = QVBoxLayout(archive_options_page)
        archive_options_layout.setContentsMargins(0, 0, 0, 0)
        archive_options_layout.setSpacing(8)
        archive_controls_row = QHBoxLayout()
        age_label = QLabel("Fichiers plus anciens que")
        self.archive_age_spin = QSpinBox()
        self.archive_age_spin.setObjectName("archiveAge")
        self.archive_age_spin.setRange(1, 365000)
        self.archive_age_spin.setValue(12)
        self.archive_age_spin.setAccessibleName("Âge minimal des fichiers à archiver")
        age_label.setBuddy(self.archive_age_spin)
        self.archive_age_unit_combo = QComboBox()
        self.archive_age_unit_combo.setObjectName("archiveAgeUnit")
        self.archive_age_unit_combo.setAccessibleName("Unité de l'ancienneté")
        self.archive_age_unit_combo.addItem("jours", "days")
        self.archive_age_unit_combo.addItem("mois", "months")
        self.archive_age_unit_combo.addItem("années", "years")
        self.archive_age_unit_combo.setCurrentIndex(1)
        archive_controls_row.addWidget(age_label)
        archive_controls_row.addWidget(self.archive_age_spin)
        archive_controls_row.addWidget(self.archive_age_unit_combo)
        archive_controls_row.addStretch()
        archive_options_layout.addLayout(archive_controls_row)
        archive_format_row = QHBoxLayout()
        archive_format_label = QLabel("Format d'archivage")
        archive_format_label.setObjectName("fieldLabel")
        self.archive_format_group = QButtonGroup(self)
        self.archive_format_group.setExclusive(True)
        self.archive_format_zip_radio = QRadioButton("Fichier ZIP compressé")
        self.archive_format_zip_radio.setObjectName("archiveFormatZip")
        self.archive_format_zip_radio.setAccessibleName("Créer une archive ZIP")
        self.archive_format_zip_radio.setToolTip(
            "Regroupe les fichiers sélectionnés dans une archive ZIP compressée."
        )
        self.archive_format_folders_radio = QRadioButton(
            "Conserver l'arborescence de dossiers"
        )
        self.archive_format_folders_radio.setObjectName("archiveFormatFolders")
        self.archive_format_folders_radio.setAccessibleName(
            "Conserver les fichiers dans une arborescence de dossiers"
        )
        self.archive_format_folders_radio.setToolTip(
            "Déplace les fichiers en conservant leur structure de dossiers."
        )
        self.archive_format_group.addButton(self.archive_format_zip_radio)
        self.archive_format_group.addButton(self.archive_format_folders_radio)
        self.archive_format_folders_radio.setChecked(True)
        archive_format_row.addWidget(archive_format_label)
        archive_format_row.addWidget(self.archive_format_zip_radio)
        archive_format_row.addWidget(self.archive_format_folders_radio)
        archive_format_row.addStretch()
        archive_options_layout.addLayout(archive_format_row)
        archive_destination_label = QLabel("DOSSIER D'ARCHIVE")
        archive_destination_label.setObjectName("fieldLabel")
        self.archive_destination_edit = QLineEdit()
        self.archive_destination_edit.setObjectName("archiveDestination")
        self.archive_destination_edit.setReadOnly(True)
        self.archive_destination_edit.setPlaceholderText("Choisir un dossier d'archive")
        self.archive_destination_edit.setAccessibleName("Dossier de destination de l'archive")
        archive_destination_label.setBuddy(self.archive_destination_edit)
        self.archive_destination_browse_button = QPushButton("Parcourir…")
        self.archive_destination_browse_button.setObjectName("secondaryButton")
        self.archive_destination_browse_button.clicked.connect(
            self._choose_archive_destination
        )
        archive_destination_row = QHBoxLayout()
        archive_destination_row.addWidget(self.archive_destination_edit, 1)
        archive_destination_row.addWidget(self.archive_destination_browse_button)
        archive_options_layout.addWidget(archive_destination_label)
        archive_options_layout.addLayout(archive_destination_row)
        self.operation_options_stack.addWidget(archive_options_page)

        empty_options_page = QWidget()
        empty_options_layout = QVBoxLayout(empty_options_page)
        empty_options_layout.setContentsMargins(0, 0, 0, 0)
        empty_options_hint = QLabel(
            "File Janitor vérifiera les sous-dossiers du dossier source. "
            "Les dossiers proposés seront affichés avant leur suppression réversible."
        )
        empty_options_hint.setObjectName("fieldHint")
        empty_options_hint.setWordWrap(True)
        empty_options_layout.addWidget(empty_options_hint)
        empty_options_layout.addStretch(1)
        self.operation_options_stack.addWidget(empty_options_page)
        # QStackedWidget prend par défaut la taille de sa page la plus haute.
        # La page d'archivage impose donc un grand espace vide même lorsque
        # « Dossiers vides » est sélectionné, ce qui écrase les panneaux de
        # résultat et de prévisualisation dans la fenêtre.
        self.operation_options_stack.setMinimumHeight(0)

        self.rename_button = QPushButton("Renommer par lot…")
        self.rename_button.setObjectName("secondaryButton")
        self.rename_button.setAccessibleName("Renommer les fichiers par lot")
        self.rename_button.setToolTip(
            "Prévisualiser des noms selon un modèle, puis renommer la sélection."
        )
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._open_rename_dialog)
        self.rename_button.setParent(self)
        self.rename_button.hide()

        self.archive_button = QPushButton("Archiver selon l’ancienneté…")
        self.archive_button.setObjectName("secondaryButton")
        self.archive_button.setAccessibleName("Archiver les fichiers anciens")
        self.archive_button.setToolTip(
            "Prévisualiser les fichiers anciens et les déplacer vers un dossier d’archive."
        )
        self.archive_button.setEnabled(False)
        self.archive_button.clicked.connect(self._open_archive_dialog)
        self.archive_button.setParent(self)
        self.archive_button.hide()

        self.empty_directories_button = QPushButton("Supprimer les dossiers vides…")
        self.empty_directories_button.setObjectName("secondaryButton")
        self.empty_directories_button.setAccessibleName(
            "Repérer les dossiers vides"
        )
        self.empty_directories_button.setToolTip(
            "Prévisualiser les dossiers vides avant leur suppression réversible."
        )
        self.empty_directories_button.setEnabled(False)
        self.empty_directories_button.clicked.connect(
            self._start_empty_directory_analysis
        )
        self.empty_directories_button.setParent(self)
        self.empty_directories_button.hide()

        # Les actions de l'onglet sont des choix exclusifs : une seule reste
        # mise en évidence après le clic, tandis que les autres gardent leur
        # apparence bleue atténuée.
        self.analysis_action_group = QButtonGroup(self)
        self.analysis_action_group.setExclusive(True)
        for button in (
            self.analyze_button,
            self.rename_button,
            self.archive_button,
            self.empty_directories_button,
        ):
            button.setCheckable(True)
            self.analysis_action_group.addButton(button)
        self.analysis_action_group.buttonToggled.connect(
            self._refresh_analysis_action_button_styles
        )
        self._refresh_analysis_action_button_styles()

        self.status_label = QLabel("Sélectionnez un dossier pour commencer.")
        self.status_label.setObjectName("statusText")
        self.status_label.setWordWrap(True)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summaryText")
        self.summary_label.setWordWrap(True)

        self.scan_errors_label = QLabel("")
        self.scan_errors_label.setObjectName("warningText")
        self.scan_errors_label.setWordWrap(True)
        self.scan_errors_label.setVisible(False)

        self.scan_errors_table = QTableWidget(0, 1)
        self.scan_errors_table.setHorizontalHeaderLabels(
            ["Détail des erreurs de scan"]
        )
        self.scan_errors_table.setAccessibleName(
            "Détail des erreurs rencontrées pendant l’analyse"
        )
        self.scan_errors_table.verticalHeader().setVisible(False)
        self.scan_errors_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.scan_errors_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.scan_errors_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.scan_errors_table.setMaximumHeight(170)
        self.scan_errors_table.setVisible(False)

        self.classification_groups_label = QLabel("")
        self.classification_groups_label.setObjectName("classificationGroups")
        self.classification_groups_label.setWordWrap(True)
        self.classification_groups_label.setVisible(False)

        self.duplicate_resolution_button = QPushButton("Résoudre les doublons…")
        self.duplicate_resolution_button.setObjectName("secondaryButton")
        self.duplicate_resolution_button.setVisible(False)
        self.duplicate_resolution_button.clicked.connect(
            self._open_duplicate_resolution
        )

        self.category_table = QTableWidget(0, 2)
        self.category_table.setHorizontalHeaderLabels(["Catégorie", "Fichiers"])
        self.category_table.verticalHeader().setVisible(False)
        self.category_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.category_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.category_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.category_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.category_table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.category_table.itemSelectionChanged.connect(
            self._show_selected_details
        )
        self.category_table.itemClicked.connect(self._open_selected_preview)
        self.category_table.setMinimumHeight(120)
        self.category_table.setVisible(False)

        self.preview_scope_label = QLabel("PÉRIMÈTRE DE PRÉVISUALISATION")
        self.preview_scope_label.setObjectName("previewScopeLabel")
        self.preview_scope_label.setVisible(False)

        self.preview_scope_combo = QComboBox()
        self.preview_scope_combo.setObjectName("previewScopeCombo")
        self.preview_scope_combo.setAccessibleName(
            "Choisir le groupe de fichiers à prévisualiser"
        )
        self.preview_scope_combo.setToolTip(
            "Choisissez ici Tous les fichiers classés ou un groupe précis "
            "(extension, mois, racine commune ou tranche de taille)."
        )
        self.preview_scope_combo.currentIndexChanged.connect(
            self._preview_scope_changed
        )
        self.preview_scope_combo.setVisible(False)

        self.details_label = QLabel("")
        self.details_label.setVisible(False)

        self.preview_scope_summary = QLabel("")
        self.preview_scope_summary.setObjectName("previewScopeSummary")
        self.preview_scope_summary.setVisible(False)

        self.details_filter_label = QLabel("RECHERCHE TEXTE DANS LE PÉRIMÈTRE")
        self.details_filter_label.setObjectName("previewFilterLabel")
        self.details_filter_label.setVisible(False)

        self.details_filter = QLineEdit()
        self.details_filter.setAccessibleName(
            "Rechercher du texte dans les fichiers affichés"
        )
        self.details_filter.setPlaceholderText(
            "Nom du fichier, destination ou raison…"
        )
        self.details_filter.setClearButtonEnabled(True)
        self.details_filter.setToolTip(
            "Le filtre ne modifie pas l'analyse. Il limite uniquement les "
            "lignes affichées et la portée des boutons de sélection."
        )
        self.details_filter.textChanged.connect(self._filter_details)
        self.details_filter.setVisible(False)

        self.details_filter_hint = QLabel(
            "Recherche uniquement le nom du fichier, la destination et la raison. "
            "Pour une extension précise, utilisez « Périmètre de prévisualisation » "
            "ci-dessus ; saisir exactement xml ou .xml depuis la vue globale bascule "
            "automatiquement sur cette extension."
        )
        self.details_filter_hint.setObjectName("previewFilterHint")
        self.details_filter_hint.setWordWrap(True)
        self.details_filter_hint.setVisible(False)

        self.selection_label = QLabel("Sélection globale : 0 / 0 actions")
        self.selection_label.setObjectName("selectionCount")
        self.selection_label.setVisible(False)
        self.select_all_button = QPushButton("Sélectionner les fichiers")
        self.select_all_button.setObjectName("secondaryButton")
        self.select_all_button.clicked.connect(self._select_visible_actions)
        self.select_all_button.setVisible(False)
        self.clear_selection_button = QPushButton("Désélectionner les fichiers")
        self.clear_selection_button.setObjectName("secondaryButton")
        self.clear_selection_button.clicked.connect(self._clear_visible_selection)
        self.clear_selection_button.setVisible(False)

        selection_row = QHBoxLayout()
        selection_row.addWidget(self.selection_label)
        selection_row.addStretch()
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_selection_button)

        self.execute_button = QPushButton("Exécuter la sélection…")
        self.execute_button.setObjectName("successButton")
        self.execute_button.setAccessibleName("Exécuter la sélection")
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self._confirm_execution)

        self.execution_mode_label = QLabel("Mode des déplacements rclone/FUSE :")
        self.execution_mode_combo = QComboBox()
        self.execution_mode_combo.setObjectName("executionMoveMode")
        self.execution_mode_combo.setAccessibleName("Mode des déplacements")
        self.execution_mode_combo.addItem("Sûr — copie si nécessaire", False)
        self.execution_mode_combo.addItem("Rapide — déplacement sur le partage", True)
        self.execution_mode_combo.setToolTip(
            "Le mode rapide peut écraser une destination créée simultanément "
            "par un autre programme si rclone/FUSE ne prend pas en charge "
            "RENAME_NOREPLACE. Le choix s'applique à cette exécution."
        )
        if self._preferences is not None and self._preferences.fast_remote_move_enabled():
            self.execution_mode_combo.setCurrentIndex(1)

        execution_mode_row = QHBoxLayout()
        execution_mode_row.addWidget(self.execution_mode_label)
        execution_mode_row.addWidget(self.execution_mode_combo, 1)

        self.execution_summary_label = QLabel("")
        self.execution_summary_label.setObjectName("successText")
        self.execution_summary_label.setWordWrap(True)
        self.execution_summary_label.setVisible(False)

        self.execution_errors_table = QTableWidget(0, 1)
        self.execution_errors_table.setHorizontalHeaderLabels(["Erreurs d'exécution"])
        self.execution_errors_table.verticalHeader().setVisible(False)
        self.execution_errors_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.execution_errors_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.execution_errors_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.execution_errors_table.setVisible(False)

        self.undo_button = QPushButton("Annuler le dernier batch…")
        self.undo_button.setObjectName("warningButton")
        self.undo_button.setAccessibleName("Annuler la dernière exécution")
        self.undo_button.setEnabled(False)
        self.undo_button.setVisible(False)
        self.undo_button.clicked.connect(self._confirm_undo)

        self.undo_summary_label = QLabel("")
        self.undo_summary_label.setObjectName("warningText")
        self.undo_summary_label.setWordWrap(True)
        self.undo_summary_label.setVisible(False)

        self.history_button = QPushButton("Actualiser l'historique")
        self.history_button.setObjectName("primaryButton")
        self.history_button.setAccessibleName("Actualiser l'historique")
        self.history_button.clicked.connect(self._start_history_refresh)

        self.history_status_label = QLabel("Historique non chargé.")
        self.history_status_label.setObjectName("statusText")
        self.history_status_label.setWordWrap(True)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["Exécution", "Date", "Dossier", "État", "Bilan"]
        )
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.history_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        for column in (0, 1, 3, 4):
            self.history_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.history_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.history_table.itemSelectionChanged.connect(
            self._show_selected_history_operations
        )
        self.history_table.setVisible(False)

        self.history_operations_status_label = QLabel("")
        self.history_operations_status_label.setWordWrap(True)
        self.history_operations_status_label.setVisible(False)

        self.resolve_recovery_button = QPushButton(
            "Clore la récupération sans modifier les fichiers…"
        )
        self.resolve_recovery_button.setObjectName("warningButton")
        self.resolve_recovery_button.setAccessibleName(
            "Clore les opérations de récupération non résolues"
        )
        self.resolve_recovery_button.setVisible(False)
        self.resolve_recovery_button.setEnabled(False)
        self.resolve_recovery_button.clicked.connect(
            self._confirm_history_recovery_resolution
        )

        self.resume_queued_button = QPushButton(
            "Reprendre les opérations non démarrées…"
        )
        self.resume_queued_button.setObjectName("primaryButton")
        self.resume_queued_button.setAccessibleName(
            "Reprendre les opérations non démarrées de l’exécution sélectionnée"
        )
        self.resume_queued_button.setVisible(False)
        self.resume_queued_button.setEnabled(False)
        self.resume_queued_button.clicked.connect(
            self._confirm_queued_resume
        )

        self.history_operations_table = QTableWidget(0, 7)
        self.history_operations_table.setHorizontalHeaderLabels(
            ["Action", "Fichier", "Taille", "Stockage", "Catégorie", "État", "Erreur"]
        )
        self.history_operations_table.verticalHeader().setVisible(False)
        self.history_operations_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.history_operations_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        for column in (0, 2, 4, 5):
            self.history_operations_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for column in (1, 3, 6):
            self.history_operations_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch
            )
        self.history_operations_table.setVisible(False)

        self.details_table = QTableWidget(0, 6)
        self.details_table.setObjectName("detailsTable")
        self.details_table.setHorizontalHeaderLabels(
            ["Retenir", "Action", "Fichier", "Taille", "Destination", "Raison"]
        )
        self.details_table.verticalHeader().setVisible(False)
        self.details_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.details_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.details_table.horizontalHeader().setMinimumSectionSize(36)
        self.details_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.details_table.setColumnWidth(0, 68)
        for column in (1, 3):
            self.details_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for column in (2, 4, 5):
            self.details_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch
            )
        self.details_table.itemChanged.connect(self._detail_selection_changed)
        self.details_table.setMinimumHeight(220)
        self.details_table.setVisible(False)

        self.preview_empty_label = QLabel(
            "Aucune prévisualisation à afficher. Analysez un dossier, puis choisissez une catégorie."
        )
        self.preview_empty_label.setObjectName("emptyState")
        self.preview_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_empty_label.setWordWrap(True)

        self.history_empty_label = QLabel(
            "Aucune exécution affichée. Actualisez l’historique pour consulter les opérations récentes."
        )
        self.history_empty_label.setObjectName("emptyState")
        self.history_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_empty_label.setWordWrap(True)

        self.history_operations_empty_label = QLabel(
            "Sélectionnez une exécution pour consulter le détail de ses opérations."
        )
        self.history_operations_empty_label.setObjectName("emptyState")
        self.history_operations_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_operations_empty_label.setWordWrap(True)

        for table in (
            self.details_table,
            self.history_table,
            self.history_operations_table,
        ):
            table.setAlternatingRowColors(True)
            table.setWordWrap(False)
            table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
            table.setHorizontalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel
            )

        self.folder_section_title = QLabel("DOSSIER À ANALYSER")
        self.folder_section_title.setObjectName("sectionTitle")
        self.folder_section_title.setProperty("tone", "blue")
        self.workspace_operation_title = QLabel("Classer les fichiers")
        self.workspace_operation_title.setObjectName("workspaceOperationTitle")

        folder_panel_layout = QVBoxLayout()
        folder_panel_layout.setContentsMargins(14, 12, 14, 12)
        folder_panel_layout.setSpacing(9)
        folder_panel_layout.addWidget(self.folder_section_title)
        folder_panel_layout.addWidget(self.workspace_operation_title)
        folder_panel_layout.addLayout(folder_row)
        folder_panel_layout.addWidget(self.operation_options_stack)
        folder_panel_layout.addWidget(self.analyze_button)

        self.folder_panel = QFrame()
        self.folder_panel.setObjectName("folderPanel")
        folder_panel_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinimumSize
        )
        self.folder_panel.setLayout(folder_panel_layout)

        result_section_title = QLabel("RÉSULTAT DE L'ANALYSE")
        result_section_title.setObjectName("sectionTitle")
        result_section_title.setProperty("tone", "green")

        self.category_table.setObjectName("categoryTable")
        analysis_summary_layout = QVBoxLayout()
        analysis_summary_layout.setContentsMargins(14, 12, 14, 12)
        analysis_summary_layout.setSpacing(7)
        analysis_summary_layout.addWidget(result_section_title)
        analysis_summary_layout.addWidget(self.status_label)
        analysis_summary_layout.addWidget(self.summary_label)
        analysis_summary_layout.addWidget(self.scan_errors_label)
        analysis_summary_layout.addWidget(self.scan_errors_table)
        analysis_summary_layout.addWidget(self.classification_groups_label)
        analysis_summary_layout.addWidget(self.duplicate_resolution_button)
        analysis_summary_layout.addWidget(self.category_table, 1)

        self.analysis_summary_panel = QFrame()
        self.analysis_summary_panel.setObjectName("analysisSummaryPanel")
        self.analysis_summary_panel.setLayout(analysis_summary_layout)

        preview_section_title = QLabel("PRÉVISUALISATION DES ACTIONS")
        preview_section_title.setObjectName("sectionTitle")
        preview_section_title.setProperty("tone", "purple")

        analysis_actions_layout = QVBoxLayout()
        analysis_actions_layout.setContentsMargins(14, 12, 14, 12)
        analysis_actions_layout.setSpacing(8)
        analysis_actions_layout.addWidget(preview_section_title)
        analysis_actions_layout.addWidget(self.preview_scope_label)
        analysis_actions_layout.addWidget(self.preview_scope_combo)
        analysis_actions_layout.addWidget(self.details_label)
        analysis_actions_layout.addWidget(self.preview_scope_summary)
        analysis_actions_layout.addWidget(self.details_filter_label)
        analysis_actions_layout.addWidget(self.details_filter)
        analysis_actions_layout.addWidget(self.details_filter_hint)
        analysis_actions_layout.addLayout(selection_row)
        analysis_actions_layout.addLayout(execution_mode_row)
        analysis_actions_layout.addWidget(self.execute_button)
        analysis_actions_layout.addWidget(self.execution_summary_label)
        analysis_actions_layout.addWidget(self.execution_errors_table)
        analysis_actions_layout.addWidget(self.undo_summary_label)
        analysis_actions_layout.addWidget(self.preview_empty_label, 1)
        analysis_actions_layout.addWidget(self.details_table, 1)

        self.analysis_actions_panel = QFrame()
        self.analysis_actions_panel.setObjectName("analysisActionsPanel")
        self.analysis_actions_panel.setLayout(analysis_actions_layout)

        analysis_layout = QVBoxLayout()
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(10)
        analysis_layout.addWidget(self.folder_panel)
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.analysis_summary_panel)
        self.workspace_splitter.addWidget(self.analysis_actions_panel)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 2)
        self.workspace_splitter.setSizes([310, 500])
        analysis_layout.addWidget(self.workspace_splitter, 1)

        self.analysis_tab = QWidget()
        self.analysis_tab.setObjectName("workspacePage")
        self.analysis_tab.setLayout(analysis_layout)
        # Compatibilité des méthodes et raccourcis historiques : les détails
        # vivent maintenant dans la même vue que les options et le résultat.
        self.preview_tab = self.analysis_tab

        history_section_title = QLabel("HISTORIQUE DES EXÉCUTIONS")
        history_section_title.setObjectName("sectionTitle")
        history_section_title.setProperty("tone", "blue")

        history_header_row = QHBoxLayout()
        history_header_row.addWidget(history_section_title)
        history_header_row.addStretch()
        history_header_row.addWidget(self.history_button)

        self.history_table.setObjectName("historyTable")
        history_batches_layout = QVBoxLayout()
        history_batches_layout.setContentsMargins(14, 12, 14, 12)
        history_batches_layout.setSpacing(8)
        history_batches_layout.addLayout(history_header_row)
        history_batches_layout.addWidget(self.history_status_label)
        history_batches_layout.addWidget(self.history_empty_label, 1)
        history_batches_layout.addWidget(self.history_table, 1)

        self.history_batches_panel = QFrame()
        self.history_batches_panel.setObjectName("historyBatchesPanel")
        self.history_batches_panel.setLayout(history_batches_layout)

        operations_section_title = QLabel("DÉTAIL DES OPÉRATIONS (SÉLECTION)")
        operations_section_title.setObjectName("sectionTitle")
        operations_section_title.setProperty("tone", "blue")

        self.history_operations_table.setObjectName("historyOperationsTable")
        history_operations_layout = QVBoxLayout()
        history_operations_layout.setContentsMargins(14, 12, 14, 12)
        history_operations_layout.setSpacing(8)
        history_operations_layout.addWidget(operations_section_title)
        history_operations_layout.addWidget(self.history_operations_status_label)
        history_operations_layout.addWidget(self.resolve_recovery_button)
        history_operations_layout.addWidget(self.resume_queued_button)
        history_operations_layout.addWidget(self.history_operations_empty_label, 1)
        history_operations_layout.addWidget(self.history_operations_table, 1)

        self.history_operations_panel = QFrame()
        self.history_operations_panel.setObjectName("historyOperationsPanel")
        self.history_operations_panel.setLayout(history_operations_layout)

        self.history_splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_splitter.setObjectName("historySplitter")
        self.history_splitter.setChildrenCollapsible(False)
        self.history_splitter.addWidget(self.history_batches_panel)
        self.history_splitter.addWidget(self.history_operations_panel)
        self.history_splitter.setStretchFactor(0, 1)
        self.history_splitter.setStretchFactor(1, 2)
        self.history_splitter.setSizes([340, 460])

        history_layout = QVBoxLayout()
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(self.history_splitter)

        self.history_tab = QWidget()
        self.history_tab.setObjectName("historyPage")
        self.history_tab.setLayout(history_layout)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.tabBar().hide()
        self.tabs.addTab(self.analysis_tab, "Espace de travail")
        self.tabs.addTab(self.history_tab, "Historique")
        self.tabs.setCurrentWidget(self.analysis_tab)
        self.tabs.setTabToolTip(0, "Espace de travail (Alt+1 / Alt+2)")
        self.tabs.setTabToolTip(1, "Historique des exécutions (Alt+3)")

        self.navigation_sidebar = QFrame()
        self.navigation_sidebar.setObjectName("navigationSidebar")
        self.navigation_sidebar.setMinimumWidth(198)
        self.navigation_sidebar.setMaximumWidth(250)
        sidebar_layout = QVBoxLayout(self.navigation_sidebar)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(7)
        sidebar_title = QLabel("OPÉRATIONS")
        sidebar_title.setObjectName("sidebarSectionTitle")
        sidebar_layout.addWidget(sidebar_title)
        self.navigation_button_group = QButtonGroup(self)
        self.navigation_button_group.setExclusive(True)
        self.navigation_buttons: dict[str, QPushButton] = {}
        navigation_items = (
            ("sort", "Classer", "Trier et organiser", QStyle.StandardPixmap.SP_DirOpenIcon),
            ("rename", "Renommer", "Renommage par lot", QStyle.StandardPixmap.SP_FileDialogDetailedView),
            ("archive", "Archiver", "Fichiers anciens", QStyle.StandardPixmap.SP_DriveHDIcon),
            ("empty", "Dossiers vides", "Repérer avant suppression", QStyle.StandardPixmap.SP_TrashIcon),
        )
        for key, title, subtitle_text, standard_icon in navigation_items:
            button = QPushButton(f"{title}\n{subtitle_text}")
            button.setObjectName("navigationOperationButton")
            button.setCheckable(True)
            button.setIcon(self.style().standardIcon(standard_icon))
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(f"Opération : {title}")
            button.setToolTip(subtitle_text)
            button.setMinimumHeight(58)
            button.clicked.connect(
                lambda _checked=False, operation=key: self._select_operation(operation)
            )
            self.navigation_button_group.addButton(button)
            self.navigation_buttons[key] = button
            sidebar_layout.addWidget(button)
        sidebar_layout.addSpacing(10)
        separator = QFrame()
        separator.setObjectName("sidebarSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        sidebar_layout.addWidget(separator)
        history_nav_button = QPushButton("Historique\nOpérations réalisées")
        history_nav_button.setObjectName("navigationOperationButton")
        history_nav_button.setCheckable(True)
        history_nav_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        history_nav_button.setAccessibleName("Afficher l'historique des opérations")
        history_nav_button.clicked.connect(
            lambda _checked=False: self._select_operation("history")
        )
        self.navigation_button_group.addButton(history_nav_button)
        self.navigation_buttons["history"] = history_nav_button
        sidebar_layout.addWidget(history_nav_button)
        sidebar_layout.addStretch(1)
        self.navigation_hint = QLabel(
            "Choisissez une opération, puis vérifiez l’aperçu avant exécution."
        )
        self.navigation_hint.setObjectName("sidebarHint")
        self.navigation_hint.setWordWrap(True)
        sidebar_layout.addWidget(self.navigation_hint)
        self.navigation_buttons["sort"].setChecked(True)
        self._refresh_navigation_button_styles()

        workspace_row = QHBoxLayout()
        workspace_row.setContentsMargins(0, 0, 0, 0)
        workspace_row.setSpacing(10)
        workspace_row.addWidget(self.navigation_sidebar)
        workspace_row.addWidget(self.tabs, 1)

        self._navigation_shortcuts: list[QShortcut] = []
        self._install_navigation_shortcuts()

        self.activity_progress = QProgressBar()
        self.activity_progress.setObjectName("activityProgress")
        self.activity_progress.setRange(0, 0)
        self.activity_progress.setTextVisible(False)
        self.activity_progress.setFixedWidth(120)
        self.activity_progress.setVisible(False)

        self.activity_percent = QLabel("Lot : 0 %")
        self.activity_percent.setObjectName("activityPercent")
        self.activity_percent.setAccessibleName("Progression globale")
        self.activity_percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.activity_percent.setFixedWidth(118)
        self.activity_percent.setVisible(False)

        self.activity_label = QLabel("Prêt")
        self.activity_label.setObjectName("activityLabel")
        self.activity_label.setProperty("tone", "idle")

        self.cancel_analysis_button = QPushButton("Annuler l\'analyse")
        self.cancel_analysis_button.setObjectName("secondaryButton")
        self.cancel_analysis_button.setAccessibleName("Annuler l\'analyse en cours")
        self.cancel_analysis_button.setVisible(False)
        self.cancel_analysis_button.setEnabled(False)
        self.cancel_analysis_button.clicked.connect(self._cancel_active_operation)

        self.version_label = QLabel(f"File Janitor v{__version__}")
        self.version_label.setObjectName("versionLabel")
        self.security_label = QLabel(
            "Toutes les opérations sont sécurisées et annulables."
        )
        self.security_label.setObjectName("securityLabel")

        self.footer_bar = QFrame()
        self.footer_bar.setObjectName("footerBar")
        footer_layout = QHBoxLayout(self.footer_bar)
        footer_layout.setContentsMargins(14, 6, 14, 6)
        footer_layout.setSpacing(14)
        footer_layout.addWidget(self.version_label)
        footer_layout.addWidget(self.security_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.activity_progress)
        footer_layout.addWidget(self.activity_percent)
        footer_layout.addWidget(self.activity_label)
        footer_layout.addWidget(self.cancel_analysis_button)
        footer_layout.addWidget(self.undo_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.app_header)
        layout.addLayout(workspace_row, 1)
        layout.addWidget(self.footer_bar)

        central = QWidget()
        central.setObjectName("appRoot")
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._restore_preferences()

    def showEvent(self, event: QShowEvent) -> None:
        """Restaure une fois la cible Undo persistée au premier affichage."""

        super().showEvent(event)
        if not self._persistent_undo_target_loaded:
            self._restore_persistent_undo_target()

    def _restore_persistent_undo_target(self) -> None:
        """Synchronise le bouton Undo avec la dernière cible LIFO en SQLite."""

        self._persistent_undo_target_loaded = True
        try:
            run_startup_crash_recovery()
            self._last_batch_id = get_latest_undoable_batch_id()
        except Exception:
            # Fail-closed : ne jamais exposer une cible Undo si le recovery
            # ou l'accès à l'historique n'a pas pu être réconcilié.
            self._last_batch_id = None

        self.undo_button.setVisible(self._last_batch_id is not None)
        self.undo_button.setEnabled(
            self._active_worker is None and self._last_batch_id is not None
        )

    def _restore_preferences(self) -> None:
        """Restaure uniquement des préférences de présentation valides."""

        if self._preferences is None:
            return

        geometry = self._preferences.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)

        folder = self._preferences.last_folder() if self._preferences.restore_folder() else ""
        if folder and Path(folder).is_dir():
            folder_path = Path(folder)
            self.folder_edit.setText(folder)
            self._auto_select_analysis_profile(folder_path)

            stored_profile = (
                self._preferences.analysis_profile()
                if self._preferences.restore_analysis_profile()
                else ""
            )
            if stored_profile in {"standard", "remote"}:
                index = self.analysis_profile_combo.findData(stored_profile)
                if index >= 0:
                    self._analysis_profile_change_is_automatic = True
                    try:
                        self.analysis_profile_combo.setCurrentIndex(index)
                    finally:
                        self._analysis_profile_change_is_automatic = False

                    self._analysis_profile_source = (
                        folder_path.expanduser().absolute()
                    )
                    self._analysis_profile_user_override = True
                    self.analysis_profile_hint.setText(
                        "Profil d’analyse restauré depuis vos préférences."
                    )

            self._update_analyze_button()
            self._set_feedback(self.status_label, "Prêt à analyser.", "idle")

        destination = (
            self._preferences.last_destination()
            if self._preferences.restore_destination()
            else ""
        )
        if destination and Path(destination).is_dir():
            self.destination_edit.setText(destination)
            self._update_analyze_button()

        stored_mode = (
            self._preferences.classification_mode()
            if self._preferences.restore_classification_mode()
            else ""
        )
        for index in range(self.classification_mode_combo.count()):
            try:
                mode = ClassificationMode(self.classification_mode_combo.itemData(index))
            except (TypeError, ValueError):
                continue
            if mode.value == stored_mode:
                self.classification_mode_combo.setCurrentIndex(index)
                break
        stored_secondary = self._preferences.secondary_classification_mode()
        if self._preferences.restore_classification_mode() and stored_secondary:
            for index in range(1, self.secondary_mode_combo.count()):
                if self.secondary_mode_combo.itemData(index) == stored_secondary:
                    self.secondary_mode_combo.setCurrentIndex(index)
                    break

        # Chaque session démarre sur Analyse, y compris si une ancienne
        # version avait mémorisé Historique comme dernier onglet.
        self.tabs.setCurrentWidget(self.analysis_tab)

    def _open_preferences(self) -> None:
        """Ouvre les préférences GUI sans exposer de configuration métier."""

        if self._preferences is None:
            return
        dialog = PreferencesDialog(self._preferences, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.execution_mode_combo.setCurrentIndex(
                1 if self._preferences.fast_remote_move_enabled() else 0
            )

    def _save_preferences(self) -> None:
        if self._preferences is None:
            return
        if self._preferences.consume_reset_pending():
            return
        self._preferences.save(
            geometry=self.saveGeometry(),
            last_folder=self.folder_edit.text(),
            last_destination=self.destination_edit.text(),
            classification_mode=self._selected_classification_mode().value,
            secondary_classification_mode=(
                self._selected_secondary_mode().value
                if self._selected_secondary_mode() is not None else ""
            ),
            analysis_profile=self._selected_analysis_profile(),
            active_tab="analysis",
        )

    def _install_navigation_shortcuts(self) -> None:
        """Installe les raccourcis de navigation sans modifier l'état métier."""

        bindings = (
            ("Alt+1", self._focus_folder_input),
            ("Alt+2", self._focus_preview_panel),
            ("Alt+3", lambda: self._select_operation("history")),
        )
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._navigation_shortcuts.append(shortcut)

        folder_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        folder_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        folder_shortcut.activated.connect(self._focus_folder_input)
        self._navigation_shortcuts.append(folder_shortcut)

    def _focus_tab(self, tab: QWidget) -> None:
        """Affiche un onglet puis place le focus sur son contrôle principal."""

        self.tabs.setCurrentWidget(tab)
        if tab is self.history_tab:
            target = self.history_table
        elif tab is self.analysis_tab:
            target = self.folder_edit
        else:
            target = self.details_filter if self.details_filter.isVisible() else self.details_table
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _focus_preview_panel(self) -> None:
        """Place le focus dans l'aperçu sans changer de page."""

        self.tabs.setCurrentWidget(self.analysis_tab)
        target = self.details_filter if self.details_filter.isVisible() else self.details_table
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _refresh_navigation_button_styles(self) -> None:
        """Applique au menu latéral un état actif explicite et accessible."""

        for key, button in self.navigation_buttons.items():
            selected = key == self._selected_operation
            button.setProperty("navigationSelected", "true" if selected else "false")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _fit_operation_options(self) -> None:
        """Ajuste la zone d'options à la page de l'opération active."""

        page = self.operation_options_stack.currentWidget()
        layout = page.layout() if page is not None else None
        if layout is None:
            return
        layout.activate()
        width = max(self.operation_options_stack.width(), 600)
        height = layout.heightForWidth(width)
        if height < 0:
            height = layout.sizeHint().height()
        # Petite marge de sécurité pour les métriques de police et les styles
        # Qt ; le contenu reste en haut de page et le panneau peut se rétracter.
        self.operation_options_stack.setMaximumHeight(max(52, height + 12))

    def _select_operation(self, operation: str) -> None:
        """Affiche les options correspondant à l'opération choisie."""

        if operation == "history":
            self.tabs.setCurrentWidget(self.history_tab)
            self._selected_operation = "history"
            self.navigation_buttons["history"].setChecked(True)
            self.navigation_hint.setText(
                "Consultez les lots exécutés et sélectionnez-en un pour voir le détail."
            )
            self._refresh_navigation_button_styles()
            if self.history_status_label.text() == "Historique non chargé.":
                self._start_history_refresh()
            return

        if operation not in {"sort", "rename", "archive", "empty"}:
            return
        if self._active_worker is not None:
            return

        self._selected_operation = operation
        self.navigation_buttons[operation].setChecked(True)
        self.tabs.setCurrentWidget(self.analysis_tab)
        page_indexes = {"sort": 0, "rename": 1, "archive": 2, "empty": 3}
        titles = {
            "sort": "Classer les fichiers",
            "rename": "Renommer les fichiers par lot",
            "archive": "Archiver les fichiers anciens",
            "empty": "Repérer les dossiers vides",
        }
        descriptions = {
            "sort": "Choisissez une destination et les critères de classement.",
            "rename": "Définissez un modèle ; les noms seront vérifiés avant exécution.",
            "archive": "Indiquez l’ancienneté, le format et la destination de l’archive.",
            "empty": "Les sous-dossiers seront vérifiés avant toute suppression.",
        }
        self.operation_options_stack.setCurrentIndex(page_indexes[operation])
        self._fit_operation_options()
        self.folder_section_title.setText(
            "DOSSIER SOURCE" if operation == "archive" else "DOSSIER À ANALYSER"
        )
        self.workspace_operation_title.setText(titles[operation])
        self.navigation_hint.setText(descriptions[operation])
        proxy_buttons = {
            "sort": self.analyze_button,
            "rename": self.rename_button,
            "archive": self.archive_button,
            "empty": self.empty_directories_button,
        }
        proxy_buttons[operation].setChecked(True)
        button_labels = {
            "sort": "Analyser et afficher l’aperçu",
            "rename": "Prévisualiser les nouveaux noms",
            "archive": "Analyser les fichiers à archiver",
            "empty": "Rechercher les dossiers vides",
        }
        self.analyze_button.setText(button_labels[operation])
        self.analyze_button.setAccessibleName(button_labels[operation])
        self._refresh_navigation_button_styles()
        self._update_analyze_button()

    def _archive_older_than_days(self) -> int:
        """Convertit la période choisie en jours pour le moteur d'archivage."""

        value = self.archive_age_spin.value()
        unit = self.archive_age_unit_combo.currentData()
        days_per_unit = {"days": 1, "months": 365.2425 / 12, "years": 365.2425}
        return max(1, round(value * days_per_unit.get(unit, 1)))

    def _choose_archive_destination(self) -> None:
        """Choisit le dossier d'archive depuis le formulaire principal."""

        source_text = self.folder_edit.text().strip()
        start = str(Path(source_text).expanduser().parent) if source_text else str(Path.home())
        destination = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier d’archive", start
        )
        if destination:
            self.archive_destination_edit.setText(destination)

    def _start_selected_operation(self) -> None:
        """Démarre l'analyse associée à l'opération sélectionnée."""

        folder_text = self.folder_edit.text().strip()
        if not folder_text or self._active_worker is not None:
            return
        if self._selected_operation == "sort":
            self._start_analysis()
        elif self._selected_operation == "rename":
            pattern = self.rename_pattern_edit.text().strip()
            try:
                RenameTemplate.compile(pattern)
            except RenameTemplateError as exc:
                QMessageBox.warning(self, "Modèle invalide", str(exc))
                self.rename_pattern_edit.setFocus()
                return
            self._last_rename_pattern = pattern
            self._start_rename_analysis(Path(folder_text), pattern)
        elif self._selected_operation == "archive":
            destination_text = self.archive_destination_edit.text().strip()
            if not destination_text:
                self._set_feedback(
                    self.status_label,
                    "Choisissez le dossier où enregistrer l’archive.",
                    "warning",
                )
                self.archive_destination_browse_button.setFocus()
                return
            self._start_archive_analysis(
                Path(folder_text),
                Path(destination_text),
                self._archive_older_than_days(),
                (
                    ArchiveFormat.ZIP
                    if self.archive_format_zip_radio.isChecked()
                    else ArchiveFormat.FOLDERS
                ),
            )
        elif self._selected_operation == "empty":
            self._start_empty_directory_analysis()

    def _focus_folder_input(self) -> None:
        """Revient à l'analyse et sélectionne le chemin pour une saisie rapide."""

        self.tabs.setCurrentWidget(self.analysis_tab)
        self.folder_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.folder_edit.selectAll()

    def _open_selected_preview(self, _item: QTableWidgetItem) -> None:
        """Ouvre la prévisualisation du groupe de classement sélectionné."""

        if self._analysis_result is None or self.details_label.isHidden():
            return
        self.tabs.setCurrentWidget(self.preview_tab)
        if not self.details_table.isHidden():
            self.details_table.setFocus(Qt.FocusReason.TabFocusReason)

    def _resolve_duplicate_preview(self, result: AnalysisResult) -> bool:
        """Demande un keeper par groupe et mémorise la résolution sans exécuter."""

        dialog = DuplicateResolutionDialog(
            result.duplicate_groups,
            keepers=self._duplicate_keepers,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        keepers = dialog.keepers()
        required = {group.key for group in result.duplicate_groups}
        if set(keepers) != required:
            return False

        self._duplicate_keepers = keepers

        # Valider le dialogue signifie « traiter ces groupes ». La sélection
        # DUPLICATE devient atomique : toutes les clés techniques du plan sont
        # sélectionnées ensemble et la Preview résolue n'expose plus de cases
        # individuelles qui pourraient suggérer qu'un membre peut être retiré
        # isolément du traitement du groupe.
        self._selected_actions = {
            key for key in self._selected_actions if key[0] != "duplicate"
        }
        details = result.details_for("duplicate")
        if details is not None:
            for index, detail in enumerate(details.items):
                if detail.action != "none":
                    self._selected_actions.add(("duplicate", index))
        self._update_selection_controls()
        return True

    def _auto_select_analysis_profile(self, folder: Path) -> None:
        """Adapte le profil à la source sans écraser un choix manuel courant."""

        source = folder.expanduser().absolute()
        info = remote_folder_info(source)

        if (
            self._analysis_profile_source == source
            and self._analysis_profile_user_override
        ):
            fs_label = info.filesystem_type or "filesystem indéterminé"
            self.analysis_profile_hint.setText(
                f"Choix manuel conservé pour cette source ({fs_label})."
            )
            return

        self._analysis_profile_source = source
        self._analysis_profile_user_override = False

        if info.filesystem_type is None:
            current_profile = self._selected_analysis_profile()
            self.analysis_profile_hint.setText(
                "Type de filesystem indéterminé ; "
                f"profil {current_profile.capitalize()} conservé."
            )
            return

        profile = "remote" if info.is_remote else "standard"
        index = self.analysis_profile_combo.findData(profile)
        if index >= 0:
            self._analysis_profile_change_is_automatic = True
            try:
                self.analysis_profile_combo.setCurrentIndex(index)
            finally:
                self._analysis_profile_change_is_automatic = False

        if info.is_remote:
            self.analysis_profile_hint.setText(
                f"Dossier distant détecté ({info.filesystem_type}) — "
                "profil Remote / rapide sélectionné."
            )
        else:
            self.analysis_profile_hint.setText(
                f"Dossier local détecté ({info.filesystem_type}) — "
                "profil Standard sélectionné."
            )

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choisir un dossier")
        if not folder:
            return

        self.folder_edit.setText(folder)
        self._auto_select_analysis_profile(Path(folder))
        invalidated = self._invalidate_analysis_for_context_change(
            "Dossier source modifié — relancez l’analyse."
        )
        if not invalidated and self._analysis_result is None:
            self._set_feedback(self.status_label, "Prêt à analyser.", "idle")
        self._update_analyze_button()

    def _choose_destination(self) -> None:
        """Choisit une racine de destination distincte de la source."""

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier de destination",
        )
        if not folder:
            return

        self.destination_edit.setText(folder)
        if not self._invalidate_analysis_for_context_change(
            "Destination modifiée — relancez l’analyse."
        ):
            self._set_feedback(self.status_label, "Destination modifiée.", "idle")
        self._update_analyze_button()

    def _use_source_as_destination(self) -> None:
        """Revient au comportement historique : classement dans la source."""

        self.destination_edit.clear()
        if self._preferences is not None:
            self._preferences.forget_destination()
        if not self._invalidate_analysis_for_context_change(
            "Destination remplacée par la source — relancez l’analyse."
        ):
            self._set_feedback(
                self.status_label,
                "La source sera utilisée comme destination.",
                "idle",
            )
        self._update_analyze_button()

    def _invalidate_analysis_for_context_change(self, message: str) -> bool:
        """Supprime toute Preview devenue incohérente avec les contrôles."""

        if self._analysis_result is None:
            return False

        current_context = self._current_analysis_context()
        if (
            self._last_analysis_context is not None
            and current_context == self._last_analysis_context
        ):
            return False

        # Une fois la Preview invalidée, revenir ensuite aux anciennes valeurs
        # ne doit jamais la rendre implicitement valide : une nouvelle analyse
        # est obligatoire.
        self._last_analysis_context = None
        self._clear_results(clear_execution=False)
        self._set_feedback(self.status_label, message, "idle")
        return True

    def _clear_results(self, *, clear_execution: bool = True) -> None:
        self._analysis_result = None
        self._selected_actions.clear()
        self._duplicate_keepers.clear()
        self._update_selection_controls()
        self.summary_label.clear()
        self.scan_errors_label.clear()
        self.scan_errors_label.setVisible(False)
        self.scan_errors_table.clearContents()
        self.scan_errors_table.setRowCount(0)
        self.scan_errors_table.setVisible(False)
        self.classification_groups_label.clear()
        self.classification_groups_label.setVisible(False)
        self.execution_mode_label.setVisible(True)
        self.execution_mode_combo.setVisible(True)
        self.duplicate_resolution_button.setVisible(False)
        self.category_table.clearContents()
        self.category_table.setRowCount(0)
        self.category_table.setVisible(False)
        self.preview_scope_label.setVisible(False)
        self.preview_scope_combo.blockSignals(True)
        self.preview_scope_combo.clear()
        self.preview_scope_combo.blockSignals(False)
        self.preview_scope_combo.setVisible(False)
        self.details_label.clear()
        self.details_label.setVisible(False)
        self.preview_scope_summary.clear()
        self.preview_scope_summary.setVisible(False)
        self.details_filter_label.setVisible(False)
        self.details_filter.clear()
        self.details_filter.setVisible(False)
        self.details_filter_hint.setVisible(False)
        self.details_table.setMinimumHeight(220)
        self.details_table.clearContents()
        self.details_table.setRowCount(0)
        self.details_table.setVisible(False)
        self.preview_empty_label.setVisible(True)
        if clear_execution:
            self._clear_execution_result()

    def _clear_execution_result(self) -> None:
        self.execution_summary_label.clear()
        self.execution_summary_label.setVisible(False)
        self.execution_errors_table.clearContents()
        self.execution_errors_table.setRowCount(0)
        self.execution_errors_table.setVisible(False)

    def _start_worker(self, worker: Worker) -> None:
        """Conserve le worker vivant jusqu'à sa terminaison."""

        if self._closing:
            worker.cancel()
            return
        self._workers.add(worker)
        worker.signals.finished.connect(
            lambda worker=worker: self._workers.discard(worker)
        )
        self._thread_pool.start(worker)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Sauvegarde la présentation puis désactive les publications tardives."""

        self._save_preferences()
        self._closing = True
        for worker in tuple(self._workers):
            worker.cancel()
        self._workers.clear()
        self._active_worker = None
        self._history_operation_worker = None
        super().closeEvent(event)

    @staticmethod
    def _set_feedback(label: QLabel, text: str, tone: str) -> None:
        """Met à jour un message d'état et son niveau visuel."""

        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def _set_activity(self, busy: bool, text: str = "Prêt", tone: str = "idle") -> None:
        """Affiche l'activité globale sans bloquer le thread d'interface."""

        if busy and self._active_operation != "execution":
            self.activity_progress.setRange(0, 0)
            self.activity_progress.setTextVisible(False)
        self.activity_progress.setVisible(busy)
        self.activity_percent.setVisible(busy and self._active_operation == "execution")
        self.security_label.setVisible(not busy)
        self.activity_label.setText(text)
        self.activity_label.setProperty("tone", tone)
        self.activity_label.style().unpolish(self.activity_label)
        self.activity_label.style().polish(self.activity_label)

    def _analysis_elapsed_text(self) -> str:
        """Temps écoulé de l'analyse active au format HH:MM:SS."""

        if self._analysis_started_at is None:
            seconds = 0
        else:
            seconds = max(0, int(monotonic() - self._analysis_started_at))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _analysis_activity_text(self) -> str:
        phase = self._analysis_activity_phase or "Analyse en cours"
        return f"{phase} — {self._analysis_elapsed_text()}"

    def _start_analysis_clock(self, phase: str) -> None:
        self._analysis_started_at = monotonic()
        self._analysis_activity_phase = phase
        self._analysis_elapsed_timer.start()
        self._set_activity(True, self._analysis_activity_text(), "busy")

    def _set_analysis_activity_phase(self, phase: str) -> None:
        self._analysis_activity_phase = phase
        self._set_activity(True, self._analysis_activity_text(), "busy")

    def _update_analysis_elapsed(self) -> None:
        if self._analysis_started_at is None:
            return
        self._set_activity(True, self._analysis_activity_text(), "busy")

    def _stop_analysis_clock(self) -> str:
        elapsed = self._analysis_elapsed_text()
        self._analysis_elapsed_timer.stop()
        self._analysis_started_at = None
        self._analysis_activity_phase = ""
        return elapsed

    def _set_busy(self, busy: bool) -> None:
        """Synchronise les contrôles interactifs avec l'opération principale."""

        interactive = not busy
        self.browse_button.setEnabled(interactive)
        self.destination_browse_button.setEnabled(interactive)
        self.destination_source_button.setEnabled(interactive)
        self.preferences_button.setEnabled(interactive)
        self.classification_mode_combo.setEnabled(interactive)
        self.analysis_profile_combo.setEnabled(interactive)
        self._update_analyze_button(interactive=interactive)
        self.category_table.setEnabled(interactive)
        self.details_filter.setEnabled(interactive)
        self.select_all_button.setEnabled(interactive)
        self.clear_selection_button.setEnabled(interactive)
        self.execute_button.setEnabled(interactive and bool(self._selected_actions))
        self.execution_mode_combo.setEnabled(interactive)
        self.undo_button.setEnabled(
            interactive and self._last_batch_id is not None
        )
        self.history_button.setEnabled(interactive)
        self.history_table.setEnabled(interactive)
        self.resolve_recovery_button.setEnabled(
            interactive and self.resolve_recovery_button.isVisible()
        )
        self.resume_queued_button.setEnabled(
            interactive
            and self.resume_queued_button.isVisible()
            and bool(self.resume_queued_button.property("resumeReady"))
        )
        if not busy:
            self.cancel_analysis_button.setVisible(False)
            self.cancel_analysis_button.setEnabled(False)
        if not busy and self.activity_progress.isVisible():
            self._set_activity(False)
        if not busy:
            self._update_selection_controls()

    def _current_analysis_context(
        self,
    ) -> tuple[Path, Path, ClassificationMode, ClassificationMode | None, str] | None:
        """Retourne source/destination/mode/profil qui déterminent la validité."""

        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            return None

        source = Path(folder_text).expanduser().absolute()
        destination_text = self.destination_edit.text().strip()
        destination = (
            Path(destination_text).expanduser().absolute()
            if destination_text
            else source
        )
        return (
            source,
            destination,
            self._selected_classification_mode(),
            self._selected_secondary_mode(),
            self._selected_analysis_profile(),
        )

    def _update_analyze_button(self, *, interactive: bool | None = None) -> None:
        """Active l'analyse générale si elle peut produire un nouveau résultat."""

        if interactive is None:
            interactive = self._active_worker is None
        current_context = self._current_analysis_context()
        specialized_preview = self._analysis_result is not None and any(
            self._analysis_result.details_for(key) is not None
            for key in ("rename", "archive", "empty_directory")
        )
        can_reanalyze = (
            current_context is not None
            and (
                current_context != self._last_analysis_context
                or specialized_preview
            )
        )
        self.analyze_button.setEnabled(
            interactive
            and (
                can_reanalyze
                if self._selected_operation == "sort"
                else bool(self.folder_edit.text().strip())
            )
        )
        self.rename_button.setEnabled(
            interactive and bool(self.folder_edit.text().strip())
        )
        self.archive_button.setEnabled(
            interactive and bool(self.folder_edit.text().strip())
        )
        self.empty_directories_button.setEnabled(
            interactive and bool(self.folder_edit.text().strip())
        )

    def _refresh_analysis_action_button_styles(self, *_args: object) -> None:
        """Repasse explicitement l'état choisi au thème Qt des boutons."""

        for button in (
            self.analyze_button,
            self.rename_button,
            self.archive_button,
            self.empty_directories_button,
        ):
            button.setProperty("analysisAction", "true")
            button.setProperty(
                "analysisSelected",
                "true" if button.isChecked() else "false",
            )
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _selected_classification_mode(self) -> ClassificationMode:
        """Retourne le mode de classement sélectionné dans l'interface."""

        mode = self.classification_mode_combo.currentData()
        try:
            return ClassificationMode(mode)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("mode de classement GUI invalide") from exc

    def _selected_secondary_mode(self) -> ClassificationMode | None:
        mode = self.secondary_mode_combo.currentData()
        if mode is None:
            return None
        try:
            return ClassificationMode(mode)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("second critère de classement invalide") from exc

    def _selected_analysis_profile(self) -> str:
        """Retourne le profil d'analyse sélectionné dans l'interface."""

        profile = self.analysis_profile_combo.currentData()
        if profile not in {"standard", "remote"}:
            raise RuntimeError("profil d'analyse GUI invalide")
        return profile

    def _analysis_compute_hashes(self) -> bool:
        """Indique si l'analyse doit lire intégralement les candidats doublons."""

        return self._selected_analysis_profile() == "standard"

    def _analysis_compute_content_type(self) -> bool:
        """Indique si l'analyse doit lire les magic bytes des fichiers."""

        return self._selected_analysis_profile() == "standard"

    def _analysis_profile_changed(self) -> None:
        """Explique le profil et mémorise un choix manuel pour la source courante."""

        if (
            not self._analysis_profile_change_is_automatic
            and self._analysis_profile_source is not None
        ):
            self._analysis_profile_user_override = True

        if self._selected_analysis_profile() == "remote":
            self.analysis_profile_hint.setText(
                "Remote / rapide : recommandé pour clouds/FUSE ; évite le hachage "
                "et la lecture du contenu. Les doublons et extensions trompeuses "
                "ne sont pas détectés."
            )
        else:
            self.analysis_profile_hint.setText(
                "Standard : analyse complète avec détection par contenu."
            )
        self._invalidate_analysis_for_context_change(
            "Profil d’analyse modifié — relancez l’analyse."
        )
        self._update_analyze_button()

    def _classification_mode_changed(self) -> None:
        """Explique la stratégie sélectionnée sans dupliquer sa logique métier."""

        hints = {
            ClassificationMode.EXTENSION: (
                "Classe les fichiers dans des dossiers correspondant à leur extension."
            ),
            ClassificationMode.DATE: (
                "Classe les fichiers par mois de modification (AAAA-MM)."
            ),
            ClassificationMode.COMMON_NAME: (
                "Regroupe uniquement les fichiers partageant une racine de nom "
                "commune ; les fichiers isolés restent non classés."
            ),
            ClassificationMode.SIZE: (
                "Classe les fichiers par tranches : <10 Ko, 10–50 Ko, 50–100 Ko, "
                "100–500 Ko, 500 Ko–1 Mo, puis 1–10, 10–100, 100–500 Mo "
                "et 500 Mo et plus."
            ),
        }
        primary = self._selected_classification_mode()
        secondary = self._selected_secondary_mode()
        if secondary is primary:
            self.secondary_mode_combo.setCurrentIndex(0)
            secondary = None
        hint = hints[primary]
        if secondary is not None:
            hint += f" Puis : {hints[secondary]} Chaque fichier est déplacé directement dans le dossier final."
        self.classification_mode_hint.setText(hint)
        self._invalidate_analysis_for_context_change(
            "Mode de classement modifié — relancez l’analyse."
        )
        self._update_analyze_button()

    def _confirm_slow_remote_analysis(self, folder: Path) -> bool:
        """Demande confirmation uniquement pour Standard sur stockage distant."""

        info = remote_folder_info(folder)
        profile = self._selected_analysis_profile()
        if not info.is_remote or profile != "standard":
            return True

        fs_label = info.filesystem_type or "filesystem distant"
        title = "Analyse Standard sur dossier distant"
        message = (
            f"Le dossier sélectionné se trouve sur un stockage distant "
            f"({fs_label}).\n\n"
            "Le profil Standard lit le contenu des fichiers et peut être "
            "nettement plus lent sur un cloud ou un montage réseau.\n\n"
            "Le profil « Remote / rapide » évite le hachage et la détection "
            "par contenu.\n\n"
            "Continuer malgré tout en profil Standard ?"
        )

        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _start_analysis(self) -> None:
        folder_text = self.folder_edit.text()
        if not folder_text or self._active_worker is not None:
            return

        folder = Path(folder_text)
        profile = self._selected_analysis_profile()
        profile_label = self._analysis_profile_label(profile)
        if not self._confirm_slow_remote_analysis(folder):
            self._set_feedback(
                self.status_label,
                f"Analyse {profile_label} annulée avant démarrage.",
                "idle",
            )
            return

        self._active_analysis_profile = profile
        classification_mode = self._selected_classification_mode()
        metadata_required = classification_mode in {
            ClassificationMode.DATE,
            ClassificationMode.SIZE,
        } or self._selected_secondary_mode() in {ClassificationMode.DATE, ClassificationMode.SIZE}
        if profile == "remote" and metadata_required:
            start_message = f"Lecture des métadonnées · {profile_label}…"
        else:
            start_message = f"Analyse {profile_label} en cours…"
        self._set_busy(True)
        self._set_feedback(self.status_label, start_message, "busy")
        self._start_analysis_clock(start_message)
        self._clear_results()

        destination_text = self.destination_edit.text().strip()
        destination_root = (
            Path(destination_text) if destination_text else None
        )
        config = classification_plan_config(
            classification_mode,
            destination_root=destination_root,
            secondary_mode=self._selected_secondary_mode(),
        )
        worker_kwargs = dict(
            config=config,
            compute_hashes=self._analysis_compute_hashes(),
            compute_content_type=self._analysis_compute_content_type(),
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
        )
        # Préserver le contrat historique de ``analyze_folder`` pour Standard
        # et pour les profils qui exigent déjà les métadonnées. Le nouveau
        # kwarg n'est nécessaire que pour activer explicitement le scan light.
        if profile == "remote" and not metadata_required:
            worker_kwargs["collect_metadata"] = False

        worker = Worker(
            analyze_folder,
            folder,
            **worker_kwargs,
        )
        self._active_worker = worker
        self._active_operation = "analysis"
        self.cancel_analysis_button.setText("Annuler l’analyse")
        self.cancel_analysis_button.setAccessibleName("Annuler l’analyse en cours")
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._analysis_succeeded)
        worker.signals.error.connect(self._analysis_failed)
        worker.signals.progress.connect(self._analysis_progress)
        worker.signals.cancelled.connect(self._analysis_cancelled)
        worker.signals.finished.connect(self._analysis_finished)
        self._start_worker(worker)

    def _open_rename_dialog(self) -> None:
        """Demande un modèle puis prépare la preview sans modifier les fichiers."""

        folder_text = self.folder_edit.text().strip()
        if not folder_text or self._active_worker is not None:
            return
        pattern, accepted = QInputDialog.getText(
            self,
            "Renommage par lot",
            "Modèle de nom — champs : {name}, {ext}, {parent}, {date}, "
            "{counter} (format possible : {counter:03d}).\n"
            "Le renommage s’applique aussi aux sous-dossiers. Aucun fichier "
            "n’est modifié avant validation de la prévisualisation. Le mode "
            "rapide rclone/FUSE n’est pas utilisé pour le renommage par lot.",
            QLineEdit.EchoMode.Normal,
            self._last_rename_pattern,
        )
        if not accepted:
            return
        try:
            RenameTemplate.compile(pattern)
        except RenameTemplateError as exc:
            QMessageBox.warning(self, "Modèle invalide", str(exc))
            return

        self._last_rename_pattern = pattern
        self._start_rename_analysis(Path(folder_text), pattern)

    def _start_rename_analysis(self, folder: Path, pattern: str) -> None:
        """Lance le scan de renommage dans le pool de threads de la fenêtre."""

        self._active_analysis_profile = "rename"
        self._set_busy(True)
        start_message = "Préparation de la prévisualisation des renommages…"
        self._set_feedback(self.status_label, start_message, "busy")
        self._start_analysis_clock(start_message)
        self._clear_results()

        worker = Worker(
            analyze_rename_folder,
            folder,
            pattern,
            recursive=True,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
        )
        self._active_worker = worker
        self._active_operation = "analysis"
        self.cancel_analysis_button.setText("Annuler le renommage")
        self.cancel_analysis_button.setAccessibleName(
            "Annuler la préparation du renommage"
        )
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._analysis_succeeded)
        worker.signals.error.connect(self._analysis_failed)
        worker.signals.progress.connect(self._analysis_progress)
        worker.signals.cancelled.connect(self._analysis_cancelled)
        worker.signals.finished.connect(self._analysis_finished)
        self._start_worker(worker)

    def _open_archive_dialog(self) -> None:
        """Choisit l'ancienneté, le format et la destination d'archive."""

        folder_text = self.folder_edit.text().strip()
        if not folder_text or self._active_worker is not None:
            return
        age, accepted = QInputDialog.getInt(
            self,
            "Archivage selon l’ancienneté",
            "Archiver les fichiers modifiés depuis au moins combien de jours ?",
            365,
            1,
            365000,
        )
        if not accepted:
            return
        format_label, accepted = QInputDialog.getItem(
            self,
            "Format de l’archive",
            "Choisir le format de sortie :",
            [
                "Dossiers conservés (arborescence actuelle)",
                "ZIP compressé (arborescence conservée dans le ZIP)",
            ],
            0,
            False,
        )
        if not accepted:
            return
        archive_format = (
            ArchiveFormat.ZIP
            if format_label.startswith("ZIP")
            else ArchiveFormat.FOLDERS
        )
        destination_text = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier d’archive",
            str(Path(folder_text).expanduser().parent),
        )
        if not destination_text:
            return
        self._start_archive_analysis(
            Path(folder_text), Path(destination_text), age, archive_format
        )

    def _start_archive_analysis(
        self,
        folder: Path,
        destination: Path,
        older_than_days: int,
        archive_format: ArchiveFormat = ArchiveFormat.FOLDERS,
    ) -> None:
        """Lance l’analyse d’ancienneté dans le pool de threads."""

        self._active_analysis_profile = "archive"
        self._set_busy(True)
        start_message = "Préparation de la prévisualisation de l’archivage…"
        self._set_feedback(self.status_label, start_message, "busy")
        self._start_analysis_clock(start_message)
        self._clear_results()
        worker = Worker(
            analyze_archive_folder,
            folder,
            destination,
            older_than_days,
            archive_format=archive_format,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
        )
        self._active_worker = worker
        self._active_operation = "analysis"
        self.cancel_analysis_button.setText("Annuler l’archivage")
        self.cancel_analysis_button.setAccessibleName(
            "Annuler la préparation de l’archivage"
        )
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._analysis_succeeded)
        worker.signals.error.connect(self._analysis_failed)
        worker.signals.progress.connect(self._analysis_progress)
        worker.signals.cancelled.connect(self._analysis_cancelled)
        worker.signals.finished.connect(self._analysis_finished)
        self._start_worker(worker)

    def _start_empty_directory_analysis(self) -> None:
        """Lance le repérage des dossiers vides, sans suppression."""

        folder_text = self.folder_edit.text().strip()
        if not folder_text or self._active_worker is not None:
            return
        self._active_analysis_profile = "empty_directories"
        self._set_busy(True)
        start_message = "Repérage des dossiers vides…"
        self._set_feedback(self.status_label, start_message, "busy")
        self._start_analysis_clock(start_message)
        self._clear_results()
        worker = Worker(
            analyze_empty_directories,
            Path(folder_text),
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
        )
        self._active_worker = worker
        self._active_operation = "analysis"
        self.cancel_analysis_button.setText("Annuler le repérage")
        self.cancel_analysis_button.setAccessibleName(
            "Annuler le repérage des dossiers vides"
        )
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._analysis_succeeded)
        worker.signals.error.connect(self._analysis_failed)
        worker.signals.progress.connect(self._analysis_progress)
        worker.signals.cancelled.connect(self._analysis_cancelled)
        worker.signals.finished.connect(self._analysis_finished)
        self._start_worker(worker)

    def _cancel_active_operation(self) -> None:
        """Route le bouton d'arrêt vers l'opération principale active."""

        if self._active_operation == "execution":
            self._cancel_execution()
            return
        self._cancel_analysis()

    def _cancel_analysis(self) -> None:
        """Demande l’arrêt coopératif de l’analyse active."""

        worker = self._active_worker
        if worker is None:
            return
        self.cancel_analysis_button.setEnabled(False)
        profile_label = self._analysis_profile_label(self._active_analysis_profile)
        message = f"Annulation de l'analyse {profile_label} demandée…"
        self._set_feedback(self.status_label, message, "busy")
        self._set_analysis_activity_phase(message)
        worker.request_cancel()

    def _analysis_cancelled(self) -> None:
        """Restaure l’interface après une analyse annulée."""

        profile_label = self._analysis_profile_label(self._active_analysis_profile)
        self._clear_results()
        elapsed = self._stop_analysis_clock()
        self._set_feedback(
            self.status_label,
            f"Analyse {profile_label} annulée.",
            "warning",
        )
        self._set_activity(
            False,
            f"Analyse {profile_label} annulée — {elapsed}",
            "warning",
        )

    @staticmethod
    def _analysis_profile_label(profile: str | None) -> str:
        """Retourne le libellé du profil figé pour l'analyse active."""

        if profile == "rename":
            return "Renommage par lot"
        if profile == "archive":
            return "Archivage selon l’ancienneté"
        if profile == "empty_directories":
            return "Repérage des dossiers vides"
        return "Remote / rapide" if profile == "remote" else "Standard"

    def _analysis_progress(self, stage: object) -> None:
        """Affiche la phase courante ; la barre d'état montre le temps écoulé."""

        stage_key = stage[0] if isinstance(stage, tuple) and stage else stage
        messages = {
            "remote_listing": "Inventaire cloud via rclone",
            "metadata": "Lecture des métadonnées",
            "hashes": "Recherche des doublons",
            "content": "Détection des types de contenu",
            "planning": (
                "Préparation des noms proposés"
                if self._active_analysis_profile == "rename"
                else (
                    "Sélection des fichiers anciens"
                    if self._active_analysis_profile == "archive"
                    else (
                        "Repérage des dossiers vides"
                        if self._active_analysis_profile == "empty_directories"
                        else "Préparation du plan"
                    )
                )
            ),
            "empty_directories": "Parcours des dossiers",
            "result": "Préparation des résultats",
        }
        base = messages.get(str(stage_key))
        if base is None:
            return

        if stage_key == "remote_listing" and isinstance(stage, tuple) and len(stage) > 1:
            base += f" · {human_count(int(stage[1]))} fichiers repérés"
        elif stage_key == "empty_directories" and isinstance(stage, tuple) and len(stage) > 1:
            base += f" · {human_count(int(stage[1]))} dossiers parcourus"

        profile_label = self._analysis_profile_label(self._active_analysis_profile)
        message = f"{base} · {profile_label}…"
        self._set_feedback(self.status_label, message, "busy")
        self._set_analysis_activity_phase(message)

    def _analysis_succeeded(self, result: object) -> None:
        if not isinstance(result, AnalysisResult):
            raise TypeError("résultat d'analyse applicatif invalide")

        profile_label = self._analysis_profile_label(self._active_analysis_profile)
        elapsed = self._stop_analysis_clock()
        if result.scan_errors:
            error_count = len(result.scan_errors)
            error_word = "erreur" if error_count == 1 else "erreurs"
            status_text = (
                f"Analyse {profile_label} terminée avec "
                f"{human_count(error_count)} {error_word} de scan — "
                "résultat partiel."
            )
            self._set_feedback(self.status_label, status_text, "warning")
            self._set_activity(
                False,
                f"Analyse {profile_label} partielle — "
                f"{human_count(error_count)} {error_word} — {elapsed}",
                "warning",
            )
        else:
            self._set_feedback(
                self.status_label, f"Analyse {profile_label} terminée.", "success"
            )
            self._set_activity(
                False, f"Analyse {profile_label} terminée — {elapsed}", "success"
            )
        self._last_analysis_context = self._current_analysis_context()
        self._show_result(result)
        self._update_analyze_button(interactive=False)

    @staticmethod
    def _analysis_category_available(
        result: AnalysisResult,
        category_key: str,
    ) -> bool:
        """Indique si une catégorie dépend d'une capacité réellement calculée."""

        if category_key == "duplicate":
            return result.capabilities.hashes_computed
        if category_key == "extension_mismatch":
            return result.capabilities.content_types_computed
        if category_key in {"old_archive", "large_file", "old_file"}:
            return result.capabilities.metadata_computed
        return True

    def _show_result(self, result: AnalysisResult) -> None:
        self._analysis_result = result
        self.execution_mode_label.setVisible(True)
        self.execution_mode_combo.setVisible(True)
        summary = result.summary
        if result.details_for("empty_directory") is not None:
            summary_text = (
                f"{human_count(summary.total_count)} dossier(s) vide(s) proposé(s)"
            )
        elif result.capabilities.metadata_computed:
            summary_text = (
                f"{human_count(summary.total_count)} fichiers — "
                f"{human_size(summary.total_size)}"
            )
        else:
            summary_text = (
                f"{human_count(summary.total_count)} fichiers — "
                "taille non calculée (Remote / rapide)"
            )
        self.summary_label.setText(summary_text)
        self._show_scan_errors(result.scan_errors)

        rename_details = result.details_for("rename")
        if rename_details is not None:
            count = len(rename_details.items)
            self.classification_groups_label.setText(
                "Renommage par lot — vérifiez les noms proposés et les collisions."
            )
            self.classification_groups_label.setToolTip(
                "Les collisions et les noms inchangés ne sont pas sélectionnables. "
                "Une cible occupée après la prévisualisation sera aussi refusée "
                "au moment de l’exécution."
            )
            self.classification_groups_label.setVisible(True)
            self.category_table.setRowCount(1 if count else 0)
            if count:
                label_item = QTableWidgetItem("Noms proposés")
                label_item.setData(Qt.ItemDataRole.UserRole, "rename")
                self.category_table.setItem(0, 0, label_item)
                self.category_table.setItem(
                    0, 1, QTableWidgetItem(human_count(count))
                )
            self.category_table.setVisible(bool(count))
            self.preview_scope_combo.blockSignals(True)
            self.preview_scope_combo.clear()
            if count:
                self.preview_scope_combo.addItem(
                    f"Renommage par lot ({human_count(count)})", "rename"
                )
            self.preview_scope_combo.blockSignals(False)
            self.preview_scope_label.setVisible(bool(count))
            self.preview_scope_combo.setVisible(bool(count))
            self.duplicate_resolution_button.setVisible(False)
            self.execution_mode_label.setVisible(False)
            self.execution_mode_combo.setVisible(False)
            if count:
                self._select_preview_category(0)
            else:
                self._clear_details()
            return

        archive_details = result.details_for("archive")
        if archive_details is not None:
            count = len(archive_details.items)
            self.classification_groups_label.setText(
                "Archivage selon l’ancienneté — vérifiez les destinations et les conflits."
            )
            self.classification_groups_label.setToolTip(
                "Les fichiers sont déplacés en conservant leur arborescence. "
                "Toute destination déjà présente est bloquée et ne sera pas écrasée."
            )
            self.classification_groups_label.setVisible(True)
            self.category_table.setRowCount(1 if count else 0)
            if count:
                label_item = QTableWidgetItem("Fichiers anciens")
                label_item.setData(Qt.ItemDataRole.UserRole, "archive")
                self.category_table.setItem(0, 0, label_item)
                self.category_table.setItem(0, 1, QTableWidgetItem(human_count(count)))
            self.category_table.setVisible(bool(count))
            self.preview_scope_combo.blockSignals(True)
            self.preview_scope_combo.clear()
            if count:
                self.preview_scope_combo.addItem(
                    f"Archivage selon l’ancienneté ({human_count(count)})", "archive"
                )
            self.preview_scope_combo.blockSignals(False)
            self.preview_scope_label.setVisible(bool(count))
            self.preview_scope_combo.setVisible(bool(count))
            self.duplicate_resolution_button.setVisible(False)
            self.execution_mode_label.setVisible(True)
            self.execution_mode_combo.setVisible(True)
            if count:
                self._select_preview_category(0)
            else:
                self._clear_details()
            return

        empty_details = result.details_for("empty_directory")
        if empty_details is not None:
            count = len(empty_details.items)
            self.classification_groups_label.setText(
                "Suppression des dossiers vides — les sous-dossiers sont traités avant leurs parents."
            )
            self.classification_groups_label.setToolTip(
                "Seuls les dossiers réellement vides sont supprimés. Si un fichier "
                "apparaît après l’analyse, rmdir refuse le dossier et conserve son contenu. "
                "L’annulation du batch recrée les dossiers supprimés."
            )
            self.classification_groups_label.setVisible(True)
            self.category_table.setRowCount(1 if count else 0)
            if count:
                label_item = QTableWidgetItem("Dossiers vides")
                label_item.setData(Qt.ItemDataRole.UserRole, "empty_directory")
                self.category_table.setItem(0, 0, label_item)
                self.category_table.setItem(0, 1, QTableWidgetItem(human_count(count)))
            self.category_table.setVisible(bool(count))
            self.preview_scope_combo.blockSignals(True)
            self.preview_scope_combo.clear()
            if count:
                self.preview_scope_combo.addItem(
                    f"Dossiers vides ({human_count(count)})", "empty_directory"
                )
            self.preview_scope_combo.blockSignals(False)
            # Une seule catégorie est disponible ici : masquer son sélecteur
            # libère une ligne pour les messages et le tableau d'actions.
            self.preview_scope_label.setVisible(False)
            self.preview_scope_combo.setVisible(False)
            self.duplicate_resolution_button.setVisible(False)
            self.execution_mode_label.setVisible(False)
            self.execution_mode_combo.setVisible(False)
            if count:
                self._select_preview_category(0)
            else:
                self._clear_details()
            return

        mode = self._selected_classification_mode()
        secondary = self._selected_secondary_mode()
        groups = summarize_classification_groups(result, mode, secondary)
        mode_labels = {
            ClassificationMode.EXTENSION: "Extensions trouvées",
            ClassificationMode.DATE: "Mois trouvés",
            ClassificationMode.COMMON_NAME: "Racines communes trouvées",
            ClassificationMode.SIZE: "Tranches de taille trouvées",
        }
        self.classification_groups_label.setText(
            f"Groupes : {self.classification_mode_combo.currentText()} → "
            f"{self.secondary_mode_combo.currentText()}"
            if secondary else mode_labels[mode]
        )
        self.classification_groups_label.setToolTip(
            "Chaque ligne correspond à un dossier de destination proposé "
            "par le mode de classement actif."
        )
        self.classification_groups_label.setVisible(True)

        classification_details = result.details_for("to_sort")
        classified_count = (
            sum(
                1
                for item in classification_details.items
                if item.destination is not None
            )
            if classification_details is not None
            else 0
        )

        row_count = len(groups) + (1 if classified_count else 0)
        self.category_table.setRowCount(row_count)

        group_row_offset = 0
        if classified_count:
            all_item = QTableWidgetItem("Tous les fichiers classés")
            all_item.setData(
                Qt.ItemDataRole.UserRole,
                "classification_all",
            )
            all_item.setToolTip(
                "Afficher toutes les actions proposées par le mode "
                "de classement actif."
            )
            self.category_table.setItem(0, 0, all_item)
            self.category_table.setItem(
                0,
                1,
                QTableWidgetItem(human_count(classified_count)),
            )
            group_row_offset = 1

        for group_index, group in enumerate(groups):
            row = group_index + group_row_offset
            label_item = QTableWidgetItem(group.name)
            label_item.setData(
                Qt.ItemDataRole.UserRole,
                f"classification_group:{group.name}",
            )
            self.category_table.setItem(row, 0, label_item)
            self.category_table.setItem(
                row, 1, QTableWidgetItem(human_count(group.item_count))
            )

        self.category_table.setVisible(bool(row_count))

        self.preview_scope_combo.blockSignals(True)
        self.preview_scope_combo.clear()
        if classified_count:
            self.preview_scope_combo.addItem(
                f"Tous les fichiers classés ({human_count(classified_count)})",
                "classification_all",
            )
        for group in groups:
            self.preview_scope_combo.addItem(
                f"{group.name} ({human_count(group.item_count)})",
                f"classification_group:{group.name}",
            )
        self.preview_scope_combo.blockSignals(False)
        self.preview_scope_label.setVisible(bool(row_count))
        self.preview_scope_combo.setVisible(bool(row_count))

        if row_count:
            # Vue d'ensemble par défaut. Le sélecteur de Prévisualisation et
            # la table Analyse restent synchronisés.
            self._select_preview_category(0)
        else:
            self._clear_details()

        duplicate_count = sum(
            max(0, len(group.members) - 1)
            for group in result.duplicate_groups
        )
        self.duplicate_resolution_button.setText(
            f"Résoudre les doublons… ({human_count(duplicate_count)})"
        )
        self.duplicate_resolution_button.setVisible(
            result.capabilities.hashes_computed and duplicate_count > 0
        )

    def _show_scan_errors(self, errors: tuple[str, ...]) -> None:
        """Rend visible toute analyse partielle sans bloquer son exploitation."""

        if not errors:
            self.scan_errors_label.clear()
            self.scan_errors_label.setVisible(False)
            self.scan_errors_table.clearContents()
            self.scan_errors_table.setRowCount(0)
            self.scan_errors_table.setVisible(False)
            return

        error_count = len(errors)
        error_word = "erreur" if error_count == 1 else "erreurs"
        self.scan_errors_label.setText(
            f"Analyse partielle : {human_count(error_count)} {error_word} "
            "de scan. Certaines entrées peuvent être absentes du résultat."
        )
        self.scan_errors_label.setVisible(True)

        self.scan_errors_table.clearContents()
        self.scan_errors_table.setRowCount(error_count)
        for row, message in enumerate(errors):
            item = QTableWidgetItem(message)
            item.setToolTip(message)
            self.scan_errors_table.setItem(row, 0, item)
        self.scan_errors_table.resizeRowsToContents()
        self.scan_errors_table.setVisible(True)

    def _open_duplicate_resolution(self) -> None:
        """Ouvre le workflow doublons indépendamment du classement courant."""

        result = self._analysis_result
        if result is None or not result.duplicate_groups:
            return
        if not self._resolve_duplicate_preview(result):
            return
        details = result.details_for("duplicate")
        if details is not None:
            self.details_label.setText(
                f"Prévisualisation — Doublons "
                f"({human_count(len(details.items))} éléments)"
            )
        self.tabs.setCurrentWidget(self.preview_tab)
        self._show_duplicate_details()

    def _resolved_duplicate_groups(self):
        """Retourne la résolution courante utilisée par Preview et Execute."""

        result = self._analysis_result
        if result is None:
            return ()
        groups = duplicate_groups_for_selection(
            result, set(self._selected_actions)
        )
        if not groups:
            return ()
        required = {group.key for group in groups}
        if not required.issubset(self._duplicate_keepers):
            return ()
        keepers = {key: self._duplicate_keepers[key] for key in required}
        return resolve_duplicate_groups_for_selection(
            result,
            set(self._selected_actions),
            keepers,
        )

    def _show_duplicate_details(self) -> None:
        """Affiche le plan physique résolu du workflow Doublons."""

        resolved_groups = self._resolved_duplicate_groups()
        if not resolved_groups:
            self._clear_details()
            return

        preview_items = tuple(
            item
            for group in resolved_groups
            for item in group.preview_items
        )
        self._populate_details_table(
            tuple(enumerate(preview_items)),
            category_key="duplicate",
            label="Doublons résolus",
            selectable=False,
        )

    def _populate_details_table(
        self,
        indexed_items: tuple[tuple[int, object], ...],
        *,
        category_key: str,
        label: str,
        selectable: bool = True,
    ) -> None:
        """Peuple la prévisualisation avec des indices d'actions stables."""

        result = self._analysis_result
        if result is None:
            return
        self._details_category_key = category_key
        self._details_scope_label = label
        self.details_label.setText(
            f"Prévisualisation — {label} "
            f"({human_count(len(indexed_items))} élément"
            f"{'s' if len(indexed_items) != 1 else ''})"
        )
        self.details_label.setVisible(True)
        self.details_filter.clear()
        has_items = bool(indexed_items)
        is_empty_directory = category_key == "empty_directory"
        self.preview_scope_summary.setVisible(has_items)
        self.details_filter_label.setVisible(has_items and not is_empty_directory)
        self.details_filter.setVisible(has_items)
        self.details_filter_hint.setVisible(has_items and not is_empty_directory)
        self.details_table.setMinimumHeight(140 if is_empty_directory else 220)
        self.details_table.blockSignals(True)
        self.details_table.setRowCount(len(indexed_items))
        for detail_row, (action_index, item) in enumerate(indexed_items):
            # QTableWidget peut conserver l'état hidden d'un index de ligne
            # réutilisé après un filtre précédent. Une nouvelle
            # prévisualisation doit repartir entièrement visible ; seul le
            # filtre courant a ensuite le droit de masquer des lignes.
            self.details_table.setRowHidden(detail_row, False)
            destination = str(item.destination) if item.destination else "—"
            action_label = {
                "move": "Déplacer",
                "copy": "Copier",
                "trash": "Corbeille",
                "keep": "Conserver",
                "none": "Aucune",
                "rmdir": "Supprimer le dossier",
            }.get(item.action, item.action)
            if category_key == "rename" and item.action == "move":
                action_label = "Renommer"
            elif category_key == "archive" and item.action == "move":
                action_label = (
                    "Inclure dans ZIP"
                    if item.archive_format == ArchiveFormat.ZIP.value
                    else "Archiver"
                )
            selection_item = QTableWidgetItem()
            selection_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            selection_item.setData(
                Qt.ItemDataRole.UserRole, (category_key, action_index)
            )
            if not selectable:
                selection_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                selection_item.setToolTip(
                    "Le groupe de doublons est une décision atomique : "
                    "le keeper et les autres membres sont traités ensemble."
                )
            elif item.action == "none":
                selection_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            else:
                selection_item.setToolTip(
                    "Cocher pour inclure ce fichier dans la sélection à exécuter."
                )
                selection_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                selection_item.setCheckState(
                    Qt.CheckState.Checked
                    if (category_key, action_index) in self._selected_actions
                    else Qt.CheckState.Unchecked
                )
            self.details_table.setItem(detail_row, 0, selection_item)
            self.details_table.setItem(detail_row, 1, QTableWidgetItem(action_label))
            self.details_table.setItem(detail_row, 2, QTableWidgetItem(str(item.path)))
            size_text = (
                "—"
                if category_key == "empty_directory"
                else (
                    human_size(item.size)
                    if result.capabilities.metadata_computed
                    else "Non calculée"
                )
            )
            self.details_table.setItem(detail_row, 3, QTableWidgetItem(size_text))
            destination_item = QTableWidgetItem(destination)
            reason_text = item.reason
            if item.conflict_reason:
                destination_item.setToolTip(item.conflict_reason)
                reason_text = f"{reason_text} · {item.conflict_reason}"
            self.details_table.setItem(detail_row, 4, destination_item)
            self.details_table.setItem(detail_row, 5, QTableWidgetItem(reason_text))

        self.details_table.blockSignals(False)
        self.preview_empty_label.setVisible(False)
        self.details_table.setVisible(True)
        self._update_selection_controls()

    def _select_preview_category(self, row: int) -> None:
        """Sélectionne une catégorie et remplit l'aperçu explicitement.

        ``selectRow`` ne garantit pas à lui seul la mise à jour de l'index
        courant. Or le rendu lit ``currentRow`` : définir la cellule courante
        d'abord évite une prévisualisation vide après une nouvelle analyse.
        """

        if row < 0 or row >= self.category_table.rowCount():
            self._clear_details()
            return
        self.category_table.setCurrentCell(row, 0)
        self.category_table.selectRow(row)
        self._show_selected_details()

    def _preview_scope_changed(self, index: int) -> None:
        """Synchronise le périmètre choisi dans Prévisualisation avec Analyse."""

        if index < 0:
            return
        key = str(self.preview_scope_combo.itemData(index) or "")
        if not key:
            return
        for row in range(self.category_table.rowCount()):
            item = self.category_table.item(row, 0)
            if item is None:
                continue
            if str(item.data(Qt.ItemDataRole.UserRole) or "") != key:
                continue
            self._select_preview_category(row)
            return

    def _show_selected_details(self) -> None:
        result = self._analysis_result
        row = self.category_table.currentRow()
        if result is None or row < 0:
            self._clear_details()
            return

        label_item = self.category_table.item(row, 0)
        if label_item is None:
            self._clear_details()
            return

        key = str(label_item.data(Qt.ItemDataRole.UserRole) or "")
        combo_index = self.preview_scope_combo.findData(key)
        if combo_index >= 0 and combo_index != self.preview_scope_combo.currentIndex():
            self.preview_scope_combo.blockSignals(True)
            self.preview_scope_combo.setCurrentIndex(combo_index)
            self.preview_scope_combo.blockSignals(False)

        if key == "rename":
            details = result.details_for("rename")
            if details is None:
                self._clear_details()
                return
            self._populate_details_table(
                tuple(enumerate(details.items)),
                category_key="rename",
                label="Renommage par lot",
            )
            return

        if key == "archive":
            details = result.details_for("archive")
            if details is None:
                self._clear_details()
                return
            self._populate_details_table(
                tuple(enumerate(details.items)),
                category_key="archive",
                label="Archivage selon l’ancienneté",
            )
            return

        if key == "empty_directory":
            details = result.details_for("empty_directory")
            if details is None:
                self._clear_details()
                return
            self._populate_details_table(
                tuple(enumerate(details.items)),
                category_key="empty_directory",
                label="Dossiers vides",
            )
            return

        details = result.details_for("to_sort")
        if details is None:
            self._clear_details()
            return

        if key == "classification_all":
            indexed = tuple(
                (index, item)
                for index, item in enumerate(details.items)
                if item.destination is not None
            )
            self._populate_details_table(
                indexed,
                category_key="to_sort",
                label="Tous les fichiers classés",
            )
            return

        prefix = "classification_group:"
        if not key.startswith(prefix):
            self._clear_details()
            return
        group_name = key[len(prefix):]

        indexed = tuple(
            (index, item)
            for index, item in enumerate(details.items)
            if item.destination is not None
            and classification_group_name(
                item, self._selected_classification_mode(), self._selected_secondary_mode()
            ) == group_name
        )
        self._populate_details_table(
            indexed,
            category_key="to_sort",
            label=group_name,
        )

    def _detail_selection_changed(self, item: QTableWidgetItem) -> None:
        """Mémorise les actions retenues sans exécuter aucune opération."""

        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(key, tuple) or len(key) != 2:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_actions.add(key)
        else:
            self._selected_actions.discard(key)
        self._update_selection_controls()

    def _set_visible_selection(self, checked: bool) -> None:
        """Met à jour la sélection visible en une seule passe GUI."""

        target_state = (
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self.details_table.blockSignals(True)
        try:
            for row in range(self.details_table.rowCount()):
                if self.details_table.isRowHidden(row):
                    continue
                item = self.details_table.item(row, 0)
                if item is None or not (
                    item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                ):
                    continue
                key = item.data(Qt.ItemDataRole.UserRole)
                if not isinstance(key, tuple) or len(key) != 2:
                    continue
                item.setCheckState(target_state)
                if checked:
                    self._selected_actions.add(key)
                else:
                    self._selected_actions.discard(key)
        finally:
            self.details_table.blockSignals(False)
        self._update_selection_controls()

    def _select_visible_actions(self) -> None:
        self._set_visible_selection(True)

    def _clear_visible_selection(self) -> None:
        self._set_visible_selection(False)

    def _update_duplicate_selection_controls(self) -> None:
        """Décrit une résolution Doublons atomique sans cases individuelles."""

        resolved_groups = self._resolved_duplicate_groups()
        if not resolved_groups:
            return

        mutation_count = sum(group.mutation_count for group in resolved_groups)
        group_count = len(resolved_groups)
        group_word = "groupe" if group_count == 1 else "groupes"
        mutation_word = "mutation prévue" if mutation_count == 1 else "mutations prévues"
        self.selection_label.setText(
            f"Doublons résolus : {human_count(group_count)} {group_word} · "
            f"{human_count(mutation_count)} {mutation_word}"
        )

        row_count = self.details_table.rowCount()
        visible_count = sum(
            not self.details_table.isRowHidden(row)
            for row in range(row_count)
        )
        filter_query = self.details_filter.text().strip()
        file_word = "fichier" if row_count == 1 else "fichiers"
        displayed_word = "affiché" if row_count == 1 else "affichés"
        if filter_query:
            self.preview_scope_summary.setText(
                f"Périmètre : Doublons résolus · Recherche texte : "
                f"« {filter_query} » · {human_count(visible_count)} / "
                f"{human_count(row_count)} {file_word} {displayed_word}"
            )
        else:
            self.preview_scope_summary.setText(
                f"Périmètre : Doublons résolus · {human_count(row_count)} "
                f"{file_word} {displayed_word}"
            )

        has_details = row_count > 0
        self.preview_scope_summary.setVisible(has_details)
        self.details_filter_label.setVisible(has_details)
        self.details_filter_hint.setVisible(has_details)
        self.selection_label.setVisible(has_details)
        self.select_all_button.setVisible(False)
        self.clear_selection_button.setVisible(False)
        self.select_all_button.setEnabled(False)
        self.clear_selection_button.setEnabled(False)
        self.execute_button.setEnabled(
            self._active_worker is None
            and bool(self._selected_actions)
            and mutation_count > 0
        )

    def _update_selection_controls(self) -> None:
        """Rend explicites la vue, le filtre et la sélection globale."""

        category_key = self._details_category_key
        result = self._analysis_result
        details = (
            result.details_for(category_key)
            if result is not None and category_key is not None
            else None
        )

        if category_key == "duplicate" and self._duplicate_keepers:
            self._update_duplicate_selection_controls()
            return

        total_count = 0
        selected_count = 0
        if details is not None and category_key is not None:
            for action_index, item in enumerate(details.items):
                if item.action == "none":
                    continue
                total_count += 1
                if (category_key, action_index) in self._selected_actions:
                    selected_count += 1

        scope_count = 0
        visible_count = 0
        visible_selected_count = 0
        for row in range(self.details_table.rowCount()):
            item = self.details_table.item(row, 0)
            if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            scope_count += 1
            if self.details_table.isRowHidden(row):
                continue
            visible_count += 1
            if item.checkState() == Qt.CheckState.Checked:
                visible_selected_count += 1

        action_word = "action" if total_count == 1 else "actions"
        self.selection_label.setText(
            f"Sélection globale : {human_count(selected_count)} / "
            f"{human_count(total_count)} {action_word}"
        )

        filter_query = self.details_filter.text().strip()
        filter_active = bool(filter_query)
        scope = self._details_scope_label or "Vue courante"
        scope_file_word = "fichier" if scope_count == 1 else "fichiers"
        scope_displayed_word = "affiché" if scope_count == 1 else "affichés"
        if filter_active:
            self.preview_scope_summary.setText(
                f"Périmètre : {scope} · Recherche texte : « {filter_query} » · "
                f"{human_count(visible_count)} / {human_count(scope_count)} "
                f"{scope_file_word} {scope_displayed_word}"
            )
            select_text = f"Sélectionner les visibles ({human_count(visible_count)})"
            clear_text = (
                "Désélectionner les visibles "
                f"({human_count(visible_selected_count)})"
            )
        else:
            self.preview_scope_summary.setText(
                f"Périmètre : {scope} · {human_count(scope_count)} "
                f"{scope_file_word} {scope_displayed_word}"
            )
            if scope == "Tous les fichiers classés":
                select_text = f"Tout sélectionner ({human_count(visible_count)})"
                clear_text = (
                    f"Tout désélectionner ({human_count(visible_selected_count)})"
                )
            else:
                select_text = f"Sélectionner cette vue ({human_count(visible_count)})"
                clear_text = (
                    "Désélectionner cette vue "
                    f"({human_count(visible_selected_count)})"
                )

        self.select_all_button.setText(select_text)
        self.clear_selection_button.setText(clear_text)
        self.select_all_button.setToolTip(
            "Ajoute à la sélection toutes les actions actuellement affichées."
        )
        self.clear_selection_button.setToolTip(
            "Retire de la sélection toutes les actions actuellement affichées."
        )

        has_details = self.details_table.rowCount() > 0
        self.preview_scope_summary.setVisible(has_details)
        self.details_filter_label.setVisible(has_details)
        self.details_filter_hint.setVisible(has_details)
        self.selection_label.setVisible(has_details)
        self.select_all_button.setVisible(has_details)
        self.clear_selection_button.setVisible(has_details)
        self.select_all_button.setEnabled(
            self._active_worker is None
            and visible_count > 0
            and visible_selected_count < visible_count
        )
        self.clear_selection_button.setEnabled(
            self._active_worker is None and visible_selected_count > 0
        )
        self.execute_button.setEnabled(
            self._active_worker is None and bool(self._selected_actions)
        )

    def _confirm_execution(self) -> None:
        result = self._analysis_result
        if result is None or not self._selected_actions or self._active_worker is not None:
            return

        groups = duplicate_groups_for_selection(
            result, set(self._selected_actions)
        )
        if groups:
            required = {group.key for group in groups}
            if not required.issubset(self._duplicate_keepers):
                QMessageBox.information(
                    self,
                    "Résoudre les doublons",
                    "Choisissez d’abord le fichier à conserver pour chaque "
                    "groupe de doublons depuis la catégorie « Doublons ».",
                )
                return

        duplicate_keepers = {
            group.key: self._duplicate_keepers[group.key]
            for group in groups
        }
        rename_selection = bool(self._selected_actions) and all(
            category_key == "rename"
            for category_key, _action_index in self._selected_actions
        )
        archive_selection = bool(self._selected_actions) and all(
            category_key == "archive"
            for category_key, _action_index in self._selected_actions
        )
        selected_archive_items = [
            item
            for category_key, action_index, item in getattr(result, "_actions", ())
            if (category_key, action_index) in self._selected_actions
        ]
        zip_archive_selection = archive_selection and bool(
            selected_archive_items
        ) and all(
            item.archive_format == ArchiveFormat.ZIP.value
            for item in selected_archive_items
        )
        empty_directory_selection = bool(self._selected_actions) and all(
            category_key == "empty_directory"
            for category_key, _action_index in self._selected_actions
        )

        if groups:
            try:
                resolved_items = resolve_selected_actions(
                    result,
                    set(self._selected_actions),
                    duplicate_keepers=duplicate_keepers,
                )
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    "Prévisualisation invalide",
                    str(exc),
                )
                return
            count = len(resolved_items)
            conflict_count = sum(item.conflict for item in resolved_items)
        else:
            # Hors workflow Doublons, une ligne sélectionnée correspond déjà à
            # une mutation physique. Ne pas imposer ici la représentation
            # interne ``_actions`` : certains adaptateurs/tests construisent
            # légitimement AnalysisResult à partir des seuls détails affichés.
            count = len(self._selected_actions)
            conflict_count = 0
            for category_key, action_index in self._selected_actions:
                details = result.details_for(category_key)
                if (
                    details is not None
                    and 0 <= action_index < len(details.items)
                    and details.items[action_index].conflict
                ):
                    conflict_count += 1
        collision_notice = (
            f"\n\n{human_count(conflict_count)} action"
            f"{'s' if conflict_count != 1 else ''} "
            f"{'appartiennent' if conflict_count != 1 else 'appartient'} à une "
            "collision interne déjà résolue dans la prévisualisation."
            if conflict_count
            else ""
        )
        fast_move_enabled = (
            bool(self.execution_mode_combo.currentData())
            and not rename_selection
            and not empty_directory_selection
            and not zip_archive_selection
        )
        large_remote_bytes = (
            self._large_remote_move_size(result, resolved_items if groups else None)
            if not fast_move_enabled and not rename_selection and not empty_directory_selection
            else 0
        )
        if large_remote_bytes:
            choice = self._confirm_large_remote_execution(
                count, large_remote_bytes, collision_notice
            )
            if choice is None:
                return
            fast_move_enabled = choice
            if choice:
                self.execution_mode_combo.setCurrentIndex(1)
        else:
            fast_move_notice = (
                "\n\nMode rapide rclone/FUSE actif : une destination créée "
                "concurremment par un autre programme pourrait être écrasée."
                if fast_move_enabled
                else ""
            )
            confirmation_text = (
                f"Vous êtes sur le point de renommer {human_count(count)} "
                f"fichier{'s' if count != 1 else ''}."
                if rename_selection
                else (
                    f"Vous êtes sur le point d’archiver {human_count(count)} "
                    f"fichier{'s' if count != 1 else ''}."
                    if archive_selection
                    else (
                        f"Vous allez supprimer {human_count(count)} dossier(s) vide(s)."
                        if empty_directory_selection
                        else f"Vous êtes sur le point d’exécuter {human_count(count)} "
                        f"mutation{'s' if count != 1 else ''} prévue"
                        f"{'s' if count != 1 else ''}."
                    )
                )
            )
            confirmation_text += f"{collision_notice}{fast_move_notice}\n\n"
            confirmation_text += (
                "Les fichiers gardent leur dossier et seul leur nom change. "
                if rename_selection
                else (
                    "Les fichiers seront déplacés vers leur destination d’archive, "
                    "en conservant leur arborescence. "
                    if archive_selection
                    else (
                        "Les dossiers qui ne sont plus vides seront conservés ; "
                        "le batch peut être annulé depuis l’historique. "
                        if empty_directory_selection
                        else "Les fichiers concernés pourront être déplacés ou copiés. "
                    )
                )
            )
            confirmation_text += "Vérifiez la prévisualisation avant de continuer."
            answer = QMessageBox.question(
                self,
                (
                    "Renommer les fichiers sélectionnés ?"
                    if rename_selection
                    else (
                        "Archiver les fichiers sélectionnés ?"
                        if archive_selection
                        else (
                            "Supprimer les dossiers vides ?"
                            if empty_directory_selection
                            else "Exécuter les actions sélectionnées ?"
                        )
                    )
                ),
                confirmation_text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._set_busy(True)
        self._execution_completed = 0
        self._set_feedback(self.status_label, "Exécution en cours…", "busy")
        self._set_activity(True, "Exécution en cours…", "busy")
        self.activity_progress.setRange(0, count)
        self.activity_progress.setValue(0)
        self.activity_progress.setTextVisible(False)
        self.activity_percent.setText("Lot : 0 %")
        self.activity_percent.setVisible(True)
        worker_kwargs = (
            {"duplicate_keepers": duplicate_keepers}
            if duplicate_keepers
            else {}
        )
        if fast_move_enabled:
            worker_kwargs["allow_unsafe_fast_move"] = True
        worker = Worker(
            execute_selected_actions,
            result,
            set(self._selected_actions),
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
            return_result_on_cancel=True,
            **worker_kwargs,
        )
        self._active_worker = worker
        self._active_operation = "execution"
        self.cancel_analysis_button.setText("Annuler l’exécution")
        self.cancel_analysis_button.setAccessibleName("Annuler l’exécution en cours")
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._execution_succeeded)
        worker.signals.error.connect(self._execution_failed)
        worker.signals.progress.connect(self._execution_progress)
        worker.signals.finished.connect(self._execution_finished)
        self._start_worker(worker)

    def _large_remote_move_size(
        self, result: AnalysisResult, resolved_items: object | None
    ) -> int:
        """Compte les MOVE sur le même montage distant sans lire leurs octets."""

        if not hasattr(result, "summary"):
            return 0
        moves: list[tuple[int, Path]] = []
        if resolved_items is not None:
            for item in resolved_items:
                if item.action.value == "move" and item.destination is not None:
                    moves.append((item.size, item.destination))
        else:
            for category_key, action_index in self._selected_actions:
                details = result.details_for(category_key)
                if details is None or not 0 <= action_index < len(details.items):
                    continue
                item = details.items[action_index]
                if item.action == "move" and item.destination is not None:
                    moves.append((item.size, item.destination))

        total = sum(max(0, size) for size, _ in moves)
        if total < 512 * 1024 * 1024:
            return 0

        source_info = remote_folder_info(result.summary.root)
        if not source_info.is_remote or source_info.mount_point is None:
            return 0
        for _, destination in moves:
            destination_info = remote_folder_info(destination.parent)
            if (
                destination_info.mount_point != source_info.mount_point
                or destination_info.source != source_info.source
            ):
                return 0
        return total

    def _confirm_large_remote_execution(
        self, count: int, total_bytes: int, collision_notice: str
    ) -> bool | None:
        """Fait choisir explicitement entre copie sûre et renommage rapide."""

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Classement de gros fichiers distants")
        dialog.setText(
            f"{human_count(count)} actions sélectionnées, dont "
            f"{human_size(total_bytes)} à déplacer sur le partage."
        )
        dialog.setInformativeText(
            "Le mode sûr peut recopier tous ces octets avant de supprimer "
            "les sources. Le mode rapide tente un renommage sur le partage ; "
            "si le montage ne le permet pas, une copie reste nécessaire. "
            "Si une destination est créée au même instant par un autre "
            "programme, le renommage rapide pourrait l’écraser."
            f"{collision_notice}\n\nVérifiez la prévisualisation avant de continuer."
        )
        safe_button = dialog.addButton(
            "Exécuter en mode sûr", QMessageBox.ButtonRole.AcceptRole
        )
        fast_button = dialog.addButton(
            "Exécuter en mode rapide", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = dialog.addButton(
            "Annuler", QMessageBox.ButtonRole.RejectRole
        )
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() is fast_button:
            return True
        if dialog.clickedButton() is safe_button:
            return False
        return None

    def _cancel_execution(self) -> None:
        """Demande l'arrêt du lot après l'action actuellement en cours."""

        worker = self._active_worker
        if worker is None or self._active_operation != "execution":
            return
        self.cancel_analysis_button.setEnabled(False)
        message = (
            "Annulation de l’exécution demandée… "
            "L’action en cours sera terminée."
        )
        self._set_feedback(self.status_label, message, "busy")
        self._set_activity(True, message, "busy")
        worker.request_cancel()

    def _execution_progress(self, progress: object) -> None:
        """Distingue les actions terminées de l'action et des octets en cours."""

        if not isinstance(progress, tuple) or len(progress) < 4:
            return
        stage, current, total, path = progress[:4]
        if stage not in {
            "archive_compress", "execution", "execution_copy", "execution_done"
        }:
            return
        if stage == "execution_copy" and len(progress) < 7:
            return
        try:
            index, total_actions = int(current), int(total)
        except (TypeError, ValueError):
            return
        if total_actions < 1 or not 1 <= index <= total_actions:
            return
        completed = index if stage == "execution_done" else index - 1
        self._execution_completed = min(
            total_actions, max(self._execution_completed, completed)
        )
        remaining = total_actions - self._execution_completed
        summary = (
            f"Traités : {human_count(self._execution_completed)} · "
            f"restants : {human_count(remaining)}"
        )
        name = Path(str(path)).name
        if stage == "execution_copy":
            copied = max(0, int(progress[4]))
            total_bytes = max(0, int(progress[5]))
            elapsed = max(float(progress[6]), 0.000001)
            rate = int(copied / elapsed)
            percent = min(100, int(100 * copied / total_bytes)) if total_bytes else 0
            message = (
                f"Écriture sûre {human_count(index)}/"
                f"{human_count(total_actions)} — {name} — "
                f"{human_size(copied)} / {human_size(total_bytes)} · "
                f"{percent}% · {human_size(rate)}/s · {summary}"
            )
        elif stage == "execution_done":
            message = (
                f"Action terminée {human_count(index)}/"
                f"{human_count(total_actions)} — {name} · {summary}"
            )
        elif stage == "archive_compress":
            message = (
                f"Compression ZIP {human_count(index)}/"
                f"{human_count(total_actions)} — {name} · {summary}"
            )
            if len(progress) >= 7:
                copied = max(0, int(progress[4]))
                total_bytes = max(0, int(progress[5]))
                percent = (
                    min(100, int(100 * copied / total_bytes))
                    if total_bytes
                    else 100
                )
                message += (
                    f" — {human_size(copied)} / {human_size(total_bytes)}"
                    f" · {percent}%"
                )
        else:
            message = (
                f"Exécution {human_count(index)}/"
                f"{human_count(total_actions)} — {name} · {summary}"
            )
        self._set_feedback(self.status_label, message, "busy")
        self._set_activity(True, summary, "busy")
        self.activity_progress.setRange(0, total_actions)
        self.activity_progress.setValue(self._execution_completed)
        self.activity_progress.setTextVisible(False)
        current_fraction = (
            min(1.0, copied / total_bytes)
            if stage == "execution_copy" and total_bytes
            else (
                min(
                    1.0,
                    max(0, int(progress[4])) / max(1, int(progress[5])),
                )
                if stage == "archive_compress" and len(progress) >= 7
                else 0.0
            )
        )
        overall_percent = 100 * (
            self._execution_completed + current_fraction
        ) / total_actions
        if 0 < overall_percent < 0.1:
            overall_text = "<0,1"
        elif overall_percent < 10 and overall_percent != 0:
            overall_text = f"{overall_percent:.1f}".replace(".", ",")
        else:
            overall_text = str(int(overall_percent))
        percent_text = f"Lot : {overall_text} %"
        if stage == "execution_copy":
            percent_text += f"\nFichier : {percent} %"
        elif stage == "archive_compress" and len(progress) >= 7:
            total_bytes = max(0, int(progress[5]))
            file_percent = (
                min(100, int(100 * max(0, int(progress[4])) / total_bytes))
                if total_bytes
                else 100
            )
            percent_text += f"\nFichier : {file_percent} %"
        self.activity_percent.setText(percent_text)
        self.activity_percent.setVisible(True)

    def _execution_succeeded(
        self,
        result: object,
        *,
        resumed: bool = False,
    ) -> None:
        if not isinstance(result, ExecuteResult):
            raise TypeError("résultat d'exécution applicatif invalide")

        error_count = len(result.errors)
        operation_name = "Reprise" if resumed else "Exécution"
        if result.cancelled:
            execution_status = f"{operation_name} interrompue."
            activity_text = f"{operation_name} interrompue"
            tone = "warning"
        else:
            execution_status = (
                f"{operation_name} terminée."
                if not error_count
                else f"{operation_name} terminée avec erreurs."
            )
            activity_text = (
                f"{operation_name} terminée"
                if not error_count
                else f"{operation_name} partielle"
            )
            tone = "success" if not error_count else "warning"

        self._set_feedback(self.status_label, execution_status, tone)
        self._set_activity(False, activity_text, tone)

        summary = (
            f"{human_count(result.success)} action"
            f"{'s' if result.success != 1 else ''} réussie"
            f"{'s' if result.success != 1 else ''}, "
            f"{human_count(error_count)} erreur{'s' if error_count != 1 else ''}"
        )
        if result.cancelled or (resumed and result.skipped):
            summary += (
                f", {human_count(result.skipped)} non démarrée"
                f"{'s' if result.skipped != 1 else ''}"
            )
        if result.batch_id is not None:
            summary = f"Batch #{result.batch_id} — {summary}"
        self.execution_summary_label.setText(summary + ".")
        self.execution_summary_label.setVisible(True)
        self.execution_errors_table.setRowCount(error_count)
        for row, message in enumerate(result.errors):
            error_item = QTableWidgetItem(message)
            error_item.setToolTip(message)
            self.execution_errors_table.setItem(row, 0, error_item)
        self.execution_errors_table.setVisible(bool(result.errors))
        # Seul un batch ayant réellement muté le filesystem devient la
        # nouvelle cible d'Undo. Une tentative sans mutation (préflight
        # refusé, batch entièrement échoué, annulation avant la première
        # action, etc.) ne doit pas faire perdre le dernier batch encore
        # annulable dans cette session.
        if result.batch_id is not None and result.success > 0:
            self._last_batch_id = result.batch_id
        self._pending_history_refresh = result.batch_id is not None
        self._pending_history_batch_id = result.batch_id
        self.undo_button.setVisible(self._last_batch_id is not None)
        self.undo_button.setEnabled(
            self._active_worker is None and self._last_batch_id is not None
        )
        self.undo_summary_label.clear()
        self.undo_summary_label.setVisible(False)

        if result.errors:
            QMessageBox.warning(
                self,
                f"{operation_name} partielle",
                "Certaines actions n’ont pas pu être exécutées. "
                "Consultez les erreurs affichées dans la fenêtre.",
            )

    def _execution_failed(self, error: object) -> None:
        exception, _formatted_traceback = error
        self._set_feedback(self.status_label, "L'exécution a échoué.", "error")
        self._set_activity(False, "Échec de l'exécution", "error")
        QMessageBox.critical(self, "Erreur d'exécution", str(exception))

    def _execution_finished(self) -> None:
        self._active_worker = None
        self._active_operation = None
        self._execution_completed = 0

        # Une exécution peut avoir modifié le filesystem : l'analyse précédente
        # n'est donc plus une représentation valide du dossier courant.
        self._last_analysis_context = None

        # Toute prévisualisation devient périmée après une tentative d'exécution.
        # Le bilan d'exécution reste toutefois visible jusqu'à la prochaine analyse.
        self._clear_results(clear_execution=False)
        self._set_busy(False)
        self._refresh_history_after_mutation()

    def _confirm_undo(self) -> None:
        batch_id = self._last_batch_id
        if batch_id is None or self._active_worker is not None:
            return

        answer = QMessageBox.question(
            self,
            "Annuler la dernière exécution ?",
            f"Restaurer les fichiers modifiés par l’exécution #{batch_id} ?\n\n"
            "Les fichiers restaurés retrouveront leur emplacement d’origine. "
            "Les dossiers de destination devenus vides seront supprimés.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._set_feedback(self.status_label, "Annulation en cours…", "busy")
        self._set_activity(True, "Annulation en cours…", "busy")
        worker = Worker(undo_execution, batch_id)
        self._active_worker = worker
        worker.signals.result.connect(self._undo_succeeded)
        worker.signals.error.connect(self._undo_failed)
        worker.signals.finished.connect(self._undo_finished)
        self._start_worker(worker)

    def _undo_succeeded(self, result: object) -> None:
        if not isinstance(result, UndoResult):
            raise TypeError("résultat d'annulation applicatif invalide")

        error_count = len(result.errors)
        self._pending_history_refresh = True
        self._pending_history_batch_id = self._last_batch_id
        undo_status = (
            "Annulation terminée."
            if not error_count
            else "Annulation terminée avec erreurs."
        )
        self._set_feedback(
            self.status_label, undo_status, "success" if not error_count else "warning"
        )
        self._set_activity(
            False,
            "Annulation terminée" if not error_count else "Annulation partielle",
            "success" if not error_count else "warning",
        )
        self.undo_summary_label.setText(
            f"{human_count(result.success)} action"
            f"{'s' if result.success != 1 else ''} restaurée"
            f"{'s' if result.success != 1 else ''}, "
            f"{human_count(error_count)} erreur{'s' if error_count != 1 else ''}."
        )
        self.undo_summary_label.setVisible(True)

        if result.errors:
            QMessageBox.warning(
                self,
                "Annulation partielle",
                "Certains fichiers n’ont pas pu être restaurés. "
                "Cette exécution reste disponible pour une nouvelle tentative.",
            )
        else:
            # Le batch courant est intégralement restauré. Retrouver
            # immédiatement la cible LIFO précédente encore annulable afin
            # que la continuité d'Undo ne dépende pas de la session GUI.
            self._restore_persistent_undo_target()

    def _undo_failed(self, error: object) -> None:
        exception, _formatted_traceback = error
        self._set_feedback(self.status_label, "L'annulation a échoué.", "error")
        self._set_activity(False, "Échec de l'annulation", "error")
        QMessageBox.critical(self, "Erreur d'annulation", str(exception))

    def _undo_finished(self) -> None:
        self._active_worker = None

        # L'Undo modifie également le filesystem : une nouvelle analyse est requise.
        self._last_analysis_context = None

        self._set_busy(False)
        self._refresh_history_after_mutation()

    def _refresh_history_after_mutation(self) -> None:
        if not self._pending_history_refresh:
            return
        preferred_batch_id = self._pending_history_batch_id
        self._pending_history_refresh = False
        self._pending_history_batch_id = None
        self._refresh_history(preferred_batch_id=preferred_batch_id)

    def _start_history_refresh(self) -> None:
        self._refresh_history(preferred_batch_id=self._selected_history_batch_id())

    def _refresh_history(self, *, preferred_batch_id: int | None) -> None:
        if self._active_worker is not None:
            return

        self._history_generation += 1
        self._history_refresh_selection_id = preferred_batch_id
        self._set_busy(True)
        self._set_feedback(self.history_status_label, "Chargement de l'historique…", "busy")
        self._set_activity(True, "Historique en cours…", "busy")
        worker = Worker(get_history_summary)
        self._active_worker = worker
        worker.signals.result.connect(self._history_succeeded)
        worker.signals.error.connect(self._history_failed)
        worker.signals.finished.connect(self._history_finished)
        self._start_worker(worker)

    def _history_succeeded(self, result: object) -> None:
        if not isinstance(result, tuple) or not all(
            isinstance(batch, HistoryBatchSummary) for batch in result
        ):
            raise TypeError("résultat d'historique applicatif invalide")

        self._history_batches = result
        self.history_table.clearContents()
        self.history_table.setRowCount(len(result))
        for row, batch in enumerate(result):
            batch_item = QTableWidgetItem(str(batch.id))
            batch_item.setData(Qt.ItemDataRole.UserRole, row)
            self.history_table.setItem(row, 0, batch_item)
            created_at_text = (
                f"Horodatage invalide ({batch.created_at_raw})"
                if batch.created_at_raw
                else batch.created_at
            )
            status_text = (
                f"État inconnu ({batch.status_raw})"
                if batch.status == "unknown" and batch.status_raw
                else self._history_status_text(batch.status)
            )
            created_at_item = QTableWidgetItem(created_at_text)
            root_item = QTableWidgetItem(
                self._history_batch_root_text(batch.root, batch.root_raw)
            )
            status_item = QTableWidgetItem(status_text)
            execution_item = QTableWidgetItem(
                self._history_execution_summary(batch)
            )
            issue_text = self._history_batch_issue_text(batch)
            if issue_text is not None:
                created_at_item.setToolTip(issue_text)
                root_item.setToolTip(issue_text)
                status_item.setToolTip(issue_text)
                execution_item.setToolTip(issue_text)

            self.history_table.setItem(row, 1, created_at_item)
            self.history_table.setItem(row, 2, root_item)
            self.history_table.setItem(row, 3, status_item)
            self.history_table.setItem(row, 4, execution_item)

        self.history_table.setVisible(bool(result))
        self.history_empty_label.setVisible(not bool(result))
        self._clear_history_operations()
        self.history_status_label.setText(
            f"{human_count(len(result))} exécution"
            f"{'s' if len(result) != 1 else ''} récente"
            f"{'s' if len(result) != 1 else ''}."
            if result
            else "Aucune exécution dans l’historique."
        )
        self.history_status_label.setProperty("tone", "success")
        self.history_status_label.style().unpolish(self.history_status_label)
        self.history_status_label.style().polish(self.history_status_label)
        self._set_activity(False, "Historique actualisé", "success")
        if result:
            preferred_id = self._history_refresh_selection_id
            selected_row = next(
                (row for row, batch in enumerate(result) if batch.id == preferred_id),
                0,
            )
            self.history_table.selectRow(selected_row)
            self.history_splitter.setSizes([300, 430])
        else:
            self.history_splitter.setSizes([260, 470])
        self._history_refresh_selection_id = None

    @staticmethod
    def _history_batch_root_text(
        root: str | None,
        raw_value: object | None,
    ) -> str:
        """Rend une racine valide ou sa valeur persistée invalide."""

        if root is not None:
            return root
        if raw_value is not None:
            return f"Valeur persistée invalide ({raw_value!r})"
        return "Racine indisponible"

    @staticmethod
    def _history_execution_summary(batch: HistoryBatchSummary) -> str:
        """Résumé persistant sûr du résultat initial d'une exécution."""

        raw_counts = (
            batch.planned_count_raw,
            batch.success_count_raw,
            batch.failed_count_raw,
            batch.skipped_count_raw,
        )
        if any(value is not None for value in raw_counts):
            return (
                "Bilan persisté invalide ("
                + ", ".join(repr(value) for value in raw_counts)
                + ")"
            )

        counts = (
            batch.planned_count,
            batch.success_count,
            batch.failed_count,
            batch.skipped_count,
        )
        if any(value is None for value in counts):
            return "Bilan indisponible"

        planned, success, failed, skipped = counts
        if planned <= 0:
            return "—"

        success_word = "réussie" if success == 1 else "réussies"
        parts = [f"{success}/{planned} {success_word}"]
        if failed:
            parts.append(f"{failed} échec{'s' if failed != 1 else ''}")
        if skipped:
            parts.append(
                f"{skipped} non démarrée"
                f"{'s' if skipped != 1 else ''}"
            )
        return " · ".join(parts)

    @staticmethod
    def _history_status_text(status: str) -> str:
        return {
            "running": "En cours",
            "completed": "Terminé",
            "partial": "Partiel",
            "failed": "Échoué",
            "cancelled": "Interrompu",
            "undoing": "Annulation en cours",
            "undone": "Annulé",
            "undo_partial": "Annulation partielle",
            "undo_failed": "Annulation échouée",
            "queued": "Non démarrée",
            "planned": "Planifiée",
            "unknown": "État inconnu",
        }.get(status, status)

    @classmethod
    def _history_operation_status_text(
        cls,
        operation: HistoryOperationSummary,
    ) -> str:
        """Expose le recovery non résolu sans parser le diagnostic texte."""

        if operation.recovery_attention_required:
            return {
                PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED: "À vérifier après crash",
                PlannedRecoveryState.AMBIGUOUS: "État ambigu après crash",
                PlannedRecoveryState.UNKNOWN: (
                    f"État de récupération inconnu ({operation.recovery_state_raw})"
                    if operation.recovery_state_raw
                    else "État de récupération inconnu"
                ),
            }.get(operation.recovery_state, "À vérifier après crash")
        if operation.status == "queued":
            if operation.resume_candidate:
                return "Candidate à la reprise"
            if operation.resume_blockers:
                return "Reprise bloquée"
        if operation.status == "unknown" and operation.status_raw:
            return f"État inconnu ({operation.status_raw})"
        return cls._history_status_text(operation.status)

    @staticmethod
    def _history_core_metadata_issue_text(
        issues: tuple[str, ...],
    ) -> str | None:
        labels = {
            "invalid_created_at": "horodatage invalide",
            "unknown_status": "état inconnu",
            # 4E-22B: diagnostic visible de la valeur kind persistée invalide.
            "unknown_kind": "type d’opération inconnu",
            "invalid_original_path": "chemin d’origine invalide",
            "invalid_stored_path": "chemin stocké invalide",
            "invalid_size": "taille invalide",
            "invalid_category": "catégorie invalide",
            "invalid_root": "racine de batch invalide",
            "invalid_undone": "indicateur d’annulation invalide",
            "invalid_planned_count": "total prévu invalide",
            "invalid_success_count": "total réussi invalide",
            "invalid_failed_count": "total échoué invalide",
            "invalid_skipped_count": "total non démarré invalide",
            "inconsistent_counts": "bilan d’exécution incohérent",
        }
        rendered = [labels[issue] for issue in issues if issue in labels]
        if not rendered:
            return None
        return "Métadonnées cœur incohérentes : " + ", ".join(rendered)

    @staticmethod
    def _history_batch_consistency_issue_text(
        issues: tuple[str, ...],
    ) -> str | None:
        labels = {
            "missing_batch": "batch parent introuvable",
            "operation_count_mismatch": "nombre d’opérations incohérent",
            "result_count_mismatch": "bilan incohérent avec les opérations",
            "pending_operation_in_terminal_batch": (
                "opération planifiée dans un batch terminé"
            ),
            "undo_state_mismatch": "état d’annulation incohérent",
        }
        rendered = [labels[issue] for issue in issues if issue in labels]
        if not rendered:
            return None
        return "Cohérence batch/opérations : " + ", ".join(rendered)

    @classmethod
    def _history_batch_issue_text(
        cls,
        batch: HistoryBatchSummary,
    ) -> str | None:
        issue_texts = (
            cls._history_core_metadata_issue_text(batch.core_metadata_issues),
            cls._history_batch_consistency_issue_text(
                getattr(batch, "consistency_issues", ())
            ),
        )
        rendered = [text for text in issue_texts if text is not None]
        return "\n".join(rendered) if rendered else None

    @staticmethod
    def _history_path_text(
        path: Path | None,
        raw_value: object | None,
        *,
        absent_text: str,
    ) -> str:
        """Distingue un chemin valide, absent ou persisté invalide."""

        if path is not None:
            return str(path)
        if raw_value is not None:
            return f"Valeur persistée invalide ({raw_value!r})"
        return absent_text

    @staticmethod
    def _history_size_text(
        size: int | None,
        raw_value: object | None,
    ) -> str:
        """Rend une taille valide ou sa valeur persistée invalide."""

        if size is not None:
            return human_size(size)
        if raw_value is not None:
            return f"Valeur persistée invalide ({raw_value!r})"
        return "Taille indisponible"

    @staticmethod
    def _history_category_text(
        category: str | None,
        raw_value: object | None,
    ) -> str:
        """Rend une catégorie valide ou sa valeur persistée invalide."""

        if category is not None:
            return category
        if raw_value is not None:
            return f"Valeur persistée invalide ({raw_value!r})"
        return "Catégorie indisponible"

    @staticmethod
    def _history_stored_identity_issue_text(
        operation: HistoryOperationSummary,
    ) -> str | None:
        return {
            "invalid_stored_identity": (
                "Identité de fichier persistée incohérente"
            ),
        }.get(operation.stored_identity_issue)

    @staticmethod
    def _history_resume_qualification_text(
        operation: HistoryOperationSummary,
    ) -> str | None:
        """Expose la qualification read-only d'une opération QUEUED."""

        if operation.resume_candidate:
            return (
                "Candidate à la reprise ; validation live requise avant "
                "toute action"
            )
        if not operation.resume_blockers:
            return None
        labels = {
            "missing_batch": "batch parent introuvable",
            "invalid_batch_metadata": "métadonnées du batch invalides",
            "batch_operation_inconsistency": (
                "incohérence entre batch et opérations"
            ),
            "batch_status_not_resumable": "état du batch non reprenable",
            "missing_stored_path": "destination absente",
            "unsupported_category": "catégorie non reprenable",
            "missing_original_identity": "identité source absente",
            "invalid_original_identity": "identité source invalide",
            "original_identity_size_mismatch": (
                "taille incohérente avec l’identité source"
            ),
            "missing_conflict_policy": "politique de collision absente",
            "invalid_conflict_policy": "politique de collision invalide",
        }
        rendered = [
            labels.get(blocker, blocker)
            for blocker in operation.resume_blockers
        ]
        return "Reprise bloquée : " + ", ".join(rendered)

    @staticmethod
    def _history_recovery_audit_issue_text(
        operation: HistoryOperationSummary,
    ) -> str | None:
        return {
            "resolution_kind_without_resolved_at": (
                "Audit recovery incohérent : type de résolution sans horodatage"
            ),
            "resolved_at_without_resolution_kind": (
                "Audit recovery incohérent : horodatage sans type de résolution"
            ),
            "unknown_resolution_kind": (
                "Audit recovery incohérent : type de résolution inconnu"
            ),
            "invalid_resolved_at": (
                "Audit recovery incohérent : horodatage de résolution invalide"
            ),
            "resolution_kind_state_mismatch": (
                "Audit recovery incohérent : résolution incompatible avec l’état recovery"
            ),
        }.get(operation.recovery_audit_issue)

    @staticmethod
    def _history_resolution_text(
        operation: HistoryOperationSummary,
    ) -> str | None:
        if operation.resolution_kind is RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION:
            return "Clôturée automatiquement après crash"
        if operation.resolution_kind is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION:
            return "Clôturée manuellement sans action sur les fichiers"
        if operation.resolution_kind is RecoveryResolutionKind.UNKNOWN:
            if operation.resolution_kind_raw:
                return (
                    "Résolution de récupération inconnue "
                    f"({operation.resolution_kind_raw})"
                )
            return "Résolution de récupération inconnue"
        return None

    @staticmethod
    def _history_batch_has_consistency_issues(
        batch: HistoryBatchSummary | None,
    ) -> bool:
        return batch is not None and bool(batch.consistency_issues)

    @staticmethod
    def _history_recovery_attention_text(
        operations: tuple[HistoryOperationSummary, ...],
    ) -> str | None:
        attention_count = sum(
            1 for operation in operations if operation.recovery_attention_required
        )
        if attention_count == 0:
            return None

        noun = "opération nécessite" if attention_count == 1 else "opérations nécessitent"
        return (
            f"{human_count(attention_count)} {noun} une vérification manuelle "
            "après récupération d’un crash."
        )

    def _show_selected_history_operations(self) -> None:
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self._history_batches):
            self._clear_history_operations()
            return

        batch = self._history_batches[row]
        self._clear_history_operations()
        self.history_operations_status_label.setText(
            f"Chargement des opérations de l’exécution #{batch.id}…"
        )
        self.history_operations_status_label.setVisible(True)
        self.history_operations_empty_label.setVisible(False)

        generation = self._history_generation
        worker = Worker(get_history_operation_summary, batch.id)
        self._history_operation_worker = worker
        worker.signals.result.connect(
            lambda result, batch_id=batch.id, generation=generation:
            self._history_operations_succeeded(batch_id, generation, result)
        )
        worker.signals.error.connect(
            lambda error, batch_id=batch.id, generation=generation:
            self._history_operations_failed(batch_id, generation, error)
        )
        worker.signals.finished.connect(
            lambda worker=worker: self._history_operations_finished(worker)
        )
        self._start_worker(worker)

    def _selected_history_batch_id(self) -> int | None:
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self._history_batches):
            return None
        return self._history_batches[row].id

    def _history_operations_succeeded(
        self, batch_id: int, generation: int, result: object
    ) -> None:
        if not isinstance(result, tuple) or not all(
            isinstance(operation, HistoryOperationSummary) for operation in result
        ):
            raise TypeError("résultat d'opérations d'historique applicatif invalide")
        if generation != self._history_generation:
            return
        if self._selected_history_batch_id() != batch_id:
            return

        self.history_operations_table.setRowCount(len(result))
        for operation_row, operation in enumerate(result):
            original_path = self._history_path_text(
                operation.original_path,
                operation.original_path_raw,
                absent_text="Chemin indisponible",
            )
            stored_path = self._history_path_text(
                operation.stored_path,
                operation.stored_path_raw,
                absent_text="—",
            )
            size = self._history_size_text(
                operation.size,
                operation.size_raw,
            )
            category = self._history_category_text(
                operation.category,
                operation.category_raw,
            )

            resolution_text = self._history_resolution_text(operation)
            audit_issue_text = self._history_recovery_audit_issue_text(operation)
            core_issue_text = self._history_core_metadata_issue_text(
                operation.core_metadata_issues
            )
            stored_identity_issue_text = (
                self._history_stored_identity_issue_text(operation)
            )
            if stored_identity_issue_text is not None:
                core_issue_text = (
                    f"{core_issue_text}\n{stored_identity_issue_text}"
                    if core_issue_text
                    else stored_identity_issue_text
                )
            resume_qualification_text = (
                self._history_resume_qualification_text(operation)
            )
            diagnostic_text = operation.error or "—"
            if resolution_text is not None:
                if operation.resolved_at is not None:
                    resolution_text += (
                        " · "
                        + operation.resolved_at.astimezone().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    )
                elif operation.resolved_at_raw:
                    resolution_text += (
                        " · Horodatage de résolution invalide : "
                        + operation.resolved_at_raw
                    )

                diagnostic_text = (
                    f"{resolution_text} · {operation.error}"
                    if operation.error
                    else resolution_text
                )

            if audit_issue_text is not None:
                diagnostic_text = (
                    f"{audit_issue_text} · {diagnostic_text}"
                    if diagnostic_text != "—"
                    else audit_issue_text
                )

            if core_issue_text is not None:
                diagnostic_text = (
                    f"{core_issue_text} · {diagnostic_text}"
                    if diagnostic_text != "—"
                    else core_issue_text
                )

            if resume_qualification_text is not None:
                diagnostic_text = (
                    f"{resume_qualification_text} · {diagnostic_text}"
                    if diagnostic_text != "—"
                    else resume_qualification_text
                )

            values = (
                operation.kind,
                original_path,
                size,
                stored_path,
                category,
                self._history_operation_status_text(operation),
                diagnostic_text,
            )
            for column, value in enumerate(values):
                self.history_operations_table.setItem(
                    operation_row, column, QTableWidgetItem(value)
                )

        row = self.history_table.currentRow()
        batch = (
            self._history_batches[row]
            if 0 <= row < len(self._history_batches)
            else None
        )
        journal_text = (
            f"{human_count(len(result))} opération"
            f"{'s' if len(result) != 1 else ''} journalisée"
            f"{'s' if len(result) != 1 else ''}"
        )
        if batch is not None and batch.skipped_count:
            journal_text += (
                f" · {human_count(batch.skipped_count)} non démarrée"
                f"{'s' if batch.skipped_count != 1 else ''}"
            )
        recovery_attention_count = sum(
            1 for operation in result if operation.recovery_attention_required
        )
        recovery_blocked_count = sum(
            1
            for operation in result
            if operation.recovery_attention_required
            and operation.recovery_audit_issue is not None
        )
        batch_consistency_blocked = (
            self._history_batch_has_consistency_issues(batch)
        )
        can_resolve_recovery = (
            recovery_attention_count > 0
            and recovery_blocked_count == 0
            and not batch_consistency_blocked
        )
        resume_candidate_count = sum(
            1 for operation in result if operation.resume_candidate
        )
        resume_blocked_count = sum(
            1
            for operation in result
            if operation.status == "queued"
            and bool(operation.resume_blockers)
        )

        self.resolve_recovery_button.setProperty(
            "recoveryAttentionCount",
            recovery_attention_count,
        )
        self.resolve_recovery_button.setProperty(
            "recoveryBlockedCount",
            recovery_blocked_count,
        )
        self.resolve_recovery_button.setProperty(
            "batchConsistencyBlocked",
            batch_consistency_blocked,
        )
        self.resolve_recovery_button.setVisible(recovery_attention_count > 0)
        self.resolve_recovery_button.setEnabled(can_resolve_recovery)

        queued_count = sum(
            1 for operation in result if operation.status == "queued"
        )
        resume_ready = (
            queued_count > 0
            and resume_candidate_count == queued_count
            and resume_blocked_count == 0
            and not batch_consistency_blocked
        )
        can_resume = resume_ready and self._active_worker is None
        self.resume_queued_button.setProperty(
            "resumeCandidateCount",
            resume_candidate_count,
        )
        self.resume_queued_button.setProperty(
            "resumeBlockedCount",
            resume_blocked_count,
        )
        self.resume_queued_button.setProperty("resumeReady", resume_ready)
        self.resume_queued_button.setText(
            "Reprendre les opérations non démarrées…"
            if queued_count != 1
            else "Reprendre l’opération non démarrée…"
        )
        self.resume_queued_button.setVisible(queued_count > 0)
        self.resume_queued_button.setEnabled(can_resume)
        self.resume_queued_button.setToolTip(
            ""
            if can_resume
            else (
                "La reprise est bloquée tant que toutes les opérations "
                "non démarrées ne sont pas qualifiées."
            )
        )

        recovery_attention = self._history_recovery_attention_text(result)
        if batch_consistency_blocked:
            self._set_feedback(
                self.history_operations_status_label,
                (
                    "Le batch présente une incohérence avec ses opérations. "
                    "La clôture manuelle est bloquée."
                ),
                "warning",
            )
        elif recovery_blocked_count > 0:
            self._set_feedback(
                self.history_operations_status_label,
                (
                    f"{human_count(recovery_blocked_count)} opération"
                    f"{'s' if recovery_blocked_count != 1 else ''} présente"
                    f"{'nt' if recovery_blocked_count != 1 else ''} un audit "
                    "recovery incohérent. La clôture manuelle est bloquée."
                ),
                "warning",
            )
        elif recovery_attention is not None:
            self._set_feedback(
                self.history_operations_status_label,
                recovery_attention,
                "warning",
            )
        elif resume_candidate_count or resume_blocked_count:
            parts: list[str] = []
            if resume_candidate_count:
                parts.append(
                    f"{human_count(resume_candidate_count)} candidate"
                    f"{'s' if resume_candidate_count != 1 else ''} à la reprise"
                )
            if resume_blocked_count:
                parts.append(
                    f"{human_count(resume_blocked_count)} reprise"
                    f"{'s' if resume_blocked_count != 1 else ''} bloquée"
                    f"{'s' if resume_blocked_count != 1 else ''}"
                )
            self._set_feedback(
                self.history_operations_status_label,
                " · ".join(parts)
                + (
                    ". Reprise manuelle disponible ; chaque action sera "
                    "revalidée juste avant son I/O."
                    if can_resume
                    else ". Reprise manuelle bloquée."
                ),
                "warning",
            )
        else:
            self._set_feedback(
                self.history_operations_status_label,
                f"{journal_text} pour l’exécution #{batch_id}.",
                "success",
            )
        self.history_operations_status_label.setVisible(True)
        self.history_operations_table.setVisible(bool(result))
        self.history_operations_empty_label.setVisible(not bool(result))

    def _confirm_history_recovery_resolution(self) -> None:
        batch_id = self._selected_history_batch_id()
        attention_count = int(
            self.resolve_recovery_button.property("recoveryAttentionCount") or 0
        )
        blocked_count = int(
            self.resolve_recovery_button.property("recoveryBlockedCount") or 0
        )
        if batch_id is None or attention_count <= 0 or blocked_count > 0:
            return

        answer = QMessageBox.question(
            self,
            "Clore la récupération après crash",
            (
                f"{human_count(attention_count)} opération"
                f"{'s' if attention_count != 1 else ''} non résolue"
                f"{'s' if attention_count != 1 else ''}.\n\n"
                "File Janitor ne déplacera, ne copiera, ne supprimera et ne "
                "restaurera aucun fichier. Les opérations seront seulement "
                "clôturées comme échouées dans l’historique.\n\n"
                "À utiliser uniquement après vérification manuelle des fichiers. "
                "Continuer ?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            resolved_count = resolve_history_recovery_without_file_action(batch_id)
        except Exception as exception:
            QMessageBox.critical(
                self,
                "Erreur de résolution de récupération",
                str(exception),
            )
            return

        QMessageBox.information(
            self,
            "Récupération clôturée",
            (
                f"{human_count(resolved_count)} opération"
                f"{'s' if resolved_count != 1 else ''} clôturée"
                f"{'s' if resolved_count != 1 else ''} sans action sur les fichiers."
            ),
        )
        self._refresh_history(preferred_batch_id=batch_id)

    def _confirm_queued_resume(self) -> None:
        """Demande une confirmation avant toute reprise de fichiers."""

        batch_id = self._selected_history_batch_id()
        candidate_count = int(
            self.resume_queued_button.property("resumeCandidateCount") or 0
        )
        blocked_count = int(
            self.resume_queued_button.property("resumeBlockedCount") or 0
        )
        if (
            batch_id is None
            or candidate_count <= 0
            or blocked_count > 0
            or not bool(self.resume_queued_button.property("resumeReady"))
            or self._active_worker is not None
        ):
            return

        answer = QMessageBox.question(
            self,
            "Reprendre les opérations non démarrées ?",
            (
                f"Reprendre {human_count(candidate_count)} opération"
                f"{'s' if candidate_count != 1 else ''} de l’exécution "
                f"#{batch_id} ?\n\n"
                "Les sources et destinations seront revalidées juste avant "
                "chaque action. Si un contrôle échoue, File Janitor n’effectuera "
                "aucune reprise du lot. Les déplacements distants utiliseront "
                "le mode sûr.\n\nContinuer ?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        self._execution_completed = 0
        self._set_feedback(self.status_label, "Reprise en cours…", "busy")
        self._set_activity(True, "Reprise en cours…", "busy")
        self.activity_progress.setRange(0, candidate_count)
        self.activity_progress.setValue(0)
        self.activity_progress.setTextVisible(False)
        self.activity_percent.setText("Lot : 0 %")
        self.activity_percent.setVisible(True)
        worker = Worker(
            resume_queued_operations,
            batch_id,
            progress_kwarg="progress_callback",
            cancel_kwarg="cancel_callback",
            return_result_on_cancel=True,
        )
        self._active_worker = worker
        self._active_operation = "execution"
        self.cancel_analysis_button.setText("Annuler la reprise")
        self.cancel_analysis_button.setAccessibleName(
            "Annuler la reprise en cours"
        )
        self.cancel_analysis_button.setVisible(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.signals.result.connect(self._queued_resume_succeeded)
        worker.signals.error.connect(self._queued_resume_failed)
        worker.signals.progress.connect(self._execution_progress)
        worker.signals.finished.connect(self._queued_resume_finished)
        self._start_worker(worker)

    def _queued_resume_succeeded(self, result: object) -> None:
        self._execution_succeeded(result, resumed=True)

    def _queued_resume_failed(self, error: object) -> None:
        exception, _formatted_traceback = error
        self._pending_history_refresh = True
        self._pending_history_batch_id = self._selected_history_batch_id()
        self._set_feedback(self.status_label, "La reprise a échoué.", "error")
        self._set_activity(False, "Échec de la reprise", "error")
        QMessageBox.critical(self, "Erreur de reprise", str(exception))

    def _queued_resume_finished(self) -> None:
        self._execution_finished()

    def _history_operations_failed(
        self, batch_id: int, generation: int, error: object
    ) -> None:
        if generation != self._history_generation:
            return
        if self._selected_history_batch_id() != batch_id:
            return
        exception, _formatted_traceback = error
        self.history_operations_status_label.setText(
            f"Le chargement des opérations de l’exécution #{batch_id} a échoué."
        )
        self.history_operations_status_label.setVisible(True)
        QMessageBox.critical(self, "Erreur d'historique", str(exception))

    def _history_operations_finished(self, worker: Worker) -> None:
        if self._history_operation_worker is worker:
            self._history_operation_worker = None

    def _clear_history_operations(self) -> None:
        self.history_operations_status_label.clear()
        self.history_operations_status_label.setVisible(False)
        self.resolve_recovery_button.setProperty("recoveryAttentionCount", 0)
        self.resolve_recovery_button.setProperty("recoveryBlockedCount", 0)
        self.resolve_recovery_button.setVisible(False)
        self.resolve_recovery_button.setEnabled(False)
        self.resume_queued_button.setProperty("resumeCandidateCount", 0)
        self.resume_queued_button.setProperty("resumeBlockedCount", 0)
        self.resume_queued_button.setProperty("resumeReady", False)
        self.resume_queued_button.setVisible(False)
        self.resume_queued_button.setEnabled(False)
        self.history_operations_table.clearContents()
        self.history_operations_table.setRowCount(0)
        self.history_operations_table.setVisible(False)
        self.history_operations_empty_label.setVisible(True)

    def _history_failed(self, error: object) -> None:
        exception, _formatted_traceback = error
        self._set_feedback(self.history_status_label, "Le chargement de l'historique a échoué.", "error")
        self._set_activity(False, "Échec de l'historique", "error")
        QMessageBox.critical(self, "Erreur d'historique", str(exception))

    def _history_finished(self) -> None:
        self._active_worker = None
        self._set_busy(False)

    def _filter_details(self, text: str) -> None:
        """Recherche locale ; une extension exacte devient un périmètre."""

        query = text.strip().casefold()

        # En mode Extension, ``xml`` ou ``.xml`` saisi depuis la vue globale
        # signifie presque toujours « afficher l'extension XML », pas
        # « rechercher ces trois caractères dans des noms opaques ». Les noms
        # chiffrés (Cryptomator .c9r, par exemple) peuvent contenir ``xml``
        # par hasard. Basculer explicitement vers le groupe élimine cette
        # ambiguïté tout en conservant la recherche texte pour les autres
        # requêtes (workspace, rapport, etc.).
        current_scope = str(self.preview_scope_combo.currentData() or "")
        if (
            query
            and current_scope == "classification_all"
            and self._selected_classification_mode() is ClassificationMode.EXTENSION
            and self._selected_secondary_mode() is None
        ):
            normalized_extension = query.removeprefix(".")
            target_key = f"classification_group:{normalized_extension}"
            target_index = self.preview_scope_combo.findData(target_key)
            if target_index >= 0:
                self.details_filter.blockSignals(True)
                self.details_filter.clear()
                self.details_filter.blockSignals(False)
                self.preview_scope_combo.setCurrentIndex(target_index)
                return

        for row in range(self.details_table.rowCount()):
            source_item = self.details_table.item(row, 2)
            destination_item = self.details_table.item(row, 4)
            reason_item = self.details_table.item(row, 5)
            source_name = (
                Path(source_item.text()).name if source_item is not None else ""
            )
            destination_name = ""
            if destination_item is not None:
                destination_path = Path(destination_item.text())
                destination_root = (
                    Path(self.destination_edit.text().strip())
                    if self.destination_edit.text().strip()
                    else self._analysis_result.summary.root
                    if self._analysis_result is not None else None
                )
                try:
                    destination_name = str(destination_path.relative_to(destination_root))
                except (TypeError, ValueError):
                    destination_name = destination_path.name
            values = (
                source_name,
                destination_name,
                reason_item.text() if reason_item is not None else "",
            )
            matches = not query or any(
                query in value.casefold() for value in values
            )
            self.details_table.setRowHidden(row, not matches)
        self._update_selection_controls()

    def _clear_details(self) -> None:
        self.details_label.clear()
        self.details_label.setVisible(False)
        self.preview_scope_summary.clear()
        self.preview_scope_summary.setVisible(False)
        self.details_filter_label.setVisible(False)
        self.details_filter.clear()
        self.details_filter.setVisible(False)
        self.details_filter_hint.setVisible(False)
        self.details_table.clearContents()
        self.details_table.setRowCount(0)
        self.details_table.setVisible(False)
        self._details_category_key = None
        self._details_scope_label = ""
        self.preview_empty_label.setVisible(True)
        self.selection_label.setVisible(False)
        self.select_all_button.setVisible(False)
        self.clear_selection_button.setVisible(False)

    def _analysis_failed(self, error: object) -> None:
        exception, _formatted_traceback = error
        self._set_feedback(
            self.status_label,
            (
                f"L'analyse "
                f"{self._analysis_profile_label(self._active_analysis_profile)} "
                "a échoué."
            ),
            "error",
        )
        elapsed = self._stop_analysis_clock()
        self._set_activity(False, f"Échec de l'analyse — {elapsed}", "error")
        QMessageBox.critical(
            self,
            "Erreur d'analyse",
            str(exception),
        )

    def _analysis_finished(self) -> None:
        if self._analysis_started_at is not None:
            self._stop_analysis_clock()
        self._active_worker = None
        self._active_analysis_profile = None
        self._active_operation = None
        self._set_busy(False)
