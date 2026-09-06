param([string]$InstallRoot = "C:\Bectanse\MT5Worker")
$ErrorActionPreference = "Stop"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "Installez Python 3.11+ avant de continuer." }
  winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
& "$PSScriptRoot\install.ps1" -InstallRoot $InstallRoot
