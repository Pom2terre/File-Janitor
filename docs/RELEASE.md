# Release workflow

Ce document décrit le workflow maintainer pour préparer et valider une release de File Janitor.

Il complète le `README.md` : le README documente l’usage du produit ; ce fichier documente la fabrication et la validation des artefacts de release.

> Baseline au moment de la rédaction : File Janitor `0.4.0`, Python `>=3.10`, packaging `setuptools`, wheel + sdist.

## 1. Pré-requis

Travaillez depuis la racine du repository dans un virtualenv de développement contenant au minimum les dépendances GUI, `pytest`, `build` et `twine.

```bash
python --version
python -m build --version
python -m twine --version
```

La release doit partir d’un working tree clean :

```bash
git status
git branch --show-current
```

Pour une release normale, la branche attendue est `master`.

## 2. Vérifier la version

File Janitor maintient la version dans deux emplacements qui doivent rester synchronisés :

- `pyproject.toml` → `[project].version`
- `file_janitor/__init__.py` → `__version__`

Le test `tests/test_package_metadata.py` protège cette cohérence.

Contrôle rapide :

```bash
python - <<'PY'
import tomllib
from pathlib import Path

from file_janitor import __version__

with Path("pyproject.toml").open("rb") as stream:
    project_version = tomllib.load(stream)["project"]["version"]

assert project_version == __version__
print(f"Version release : {__version__}")
PY
```

Pour préparer une nouvelle version, modifiez les deux valeurs dans le même patch puis lancez les tests avant de construire les artefacts.

## 3. Quality gate avant build

Compilez les sources et les tests, puis lancez la suite complète :

```bash
python3 -m compileall -q file_janitor tests
pytest -q
git diff --check
git status
```

Tous les tests doivent passer et aucun changement inattendu ne doit être présent.

Le frontend GUI ne doit pas importer directement les modules internes du moteur. Le contrôle utilisé pendant le développement peut être rejoué :

```bash
grep -RInE   'file_janitor\.(executor|models|path_safety|plan|report|scanner|scheduler|storage)'   file_janitor/gui   --include='*.py'   --exclude-dir='__pycache__'   || echo "Aucun import moteur interdit détecté"
```

## 4. Build clean

Supprimez les artefacts précédents avant chaque build de release :

```bash
rm -rf build dist
python -m build
```

Le build doit produire exactement un wheel et un sdist correspondant à la version courante :

```bash
ls -lh dist/
```

Pour `0.4.0`, les noms attendus sont :

```text
file_janitor-0.4.0-py3-none-any.whl
file_janitor-0.4.0.tar.gz
```

Pour une version ultérieure, adaptez naturellement le numéro.

## 5. Valider les distributions

Validez les métadonnées avec Twine :

```bash
python -m twine check dist/*
```

Le wheel et le sdist doivent tous les deux retourner `PASSED`.

Contrôlez également leur contenu :

```bash
python - <<'PY'
from pathlib import Path
from zipfile import ZipFile
import tarfile

wheels = list(Path("dist").glob("*.whl"))
sdists = list(Path("dist").glob("*.tar.gz"))

assert len(wheels) == 1, wheels
assert len(sdists) == 1, sdists

with ZipFile(wheels[0]) as archive:
    names = archive.namelist()
    assert any(name.endswith(".dist-info/METADATA") for name in names)
    assert any(name.endswith(".dist-info/entry_points.txt") for name in names)
    assert any("/licenses/LICENSE" in name for name in names)

with tarfile.open(sdists[0]) as archive:
    names = archive.getnames()
    assert any(name.endswith("/LICENSE") for name in names)
    assert any(name.endswith("/README.md") for name in names)

print("Contenu wheel + sdist : OK")
PY
```

Les artefacts de build (`build/`, `dist/`) sont locaux et ignorés par Git.

## 6. Smoke test depuis un environnement vierge

Le test le plus utile après le build est une installation hors checkout. Créez un virtualenv temporaire et installez le wheel avec l’extra GUI :

```bash
RELEASE_VENV="$(mktemp -d)/venv"

python3 -m venv "$RELEASE_VENV"
"$RELEASE_VENV/bin/python" -m pip install --upgrade pip
"$RELEASE_VENV/bin/python" -m pip install "dist/$(basename "$(ls dist/*.whl)")"[gui]
```

Puis quittez le repository avant les imports afin de vérifier que Python utilise réellement le package installé :

```bash
(
  cd /tmp

  "$RELEASE_VENV/bin/janitor" --help

  QT_QPA_PLATFORM=offscreen "$RELEASE_VENV/bin/python" - <<'PY'
from file_janitor import __version__
from file_janitor.gui.app import create_application
from file_janitor.gui.main_window import MainWindow

app = create_application(["file-janitor-release-smoke"])
window = MainWindow()

assert window.windowTitle() == "File Janitor"
assert window.version_label.text() == f"File Janitor v{__version__}"
assert window.destination_edit is not None
assert window.destination_browse_button is not None
assert window.destination_source_button is not None

window.close()
print(f"GUI smoke test : OK ({__version__})")
PY
)
```

Supprimez ensuite le virtualenv temporaire si vous n’en avez plus besoin.

### Smoke test du sdist

Le sdist doit lui aussi être installable. Utilisez un second environnement vierge :

```bash
SDIST_VENV="$(mktemp -d)/venv"

python3 -m venv "$SDIST_VENV"
"$SDIST_VENV/bin/python" -m pip install --upgrade pip
"$SDIST_VENV/bin/python" -m pip install "dist/$(basename "$(ls dist/*.tar.gz)")"[gui]

(
  cd /tmp
  "$SDIST_VENV/bin/janitor" --help
  QT_QPA_PLATFORM=offscreen "$SDIST_VENV/bin/janitor-gui" --help >/dev/null 2>&1 || true
)
```

Pour la GUI, le smoke test Python du wheel reste le contrôle fonctionnel de référence ; lancer directement un event loop GUI dans une procédure automatisée peut bloquer le terminal.

## 7. Gate Git avant tag

Avant de tagger :

```bash
git status
pytest -q
git diff --check
git log --oneline --decorate -8
```

Le working tree doit être clean et le commit `HEAD` doit être celui que vous souhaitez publier.

Récupérez la version courante :

```bash
VERSION="$(python - <<'PY'
from file_janitor import __version__
print(__version__)
PY
)"

echo "$VERSION"
```

Vérifiez qu’aucun tag correspondant n’existe déjà :

```bash
git tag --list "v$VERSION"
```

## 8. Créer le tag de release

Une fois tous les gates validés, créez un tag annoté :

```bash
git tag -a "v$VERSION" -m "File Janitor $VERSION"
```

Contrôlez-le :

```bash
git show --no-patch --decorate "v$VERSION"
git status
```

Le tag marque le commit validé ; il ne publie rien à lui seul.

Si un remote Git est configuré et que la release doit y être poussée, vérifiez d’abord explicitement la destination avec :

```bash
git remote -v
```

Le push du commit, du tag ou des artefacts reste une action de publication volontaire et n’est pas automatisé par cette checklist.

## 9. Publication PyPI

La publication PyPI n’est pas considérée comme configurée par défaut dans File Janitor.

Avant toute première publication, configurez séparément le compte, l’authentification et la destination souhaitée (TestPyPI ou PyPI). Ne déduisez jamais ces paramètres de cette documentation.

Lorsque cette infrastructure aura été décidée, la commande de publication pourra être ajoutée à ce workflow avec son mode d’authentification réel.

## 10. Backup

Après validation du commit/tag, effectuez le backup habituel du repository et conservez au minimum :

- le commit SHA de la release ;
- le tag ;
- le wheel ;
- le sdist ;
- éventuellement leurs checksums.

Le backup n’est pas un substitut au tag Git, et le tag n’est pas un substitut au backup.

## Checklist release

- [ ] working tree clean
- [ ] version synchronisée entre `pyproject.toml` et `file_janitor.__version__`
- [ ] `compileall` passe
- [ ] suite `pytest` green
- [ ] `git diff --check` clean
- [ ] aucun import moteur interdit dans `file_janitor/gui`
- [ ] `build/` et `dist/` nettoyés avant build
- [ ] wheel construit
- [ ] sdist construit
- [ ] `twine check dist/*` → `PASSED`
- [ ] contenu wheel/sdist contrôlé
- [ ] wheel installé dans un venv vierge
- [ ] CLI smoke-testée hors checkout
- [ ] GUI smoke-testée hors checkout
- [ ] smoke manuel : source A + destination B → preview pointe vers B
- [ ] smoke manuel : **Utiliser la source** restaure le fallback vers la source
- [ ] sdist installé dans un second venv vierge
- [ ] `HEAD` final vérifié
- [ ] tag annoté créé
- [ ] backup effectué
- [ ] publication éventuelle effectuée explicitement
