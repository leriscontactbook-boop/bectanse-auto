param(
  [Parameter(Mandatory=$true)][string]$ReleaseRoot,
  [string]$InstallRoot = "C:\Bectanse\CopyTrading"
)
$ErrorActionPreference = "Stop"
$configPath = Join-Path $InstallRoot 'config\.worker.env'
if (-not (Test-Path $configPath)) { throw "Configuration Copy Trading absente." }
$releaseManifestPath = Join-Path $ReleaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json'
if (-not (Test-Path $releaseManifestPath)) { throw "Manifeste de release absent." }
$releaseManifest = Get-Content -LiteralPath $releaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($releaseManifest.schema_version -ne 1 -or -not $releaseManifest.release_version -or
    -not $releaseManifest.source_commit -or -not $releaseManifest.files) {
  throw 'Manifeste de release invalide; worker non démarré.'
}
foreach ($entry in $releaseManifest.files) {
  $relative = [string]$entry.path
  if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains('..') -or
      $relative.Contains('\') -or $relative.Contains(':')) {
    throw "Chemin non sûr dans le manifeste: $relative"
  }
  $memberPath = Join-Path $ReleaseRoot ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
  if (-not (Test-Path -LiteralPath $memberPath -PathType Leaf)) { throw "Fichier de release absent: $relative" }
  $memberHash = (Get-FileHash -LiteralPath $memberPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($memberHash -ne ([string]$entry.sha256).ToLowerInvariant()) { throw "Hash de release invalide: $relative" }
}
$env:COPY_TRADING_RELEASE_VERSION = [string]$releaseManifest.release_version
$env:COPY_TRADING_SOURCE_COMMIT = [string]$releaseManifest.source_commit
Get-Content -LiteralPath $configPath -Encoding UTF8 | ForEach-Object {
  if ($_ -match '^\s*([^#=\s]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$logPath = Join-Path $InstallRoot 'logs\copy-trading-worker.log'
if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 10000000) {
  $archive = "$logPath.$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')).old"
  Move-Item $logPath $archive -Force
}
$hostTimestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss,fff', [Globalization.CultureInfo]::InvariantCulture)
"$hostTimestamp INFO copy-trading-host release version=$env:COPY_TRADING_RELEASE_VERSION source_commit=$env:COPY_TRADING_SOURCE_COMMIT session_id=$((Get-Process -Id $PID).SessionId)" |
  Tee-Object -FilePath $logPath -Append | Out-Null
Set-Location $ReleaseRoot
& "$ReleaseRoot\venv\Scripts\python.exe" -m copy_trading 2>&1 |
  Tee-Object -FilePath $logPath -Append
exit $LASTEXITCODE
