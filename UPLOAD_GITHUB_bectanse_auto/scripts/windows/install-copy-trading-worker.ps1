param(
  [string]$InstallRoot = "C:\Bectanse\CopyTrading",
  [string]$PythonExe = "C:\Program Files\Python314\python.exe"
)
$ErrorActionPreference = "Stop"
$sourceRoot = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
$manifestPath = Join-Path $sourceRoot 'COPY_TRADING_RELEASE_MANIFEST.json'
$pythonVersion = '3.14.10'
$pythonInstallerUrl = 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe'
$pythonInstallerSha256 = '92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965'

if (-not (Test-Path -LiteralPath $manifestPath)) {
  throw 'Le manifeste de l’archive est absent. Extraire le ZIP complet avant installation.'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.runtime.python -ne $pythonVersion -or
    $manifest.runtime.os -ne 'windows' -or $manifest.runtime.architecture -ne 'x64') {
  throw 'Manifeste ou runtime Windows non pris en charge.'
}
if (-not $manifest.source_commit -or -not $manifest.release_version -or -not $manifest.files) {
  throw 'Manifeste de livraison incomplet.'
}

# Verify every package member before writing any files to the install root.
$requiredMembers = @(
  'copy_trading/__main__.py',
  'copy_trading/worker.py',
  'requirements-copy-trading-worker.lock',
  'scripts/windows/copy-trading-worker-host.ps1'
)
$memberNames = @($manifest.files | ForEach-Object { [string]$_.path })
foreach ($required in $requiredMembers) {
  if ($memberNames -notcontains $required) { throw "Archive member absent: $required" }
}
foreach ($entry in $manifest.files) {
  $relative = [string]$entry.path
  if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains('..') -or
      $relative.Contains('\') -or $relative.Contains(':')) {
    throw "Chemin d’archive non sûr: $relative"
  }
  $candidate = Join-Path $sourceRoot ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Fichier d’archive absent: $relative" }
  $actual = Get-FileHash -LiteralPath $candidate -Algorithm SHA256
  if ($actual.Hash.ToLowerInvariant() -ne ([string]$entry.sha256).ToLowerInvariant()) {
    throw "Hash d’archive invalide: $relative"
  }
}

$versionPart = [string]$manifest.release_version -replace '[^A-Za-z0-9._-]', '_'
$commitPart = ([string]$manifest.source_commit).Substring(0, [Math]::Min(12, ([string]$manifest.source_commit).Length))
$releaseId = "$versionPart-$commitPart"
$releasesRoot = Join-Path $InstallRoot 'releases'
$releaseRoot = Join-Path $releasesRoot $releaseId
New-Item -ItemType Directory -Force -Path $InstallRoot, $releasesRoot,
  (Join-Path $InstallRoot 'config'), (Join-Path $InstallRoot 'logs') | Out-Null

if (Test-Path -LiteralPath $releaseRoot) {
  $existingManifest = Join-Path $releaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json'
  if (-not (Test-Path -LiteralPath $existingManifest) -or
      (Get-FileHash -LiteralPath $existingManifest -Algorithm SHA256).Hash -ne
      (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash) {
    throw "Le répertoire immuable existe déjà avec un contenu différent: $releaseRoot"
  }
} else {
  foreach ($entry in $manifest.files) {
    $relative = ([string]$entry.path).Replace('/', [IO.Path]::DirectorySeparatorChar)
    $destination = Join-Path $releaseRoot $relative
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $destination
  }
  Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $releaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json')
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
  $downloadsRoot = Join-Path $InstallRoot 'downloads'
  New-Item -ItemType Directory -Force -Path $downloadsRoot | Out-Null
  $installer = Join-Path $downloadsRoot ("python-3.14.10-amd64-" + [guid]::NewGuid().ToString('N') + '.exe')
  if (-not (Test-Path -LiteralPath $installer)) {
    Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $installer -UseBasicParsing
  }
  $downloadHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($downloadHash -ne $pythonInstallerSha256) { throw "Hash SHA-256 de Python $pythonVersion invalide; fichier conservé pour inspection: $installer" }
  $signature = Get-AuthenticodeSignature -FilePath $installer
  if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    throw "La signature de l’installateur Python n’est pas valide; fichier conservé: $installer"
  }
  $pythonTarget = Split-Path -Parent $PythonExe
  New-Item -ItemType Directory -Force -Path $pythonTarget | Out-Null
  $arguments = "/quiet InstallAllUsers=1 TargetDir=`"$pythonTarget`" PrependPath=0 Include_launcher=0 Include_test=0"
  $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
  if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) {
    throw "Installation Python $pythonVersion échouée (code $($process.ExitCode)); installateur conservé: $installer"
  }
}

$pythonSignature = Get-AuthenticodeSignature -FilePath $PythonExe
if ($pythonSignature.Status -ne 'Valid' -or
    $pythonSignature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
  throw "L’interpréteur Python n’est pas signé par Python Software Foundation: $PythonExe"
}
$runtime = & $PythonExe -c 'import json,platform,struct,sys; print(json.dumps({"version":sys.version.split()[0],"bits":struct.calcsize("P")*8,"machine":platform.machine()}))'
if ($LASTEXITCODE -ne 0) { throw 'Impossible de démarrer Python.' }
$runtimeInfo = $runtime | ConvertFrom-Json
if ($runtimeInfo.version -ne $pythonVersion -or $runtimeInfo.bits -ne 64 -or
    $runtimeInfo.machine -notmatch 'AMD64|x86_64') {
  throw "Runtime requis: Python $pythonVersion x64 AMD64; trouvé: $($runtimeInfo.version), $($runtimeInfo.bits)-bit, $($runtimeInfo.machine)."
}

$venvPython = Join-Path $releaseRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
  & $PythonExe -m venv (Join-Path $releaseRoot 'venv')
  if ($LASTEXITCODE -ne 0) { throw 'Création du virtualenv de version échouée.' }
}
$lockPath = Join-Path $releaseRoot 'requirements-copy-trading-worker.lock'
& $venvPython -m pip --disable-pip-version-check install --no-input --only-binary=:all: --require-hashes -r $lockPath
if ($LASTEXITCODE -ne 0) { throw 'Installation vérifiée des dépendances Copy Trading échouée.' }
& $venvPython -m py_compile `
  (Join-Path $releaseRoot 'copy_trading\__main__.py') `
  (Join-Path $releaseRoot 'copy_trading\worker.py') `
  (Join-Path $releaseRoot 'tools\probe_mt5_terminal_demo.py')
if ($LASTEXITCODE -ne 0) { throw 'La compilation du worker ou du diagnostic MT5 a échoué.' }
& $venvPython -c 'from copy_trading.config import CopyTradingConfigurationError; from copy_trading.providers.mt5 import MetaTrader5ExecutionProvider; from trading_journal.providers.base import ProviderError; print("copy-trading imports: OK")'
if ($LASTEXITCODE -ne 0) { throw 'Les imports du worker Windows ont échoué.' }

Write-Host "Release local installée: $releaseRoot"
Write-Host 'Le worker n’a pas été démarré et aucun terminal ni compte MT5 n’a été connecté.'
Write-Output $releaseRoot
