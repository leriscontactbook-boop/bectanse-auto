param(
  [string]$InstallRoot = "C:\Bectanse\MT5Worker",
  [Parameter(Mandatory=$true)][string]$BackendUrl,
  [Parameter(Mandatory=$true)][Security.SecureString]$WorkerSecret,
  [Parameter(Mandatory=$true)][string[]]$TerminalPaths,
  [string]$WorkerId = $env:COMPUTERNAME
)
$ErrorActionPreference = "Stop"
foreach ($terminal in $TerminalPaths) { if (-not (Test-Path $terminal)) { throw "Terminal introuvable: $terminal" } }
$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($WorkerSecret))
if ($plain.Length -lt 32) { throw "Le secret worker doit contenir au moins 32 caractères." }
$values = @{
  BACKEND_URL=$BackendUrl.TrimEnd('/'); INTERNAL_WORKER_SECRET=$plain; WORKER_ID=$WorkerId;
  MT5_TERMINAL_PATHS=($TerminalPaths -join ';'); MT5_WORKER_COUNT=$TerminalPaths.Count;
  MAX_CONCURRENT_MT5_SESSIONS=$TerminalPaths.Count; MT5_REQUIRE_READ_ONLY='true';
  MT5_WORKER_POLL_SECONDS='5'; LOG_LEVEL='INFO'
}
$lines = $values.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }
$configPath = "$InstallRoot\.worker.env"
[IO.File]::WriteAllLines($configPath, $lines, [Text.UTF8Encoding]::new($false))
icacls $configPath /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(R)" | Out-Null
$plain = $null
Write-Host "Configuration chiffrée au repos par Windows ACL et enregistrée."
