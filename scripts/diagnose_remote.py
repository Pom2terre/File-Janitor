"""Mesure sur un dossier de test isolé les étapes du classement File Janitor.

Usage : python -m scripts.diagnose_remote /chemin/vers/le/montage --size-mib 64
Le script ne lit ni ne déplace aucun fichier déjà présent sur le montage.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
import os

from file_janitor.application import (
    AnalysisResult,
    ClassificationMode,
    analyze_folder,
    classification_plan_config,
    execute_selected_actions,
    remote_folder_info,
)


def cleanup_mount_temp(folder: Path, *, attempts: int = 12) -> bool:
    """Attend les écritures FUSE retardées et réessaie le nettoyage du test."""

    for attempt in range(attempts):
        try:
            shutil.rmtree(folder)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 == attempts:
                return False
            sleep(min(3.0, 0.25 * (2**attempt)))
    return not folder.exists()


def timed_analysis(
    folder: Path, mode: ClassificationMode, *, metadata: bool,
) -> tuple[float, AnalysisResult]:
    start = perf_counter()
    result = analyze_folder(
        folder,
        config=classification_plan_config(mode),
        compute_hashes=False,
        compute_content_type=False,
        collect_metadata=metadata,
    )
    elapsed = perf_counter() - start
    return elapsed, result


def measure_move(
    root: Path, history: Path, *, size_mib: int, fast: bool,
    visibility_seconds: float = 12,
) -> tuple[float, float, bool]:
    source_folder = root / ("rapide" if fast else "sur")
    source_folder.mkdir()
    source = source_folder / "echantillon.bin"
    chunk = os.urandom(1024 * 1024)
    start = perf_counter()
    with source.open("wb") as file:
        for _ in range(size_mib):
            file.write(chunk)
        file.flush()
        os.fsync(file.fileno())
    write_seconds = perf_counter() - start

    deadline = perf_counter() + visibility_seconds
    while True:
        _, result = timed_analysis(
            source_folder, ClassificationMode.EXTENSION, metadata=False,
        )
        if result.summary.total_count == 1 or perf_counter() >= deadline:
            break
        sleep(min(2, max(0, deadline - perf_counter())))
    details = result.details_for("to_sort")
    if details is None or len(details.items) != 1:
        raise RuntimeError(
            "L'inventaire du fichier de test à déplacer reste incomplet "
            f"({result.summary.total_count} trouvé, {len(result.scan_errors)} erreur(s))"
        )

    copied = False

    def progress(event: object) -> None:
        nonlocal copied
        if isinstance(event, tuple) and event and event[0] == "execution_copy":
            copied = True

    start = perf_counter()
    execution = execute_selected_actions(
        result,
        {("to_sort", 0)},
        db_path=history,
        progress_callback=progress,
        allow_unsafe_fast_move=fast,
    )
    move_seconds = perf_counter() - start
    if execution.success != 1 or execution.errors:
        raise RuntimeError(f"Déplacement du fichier de test échoué : {execution.errors}")
    return write_seconds, move_seconds, copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Dossier existant sur le montage à mesurer")
    parser.add_argument("--size-mib", type=int, default=64, help="Taille de chaque fichier de test en Mio (défaut : 64)")
    parser.add_argument("--include-fast", action="store_true", help="Tester aussi le renommage rapide sur un fichier temporaire isolé")
    parser.add_argument("--visibility-seconds", type=float, default=12, help="Attente maximale des nouveaux fichiers dans l'inventaire (défaut : 12 s)")
    args = parser.parse_args()
    if not args.folder.is_dir() or not 1 <= args.size_mib <= 1024 or not 0 <= args.visibility_seconds <= 120:
        parser.error("Indiquez un dossier accessible, une taille de 1 à 1024 Mio et une attente de 0 à 120 s")

    info = remote_folder_info(args.folder)
    print(f"Montage détecté : {info.filesystem_type or 'non identifié'}", flush=True)

    mount_context = TemporaryDirectory(
        prefix="file-janitor-diagnostic-",
        dir=args.folder,
        ignore_cleanup_errors=True,
    )
    mount_path = Path(mount_context.name)
    try:
        with mount_context as mount_temp:
            with TemporaryDirectory(prefix="file-janitor-history-") as local_temp:
                root = Path(mount_temp)
                history = Path(local_temp) / "history.db"
                sample_folder = root / "inventaire"
                sample_folder.mkdir()
                sample = sample_folder / "exemple.bin"
                sample.write_bytes(b"test")
                try:
                    extension_seconds, extension_result = timed_analysis(
                        sample_folder, ClassificationMode.EXTENSION, metadata=False,
                    )
                    print(
                        f"Inventaire extension : {extension_seconds:.2f} s ; "
                        f"{extension_result.summary.total_count} fichier(s) vu(s)", flush=True,
                    )
                    start = perf_counter()
                    date_seconds = 0.0
                    attempts = 0
                    while True:
                        duration, date_result = timed_analysis(
                            sample_folder, ClassificationMode.DATE, metadata=True,
                        )
                        date_seconds += duration
                        attempts += 1
                        if date_result.summary.total_count == 1:
                            break
                        remaining = args.visibility_seconds - (perf_counter() - start)
                        if remaining <= 0:
                            break
                        sleep(min(2, remaining))
                    visibility = perf_counter() - start
                    print(
                        f"Inventaire date/taille : {date_seconds:.2f} s de scan ; "
                        f"{date_result.summary.total_count} fichier(s) vu(s) "
                        f"après {attempts} essai(s) / {visibility:.1f} s ; "
                        f"{len(date_result.scan_errors)} erreur(s)", flush=True,
                    )
                    if date_result.summary.total_count != 1:
                        try:
                            sample.stat()
                            stat_status = "réussi"
                        except OSError as exc:
                            stat_status = f"échec ({exc.strerror or type(exc).__name__})"
                        print(f"Stat direct du fichier : {stat_status}", flush=True)
                        print(
                            "Le classement par date ne voit pas encore cet échantillon ; "
                            "mesure du déplacement poursuivie.", flush=True,
                        )
                        if date_result.scan_errors:
                            print(f"Première erreur de scan : {date_result.scan_errors[0]}", flush=True)
                finally:
                    try:
                        sample.unlink(missing_ok=True)
                    except OSError:
                        # Le rclone mount peut rendre visible la suppression avec
                        # retard ; la suppression récursive réessaiera à la fin.
                        pass

                for fast in (False, True) if args.include_fast else (False,):
                    write, move, copied = measure_move(
                        root, history, size_mib=args.size_mib, fast=fast,
                        visibility_seconds=args.visibility_seconds,
                    )
                    label = "rapide" if fast else "sûr"
                    rate = args.size_mib / move if move > 0 else float("inf")
                    print(
                        f"Mode {label} : écriture {write:.2f} s ; "
                        f"déplacement {move:.2f} s ({rate:.1f} Mio/s) ; "
                        f"copie {'oui' if copied else 'non'}", flush=True,
                    )
    finally:
        if not cleanup_mount_temp(mount_path):
            print(
                "Nettoyage incomplet du dossier temporaire : "
                f"{mount_path}. Vérifiez puis supprimez ce dossier dans le montage.",
                file=sys.stderr,
                flush=True,
            )


if __name__ == "__main__":
    main()
