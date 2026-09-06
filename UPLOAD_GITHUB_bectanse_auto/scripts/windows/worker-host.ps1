param([string]$InstallRoot = "C:\Bectanse\MT5Worker")
$ErrorActionPreference = "Continue"
$configPath = "$InstallRoot\.worker.env"
if (-not (Test-Path $configPath)) { throw "Configuration absente: exécutez configure.ps1" }
Get-Content $configPath | ForEach-Object {
  if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
while ($true) {
  & "$InstallRoot\venv\Scripts\python.exe" -m trading_journal.worker 2>&1 |
    Tee-Object -FilePath "$InstallRoot\logs\worker.log" -Append
  Start-Sleep -Seconds 15
}
