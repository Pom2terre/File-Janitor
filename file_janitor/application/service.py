"""Façade applicative indépendante des interfaces utilisateur.

La CLI et la future GUI doivent pouvoir appeler cette API sans connaître les
détails d'ouverture du HistoryStore ni les signatures internes de l'exécuteur.
"""

from __future__ import annotations

import errno
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile

from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from file_janitor import scheduler
from file_janitor.executor import (
    KeeperGuard,
    execute_items,
    resolve_destination_path,
    undo_batch,
)
from file_janitor.models import (
    ActionItem,
    ActionKind,
    CATEGORY_LABELS,
    ConflictPolicy,
    FileCategory,
    FileIdentity,
    FileRecord,
    Plan,
    ScanResult,
)
from file_janitor.archiving import ArchiveFormat, build_archive_actions
from file_janitor.plan.builder import PlanConfig, build_plan
from file_janitor.path_safety import (
    PathSafetyError,
    current_file_identity,
    validate_destination_path,
    validate_source_file,
    validate_source_empty_directory,
    validate_source_identity,
)
from file_janitor.plan.grouping import (
    DateGranularity,
    GroupBy,
    destination_folder,
)
from file_janitor.plan.templates import DEFAULT_TEMPLATE_PATTERN, Template
from file_janitor.renaming import build_rename_actions
from file_janitor.report import (
    build_report_data,
    render_html_report,
    write_html_report,
)
from file_janitor.scanner.scan import scan_directory
from file_janitor.application.remote import (
    RemoteFolderInfo,
    _rclone_remote_spec,
    remote_folder_info,
    scan_rclone_empty_directories,
    scan_rclone_metadata,
)
from file_janitor.scanner.empty_directories import (
    EmptyDirectoryScan,
    scan_empty_directories,
)
from file_janitor.recovery import (
    CrashRecoveryReport,
    PlannedRecoveryState,
    recovery_state_from_persisted_error,
    resolve_unresolved_planned_operations_without_file_action,
    run_crash_recovery,
)
from file_janitor.storage.history import (
    PlannedRecoveryState,
    BatchStatus,
    Batch,
    HistoryStore,
    Operation,
    RecoveryResolutionKind,
)


class ClassificationMode(str, Enum):
    """Stratégies de classement proposées aux interfaces utilisateur."""

    EXTENSION = "extension"
    DATE = "date"
    COMMON_NAME = "common_name"
    SIZE = "size"


class _ArchiveSourceReadError(OSError):
    """Erreur de lecture d’une source pendant la préparation d’un ZIP."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.source_path = path
        super().__init__(
            cause.errno,
            f"lecture de la source {path}: {cause.strerror or cause}",
            str(path),
        )


def _iter_rclone_cat_chunks(
    path: Path,
    root: Path,
    remote_info: RemoteFolderInfo,
    *,
    cancel_callback: Callable[[], bool] | None,
    chunk_size: int = 8 * 1024 * 1024,
) -> Iterator[bytes]:
    """Lit un fichier rclone directement, sans repasser par le montage FUSE."""

    executable = shutil.which("rclone")
    remote_root = _rclone_remote_spec(root, remote_info)
    if executable is None or remote_root is None:
        raise OSError(errno.EIO, "rclone cat indisponible pour cette source")

    relative = path.relative_to(root).as_posix()
    if remote_root.endswith((":", "/")):
        remote_path = f"{remote_root}{relative}"
    else:
        remote_path = f"{remote_root}/{relative}"

    errors = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(
            [executable, "cat", remote_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
    except OSError as exc:
        errors.close()
        raise OSError(
            errno.EIO,
            f"impossible de lancer rclone cat : {exc}",
        ) from exc

    assert process.stdout is not None
    try:
        while True:
            if cancel_callback is not None and cancel_callback():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise CancelledError()
            chunk = process.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk

        return_code = process.wait()
        if return_code != 0:
            errors.seek(0)
            detail = errors.read(4096).decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise OSError(
                errno.EIO,
                f"rclone cat a échoué (code {return_code}){suffix}",
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        errors.close()


@dataclass(frozen=True, slots=True)
class ClassificationGroupSummary:
    """Groupe de destination proposé, indépendant de l'interface."""

    name: str
    item_count: int
    total_size: int


def classification_plan_config(
    mode: ClassificationMode,
    *,
    destination_root: Path | None = None,
    secondary_mode: ClassificationMode | None = None,
) -> PlanConfig:
    """Traduit un choix produit stable vers la configuration du planificateur.

    Les modes exposés à l'utilisateur s'appliquent à tous les fichiers
    trouvés par le scan, y compris dans les sous-dossiers.
    """

    if secondary_mode is not None:
        mapping = {
            ClassificationMode.EXTENSION: GroupBy.EXTENSION,
            ClassificationMode.DATE: GroupBy.DATE,
            ClassificationMode.SIZE: GroupBy.SIZE,
            ClassificationMode.COMMON_NAME: GroupBy.COMMON_NAME,
        }
        if mode not in mapping or secondary_mode not in mapping or mode is secondary_mode:
            raise ValueError("Choisissez deux critères de classement distincts")
        return PlanConfig(
            group_by=mapping[mode],
            secondary_group_by=mapping[secondary_mode],
            date_granularity=DateGranularity.MONTH,
            destination_root=destination_root,
            recursive_sort=True,
        )

    if mode is ClassificationMode.EXTENSION:
        return PlanConfig(
            group_by=GroupBy.EXTENSION,
            destination_root=destination_root,
            recursive_sort=True,
        )
    if mode is ClassificationMode.DATE:
        return PlanConfig(
            group_by=GroupBy.DATE,
            date_granularity=DateGranularity.MONTH,
            destination_root=destination_root,
            recursive_sort=True,
        )
    if mode is ClassificationMode.COMMON_NAME:
        return PlanConfig(
            group_by=GroupBy.EXTENSION,
            template=Template.compile(DEFAULT_TEMPLATE_PATTERN),
            template_only=True,
            template_min_group_size=2,
            infer_common_roots=True,
            destination_root=destination_root,
            recursive_sort=True,
        )
    if mode is ClassificationMode.SIZE:
        return PlanConfig(
            group_by=GroupBy.SIZE,
            destination_root=destination_root,
            recursive_sort=True,
        )
    raise ValueError(f"mode de classement non pris en charge : {mode!r}")


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    """Résultat applicatif d'une exécution."""

    batch_id: int | None
    success: int
    errors: tuple[str, ...]
    cancelled: bool = False
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class UndoResult:
    """Résultat applicatif d'un undo."""

    success: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoryOperationSummary:
    """Vue read-only d'une opération d'historique."""

    kind: str
    original_path: Path | None
    stored_path: Path | None
    size: int | None
    category: str | None
    status: str
    error: str | None
    original_path_raw: object | None = None
    stored_path_raw: object | None = None
    size_raw: object | None = None
    category_raw: object | None = None
    status_raw: str | None = None
    recovery_state: PlannedRecoveryState | None = None
    recovery_state_raw: str | None = None
    recovery_attention_required: bool = False
    resolution_kind: RecoveryResolutionKind | None = None
    resolution_kind_raw: str | None = None
    resolved_at: datetime | None = None
    resolved_at_raw: str | None = None
    recovery_audit_issue: str | None = None
    stored_identity_issue: str | None = None
    core_metadata_issues: tuple[str, ...] = ()
    conflict_policy: str | None = None
    conflict_policy_raw: object | None = None
    original_identity_issue: str | None = None
    resume_candidate: bool = False
    resume_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HistoryQueuedResumeValidation:
    """Résultat instantané et read-only d'une revalidation QUEUED.

    ``live_ready`` n'est jamais une autorisation durable : toute future
    mutation doit répéter les mêmes contrôles immédiatement avant son I/O.
    """

    operation_id: int
    kind: str
    original_path: Path | None
    stored_path: Path | None
    conflict_policy: str | None
    metadata_candidate: bool
    live_checked: bool
    live_ready: bool
    effective_destination: Path | None = None
    blockers: tuple[str, ...] = ()
    diagnostic: str | None = None
    revalidation_required_before_io: bool = True


@dataclass(frozen=True, slots=True)
class HistoryBatchSummary:
    """Vue read-only des métadonnées d'un batch d'historique."""

    id: int
    created_at: str
    root: str | None
    status: str
    undone: bool | None
    created_at_raw: str | None = None
    status_raw: str | None = None
    root_raw: object | None = None
    undone_raw: object | None = None
    core_metadata_issues: tuple[str, ...] = ()
    planned_count: int | None = 0
    success_count: int | None = 0
    failed_count: int | None = 0
    skipped_count: int | None = 0
    planned_count_raw: object | None = None
    success_count_raw: object | None = None
    failed_count_raw: object | None = None
    skipped_count_raw: object | None = None
    consistency_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CategorySummary:
    """Résumé applicatif d’une catégorie d’analyse."""

    key: str
    label: str
    item_count: int
    total_size: int
    display_metric: str


@dataclass(frozen=True, slots=True)
class AnalysisSummary:
    """Résumé d’analyse indépendant de toute interface utilisateur."""

    root: Path
    total_count: int
    total_size: int
    categories: tuple[CategorySummary, ...]


@dataclass(frozen=True, slots=True)
class AnalysisItem:
    """Détail read-only d'un élément proposé par l'analyse."""

    path: Path
    size: int
    reason: str
    destination: Path | None
    action: str = "none"
    conflict: bool = False
    conflict_reason: str | None = None
    archive_format: str | None = None


@dataclass(frozen=True, slots=True)
class CategoryDetails:
    """Détails read-only des éléments d'une catégorie."""

    key: str
    label: str
    items: tuple[AnalysisItem, ...]


@dataclass(frozen=True, slots=True)
class AnalysisCapabilities:
    """Capacités coûteuses réellement calculées pendant une analyse."""

    hashes_computed: bool = True
    content_types_computed: bool = True
    metadata_computed: bool = True
    identities_computed: bool = True


@dataclass(frozen=True, slots=True)
class DuplicateGroupMember:
    """Membre read-only d'un groupe de doublons exacts."""

    path: Path
    size: int
    mtime: datetime | None = None
    identity: FileIdentity | None = None


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Groupe de fichiers partageant exactement le même hash."""

    key: str
    members: tuple[DuplicateGroupMember, ...]


@dataclass(frozen=True, slots=True)
class ResolvedDuplicateGroup:
    """Plan résolu d'un groupe de doublons après choix du keeper."""

    key: str
    keeper: Path
    preview_items: tuple[AnalysisItem, ...]
    actions: tuple[ActionItem, ...]

    @property
    def mutation_count(self) -> int:
        """Nombre d'opérations physiques prévues pour ce groupe."""

        return len(self.actions)


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Résultat applicatif complet d'une analyse et ses actions opaques."""

    summary: AnalysisSummary
    details: tuple[CategoryDetails, ...]
    capabilities: AnalysisCapabilities = AnalysisCapabilities()
    duplicate_groups: tuple[DuplicateGroup, ...] = ()
    scan_errors: tuple[str, ...] = ()
    _actions: tuple[tuple[str, int, ActionItem], ...] = ()

    def details_for(self, category_key: str) -> CategoryDetails | None:
        """Retourne les détails d'une catégorie par sa clé stable."""

        return next(
            (details for details in self.details if details.key == category_key),
            None,
        )


@dataclass(frozen=True, slots=True)
class SchedulePreview:
    """Prévisualisation portable d'une planification automatique."""

    cron_available: bool
    cron_expression: str | None
    command: str | None
    windows_hint: str | None


def _validate_schedule_action(action: str) -> None:
    """Valide une action planifiable avant d'appeler l'infrastructure."""

    if action not in {"sort", "clean"}:
        raise scheduler.ScheduleError(
            "l'action planifiée doit être 'sort' ou 'clean'"
        )


def preview_schedule(
    folder: Path,
    *,
    every: int,
    unit: str,
    action: str,
    extra_args: str | None = None,
) -> SchedulePreview:
    """Prépare une planification sans modifier la crontab."""

    _validate_schedule_action(action)

    if not scheduler.cron_available():
        return SchedulePreview(
            cron_available=False,
            cron_expression=None,
            command=None,
            windows_hint=scheduler.schtasks_hint(
                folder,
                every=every,
                unit=unit,
                action=action,
                extra_args=extra_args,
            ),
        )

    return SchedulePreview(
        cron_available=True,
        cron_expression=scheduler.build_cron_expression(every, unit),
        command=scheduler.build_command(folder, action, extra_args),
        windows_hint=None,
    )


def install_schedule(
    folder: Path,
    *,
    every: int,
    unit: str,
    action: str,
    extra_args: str | None = None,
) -> scheduler.ScheduledJob:
    """Installe une tâche périodique via l'adaptateur de scheduling."""

    _validate_schedule_action(action)
    return scheduler.install_job(
        folder,
        every=every,
        unit=unit,
        action=action,
        extra_args=extra_args,
    )


def list_schedules() -> list[scheduler.ScheduledJob]:
    """Retourne les tâches File Janitor actuellement planifiées."""

    return scheduler.list_jobs()


def remove_schedule(job_id: str) -> bool:
    """Supprime une tâche planifiée par son identifiant."""

    return scheduler.remove_job(job_id)


def scan_folder(
    folder: Path,
    *,
    compute_hashes: bool = True,
    compute_content_type: bool = True,
    follow_symlinks: bool = False,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    hash_workers: int | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    collect_metadata: bool = True,
    use_remote_metadata: bool = True,
) -> ScanResult:
    """Scanne un dossier sans modifier le filesystem."""

    # Remote / rapide + métadonnées : contourner le mount FUSE lorsque
    # rclone peut fournir les métadonnées directement depuis le backend.
    # Ce chemin est particulièrement important pour le classement Date/Taille.
    if (
        use_remote_metadata
        and collect_metadata
        and not compute_hashes
        and not compute_content_type
        and not follow_symlinks
    ):
        remote_scan = scan_rclone_metadata(
            folder,
            exclude_patterns=exclude_patterns,
            use_default_excludes=use_default_excludes,
            use_ignore_file=use_ignore_file,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        if remote_scan is not None:
            return remote_scan

    kwargs = dict(
        compute_hashes=compute_hashes,
        compute_content_type=compute_content_type,
        follow_symlinks=follow_symlinks,
        exclude_patterns=exclude_patterns,
        use_default_excludes=use_default_excludes,
        use_ignore_file=use_ignore_file,
        hash_workers=hash_workers,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    if not collect_metadata:
        kwargs["collect_metadata"] = False
    return scan_directory(folder, **kwargs)


def create_plan(
    scan: ScanResult,
    config: PlanConfig | None = None,
) -> Plan:
    """Construit un plan à partir d'un résultat de scan."""

    return build_plan(
        scan,
        config,
    )


def summarize_analysis(scan: ScanResult, plan: Plan) -> AnalysisSummary:
    """Construit le résumé d’analyse consommable par une interface utilisateur.

    La couche applicative porte ici la sémantique des catégories et indique
    quelle métrique est pertinente pour l’affichage. Le formatage humain
    (octets, nombres, widgets, couleurs) reste la responsabilité de l’UI.
    """

    count_categories = {
        FileCategory.TO_SORT,
        FileCategory.EXTENSION_MISMATCH,
    }
    categories = tuple(
        CategorySummary(
            key=category.value,
            label=label,
            item_count=len(plan.items(category)),
            total_size=plan.total_size(category),
            display_metric=(
                "count" if category in count_categories else "size"
            ),
        )
        for category, label in CATEGORY_LABELS.items()
    )

    return AnalysisSummary(
        root=scan.root,
        total_count=scan.total_count,
        total_size=scan.total_size,
        categories=categories,
    )


def build_analysis_result(
    scan: ScanResult,
    plan: Plan,
    *,
    hashes_computed: bool = True,
    content_types_computed: bool = True,
    metadata_computed: bool = True,
    identities_computed: bool | None = None,
) -> AnalysisResult:
    """Construit un résultat d'analyse read-only pour les interfaces."""

    if identities_computed is None:
        identities_computed = metadata_computed

    summary = summarize_analysis(scan, plan)
    details = tuple(
        CategoryDetails(
            key=category.value,
            label=label,
            items=tuple(
                AnalysisItem(
                    path=item.path,
                    size=item.size,
                    reason=item.reason,
                    destination=item.destination,
                    action=item.action.value,
                    conflict=item.conflict,
                    conflict_reason=item.conflict_reason,
                )
                for item in plan.items(category)
            ),
        )
        for category, label in CATEGORY_LABELS.items()
    )
    actions = tuple(
        (category.value, index, item)
        for category, _label in CATEGORY_LABELS.items()
        for index, item in enumerate(plan.items(category))
    )

    by_hash: dict[str, list[DuplicateGroupMember]] = {}
    for record in scan.files:
        # Garder le dialogue de résolution cohérent avec le planificateur :
        # un fichier vide n'est jamais proposé comme doublon à supprimer.
        if record.size > 0 and record.hash:
            by_hash.setdefault(record.hash, []).append(
                DuplicateGroupMember(
                    path=record.path,
                    size=record.size,
                    mtime=record.mtime,
                    identity=record.identity,
                )
            )
    duplicate_groups = tuple(
        DuplicateGroup(key=file_hash, members=tuple(members))
        for file_hash, members in sorted(by_hash.items())
        if len(members) >= 2
    )

    return AnalysisResult(
        summary=summary,
        details=details,
        capabilities=AnalysisCapabilities(
            hashes_computed=hashes_computed,
            content_types_computed=content_types_computed,
            metadata_computed=metadata_computed,
            identities_computed=identities_computed,
        ),
        duplicate_groups=duplicate_groups,
        scan_errors=tuple(scan.errors),
        _actions=actions,
    )


def classification_group_name(
    item: AnalysisItem,
    mode: ClassificationMode = ClassificationMode.EXTENSION,
    secondary_mode: ClassificationMode | None = None,
) -> str:
    """Clé du dossier de destination affichée et utilisée pour filtrer l'aperçu."""

    if item.destination is None:
        raise ValueError("L'action n'a pas de destination de classement")
    if secondary_mode is not None:
        return f"{item.destination.parent.parent.name}/{item.destination.parent.name}"
    return item.destination.parent.name


def summarize_classification_groups(
    result: AnalysisResult,
    mode: ClassificationMode = ClassificationMode.EXTENSION,
    secondary_mode: ClassificationMode | None = None,
) -> tuple[ClassificationGroupSummary, ...]:
    """Résume les dossiers de destination proposés pour les fichiers à classer."""

    details = result.details_for(FileCategory.TO_SORT.value)
    if details is None:
        return ()

    grouped: dict[str, tuple[int, int]] = {}
    for item in details.items:
        if item.destination is None:
            continue
        name = classification_group_name(item, mode, secondary_mode)
        count, total_size = grouped.get(name, (0, 0))
        grouped[name] = (count + 1, total_size + item.size)

    return tuple(
        ClassificationGroupSummary(name=name, item_count=count, total_size=total_size)
        for name, (count, total_size) in sorted(grouped.items(), key=lambda entry: entry[0].casefold())
    )


def analyze_folder(
    folder: Path,
    *,
    config: PlanConfig | None = None,
    compute_hashes: bool = True,
    compute_content_type: bool = True,
    follow_symlinks: bool = False,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    hash_workers: int | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    collect_metadata: bool = True,
) -> AnalysisResult:
    """Scanne, planifie et prépare un résultat read-only en un seul cas d'usage."""

    scan_kwargs = dict(
        compute_hashes=compute_hashes,
        compute_content_type=compute_content_type,
        follow_symlinks=follow_symlinks,
        exclude_patterns=exclude_patterns,
        use_default_excludes=use_default_excludes,
        use_ignore_file=use_ignore_file,
        hash_workers=hash_workers,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    if not collect_metadata:
        scan_kwargs["collect_metadata"] = False
    scan = scan_folder(folder, **scan_kwargs)
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("planning")
    plan = create_plan(scan, config)
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("result")
    result_kwargs = dict(
        hashes_computed=compute_hashes,
        content_types_computed=compute_content_type,
    )
    if not getattr(scan, "identities_complete", collect_metadata):
        result_kwargs["identities_computed"] = False
    if not collect_metadata:
        result_kwargs["metadata_computed"] = False
    result = build_analysis_result(scan, plan, **result_kwargs)
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    return result


def analyze_rename_folder(
    folder: Path,
    pattern: str,
    *,
    recursive: bool = False,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> AnalysisResult:
    """Prépare une prévisualisation de renommage sans modifier le disque."""

    scan = scan_folder(
        folder,
        compute_hashes=False,
        compute_content_type=False,
        exclude_patterns=exclude_patterns,
        use_default_excludes=use_default_excludes,
        use_ignore_file=use_ignore_file,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("planning")

    actions = build_rename_actions(scan, pattern, recursive=recursive)
    details_items = tuple(
        AnalysisItem(
            path=item.path,
            size=item.size,
            reason=item.reason,
            destination=item.destination,
            action=item.action.value,
            conflict=item.conflict,
            conflict_reason=item.conflict_reason,
        )
        for item in actions
    )
    actionable_count = sum(item.action is ActionKind.MOVE for item in actions)
    total_size = sum(item.size for item in actions)
    details = CategoryDetails(
        key=FileCategory.RENAME.value,
        label=CATEGORY_LABELS[FileCategory.RENAME],
        items=details_items,
    )
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=scan.root,
            total_count=len(actions),
            total_size=total_size,
            categories=(
                CategorySummary(
                    key=FileCategory.RENAME.value,
                    label=CATEGORY_LABELS[FileCategory.RENAME],
                    item_count=actionable_count,
                    total_size=sum(
                        item.size
                        for item in actions
                        if item.action is ActionKind.MOVE
                    ),
                    display_metric="count",
                ),
            ),
        ),
        details=(details,),
        capabilities=AnalysisCapabilities(
            hashes_computed=False,
            content_types_computed=False,
            metadata_computed=True,
            identities_computed=scan.identities_complete,
        ),
        scan_errors=tuple(scan.errors),
        _actions=tuple(
            (FileCategory.RENAME.value, index, item)
            for index, item in enumerate(actions)
        ),
    )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("result")
    return result


def rename_actions(result: AnalysisResult) -> list[ActionItem]:
    """Retourne uniquement les renommages valides de la prévisualisation."""

    return [
        item
        for category, _index, item in result._actions
        if category == FileCategory.RENAME.value and item.action is ActionKind.MOVE
    ]

def analyze_archive_folder(
    folder: Path,
    destination: Path,
    older_than_days: int,
    *,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    archive_format: ArchiveFormat | str = ArchiveFormat.FOLDERS,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    now: datetime | None = None,
) -> AnalysisResult:
    """Prépare une prévisualisation d'archivage sans modifier les fichiers."""

    scan = scan_folder(
        folder,
        compute_hashes=False,
        compute_content_type=False,
        exclude_patterns=exclude_patterns,
        use_default_excludes=use_default_excludes,
        use_ignore_file=use_ignore_file,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        # L'archivage doit voir les mêmes entrées que le dossier monté, même
        # si un listing rclone distant est incomplet ou en retard.
        use_remote_metadata=False,
    )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("planning")

    actions = build_archive_actions(
        scan,
        destination,
        older_than_days,
        archive_format=archive_format,
        now=now,
    )
    label = CATEGORY_LABELS[FileCategory.ARCHIVE]
    detail_items = tuple(
        AnalysisItem(
            path=item.path,
            size=item.size,
            reason=item.reason,
            destination=item.destination,
            action=item.action.value,
            conflict=item.conflict,
            conflict_reason=item.conflict_reason,
            archive_format=item.archive_format,
        )
        for item in actions
    )
    valid_count = sum(item.action is ActionKind.MOVE for item in actions)
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=scan.root,
            total_count=len(actions),
            total_size=sum(item.size for item in actions),
            categories=(
                CategorySummary(
                    key=FileCategory.ARCHIVE.value,
                    label=label,
                    item_count=valid_count,
                    total_size=sum(
                        item.size for item in actions if item.action is ActionKind.MOVE
                    ),
                    display_metric="count",
                ),
            ),
        ),
        details=(
            CategoryDetails(
                key=FileCategory.ARCHIVE.value,
                label=label,
                items=detail_items,
            ),
        ),
        capabilities=AnalysisCapabilities(
            hashes_computed=False,
            content_types_computed=False,
            metadata_computed=True,
            identities_computed=scan.identities_complete,
        ),
        scan_errors=tuple(scan.errors),
        _actions=tuple(
            (FileCategory.ARCHIVE.value, index, item)
            for index, item in enumerate(actions)
        ),
    )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("result")
    return result


def archive_actions(result: AnalysisResult) -> list[ActionItem]:
    """Retourne les seuls déplacements archivables sans conflit."""

    return [
        item
        for category, _index, item in result._actions
        if category == FileCategory.ARCHIVE.value and item.action is ActionKind.MOVE
    ]


def analyze_empty_directories(
    folder: Path,
    *,
    exclude_patterns: list[str] | None = None,
    use_default_excludes: bool = True,
    use_ignore_file: bool = True,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> AnalysisResult:
    """Repère les dossiers vides sans effectuer de suppression."""

    source = Path(folder).expanduser()
    remote_info = remote_folder_info(source)
    if remote_info.is_remote:
        remote_scan = scan_rclone_empty_directories(
            source,
            exclude_patterns=exclude_patterns,
            use_default_excludes=use_default_excludes,
            use_ignore_file=use_ignore_file,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        if remote_scan is not None:
            scan = remote_scan
        elif _supports_native_pcloud_directory_scan(remote_info):
            # Le client pCloud natif expose son contenu via FUSE (souvent avec
            # le type générique ``fuse`` et la source ``pCloud.fs``). Il n'a
            # pas de cible rclone à lister : utiliser scandir sur CE montage
            # identifié, tout en gardant le contrôle atomique rmdir à
            # l'exécution. Les autres montages distants restent fail-closed.
            scan = scan_empty_directories(
                source,
                exclude_patterns=exclude_patterns,
                use_default_excludes=use_default_excludes,
                use_ignore_file=use_ignore_file,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        else:
            # Sur les montages distants sans listing backend fiable, ne pas
            # interpréter une vue FUSE partielle comme des dossiers vides.
            root = source.resolve(strict=True)
            filesystem = remote_info.filesystem_type or "distant"
            scan = EmptyDirectoryScan(
                root,
                (),
                (
                    f"{root}: détection des dossiers vides non disponible "
                    f"de façon sûre sur {filesystem}; aucune suppression proposée.",
                ),
            )
    else:
        scan = scan_empty_directories(
            source,
            exclude_patterns=exclude_patterns,
            use_default_excludes=use_default_excludes,
            use_ignore_file=use_ignore_file,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("planning")

    label = CATEGORY_LABELS[FileCategory.EMPTY_DIRECTORY]
    actions = [
        ActionItem(
            category=FileCategory.EMPTY_DIRECTORY,
            path=record.path,
            size=record.identity.size,
            reason=(
                "Dossier vide après suppression des sous-dossiers vides"
                if record.child_directories
                else "Dossier vide"
            ),
            identity=record.identity,
            action=ActionKind.RMDIR,
        )
        for record in scan.directories
    ]
    details = CategoryDetails(
        key=FileCategory.EMPTY_DIRECTORY.value,
        label=label,
        items=tuple(
            AnalysisItem(
                path=item.path,
                size=0,
                reason=item.reason,
                destination=None,
                action=item.action.value,
            )
            for item in actions
        ),
    )
    result = AnalysisResult(
        summary=AnalysisSummary(
            root=scan.root,
            total_count=len(actions),
            total_size=0,
            categories=(
                CategorySummary(
                    key=FileCategory.EMPTY_DIRECTORY.value,
                    label=label,
                    item_count=len(actions),
                    total_size=0,
                    display_metric="count",
                ),
            ),
        ),
        details=(details,),
        capabilities=AnalysisCapabilities(
            hashes_computed=False,
            content_types_computed=False,
            metadata_computed=True,
            identities_computed=True,
        ),
        scan_errors=scan.errors,
        _actions=tuple(
            (FileCategory.EMPTY_DIRECTORY.value, index, item)
            for index, item in enumerate(actions)
        ),
    )
    if cancel_callback is not None and cancel_callback():
        raise CancelledError()
    if progress_callback is not None:
        progress_callback("result")
    return result


def _supports_native_pcloud_directory_scan(remote_info: RemoteFolderInfo) -> bool:
    """Indique que le parcours FUSE natif pCloud est suffisamment identifié."""

    filesystem = getattr(remote_info, "filesystem_type", None)
    source = getattr(remote_info, "source", None)
    filesystem = (filesystem or "").casefold()
    source = (source or "").casefold()
    return filesystem == "fuse.pcloud" or (
        filesystem == "fuse" and source == "pcloud.fs"
    )


def empty_directory_actions(result: AnalysisResult) -> list[ActionItem]:
    """Retourne les suppressions de dossiers vides du résultat d'analyse."""

    return [
        item
        for category, _index, item in result._actions
        if category == FileCategory.EMPTY_DIRECTORY.value
        and item.action is ActionKind.RMDIR
    ]


def _category_actions(plan: Plan, category: FileCategory) -> list[ActionItem]:
    """Retourne une copie de la sélection d'une catégorie du plan."""

    return list(plan.items(category))


def sort_actions(plan: Plan) -> list[ActionItem]:
    """Retourne les actions du cas d'usage « classer »."""

    return _category_actions(plan, FileCategory.TO_SORT)


def clean_actions(plan: Plan) -> list[ActionItem]:
    """Retourne les actions du cas d'usage « nettoyer »."""

    return [
        *_category_actions(plan, FileCategory.DUPLICATE),
        *_category_actions(plan, FileCategory.OLD_ARCHIVE),
    ]


def duplicate_actions(plan: Plan) -> list[ActionItem]:
    """Retourne les doublons proposés par le plan."""

    return _category_actions(plan, FileCategory.DUPLICATE)


def large_file_actions(plan: Plan) -> list[ActionItem]:
    """Retourne les fichiers volumineux proposés par le plan."""

    return _category_actions(plan, FileCategory.LARGE_FILE)


def mismatch_actions(plan: Plan) -> list[ActionItem]:
    """Retourne les incohérences d'extension proposées par le plan."""

    return _category_actions(plan, FileCategory.EXTENSION_MISMATCH)


def build_report(scan: ScanResult, plan: Plan) -> dict:
    """Construit les données sérialisables d'un rapport d'analyse."""

    return build_report_data(scan, plan)


def render_report_html(
    scan: ScanResult,
    plan: Plan,
    *,
    max_items_per_category: int,
) -> str:
    """Rend un rapport HTML sans effectuer d'écriture sur le disque."""

    return render_html_report(
        scan,
        plan,
        max_items_per_category=max_items_per_category,
    )


def write_report_html(
    scan: ScanResult,
    plan: Plan,
    output: Path,
    *,
    max_items_per_category: int,
) -> None:
    """Écrit un rapport HTML via la façade applicative."""

    write_html_report(
        scan,
        plan,
        output,
        max_items_per_category=max_items_per_category,
    )


def as_copy_actions(items: list[ActionItem]) -> list[ActionItem]:
    """Retourne des actions où les MOVE sont traduits en COPY.

    Cette transformation exprime une intention applicative (copier plutôt que
    déplacer) et reste indépendante de toute interface utilisateur. Les objets
    ``ActionItem`` d'origine ne sont jamais modifiés.
    """

    return [
        replace(item, action=ActionKind.COPY)
        if item.action is ActionKind.MOVE
        else item
        for item in items
    ]


class HistoryDatabaseError(RuntimeError):
    """Erreur stable exposée quand SQLite ne peut pas être ouvert."""


def _open_history_store(db_path: Path | None) -> HistoryStore:
    """Ouvre l'historique et traduit seulement les échecs d'ouverture."""

    try:
        return (
            HistoryStore()
            if db_path is None
            else HistoryStore(db_path=db_path)
        )
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        raise HistoryDatabaseError(
            "L’historique SQLite est indisponible ou endommagé."
        ) from exc

def execute_actions(
    items: list[ActionItem],
    *,
    root: str,
    db_path: Path | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    dependent_trash_keepers: dict[Path, Path] | None = None,
    dependent_trash_keeper_guards: dict[Path, KeeperGuard] | None = None,
    allow_unsafe_fast_move: bool = False,
) -> ExecuteResult:
    """Exécute des actions et gère le cycle de vie de l'historique."""

    store = _open_history_store(db_path)

    actionable_count = sum(
        1 for item in items if item.action is not ActionKind.NONE
    )
    execute_kwargs = dict(
        root=root,
        store=store,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    if dependent_trash_keepers:
        execute_kwargs["dependent_trash_keepers"] = dependent_trash_keepers
    if dependent_trash_keeper_guards:
        execute_kwargs["dependent_trash_keeper_guards"] = (
            dependent_trash_keeper_guards
        )
    if allow_unsafe_fast_move:
        execute_kwargs["allow_unsafe_fast_move"] = True

    try:
        batch_id, success, errors = execute_items(
            items,
            **execute_kwargs,
        )
        batch = store.get_batch(batch_id)
        cancelled = bool(
            batch is not None and batch.status is BatchStatus.CANCELLED
        )
    finally:
        store.close()

    attempted = success + len(errors)
    skipped = (
        batch.skipped_count
        if batch is not None
        else (max(0, actionable_count - attempted) if cancelled else 0)
    )
    return ExecuteResult(
        batch_id=batch_id,
        success=success,
        errors=tuple(errors),
        cancelled=cancelled,
        skipped=skipped,
    )



def _execute_zip_archive(
    items: list[ActionItem],
    *,
    root: Path,
    db_path: Path | None,
    progress_callback: Callable[[object], None] | None,
    cancel_callback: Callable[[], bool] | None,
) -> ExecuteResult:
    """Crée un ZIP vérifié, puis met ses sources à la corbeille."""

    destinations = {item.destination for item in items}
    if len(destinations) != 1 or None in destinations:
        return ExecuteResult(
            batch_id=None,
            success=0,
            errors=("destination ZIP incohérente dans la prévisualisation",),
            skipped=len(items),
        )
    destination = next(iter(destinations))
    assert destination is not None
    if destination.exists() or destination.is_symlink():
        return ExecuteResult(
            batch_id=None,
            success=0,
            errors=(f"{destination}: l’archive existe déjà",),
            skipped=len(items),
        )

    try:
        with tempfile.TemporaryDirectory(prefix="file-janitor-archive-") as temp_dir:
            staged_zip = Path(temp_dir) / "archive.zip"
            source_remote_info = remote_folder_info(root)
            is_remote_source = source_remote_info.is_remote
            can_rclone_cat = bool(
                (source_remote_info.filesystem_type or "")
                .lower()
                .startswith("fuse.rclone")
            )
            rclone_fallback_paths: set[Path] = set()
            max_read_attempts = 3 if is_remote_source else 1
            for attempt in range(1, max_read_attempts + 1):
                try:
                    with zipfile.ZipFile(
                        staged_zip,
                        mode="w",
                        compression=zipfile.ZIP_DEFLATED,
                        compresslevel=6,
                        allowZip64=True,
                    ) as archive:
                        for index, item in enumerate(items, start=1):
                            if cancel_callback is not None and cancel_callback():
                                return ExecuteResult(
                                    batch_id=None,
                                    success=0,
                                    errors=(),
                                    cancelled=True,
                                    skipped=len(items),
                                )
                            try:
                                relative = item.path.relative_to(root)
                            except ValueError as exc:
                                raise ValueError(
                                    f"{item.path}: le fichier sort du dossier source"
                                ) from exc
                            use_rclone = item.path in rclone_fallback_paths
                            try:
                                validate_source_file(item.path)
                                validate_source_identity(item.path, item.identity)
                                zip_info = zipfile.ZipInfo.from_file(
                                    item.path,
                                    arcname=relative.as_posix(),
                                )
                                source_file = (
                                    None
                                    if use_rclone
                                    else item.path.open("rb")
                                )
                            except OSError as exc:
                                if is_remote_source and exc.errno == errno.EIO:
                                    if can_rclone_cat:
                                        rclone_fallback_paths.add(item.path)
                                    raise _ArchiveSourceReadError(
                                        item.path, exc
                                    ) from exc
                                raise

                            zip_info.compress_type = zipfile.ZIP_DEFLATED
                            copied = 0
                            started = time.monotonic()
                            if use_rclone:
                                chunks = iter(
                                    _iter_rclone_cat_chunks(
                                        item.path,
                                        root,
                                        source_remote_info,
                                        cancel_callback=cancel_callback,
                                    )
                                )
                            else:
                                source_file_context = source_file
                                assert source_file_context is not None
                                chunks = iter(
                                    lambda: source_file_context.read(
                                        8 * 1024 * 1024
                                    ),
                                    b"",
                                )
                            try:
                                with archive.open(
                                    zip_info,
                                    mode="w",
                                    force_zip64=True,
                                ) as zip_member:
                                    while True:
                                        try:
                                            chunk = next(chunks)
                                        except StopIteration:
                                            break
                                        except OSError as exc:
                                            if (
                                                is_remote_source
                                                and exc.errno == errno.EIO
                                            ):
                                                if can_rclone_cat:
                                                    rclone_fallback_paths.add(item.path)
                                                raise _ArchiveSourceReadError(
                                                    item.path, exc
                                                ) from exc
                                            raise
                                        if (
                                            cancel_callback is not None
                                            and cancel_callback()
                                        ):
                                            raise CancelledError()
                                        zip_member.write(chunk)
                                        copied += len(chunk)
                                        if progress_callback is not None:
                                            progress_callback(
                                                (
                                                    "archive_compress",
                                                    index,
                                                    len(items),
                                                    str(item.path),
                                                    copied,
                                                    item.size,
                                                    time.monotonic() - started,
                                                )
                                            )
                            finally:
                                if not use_rclone and source_file is not None:
                                    source_file.close()
                            if copied != item.size:
                                cause = OSError(
                                    errno.EIO,
                                    f"{copied} octet(s) lus sur {item.size} attendu(s)",
                                )
                                if is_remote_source:
                                    if can_rclone_cat:
                                        rclone_fallback_paths.add(item.path)
                                    raise _ArchiveSourceReadError(
                                        item.path, cause
                                    ) from cause
                                raise cause
                            try:
                                validate_source_identity(item.path, item.identity)
                            except OSError as exc:
                                if is_remote_source and exc.errno == errno.EIO:
                                    if can_rclone_cat:
                                        rclone_fallback_paths.add(item.path)
                                    raise _ArchiveSourceReadError(
                                        item.path, exc
                                    ) from exc
                                raise
                            if progress_callback is not None and copied == 0:
                                progress_callback(
                                    (
                                        "archive_compress",
                                        index,
                                        len(items),
                                        str(item.path),
                                    )
                                )
                    break
                except _ArchiveSourceReadError as exc:
                    if exc.errno != errno.EIO or attempt >= max_read_attempts:
                        raise OSError(
                            exc.errno,
                            f"{exc.strerror} (après {attempt} tentative(s))",
                            exc.filename,
                        ) from exc
                    # Sur FUSE/rclone, une lecture distante peut échouer
                    # momentanément. Le ZIP local est recréé de zéro avant
                    # de retenter, afin de ne jamais publier un membre partiel.
                    time.sleep(0.25 * attempt)

            with zipfile.ZipFile(staged_zip, mode="r") as archive:
                corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise zipfile.BadZipFile(
                    f"contrôle CRC invalide pour {corrupt_member}"
                )

            zip_identity = current_file_identity(staged_zip)
            zip_action = ActionItem(
                category=FileCategory.ARCHIVE,
                path=staged_zip,
                size=zip_identity.size,
                reason="Publier l’archive ZIP vérifiée",
                identity=zip_identity,
                destination=destination,
                action=ActionKind.COPY,
                conflict_policy=ConflictPolicy.SKIP,
            )
            source_trash_actions = [
                replace(
                    item,
                    action=ActionKind.TRASH,
                    destination=None,
                    reason="Source incluse dans l’archive ZIP vérifiée",
                )
                for item in items
            ]
            dependent_keepers = {
                item.path: staged_zip for item in source_trash_actions
            }
            dependent_guards = {
                item.path: KeeperGuard(staged_zip, zip_identity)
                for item in source_trash_actions
            }
            return execute_actions(
                [zip_action, *source_trash_actions],
                root=str(root),
                db_path=db_path,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                dependent_trash_keepers=dependent_keepers,
                dependent_trash_keeper_guards=dependent_guards,
            )
    except CancelledError:
        return ExecuteResult(
            batch_id=None,
            success=0,
            errors=(),
            cancelled=True,
            skipped=len(items),
        )
    except (OSError, PathSafetyError, ValueError, zipfile.BadZipFile) as exc:
        return ExecuteResult(
            batch_id=None,
            success=0,
            errors=(f"Préparation du ZIP impossible : {exc}",),
            skipped=len(items),
        )



def duplicate_groups_for_selection(
    result: AnalysisResult,
    selected: set[tuple[str, int]],
) -> tuple[DuplicateGroup, ...]:
    """Retourne les groupes de doublons explicitement visés par la sélection."""

    # La GUI peut maintenir sa propre sélection à partir des détails affichés,
    # notamment dans les adaptateurs/tests qui ne transportent pas ``_actions``.
    # Si aucune action Doublon n'est visée, cette étape préparatoire n'a rien à
    # résoudre et ne doit pas invalider une sélection ordinaire. La validation
    # stricte reste assurée par ``execute_selected_actions``.
    duplicate_selected = {
        key for key in selected if key[0] == FileCategory.DUPLICATE.value
    }
    if not duplicate_selected:
        return ()

    available = {(category, index): item for category, index, item in result._actions}
    unknown_duplicates = duplicate_selected.difference(available)
    if unknown_duplicates:
        raise ValueError("sélection de doublons invalide ou périmée")

    duplicate_paths = {
        available[key].path
        for key in duplicate_selected
    }
    if not duplicate_paths:
        return ()

    return tuple(
        group
        for group in result.duplicate_groups
        if any(member.path in duplicate_paths for member in group.members)
    )


def resolve_duplicate_groups_for_selection(
    result: AnalysisResult,
    selected: set[tuple[str, int]],
    keepers: dict[str, Path],
) -> tuple[ResolvedDuplicateGroup, ...]:
    """Résout les groupes visés en une Preview et un plan d'exécution uniques.

    Un groupe est l'unité atomique : sélectionner n'importe quelle action
    ``duplicate`` de ce groupe signifie traiter le groupe entier après choix
    d'un keeper. La Preview et l'exécution sont construites ici à partir de la
    même résolution afin qu'elles ne puissent plus diverger.
    """

    groups = duplicate_groups_for_selection(result, selected)
    required = {group.key for group in groups}
    if set(keepers) != required:
        raise ValueError(
            "un fichier à conserver doit être choisi pour chaque groupe de doublons"
        )

    resolved: list[ResolvedDuplicateGroup] = []
    for group in groups:
        members = {member.path: member for member in group.members}
        keeper = Path(keepers[group.key])
        keeper_member = members.get(keeper)
        if keeper_member is None:
            raise ValueError("fichier à conserver invalide pour le groupe de doublons")

        keeper_action = next(
            (
                item
                for category, _index, item in result._actions
                if category == FileCategory.TO_SORT.value
                and item.path == keeper
                and item.action in {ActionKind.MOVE, ActionKind.COPY}
            ),
            None,
        )

        actions: list[ActionItem] = []
        preview_items: list[AnalysisItem] = []
        if keeper_action is not None:
            actions.append(keeper_action)
            preview_items.append(
                AnalysisItem(
                    path=keeper_action.path,
                    size=keeper_action.size,
                    reason=f"Fichier conservé (keeper) · {keeper_action.reason}",
                    destination=keeper_action.destination,
                    action=keeper_action.action.value,
                    conflict=keeper_action.conflict,
                    conflict_reason=keeper_action.conflict_reason,
                )
            )
        else:
            preview_items.append(
                AnalysisItem(
                    path=keeper,
                    size=keeper_member.size,
                    reason="Fichier conservé (keeper) — aucune mutation",
                    destination=None,
                    action="keep",
                )
            )

        for member in group.members:
            if member.path == keeper:
                continue
            trash_action = ActionItem(
                category=FileCategory.DUPLICATE,
                path=member.path,
                size=member.size,
                reason=(
                    f"Doublon exact (hash {group.key[:8]}) — "
                    f"keeper : {keeper.name}"
                ),
                identity=member.identity,
                action=ActionKind.TRASH,
            )
            actions.append(trash_action)
            preview_items.append(
                AnalysisItem(
                    path=trash_action.path,
                    size=trash_action.size,
                    reason=trash_action.reason,
                    destination=None,
                    action=trash_action.action.value,
                )
            )

        resolved.append(
            ResolvedDuplicateGroup(
                key=group.key,
                keeper=keeper,
                preview_items=tuple(preview_items),
                actions=tuple(actions),
            )
        )

    return tuple(resolved)


def resolve_selected_actions(
    result: AnalysisResult,
    selected: set[tuple[str, int]],
    *,
    duplicate_keepers: dict[str, Path] | None = None,
) -> tuple[ActionItem, ...]:
    """Matérialise le plan physique correspondant exactement à la sélection.

    Les groupes de doublons sont atomiques : les actions individuelles du plan
    initial servent uniquement à identifier le ou les groupes concernés.
    """

    available = {(category, index): item for category, index, item in result._actions}
    unknown = selected.difference(available)
    if unknown:
        raise ValueError("sélection d'actions invalide ou périmée")

    groups = duplicate_groups_for_selection(result, selected)
    if not groups:
        return tuple(available[key] for key in sorted(selected))

    if duplicate_keepers is None:
        raise ValueError("la résolution interactive des doublons est requise")

    resolved_groups = resolve_duplicate_groups_for_selection(
        result,
        selected,
        duplicate_keepers,
    )
    touched_paths = {
        member.path
        for group in groups
        for member in group.members
    }

    items = [
        available[key]
        for key in sorted(selected)
        if available[key].path not in touched_paths
    ]
    for group in resolved_groups:
        items.extend(group.actions)
    return tuple(items)




def _duplicate_keeper_action(
    group: ResolvedDuplicateGroup,
    items_by_path: dict[Path, ActionItem],
) -> ActionItem | None:
    """Retourne l'action physique du keeper lorsqu'il doit être classé."""

    item = items_by_path.get(group.keeper)
    if item is None or item.action not in {ActionKind.MOVE, ActionKind.COPY}:
        return None
    return item


def _preflight_duplicate_groups(
    result: AnalysisResult,
    groups: tuple[ResolvedDuplicateGroup, ...],
    items: list[ActionItem],
) -> tuple[str, ...]:
    """Valide tous les membres d'un groupe avant sa première mutation.

    Le préflight est volontairement global pour les groupes sélectionnés :
    si un keeper, un non-keeper ou une destination de keeper n'est plus
    conforme à la prévisualisation, aucun batch n'est démarré.
    """

    items_by_path = {item.path: item for item in items}
    source_groups = {group.key: group for group in result.duplicate_groups}
    errors: list[str] = []

    for group in groups:
        source_group = source_groups.get(group.key)
        source_members = (
            {member.path: member for member in source_group.members}
            if source_group is not None
            else {}
        )
        subject = f"keeper « {group.keeper} »"

        try:
            for member in group.preview_items:
                subject = (
                    f"keeper « {member.path} »"
                    if member.path == group.keeper
                    else f"membre « {member.path} »"
                )
                validate_source_file(member.path)

                action = items_by_path.get(member.path)
                expected_identity = (
                    action.identity
                    if action is not None and action.identity is not None
                    else (
                        source_members[member.path].identity
                        if member.path in source_members
                        else None
                    )
                )
                validate_source_identity(member.path, expected_identity)

            keeper_action = _duplicate_keeper_action(group, items_by_path)
            if keeper_action is not None:
                subject = f"destination du keeper « {group.keeper} »"
                if keeper_action.destination is None:
                    raise ValueError("destination manquante")

                destination = validate_destination_path(
                    keeper_action.path,
                    keeper_action.destination,
                )
                if destination.exists():
                    raise FileExistsError(
                        "destination déjà occupée depuis la prévisualisation : "
                        f"{destination}"
                    )

        except (OSError, ValueError) as exc:
            errors.append(
                f"Groupe de doublons {group.key[:8]} : préflight refusé "
                f"pour {subject} : {exc}. "
                "Aucune mutation du groupe n'a été démarrée."
            )

    return tuple(errors)


def _duplicate_trash_dependencies(
    groups: tuple[ResolvedDuplicateGroup, ...],
    items: list[ActionItem],
) -> dict[Path, Path]:
    """Lie chaque TRASH au succès du classement physique de son keeper."""

    items_by_path = {item.path: item for item in items}
    dependencies: dict[Path, Path] = {}

    for group in groups:
        keeper_action = _duplicate_keeper_action(group, items_by_path)
        if keeper_action is None:
            continue

        for item in group.actions:
            if item.action is ActionKind.TRASH:
                dependencies[item.path] = keeper_action.path

    return dependencies


def _duplicate_trash_keeper_guards(
    result: AnalysisResult,
    groups: tuple[ResolvedDuplicateGroup, ...],
    items: list[ActionItem],
) -> dict[Path, KeeperGuard]:
    """Associe chaque TRASH à l'identité du keeper validée au préflight."""

    items_by_path = {item.path: item for item in items}
    source_groups = {group.key: group for group in result.duplicate_groups}
    guards: dict[Path, KeeperGuard] = {}

    for group in groups:
        keeper_action = items_by_path.get(group.keeper)
        identity = (
            keeper_action.identity
            if keeper_action is not None and keeper_action.identity is not None
            else None
        )
        if identity is None:
            source_group = source_groups.get(group.key)
            if source_group is not None:
                for member in source_group.members:
                    if member.path == group.keeper and member.identity is not None:
                        identity = member.identity
                        break
        if identity is None:
            identity = current_file_identity(group.keeper)

        guard = KeeperGuard(group.keeper, identity)
        for item in group.actions:
            if item.action is ActionKind.TRASH:
                guards[item.path] = guard

    return guards


def _validate_remote_classification_freshness(
    item: ActionItem,
    identity: FileIdentity,
) -> None:
    """Refuse une décision Remote devenue fausse depuis la prévisualisation.

    Une variation de métadonnée n'est bloquante que si elle change la classe
    de destination. Une taille qui reste dans la même tranche ou une date qui
    reste dans la même période conserve donc une prévisualisation valide.
    """

    guard = item.classification_guard
    if guard is None:
        return

    try:
        group_by = GroupBy(guard.group_by)
    except ValueError as exc:
        raise ValueError(
            "garde de classement invalide dans la prévisualisation"
        ) from exc

    if group_by not in {GroupBy.SIZE, GroupBy.DATE}:
        return

    granularity = DateGranularity.MONTH
    if group_by is GroupBy.DATE:
        try:
            granularity = DateGranularity(
                guard.date_granularity or DateGranularity.MONTH.value
            )
        except ValueError as exc:
            raise ValueError(
                "granularité de date invalide dans la prévisualisation"
            ) from exc

    current = FileRecord(
        path=item.path,
        size=identity.size,
        mtime=datetime.fromtimestamp(identity.mtime_ns / 1_000_000_000),
        extension=item.path.suffix.lower(),
    )
    current_folder = destination_folder(current, group_by, granularity)

    if current_folder != guard.folder:
        raise ValueError(
            "métadonnées de la source modifiées depuis la prévisualisation "
            f"(classe prévue « {guard.folder} », "
            f"classe actuelle « {current_folder} ») ; "
            "relancez l’analyse avant d’exécuter"
        )
    if guard.secondary_group_by is not None:
        try:
            secondary_by = GroupBy(guard.secondary_group_by)
        except ValueError as exc:
            raise ValueError("garde de classement secondaire invalide") from exc
        secondary_folder = destination_folder(current, secondary_by, granularity)
        if secondary_folder != guard.secondary_folder:
            raise ValueError(
                "métadonnées de la source modifiées depuis la prévisualisation "
                f"(classe prévue « {guard.secondary_folder} », "
                f"classe actuelle « {secondary_folder} ») ; relancez l’analyse"
            )


def execute_selected_actions(
    result: AnalysisResult,
    selected: set[tuple[str, int]],
    *,
    duplicate_keepers: dict[str, Path] | None = None,
    db_path: Path | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    allow_unsafe_fast_move: bool = False,
) -> ExecuteResult:
    """Exécute une sélection, avec résolution explicite des doublons exacts."""

    selected_duplicate_groups = duplicate_groups_for_selection(
        result,
        selected,
    )
    resolved_duplicate_groups: tuple[ResolvedDuplicateGroup, ...] = ()
    if selected_duplicate_groups:
        if duplicate_keepers is None:
            raise ValueError("la résolution interactive des doublons est requise")
        resolved_duplicate_groups = resolve_duplicate_groups_for_selection(
            result,
            selected,
            duplicate_keepers,
        )

    items = list(
        resolve_selected_actions(
            result,
            selected,
            duplicate_keepers=duplicate_keepers,
        )
    )

    if any(item.action is ActionKind.NONE for item in items):
        raise ValueError("une action sans effet ne peut pas être exécutée")

    # Un scan Remote / rapide ne fait volontairement aucun stat par fichier.
    # On capture l'identité forte uniquement pour les actions effectivement
    # choisies, juste avant de les remettre à l'exécuteur. Celui-ci revalide
    # ensuite cette identité avant toute mutation (TOCTOU).
    hydrated: list[ActionItem] = []
    for item in items:
        if cancel_callback is not None and cancel_callback():
            return ExecuteResult(
                batch_id=None,
                success=0,
                errors=(),
                cancelled=True,
                skipped=len(items),
            )
        if (
            not result.capabilities.identities_computed
            and item.identity is None
        ):
            # Les listings directs rclone fournissent taille/date mais pas
            # device/inode. Capturer l'identité forte uniquement au moment
            # où l'utilisateur exécute réellement l'action.
            identity = current_file_identity(item.path)
            _validate_remote_classification_freshness(item, identity)
            item = replace(item, size=identity.size, identity=identity)
        hydrated.append(item)

    # Une action lancée depuis la Preview doit respecter exactement le chemin
    # qui a été présenté et validé par l'utilisateur. Le moteur générique
    # conserve sa politique RENAME historique pour la CLI et les autres usages,
    # mais la frontière Preview -> Execute devient stricte : si la destination
    # affichée est désormais occupée, l'action échoue au lieu d'inventer un
    # nouveau nom silencieusement. Les ActionItem du résultat restent immuables
    # du point de vue de la Preview : on ne modifie que les copies exécutées.
    strict_items = [
        replace(item, conflict_policy=ConflictPolicy.SKIP)
        if item.action in {ActionKind.MOVE, ActionKind.COPY}
        else item
        for item in hydrated
    ]

    tagged_zip_archive = any(
        item.archive_format == ArchiveFormat.ZIP.value for item in strict_items
    )
    legacy_zip_shape = False
    if not tagged_zip_archive and len(strict_items) > 1:
        destinations = {item.destination for item in strict_items}
        legacy_zip_shape = (
            len(destinations) == 1
            and None not in destinations
            and next(iter(destinations)).suffix.lower() == ".zip"
            and all(item.category is FileCategory.ARCHIVE for item in strict_items)
        )
    if tagged_zip_archive or legacy_zip_shape:
        if not all(
            item.category is FileCategory.ARCHIVE
            and (
                item.archive_format == ArchiveFormat.ZIP.value
                or legacy_zip_shape
            )
            for item in strict_items
        ):
            raise ValueError(
                "l’archivage ZIP ne peut pas être combiné à d’autres catégories"
            )
        return _execute_zip_archive(
            strict_items,
            root=result.summary.root,
            db_path=db_path,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )

    if resolved_duplicate_groups:
        preflight_errors = _preflight_duplicate_groups(
            result,
            resolved_duplicate_groups,
            strict_items,
        )
        if preflight_errors:
            return ExecuteResult(
                batch_id=None,
                success=0,
                errors=preflight_errors,
                skipped=len(strict_items),
            )

    dependent_trash_keepers = _duplicate_trash_dependencies(
        resolved_duplicate_groups,
        strict_items,
    )
    dependent_trash_keeper_guards = _duplicate_trash_keeper_guards(
        result,
        resolved_duplicate_groups,
        strict_items,
    )

    execute_kwargs = {"db_path": db_path}
    if progress_callback is not None:
        execute_kwargs["progress_callback"] = progress_callback
    if cancel_callback is not None:
        execute_kwargs["cancel_callback"] = cancel_callback
    if dependent_trash_keepers:
        execute_kwargs["dependent_trash_keepers"] = dependent_trash_keepers
    if dependent_trash_keeper_guards:
        execute_kwargs["dependent_trash_keeper_guards"] = (
            dependent_trash_keeper_guards
        )
    if allow_unsafe_fast_move:
        execute_kwargs["allow_unsafe_fast_move"] = True
    return execute_actions(
        strict_items,
        root=str(result.summary.root),
        **execute_kwargs,
    )

def undo_execution(
    batch_id: int,
    *,
    db_path: Path | None = None,
) -> UndoResult:
    """Annule un batch et gère le cycle de vie de l'historique."""

    store = _open_history_store(db_path)

    try:
        success, errors = undo_batch(
            batch_id,
            store,
        )
    finally:
        store.close()

    return UndoResult(
        success=success,
        errors=tuple(errors),
    )


def list_history(
    *,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[Batch]:
    """Retourne les batches récents."""

    store = _open_history_store(db_path)

    try:
        return store.list_batches(limit=limit)
    finally:
        store.close()


def get_history_operations(
    batch_id: int,
    *,
    db_path: Path | None = None,
) -> list[Operation]:
    """Retourne les opérations appartenant à un batch."""

    store = _open_history_store(db_path)

    try:
        return store.get_operations(batch_id)
    finally:
        store.close()


def run_startup_crash_recovery(
    *,
    db_path: Path | None = None,
) -> CrashRecoveryReport:
    """Réconcilie l'historique persistant avant restauration de l'Undo."""

    store = _open_history_store(db_path)
    try:
        return run_crash_recovery(store)
    finally:
        store.close()


def resolve_history_recovery_without_file_action(
    batch_id: int,
    *,
    db_path: Path | None = None,
) -> int:
    # Clôture les recoveries non résolues sans modifier les fichiers.
    store = _open_history_store(db_path)
    try:
        batch = store.get_batch(batch_id)
        if batch is not None and not batch.has_actionable_metadata():
            raise ValueError(
                "clôture recovery refusée : métadonnées de batch incohérentes"
            )
        if batch is not None:
            consistency_issues = store.get_batch_consistency_issues(batch_id)
            if consistency_issues:
                raise ValueError(
                    "clôture recovery refusée : incohérence batch/opérations "
                    f"({', '.join(consistency_issues)})"
                )

        operations = store.get_operations(batch_id)
        blocked = [
            operation
            for operation in operations
            if operation.status.value == "planned"
            and _history_recovery_audit_issue(operation) is not None
        ]
        if blocked:
            raise ValueError(
                "clôture recovery refusée : audit recovery incohérent"
            )

        # 4E-21C: une identité persistée invalide ne peut pas être assimilée
        # à une identité historique absente lors d'une clôture manuelle.
        invalid_identity = [
            operation
            for operation in operations
            if operation.status.value == "planned"
            and _history_stored_identity_issue(operation) is not None
        ]
        if invalid_identity:
            raise ValueError(
                "clôture recovery refusée : identité stockée incohérente"
            )

        invalid_paths = [
            operation
            for operation in operations
            if operation.status.value == "planned"
            and (
                operation.original_path_raw is not None
                or operation.stored_path_raw is not None
            )
        ]
        if invalid_paths:
            raise ValueError(
                "clôture recovery refusée : chemins persistés incohérents"
            )

        invalid_scalars = [
            operation
            for operation in operations
            if operation.status.value == "planned"
            and (
                operation.size_raw is not None
                or operation.category_raw is not None
            )
        ]
        if invalid_scalars:
            raise ValueError(
                "clôture recovery refusée : métadonnées scalaires incohérentes"
            )

        return len(
            resolve_unresolved_planned_operations_without_file_action(
                store,
                batch_id,
            )
        )
    finally:
        store.close()


def get_latest_undoable_batch_id(
    *,
    db_path: Path | None = None,
) -> int | None:
    """Retourne la cible Undo LIFO persistée la plus récente."""

    store = _open_history_store(db_path)
    try:
        return store.latest_undoable_batch_id()
    finally:
        store.close()


def get_history_summary(
    *,
    limit: int = 20,
    db_path: Path | None = None,
) -> tuple[HistoryBatchSummary, ...]:
    """Retourne les métadonnées read-only des batches récents."""

    store = _open_history_store(db_path)
    try:
        return tuple(
            HistoryBatchSummary(
                id=batch.id,
                created_at=(
                    batch.created_at.strftime("%Y-%m-%d %H:%M")
                    if batch.created_at is not None
                    else "Horodatage invalide"
                ),
                root=batch.root,
                status=batch.status.value,
                undone=batch.undone,
                created_at_raw=batch.created_at_raw,
                status_raw=batch.status_raw,
                root_raw=batch.root_raw,
                undone_raw=batch.undone_raw,
                core_metadata_issues=_history_batch_core_metadata_issues(batch),
                planned_count=batch.planned_count,
                success_count=batch.success_count,
                failed_count=batch.failed_count,
                skipped_count=batch.skipped_count,
                planned_count_raw=batch.planned_count_raw,
                success_count_raw=batch.success_count_raw,
                failed_count_raw=batch.failed_count_raw,
                skipped_count_raw=batch.skipped_count_raw,
                consistency_issues=store.get_batch_consistency_issues(batch.id),
            )
            for batch in store.list_batches(limit=limit)
        )
    finally:
        store.close()


def _history_batch_core_metadata_issues(batch: Batch) -> tuple[str, ...]:
    """Classe les anomalies cœur persistées d'un batch sans les corriger."""

    issues: list[str] = []
    if batch.created_at_raw is not None:
        issues.append("invalid_created_at")
    if batch.status_raw is not None:
        issues.append("unknown_status")
    # 4E-25B: les valeurs brutes restent visibles et classées sans
    # supposer que tous les objets minimaux de test portent ces champs.
    if getattr(batch, "root_raw", None) is not None:
        issues.append("invalid_root")
    if getattr(batch, "undone_raw", None) is not None:
        issues.append("invalid_undone")
    if getattr(batch, "planned_count_raw", None) is not None:
        issues.append("invalid_planned_count")
    if getattr(batch, "success_count_raw", None) is not None:
        issues.append("invalid_success_count")
    if getattr(batch, "failed_count_raw", None) is not None:
        issues.append("invalid_failed_count")
    if getattr(batch, "skipped_count_raw", None) is not None:
        issues.append("invalid_skipped_count")

    counts = (
        getattr(batch, "planned_count", None),
        getattr(batch, "success_count", None),
        getattr(batch, "failed_count", None),
        getattr(batch, "skipped_count", None),
    )
    if (
        all(type(value) is int and value >= 0 for value in counts)
        and counts[1] + counts[2] + counts[3] > counts[0]
    ):
        issues.append("inconsistent_counts")
    return tuple(issues)


def _history_operation_core_metadata_issues(
    operation: Operation,
) -> tuple[str, ...]:
    """Classe les anomalies cœur persistées d'une opération sans les corriger."""

    issues: list[str] = []
    if operation.status_raw is not None:
        issues.append("unknown_status")
    # 4E-22B: la valeur brute reste visible, mais devient explicitement
    # incohérente et ne doit plus être assimilée à un type actionnable.
    if operation.kind not in {"copy", "move", "delete", "rmdir"}:
        issues.append("unknown_kind")
    # 4E-23B: une valeur brute signale un chemin non reconstructible.
    if getattr(operation, "original_path_raw", None) is not None:
        issues.append("invalid_original_path")
    if getattr(operation, "stored_path_raw", None) is not None:
        issues.append("invalid_stored_path")
    # 4E-24B: les scalaires bruts restent visibles et explicitement
    # classés sans casser les objets minimaux des tests gelés.
    if getattr(operation, "size_raw", None) is not None:
        issues.append("invalid_size")
    if getattr(operation, "category_raw", None) is not None:
        issues.append("invalid_category")
    return tuple(issues)


def _history_operation_recovery_state(
    operation: Operation,
) -> PlannedRecoveryState | None:
    """Retourne d'abord l'état structuré, avec fallback legacy sur error."""

    if operation.recovery_state is not None:
        return operation.recovery_state
    return recovery_state_from_persisted_error(operation.error)


def _history_stored_identity_issue(operation: Operation) -> str | None:
    """Classe une identité persistée présente mais non reconstructible."""

    if (
        operation.stored_identity is None
        and operation.stored_identity_raw is not None
    ):
        return "invalid_stored_identity"
    return None


def _history_original_identity_issue(operation: Operation) -> str | None:
    """Classe la preuve d'identité source nécessaire à une future reprise."""

    if operation.original_identity_raw is not None:
        return "invalid_original_identity"
    if operation.original_identity is None:
        return "missing_original_identity"
    if (
        operation.size is not None
        and operation.original_identity.size != operation.size
    ):
        return "original_identity_size_mismatch"
    return None


def _history_conflict_policy_issue(operation: Operation) -> str | None:
    """Classe la politique de collision persistée d'une action QUEUED."""

    if operation.conflict_policy_raw is not None:
        return "invalid_conflict_policy"
    if operation.conflict_policy is None:
        return "missing_conflict_policy"
    return None


def _history_queued_resume_blockers(
    operation: Operation,
    batch: Batch | None,
    batch_consistency_issues: tuple[str, ...],
) -> tuple[str, ...]:
    """Qualifie sans I/O les preuves requises pour une future reprise."""

    if operation.status.value != "queued":
        return ()

    blockers: list[str] = []
    if batch is None:
        blockers.append("missing_batch")
    else:
        if not batch.has_actionable_metadata():
            blockers.append("invalid_batch_metadata")
        if batch_consistency_issues:
            blockers.append("batch_operation_inconsistency")
        if batch.status not in {
            BatchStatus.CANCELLED,
            BatchStatus.PARTIAL,
            BatchStatus.FAILED,
        }:
            blockers.append("batch_status_not_resumable")

    blockers.extend(_history_operation_core_metadata_issues(operation))
    if operation.original_path is None:
        blockers.append("missing_original_path")
    if operation.size is None:
        blockers.append("missing_size")
    if operation.category is None:
        blockers.append("missing_category")
    elif operation.category not in {
        category.value for category in FileCategory
    }:
        blockers.append("unsupported_category")
    if operation.kind in {"copy", "move"} and operation.stored_path is None:
        blockers.append("missing_stored_path")

    original_identity_issue = _history_original_identity_issue(operation)
    if original_identity_issue is not None:
        blockers.append(original_identity_issue)
    if operation.kind in {"copy", "move"}:
        conflict_policy_issue = _history_conflict_policy_issue(operation)
        if conflict_policy_issue is not None:
            blockers.append(conflict_policy_issue)

    return tuple(dict.fromkeys(blockers))


def _live_validate_queued_resume_operation(
    operation: Operation,
    metadata_blockers: tuple[str, ...],
) -> HistoryQueuedResumeValidation:
    """Revalide un QUEUED sans écrire dans SQLite ni sur le filesystem."""

    common = {
        "operation_id": operation.id,
        "kind": operation.kind,
        "original_path": operation.original_path,
        "stored_path": operation.stored_path,
        "conflict_policy": operation.conflict_policy,
    }
    if metadata_blockers:
        return HistoryQueuedResumeValidation(
            **common,
            metadata_candidate=False,
            live_checked=False,
            live_ready=False,
            blockers=metadata_blockers,
        )

    if operation.original_path is None or operation.original_identity is None:
        return HistoryQueuedResumeValidation(
            **common,
            metadata_candidate=False,
            live_checked=False,
            live_ready=False,
            blockers=("missing_live_validation_input",),
        )

    try:
        if operation.kind == "rmdir":
            source = validate_source_empty_directory(
                operation.original_path,
                operation.original_identity,
            )
        else:
            source = validate_source_file(operation.original_path)
    except PathSafetyError as error:
        return HistoryQueuedResumeValidation(
            **common,
            metadata_candidate=True,
            live_checked=True,
            live_ready=False,
            blockers=("source_unavailable_or_unsafe",),
            diagnostic=str(error),
        )

    try:
        if operation.kind != "rmdir":
            validate_source_identity(source, operation.original_identity)
    except PathSafetyError as error:
        return HistoryQueuedResumeValidation(
            **common,
            metadata_candidate=True,
            live_checked=True,
            live_ready=False,
            blockers=("source_identity_changed",),
            diagnostic=str(error),
        )

    effective_destination: Path | None = None
    if operation.kind in {"copy", "move"}:
        assert operation.stored_path is not None
        assert operation.conflict_policy is not None
        policy = ConflictPolicy(operation.conflict_policy)
        try:
            effective_destination = resolve_destination_path(
                source,
                operation.stored_path,
                policy,
            )
        except FileExistsError as error:
            blocker = (
                "destination_conflict_skip"
                if policy is ConflictPolicy.SKIP
                else "destination_conflict_replace_disabled"
            )
            return HistoryQueuedResumeValidation(
                **common,
                metadata_candidate=True,
                live_checked=True,
                live_ready=False,
                blockers=(blocker,),
                diagnostic=str(error),
            )
        except PathSafetyError as error:
            return HistoryQueuedResumeValidation(
                **common,
                metadata_candidate=True,
                live_checked=True,
                live_ready=False,
                blockers=("destination_unsafe",),
                diagnostic=str(error),
            )
        except (OSError, ValueError) as error:
            return HistoryQueuedResumeValidation(
                **common,
                metadata_candidate=True,
                live_checked=True,
                live_ready=False,
                blockers=("destination_resolution_failed",),
                diagnostic=str(error),
            )

    return HistoryQueuedResumeValidation(
        **common,
        metadata_candidate=True,
        live_checked=True,
        live_ready=True,
        effective_destination=effective_destination,
    )


def _history_recovery_audit_issue(operation: Operation) -> str | None:
    """Décrit une incohérence persistée de l'audit recovery, sans la corriger."""

    has_resolution_kind = operation.resolution_kind is not None
    has_resolved_at = (
        operation.resolved_at is not None
        or operation.resolved_at_raw is not None
    )

    if has_resolution_kind and not has_resolved_at:
        return "resolution_kind_without_resolved_at"
    if has_resolved_at and not has_resolution_kind:
        return "resolved_at_without_resolution_kind"

    if operation.resolution_kind is RecoveryResolutionKind.UNKNOWN:
        return "unknown_resolution_kind"
    if operation.resolved_at_raw is not None:
        return "invalid_resolved_at"

    state = _history_operation_recovery_state(operation)
    if (
        operation.resolution_kind
        is RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION
        and state is not PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
    ):
        return "resolution_kind_state_mismatch"

    if (
        operation.resolution_kind
        is RecoveryResolutionKind.MANUAL_NO_FILE_ACTION
        and state
        not in {
            PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
            PlannedRecoveryState.AMBIGUOUS,
        }
    ):
        return "resolution_kind_state_mismatch"

    return None


def get_history_operation_summary(
    batch_id: int,
    *,
    db_path: Path | None = None,
) -> tuple[HistoryOperationSummary, ...]:
    """Retourne les opérations read-only d'un batch à la demande."""

    store = _open_history_store(db_path)
    try:
        batch = store.get_batch(batch_id)
        batch_consistency_issues = store.get_batch_consistency_issues(batch_id)
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    summaries: list[HistoryOperationSummary] = []
    for operation in operations:
        resume_blockers = _history_queued_resume_blockers(
            operation,
            batch,
            batch_consistency_issues,
        )
        summaries.append(HistoryOperationSummary(
            kind=operation.kind,
            original_path=operation.original_path,
            stored_path=operation.stored_path,
            original_path_raw=operation.original_path_raw,
            stored_path_raw=operation.stored_path_raw,
            size=operation.size,
            category=operation.category,
            size_raw=operation.size_raw,
            category_raw=operation.category_raw,
            status=operation.status.value,
            error=operation.error,
            status_raw=operation.status_raw,
            recovery_state=_history_operation_recovery_state(operation),
            recovery_state_raw=operation.recovery_state_raw,
            resolution_kind=operation.resolution_kind,
            resolution_kind_raw=operation.resolution_kind_raw,
            resolved_at=operation.resolved_at,
            resolved_at_raw=operation.resolved_at_raw,
            recovery_audit_issue=_history_recovery_audit_issue(operation),
            stored_identity_issue=_history_stored_identity_issue(operation),
            core_metadata_issues=_history_operation_core_metadata_issues(operation),
            conflict_policy=operation.conflict_policy,
            conflict_policy_raw=operation.conflict_policy_raw,
            original_identity_issue=(
                _history_original_identity_issue(operation)
                if operation.status.value == "queued"
                else None
            ),
            resume_candidate=(
                operation.status.value == "queued"
                and not resume_blockers
            ),
            resume_blockers=resume_blockers,
            recovery_attention_required=(
                operation.status.value == "planned"
                and _history_operation_recovery_state(operation)
                in {
                    PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
                    PlannedRecoveryState.AMBIGUOUS,
                    PlannedRecoveryState.UNKNOWN,
                }
            ),
        ))
    return tuple(summaries)


def validate_queued_resume_candidates(
    batch_id: int,
    *,
    db_path: Path | None = None,
) -> tuple[HistoryQueuedResumeValidation, ...]:
    """Revalide en lecture seule tous les QUEUED d'un batch.

    Les contrôles live sont volontairement ignorés pour une ligne dont les
    preuves persistées sont déjà insuffisantes. Un résultat ``live_ready``
    décrit uniquement l'instant du contrôle et doit être recalculé juste avant
    toute future mutation.
    """

    store = _open_history_store(db_path)
    try:
        batch = store.get_batch(batch_id)
        batch_consistency_issues = store.get_batch_consistency_issues(batch_id)
        operations = store.get_operations(batch_id)
    finally:
        store.close()

    validations: list[HistoryQueuedResumeValidation] = []
    for operation in operations:
        if operation.status.value != "queued":
            continue
        metadata_blockers = _history_queued_resume_blockers(
            operation,
            batch,
            batch_consistency_issues,
        )
        validations.append(
            _live_validate_queued_resume_operation(
                operation,
                metadata_blockers,
            )
        )
    return tuple(validations)


def _queued_operation_to_action_item(operation: Operation) -> ActionItem:
    """Reconstruit une action uniquement depuis ses preuves persistées."""

    if (
        operation.original_path is None
        or operation.size is None
        or operation.category is None
        or operation.original_identity is None
    ):
        raise ValueError("métadonnées QUEUED incomplètes")

    actions = {
        "copy": ActionKind.COPY,
        "move": ActionKind.MOVE,
        "delete": ActionKind.TRASH,
        "rmdir": ActionKind.RMDIR,
    }
    try:
        action = actions[operation.kind]
        category = FileCategory(operation.category)
    except (KeyError, ValueError) as exc:
        raise ValueError("métadonnées QUEUED non prises en charge") from exc

    conflict_policy = ConflictPolicy.RENAME
    if action in {ActionKind.COPY, ActionKind.MOVE}:
        if operation.stored_path is None or operation.conflict_policy is None:
            raise ValueError("destination QUEUED incomplète")
        conflict_policy = ConflictPolicy(operation.conflict_policy)

    return ActionItem(
        category=category,
        path=operation.original_path,
        size=operation.size,
        reason="reprise contrôlée d'une opération persistée",
        identity=operation.original_identity,
        destination=(
            operation.stored_path
            if action in {ActionKind.COPY, ActionKind.MOVE}
            else None
        ),
        action=action,
        conflict_policy=conflict_policy,
    )


def resume_queued_operations(
    batch_id: int,
    *,
    db_path: Path | None = None,
    progress_callback: Callable[[object], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    allow_unsafe_fast_move: bool = False,
) -> ExecuteResult:
    """Reprend en place les opérations QUEUED sûres d'un batch.

    La qualification complète est répétée avant de réserver atomiquement le
    batch. L'exécuteur revalide ensuite chaque source et destination juste
    avant son I/O, réutilise les lignes existantes et n'alloue ni batch ni
    opération supplémentaires.
    """

    store = _open_history_store(db_path)
    try:
        batch = store.get_batch(batch_id)
        consistency_issues = store.get_batch_consistency_issues(batch_id)
        operations = store.get_operations(batch_id)
        queued_operations = [
            operation
            for operation in operations
            if operation.status.value == "queued"
        ]

        if not queued_operations:
            return ExecuteResult(
                batch_id=batch_id,
                success=0,
                errors=("aucune opération QUEUED à reprendre",),
                skipped=0,
            )

        validations = tuple(
            _live_validate_queued_resume_operation(
                operation,
                _history_queued_resume_blockers(
                    operation,
                    batch,
                    consistency_issues,
                ),
            )
            for operation in queued_operations
        )
        blocked = [
            validation
            for validation in validations
            if not validation.live_ready
        ]
        if blocked:
            errors = tuple(
                f"opération #{validation.operation_id} : reprise bloquée "
                f"({', '.join(validation.blockers)})"
                for validation in blocked
            )
            return ExecuteResult(
                batch_id=batch_id,
                success=0,
                errors=errors,
                skipped=len(queued_operations),
            )

        try:
            items = [
                _queued_operation_to_action_item(operation)
                for operation in queued_operations
            ]
        except ValueError as exc:
            return ExecuteResult(
                batch_id=batch_id,
                success=0,
                errors=(str(exc),),
                skipped=len(queued_operations),
            )

        try:
            store.start_batch_resume(batch_id)
        except ValueError as exc:
            return ExecuteResult(
                batch_id=batch_id,
                success=0,
                errors=(str(exc),),
                skipped=len(queued_operations),
            )

        execute_kwargs = {
            "root": batch.root,
            "store": store,
            "existing_batch_id": batch_id,
            "existing_operation_ids": [
                operation.id for operation in queued_operations
            ],
        }
        if progress_callback is not None:
            execute_kwargs["progress_callback"] = progress_callback
        if cancel_callback is not None:
            execute_kwargs["cancel_callback"] = cancel_callback
        if allow_unsafe_fast_move:
            execute_kwargs["allow_unsafe_fast_move"] = True

        resumed_batch_id, success, errors = execute_items(
            items,
            **execute_kwargs,
        )
        final_batch = store.get_batch(resumed_batch_id)
        cancelled = bool(
            final_batch is not None
            and final_batch.status is BatchStatus.CANCELLED
        )
        skipped = final_batch.skipped_count if final_batch is not None else 0
        return ExecuteResult(
            batch_id=resumed_batch_id,
            success=success,
            errors=tuple(errors),
            cancelled=cancelled,
            skipped=skipped or 0,
        )
    finally:
        store.close()
