param(
  [string]$InstallRoot = "C:\Bectanse\MT5Worker",
  [string]$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
)
$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
New-Item -ItemType Directory -Force -Path $InstallRoot, "$InstallRoot\logs" | Out-Null
Copy-Item "$SourceRoot\trading_journal" "$InstallRoot\trading_journal" -Recurse -Force
Copy-Item "$SourceRoot\tools" "$InstallRoot\tools" -Recurse -Force
Copy-Item "$SourceRoot\requirements-mt5-worker.txt" "$InstallRoot\requirements.txt" -Force
if (-not (Test-Path "$InstallRoot\venv\Scripts\python.exe")) { python -m venv "$InstallRoot\venv" }
& "$InstallRoot\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallRoot\venv\Scripts\python.exe" -m pip install -r "$InstallRoot\requirements.txt"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\scripts\worker-host.ps1`"" -WorkingDirectory $InstallRoot
New-Item -ItemType Directory -Force -Path "$InstallRoot\scripts" | Out-Null
Copy-Item "$PSScriptRoot\*.ps1" "$InstallRoot\scripts" -Force
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BectanseMT5Worker" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installation terminée pour $TaskUser. Exécutez configure.ps1 puis ouvrez une session avec cet utilisateur."
