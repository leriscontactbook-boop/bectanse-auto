param([string]$InstallRoot = "C:\Bectanse\MT5Worker")
& "$PSScriptRoot\status.ps1"
$backend = (Get-Content "$InstallRoot\.worker.env" | Where-Object { $_ -like 'BACKEND_URL=*' }) -replace '^BACKEND_URL=',''
try { $health = Invoke-RestMethod "$backend/health" -TimeoutSec 15; Write-Host "BACKEND ............... $($health.status)" }
catch { Write-Host "BACKEND ............... UNREACHABLE"; exit 1 }
$latest = Get-Content "$InstallRoot\logs\worker.log" -Tail 30 -ErrorAction SilentlyContinue
if ($latest -match '"event":"sync_success"') { Write-Host "LAST SYNC ............. PASS" } else { Write-Host "LAST SYNC ............. WAITING" }
