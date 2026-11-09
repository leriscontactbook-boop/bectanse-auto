param([string]$InstallRoot = "C:\Bectanse\CopyTrading")
$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
New-Item -ItemType Directory -Force -Path $InstallRoot, "$InstallRoot\logs", "$InstallRoot\scripts" | Out-Null
Copy-Item "$SourceRoot\copy_trading" "$InstallRoot\copy_trading" -Recurse -Force
Copy-Item "$SourceRoot\trading_journal" "$InstallRoot\trading_journal" -Recurse -Force
Copy-Item "$SourceRoot\requirements-copy-trading-worker.txt" "$InstallRoot\requirements.txt" -Force
Copy-Item "$PSScriptRoot\copy-trading-*.ps1" "$InstallRoot\scripts" -Force
if (-not (Test-Path "$InstallRoot\venv\Scripts\python.exe")) {
  python -m venv "$InstallRoot\venv"
  if ($LASTEXITCODE -ne 0) { throw "Création du virtualenv échouée." }
}
& "$InstallRoot\venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Mise à jour de pip échouée." }
& "$InstallRoot\venv\Scripts\python.exe" -m pip install -r "$InstallRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances Copy Trading échouée." }
& "$InstallRoot\venv\Scripts\python.exe" -m py_compile `
  "$InstallRoot\copy_trading\__main__.py" "$InstallRoot\copy_trading\worker.py"
if ($LASTEXITCODE -ne 0) { throw "La compilation du worker Copy Trading a échoué." }
Write-Host "Worker Copy Trading installé. Aucun compte MT5 n'a été connecté."
