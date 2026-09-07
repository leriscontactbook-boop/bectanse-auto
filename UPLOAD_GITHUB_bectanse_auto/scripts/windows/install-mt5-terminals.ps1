param(
  [ValidateRange(1, 32)][int]$Count = 1,
  [string]$TerminalRoot = "C:\MT5",
  [string]$DownloadUrl = "https://download.terminal.free/cdn/web/metaquotes.ltd/mt5/mt5setup.exe"
)
$ErrorActionPreference = "Stop"
$installer = Join-Path $env:TEMP "mt5setup.exe"

$uri = [Uri]$DownloadUrl
if ($uri.Scheme -ne "https" -or $uri.Host -ne "download.terminal.free") {
  throw "L'installateur MT5 doit provenir du CDN officiel download.terminal.free."
}

try {
  Invoke-WebRequest $DownloadUrl -OutFile $installer
  $signature = Get-AuthenticodeSignature $installer
  if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*MetaQuotes*") {
    throw "La signature de l'installateur MetaTrader 5 n'est pas valide."
  }

  $installed = @()
  foreach ($index in 1..$Count) {
    $node = "NODE-{0:D2}" -f $index
    $path = Join-Path $TerminalRoot $node
    $terminal = Join-Path $path "terminal64.exe"
    if (-not (Test-Path $terminal)) {
      New-Item -ItemType Directory -Force -Path $path | Out-Null
      $arguments = "/auto /path:`"$path`""
      $process = Start-Process $installer -ArgumentList $arguments -Wait -PassThru
      if ($process.ExitCode -ne 0 -and -not (Test-Path $terminal)) {
        throw "Installation MT5 échouée pour $node (code $($process.ExitCode))."
      }
    }
    if (-not (Test-Path $terminal)) { throw "Terminal MT5 introuvable après installation: $terminal" }
    $installed += $terminal
  }
  Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force
  $installed
} finally {
  Remove-Item $installer -Force -ErrorAction SilentlyContinue
}
