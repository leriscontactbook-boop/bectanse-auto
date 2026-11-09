param(
  [ValidateRange(1, 32)][int]$Count = 1,
  [string]$TerminalRoot = "C:\Bectanse\CopyTradingTerminals",
  [string]$DownloadUrl = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
)
$ErrorActionPreference = "Stop"
$installerRoot = Join-Path $TerminalRoot 'installers'
New-Item -ItemType Directory -Force -Path $installerRoot | Out-Null
$installer = Join-Path $installerRoot ("mt5setup-" + [guid]::NewGuid().ToString('N') + '.exe')

$uri = [Uri]$DownloadUrl
if ($uri.Scheme -ne "https" -or $uri.Host -ne "download.mql5.com" -or
    $uri.AbsolutePath -ne "/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe") {
  throw "L'installateur générique doit provenir de l'URL HTTPS officielle MetaQuotes."
}

try {
  Invoke-WebRequest $DownloadUrl -OutFile $installer
  $signature = Get-AuthenticodeSignature $installer
  if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notlike "*MetaQuotes*") {
    throw "La signature de l'installateur MetaTrader 5 n'est pas valide."
  }

  $installed = @()
  New-Item -ItemType Directory -Force -Path $TerminalRoot | Out-Null
  foreach ($index in 1..$Count) {
    $node = "NODE-{0:D2}" -f $index
    $path = Join-Path $TerminalRoot $node
    $terminal = Join-Path $path "terminal64.exe"
    $marker = Join-Path $path '.bectanse-copy-trading-managed.json'
    if (Test-Path -LiteralPath $path) {
      if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        throw "Le dossier existe sans preuve qu’il est réservé au Copy Trading; aucun fichier ni processus n’a été touché: $path"
      }
      if (-not (Test-Path -LiteralPath $terminal -PathType Leaf)) {
        throw "Le terminal géré existe mais son exécutable manque; inspecter avant réparation: $path"
      }
      $installed += [pscustomobject]@{ node = $node; terminal = $terminal; state = 'ALREADY_INSTALLED' }
      continue
    }
    $stage = Join-Path $TerminalRoot (".$node.staging-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    try {
      $arguments = "/auto /path:`"$stage`""
      $process = Start-Process $installer -ArgumentList $arguments -Wait -PassThru
      $stagedTerminal = Join-Path $stage 'terminal64.exe'
      if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $stagedTerminal -PathType Leaf)) {
        throw "Installation MT5 échouée pour $node (code $($process.ExitCode)); l’installation partielle reste dans $stage."
      }
      $terminalSignature = Get-AuthenticodeSignature -FilePath $stagedTerminal
      if ($terminalSignature.Status -ne 'Valid' -or $terminalSignature.SignerCertificate.Subject -notlike '*MetaQuotes*') {
        throw "Le terminal installé n’est pas signé par MetaQuotes; le dossier reste isolé pour inspection: $stage"
      }
      $proof = [ordered]@{
        installer_url = $DownloadUrl
        installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        signer_thumbprint = $signature.SignerCertificate.Thumbprint
        terminal_sha256 = (Get-FileHash -LiteralPath $stagedTerminal -Algorithm SHA256).Hash.ToLowerInvariant()
        terminal_file_version = (Get-Item -LiteralPath $stagedTerminal).VersionInfo.FileVersion
        terminal_signer_thumbprint = $terminalSignature.SignerCertificate.Thumbprint
        installed_at_utc = [DateTime]::UtcNow.ToString('o')
        isolated_portable_directory = $true
      }
      $proof | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage '.bectanse-copy-trading-managed.json') -Encoding UTF8
      Move-Item -LiteralPath $stage -Destination $path
      $installed += [pscustomobject]@{ node = $node; terminal = $terminal; state = 'INSTALLED' }
    } catch {
      throw
    }
  }
  $installed
} finally {
  if (Test-Path -LiteralPath $installer) {
    Write-Host "Installateur MetaQuotes conservé avec la release locale: $installer"
  }
}
