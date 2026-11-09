param(
  [string]$InstallRoot = "C:\Bectanse\CopyTrading",
  [string]$WorkerUser = "$env:USERDOMAIN\$env:USERNAME"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path "$InstallRoot\.worker.env")) { throw "Configurez d'abord le worker." }
if (-not (Test-Path "$InstallRoot\venv\Scripts\python.exe")) { throw "Installez d'abord le worker." }
$taskName = 'BectanseCopyTradingWorker'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\scripts\copy-trading-worker-host.ps1`" -InstallRoot `"$InstallRoot`"" `
  -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $WorkerUser
$principal = New-ScheduledTaskPrincipal -UserId $WorkerUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Tâche Copy Trading planifiée pour une session interactive de $WorkerUser."
Write-Host "Aucun compte MT5 ni aucune session n'a été démarré."
