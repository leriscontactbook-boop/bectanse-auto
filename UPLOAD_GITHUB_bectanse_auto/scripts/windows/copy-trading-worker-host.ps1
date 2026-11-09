param([string]$InstallRoot = "C:\Bectanse\CopyTrading")
$ErrorActionPreference = "Stop"
$configPath = Join-Path $InstallRoot '.worker.env'
if (-not (Test-Path $configPath)) { throw "Configuration Copy Trading absente." }
Get-Content $configPath | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$logPath = Join-Path $InstallRoot 'logs\copy-trading-worker.log'
if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 10000000) {
  $archive = "$logPath.$(Get-Date -Format yyyyMMddHHmmss).old"
  Move-Item $logPath $archive -Force
  Get-ChildItem "$logPath.*.old" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 8 |
    Remove-Item -Force -ErrorAction SilentlyContinue
}
Set-Location $InstallRoot
& "$InstallRoot\venv\Scripts\python.exe" -m copy_trading 2>&1 |
  Tee-Object -FilePath $logPath -Append
exit $LASTEXITCODE
