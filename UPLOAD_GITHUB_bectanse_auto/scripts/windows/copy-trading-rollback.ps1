param(
  [Parameter(Mandatory=$true)][string]$TargetReleaseRoot,
  [string]$InstallRoot = "C:\Bectanse\CopyTrading",
  [string]$WorkerUser = "$env:USERDOMAIN\$env:USERNAME",
  [switch]$ConfirmedNoActiveCopyJobs
)
$ErrorActionPreference = 'Stop'
$releaseRoot = (Resolve-Path -LiteralPath $TargetReleaseRoot).Path
$releasesRoot = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'releases')).TrimEnd('\') + '\'
if (-not $releaseRoot.StartsWith($releasesRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'La release cible doit rester sous le dossier releases de cette installation.'
}
$manifestPath = Join-Path $releaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json'
$pythonPath = Join-Path $releaseRoot 'venv\Scripts\python.exe'
$workerScript = Join-Path $releaseRoot 'scripts\windows\copy-trading-worker-host.ps1'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
  throw "La release cible n’est pas installée ou complète: $releaseRoot"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or -not $manifest.files) { throw 'Manifeste de rollback invalide.' }
foreach ($entry in $manifest.files) {
  $relative = [string]$entry.path
  if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains('..') -or
      $relative.Contains('\') -or $relative.Contains(':')) {
    throw "Chemin non sûr dans le manifeste: $relative"
  }
  $memberPath = Join-Path $releaseRoot ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
  if (-not (Test-Path -LiteralPath $memberPath -PathType Leaf) -or
      (Get-FileHash -LiteralPath $memberPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne
      ([string]$entry.sha256).ToLowerInvariant()) {
    throw "Hash ou fichier de la release de retour arrière invalide: $relative"
  }
}
$taskName = 'BectanseCopyTradingWorker'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
  throw 'Arrêter le worker uniquement après réconciliation des jobs, puis relancer le rollback.'
}
$processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '-m\s+copy_trading' })
if ($processes.Count -gt 0) { throw 'Un processus worker résiduel existe; aucun processus n’a été forcé.' }
if (-not $ConfirmedNoActiveCopyJobs) {
  throw 'Vérifier les jobs et handoffs dans l’API admin, puis confirmer avec -ConfirmedNoActiveCopyJobs.'
}

$arguments = '-NoProfile -ExecutionPolicy RemoteSigned -File "{0}" -InstallRoot "{1}" -ReleaseRoot "{2}"' -f `
  $workerScript, $InstallRoot, $releaseRoot
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $releaseRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $WorkerUser
$principal = New-ScheduledTaskPrincipal -UserId $WorkerUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Retour planifié vers $($manifest.release_version) ($($manifest.source_commit))."
Write-Host 'La tâche n’a pas été démarrée. Les autres releases, journaux et configurations sont conservés.'
