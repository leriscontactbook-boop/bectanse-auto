param(
  [string]$InstallRoot = "C:\Bectanse\CopyTrading",
  [switch]$ConfirmedDemoOnlyAndNoActiveJobs
)
$ErrorActionPreference = 'Stop'
$taskName = 'BectanseCopyTradingWorker'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { throw 'La tâche Copy Trading doit être provisionnée avant le démarrage.' }
$configPath = Join-Path $InstallRoot 'config\.worker.env'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Configuration worker absente.' }
$configLines = Get-Content -LiteralPath $configPath -Encoding UTF8
$mode = (($configLines | Where-Object { $_ -match '^COPY_TRADING_MODE=' } | Select-Object -First 1) -replace '^COPY_TRADING_MODE=', '').Trim()
$execution = (($configLines | Where-Object { $_ -match '^COPY_TRADING_EXECUTION_ENABLED=' } | Select-Object -First 1) -replace '^COPY_TRADING_EXECUTION_ENABLED=', '').Trim()
if ($mode -eq 'live' -and $execution -match '^(1|true|yes|on)$') {
  throw 'Démarrage live refusé par ce paquet; aucune procédure de libération live n’est incluse.'
}
if ($mode -eq 'demo' -and $execution -match '^(1|true|yes|on)$' -and -not $ConfirmedDemoOnlyAndNoActiveJobs) {
  throw 'Vérifier le backend préproduction, les comptes suiveurs démo et l’absence de job actif, puis confirmer avec -ConfirmedDemoOnlyAndNoActiveJobs.'
}
if ($task.State -eq 'Running') { Write-Host 'Worker déjà en cours; aucune deuxième instance lancée.'; exit 0 }
Start-ScheduledTask -TaskName $taskName
Write-Host 'Tâche démarrée. Ce résultat ne prouve ni la connexion MT5 ni une réplication; exécuter copy-trading-health.ps1 puis contrôler les preuves natives côté backend.'
