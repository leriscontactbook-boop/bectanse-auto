param(
  [string]$InstallRoot = "C:\Bectanse\MT5Worker",
  [string]$TerminalRoot = "C:\MT5",
  [string]$WorkerUser = "bectanse-worker",
  [string]$ToolsRoot = "C:\Bectanse\Tools"
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Ce script doit être exécuté par un administrateur."
}
if ($WorkerUser -notmatch '^[A-Za-z0-9._-]{3,32}$') {
  throw "Nom d'utilisateur worker invalide."
}
if (-not (Test-Path "$InstallRoot\scripts\worker-host.ps1")) {
  throw "Worker non installé dans $InstallRoot."
}
if (-not (Test-Path "$InstallRoot\.worker.env")) {
  throw "Configuration worker absente dans $InstallRoot."
}

$random = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 48
$random.GetBytes($bytes)
$random.Dispose()
$workerPassword = [Convert]::ToBase64String($bytes) + "!aA7"
$securePassword = ConvertTo-SecureString $workerPassword -AsPlainText -Force
$existingUser = Get-LocalUser -Name $WorkerUser -ErrorAction SilentlyContinue
if ($existingUser) {
  Set-LocalUser -Name $WorkerUser -Password $securePassword -PasswordNeverExpires $true
} else {
  New-LocalUser -Name $WorkerUser -Password $securePassword -PasswordNeverExpires `
    -UserMayNotChangePassword -AccountNeverExpires -Description "Bectanse MT5 worker interactif" | Out-Null
}

New-Item -ItemType Directory -Force -Path $ToolsRoot | Out-Null
$zipPath = Join-Path $ToolsRoot "AutoLogon.zip"
$extractPath = Join-Path $ToolsRoot "Autologon"
Invoke-WebRequest "https://download.sysinternals.com/files/AutoLogon.zip" -OutFile $zipPath
if (Test-Path $extractPath) { Remove-Item $extractPath -Recurse -Force }
Expand-Archive $zipPath -DestinationPath $extractPath -Force
$autologon = Join-Path $extractPath "Autologon64.exe"
$signature = Get-AuthenticodeSignature $autologon
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft") {
  throw "Signature Microsoft invalide pour Autologon64.exe."
}

& icacls $InstallRoot /inheritance:r /grant:r `
  "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "${WorkerUser}:(OI)(CI)RX" | Out-Null
& icacls "$InstallRoot\.worker.env" /inheritance:r /grant:r `
  "Administrators:F" "SYSTEM:F" "${WorkerUser}:R" | Out-Null
& icacls "$InstallRoot\logs" /inheritance:r /grant:r `
  "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "${WorkerUser}:(OI)(CI)M" | Out-Null
& icacls $TerminalRoot /inheritance:r /grant:r `
  "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "${WorkerUser}:(OI)(CI)M" | Out-Null

$taskName = "BectanseMT5Worker"
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $PID -and $_.CommandLine -like "*$InstallRoot*" -and `
    $_.Name -in @("python.exe", "powershell.exe")
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" | Where-Object {
  $_.ExecutablePath -like "$TerminalRoot*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$taskUser = "$env:COMPUTERNAME\$WorkerUser"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\scripts\worker-host.ps1`"" `
  -WorkingDirectory $InstallRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Principal $taskPrincipal -Settings $settings -Force | Out-Null

$process = Start-Process -FilePath $autologon `
  -ArgumentList @("/accepteula", $WorkerUser, $env:COMPUTERNAME, $workerPassword) `
  -WindowStyle Hidden -Wait -PassThru
$workerPassword = $null
$securePassword.Dispose()
if ($process.ExitCode -ne 0) {
  throw "Configuration Autologon échouée avec le code $($process.ExitCode)."
}

Write-Host "Utilisateur interactif $taskUser configuré."
Write-Host "Tâche $taskName configurée pour démarrer à l'ouverture de session."
Write-Host "Redémarrez Windows puis vérifiez la session et les heartbeats."
