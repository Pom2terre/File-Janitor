"""Point d'entrée de l'application graphique File Janitor."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from file_janitor.gui.main_window import MainWindow
from file_janitor.gui.preferences import GuiPreferences


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Crée ou réutilise l'instance Qt de l'application.

    Cette fonction séparée du point d'entrée facilite les tests et évite de
    créer plusieurs ``QApplication`` dans un même processus.
    """

    QCoreApplication.setOrganizationName("Pom2terre")
    QCoreApplication.setApplicationName("File Janitor")

    existing = QApplication.instance()
    if existing is not None:
        return existing

    return QApplication(list(argv) if argv is not None else sys.argv)


def main() -> int:
    """Lance l'interface graphique et retourne le code de sortie Qt."""

    app = create_application()
    window = MainWindow(preferences=GuiPreferences())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
