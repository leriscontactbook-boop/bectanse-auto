param(
  [Parameter(Mandatory=$true)][string]$ReleaseRoot,
  [string]$InstallRoot = "C:\Bectanse\CopyTrading",
  [string]$WorkerUser = "$env:USERDOMAIN\$env:USERNAME",
  [switch]$ConfirmedNoActiveCopyJobs
)
$ErrorActionPreference = 'Stop'
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$taskName = 'BectanseCopyTradingWorker'
$configPath = Join-Path $InstallRoot 'config\.worker.env'
$pythonPath = Join-Path $ReleaseRoot 'venv\Scripts\python.exe'
$workerScript = Join-Path $ReleaseRoot 'scripts\windows\copy-trading-worker-host.ps1'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "Configurez d’abord le worker: $configPath" }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $workerScript -PathType Leaf)) { throw "Release Windows incomplète: $ReleaseRoot" }

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$workerProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match '-m\s+copy_trading' })
if ($existingTask -or $workerProcesses.Count -gt 0) {
  if ($existingTask -and $existingTask.State -eq 'Running') {
    throw 'Le worker planifié tourne. Vérifier l’API des opérations, arrêter proprement le worker, puis reprovisionner.'
  }
  if ($workerProcesses.Count -gt 0) {
    throw 'Un processus du worker existe encore. Vérifier l’API des opérations et attendre son arrêt avant reprovisionnement.'
  }
  if (-not $ConfirmedNoActiveCopyJobs) {
    throw 'Une tâche existe déjà. Après vérification backend qu’aucune opération n’est active et que le worker est arrêté, relancer avec -ConfirmedNoActiveCopyJobs.'
  }
  Write-Warning 'L’absence d’opérations actives doit être confirmée dans l’API admin du backend avant de modifier la tâche.'
}

$arguments = '-NoProfile -ExecutionPolicy RemoteSigned -File "{0}" -InstallRoot "{1}" -ReleaseRoot "{2}"' -f `
  $workerScript, $InstallRoot, $ReleaseRoot
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $ReleaseRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $WorkerUser
$principal = New-ScheduledTaskPrincipal -UserId $WorkerUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Tâche $taskName configurée pour la release $ReleaseRoot."
Write-Host 'Elle démarre à la connexion interactive; aucun worker ni terminal MT5 n’a été démarré.'
