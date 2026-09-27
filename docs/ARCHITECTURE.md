# Architecture & development guide

Ce document décrit l’architecture interne de File Janitor et les règles à respecter lors d’une feature, d’un refactor ou d’un bugfix.

Il complète :

- `README.md`, orienté utilisateur ;
- `docs/RELEASE.md`, orienté build et release.

Baseline de version : File Janitor `0.4.0` sur `master`.

## 1. Vue d’ensemble

File Janitor sépare les interfaces utilisateur du moteur métier par une couche application explicite.

```text
┌──────────────────────────────────────┐
│ Frontends                            │
│                                      │
│  CLI (Typer/Rich)     GUI (PySide6) │
└───────────────┬──────────────────────┘
                │
                ▼
┌──────────────────────────────────────┐
│ file_janitor.application             │
│                                      │
│ use cases · orchestration · DTO      │
│ API publique des frontends           │
└───────────────┬──────────────────────┘
                │
                ▼
┌──────────────────────────────────────┐
│ Moteur                               │
│                                      │
│ scanner · plan · executor · report   │
│ storage · scheduler · path_safety    │
└──────────────────────────────────────┘
```

La règle structurante est :

```text
frontend → application → moteur
```

Un frontend ne doit pas contourner la couche application pour appeler directement les internals du moteur.

## 2. Pipeline métier

Le pipeline conceptuel du moteur est :

```text
scan → plan → preview → execute → undo
```

### scan

`file_janitor/scanner/` parcourt le filesystem et construit un `ScanResult`.

Le scan collecte notamment les `FileRecord`, les tailles, dates, extensions, hashes lorsque demandés, informations de type de contenu et `FileIdentity`.

Cette étape est read-only.

### plan

`file_janitor/plan/` transforme le résultat du scan en `Plan` contenant des `ActionItem`.

Le planner décrit des intentions : catégorie, source, destination éventuelle, action et conflict policy. Construire un plan ne modifie pas le filesystem.

Les responsabilités sont réparties entre :

- `builder.py` : construction du plan et `PlanConfig` ;
- `grouping.py` : stratégies de grouping ;
- `rules.py` : règles configurables ;
- `templates.py` : templates de destination.

### preview

La preview n’est pas un module moteur distinct : c’est une représentation contrôlée du plan destinée aux frontends.

La couche application transforme les structures moteur en DTO read-only tels que :

- `AnalysisSummary` ;
- `CategorySummary` ;
- `AnalysisItem` ;
- `CategoryDetails` ;
- `AnalysisResult` ;
- `ClassificationGroupSummary`.

Les frontends doivent préférer ces objets aux structures internes `Plan` / `ActionItem`.

### execute

`file_janitor/executor.py` applique les actions validées. Il gère notamment les opérations `MOVE`, `COPY` et `TRASH`, les conflict policies, les contrôles d’identité filesystem, les cas cross-filesystem, le journal d’opérations et les rollbacks nécessaires.

L’exécuteur ne doit être appelé par un frontend qu’à travers la couche application.

### undo

L’undo s’appuie sur l’historique enregistré pendant l’exécution. `undo_batch()` restaure les opérations supportées et nettoie les destinations devenues vides lorsque cela est safe.

La GUI et la CLI utilisent `undo_execution()` plutôt que `undo_batch()` directement.

## 3. Couche application

`file_janitor/application/` est la façade stable entre les frontends et le moteur.

Son API publique est réexportée par :

```text
file_janitor/application/__init__.py
```

Le service orchestre les modules internes et expose des use cases adaptés aux interfaces, notamment :

```text
analyze_folder()
execute_selected_actions()
undo_execution()

get_history_summary()
get_history_operation_summary()

classification_plan_config()
summarize_classification_groups()

preview_schedule()
install_schedule()
list_schedules()
remove_schedule()
```

La couche application a trois responsabilités principales.

### Orchestration

Elle compose scanner, planner, executor, history, report et scheduler sans demander au frontend de connaître leurs signatures internes.

### Traduction

Elle traduit les types moteur vers des DTO adaptés aux interfaces. Par exemple, l’historique est exposé sous forme de `HistoryBatchSummary` et `HistoryOperationSummary` plutôt que de laisser la GUI manipuler directement `HistoryStore`, `Batch` ou `Operation`.

### Validation de use case

Les règles qui appartiennent au use case, et non au widget ou au storage adapter, doivent vivre ici. Par exemple, la validation des actions planifiables `sort` / `clean` est effectuée dans la couche application.

## 4. Frontière d’architecture

Les modules suivants sont considérés comme des internals moteur vis-à-vis de la GUI :

```text
file_janitor.executor
file_janitor.models
file_janitor.path_safety
file_janitor.plan
file_janitor.report
file_janitor.scanner
file_janitor.scheduler
file_janitor.storage
```

`file_janitor/gui/` ne doit pas les importer directement.

Le guard de test correspondant est `tests/test_gui_application_boundary.py`.

Un contrôle rapide peut aussi être lancé manuellement :

```bash
grep -RInE   'file_janitor\.(executor|models|path_safety|plan|report|scanner|scheduler|storage)'   file_janitor/gui   --include='*.py'   --exclude-dir='__pycache__'   || echo "Aucun import moteur interdit détecté"
```

La CLI suit le même principe : elle consomme les use cases et DTO de la couche application et ne doit pas réintroduire une dépendance aux internals du planner. Les guards correspondants vivent dans `tests/test_cli_application_boundary.py`.

### Exception : types réexportés

Certains types de configuration du moteur sont volontairement réexportés par `file_janitor.application`, par exemple `PlanConfig`, `GroupBy`, `DateGranularity`, `Template`, `RuleError` ou `ScheduleError`.

Pour un frontend, l’import doit rester :

```python
from file_janitor.application import PlanConfig
```

et non :

```python
from file_janitor.plan.builder import PlanConfig
```

La réexportation appartient à la frontière publique ; le chemin du module moteur reste un détail d’implémentation.

## 5. GUI

La GUI se trouve dans `file_janitor/gui/`.

### `app.py`

Responsable du bootstrap Qt et de la création de l’application.

### `main_window.py`

`MainWindow` orchestre l’état de présentation et le workflow utilisateur :

```text
source / destination / mode
            ↓
         analyse
            ↓
         preview
            ↓
    sélection d’actions
            ↓
        exécution
            ↓
        historique
            ↓
           undo
```

`MainWindow` peut décider :

- quel contrôle est enabled/disabled ;
- quel tab est affiché ;
- quel feedback utilisateur est présenté ;
- quand une preview devient stale ;
- quand lancer un worker ;
- comment traduire un DTO applicatif en widgets.

Il ne doit pas décider comment scanner un dossier, résoudre un move, écrire l’historique ou restaurer un fichier.

### État d’analyse

La validité de l’analyse dépend du contexte métier :

```text
(source, destination effective, mode de classement, profil d’analyse)
```

La **destination effective** vaut :

```text
destination choisie, si elle existe
sinon source
```

La destination fait donc partie de la configuration du plan dès l’analyse ; elle n’est pas appliquée a posteriori à une preview déjà construite.

Après une analyse réussie, relancer exactement le même contexte n’est pas nécessaire. Modifier la source, la destination effective, le mode ou le profil d’analyse rend l’analyse précédente stale et réactive l’action d’analyse. Revenir exactement au contexte déjà analysé désactive de nouveau cette action.

Changer seulement de tab ne change pas cette validité.

Après une exécution, la preview précédente est invalidée car le filesystem a potentiellement changé.

### Profils d’analyse et fast path distant

Le profil **Standard** autorise les lectures de contenu nécessaires à l’analyse complète. Le profil **Remote / rapide** désactive les lectures coûteuses inutiles sur les stockages distants et fait partie du contexte d’analyse.

Pour un mount `fuse.rclone`, la couche application peut court-circuiter le parcours FUSE par fichier et appeler `rclone lsjson --recursive --files-only --no-mimetype`. Ce fast path fournit chemin, taille et date de modification pour les modes qui en ont besoin. Les identités fortes `device/inode` ne sont pas inventées : elles restent absentes du `ScanResult` et sont hydratées uniquement pour les actions sélectionnées juste avant l’exécution.

Les exclusions techniques par défaut peuvent être poussées vers rclone sous forme de filtres `--exclude` afin de réduire le listing en amont. Cette optimisation est conservatrice : elle est désactivée si les exclusions par défaut sont coupées ou si une règle de réinclusion pourrait rendre le pushdown incorrect. Le `PathSpec` Python reste appliqué au résultat comme source de vérité fonctionnelle.

### Destination et application boundary

Le moteur supporte une racine de destination via `PlanConfig.destination_root`. La GUI ne construit toutefois pas directement ce type moteur.

Le flow reste :

```text
GUI destination
    ↓
classification_plan_config(..., destination_root=...)
    ↓
file_janitor.application
    ↓
PlanConfig.destination_root
    ↓
planner
```

Cette règle préserve la frontière `GUI → application → moteur`.

Dans la GUI :

- un champ destination vide signifie « utiliser la source » ;
- **Choisir…** renseigne une destination externe ;
- **Utiliser la source** efface cette valeur explicite ;
- la destination fait partie du contexte d’analyse ;
- les contrôles de destination sont bloqués pendant le busy state comme les autres contrôles capables de muter le contexte.

La préférence **Restaurer la dernière destination** permet de persister une destination externe. Si la valeur sauvegardée ne pointe plus vers un folder existant, elle n’est pas restaurée et le fallback vers la source reste actif. **Utiliser la source** et la désactivation de cette préférence oublient immédiatement la destination mémorisée.

### Busy state

Pendant une opération async, les contrôles capables de muter le contexte sont bloqués. À la sortie du busy state, les boutons ne doivent pas être simplement réactivés aveuglément : leur état est recalculé à partir du business state réel.

Les tabs restent navigables pendant le busy state.

Ce comportement est couvert par `tests/test_gui_state_robustness.py`.

## 6. Workers Qt

`file_janitor/gui/workers.py` adapte une fonction Python synchrone à `QThreadPool` via `QRunnable`.

Un `Worker` publie trois signaux :

```text
result
error
finished
```

La GUI garde la responsabilité de connecter ces signaux aux handlers de présentation.

### Cancellation

`Worker.cancel()` ne tue pas le travail Python déjà en cours. Il empêche les émissions futures du worker.

Cette distinction est importante :

```text
cancel ≠ interruption forcée du job
cancel = ne plus publier de résultat vers une GUI fermée
```

Lors de la fermeture de `MainWindow`, les workers trackés sont cancelled afin d’éviter les callbacks tardifs vers des `QObject` détruits.

`Worker._emit()` tolère également le cas où le `QObject` de signaux a déjà été détruit.

Le lifecycle est couvert par `tests/test_gui_worker_lifecycle.py`.

## 7. Historique et storage

`file_janitor/storage/history.py` contient l’infrastructure de persistance de l’historique :

- `HistoryStore` ;
- `Batch` ;
- `Operation` ;
- statuts de batch et d’opération.

Les frontends ne doivent pas ouvrir eux-mêmes un `HistoryStore`.

Ils utilisent la couche application :

```text
get_history_summary()
get_history_operation_summary()
undo_execution()
```

Cela garde le choix du storage et ses détails de lifecycle hors des interfaces.

## 8. Path safety et invariants filesystem

Les opérations filesystem ne doivent jamais être réimplémentées dans la GUI ou la CLI.

Les contrôles de safety, conflict handling, publication atomique lorsque disponible, validation d’identité, comportement cross-filesystem et rollback appartiennent au moteur.

Une feature frontend qui a besoin d’une nouvelle opération doit donc suivre ce chemin :

```text
nouveau besoin UI
    ↓
use case application
    ↓
primitive/invariant moteur
    ↓
DTO résultat
    ↓
rendu UI
```

et non :

```text
widget → shutil / os / Path.rename()
```

## 9. Ajouter une feature

Pour une feature qui touche un nouveau comportement métier, utilisez cet ordre.

### 1. Identifier la couche propriétaire

Posez d’abord la question : la décision appartient-elle au moteur, au use case ou uniquement à la présentation ?

Exemples :

- nouveau grouping de fichiers → moteur `plan` ;
- exposer ce grouping aux frontends → couche application ;
- ajouter ce choix dans un combo → GUI ;
- changer le wording d’un bouton → GUI uniquement.

### 2. Écrire ou adapter le moteur

Ajoutez les primitives et tests moteur sans dépendance à Qt, Typer ou Rich.

### 3. Exposer le use case

Ajoutez à `file_janitor.application` une fonction ou un DTO lorsque le frontend aurait sinon besoin de connaître un internal.

Préférez des DTO read-only pour les résultats destinés à l’affichage.

### 4. Ajouter les tests de frontière

Si une nouvelle famille de modules devient un internal moteur, adaptez les architecture guards.

### 5. Brancher le frontend

Le frontend importe l’API depuis `file_janitor.application`.

Pour la GUI, toute opération potentiellement lente doit passer par le mécanisme de worker plutôt que de bloquer le GUI thread.

### 6. Tester le workflow

Ajoutez au minimum :

- test unitaire de la logique propriétaire ;
- test de la couche application ;
- test frontend ciblé ;
- test de workflow si la feature change une transition utilisateur importante.

## 10. Stratégie de tests

La suite est volontairement répartie par responsabilité.

### Tests moteur

Ils vérifient scanner, planner, executor, history, safety, reports et scheduling indépendamment des frontends.

### Tests application

`tests/test_application_service.py` vérifie notamment :

- orchestration scan/plan ;
- mapping des classification modes ;
- DTO read-only ;
- sélection d’actions depuis une preview ;
- execute/undo via l’API publique ;
- abstraction de l’historique ;
- reports et scheduling.

### Architecture guards

```text
tests/test_gui_application_boundary.py
tests/test_cli_application_boundary.py
```

Ils empêchent les frontends de contourner la façade application.

### Tests GUI ciblés

Les tests GUI utilisent `QT_QPA_PLATFORM=offscreen` afin de tester les widgets sans afficher une fenêtre réelle.

Les suites importantes incluent notamment :

```text
tests/test_gui_workflow.py
tests/test_gui_worker_lifecycle.py
tests/test_gui_state_robustness.py
```

`test_gui_workflow.py` couvre le cycle analyse → sélection → exécution → historique → undo sur le même `MainWindow`.

## 11. Regression gate

Avant tout commit significatif :

```bash
python3 -m compileall -q file_janitor tests
pytest -q
git diff --check
```

Puis contrôlez la frontière GUI :

```bash
grep -RInE   'file_janitor\.(executor|models|path_safety|plan|report|scanner|scheduler|storage)'   file_janitor/gui   --include='*.py'   --exclude-dir='__pycache__'   || echo "Aucun import moteur interdit détecté"
```

Pour une modification ciblée, lancez d’abord les tests concernés puis la suite complète.

Exemple pour une évolution du workflow GUI :

```bash
pytest -q tests/test_gui_workflow.py
pytest -q tests/test_gui_state_robustness.py
pytest -q tests/test_gui_worker_lifecycle.py
pytest -q
```

## 12. Règles de refactor

Un refactor doit préserver les invariants suivants :

1. scan et plan restent sans mutation filesystem ;
2. preview et exécution restent deux étapes distinctes ;
3. seules les actions explicitement retenues sont exécutées ;
4. une preview stale n’est pas réutilisée après mutation du contexte ou du filesystem ;
5. GUI et CLI passent par `file_janitor.application` pour les use cases métier ;
6. les opérations filesystem restent dans le moteur ;
7. le storage n’est pas ouvert directement par les frontends ;
8. le GUI thread ne porte pas les jobs longs ;
9. fermer la fenêtre ne doit pas laisser un worker publier vers une UI détruite ;
10. les changements de structure doivent être protégés par des tests d’architecture ou de workflow.

## 13. Où documenter un changement

Utilisez le document correspondant au public visé :

| Changement | Documentation |
|---|---|
| usage utilisateur, installation, GUI/CLI | `README.md` |
| architecture, internals, contribution technique | `docs/ARCHITECTURE.md` |
| build, validation, tag, release | `docs/RELEASE.md` |
| safety moteur détaillée / roadmap | `docs/ENGINE_SAFETY_ROADMAP.md` |

Évitez de dupliquer une procédure complète dans plusieurs fichiers. Préférez un lien vers le document propriétaire.

## 14. Definition of done technique

Une feature ou un refactor est prêt à être intégré lorsque :

- la responsabilité de chaque changement est dans la bonne couche ;
- aucune nouvelle dépendance frontend → moteur n’a été introduite ;
- les use cases nécessaires sont exposés par `file_janitor.application` ;
- les jobs longs GUI ne bloquent pas le GUI thread ;
- les tests ciblés passent ;
- la suite complète passe ;
- `git diff --check` est clean ;
- la documentation impactée est à jour ;
- le working tree final est maîtrisé avant stage/commit.

La checklist de release complète reste dans `docs/RELEASE.md`.
