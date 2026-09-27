"""Construit un Plan (actions proposées) à partir d'un ScanResult.

Chaque règle est une fonction pure : ScanResult -> liste d'ActionItem.
Cela permet d'ajouter facilement de nouvelles règles sans toucher au reste
du pipeline (scan / preview / execute restent inchangés).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import re

from file_janitor.models import (
    ActionItem,
    ActionKind,
    ClassificationGuard,
    FileCategory,
    FileRecord,
    Plan,
    ScanResult,
)
from file_janitor.plan.grouping import (
    DateGranularity,
    GroupBy,
    describe_destination,
    destination_folder,
)
from file_janitor.plan.rules import Rule, first_matching_rule
from file_janitor.plan.templates import DEFAULT_TEMPLATE_PATTERN, Template
from file_janitor.scanner.filetype import EXTENSION_FAMILY, FAMILY_LABELS

ARCHIVE_EXTENSIONS = {
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".bz2",
    ".xz",
    ".tgz",
}

# Familles proposées au classement
# (voir file_janitor.scanner.filetype.FAMILY_LABELS).
SORTABLE_FAMILIES = set(FAMILY_LABELS)


@dataclass(frozen=True, slots=True)
class PlanConfig:
    """Seuils et options utilisés par les règles de détection."""

    large_file_threshold: int = 500 * 1024 * 1024  # 500 Mo
    old_file_threshold: timedelta = timedelta(days=365)
    old_archive_threshold: timedelta = timedelta(days=180)
    group_by: GroupBy = GroupBy.KIND
    date_granularity: DateGranularity = DateGranularity.DAY
    rules: list[Rule] = field(default_factory=list)
    template: Template | None = None
    template_only: bool = False
    template_min_group_size: int = 1
    infer_common_roots: bool = False

    # Dossier de base pour les destinations de classement (TO_SORT).
    # None = utiliser scan.root (comportement par défaut, chaque dossier
    # s'organise vers lui-même).
    #
    # Permet de rediriger le classement de plusieurs dossiers sources
    # vers une même destination commune, par exemple :
    #
    # Downloads + Documents -> Dropbox
    #
    # via :
    #
    # janitor sort DIR1 DIR2 --to ~/Dropbox
    destination_root: Path | None = None

    # Les interfaces produit peuvent demander que le mode de classement
    # s'applique à tous les fichiers trouvés récursivement. False conserve le
    # contrat historique du planificateur générique : fichiers de racine seuls.
    recursive_sort: bool = False
    secondary_group_by: GroupBy | None = None


def _rule_duplicates(scan: ScanResult) -> list[ActionItem]:
    """Groupe les fichiers par hash.

    Tous les fichiers sauf le plus ancien sont considérés comme doublons.
    """

    by_hash: defaultdict[str, list[FileRecord]] = defaultdict(list)

    for record in scan.files:
        # Les fichiers vides ont tous le même hash mais ne constituent pas des
        # doublons utiles à nettoyer (p. ex. __init__.py, py.typed, marqueurs).
        if record.size > 0 and record.hash:
            by_hash[record.hash].append(record)

    items: list[ActionItem] = []

    for file_hash, records in by_hash.items():
        if len(records) < 2:
            continue

        # On garde comme "original" le fichier le plus ancien
        # (mtime minimal).
        records_sorted = sorted(records, key=lambda r: r.mtime)
        original, *duplicates = records_sorted

        for dup in duplicates:
            items.append(
                ActionItem(
                    category=FileCategory.DUPLICATE,
                    path=dup.path,
                    size=dup.size,
                    reason=(
                        f"Doublon de {original.path.name} "
                        f"(hash {file_hash[:8]})"
                    ),
                    identity=dup.identity,
                    action=ActionKind.TRASH,
                )
            )

    return items


def _rule_old_archives(
    scan: ScanResult,
    config: PlanConfig,
) -> list[ActionItem]:
    """Détecte les archives plus anciennes que le seuil configuré."""

    cutoff = datetime.now() - config.old_archive_threshold
    items: list[ActionItem] = []

    for record in scan.files:
        is_archive = (
            record.extension in ARCHIVE_EXTENSIONS
            or record.content_family == "archive"
        )

        if is_archive and record.mtime < cutoff:
            days = (datetime.now() - record.mtime).days

            items.append(
                ActionItem(
                    category=FileCategory.OLD_ARCHIVE,
                    path=record.path,
                    size=record.size,
                    reason=f"Archive vieille de {days} jours",
                    identity=record.identity,
                    action=ActionKind.TRASH,
                )
            )

    return items


def _rule_large_files(
    scan: ScanResult,
    config: PlanConfig,
) -> list[ActionItem]:
    """Détecte les fichiers dépassant le seuil de taille configuré."""

    items: list[ActionItem] = []

    for record in scan.files:
        if record.size >= config.large_file_threshold:
            mb = record.size / (1024 * 1024)

            items.append(
                ActionItem(
                    category=FileCategory.LARGE_FILE,
                    path=record.path,
                    size=record.size,
                    reason=f"Fichier de {mb:.0f} Mo",
                    identity=record.identity,
                    action=ActionKind.NONE,
                )
            )

    return items


def _rule_old_files(
    scan: ScanResult,
    config: PlanConfig,
) -> list[ActionItem]:
    """Détecte les fichiers plus anciens que le seuil configuré."""

    cutoff = datetime.now() - config.old_file_threshold
    items: list[ActionItem] = []

    for record in scan.files:
        if record.mtime < cutoff:
            days = (datetime.now() - record.mtime).days

            items.append(
                ActionItem(
                    category=FileCategory.OLD_FILE,
                    path=record.path,
                    size=record.size,
                    reason=f"Non modifié depuis {days} jours",
                    identity=record.identity,
                    action=ActionKind.NONE,
                )
            )

    return items


def _rule_to_sort(
    scan: ScanResult,
    config: PlanConfig,
) -> list[ActionItem]:
    """Construit les actions de classement.

    Le planificateur générique conserve son comportement historique
    (fichiers de racine uniquement). Les modes produit activent
    ``recursive_sort`` et classent alors tous les fichiers du scan.

    Trois étapes successives sont appliquées :

    1. Règles personnalisées.
    2. Template de regroupement par nom.
    3. Stratégie de classement standard.

    Chaque étape ne traite que les fichiers non couverts par la précédente.
    """

    if not scan.metadata_complete and (
        config.group_by in {GroupBy.DATE, GroupBy.SIZE}
        or config.secondary_group_by in {GroupBy.DATE, GroupBy.SIZE}
    ):
        # Ces stratégies exigent respectivement mtime et size. Ne jamais
        # fabriquer de classement à partir de métadonnées sentinelles.
        return []

    candidates = (
        list(scan.files)
        if config.recursive_sort
        else [
            record
            for record in scan.files
            if record.path.parent == scan.root
        ]
    )

    dest_root = config.destination_root or scan.root

    items: list[ActionItem] = []
    remaining = candidates

    if config.rules:
        rule_items, remaining = _apply_rules(
            remaining,
            config.rules,
            dest_root,
        )
        items.extend(rule_items)

    if config.secondary_group_by is not None:
        items.extend(_combined_sort_items(remaining, dest_root, config))
        return items

    if config.template is not None:
        template_items, remaining = _apply_template(
            remaining,
            config.template,
            dest_root,
            min_group_size=config.template_min_group_size,
        )
        items.extend(template_items)

    if config.infer_common_roots and remaining:
        common_items, remaining = _apply_inferred_common_roots(
            remaining,
            dest_root,
            min_group_size=config.template_min_group_size,
        )
        items.extend(common_items)

    if config.template_only:
        return items

    if config.group_by is GroupBy.COMMON_NAME:
        groups = _common_name_groups(remaining, dest_root, config)
        for record in remaining:
            group = groups.get(record.path)
            if group is None:
                continue
            destination = dest_root / group / record.path.name
            if destination != record.path:
                items.append(ActionItem(
                    category=FileCategory.TO_SORT,
                    path=record.path,
                    size=record.size,
                    reason=f"Racine commune « {group} », à classer",
                    identity=record.identity,
                    destination=destination,
                    action=ActionKind.MOVE,
                ))
        return items

    if config.group_by == GroupBy.KIND:
        items.extend(
            _kind_sort_items(
                remaining,
                dest_root,
            )
        )
    else:
        items.extend(
            _strategy_sort_items(
                remaining,
                dest_root,
                config,
            )
        )

    return items


def _apply_rules(
    records: list[FileRecord],
    rules: list[Rule],
    root: Path,
) -> tuple[list[ActionItem], list[FileRecord]]:
    """Applique les règles personnalisées.

    Retourne :
        (actions générées, fichiers non couverts)
    """

    items: list[ActionItem] = []
    remaining: list[FileRecord] = []

    for record in records:
        rule = first_matching_rule(record, rules)

        if rule is None:
            remaining.append(record)
            continue

        items.append(
            ActionItem(
                category=FileCategory.TO_SORT,
                path=record.path,
                size=record.size,
                reason=f"Règle « {rule.name} », à classer",
                identity=record.identity,
                destination=(
                    root
                    / rule.destination
                    / record.path.name
                ),
                action=ActionKind.MOVE,
            )
        )

    return items, remaining


def _apply_template(
    records: list[FileRecord],
    template: Template,
    root: Path,
    *,
    min_group_size: int = 1,
    include_already_sorted: bool = False,
) -> tuple[list[ActionItem], list[FileRecord]]:
    """Applique le template de regroupement par nom.

    En mode produit « racine commune », ``min_group_size=2`` évite de classer
    un fichier isolé qui ne partage en réalité sa racine avec aucun autre.
    """

    grouped: dict[str, list[FileRecord]] = {}
    unmatched: list[FileRecord] = []

    for record in records:
        group = template.extract(record.path.stem)
        if group is None:
            unmatched.append(record)
            continue
        grouped.setdefault(group, []).append(record)

    items: list[ActionItem] = []
    remaining = list(unmatched)

    for group, members in grouped.items():
        if len(members) < min_group_size:
            remaining.extend(members)
            continue

        for record in members:
            destination = root / group / record.path.name
            if destination == record.path and not include_already_sorted:
                continue

            items.append(
                ActionItem(
                    category=FileCategory.TO_SORT,
                    path=record.path,
                    size=record.size,
                    reason=(
                        f"Regroupé avec « {group} » "
                        f"(racine commune), à classer"
                    ),
                    identity=record.identity,
                    destination=destination,
                    action=ActionKind.MOVE,
                )
            )

    return items, remaining


_COMMON_ROOT_SEPARATOR_RE = re.compile(r"[-_.\\s]+")


def _common_root_candidates(stem: str) -> list[tuple[tuple[str, ...], str]]:
    """Retourne les racines candidates situées sur une frontière de nom.

    On évite volontairement les préfixes de caractères arbitraires :
    ``alpha`` et ``alphabet`` ne doivent pas devenir un groupe artificiel.
    Les séparateurs ``-``, ``_``, ``.`` et espace délimitent les segments.
    """

    candidates: list[tuple[tuple[str, ...], str]] = []
    seen: set[tuple[str, ...]] = set()

    for match in _COMMON_ROOT_SEPARATOR_RE.finditer(stem):
        prefix = stem[: match.start()].strip(" -_.")
        suffix = stem[match.end() :].strip(" -_.")
        if not prefix or not suffix:
            continue

        tokens = tuple(
            token.casefold()
            for token in _COMMON_ROOT_SEPARATOR_RE.split(prefix)
            if token
        )
        if not tokens or tokens in seen:
            continue

        compact = "".join(tokens)
        # Une racine doit porter un minimum de signal sémantique et ne pas
        # être purement numérique.
        if len(compact) < 3 or not any(char.isalpha() for char in compact):
            continue

        seen.add(tokens)
        candidates.append((tokens, prefix))

    return candidates


def _apply_inferred_common_roots(
    records: list[FileRecord],
    root: Path,
    *,
    min_group_size: int = 2,
    include_already_sorted: bool = False,
) -> tuple[list[ActionItem], list[FileRecord]]:
    """Infère des racines communes significatives entre plusieurs noms.

    Les candidats les plus spécifiques sont traités en premier. Un fichier
    n'appartient qu'à un seul groupe accepté, ce qui évite de le proposer dans
    plusieurs dossiers concurrents. Les fichiers sans groupe d'au moins
    ``min_group_size`` membres restent non classés.
    """

    ordered = sorted(records, key=lambda record: str(record.path).casefold())
    members_by_key: dict[tuple[str, ...], list[FileRecord]] = {}
    display_by_key: dict[tuple[str, ...], str] = {}

    for record in ordered:
        for key, display in _common_root_candidates(record.path.stem):
            members_by_key.setdefault(key, []).append(record)
            display_by_key.setdefault(key, display)

    shared_keys = [
        key
        for key, members in members_by_key.items()
        if len(members) >= min_group_size
    ]
    shared_keys.sort(
        key=lambda key: (
            -len(key),
            -sum(len(token) for token in key),
            "\\x1f".join(key),
        )
    )

    assigned_paths: set[Path] = set()
    items: list[ActionItem] = []

    for key in shared_keys:
        available = [
            record
            for record in members_by_key[key]
            if record.path not in assigned_paths
        ]
        if len(available) < min_group_size:
            continue

        group = display_by_key[key].strip(" -_.")
        if not group:
            continue

        for record in available:
            assigned_paths.add(record.path)
            destination = root / group / record.path.name
            if destination == record.path and not include_already_sorted:
                continue
            items.append(
                ActionItem(
                    category=FileCategory.TO_SORT,
                    path=record.path,
                    size=record.size,
                    reason=(
                        f"Regroupé avec « {group} » "
                        f"(racine commune détectée), à classer"
                    ),
                    identity=record.identity,
                    destination=destination,
                    action=ActionKind.MOVE,
                )
            )

    remaining = [
        record for record in records if record.path not in assigned_paths
    ]
    return items, remaining


def _kind_sort_items(
    records: list[FileRecord],
    root: Path,
) -> list[ActionItem]:
    """Classe les fichiers selon leur famille réelle ou leur extension."""

    items: list[ActionItem] = []

    for record in records:
        family = record.content_family

        via_content = (
            family is not None
            and family != "unknown"
        )

        if not via_content:
            # Contenu non calculé, vide ou non reconnu :
            # repli sur l'extension.
            family = EXTENSION_FAMILY.get(record.extension)

        if not family or family not in SORTABLE_FAMILIES:
            continue

        if via_content:
            reason = f"{record.content_label}, à classer"
        else:
            reason = (
                f"Extension {record.extension} reconnue, "
                f"à classer"
            )

        items.append(
            ActionItem(
                category=FileCategory.TO_SORT,
                path=record.path,
                size=record.size,
                reason=reason,
                identity=record.identity,
                destination=(
                    root
                    / FAMILY_LABELS[family]
                    / record.path.name
                ),
                action=ActionKind.MOVE,
            )
        )

    return items


def _strategy_sort_items(
    records: list[FileRecord],
    root: Path,
    config: PlanConfig,
) -> list[ActionItem]:
    """Classe selon extension, alphabet, taille ou date."""

    items: list[ActionItem] = []

    for record in records:
        folder = destination_folder(
            record,
            config.group_by,
            config.date_granularity,
        )

        destination = root / folder / record.path.name
        if destination == record.path:
            # Le fichier est déjà dans le dossier produit par la stratégie.
            continue

        classification_guard = None
        if config.group_by in {GroupBy.SIZE, GroupBy.DATE}:
            classification_guard = ClassificationGuard(
                group_by=config.group_by.value,
                folder=folder,
                date_granularity=(
                    config.date_granularity.value
                    if config.group_by is GroupBy.DATE
                    else None
                ),
            )

        items.append(
            ActionItem(
                category=FileCategory.TO_SORT,
                path=record.path,
                size=record.size,
                reason=describe_destination(record, config.group_by, folder),
                identity=record.identity,
                destination=destination,
                classification_guard=classification_guard,
                action=ActionKind.MOVE,
            )
        )

    return items


def _common_name_groups(
    records: list[FileRecord], root: Path, config: PlanConfig,
) -> dict[Path, str]:
    """Retrouve les groupes de noms existants sans classer les fichiers isolés."""

    items, remaining = _apply_template(
        records,
        config.template or Template.compile(DEFAULT_TEMPLATE_PATTERN),
        root,
        min_group_size=2,
        include_already_sorted=True,
    )
    inferred, _ = _apply_inferred_common_roots(
        remaining, root, min_group_size=2, include_already_sorted=True,
    )
    return {item.path: item.destination.parent.name for item in (*items, *inferred)}


def _combined_sort_items(
    records: list[FileRecord], root: Path, config: PlanConfig,
) -> list[ActionItem]:
    """Construit une destination finale à deux niveaux en une seule action."""

    first, second = config.group_by, config.secondary_group_by
    supported = {GroupBy.EXTENSION, GroupBy.DATE, GroupBy.SIZE, GroupBy.COMMON_NAME}
    if first not in supported or second not in supported or first is second:
        raise ValueError("La combinaison exige deux critères distincts et pris en charge")
    if not records:
        return []

    first_names = (
        _common_name_groups(records, root, config)
        if first is GroupBy.COMMON_NAME else {}
    )
    second_names: dict[Path, str] = {}
    if second is GroupBy.COMMON_NAME:
        partitions: dict[str, list[FileRecord]] = defaultdict(list)
        for record in records:
            partitions[destination_folder(record, first, config.date_granularity)].append(record)
        for group_records in partitions.values():
            second_names.update(_common_name_groups(group_records, root, config))

    items: list[ActionItem] = []
    for record in records:
        first_folder = (
            first_names.get(record.path) if first is GroupBy.COMMON_NAME
            else destination_folder(record, first, config.date_granularity)
        )
        second_folder = (
            second_names.get(record.path) if second is GroupBy.COMMON_NAME
            else destination_folder(record, second, config.date_granularity)
        )
        if first_folder is None or second_folder is None:
            continue
        destination = root / first_folder / second_folder / record.path.name
        if destination == record.path:
            continue

        guarded = (GroupBy.DATE, GroupBy.SIZE)
        primary_guard = first if first in guarded else second if second in guarded else None
        secondary_guard = second if first in guarded and second in guarded else None
        guard = None
        if primary_guard is not None:
            guard = ClassificationGuard(
                group_by=primary_guard.value,
                folder=first_folder if primary_guard is first else second_folder,
                date_granularity=(config.date_granularity.value if primary_guard is GroupBy.DATE else None),
                secondary_group_by=secondary_guard.value if secondary_guard else None,
                secondary_folder=second_folder if secondary_guard else None,
            )
        def label(criterion: GroupBy, folder: str) -> str:
            if criterion is GroupBy.COMMON_NAME:
                return f"Racine commune « {folder} »"
            return describe_destination(record, criterion, folder).removesuffix(", à classer")

        items.append(ActionItem(
            category=FileCategory.TO_SORT,
            path=record.path,
            size=record.size,
            reason=f"{label(first, first_folder)} · {label(second, second_folder)}, à classer",
            identity=record.identity,
            destination=destination,
            classification_guard=guard,
            action=ActionKind.MOVE,
        ))
    return items


def _rule_mismatched_extension(
    scan: ScanResult,
) -> list[ActionItem]:
    """Détecte une extension incompatible avec le contenu réel.

    Exemple :
        un fichier .jpg contenant en réalité une archive ZIP.

    Cette catégorie est uniquement informative :
    aucune opération automatique n'est planifiée.
    """

    items: list[ActionItem] = []

    for record in scan.files:
        expected_family = EXTENSION_FAMILY.get(record.extension)

        if expected_family is None:
            # Extension non répertoriée : rien à comparer.
            continue

        actual_family = record.content_family

        if not actual_family or actual_family == "unknown":
            # Détection non concluante :
            # on évite les faux positifs.
            continue

        if actual_family == expected_family:
            continue

        items.append(
            ActionItem(
                category=FileCategory.EXTENSION_MISMATCH,
                path=record.path,
                size=record.size,
                reason=(
                    f"Extension {record.extension} "
                    f"({FAMILY_LABELS[expected_family]}) "
                    f"mais contenu détecté : "
                    f"{record.content_label}"
                ),
                identity=record.identity,
                action=ActionKind.NONE,
            )
        )

    return items



_UNSAFE_CONTEXT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_collision_context(value: str) -> str:
    """Nettoie un morceau de chemin pour l'injecter dans un nom de fichier."""

    value = _UNSAFE_CONTEXT_CHARS.sub("_", value)
    value = " ".join(value.split()).strip(" .")
    if not value:
        return "dossier"
    # Garder les noms de destination lisibles et limiter le risque de dépasser
    # les limites de nom imposées par certains backends distants.
    return value[:48].rstrip(" .") or "dossier"


def _collision_context_labels(
    members: list[ActionItem],
    *,
    root: Path,
) -> dict[int, str]:
    """Construit le plus petit contexte de dossier qui distingue les sources.

    Exemples :
      A/report.pdf, B/report.pdf -> A, B
      A/docs/report.pdf, B/docs/report.pdf -> A - docs, B - docs

    Aucun accès filesystem n'est effectué : seuls les chemins déjà scannés
    sont manipulés.
    """

    parent_parts: dict[int, tuple[str, ...]] = {}
    for item in members:
        try:
            relative_parent = item.path.parent.relative_to(root)
            parts = relative_parent.parts
        except ValueError:
            parts = item.path.parent.parts
        parent_parts[id(item)] = tuple(parts)

    max_depth = max((len(parts) for parts in parent_parts.values()), default=0)
    for depth in range(1, max_depth + 1):
        labels: dict[int, str] = {}
        seen: set[str] = set()
        unique = True
        for item in members:
            parts = parent_parts[id(item)]
            if not parts:
                label = "racine"
            else:
                suffix = parts[-min(depth, len(parts)) :]
                label = " - ".join(_safe_collision_context(part) for part in suffix)
            folded = label.casefold()
            if folded in seen:
                unique = False
                break
            seen.add(folded)
            labels[id(item)] = label
        if unique:
            return labels

    # Des chemins identiques ne devraient pas représenter deux actions
    # distinctes, mais ce fallback garde le plan déterministe si cela arrive.
    return {
        id(item): _safe_collision_context(item.path.parent.name or "racine")
        for item in members
    }


def _contextual_collision_candidate(
    destination: Path,
    context: str,
    *,
    reserved: set[Path],
) -> Path:
    """Propose un nom contextualisé, puis suffixe seulement si nécessaire."""

    base = destination.parent / (
        f"{destination.stem} — {_safe_collision_context(context)}{destination.suffix}"
    )
    if base not in reserved:
        return base

    counter = 2
    candidate = base
    while candidate in reserved:
        candidate = base.parent / f"{base.stem} ({counter}){base.suffix}"
        counter += 1
    return candidate


def _resolve_batch_destination_collisions(
    items: list[ActionItem],
    *,
    root: Path,
) -> None:
    """Réserve des destinations uniques pour les collisions internes au plan.

    Cette étape est purement en mémoire : elle n'appelle jamais ``exists()``
    et n'ajoute donc aucun accès coûteux sur un montage distant. Les
    collisions avec des fichiers apparus ou déjà présents sur le filesystem
    restent volontairement arbitrées par l'executor no-clobber.
    """

    groups: dict[Path, list[ActionItem]] = {}
    for item in items:
        if item.action not in {ActionKind.MOVE, ActionKind.COPY}:
            continue
        if item.destination is None:
            continue
        groups.setdefault(item.destination, []).append(item)

    # Tous les noms initialement prévus sont réservés. Ainsi, si le plan
    # contient déjà ``rapport (1).pdf``, un doublon de ``rapport.pdf``
    # recevra ``rapport (2).pdf`` plutôt que de lui voler sa destination.
    reserved: set[Path] = set(groups)

    for destination in sorted(groups, key=lambda path: str(path).casefold()):
        members = groups[destination]
        if len(members) < 2:
            continue

        ordered = sorted(
            members,
            key=lambda item: (str(item.path).casefold(), str(item.path)),
        )
        keeper = ordered[0]
        keeper.conflict = True
        keeper.conflict_reason = (
            "Collision interne au lot : nom conservé ; "
            "les autres fichiers homonymes seront renommés."
        )

        context_labels = _collision_context_labels(ordered, root=root)
        for item in ordered[1:]:
            context = context_labels[id(item)]
            candidate = _contextual_collision_candidate(
                destination,
                context,
                reserved=reserved,
            )

            item.destination = candidate
            item.conflict = True
            item.conflict_reason = (
                "Collision interne au lot : destination contextualisée avec "
                f"le dossier source « {context} » en « {candidate.name} »."
            )
            reserved.add(candidate)

def build_plan(
    scan: ScanResult,
    config: PlanConfig | None = None,
) -> Plan:
    """Applique toutes les règles de détection et retourne le Plan complet."""

    config = config or PlanConfig()
    plan = Plan(scan=scan)

    for item in _rule_duplicates(scan):
        plan.add(item)

    if scan.metadata_complete:
        for item in _rule_old_archives(scan, config):
            plan.add(item)

        for item in _rule_large_files(scan, config):
            plan.add(item)

        for item in _rule_old_files(scan, config):
            plan.add(item)

    sort_items = _rule_to_sort(scan, config)
    _resolve_batch_destination_collisions(sort_items, root=scan.root)
    for item in sort_items:
        plan.add(item)

    for item in _rule_mismatched_extension(scan):
        plan.add(item)

    return plan
