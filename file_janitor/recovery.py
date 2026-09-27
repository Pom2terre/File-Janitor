"""Crash recovery helpers for File Janitor."""

from datetime import datetime, timezone
from pathlib import Path

from file_janitor.path_safety import (
    PathSafetyError,
    validate_source_file,
    validate_source_identity,
)
from file_janitor.storage.history import (
    PlannedRecoveryState,
    BatchStatus,
    HistoryStore,
    OperationStatus,
    RecoveryResolutionKind,
)


def reconcile_published_planned_operations(store: HistoryStore) -> list[int]:
    """Promote only PLANNED operations whose published object is proven intact."""
    recovered: list[int] = []

    for batch in store.list_batches():
        if (
            batch.status is not BatchStatus.RUNNING
            or not batch.has_actionable_metadata()
            or store.get_batch_consistency_issues(batch.id)
        ):
            continue

        for operation in store.get_operations(batch.id):
            if operation.status is not OperationStatus.PLANNED:
                continue
            if (
                operation.original_path is None
                or operation.original_path_raw is not None
                or operation.stored_path is None
                or operation.stored_path_raw is not None
                or operation.size_raw is not None
                or operation.category_raw is not None
                or operation.stored_identity is None
            ):
                continue

            published_path = Path(operation.stored_path)
            try:
                validate_source_file(published_path)
                validate_source_identity(published_path, operation.stored_identity)
            except (OSError, PathSafetyError):
                continue

            store.set_operation_status(operation.id, OperationStatus.COMPLETED)
            recovered.append(operation.id)

    store.reconcile_running_batches()
    return recovered

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlannedRecoveryInspection:
    """État observé d'une opération PLANNED non prouvable par stored_identity."""

    operation_id: int
    batch_id: int
    kind: str
    original_path: Path | None
    stored_path: Path | None
    original_exists: bool
    stored_exists: bool
    state: PlannedRecoveryState


def inspect_unidentified_planned_operations(
    store: HistoryStore,
) -> list[PlannedRecoveryInspection]:
    """Classe les PLANNED sans stored_identity sans modifier journal ni disque."""

    inspections: list[PlannedRecoveryInspection] = []

    for batch in store.list_batches():
        if (
            batch.status is not BatchStatus.RUNNING
            or not batch.has_actionable_metadata()
            or store.get_batch_consistency_issues(batch.id)
        ):
            continue

        for operation in store.get_operations(batch.id):
            if operation.status is not OperationStatus.PLANNED:
                continue
            invalid_path_metadata = (
                operation.original_path is None
                or operation.original_path_raw is not None
                or operation.stored_path_raw is not None
            )
            invalid_scalar_metadata = (
                operation.size_raw is not None
                or operation.category_raw is not None
            )
            if (
                operation.stored_identity is not None
                and not invalid_path_metadata
                and not invalid_scalar_metadata
            ):
                continue

            # 4E-21C: des composantes persistées mais non reconstructibles
            # constituent une preuve corrompue, pas une absence de preuve.
            invalid_stored_identity = operation.stored_identity_raw is not None
            original_exists = (
                operation.original_path is not None
                and operation.original_path.exists()
            )
            stored_exists = (
                operation.stored_path is not None
                and operation.stored_path.exists()
            )

            if (
                invalid_path_metadata
                or invalid_scalar_metadata
                or invalid_stored_identity
            ):
                state = PlannedRecoveryState.AMBIGUOUS
            elif operation.stored_path is None:
                state = PlannedRecoveryState.AMBIGUOUS
            elif operation.kind == "copy":
                if original_exists and not stored_exists:
                    state = PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
                elif original_exists and stored_exists:
                    state = PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
                else:
                    state = PlannedRecoveryState.AMBIGUOUS
            elif operation.kind in {"move", "delete"}:
                if original_exists and not stored_exists:
                    state = PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED
                elif not original_exists and stored_exists:
                    state = PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
                else:
                    state = PlannedRecoveryState.AMBIGUOUS
            else:
                state = PlannedRecoveryState.AMBIGUOUS

            inspections.append(
                PlannedRecoveryInspection(
                    operation_id=operation.id,
                    batch_id=batch.id,
                    kind=operation.kind,
                    original_path=operation.original_path,
                    stored_path=operation.stored_path,
                    original_exists=original_exists,
                    stored_exists=bool(stored_exists),
                    state=state,
                )
            )

    return inspections

_RECOVERY_NO_PUBLISHED_ERROR = (
    "récupération après crash : aucun objet publié observé ; "
    "opération non rejouée automatiquement"
)


def resolve_no_published_planned_operations(
    store: HistoryStore,
) -> list[int]:
    """Termine fail-closed les PLANNED sans publication observée."""

    resolved_operation_ids: list[int] = []

    for inspection in inspect_unidentified_planned_operations(store):
        if inspection.state is not PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED:
            continue

        store.resolve_operation_recovery(
            inspection.operation_id,
            error=_RECOVERY_NO_PUBLISHED_ERROR,
            recovery_state=inspection.state,
            resolution_kind=RecoveryResolutionKind.AUTOMATIC_NO_PUBLICATION,
            resolved_at=datetime.now(timezone.utc),
        )
        resolved_operation_ids.append(inspection.operation_id)

    store.reconcile_running_batches()
    return resolved_operation_ids

_RECOVERY_UNVERIFIED_ERROR = (
    "récupération après crash : objet publié observé mais identité non vérifiable ; "
    "opération laissée PLANNED, aucune action automatique"
)

_RECOVERY_AMBIGUOUS_ERROR = (
    "récupération après crash : état filesystem ambigu ; "
    "opération laissée PLANNED, aucune action automatique"
)


def recovery_state_from_persisted_error(
    error: str | None,
) -> PlannedRecoveryState | None:
    """Traduit un diagnostic de recovery persisté vers son état sémantique.

    Cette fonction n'inspecte pas le filesystem. Elle permet aux couches
    supérieures d'exposer un contrat stable sans parser elles-mêmes les
    messages utilisateur persistés dans le journal.
    """

    return {
        _RECOVERY_NO_PUBLISHED_ERROR: PlannedRecoveryState.NO_PUBLISHED_OBJECT_OBSERVED,
        _RECOVERY_UNVERIFIED_ERROR: PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED,
        _RECOVERY_AMBIGUOUS_ERROR: PlannedRecoveryState.AMBIGUOUS,
        _MANUAL_RECOVERY_UNVERIFIED_ERROR: (
            PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED
        ),
        _MANUAL_RECOVERY_AMBIGUOUS_ERROR: PlannedRecoveryState.AMBIGUOUS,
    }.get(error)


def annotate_unresolved_planned_operations(
    store: HistoryStore,
) -> list[int]:
    """Persiste un diagnostic pour les PLANNED encore non résolus.

    Les opérations PUBLISHED_OBJECT_UNVERIFIED et AMBIGUOUS restent PLANNED.
    Seul leur champ error est renseigné afin de rendre l'incertitude durable
    dans le journal. Aucune mutation filesystem n'est effectuée.
    """

    annotated_operation_ids: list[int] = []

    for inspection in inspect_unidentified_planned_operations(store):
        if inspection.state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED:
            error = _RECOVERY_UNVERIFIED_ERROR
        elif inspection.state is PlannedRecoveryState.AMBIGUOUS:
            error = _RECOVERY_AMBIGUOUS_ERROR
        else:
            continue

        store.annotate_operation_recovery(
            inspection.operation_id,
            error=error,
            recovery_state=inspection.state,
        )
        annotated_operation_ids.append(inspection.operation_id)

    return annotated_operation_ids


_MANUAL_RECOVERY_UNVERIFIED_ERROR = (
    "résolution manuelle après crash : objet publié non vérifiable accepté "
    "sans action filesystem"
)

_MANUAL_RECOVERY_AMBIGUOUS_ERROR = (
    "résolution manuelle après crash : état filesystem ambigu accepté "
    "sans action filesystem"
)


def resolve_unresolved_planned_operations_without_file_action(
    store: HistoryStore,
    batch_id: int,
) -> list[int]:
    # Clôture les recoveries non résolues sans toucher au filesystem.
    batch = store.get_batch(batch_id)
    if batch is None:
        raise ValueError(f"batch d'historique introuvable : {batch_id}")
    consistency_issues = store.get_batch_consistency_issues(batch_id)
    if consistency_issues:
        raise ValueError(
            "clôture recovery refusée : incohérence batch/opérations "
            f"({', '.join(consistency_issues)})"
        )

    resolved_operation_ids: list[int] = []

    for operation in store.get_operations(batch_id):
        if operation.status is not OperationStatus.PLANNED:
            continue

        state = (
            operation.recovery_state
            if operation.recovery_state is not None
            else recovery_state_from_persisted_error(operation.error)
        )
        if state is PlannedRecoveryState.PUBLISHED_OBJECT_UNVERIFIED:
            error = _MANUAL_RECOVERY_UNVERIFIED_ERROR
        elif state is PlannedRecoveryState.AMBIGUOUS:
            error = _MANUAL_RECOVERY_AMBIGUOUS_ERROR
        else:
            continue

        store.resolve_operation_recovery(
            operation.id,
            error=error,
            recovery_state=state,
            resolution_kind=RecoveryResolutionKind.MANUAL_NO_FILE_ACTION,
            resolved_at=datetime.now(timezone.utc),
        )
        resolved_operation_ids.append(operation.id)

    store.reconcile_running_batches()
    return resolved_operation_ids


@dataclass(frozen=True, slots=True)
class CrashRecoveryReport:
    """Résumé déterministe d'une passe complète de recovery."""

    promoted_operation_ids: tuple[int, ...]
    failed_operation_ids: tuple[int, ...]
    annotated_operation_ids: tuple[int, ...]

    @property
    def changed_operation_ids(self) -> tuple[int, ...]:
        return (
            self.promoted_operation_ids
            + self.failed_operation_ids
            + self.annotated_operation_ids
        )


def run_crash_recovery(store: HistoryStore) -> CrashRecoveryReport:
    """Exécute les passes de recovery dans un ordre fail-closed."""

    promoted = tuple(reconcile_published_planned_operations(store))
    failed = tuple(resolve_no_published_planned_operations(store))
    annotated = tuple(annotate_unresolved_planned_operations(store))

    store.reconcile_running_batches()

    return CrashRecoveryReport(
        promoted_operation_ids=promoted,
        failed_operation_ids=failed,
        annotated_operation_ids=annotated,
    )
