# Engine Safety Roadmap

## Objet

Ce document conserve l’historique du chantier de durcissement du moteur
filesystem réalisé sur la branche `v0.2-engine-safety`, puis intégré au projet.

Il doit être lu comme un snapshot historique des garanties, checkpoints et
limites identifiés pendant cette phase. L’état courant de l’architecture est
documenté dans `docs/ARCHITECTURE.md`, et le workflow de release dans
`docs/RELEASE.md`.

La nomenclature des phases utilisée ici formalise le travail réalisé sur cette
branche. Elle n'était pas précédemment documentée dans le `README.md`.

Objectifs principaux :

- rendre l'historique d'exécution transactionnel ;
- rendre l'undo transactionnel ;
- empêcher les écrasements accidentels lors des collisions ;
- valider les chemins utilisés pour les opérations filesystem ;
- détecter les modifications intervenues entre le scan et l'exécution ;
- réduire les fenêtres TOCTOU ;
- rendre COPY, MOVE et TRASH transactionnels ;
- conserver des garanties cohérentes pendant l'undo ;
- échouer de manière sûre lorsqu'une identité filesystem ne peut plus être
  vérifiée.

## Snapshot historique de la phase v0.2

Branche de travail d’origine :

    v0.2-engine-safety

Checkpoint de référence de cette phase :

    b509b3f fix: fail closed when destination identity cannot be verified

Baseline de tests à ce checkpoint :

    205 passed

Le working tree était propre lors de l'établissement de cette baseline.

Ces valeurs décrivent le chantier v0.2 au moment de sa clôture. Elles ne
représentent pas la baseline actuelle de `master`.

---

## Travaux réalisés

### Historique d'exécution transactionnel

Commit :

    73cbecf feat: make execution history transactional

Le moteur conserve explicitement l'état des opérations exécutées afin de
pouvoir distinguer les opérations planifiées, terminées et échouées.

### Undo transactionnel

Commit :

    628783e feat: make undo operations transactional

L'undo possède son propre cycle d'état et les échecs d'annulation restent
visibles dans l'historique.

### Protection contre les collisions

Commit :

    dc7d6b9 feat: protect file operations from collisions

Les opérations filesystem ne doivent pas écraser silencieusement une
destination existante.

### Validation des chemins filesystem

Commit :

    01ec452 feat: validate filesystem paths before execution

Les chemins source, destination et chemins de restauration sont validés avant
les mutations filesystem.

### Capture de l'identité pendant le scan

Commit :

    b825ed2 feat(safety): capture file identity during scan

Une identité filesystem est associée aux fichiers observés pendant le scan.

L'identité repose sur :

- device ;
- inode ;
- taille ;
- mtime en nanosecondes.

### Vérification de l'identité avant exécution

Commit :

    7bae1de feat(safety): verify file identity before execution

Avant une opération destructive ou une lecture utilisée pour une mutation, le
moteur vérifie que la source correspond toujours au fichier observé pendant le
scan.

### Durcissement TOCTOU

Commit :

    d9c4e11 feat: harden execution against TOCTOU races

Les opérations sensibles effectuent des revalidations supplémentaires au plus
près de l'I/O.

Pour COPY, la validation peut porter sur le descripteur réellement ouvert via
`fstat`, plutôt que seulement sur une nouvelle résolution du chemin.

---

## Phase 2G — opérations filesystem transactionnelles

La phase 2G a progressivement renforcé COPY, MOVE, TRASH et leurs opérations
d'undo.

### 2G-A — publication COPY transactionnelle

Commit :

    9c696ae feat: make copy publication transactional

COPY écrit d'abord dans un fichier temporaire privé situé dans le répertoire
de destination.

Le nom final n'est publié qu'après une copie complète.

### 2G-B — publication COPY no-clobber

Commit :

    9515aa1 feat: publish copies without overwriting concurrent destinations

La publication finale de COPY ne remplace pas une destination créée
concurremment.

La publication utilise une primitive atomique no-clobber.

### 2G-C — durabilité COPY

Commit :

    2cea3ce feat: make copy publication crash-durable

Les données du temporaire sont synchronisées avant publication et les
mutations de noms sont suivies d'une synchronisation du répertoire lorsque
cela est applicable.

### 2G-D — MOVE same-filesystem atomique

Commit :

    daf3b12 feat: make same-filesystem moves atomic and no-clobber

Sous Linux, MOVE utilise `renameat2(..., RENAME_NOREPLACE)` pour obtenir une
publication atomique sans écrasement concurrent lorsque source et destination
sont sur le même filesystem.

### 2G-E — MOVE cross-filesystem transactionnel

Commit :

    b91845c feat: add transactional cross-filesystem move fallback

Lorsqu'un MOVE échoue avec `EXDEV`, le moteur utilise un fallback
transactionnel :

1. copie vers un temporaire privé ;
2. synchronisation ;
3. publication no-clobber ;
4. suppression contrôlée de la source ;
5. rollback de la destination publiée si la suppression de la source échoue.

### 2G-F — TRASH transactionnel

Commit :

    98078d8 feat: make trash operations transactional

TRASH réutilise le moteur transactionnel de MOVE tout en conservant sa
sémantique historique d'opération `delete`.

Il bénéficie donc :

- du rename atomique/no-clobber sur le même filesystem ;
- du fallback transactionnel sur `EXDEV` ;
- des contrôles d'identité de source ;
- des mécanismes de rollback applicables.

### 2G-G — restauration MOVE/TRASH transactionnelle

Commit :

    0b27d80 feat: make undo restores transactional

L'undo des opérations MOVE et TRASH utilise à son tour :

- une restauration no-clobber sur le même filesystem ;
- un fallback transactionnel sur `EXDEV` ;
- des revalidations avant les mutations destructives ;
- un rollback lorsque la restauration ne peut pas être terminée proprement.

### 2G-H — protection de l'undo COPY

Commit :

    6e72790 feat: protect copy undo with persisted file identity
    b509b3f fix: fail closed when destination identity cannot be verified

Après publication d'une COPY, l'identité filesystem du fichier effectivement
créé est persistée dans l'historique.

Avant de supprimer cette copie pendant l'undo, le moteur vérifie que le fichier
présent correspond toujours à cette identité.

L'undo est refusé si notamment :

- la destination a été remplacée par un autre fichier ;
- son contenu ou ses métadonnées d'identité surveillées ont changé ;
- elle a été remplacée par un lien symbolique ;
- l'opération historique ne possède pas d'identité persistée permettant de
  prouver que le fichier présent est celui créé par File Janitor.

Les anciens enregistrements sans identité utilisent donc une politique
fail-closed : aucune suppression automatique n'est effectuée.

---

## Hardening post-2G — validation de destination fail-closed

Commit :

    b509b3f fix: fail closed when destination identity cannot be verified

Le gap analysis réalisé après la clôture de 2G a identifié un comportement
fail-open local dans `validate_destination_path()`.

Lorsque la source et la destination existaient toutes les deux et que
`Path.samefile()` levait `OSError`, l'erreur était auparavant ignorée et la
validation continuait.

La validation est désormais fail-closed : si le moteur ne peut pas déterminer
de façon fiable si source et destination désignent le même fichier, il lève
`PathSafetyError` et aucune mutation filesystem n'est engagée.

Un test dédié vérifie ce comportement ainsi que la préservation des deux
fichiers en cas d'échec de cette vérification.

---

## Garanties obtenues

À l'issue de 2G-H :

### COPY

- validation de la source réellement ouverte ;
- écriture dans un temporaire privé ;
- publication atomique no-clobber ;
- protection contre une destination concurrente ;
- synchronisation avant/après les mutations critiques ;
- nettoyage des temporaires après échec ;
- identité du fichier publié persistée ;
- undo protégé contre le remplacement ou la modification de la copie.

### MOVE

- validation de l'identité source ;
- publication atomique no-clobber sur le même filesystem ;
- fallback transactionnel sur `EXDEV` ;
- protection contre les collisions concurrentes ;
- rollback lorsque le fallback cross-filesystem ne peut pas terminer la
  suppression de la source.

### TRASH

- mêmes garanties filesystem que MOVE ;
- conservation de la sémantique historique `delete` ;
- undo transactionnel.

### Undo

- états explicites `UNDOING`, `UNDONE`, `UNDO_PARTIAL` et `UNDO_FAILED` ;
- état individuel des opérations annulées ;
- restauration MOVE/TRASH no-clobber ;
- fallback de restauration sur `EXDEV` ;
- protection de l'undo COPY par identité persistée ;
- refus des restaurations ou suppressions dont la sécurité ne peut pas être
  établie.

---

## Limite architecturale connue

Certaines mutations finales restent basées sur des noms de fichiers.

Les revalidations d'identité réduisent fortement les fenêtres TOCTOU, mais
elles ne constituent pas une garantie POSIX absolue équivalente à une
architecture entièrement fondée sur des descripteurs de répertoire et des
primitives telles que :

- `openat` ;
- `dir_fd` ;
- `renameat` / `renameat2` ;
- des opérations de suppression directement liées à une résolution contrôlée
  depuis un descripteur de répertoire.

Cette limite est actuellement volontaire et doit être distinguée d'un bug
ponctuel de la phase 2G.

Une éventuelle migration vers une architecture entièrement descriptor-relative
constituerait un chantier architectural distinct.

---

## Validation historique

Baseline après 2G-H :

    205 passed

Cette baseline appartient à la phase v0.2 et est conservée ici comme référence
historique. Pour l’état courant du projet, utilisez la suite de tests sur
`master` et les quality gates décrits dans `docs/RELEASE.md`.

Les suites couvrent notamment :

- historique et migrations SQLite ;
- collisions ;
- validation des chemins ;
- identité filesystem ;
- TOCTOU ;
- COPY transactionnel ;
- MOVE transactionnel ;
- TRASH transactionnel ;
- undo transactionnel ;
- identité persistée et undo COPY.

---

## Historique des checkpoints

    73cbecf feat: make execution history transactional
    628783e feat: make undo operations transactional
    dc7d6b9 feat: protect file operations from collisions
    01ec452 feat: validate filesystem paths before execution
    b825ed2 feat(safety): capture file identity during scan
    7bae1de feat(safety): verify file identity before execution
    d9c4e11 feat: harden execution against TOCTOU races
    9c696ae feat: make copy publication transactional
    9515aa1 feat: publish copies without overwriting concurrent destinations
    2cea3ce feat: make copy publication crash-durable
    daf3b12 feat: make same-filesystem moves atomic and no-clobber
    b91845c feat: add transactional cross-filesystem move fallback
    98078d8 feat: make trash operations transactional
    0b27d80 feat: make undo restores transactional
    6e72790 feat: protect copy undo with persisted file identity

---

## Après la phase 2G

Le gap analysis mentionné pendant le chantier a depuis été réalisé, et les
travaux de safety correspondants ont été intégrés au projet.

Ce document n’est donc plus une roadmap active. Les évolutions futures doivent
être pilotées depuis l’architecture et les besoins courants du projet, sans
réinterpréter les checkpoints v0.2 comme un plan de travail encore ouvert.

Lorsqu’un nouveau chantier de hardening est lancé, documentez explicitement :

- son objectif ;
- la menace ou le défaut traité ;
- les invariants recherchés ;
- les tests d'acceptation ;
- le ou les commits correspondants.

Les limites architecturales encore pertinentes restent décrites dans la
section précédente et doivent être réévaluées lorsqu’une feature modifie les
mutations filesystem ou l’undo.
