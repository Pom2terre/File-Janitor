# File Janitor

File Janitor analyse un dossier, prépare un plan d’actions et vous laisse garder le contrôle avant toute modification. La version 0.4.0 propose deux frontends sur le même moteur : une **GUI desktop** pour le workflow quotidien et une **CLI** pour les usages avancés, scripts et automatisations.

Le principe reste le même :

```text
scan → plan → preview → execute → undo
```

L’analyse et la preview ne modifient rien. Les opérations filesystem ne sont exécutées qu’après une action explicite de l’utilisateur, et les exécutions sont journalisées pour permettre un undo.

## Installation

File Janitor nécessite Python 3.10 ou plus.

### GUI + CLI

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
```

Lancez ensuite la GUI avec :

```bash
janitor-gui
```

La CLI reste disponible dans le même environnement :

```bash
janitor --help
```

### CLI uniquement

Si vous n’avez pas besoin de PySide6 :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Démarrage rapide — interface graphique

Le workflow principal tient en quelques étapes :

1. Cliquez sur **Choisir un dossier…** pour sélectionner la source.
2. Choisissez éventuellement un dossier **Destination** distinct. Sans destination explicite, File Janitor utilise la source comme destination.
3. Sélectionnez un **Mode de classement**.
4. Cliquez sur **Analyser**.
5. Consultez le résumé puis ouvrez la zone **Prévisualisation des actions**.
6. Vérifiez les chemins de destination proposés et sélectionnez les actions à conserver.
7. Lancez **Exécuter la sélection…** et confirmez.
8. Consultez le résultat dans **Historique**.
9. Si nécessaire, utilisez **Annuler le dernier batch…**.

Le bouton **Renommer par lot…** prépare également une prévisualisation de noms
pour les fichiers du dossier et de ses sous-dossiers. Les champs disponibles
sont `{name}` (nom sans extension), `{ext}` (extension avec le point),
`{parent}` (nom du dossier parent), `{date}` (date de modification au format
`AAAA-MM-JJ`) et `{counter}`. Un format comme `{counter:03d}` ajoute des zéros
au compteur, qui repart à 1 dans chaque dossier. Les noms inchangés et les
collisions restent visibles mais ne peuvent pas être sélectionnés ; toute
destination devenue occupée après la prévisualisation est également refusée.
Les renommages exécutés apparaissent dans l’historique et peuvent être annulés.

Le bouton **Archiver selon l’ancienneté…** prépare une prévisualisation des
fichiers dont la date de modification atteint le seuil choisi (365 jours par
défaut). Choisissez un dossier d’archive distinct de la source : les chemins
relatifs sont conservés sous cette destination. Une cible déjà existante est
signalée comme bloquée et n’est jamais écrasée. Seuls les fichiers cochés sont
déplacés ; l’opération apparaît dans l’historique et peut être annulée.
L’assistant propose aussi un ZIP unique compressé, qui conserve les chemins
relatifs à l’intérieur de l’archive. Dans ce mode, les sources ne sont mises à
la corbeille qu’après vérification du ZIP ; l’annulation restaure les sources et
retire le ZIP.

Le bouton **Supprimer les dossiers vides…** repère les dossiers vides ainsi
que les parents qui ne contiennent que des sous-dossiers vides. L’aperçu les
présente du plus profond au plus haut ; sélectionnez les enfants et leurs
parents pour retirer toute la branche. À l’exécution, un dossier devenu non
vide est conservé. Le batch est annulable depuis l’historique.

Après une analyse, le bouton **Analyser** reste désactivé tant que la source, la destination effective et le mode de classement n’ont pas changé. Après une exécution, une nouvelle analyse peut être lancée car le filesystem a potentiellement changé.

### Destination GUI

Le champ **Destination** contrôle la racine dans laquelle les dossiers de classement seront créés.

- si aucun folder de destination n’est choisi, la source est utilisée comme destination : le comportement historique reste donc inchangé ;
- **Choisir…** permet de sélectionner un autre folder ;
- **Utiliser la source** revient explicitement au comportement par défaut et oublie la destination externe mémorisée ;
- la preview affiche les chemins calculés à partir de la destination effective avant toute exécution ;
- changer de destination invalide le contexte d’analyse courant et réactive **Analyser**.

Exemple :

```text
Source       : ~/Downloads
Destination  : ~/Sorted
Mode         : Extension

~/Downloads/photo.jpg
    → ~/Sorted/jpg/photo.jpg
```

Le destination folder n’est pas une opération séparée ajoutée après l’analyse : il fait partie de la configuration utilisée pour construire la preview. L’exécution applique ensuite les destinations déjà affichées.

### Modes de classement GUI

La GUI permet de choisir un tri principal et, si besoin, un second critère. Les quatre critères peuvent être combinés dans l'ordre choisi : `Date → Extension` place un fichier dans `AAAA-MM/mp4/fichier.mp4`, tandis que `Extension → Date` le place dans `mp4/AAAA-MM/fichier.mp4`. Une seule action déplace le fichier directement vers sa destination finale.

| Mode | Comportement |
|---|---|
| **Extension** | Classe les fichiers dans des dossiers correspondant à leur extension. |
| **Date (AAAA-MM)** | Classe les fichiers par mois de modification. |
| **Nom commun** | Regroupe uniquement les séries partageant une racine de nom commune significative ; les fichiers isolés restent non classés. |
| **Taille** | Classe par tranches de taille, de `<10 Ko` jusqu’à `500 Mo et plus`. |

Avec **Nom commun** en second critère, les séries sont recherchées séparément dans chaque groupe du premier critère. Les fichiers sans racine commune restent non classés. Choisir « Aucun » pour le second critère conserve le classement simple.

Changer de source, de destination effective, de mode ou de profil d’analyse invalide le contexte d’analyse courant et réactive **Analyser**.

### Profil d’analyse

La GUI propose deux profils :

- **Standard** : analyse complète avec détection par contenu ;
- **Remote / rapide** : évite le hachage et les lectures de contenu coûteuses sur les stockages cloud/FUSE.

Lorsqu’un dossier est détecté sur un filesystem distant, la GUI sélectionne automatiquement **Remote / rapide** tant que l’utilisateur n’a pas imposé manuellement un autre profil. Pour les mounts `fuse.rclone`, les critères **Date** et **Taille**, à l'un ou l'autre niveau, peuvent utiliser `rclone lsjson` afin de récupérer chemin, taille et date directement côté backend au lieu d’effectuer un `stat()` FUSE par fichier. Les combinaisons utilisant uniquement **Extension** et **Nom commun** restent metadata-light et n’ont pas besoin de lire le contenu des fichiers.

Le lecteur pCloud natif est également reconnu lorsqu’il apparaît sous le type
`fuse` avec la source `pCloud.fs`. Si un profil Standard a été enregistré
manuellement, il reste prioritaire : choisissez **Remote / rapide** dans la GUI
pour éviter de lire les vidéos pendant l’analyse. Sur un déplacement pCloud où
`RENAME_NOREPLACE` n’est pas disponible, le mode d’exécution **Sûr** doit encore
recopier chaque fichier pour garantir qu’aucune destination concurrente ne soit
écrasée. Le mode **Rapide** évite cette copie, avec le risque de concurrence
signalé dans la confirmation de l’interface.

Avant une sélection d’au moins 512 Mio de déplacements sur le même montage
distant, la confirmation propose directement **Exécuter en mode sûr**,
**Exécuter en mode rapide** ou **Annuler**. Aucun renommage rapide n’est choisi
sans action explicite. Pendant l’exécution, le pied de fenêtre affiche
séparément la progression du lot et, lorsqu’une copie est en cours, celle du
fichier actuel ; le nombre de fichiers traités n’augmente qu’après la fin de
chaque action.

Les exclusions techniques par défaut sont également poussées dans le listing rclone lorsqu’il est sûr de le faire. Le filtrage Python reste ensuite appliqué comme contrôle final ; si une règle de réinclusion `!…` est présente ou si les exclusions par défaut sont désactivées, ce pushdown est désactivé afin de préserver exactement la sémantique attendue.

Pour les grandes arborescences, le JSON rclone est stocké dans un fichier temporaire local puis décodé objet par objet : le listing complet n'est plus dupliqué en mémoire. L'analyse affiche le nombre de fichiers repérés pendant ce décodage, sans pourcentage tant que le total n'est pas connu.

Pour mesurer les temps sur votre propre montage, depuis la racine du projet :

```bash
.venv/bin/python -m scripts.diagnose_remote /chemin/vers/pcloud --size-mib 64
```

Le diagnostic crée ses propres dossiers temporaires sur le montage, mesure l'inventaire, l'écriture de l'échantillon et son déplacement sûr, puis efface les fichiers créés. Le nettoyage du montage est réessayé si rclone rend visibles les suppressions avec retard ; si un nettoyage reste incomplet, le chemin du dossier temporaire est affiché pour vérification manuelle. Si l'inventaire par date tarde à voir un fichier neuf, il réessaie pendant 12 secondes (réglable avec `--visibility-seconds`) et indique le nombre de fichiers et les erreurs observées avant de poursuivre la mesure du déplacement. `--include-fast` mesure aussi le déplacement rapide dans un dossier isolé ; il n'est activé que si vous le demandez. L'historique du test reste dans un dossier temporaire local et est effacé avec lui. Les débits mesurés sur un petit échantillon ne préjugent pas des temps observés sur des vidéos plus volumineuses.

### Prévisualisation et exécution

La preview permet de contrôler les actions proposées avant exécution. Le **Périmètre de prévisualisation** limite la table à un groupe de classement précis ou à tous les fichiers classés. La recherche texte filtre ensuite uniquement le nom du fichier, le chemin de destination relatif à la racine choisie et la raison affichée ; elle ne recherche pas dans tout le chemin source ni dans le préfixe absolu de la destination.

En mode **Extension**, saisir exactement une extension telle que `xml` ou `.xml` depuis le périmètre global bascule directement vers le groupe correspondant. Le compteur de sélection reste global, tandis que les commandes de sélection indiquent explicitement la vue qu’elles affectent. Seules les actions sélectionnées sont envoyées au moteur lors de la confirmation.

Une exécution terminée invalide la preview précédente : elle ne peut pas être réutilisée sur un filesystem qui vient potentiellement de changer.

### Historique et undo

L’onglet **Historique** affiche les batches enregistrés et leurs opérations. Lorsqu’un dernier batch est annulable, la GUI expose **Annuler le dernier batch…** et demande confirmation avant l’undo.

L’undo s’appuie sur le journal d’exécution du moteur. Pour les opérations de copie, l’undo retire la copie créée sans toucher à l’original.

### Préférences

Le bouton **Préférences…** permet de choisir ce que la GUI restaure au prochain démarrage :

- dernier dossier source ;
- dernière destination externe ;
- mode de classement ;
- profil d’analyse ;
- dernier onglet.

Décocher une option oublie immédiatement la valeur enregistrée correspondante. Une destination mémorisée n’est restaurée que si le folder existe encore ; sinon la GUI revient au fallback sûr « destination = source ». Cliquer sur **Utiliser la source** efface également la destination externe mémorisée.

La géométrie de la fenêtre est restaurée automatiquement. **Réinitialiser les préférences…** ne supprime ni l’historique File Janitor ni vos fichiers.

### Raccourcis clavier

| Raccourci | Action |
|---|---|
| `Alt+1` | Affiche **Analyse** |
| `Alt+2` | Affiche **Prévisualisation** |
| `Alt+3` | Affiche **Historique** |
| `Ctrl+L` | Affiche **Analyse** et place le focus sur le champ du dossier |

Les shortcuts changent le contexte d’affichage sans modifier le contexte métier courant.

## Quick start CLI

```bash
janitor analyze ~/Downloads
janitor sort ~/Downloads
janitor sort ~/Downloads --apply --yes
janitor rename ~/Downloads --pattern '{date}_{counter:03d}_{name}{ext}'
janitor rename ~/Downloads --pattern '{name}_{counter:03d}{ext}' --recursive --apply
janitor archive ~/Downloads --to ~/Archives/Downloads --older-than-days 365
janitor archive ~/Downloads --to ~/Archives/Downloads --older-than-days 180 --apply
janitor archive ~/Downloads --to ~/Archives/Downloads --older-than-days 365 --format zip
janitor empty-dirs ~/Downloads
janitor empty-dirs ~/Downloads --apply
janitor clean ~/Downloads --apply
janitor history
janitor undo 3
```

`janitor rename` affiche d’abord les noms actuels, les noms proposés et les
collisions ; aucune modification n’est faite sans `--apply`. La commande ne
renomme que les fichiers directement dans le dossier par défaut. Ajoutez
`--recursive` pour inclure les sous-dossiers ; le compteur recommence alors à 1
dans chaque dossier. Les champs admis sont `{name}`, `{ext}`, `{parent}`,
`{date}` et `{counter}`. Les collisions de nom ne sont jamais écrasées ; les
opérations exécutées peuvent être annulées avec `janitor undo <batch>`.

`janitor archive DOSSIER --to DESTINATION --older-than-days JOURS` affiche les
fichiers dont la date de modification a dépassé le seuil et conserve leur
arborescence relative. Avec `--format zip`, les fichiers sélectionnés sont
compressés dans un ZIP unique en conservant cette arborescence à l’intérieur.
Le format par défaut est `folders`. La commande est en prévisualisation par défaut ; ajoutez
`--apply` pour déplacer les fichiers, puis `--yes` pour omettre la confirmation
interactive. La destination doit être distincte de la source et de ses
sous-dossiers. Les conflits sont bloqués sans écrasement et le batch peut être
annulé avec `janitor undo <batch>`.

`janitor empty-dirs DOSSIER` affiche les dossiers vides trouvés sans rien
supprimer. Ajoutez `--apply` pour les supprimer après confirmation, ou `--yes`
pour un lancement non interactif. Les sous-dossiers sont traités avant leurs
parents ; `rmdir` refuse un dossier si un élément y apparaît entre l’aperçu et
l’exécution. Le batch peut être annulé avec `janitor undo <batch>`.

`janitor analyze` est en lecture seule. Dans un terminal interactif, il peut afficher un menu permettant d’agir directement sur le résultat déjà scanné, sans refaire de scan. En script, pipe ou CI, ce menu est automatiquement ignoré.

Les commandes qui modifient le filesystem affichent d’abord le plan et utilisent un dry-run par défaut lorsque prévu par la commande. Les actions destructrices demandent une confirmation, sauf lorsque vous fournissez explicitement les options d’application/confirmation adaptées à un workflow non interactif.

Quelques commandes utiles :

```bash
janitor duplicates ~/Downloads
janitor large ~/Downloads --threshold-mb 500
janitor mismatches ~/Downloads
janitor report ~/Downloads -o report.json
janitor report ~/Downloads -o report.html
janitor history
janitor undo 3
```

## Architecture et sécurité

Le moteur suit un pipeline en cinq étapes :

```text
scan → plan → preview → execute → undo
```

- **scan** parcourt le filesystem en lecture seule et produit les métadonnées nécessaires ;
- **plan** applique les règles sans modifier le disque ;
- **preview** expose les actions proposées avant exécution ;
- **execute** applique uniquement les actions validées et journalise le batch ;
- **undo** restaure, lorsque l’opération le permet, l’état précédent à partir du journal.

Les suppressions gérées par le moteur passent par la corbeille locale File Janitor plutôt que par une suppression définitive directe. Le durcissement transactionnel de `COPY`, `MOVE`, `TRASH` et `undo`, ainsi que les limites connues, sont détaillés dans [`docs/ENGINE_SAFETY_ROADMAP.md`](docs/ENGINE_SAFETY_ROADMAP.md).

## CLI avancée

Les sections suivantes documentent les fonctions avancées du moteur et de la CLI.

## Classement par contenu réel

Le classement (`sort`) et la détection d'archives ne se fient pas qu'à
l'extension : les premiers octets de chaque fichier (« magic bytes ») sont
inspectés pour déterminer son vrai type ([file_janitor/scanner/filetype.py](file_janitor/scanner/filetype.py)),
sans dépendance externe (pas de libmagic).

- Un fichier **sans extension** dont le contenu est reconnu (image, PDF,
  archive, ...) est quand même proposé au classement.
- Un ZIP est inspecté plus finement pour distinguer une archive générique
  d'un `.docx`/`.xlsx`/`.pptx` (Office Open XML), d'un `.jar` (Java) ou d'un
  `.apk` (Android).
- Si l'extension déclarée ne correspond pas au contenu détecté (ex. un
  `.jpg` qui est en fait une archive ZIP), le fichier est signalé dans
  « Extensions trompeuses » (`janitor mismatches`) et classé selon son
  **vrai** contenu plutôt que son extension.
- `--no-content-check` désactive cette détection (repli sur l'extension
  seule, plus rapide) sur `analyze`, `sort`, `clean` et `report`.

## Stratégies de classement (`--group-by`)

`kind` (contenu réel, voir ci-dessus) est la stratégie par défaut, mais
`sort` et `analyze` acceptent aussi `--group-by` pour classer *tous* les
fichiers de la racine selon un autre critère purement mécanique :

```bash
janitor sort ~/Downloads --group-by extension   # dossiers mp3/, jpg/, pdf/, ...
janitor sort ~/Downloads --group-by alphabet    # dossiers a/, b/, ..., # pour non-alpha
janitor sort ~/Downloads --group-by size        # kilobytes/, megabytes/, gigabytes/
janitor sort ~/Downloads --group-by date --date-granularity month  # 2024-05/, 2024-06/, ...
```

`--date-granularity` (day/month/year, défaut day) ne s'applique qu'à
`--group-by date`. Contrairement à `kind`, ces stratégies ne filtrent rien :
même un fichier au contenu ou à l'extension totalement inconnus est classé.

## Règles personnalisées (`--rules`)

Quand le classement par famille ou extension est trop simpliste, un fichier
JSON de règles permet de combiner plusieurs conditions (toutes requises —
ET logique) et de choisir une destination sur mesure. La première règle
qui correspond à un fichier l'emporte ; les fichiers non couverts par
aucune règle retombent sur `--group-by` (kind par défaut).

```json
[
  {
    "name": "Films",
    "destination": "Films",
    "extension": ["mp4", "mkv", "avi"],
    "name_contains": "movie",
    "size_gt": "100MB"
  },
  {
    "name": "Photos de vacances",
    "destination": "Photos/Vacances",
    "extension": ["jpg", "jpeg"],
    "name_contains": "vacances",
    "date_after": "2024-01-01"
  }
]
```

```bash
janitor sort ~/Downloads --rules rules.json --apply --yes
```

Conditions disponibles ([file_janitor/plan/rules.py](file_janitor/plan/rules.py)), toutes optionnelles mais au
moins une requise par règle :

| Clé | Type | Exemple |
|---|---|---|
| `extension` | liste | `["mp4", "mkv"]` |
| `name_contains` | texte (insensible à la casse) | `"movie"` |
| `size_gt` / `size_lt` | octets ou `"100MB"`/`"500KB"`/`"2GB"` | `"100MB"` |
| `date_after` / `date_before` | date ISO `AAAA-MM-JJ`, comparée à la date de modification | `"2024-01-01"` |
| `content_family` | famille de contenu réel : `image`, `video`, `audio`, `document`, `archive`, `executable`, `database`, `text` | `"image"` |

`destination` doit être un chemin relatif (peut contenir `/` pour des
sous-dossiers imbriqués comme `Photos/Vacances`) ; les chemins absolus et
`..` sont refusés. Toute clé inconnue, règle sans condition, date/taille
mal formée ou destination dangereuse est rejetée avec un message d'erreur
clair avant toute analyse (`janitor sort` s'arrête avec le code 1).

## Templates (`--template`) : regroupement par motif commun

Quand des fichiers partagent un schéma de nommage (ex. `hello-world-1.jpg`,
`hello-world-2.jpg`, `hello-world-3.jpg`), `--template` les regroupe
automatiquement dans un dossier nommé d'après la partie commune du nom
(`hello-world/`), sans avoir à définir chaque groupe manuellement :

```bash
janitor sort ~/Downloads --template --apply --yes
```

Le motif par défaut retire un compteur numérique final (avec séparateur
`-`, `_` ou espace optionnel). Un motif regex personnalisé (avec un groupe
de capture obligatoire) peut être fourni pour d'autres schémas de nommage :

```bash
janitor sort ~/Downloads --template-pattern '^(\w{3})_.*$' --apply --yes
```

Priorité d'application (chaque étape ne traite que les fichiers non encore
couverts par la précédente) : **règles (`--rules`) > template > stratégie
de classement (`--group-by`)**. Un motif regex invalide ou sans groupe de
capture est rejeté avec un message clair (code de sortie 1).

## Plusieurs dossiers, destination commune (`--to`)

`sort` et `clean` acceptent plusieurs dossiers sources en une seule
commande : chaque dossier est scanné indépendamment, mais l'aperçu et
l'exécution (avec undo) sont regroupés en un seul batch.

```bash
janitor sort ~/Downloads ~/Documents --apply --yes
```

Par défaut, chaque dossier s'organise vers lui-même. Avec `--to`, tous les
dossiers listés sont classés vers une **destination commune** :

```bash
janitor sort ~/Downloads ~/Documents --to ~/Dropbox --apply --yes
```

Note : `clean` détecte les doublons *à l'intérieur* de chaque dossier listé,
pas entre deux dossiers différents (un même fichier présent dans deux
dossiers différents ne sera pas signalé comme doublon).

## Auto-organizing (`janitor schedule`)

File Janitor est une CLI, pas un service qui tourne en arrière-plan : plutôt
que de réinventer un ordonnanceur, `janitor schedule` délègue à **cron**
(Linux/Mac) pour exécuter `sort`/`clean` automatiquement à intervalle
régulier.

```bash
janitor schedule add ~/Downloads --every 2 --unit hours                     # sort --apply --yes toutes les 2h
janitor schedule add ~/Downloads --every 1 --unit days --action clean       # clean --apply --yes tous les jours
janitor schedule add ~/Downloads --every 30 --unit minutes --args "--group-by extension"
janitor schedule list                                                      # liste les tâches planifiées
janitor schedule remove <id>                                               # supprime une tâche
```

- `--every`/`--unit` (minutes/hours/days) sont traduits en expression cron
  (`*/N` — limite native de cron : redémarre à 0 à chaque nouvelle
  heure/jour/mois, donc pas un intervalle glissant parfaitement continu).
- Chaque tâche installée est identifiée par un tag unique dans le
  commentaire de la ligne crontab (`# file-janitor:<id> ...`) : la commande
  ne touche jamais aux autres entrées de votre crontab, ajout comme
  suppression.
- La commande planifiée s'exécute toujours avec `--apply --yes` (sans
  supervision possible) : **testez d'abord en dry-run** (`janitor sort
  ~/Downloads`) avant de l'automatiser.
- Sous Windows (pas de `crontab`), la commande équivalente `schtasks` est
  affichée à titre indicatif, mais n'est jamais exécutée automatiquement.

## Copier au lieu de déplacer

Par défaut, `sort` **déplace** les fichiers classés. Avec `--copy`, les
fichiers sont dupliqués : l'original reste intact à son emplacement, et
seule la copie apparaît dans le dossier de destination.

```bash
janitor sort ~/Downloads --apply --yes --copy
```

L'undo d'un batch effectué avec `--copy` ne supprime que la copie créée ;
il ne touche jamais à l'original, qui n'a jamais bougé. Sans effet sur
`clean` (les suppressions vont toujours vers la corbeille, jamais copiées).

## Rapport HTML

`janitor report` génère un rapport JSON (par défaut) ou HTML, en fonction
de l'extension de `--output` ou de `--format` explicite :

```bash
janitor report ~/Downloads -o report.html        # HTML déduit de l'extension
janitor report ~/Downloads --format html         # nom de fichier auto-généré
janitor report ~/Downloads --format html --open  # ouvre le rapport dans le navigateur
janitor report ~/Downloads --format html --max-items 100  # limite par catégorie
```

Le rapport HTML ([file_janitor/report.py](file_janitor/report.py)) est un fichier unique et autonome
(CSS inline, aucun JavaScript ni ressource externe) : un résumé visuel par
barres proportionnelles, puis une section détaillée par catégorie
(repliable, `<details>` natif). Toutes les valeurs interpolées (chemins,
raisons) sont échappées via `html.escape`, y compris les noms de fichiers
contenant des caractères spéciaux (`<`, `>`, `&`).

## Hachage parallèle

La détection de doublons hache uniquement les fichiers dont la taille est
partagée avec au moins un autre (les autres ne peuvent pas être identiques),
puis répartit ce hachage sur plusieurs threads ([file_janitor/scanner/scan.py](file_janitor/scanner/scan.py)) :
l'implémentation C de `hashlib` relâche le GIL pendant le calcul sur des
blocs de 1 Mo, donc ces threads exploitent réellement plusieurs cœurs CPU
(gain mesuré ×4 sur une machine à 8 cœurs, avec des hashes strictement
identiques au mode séquentiel).

```bash
janitor duplicates ~/Downloads                  # parallélisme automatique (cœurs CPU + marge)
janitor duplicates ~/Downloads --hash-workers 1  # séquentiel (déterministe, utile pour déboguer)
janitor duplicates ~/Downloads --hash-workers 16 # forcer un nombre de threads
```

Disponible sur `analyze`, `duplicates`, `clean` et `report`.

## Exclusions de scan

Trois façons de combiner les exclusions (syntaxe gitignore) :

```bash
janitor analyze ~/Downloads -e "*.tmp" -e "node_modules/"   # --exclude, répétable
```

```
# ~/Downloads/.janitorignore (détecté automatiquement)
*.iso
cache/
!important.iso   # négation : ré-inclut malgré la règle précédente
```

Les exclusions par défaut couvrent l’état interne de File Janitor ainsi que les répertoires techniques/régénérables courants, notamment `.git/`, `.venv/`, `venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.idea/`, `.vscode/`, `.vs/`, `build/`, `dist/` et `*.egg-info/`. Elles réduisent le bruit de classement et les collisions artificielles dans les sauvegardes de projets.

Vous pouvez désactiver ces exclusions avec `--no-default-excludes`. Le fichier `.janitorignore` peut être désactivé avec `--no-ignore-file`. Ces options sont disponibles sur toutes les commandes qui scannent (`analyze`, `duplicates`, `large`, `sort`, `clean`, `report`).

## Développement

Installez la GUI et les dépendances de développement, puis lancez la suite de tests :

```bash
pip install -e ".[gui,dev]"
python -m pytest -q
```

Le projet utilise `pyproject.toml` pour le packaging. Les artefacts de distribution peuvent être construits avec :

```bash
python -m build
python -m twine check dist/*
```

## Licence

File Janitor est distribué sous licence **MIT**. Voir [`LICENSE`](LICENSE).
