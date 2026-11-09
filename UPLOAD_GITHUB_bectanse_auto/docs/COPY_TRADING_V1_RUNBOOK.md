# Copy Trading V1 — préparation et acceptation

## État de livraison

Cette branche ajoute une API/outbox Copy Trading distincte du worker de
journalisation MT5. Elle est **préparée pour revue et déploiement**, mais ne
constitue pas une remise en service vérifiée : aucun worker Windows, terminal,
compte démo ajouté ou parcours MT5 de bout en bout n'a été connecté ici. Le
backend conserve l'exécution désactivée par défaut et le mode `simulation`.
Ne pas présenter `READY` comme un simple état du site ou de Railway.

Le worker de journalisation `trading_journal.worker` reste séparé. Il importe
les transactions et positions pour le journal et ne doit pas être utilisé
comme exécuteur d'ordres du copier.

## Architecture cible

- **Railway** : application Flask, API signée, PostgreSQL, outbox, consentements,
  liens de positions, preuves de mapping, audit et récupération au démarrage.
- **VPS Windows** : un processus superviseur dédié `python -m copy_trading`, un
  terminal MT5 maître en lecture seule et un terminal indépendant par suiveur.
  Chaque session IPC MT5 est isolée dans un processus enfant. Le worker utilise
  HTTPS sortant ; aucun port entrant n'est nécessaire.
- **Reprise** : une tâche qui a commencé le handoff reste `VERIFY_REQUIRED`
  après une panne ou un résultat ambigu. Elle n'est jamais renvoyée aveuglément.
- **Fraîcheur MT5** : l'expiration absolue de 120 secondes part du
  `time_update_msc` natif de la position ou du deal de clôture, jamais de
  l'heure à laquelle un worker redémarré a redécouvert l'objet. Une position
  ouverte déjà ancienne devient une référence de base sans ouverture tardive.
  Une clôture confirmée par un deal trop ancien n'est pas envoyée ; les
  suiveurs ayant un lien ouvert passent en rapprochement requis jusqu'à une
  vérification opérateur. Le statut membre et l'API admin des opérations
  exposent le verrou. Cette V1 ne le lève pas automatiquement : le terminal
  suiveur doit être rapproché par un opérateur avant toute reprise.

## Configuration Railway

La migration est additive et relançable via `ensure_copy_schema` au démarrage
de l'application. Avant de déployer cette branche sur un environnement de
préproduction, configurer les variables suivantes dans son gestionnaire de
secrets :

| Variable | Valeur initiale |
| --- | --- |
| `COPY_TRADING_MODE` | `simulation` |
| `COPY_TRADING_EXECUTION_ENABLED` | `false` |
| `COPY_TRADING_LIVE_RELEASE_APPROVED` | `false` |
| `COPY_TRADING_CONSENT_VERSION` | vide tant que le texte de consentement n'est pas publié |
| `COPY_TRADING_MASTER_MEMBER_CODE` | code maître explicitement validé |
| `COPY_TRADING_MASTER_WORKER_SECRET` | secret aléatoire ≥ 32 caractères |
| `COPY_TRADING_FOLLOWER_WORKER_SECRET` | secret aléatoire distinct ≥ 32 caractères |
| `COPY_TRADING_CALENDAR_WORKER_SECRET` | secret aléatoire distinct ≥ 32 caractères |
| `COPY_TRADING_CALENDAR_PROVIDER` | `tradingeconomics` uniquement après souscription et configuration du collecteur |
| `COPY_TRADING_CALENDAR_FAILURE_POLICY` | `BLOCK` (défaut). `ALLOW` est un choix global explicite qui laisse les copies passer si le flux est indisponible |
| `COPY_TRADING_CALENDAR_SYNC_TTL_SECONDS` | fraîcheur maximale du flux, 900 s par défaut, bornée entre 120 et 3600 s |

Ne pas copier de secrets dans Git, une image de conteneur, un journal ou un
ticket. Ne pas réutiliser les secrets des workers du journal MT5. L'API seule
doit servir les routes internes du nouveau worker ; le service Railway ne doit
pas importer le paquet Windows `MetaTrader5`.

Le module Flask existant héberge les routes Copy Trading ; il n'y a pas de
module `copy_trading.server_host` dans cette livraison. Le service Railway
doit conserver le démarrage Flask utilisé par l'application (migration puis
`gunicorn app:app`) et ne doit pas être basculé vers un module absent. Vérifier
le manifeste du **déploiement actif**, pas seulement la configuration de
service en attente, avant tout redéploiement.

### Collecteur du calendrier

Créer un **service Railway distinct** depuis le même commit avec la commande
`python -m copy_trading.calendar_worker`. Il pousse des snapshots signés vers
`/internal/copy-trading/calendar/snapshot`; aucun accès PostgreSQL direct
n'est requis. Fournir au service le backend HTTPS, le secret calendrier
(`COPY_TRADING_CALENDAR_WORKER_SECRET`, identique au backend et distinct des
secrets maître/suiveur), `COPY_TRADING_CALENDAR_PROVIDER=tradingeconomics`
et `TRADING_ECONOMICS_API_KEY`. Le collecteur s'arrête proprement si son URL,
son secret worker ou son identifiant ne sont pas configurés. Il synchronise
toutes les 300 secondes par défaut et signale les erreurs sans journaliser la
clé ni l'URL authentifiée. Le fournisseur renvoie les dates en UTC; le moteur
compare des instants UTC et des durées écoulées, ce qui évite les erreurs de
changement d'heure. Pour une heure estimée (`DateSpan=1`), la journée UTC
entière est bloquée de façon conservatrice.

L'accès à l'API Trading Economics requiert une souscription et une clé fournie
par son gestionnaire de secrets; la clé n'est pas incluse dans le dépôt ou le
package Windows. Le snapshot est remplacé en une transaction PostgreSQL et
une tentative ratée ne marque jamais les anciennes données comme fraîches.
Avec la politique `BLOCK`, l'absence de source, un échec de synchronisation,
une couverture expirée ou un paramètre invalide bloque la nouvelle ouverture
ou modification; les clôtures et annulations de sécurité restent permises.
La politique `ALLOW` n'est activée que par configuration serveur explicite
et le statut la signale comme `CALENDAR_UNAVAILABLE_FAIL_OPEN`.

## Préparer le VPS Windows

Le paquet et les limites opérationnelles (installation, configuration,
supervision et retour arrière) sont décrits dans
[`COPY_TRADING_WINDOWS_OPERATIONS.md`](COPY_TRADING_WINDOWS_OPERATIONS.md).
Ce runbook reste la référence pour les comportements du moteur et les critères
de libération.

Le paquet Windows est généré depuis un commit propre avec un manifeste de
hashes par fichier, une archive ZIP déterministe et un SHA-256 externe. Il
installe Python x64 `3.14.10` après vérification du hash et de la signature,
puis installe les wheels exclusivement depuis le lock hashé. Chaque version
est immuable sous `C:\Bectanse\CopyTrading\releases\`; secrets et journaux
restent dans des répertoires persistants séparés. Le worker n'est pas démarré
par l'installation.

Le script MT5 crée des répertoires portables distincts sous
`C:\Bectanse\CopyTradingTerminals`. Il refuse d'adopter un dossier existant
sans marqueur géré par Bectanse et ne termine aucun processus MT5. L'installateur
MetaQuotes évolue indépendamment du paquet : sa signature, son hash et la
version du binaire sont enregistrés localement pour la reproductibilité de
l'installation effectivement réalisée.

Après configuration, le worker planifié s'exécute uniquement dans la session
interactive de l'utilisateur prévu. Nous ne configurons pas l'ouverture de
session automatique Windows ni un stockage de mot de passe de connexion. Après
redémarrage du VPS, il faut confirmer que cette session revient, puis vérifier
le PID/session, la release chargée, le heartbeat backend et la dernière preuve
native MT5. Un processus Python lancé ou `/health` en HTTP 200 ne prouve pas un
parcours Copy Trading MT5.

Après une tentative de connexion native, le backend conserve par membre l'état
`CONNECTED`/`FAILED`, le code diagnostic MT5 (dont `-10005`), le PID et la
session Windows du processus Python qui a ouvert l'IPC, ainsi qu'une empreinte
HMAC du couple compte/serveur. Aucun identifiant de connexion ni mot de passe
n'est ajouté à ce journal. Un échec récent reste visible et bloque le statut
opérationnel jusqu'à une connexion native réussie. La preuve est une dernière
connexion lue dans les 24 heures, pas une promesse que le terminal reste
attaché à l'API Python entre deux événements.

## Limites à lever avant la démo

1. **Protections Académie** : drawdown equity depuis le plus haut observé,
   plafond de gain equity, et objectifs/pertes sur balance réalisée par période
   UTC ont des seuils persistés et un moniteur MT5 par suiveur. Un déclenchement
   suspend les nouvelles copies; les clôtures des positions liées sont mises
   en file idempotente et confirmées par relecture native. La reprise est
   explicite et attend la réconciliation. Les protections ne ferment jamais
   une position sur un relevé périmé ou incomplet.
2. **Ordres en attente** : création, modification, annulation et expiration
   sont reliées au ticket maître, envoyées avec un handoff atomique et
   confirmées par un snapshot d'ordres MT5 complet. Deux snapshots maître
   absents plus un état d'historique terminal permettent de fermer le lien;
   côté suiveur, un ticket absent n'est considéré terminé qu'après lecture de
   son historique MT5; un ticket rempli est mis en vérification, jamais déclaré
   annulé. Une protection de capital met également en file l'annulation des
   ordres liés après comparaison exacte du snapshot natif. Une preuve
   incomplète bloque pour revue. La conversion en ordre au marché reste
   volontairement non prise en charge et est refusée.
3. **Calendrier** : le filtre n'est consulté que lorsqu'il est activé.
   Devises (liste vide = toutes), importance minimale et marges avant/après
   sont réglables par compte. Les paramètres temporels sont des minutes réelles
   autour d'instants UTC; le champ IANA sert à l'usage local et les décisions
   restent indépendantes du DST. L'activation est refusée tant qu'un snapshot
   valide n'a pas été synchronisé, sauf si un opérateur a explicitement choisi
   `ALLOW` au niveau serveur. Quand le filtre est OFF, aucune requête calendrier
   ne bloque l'exécution.
4. **Volume maître augmenté** : le scale-in est mis en revue et bloque le
   suiveur jusqu'à l'ajout d'un registre de composants natifs. Il n'est pas
   considéré comme synchronisé.
4. **Positions déjà ouvertes** : l'abonnement d'un nouveau suiveur ne crée pas
   automatiquement de copie historique des positions maître. Seuls les
   événements observés après son inscription sont candidats.
5. Les alias de symboles restent en attente tant qu'une revue humaine ne les a
   pas confirmés avec preuves natives des deux terminaux.
6. Un verrou de rapprochement créé par une clôture source périmée n'a pas de
   route de déblocage automatique. Il nécessite une revue opérateur des
   positions natives suiveur ; aucune clôture broker n'est simulée.
7. Chaque suiveur en mode live doit avoir sa propre preuve Copy Trading démo
   complète et récente (24 h) ; le backend filtre les jobs sans cette preuve,
   même si un worker live est démarré.

## Acceptation préproduction — compte démo uniquement

Effectuer ces étapes seulement après que le VPS, l'API préproduction, un compte
maître autorisé, un compte suiveur démo et le consentement de test soient
configurés. Les essais suivants créent des ordres **sur le compte démo** ; ne
pas les remplacer par un compte réel.

1. Vérifier que les deux workers ont un heartbeat frais, avec l'identité native
   attendue, et que le membre indique les consentements et accès requis.
2. Ouvrir une position démo maître autorisée et vérifier l'événement outbox,
   l'unique job par membre, le mapping à partir des preuves MT5 et l'ordre démo.
3. Répéter le même snapshot maître : vérifier qu'aucun second ordre n'est créé.
4. Fermer la position maître ; après confirmation sur deux snapshots complets
   et lecture du deal natif MT5, vérifier la fermeture de la position liée sur
   le terminal démo. Un deal de clôture périmé (120 secondes) bloque le
   rapprochement au lieu de générer un ordre tardif.
5. Changer le profil Académie en préproduction et vérifier le volume/les
   protections correspondants sur une nouvelle opération démo.
6. Redémarrer le worker après une ouverture démo. Une tâche interrompue avant
   l'envoi peut expirer ; une tâche interrompue après le handoff doit être
   quarantinée et réconciliée, jamais répétée automatiquement.
7. Tester l'expiration absolue à 120 s depuis les timestamps MT5 natifs, la
   mise en rapprochement d'une clôture ancienne, le refus du filtre calendrier
   ON quand ses données sont périmées, et l'absence de blocage calendrier OFF.
8. Vérifier les événements d'audit API/DB et le statut membre. Chaque résultat
   doit refléter une lecture native après action, pas seulement le retcode MT5.

Ne pas ouvrir un compte réel pour un test de déploiement. Un déploiement Railway
ou un processus Windows lancé ne vaut pas preuve du parcours ci-dessus.

## Conditions de libération

La mise en production est bloquée jusqu'à : migration revue sur une base de
préproduction, tests du dépôt, validation des limites ci-dessus, compte démo
ajouté parcourant connexion/ouverture/fermeture/reprise, confirmation des
sessions IPC réelles après redémarrage, accord explicite sur le texte de
consentement et vérification du worker réellement exécuté. La libération live
demande en plus une autorisation séparée ; les paramètres de production ne
doivent pas être forcés pour afficher un état prêt.
