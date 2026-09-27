"""Génération des rapports d'analyse (JSON et HTML).

Le rapport HTML est un unique fichier autonome (CSS inline, aucun JS ni
ressource externe) afin de rester consultable hors-ligne, y compris des
années plus tard. Les valeurs interpolées (chemins, raisons) sont toujours
échappées via `html.escape` : un nom de fichier contenant `<`, `>` ou `&`
ne doit jamais casser la page ni permettre d'y injecter du HTML/JS.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from file_janitor.formatting import human_count, human_size
from file_janitor.models import CATEGORY_LABELS, ActionItem, FileCategory, Plan, ScanResult

DEFAULT_MAX_ITEMS_PER_CATEGORY = 500


def build_report_data(scan: ScanResult, plan: Plan) -> dict:
    """Construit la structure de données brute du rapport (utilisée pour le JSON)."""
    return {
        "root": str(scan.root),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_count": scan.total_count,
        "total_size": scan.total_size,
        "categories": {
            category.value: {
                "label": CATEGORY_LABELS[category],
                "count": len(plan.items(category)),
                "size": plan.total_size(category),
                "items": [
                    {
                        "path": str(item.path),
                        "size": item.size,
                        "reason": item.reason,
                        "destination": str(item.destination) if item.destination else None,
                    }
                    for item in plan.items(category)
                ],
            }
            for category in FileCategory
        },
    }


def _bar_row(label: str, count: int, size: int, max_size: int) -> str:
    pct = (size / max_size * 100) if max_size else 0.0
    return f"""
    <tr>
      <td class="bar-label">{escape(label)}</td>
      <td class="bar-cell"><div class="bar" style="width:{pct:.1f}%"></div></td>
      <td class="bar-value">{human_size(size)}</td>
      <td class="bar-count">{human_count(count)} fichier(s)</td>
    </tr>"""


def _items_rows(items: list[ActionItem], max_items: int) -> tuple[str, int]:
    shown = sorted(items, key=lambda i: i.size, reverse=True)[:max_items]
    hidden_count = len(items) - len(shown)
    rows = "\n".join(
        f"""
    <tr>
      <td class="path">{escape(str(item.path))}</td>
      <td class="size">{human_size(item.size)}</td>
      <td class="reason">{escape(item.reason)}</td>
    </tr>"""
        for item in shown
    )
    return rows, hidden_count


def _category_section(category: FileCategory, plan: Plan, max_items: int) -> str:
    items = plan.items(category)
    label = CATEGORY_LABELS[category]
    if not items:
        return f"""
  <section class="category">
    <h2>{escape(label)}</h2>
    <p class="empty">Rien à signaler.</p>
  </section>"""

    rows, hidden_count = _items_rows(items, max_items)
    hidden_note = (
        f'<p class="hidden-note">... et {human_count(hidden_count)} fichier(s) supplémentaire(s) non affiché(s).</p>'
        if hidden_count > 0
        else ""
    )
    total_size = plan.total_size(category)
    return f"""
  <section class="category">
    <details open>
      <summary>
        <h2>{escape(label)} — {human_count(len(items))} fichier(s), {human_size(total_size)}</h2>
      </summary>
      <table class="items">
        <thead><tr><th>Chemin</th><th>Taille</th><th>Raison</th></tr></thead>
        <tbody>{rows}
        </tbody>
      </table>
      {hidden_note}
    </details>
  </section>"""


_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
header h1 { margin-bottom: 0.2rem; font-size: 1.5rem; }
header p.meta { color: #666; margin-top: 0; }
table.bars { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
table.bars td { padding: 0.35rem 0.5rem; vertical-align: middle; }
td.bar-label { width: 14rem; white-space: nowrap; }
td.bar-cell { width: 100%; }
td.bar-value, td.bar-count { white-space: nowrap; text-align: right; color: #555; }
.bar { background: linear-gradient(90deg, #4f8cff, #2f6fed); height: 0.9rem; border-radius: 3px; min-width: 2px; }
section.category { margin: 1.5rem 0; border-top: 1px solid #ddd; padding-top: 1rem; }
section.category h2 { font-size: 1.1rem; display: inline; }
summary { cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary:before { content: "▸ "; }
details[open] summary:before { content: "▾ "; }
table.items { width: 100%; border-collapse: collapse; margin-top: 0.75rem; font-size: 0.9rem; }
table.items th, table.items td { text-align: left; padding: 0.3rem 0.5rem; border-bottom: 1px solid #eee; }
table.items td.path { font-family: ui-monospace, monospace; word-break: break-all; }
table.items td.size { white-space: nowrap; text-align: right; }
p.empty { color: #4a9; }
p.hidden-note { color: #888; font-style: italic; }
footer { margin-top: 2rem; color: #999; font-size: 0.85rem; }
"""


def render_html_report(
    scan: ScanResult,
    plan: Plan,
    *,
    max_items_per_category: int = DEFAULT_MAX_ITEMS_PER_CATEGORY,
) -> str:
    """Génère un rapport HTML autonome (une seule page, sans dépendance externe)."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    max_category_size = max((plan.total_size(c) for c in FileCategory), default=0)

    bars = "\n".join(
        _bar_row(CATEGORY_LABELS[c], len(plan.items(c)), plan.total_size(c), max_category_size)
        for c in FileCategory
    )
    sections = "\n".join(_category_section(c, plan, max_items_per_category) for c in FileCategory)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Rapport File Janitor — {escape(str(scan.root))}</title>
<style>{_CSS}</style>
</head>
<body>
  <header>
    <h1>Analyse de {escape(str(scan.root))}</h1>
    <p class="meta">{human_count(scan.total_count)} fichiers — {human_size(scan.total_size)} — généré le {generated_at}</p>
  </header>

  <table class="bars">
    <tbody>{bars}
    </tbody>
  </table>

  {sections}

  <footer>Généré par File Janitor. Ce rapport est en lecture seule : aucune action n'a été effectuée.</footer>
</body>
</html>
"""


def write_html_report(
    scan: ScanResult,
    plan: Plan,
    output: Path,
    *,
    max_items_per_category: int = DEFAULT_MAX_ITEMS_PER_CATEGORY,
) -> None:
    html = render_html_report(scan, plan, max_items_per_category=max_items_per_category)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
