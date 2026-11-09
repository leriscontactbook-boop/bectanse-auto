param(
  [Parameter(Mandatory=$true)][string]$BackendUrl,
  [Parameter(Mandatory=$true)][string]$MasterMemberCode,
  [Parameter(Mandatory=$true)][string]$ReleaseRoot,
  [Parameter(Mandatory=$true)][string]$MasterTerminalPath,
  [Parameter(Mandatory=$true)][string[]]$FollowerTerminalPaths,
  [Parameter(Mandatory=$true)][Security.SecureString]$MasterWorkerSecret,
  [Parameter(Mandatory=$true)][Security.SecureString]$FollowerWorkerSecret,
  [string]$WorkerId = $env:COMPUTERNAME,
  [string]$WorkerUser = "$env:USERDOMAIN\$env:USERNAME",
  [string]$InstallRoot = "C:\Bectanse\CopyTrading"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $InstallRoot, (Join-Path $InstallRoot 'logs') | Out-Null
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $ReleaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json') -PathType Leaf)) {
  throw "Release Windows non vérifiée: $ReleaseRoot"
}
$backendUri = $null
if (-not [Uri]::TryCreate($BackendUrl, [UriKind]::Absolute, [ref]$backendUri) -or
    $backendUri.Scheme -ne 'https' -or $backendUri.UserInfo -or $backendUri.Query -or $backendUri.Fragment) {
  throw "Le backend doit être une URL HTTPS absolue sans credentials, query ni fragment."
}
if (-not (Test-Path $MasterTerminalPath)) { throw "Terminal maître introuvable." }
if ($FollowerTerminalPaths.Count -lt 1) { throw "Au moins un terminal suiveur dédié est requis." }
foreach ($terminal in $FollowerTerminalPaths) {
  if (-not (Test-Path $terminal)) { throw "Terminal suiveur introuvable." }
}
$allPaths = @($MasterTerminalPath) + @($FollowerTerminalPaths)
if ($allPaths | Where-Object { $_ -match '[;\r\n]' }) {
  throw 'Les chemins MT5 ne peuvent pas contenir point-virgule ou retour à la ligne.'
}
$resolvedTerminalPaths = @($allPaths | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
if (($resolvedTerminalPaths | ForEach-Object { $_.ToLowerInvariant() } | Select-Object -Unique).Count -ne $resolvedTerminalPaths.Count) {
  throw "Chaque session MT5 doit utiliser un exécutable distinct."
}
if (@($resolvedTerminalPaths | ForEach-Object { (Split-Path -Parent $_).ToLowerInvariant() } |
      Select-Object -Unique).Count -ne $resolvedTerminalPaths.Count) {
  throw "Chaque compte doit utiliser un dossier d'installation MT5 portable distinct pour isoler son IPC."
}
if ($resolvedTerminalPaths | Where-Object { (Split-Path -Leaf $_) -ine 'terminal64.exe' }) {
  throw "Chaque chemin doit désigner l'exécutable MT5 terminal64.exe."
}
foreach ($terminal in $resolvedTerminalPaths) {
  $terminalRoot = Split-Path -Parent $terminal
  $managedMarker = Join-Path $terminalRoot '.bectanse-copy-trading-managed.json'
  if (-not (Test-Path -LiteralPath $managedMarker -PathType Leaf)) {
    throw "Terminal non géré/refus d’élargir ses ACL: $terminalRoot. Installer un nouveau terminal dédié avec install-mt5-terminals.ps1."
  }
}
if ($MasterMemberCode -notmatch '^[A-Za-z0-9_-]{2,100}$') { throw "Code maître invalide." }
if ($WorkerId -notmatch '^[A-Za-z0-9._-]{1,48}$') { throw "Identifiant worker invalide." }

function ConvertTo-PlainText([Security.SecureString]$Value) {
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$masterSecret = ConvertTo-PlainText $MasterWorkerSecret
$followerSecret = ConvertTo-PlainText $FollowerWorkerSecret
try {
  if ($masterSecret.Length -lt 32 -or $followerSecret.Length -lt 32 -or
      $masterSecret -match '[\r\n]' -or $followerSecret -match '[\r\n]' -or
      $masterSecret -ceq $followerSecret) {
    throw "Les secrets maîtres et suiveurs doivent être distincts et contenir au moins 32 caractères."
  }
  $values = [ordered]@{
    COPY_TRADING_BACKEND_URL=$BackendUrl.TrimEnd('/');
    COPY_TRADING_MASTER_MEMBER_CODE=$MasterMemberCode.Trim().ToUpperInvariant();
    COPY_TRADING_WORKER_ID=$WorkerId;
    COPY_TRADING_MASTER_WORKER_SECRET=$masterSecret;
    COPY_TRADING_FOLLOWER_WORKER_SECRET=$followerSecret;
    MT5_COPY_MASTER_TERMINAL_PATH=$MasterTerminalPath;
    MT5_COPY_TERMINAL_PATHS=($FollowerTerminalPaths -join ';');
    COPY_TRADING_MODE='simulation';
    COPY_TRADING_EXECUTION_ENABLED='false';
    COPY_TRADING_LIVE_RELEASE_APPROVED='false';
    COPY_TRADING_POLL_SECONDS='2';
    COPY_TRADING_HEARTBEAT_SECONDS='15';
    COPY_TRADING_CONNECT_TIMEOUT_MS='60000';
    COPY_TRADING_LOG_LEVEL='INFO'
  }
  $configDirectory = Join-Path $InstallRoot 'config'
  New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null
  $configPath = Join-Path $configDirectory '.worker.env'
  & icacls $InstallRoot /grant:r "${WorkerUser}:(OI)(CI)RX" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Impossible d’autoriser le worker à traverser le dossier d’installation." }
  & icacls $ReleaseRoot /grant:r "${WorkerUser}:(OI)(CI)RX" /T /C | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Impossible d’autoriser la lecture de la release par le worker." }
  & icacls $configDirectory /inheritance:r /grant:r `
    "Administrators:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "${WorkerUser}:(OI)(CI)RX" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Impossible de protéger le dossier des secrets du worker." }
  $temporaryConfigPath = "$configPath.$([guid]::NewGuid().ToString('N')).tmp"
  $lines = $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
  try {
    [IO.File]::WriteAllLines($temporaryConfigPath, $lines, [Text.UTF8Encoding]::new($false))
    & icacls $temporaryConfigPath /inheritance:r /grant:r `
      "Administrators:F" "SYSTEM:F" "${WorkerUser}:R" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Impossible de protéger le fichier de configuration." }
    if (Test-Path -LiteralPath $configPath) {
      $backupPath = "$configPath.$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')).backup"
      Copy-Item -LiteralPath $configPath -Destination $backupPath
    }
    Move-Item -LiteralPath $temporaryConfigPath -Destination $configPath -Force
  } finally {
    if (Test-Path -LiteralPath $temporaryConfigPath) {
      Remove-Item -LiteralPath $temporaryConfigPath -Force -ErrorAction SilentlyContinue
    }
  }
  $logRoot = Join-Path $InstallRoot 'logs'
  New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
  & icacls $logRoot /grant:r "${WorkerUser}:(OI)(CI)M" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Impossible d'autoriser l'écriture des journaux du worker." }
  foreach ($terminal in $resolvedTerminalPaths) {
    $terminalRoot = Split-Path -Parent $terminal
    & icacls $terminalRoot /grant:r "${WorkerUser}:(OI)(CI)M" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Impossible d'autoriser le worker à écrire dans le dossier terminal dédié." }
  }
  Write-Host "Configuration locale enregistrée, exécution désactivée par défaut."
} finally {
  $masterSecret = $null
  $followerSecret = $null
}
