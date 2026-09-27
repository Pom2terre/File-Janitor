"""Petits utilitaires de formatage pour l'affichage console."""

from __future__ import annotations


def human_size(num_bytes: int) -> str:
    """Formate un nombre d'octets en unité lisible (style FR : Go, Mo, ...)."""
    step = 1000.0
    units = ["o", "Ko", "Mo", "Go", "To", "Po"]
    size = float(num_bytes)
    for unit in units:
        if size < step:
            if unit == "o":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} Eo"


def human_count(n: int) -> str:
    """Formate un entier avec des espaces comme séparateurs de milliers (style FR)."""
    return f"{n:,}".replace(",", " ")
