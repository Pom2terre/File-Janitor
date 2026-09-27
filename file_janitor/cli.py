"""Interface en ligne de commande du File Janitor.

Pipeline : scan -> plan -> preview -> execute -> undo.
Rien n'est jamais supprimé ou déplacé sans confirmation explicite
(ou sans --yes), et toute exécution passe par une corbeille locale
permettant l'undo (voir file_janitor.storage.history).
"""

from __future__ import annotations

import logging
import sys
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from file_janitor.application import (
    ArchiveFormat,
    DEFAULT_MAX_ITEMS_PER_CATEGORY,
    DEFAULT_TEMPLATE_PATTERN,
    RenameTemplateError,
    DateGranularity,
    GroupBy,
    PlanConfig,
    RuleError,
    ScheduleError,
    Template,
    TemplateError,
    analyze_rename_folder,
    analyze_archive_folder,
    analyze_empty_directories,
    as_copy_actions,
    archive_actions,
    empty_directory_actions,
    build_report,
    clean_actions,
    create_plan,
    duplicate_actions,
    execute_actions,
    execute_selected_actions,
    get_history_operation_summary,
    get_history_summary,
    install_schedule,
    large_file_actions,
    list_schedules,
    mismatch_actions,
    load_rules,
    preview_schedule,
    remove_schedule,
    rename_actions,
    resume_queued_operations,
    scan_folder,
    sort_actions,
    summarize_analysis,
    undo_execution,
    validate_queued_resume_candidates,
    write_report_html,
)
from file_janitor.archiving import ArchivePlanError
from file_janitor.formatting import human_count, human_size

app = typer.Typer(
    help="Analyse un dossier et propose des actions de nettoyage, sans jamais rien modifier sans confirmation.",
    no_args_is_help=True,
)
schedule_app = typer.Typer(help="Planifie l'exécution automatique de sort/clean via cron (auto-organizing).", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")
console = Console()


class ReportFormat(str, Enum):
    json = "json"
    html = "html"


# Options d'exclusion partagées par toutes les commandes qui scannent un
# dossier (voir file_janitor.scanner.ignore pour la logique de combinaison
# defaults + .janitorignore + --exclude).
EXCLUDE_OPTION = typer.Option(
    None,
    "--exclude",
    "-e",
    help="Motif à exclure, syntaxe gitignore (répétable, ex: -e '*.tmp' -e 'cache/').",
)
NO_DEFAULT_EXCLUDES_OPTION = typer.Option(
    False,
    "--no-default-excludes",
    help="Désactive les exclusions par défaut (état interne du janitor).",
)
NO_IGNORE_FILE_OPTION = typer.Option(
    False,
    "--no-ignore-file",
    help="Ignore le fichier .janitorignore présent dans le dossier scanné.",
)
NO_CONTENT_CHECK_OPTION = typer.Option(
    False,
    "--no-content-check",
    help="Désactive la détection du contenu réel (magic bytes) ; classement basé sur l'extension seule.",
)
HASH_WORKERS_OPTION = typer.Option(
    None,
    "--hash-workers",
    help="Threads pour le hachage parallèle des doublons (défaut : auto selon les cœurs CPU ; 1 = séquentiel).",
)
GROUP_BY_OPTION = typer.Option(
    GroupBy.KIND,
    "--group-by",
    help="Stratégie de classement : kind (par contenu réel), extension, alphabet, size ou date.",
)
DATE_GRANULARITY_OPTION = typer.Option(
    DateGranularity.DAY,
    "--date-granularity",
    help="Granularité du classement --group-by date : day, month ou year.",
)
RULES_OPTION = typer.Option(
    None,
    "--rules",
    help="Fichier JSON de règles personnalisées (conditions -> destination), prioritaires sur --group-by.",
)
TEMPLATE_OPTION = typer.Option(
    False,
    "--template",
    help="Regroupe les fichiers par motif commun dans le nom (ex: hello-1.jpg, hello-2.jpg -> dossier hello/).",
)
TEMPLATE_PATTERN_OPTION = typer.Option(
    None,
    "--template-pattern",
    help=f"Regex personnalisée pour --template (1 groupe de capture requis). Défaut : {DEFAULT_TEMPLATE_PATTERN!r}.",
)


def _load_rules_or_exit(rules_path: Path | None) -> list:
    """Charge le fichier de règles, ou affiche une erreur claire et quitte (code 1)."""
    if rules_path is None:
        return []
    try:
        return load_rules(rules_path)
    except RuleError as exc:
        console.print(f"[red]Erreur dans le fichier de règles :[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _load_template_or_exit(enabled: bool, custom_pattern: str | None) -> Template | None:
    """Compile le template (motif par défaut ou personnalisé), ou affiche une
    erreur claire et quitte (code 1). Retourne None si --template n'est pas demandé."""
    if not enabled and custom_pattern is None:
        return None
    pattern = custom_pattern or DEFAULT_TEMPLATE_PATTERN
    try:
        return Template.compile(pattern)
    except TemplateError as exc:
        console.print(f"[red]Erreur de template :[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _scan(
    folder: Path,
    *,
    compute_hashes: bool,
    compute_content_type: bool = True,
    hash_workers: int | None = None,
    exclude: list[str] | None,
    no_default_excludes: bool,
    no_ignore_file: bool,
):
    return scan_folder(
        folder,
        compute_hashes=compute_hashes,
        compute_content_type=compute_content_type,
        hash_workers=hash_workers,
        exclude_patterns=exclude,
        use_default_excludes=not no_default_excludes,
        use_ignore_file=not no_ignore_file,
    )


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Active les logs détaillés.")) -> None:
    _configure_logging(verbose)


@app.command()
def analyze(
    folder: Path = typer.Argument(..., help="Dossier à analyser."),
    no_hash: bool = typer.Option(False, "--no-hash", help="Désactive la détection de doublons (plus rapide)."),
    rules: Path = RULES_OPTION,
    template: bool = TEMPLATE_OPTION,
    template_pattern: str = TEMPLATE_PATTERN_OPTION,
    group_by: GroupBy = GROUP_BY_OPTION,
    date_granularity: DateGranularity = DATE_GRANULARITY_OPTION,
    no_content_check: bool = NO_CONTENT_CHECK_OPTION,
    hash_workers: int = HASH_WORKERS_OPTION,
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Analyse un dossier et affiche un résumé (aucune modification effectuée)."""
    loaded_rules = _load_rules_or_exit(rules)
    loaded_template = _load_template_or_exit(template, template_pattern)
    scan = _scan(
        folder,
        compute_hashes=not no_hash,
        compute_content_type=not no_content_check,
        hash_workers=hash_workers,
        exclude=exclude,
        no_default_excludes=no_default_excludes,
        no_ignore_file=no_ignore_file,
    )

    for error in scan.errors:
        console.print(f"[red]Erreur :[/red] {error}")
    if not scan.files and scan.errors:
        raise typer.Exit(code=1)

    plan = create_plan(
        scan,
        PlanConfig(group_by=group_by, date_granularity=date_granularity, rules=loaded_rules, template=loaded_template),
    )

    summary = summarize_analysis(scan, plan)

    console.print(f"\n[bold]Analyse de {summary.root}[/bold]\n")
    console.print(
        f"{human_count(summary.total_count)} fichiers — "
        f"{human_size(summary.total_size)}\n"
    )

    table = Table(show_header=False, box=None)
    table.add_column("label")
    table.add_column("value", justify="right")
    for category in summary.categories:
        value = (
            human_count(category.item_count)
            if category.display_metric == "count"
            else human_size(category.total_size)
        )
        table.add_row(category.label, value)
    console.print(table)

    console.print("\n[bold]Actions proposées :[/bold]")
    console.print("[1] Afficher les doublons        janitor duplicates FOLDER")
    console.print("[2] Classer les fichiers          janitor sort FOLDER")
    console.print("[3] Examiner les gros fichiers     janitor large FOLDER")
    console.print("[4] Nettoyer                       janitor clean FOLDER")
    console.print("[5] Générer un rapport             janitor report FOLDER")
    console.print("[6] Extensions trompeuses          janitor mismatches FOLDER")

    if not sys.stdin.isatty():
        return  # entrée non interactive (script, pipe, tests) : pas de prompt

    while True:
        choice = typer.prompt("\nVotre choix (1-6, Entrée pour quitter)", default="", show_default=False).strip()
        if not choice:
            return
        if choice == "1":
            _print_items_table(duplicate_actions(plan), "Doublons")
        elif choice == "2":
            _interactive_sort(scan, plan)
        elif choice == "3":
            _print_items_table(large_file_actions(plan), "Fichiers > 500 Mo")
        elif choice == "4":
            _interactive_clean(scan, plan)
        elif choice == "5":
            _interactive_report(scan, plan)
        elif choice == "6":
            _print_items_table(mismatch_actions(plan), "Extensions trompeuses")
        else:
            console.print("[red]Choix invalide, entrez un chiffre entre 1 et 6.[/red]")


def _interactive_sort(scan, plan) -> None:
    items = sort_actions(plan)
    _print_items_table(items, "Fichiers à classer")
    if not items:
        return
    if not typer.confirm(f"\nClasser ces {len(items)} fichiers maintenant ?"):
        return
    copy = typer.confirm("Copier au lieu de déplacer (fichiers d'origine conservés) ?", default=False)
    execution_items = as_copy_actions(items) if copy else items
    result = execute_actions(execution_items, root=str(scan.root))
    batch_id, success, errors = result.batch_id, result.success, result.errors
    verb = "copié(s)" if copy else "classé(s)"
    console.print(f"[green]{success} fichier(s) {verb}.[/green] (batch #{batch_id})")
    for err in errors:
        console.print(f"[red]{err}[/red]")


def _interactive_clean(scan, plan) -> None:
    items = clean_actions(plan)
    _print_items_table(items, "À nettoyer (doublons + archives anciennes)")
    if not items:
        return
    if not typer.confirm(f"\nSupprimer (vers la corbeille) ces {len(items)} fichiers ?"):
        return
    result = execute_actions(items, root=str(scan.root))
    batch_id, success, errors = result.batch_id, result.success, result.errors
    console.print(f"[green]{success} fichier(s) déplacé(s) vers la corbeille.[/green] (batch #{batch_id})")
    console.print(f"Pour annuler : janitor undo {batch_id}")
    for err in errors:
        console.print(f"[red]{err}[/red]")


def _interactive_report(scan, plan) -> None:
    import json

    console.print(json.dumps(build_report(scan, plan), indent=2, ensure_ascii=False))


def _print_items_table(items, title: str) -> None:
    if not items:
        console.print(f"[green]Aucun élément dans « {title} ».[/green]")
        return
    table = Table(title=title)
    table.add_column("Chemin", overflow="fold")
    table.add_column("Taille", justify="right")
    table.add_column("Raison")
    for item in sorted(items, key=lambda i: i.size, reverse=True):
        table.add_row(str(item.path), human_size(item.size), item.reason)
    console.print(table)
    console.print(f"\nTotal : {human_count(len(items))} fichiers — {human_size(sum(i.size for i in items))}")


@app.command()
def duplicates(
    folder: Path = typer.Argument(...),
    hash_workers: int = HASH_WORKERS_OPTION,
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Affiche les fichiers en double détectés dans le dossier."""
    scan = _scan(
        folder,
        compute_hashes=True,
        compute_content_type=False,
        hash_workers=hash_workers,
        exclude=exclude,
        no_default_excludes=no_default_excludes,
        no_ignore_file=no_ignore_file,
    )
    plan = create_plan(scan)
    _print_items_table(duplicate_actions(plan), "Doublons")


@app.command()
def large(
    folder: Path = typer.Argument(...),
    threshold_mb: int = typer.Option(500, help="Seuil en Mo pour considérer un fichier comme volumineux."),
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Liste les fichiers dont la taille dépasse le seuil donné."""
    scan = _scan(
        folder,
        compute_hashes=False,
        compute_content_type=False,
        exclude=exclude,
        no_default_excludes=no_default_excludes,
        no_ignore_file=no_ignore_file,
    )
    config = PlanConfig(large_file_threshold=threshold_mb * 1024 * 1024)
    plan = create_plan(scan, config)
    _print_items_table(large_file_actions(plan), f"Fichiers > {threshold_mb} Mo")


@app.command()
def mismatches(
    folder: Path = typer.Argument(...),
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Affiche les fichiers dont l'extension ne correspond pas au contenu réel détecté."""
    scan = _scan(
        folder,
        compute_hashes=False,
        compute_content_type=True,
        exclude=exclude,
        no_default_excludes=no_default_excludes,
        no_ignore_file=no_ignore_file,
    )
    plan = create_plan(scan)
    _print_items_table(mismatch_actions(plan), "Extensions trompeuses")


def _print_rename_preview(result) -> None:
    details = result.details_for("rename")
    if details is None or not details.items:
        console.print("[green]Aucun fichier dans le périmètre de renommage.[/green]")
        return

    table = Table(title="Prévisualisation des renommages")
    table.add_column("État")
    table.add_column("Nom actuel", overflow="fold")
    table.add_column("Nom proposé", overflow="fold")
    table.add_column("Détail", overflow="fold")
    for item in details.items:
        if item.conflict:
            status = "[red]Collision[/red]"
        elif item.action == "none":
            status = "Inchangé"
        else:
            status = "[green]Renommer[/green]"
        table.add_row(
            status,
            str(item.path.relative_to(result.summary.root)),
            str(item.destination.relative_to(result.summary.root))
            if item.destination is not None else "—",
            item.conflict_reason or item.reason,
        )
    console.print(table)
    actionable = len(rename_actions(result))
    console.print(
        f"\n{human_count(actionable)} renommage(s) proposé(s) ; "
        f"{human_count(len(details.items) - actionable)} élément(s) inchangé(s) "
        "ou en collision."
    )


@app.command()
def rename(
    folder: Path = typer.Argument(..., help="Dossier dont les fichiers seront renommés."),
    pattern: str = typer.Option(
        ...,
        "--pattern",
        "-p",
        help=(
            "Modèle de nom : {name}, {ext}, {parent}, {date}, {counter}; "
            "ex. '{date}_{counter:03d}_{name}{ext}'."
        ),
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help="Inclut aussi les sous-dossiers ; le compteur repart à 1 dans chaque dossier.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Prévisualise sans agir (défaut) ou applique les renommages sans collision.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Renomme des fichiers selon un modèle, avec preview et annulation."""

    try:
        result = analyze_rename_folder(
            folder,
            pattern,
            recursive=recursive,
            exclude_patterns=exclude,
            use_default_excludes=not no_default_excludes,
            use_ignore_file=not no_ignore_file,
        )
    except RenameTemplateError as exc:
        console.print(f"[red]Modèle de renommage invalide :[/red] {exc}")
        raise typer.Exit(code=2) from exc

    for error in result.scan_errors:
        console.print(f"[red]Erreur de scan :[/red] {error}")
    _print_rename_preview(result)
    actions = rename_actions(result)
    if not actions or dry_run:
        if actions and dry_run:
            console.print(
                "\n[yellow]Mode --dry-run : aucune modification effectuée. "
                "Utilisez --apply pour renommer.[/yellow]"
            )
        return

    if not yes and not typer.confirm(
        f"\nRenommer ces {len(actions)} fichier(s) ?"
    ):
        raise typer.Abort()

    execution = execute_actions(actions, root=str(result.summary.root))
    console.print(
        f"[green]{execution.success} fichier(s) renommé(s).[/green] "
        f"(batch #{execution.batch_id})"
    )
    if execution.batch_id is not None:
        console.print(f"Pour annuler : janitor undo {execution.batch_id}")
    for error in execution.errors:
        console.print(f"[red]{error}[/red]")


def _print_archive_preview(result) -> None:
    details = result.details_for("archive")
    if details is None or not details.items:
        console.print("[green]Aucun fichier ne dépasse le seuil d’ancienneté.[/green]")
        return

    is_zip = any(item.archive_format == ArchiveFormat.ZIP.value for item in details.items)
    table = Table(
        title=(
            "Prévisualisation de l’archivage ZIP"
            if is_zip
            else "Prévisualisation de l’archivage selon l’ancienneté"
        )
    )
    table.add_column("État")
    table.add_column("Fichier", overflow="fold")
    table.add_column("Taille")
    table.add_column("Destination", overflow="fold")
    table.add_column("Motif", overflow="fold")
    for item in details.items:
        status = "[red]Bloqué[/red]" if item.conflict else "[green]Archiver[/green]"
        table.add_row(
            status,
            str(item.path.relative_to(result.summary.root)),
            human_size(item.size),
            str(item.destination),
            item.conflict_reason or item.reason,
        )
    console.print(table)
    actionable = len(archive_actions(result))
    console.print(
        f"\n{human_count(actionable)} fichier(s) archivable(s) ; "
        f"{human_count(len(details.items) - actionable)} cible(s) bloquée(s)."
    )


@app.command()
def archive(
    folder: Path = typer.Argument(..., help="Dossier source à analyser."),
    destination: Path = typer.Option(
        ..., "--to", help="Dossier d’archive, distinct du dossier source."
    ),
    older_than_days: int = typer.Option(
        ..., "--older-than-days", min=1,
        help="Archive les fichiers modifiés depuis au moins ce nombre de jours.",
    ),
    archive_format: ArchiveFormat = typer.Option(
        ArchiveFormat.FOLDERS,
        "--format",
        help="folders : conserve l’arborescence en dossiers ; zip : crée une archive ZIP.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Prévisualise sans agir (défaut) ou déplace les fichiers sélectionnés.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Archive les fichiers anciens en dossiers ou dans un ZIP."""

    try:
        result = analyze_archive_folder(
            folder,
            destination,
            older_than_days,
            archive_format=archive_format,
            exclude_patterns=exclude,
            use_default_excludes=not no_default_excludes,
            use_ignore_file=not no_ignore_file,
        )
    except ArchivePlanError as exc:
        console.print(f"[red]Paramètres d’archivage invalides :[/red] {exc}")
        raise typer.Exit(code=2) from exc

    for error in result.scan_errors:
        console.print(f"[red]Erreur de scan :[/red] {error}")
    _print_archive_preview(result)
    actions = archive_actions(result)
    if not actions or dry_run:
        if actions and dry_run:
            console.print(
                "\n[yellow]Mode --dry-run : aucun déplacement effectué. "
                "Utilisez --apply pour archiver.[/yellow]"
            )
        return

    if not yes and not typer.confirm(
        f"\nArchiver ces {len(actions)} fichier(s) vers « {destination} » ?"
    ):
        raise typer.Abort()

    details = result.details_for("archive")
    selected = {
        ("archive", index)
        for index, item in enumerate(details.items if details is not None else ())
        if item.action == "move"
    }
    execution = execute_selected_actions(result, selected)
    archived_count = (
        max(0, execution.success - 1)
        if archive_format is ArchiveFormat.ZIP
        else execution.success
    )
    console.print(
        f"[green]{archived_count} fichier(s) archivé(s).[/green] "
        f"(batch #{execution.batch_id})"
    )
    if execution.batch_id is not None:
        console.print(f"Pour annuler : janitor undo {execution.batch_id}")
    for error in execution.errors:
        console.print(f"[red]{error}[/red]")


def _print_empty_directories_preview(result) -> None:
    details = result.details_for("empty_directory")
    if details is None or not details.items:
        console.print("[green]Aucun dossier vide trouvé.[/green]")
        return

    table = Table(title="Prévisualisation des dossiers vides")
    table.add_column("Action")
    table.add_column("Dossier", overflow="fold")
    table.add_column("Motif", overflow="fold")
    for item in details.items:
        table.add_row(
            "Supprimer",
            str(item.path.relative_to(result.summary.root)),
            item.reason,
        )
    console.print(table)
    console.print(
        f"\n{human_count(len(empty_directory_actions(result)))} dossier(s) vide(s)."
    )


@app.command("empty-dirs")
def empty_dirs(
    folder: Path = typer.Argument(..., help="Dossier source à parcourir."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="Prévisualise sans agir (défaut) ou supprime les dossiers sélectionnés.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Supprime les dossiers vides (sans jamais toucher à leur contenu)."""

    try:
        result = analyze_empty_directories(
            folder,
            exclude_patterns=exclude,
            use_default_excludes=not no_default_excludes,
            use_ignore_file=not no_ignore_file,
        )
    except (OSError, ValueError) as exc:
        console.print(f"[red]Impossible d’analyser les dossiers :[/red] {exc}")
        raise typer.Exit(code=2) from exc

    for error in result.scan_errors:
        console.print(f"[red]Erreur de scan :[/red] {error}")
    _print_empty_directories_preview(result)
    actions = empty_directory_actions(result)
    if not actions or dry_run:
        if actions and dry_run:
            console.print(
                "\n[yellow]Mode --dry-run : aucun dossier supprimé. "
                "Utilisez --apply pour continuer.[/yellow]"
            )
        return

    if not yes and not typer.confirm(
        f"\nSupprimer ces {len(actions)} dossier(s) vides ?"
    ):
        raise typer.Abort()

    execution = execute_actions(actions, root=str(result.summary.root))
    console.print(
        f"[green]{execution.success} dossier(s) vide(s) supprimé(s).[/green] "
        f"(batch #{execution.batch_id})"
    )
    for error in execution.errors:
        console.print(f"[red]{error}[/red]")
    if execution.batch_id is not None:
        console.print(f"Pour annuler : janitor undo {execution.batch_id}")


@app.command()
def sort(
    folders: list[Path] = typer.Argument(..., help="Un ou plusieurs dossiers à classer."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Prévisualise sans agir (défaut) ou applique réellement."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
    copy: bool = typer.Option(False, "--copy", help="Copie les fichiers au lieu de les déplacer (l'original reste en place)."),
    to: Path = typer.Option(
        None, "--to", help="Destination commune pour tous les dossiers listés (sinon chaque dossier s'organise vers lui-même)."
    ),
    rules: Path = RULES_OPTION,
    template: bool = TEMPLATE_OPTION,
    template_pattern: str = TEMPLATE_PATTERN_OPTION,
    group_by: GroupBy = GROUP_BY_OPTION,
    date_granularity: DateGranularity = DATE_GRANULARITY_OPTION,
    no_content_check: bool = NO_CONTENT_CHECK_OPTION,
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Propose de classer les fichiers non triés dans des sous-dossiers, selon
    --rules (règles personnalisées prioritaires), puis --template (motif
    commun dans le nom), puis --group-by : kind (contenu réel, défaut),
    extension, alphabet, size ou date.

    Accepte plusieurs dossiers sources en une seule commande ; avec --to,
    tous sont classés vers une même destination commune (ex: Downloads et
    Documents réunis vers Dropbox). Sans --to, chaque dossier s'organise
    vers lui-même, indépendamment des autres.
    """
    loaded_rules = _load_rules_or_exit(rules)
    loaded_template = _load_template_or_exit(template, template_pattern)
    # Le contenu réel est utile pour la stratégie 'kind', ou si une règle
    # personnalisée filtre sur content_family : sinon on évite de lire
    # l'en-tête de chaque fichier (I/O inutile pour les stratégies mécaniques).
    needs_content = group_by == GroupBy.KIND or any(r.content_family is not None for r in loaded_rules)
    compute_content_type = needs_content and not no_content_check
    destination_root = to.expanduser().resolve() if to else None

    all_items = []
    scanned_roots = []
    for folder in folders:
        scan = _scan(
            folder,
            compute_hashes=False,
            compute_content_type=compute_content_type,
            exclude=exclude,
            no_default_excludes=no_default_excludes,
            no_ignore_file=no_ignore_file,
        )
        for error in scan.errors:
            console.print(f"[red]Erreur :[/red] {error}")
        config = PlanConfig(
            group_by=group_by,
            date_granularity=date_granularity,
            rules=loaded_rules,
            template=loaded_template,
            destination_root=destination_root,
        )
        plan = create_plan(scan, config)
        all_items.extend(sort_actions(plan))
        scanned_roots.append(str(scan.root))

    _print_items_table(all_items, "Fichiers à classer")

    if not all_items or dry_run:
        if all_items and dry_run:
            action = "copier" if copy else "classer"
            console.print(f"\n[yellow]Mode --dry-run : aucune modification effectuée. Utilisez --apply pour {action} réellement.[/yellow]")
        return

    if not yes and not typer.confirm(f"\n{'Copier' if copy else 'Classer'} ces {len(all_items)} fichiers ?"):
        raise typer.Abort()

    execution_items = as_copy_actions(all_items) if copy else all_items
    result = execute_actions(execution_items, root=", ".join(scanned_roots))
    batch_id, success, errors = result.batch_id, result.success, result.errors

    verb = "copié(s)" if copy else "classé(s)"
    console.print(f"[green]{success} fichier(s) {verb}.[/green] (batch #{batch_id})")
    for err in errors:
        console.print(f"[red]{err}[/red]")


@app.command()
def clean(
    folders: list[Path] = typer.Argument(..., help="Un ou plusieurs dossiers à nettoyer."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Prévisualise sans agir (défaut) ou applique réellement."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
    no_content_check: bool = NO_CONTENT_CHECK_OPTION,
    hash_workers: int = HASH_WORKERS_OPTION,
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Supprime (vers la corbeille locale) les doublons et archives anciennes.

    Accepte plusieurs dossiers en une seule commande. Chaque dossier est
    analysé indépendamment : les doublons ne sont détectés qu'à l'intérieur
    d'un même dossier, pas entre deux dossiers différents.
    """
    all_items = []
    scanned_roots = []
    for folder in folders:
        scan = _scan(
            folder,
            compute_hashes=True,
            compute_content_type=not no_content_check,
            hash_workers=hash_workers,
            exclude=exclude,
            no_default_excludes=no_default_excludes,
            no_ignore_file=no_ignore_file,
        )
        for error in scan.errors:
            console.print(f"[red]Erreur :[/red] {error}")
        plan = create_plan(scan)
        all_items.extend(clean_actions(plan))
        scanned_roots.append(str(scan.root))

    _print_items_table(all_items, "À nettoyer (doublons + archives anciennes)")

    if not all_items:
        return
    if dry_run:
        console.print("\n[yellow]Mode --dry-run : aucune modification effectuée. Utilisez --apply pour nettoyer réellement.[/yellow]")
        return

    if not yes and not typer.confirm(f"\nSupprimer (vers la corbeille) ces {len(all_items)} fichiers ?"):
        raise typer.Abort()

    result = execute_actions(all_items, root=", ".join(scanned_roots))
    batch_id, success, errors = result.batch_id, result.success, result.errors

    console.print(f"[green]{success} fichier(s) déplacé(s) vers la corbeille.[/green] (batch #{batch_id})")
    console.print(f"Pour annuler : janitor undo {batch_id}")
    for err in errors:
        console.print(f"[red]{err}[/red]")



@app.command()
def report(
    folder: Path = typer.Argument(...),
    output: Path = typer.Option(
        None, "--output", "-o", help="Fichier de sortie (JSON affiché sur stdout si omis ; HTML écrit dans un fichier)."
    ),
    format: ReportFormat = typer.Option(
        None, "--format", help="Format du rapport : déduit de l'extension de --output si omis (JSON par défaut)."
    ),
    max_items: int = typer.Option(
        DEFAULT_MAX_ITEMS_PER_CATEGORY, "--max-items", help="Nombre maximum de fichiers listés par catégorie (rapport HTML)."
    ),
    open_browser: bool = typer.Option(False, "--open", help="Ouvre le rapport HTML dans le navigateur après génération."),
    no_content_check: bool = NO_CONTENT_CHECK_OPTION,
    hash_workers: int = HASH_WORKERS_OPTION,
    exclude: list[str] = EXCLUDE_OPTION,
    no_default_excludes: bool = NO_DEFAULT_EXCLUDES_OPTION,
    no_ignore_file: bool = NO_IGNORE_FILE_OPTION,
) -> None:
    """Génère un rapport de l'analyse, en JSON (défaut) ou en HTML (aucune modification)."""
    import json
    import webbrowser

    scan = _scan(
        folder,
        compute_hashes=True,
        compute_content_type=not no_content_check,
        hash_workers=hash_workers,
        exclude=exclude,
        no_default_excludes=no_default_excludes,
        no_ignore_file=no_ignore_file,
    )
    plan = create_plan(scan)

    resolved_format = format
    if resolved_format is None:
        resolved_format = ReportFormat.html if output and output.suffix.lower() == ".html" else ReportFormat.json
    if resolved_format == ReportFormat.html and output is None:
        output = Path(f"{scan.root.name or 'rapport'}_janitor_report.html")

    if resolved_format == ReportFormat.html:
        write_report_html(scan, plan, output, max_items_per_category=max_items)
        console.print(f"[green]Rapport HTML écrit dans {output}[/green]")
        if open_browser:
            webbrowser.open(output.resolve().as_uri())
    else:
        text = json.dumps(build_report(scan, plan), indent=2, ensure_ascii=False)
        if output:
            output.write_text(text, encoding="utf-8")
            console.print(f"[green]Rapport JSON écrit dans {output}[/green]")
        else:
            # La sortie JSON est destinée aux scripts : Rich insère des sauts
            # de ligne dans les chemins longs en fonction du terminal.
            typer.echo(text)


@app.command()
def undo(batch_id: int = typer.Argument(..., help="Identifiant du batch à annuler (voir 'janitor history').")) -> None:
    """Annule les actions d'un batch précédent (restaure les fichiers)."""
    result = undo_execution(batch_id)

    console.print(f"[green]{result.success} fichier(s) restauré(s).[/green]")
    for err in result.errors:
        console.print(f"[red]{err}[/red]")


@app.command()
def resume(
    batch_id: int = typer.Argument(
        ...,
        help="Identifiant du batch interrompu à reprendre.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Exécute la reprise après une nouvelle revalidation complète.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Ne pas demander de confirmation avec --apply.",
    ),
) -> None:
    """Prévisualise ou reprend les opérations non démarrées d'un batch.

    Sans ``--apply``, cette commande reste strictement en lecture seule. Le
    mode sûr est toujours utilisé pour les déplacements distants.
    """

    validations = validate_queued_resume_candidates(batch_id)
    if not validations:
        console.print(
            f"[red]Aucune opération non démarrée pour le batch #{batch_id}.[/red]"
        )
        raise typer.Exit(code=1)

    table = Table(title=f"Reprise du batch #{batch_id}")
    table.add_column("Opération", justify="right")
    table.add_column("Action")
    table.add_column("Source", overflow="fold")
    table.add_column("Destination", overflow="fold")
    table.add_column("État")

    ready_count = 0
    for validation in validations:
        if validation.live_ready:
            ready_count += 1
            state = "[green]Prête[/green]"
        else:
            blockers = ", ".join(validation.blockers) or "contrôle live refusé"
            state = f"[red]Bloquée : {blockers}[/red]"
        destination = (
            validation.effective_destination
            if validation.effective_destination is not None
            else validation.stored_path
        )
        table.add_row(
            str(validation.operation_id),
            validation.kind,
            str(validation.original_path or "—"),
            str(destination or "—"),
            state,
        )

    blocked_count = len(validations) - ready_count
    console.print(table)
    console.print(
        f"{human_count(ready_count)} prête"
        f"{'s' if ready_count != 1 else ''} · "
        f"{human_count(blocked_count)} bloquée"
        f"{'s' if blocked_count != 1 else ''}."
    )
    for validation in validations:
        if not validation.live_ready:
            blockers = ", ".join(validation.blockers) or "contrôle live refusé"
            console.print(
                f"[red]Opération #{validation.operation_id} bloquée : "
                f"{blockers}.[/red]"
            )

    if not apply:
        console.print(
            "[yellow]Aperçu uniquement : aucune modification effectuée. "
            "Utilisez --apply pour reprendre ce batch.[/yellow]"
        )
        return

    if blocked_count:
        console.print(
            "[red]Reprise refusée : toutes les opérations non démarrées "
            "doivent être prêtes.[/red]"
        )
        raise typer.Exit(code=1)

    if not yes and not typer.confirm(
        f"Reprendre {len(validations)} opération(s) en mode sûr ?"
    ):
        console.print(
            "[yellow]Reprise annulée : aucune modification effectuée.[/yellow]"
        )
        raise typer.Exit(code=1)

    # L'aperçu n'est jamais une autorisation durable : le cas d'usage répète
    # la totalité des contrôles juste avant de réserver puis d'exécuter le lot.
    result = resume_queued_operations(batch_id)
    console.print(
        f"[green]{result.success} opération(s) reprise(s).[/green] "
        f"(batch #{result.batch_id})"
    )
    if result.skipped:
        console.print(
            f"[yellow]{result.skipped} opération(s) non démarrée(s).[/yellow]"
        )
    for error in result.errors:
        console.print(f"[red]{error}[/red]")
    if result.errors or result.cancelled:
        raise typer.Exit(code=1)


@app.command()
def history(limit: int = typer.Option(20, help="Nombre de batches à afficher.")) -> None:
    """Liste les batches et signale les reprises QUEUED découvrables."""
    batches = get_history_summary(limit=limit)

    if not batches:
        console.print("Aucun historique.")
        return

    table = Table()
    table.add_column("Batch")
    table.add_column("Date")
    table.add_column("Dossier")
    table.add_column("Reprise")
    table.add_column("Annulé")
    has_queued_operations = False
    resumable_batch_ids: list[int] = []
    for batch in batches:
        operations = get_history_operation_summary(batch.id)
        queued_operations = [
            operation for operation in operations if operation.status == "queued"
        ]
        candidate_count = sum(
            1 for operation in queued_operations if operation.resume_candidate
        )
        blocked_count = len(queued_operations) - candidate_count
        if queued_operations:
            has_queued_operations = True
        if candidate_count and not blocked_count:
            resumable_batch_ids.append(batch.id)
            resume_status = (
                f"{human_count(candidate_count)} candidate"
                f"{'s' if candidate_count != 1 else ''}"
            )
        elif queued_operations:
            parts: list[str] = []
            if candidate_count:
                parts.append(
                    f"{human_count(candidate_count)} candidate"
                    f"{'s' if candidate_count != 1 else ''}"
                )
            if blocked_count:
                parts.append(
                    f"{human_count(blocked_count)} bloquée"
                    f"{'s' if blocked_count != 1 else ''}"
                )
            resume_status = " · ".join(parts)
        else:
            resume_status = "—"
        table.add_row(
            str(batch.id),
            batch.created_at,
            batch.root or "Dossier invalide",
            resume_status,
            (
                "oui"
                if batch.undone is True
                else "non" if batch.undone is False else "invalide"
            ),
        )
    console.print(table)
    for batch_id in resumable_batch_ids:
        console.print(f"[green]Batch #{batch_id} : janitor resume {batch_id}[/green]")
    if has_queued_operations:
        console.print(
            "[dim]Qualification métadonnée uniquement. Utilisez "
            "'janitor resume <batch>' pour la revalidation live.[/dim]"
        )


@schedule_app.command("add")
def schedule_add(
    folder: Path = typer.Argument(..., help="Dossier à organiser automatiquement."),
    every: int = typer.Option(..., "--every", help="Intervalle (ex: --every 2 --unit hours = toutes les 2 heures)."),
    unit: str = typer.Option("hours", "--unit", help="Unité de l'intervalle : minutes, hours ou days."),
    action: str = typer.Option("sort", "--action", help="Commande à planifier : sort ou clean."),
    args: str = typer.Option(None, "--args", help="Options supplémentaires passées telles quelles (ex: '--group-by extension')."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander de confirmation."),
) -> None:
    """Planifie l'exécution automatique et périodique de sort/clean sur un
    dossier, via cron (Linux/Mac). L'action planifiée s'exécute toujours
    avec --apply --yes (sans supervision) : vérifiez d'abord son
    comportement en dry-run avant de l'automatiser.
    """
    try:
        preview = preview_schedule(
            folder,
            every=every,
            unit=unit,
            action=action,
            extra_args=args,
        )
        if not preview.cron_available:
            console.print("[yellow]'crontab' est introuvable sur ce système (probablement Windows).[/yellow]")
            console.print("Commande Planificateur de tâches équivalente (à exécuter vous-même) :\n")
            console.print(preview.windows_hint)
            raise typer.Exit(code=1)

        console.print(
            f"Ligne crontab à ajouter :\n  "
            f"{preview.cron_expression} {preview.command}\n"
        )

        if not yes and not typer.confirm("Confirmer l'ajout de cette tâche planifiée ?"):
            raise typer.Abort()

        job = install_schedule(
            folder,
            every=every,
            unit=unit,
            action=action,
            extra_args=args,
        )
    except ScheduleError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Tâche planifiée (id {job.job_id}).[/green]")
    console.print(f"Pour la supprimer : janitor schedule remove {job.job_id}")


@schedule_app.command("list")
def schedule_list() -> None:
    """Liste les tâches File Janitor actuellement planifiées via cron."""
    try:
        jobs = list_schedules()
    except ScheduleError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not jobs:
        console.print("Aucune tâche planifiée.")
        return

    table = Table()
    table.add_column("ID")
    table.add_column("Planification (cron)")
    table.add_column("Description")
    for job in jobs:
        table.add_row(job.job_id, job.cron_expression, job.description)
    console.print(table)


@schedule_app.command("remove")
def schedule_remove(
    job_id: str = typer.Argument(..., help="Identifiant de la tâche à supprimer (voir 'janitor schedule list')."),
) -> None:
    """Supprime une tâche planifiée."""
    try:
        removed = remove_schedule(job_id)
    except ScheduleError as exc:
        console.print(f"[red]Erreur :[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if removed:
        console.print(f"[green]Tâche {job_id} supprimée.[/green]")
    else:
        console.print(f"[red]Aucune tâche avec l'id {job_id}.[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
