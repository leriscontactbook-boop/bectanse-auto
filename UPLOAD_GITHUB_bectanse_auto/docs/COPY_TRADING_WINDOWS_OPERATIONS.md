# Copy Trading — installation Windows, supervision et retour arrière

## Portée exacte du paquet

L'archive `bectanse-copy-trading-release-*.zip` contient le worker, les
fichiers de support Python nécessaires, les scripts PowerShell, un lock
CPython 3.14.10 / Windows x64 avec hashes SHA-256, le diagnostic terminal et
ce runbook. Elle ne contient ni identifiants, ni compte MT5, ni terminal MT5,
ni ordre de test. Le constructeur n'inclut que des chemins allowlistés et
refuse une source non committée pour un paquet distribuable.

L'installation déploie un runtime versionné, mais ne configure pas les
consentements et ne démarre ni terminal ni worker. La configuration initiale
fixe le worker en `simulation`, exécution désactivée. Tout essai d'exécution
sur compte démo nécessite une préproduction, un compte démo, les consentements
requis et une activation opérateur distincte. La procédure de ce paquet refuse
le démarrage d'une exécution live.

## Construire et vérifier l'archive

Depuis un commit relu et propre sur `codex/copytrading-finalization` :

```sh
python3 tools/build_copy_trading_release.py
shasum -a 256 dist/bectanse-copy-trading-release.zip
```

Le fichier `dist/bectanse-copy-trading-release.zip.sha256` porte le même hash.
Conserver les deux fichiers avec le commit source annoncé dans le manifeste
embarqué. `--allow-dirty-source` est réservé à l'inspection locale; une archive
ainsi construite ne doit pas être diffusée. Le ZIP ne contient pas `.git`,
`.env`, les journaux, les données client ou les comptes.

Vérification sur Windows avant extraction :

```powershell
$expected = (Get-Content .\bectanse-copy-trading-release.zip.sha256).Split(' ')[0]
$actual = (Get-FileHash .\bectanse-copy-trading-release.zip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'Checksum du paquet invalide' }
$stageRoot = Join-Path 'C:\Bectanse\staging' ('copy-trading-' + [guid]::NewGuid().ToString('N'))
Expand-Archive .\bectanse-copy-trading-release.zip $stageRoot
$packageRoot = Get-ChildItem -LiteralPath $stageRoot -Directory | Select-Object -First 1
if (-not $packageRoot) { throw 'Dossier du paquet absent' }
Get-ChildItem -LiteralPath $packageRoot.FullName -Recurse -Filter *.ps1 | Unblock-File
Set-Location $packageRoot.FullName
```

Le script d'installation revérifie ensuite chaque fichier contre
`COPY_TRADING_RELEASE_MANIFEST.json` avant de les écrire.

## Installation neuve

Prérequis : Windows Server 2022 x64, PowerShell 5.1, droits locaux pour
l'installation dans `C:\Bectanse`, HTTPS sortant vers Railway/PyPI/MetaQuotes,
connectivité sortante au broker et un compte Windows interactif dédié. Le
compte worker doit pouvoir ouvrir sa session interactive après redémarrage;
ne pas automatiser cette ouverture avec un mot de passe stocké.

Depuis le répertoire racine extrait, ouvrir PowerShell en administrateur :

```powershell
$releaseRoot = .\scripts\windows\install-copy-trading-worker.ps1 `
  -InstallRoot 'C:\Bectanse\CopyTrading'
```

Si Python 3.14.10 x64 n'existe pas à l'emplacement par défaut, le script
récupère le programme officiel Python, compare son SHA-256 attendu et valide
sa signature Authenticode avant l'installation système dans
`C:\Program Files\Python314`, afin que le compte worker dédié puisse le lire.
Le runtime et chaque wheel sont versionnés/hashés; aucun `pip install --upgrade`
n'est exécuté.
L'installation compile les modules et vérifie leurs imports, puis affiche le
chemin versionné. Elle ne démarre rien.

Créer des terminaux portables dédiés; ne pas réutiliser les dossiers de
sessions personnelles ou de production :

```powershell
& "$releaseRoot\scripts\windows\install-mt5-terminals.ps1" `
  -Count 2 -TerminalRoot 'C:\Bectanse\CopyTradingTerminals'
```

Le premier terminal sera utilisé comme maître et le second comme suiveur dans
l'exemple. Le script récupère l'installateur générique depuis l'URL officielle
`download.mql5.com`, contrôle l'hôte et le chemin HTTPS, Authenticode et le nom
du signataire, vérifie aussi la signature de `terminal64.exe`, installe
seulement dans les nouveaux dossiers isolés et garde la preuve locale des
hashes et versions. Il refuse de modifier un dossier préexistant non marqué.
Le script ne lance, ne ferme et ne connecte lui-même aucun terminal. Le
comportement de lancement éventuel de l'installateur MetaQuotes n'est pas
validé ici; toute fenêtre ou tout processus qui apparaît pendant son exécution
doit être inspecté avant de poursuivre. Les répertoires créés sont isolés et
ne contiennent aucun identifiant MT5. Les installateurs téléchargés sont
conservés sous `C:\Bectanse\CopyTradingTerminals\installers\` pour l'audit.

Saisir les secrets worker en `SecureString`; ne les inscrire ni dans une
commande, ni dans un ticket, ni dans Git :

```powershell
$masterSecret = Read-Host 'Secret worker maître' -AsSecureString
$followerSecret = Read-Host 'Secret worker suiveur' -AsSecureString
& "$releaseRoot\scripts\windows\copy-trading-configure.ps1" `
  -BackendUrl 'https://<backend-preproduction>' `
  -MasterMemberCode '<code-maitre-valide>' `
  -ReleaseRoot $releaseRoot `
  -MasterTerminalPath 'C:\Bectanse\CopyTradingTerminals\NODE-01\terminal64.exe' `
  -FollowerTerminalPaths @('C:\Bectanse\CopyTradingTerminals\NODE-02\terminal64.exe') `
  -MasterWorkerSecret $masterSecret -FollowerWorkerSecret $followerSecret `
  -InstallRoot 'C:\Bectanse\CopyTrading'
$masterSecret = $null
$followerSecret = $null
```

Cette étape protège `config\.worker.env` par ACL restreintes avant d'y écrire,
limite les ACL des terminaux aux installations marquées dédiées et laisse le
mode simulation/exécution désactivée. Le fichier local contient uniquement
les secrets worker et les chemins configurés; aucun mot de passe MT5 n'y est
stocké. Lors d'une reconfiguration, le secret précédent est conservé dans une
copie horodatée UTC `.backup` sous les mêmes ACL restreintes. Le worker récupère
les identifiants MT5 depuis l'API signée et ne les place pas en arguments de
processus ni dans les journaux.

Créer la tâche planifiée après avoir confirmé la configuration :

```powershell
& "$releaseRoot\scripts\windows\copy-trading-provision-task.ps1" `
  -ReleaseRoot $releaseRoot -InstallRoot 'C:\Bectanse\CopyTrading'
```

La tâche utilise un principal interactif limité, démarre à la connexion de
l'utilisateur worker, redémarre après échec et ignore les instances multiples.
L'enregistrement de la tâche ne lance pas le worker. La première ouverture de
session Windows le déclenchera. Aucune tâche `SYSTEM`, aucun port entrant et
aucun ordre de test réel ne sont nécessaires.

## Supervision et preuve

```powershell
& "$releaseRoot\scripts\windows\copy-trading-health.ps1" `
  -InstallRoot 'C:\Bectanse\CopyTrading'
```

Le contrôle local compare l'action de la tâche, le manifeste de release,
l'exécutable Python du processus, son PID/session interactive, le marqueur de
démarrage de cette release, un heartbeat API récent et `/health` (application
et PostgreSQL). Il ne prétend pas vérifier l'identité MT5 ni le résultat d'un
ordre. Pour confirmer l'IPC réellement exécuté, consulter aussi dans le
backend l'état terminal par membre (PID, session Windows, chemin terminal
natif, compte/serveur masqués et diagnostic) et les opérations/job audit.
`TERMINAL_CONNECTION_FAILED (-10005)` demeure un échec tant qu'une preuve
native plus récente et réussie n'est pas reçue.

Pour tester une connexion de manière strictement non transactionnelle, utiliser
uniquement un répertoire terminal marqué et dédié. Le test ne place aucun
ordre, exige une identité de compte démo, vérifie `terminal_info().path`,
l'état connecté et `account_info()`, puis appelle `shutdown()`. Les identifiants
facultatifs sont demandés interactivement; le mot de passe est masqué et ne
figure ni dans la ligne de commande, ni dans un fichier de résultat :

```powershell
& "$releaseRoot\scripts\windows\test-terminal-interactive.ps1" `
  -InstallRoot 'C:\Bectanse\CopyTrading' -ReleaseRoot $releaseRoot `
  -TerminalPath 'C:\Bectanse\CopyTradingTerminals\NODE-02\terminal64.exe' `
  -PromptDemoCredentials
```

Ce test peut changer le compte connecté dans **ce terminal dédié** lorsqu'un
login démo est fourni; ne jamais le pointer vers un terminal personnel ou
portant un compte réel. Sans login il vérifie le compte déjà ouvert et échoue
si son mode natif n'est pas démo.

Pour les jobs, vérifications de consentement et preuves MT5 faisant autorité,
utiliser la page d'opérations admin/API du backend. Le statut du système ne
doit jamais être forcé à `READY`.

## Mise à niveau et retour arrière

Chaque version reste séparée dans `C:\Bectanse\CopyTrading\releases\`.
L'installation d'une nouvelle release n'écrase ni l'ancienne release, ni la
configuration, ni les journaux. Avant de réaffecter la tâche, vérifier dans
l'API admin qu'aucun job n'est actif, qu'aucun envoi/handoff n'est en cours et
qu'aucune opération ne nécessite de rapprochement. Ensuite seulement, arrêter
la tâche explicitement :

```powershell
& "$releaseRoot\scripts\windows\copy-trading-stop.ps1" `
  -ConfirmedNoActiveCopyJobs
```

Le script refuse de forcer la fin d'un processus worker résiduel. Installer la
nouvelle archive dans une nouvelle version puis reprovisionner la tâche :

```powershell
& "$newRelease\scripts\windows\copy-trading-provision-task.ps1" `
  -ReleaseRoot $newRelease -InstallRoot 'C:\Bectanse\CopyTrading' `
  -ConfirmedNoActiveCopyJobs
```

Pour revenir à l'ancienne version, conserver les mêmes préconditions et
réaffecter la tâche sans démarrer le worker :

```powershell
& "$previousRelease\scripts\windows\copy-trading-rollback.ps1" `
  -TargetReleaseRoot $previousRelease `
  -InstallRoot 'C:\Bectanse\CopyTrading' `
  -ConfirmedNoActiveCopyJobs
```

Le retour arrière conserve toutes les releases, les secrets et les logs. Il ne
redémarre aucun terminal ni worker; après vérification API/configuration, le
prochain logon interactif lance la version sélectionnée. Conserver le hash ZIP
et le commit source du paquet qui a été réellement installé.

La procédure MT5 contrôle et journalise l'installateur officiel reçu, mais
MetaQuotes publie un installateur évolutif : elle n'épinglera pas le même
binaire terminal pour de futures installations. Le hash, la signature et la
version de chaque installation doivent donc être conservés avec le dossier
VPS et testés pour chaque build utilisé.

## Cas de panne et escalade

- Tâche/processus en session 0, heartbeat ancien ou mismatch de release : ne
  pas interpréter comme terminal MT5 connecté; examiner la session Windows et
  le log `copy-trading-worker.log`.
- `TERMINAL_CONNECTION_FAILED (-10005)` : le retry ne vaut pas preuve. Vérifier
  le processus/session que le backend a reçus, le vrai chemin terminal et la
  prochaine lecture native; garder l'état en échec jusque-là.
- `OPERATIONAL_RELEASE_REQUIRED` : la barrière de sécurité reste active; ne pas
  éditer manuellement les flags pour obtenir un statut positif.
- Handoff commencé ou résultat incertain après interruption : laisser le job
  en vérification/rapprochement et confirmer avec l'état MT5 natif. Ne jamais
  relancer l'envoi uniquement parce qu'un processus a redémarré.
- Erreur PowerShell, réseau, signature ou permissions : classer comme erreur
  d'installation/outillage jusqu'à preuve contraire; ce n'est pas en soi un
  code d'erreur MetaTrader.

Ne pas supprimer d'anciennes releases, de journaux, de comptes ou de positions
comme étape de dépannage.
