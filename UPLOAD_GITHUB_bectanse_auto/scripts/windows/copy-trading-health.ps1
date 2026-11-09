param([string]$InstallRoot = "C:\Bectanse\CopyTrading")
$ErrorActionPreference = 'Stop'
$taskName = 'BectanseCopyTradingWorker'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Host 'TASK ................. NOT INSTALLED'; exit 1 }
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "TASK .................. $($task.State)"
Write-Host "LAST RUN .............. $($info.LastRunTime)"
Write-Host "LAST RESULT ........... $($info.LastTaskResult)"

$taskAction = @($task.Actions | Where-Object { $_.Execute -match 'powershell(\.exe)?$' } | Select-Object -First 1)
if (-not $taskAction) { Write-Host 'RELEASE ACTION ........ MISSING'; exit 1 }
$releaseMatch = [regex]::Match([string]$taskAction[0].Arguments, '-ReleaseRoot\s+"([^"]+)"')
if (-not $releaseMatch.Success) { Write-Host 'RELEASE ACTION ........ PATH MISSING'; exit 1 }
$releaseRoot = $releaseMatch.Groups[1].Value
$releaseManifestPath = Join-Path $releaseRoot 'COPY_TRADING_RELEASE_MANIFEST.json'
if (-not (Test-Path -LiteralPath $releaseManifestPath -PathType Leaf)) { Write-Host 'RELEASE ACTION ........ RELEASE MISSING'; exit 1 }
$releaseManifest = Get-Content -LiteralPath $releaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedPython = [IO.Path]::GetFullPath((Join-Path $releaseRoot 'venv\Scripts\python.exe'))

$workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  $_.CommandLine -like '*-m copy_trading*'
})
if (-not $workers) { Write-Host 'COPY WORKER ........... NOT RUNNING'; exit 1 }
$workers | Select-Object ProcessId,SessionId | Format-Table -AutoSize
if ($workers | Where-Object { $_.SessionId -eq 0 }) {
  Write-Host 'INTERACTIVE SESSION ... FAIL (worker is in session 0)'; exit 1
}
if (-not ($workers | Where-Object {
    $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $expectedPython
  })) {
  Write-Host 'RUNNING RELEASE ....... MISMATCH'; exit 1
}
Write-Host "RUNNING RELEASE ....... $($releaseManifest.release_version) ($($releaseManifest.source_commit))"

$log = Join-Path $InstallRoot 'logs\copy-trading-worker.log'
if (-not (Test-Path -LiteralPath $log)) { Write-Host 'BACKEND HEARTBEAT ..... NOT SEEN'; exit 1 }
$logTail = @(Get-Content -LiteralPath $log -Tail 1000)
$releaseLine = $logTail | Where-Object { $_ -match 'INFO copy-trading-host release version=' } | Select-Object -Last 1
if (-not $releaseLine -or $releaseLine -notmatch [regex]::Escape(
    "version=$($releaseManifest.release_version) source_commit=$($releaseManifest.source_commit)")) {
  Write-Host 'RELEASE LOG ........... MISMATCH'; exit 1
}
$lastHeartbeat = $logTail | Where-Object { $_ -match 'worker_heartbeat role=' } | Select-Object -Last 1
if (-not $lastHeartbeat) { Write-Host 'BACKEND HEARTBEAT ..... NOT SEEN'; exit 1 }
if ($lastHeartbeat -notmatch '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})') {
  Write-Host 'BACKEND HEARTBEAT ..... TIMESTAMP INVALID'; exit 1
}
$heartbeatAt = [datetime]::ParseExact($matches[1], 'yyyy-MM-dd HH:mm:ss,fff',
  [Globalization.CultureInfo]::InvariantCulture)
$heartbeatAge = (Get-Date) - $heartbeatAt
if ($heartbeatAge.TotalSeconds -gt 90 -or $heartbeatAge.TotalSeconds -lt -5) {
  Write-Host "BACKEND HEARTBEAT ..... STALE ($([int]$heartbeatAge.TotalSeconds)s)"; exit 1
}
if ($lastHeartbeat -notmatch '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})') {
  Write-Host 'BACKEND HEARTBEAT ..... TIMESTAMP INVALID'; exit 1
}
$lastHeartbeatAt = [datetime]::ParseExact($matches[1], 'yyyy-MM-dd HH:mm:ss,fff',
  [Globalization.CultureInfo]::InvariantCulture)
if ($releaseLine -notmatch '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})') {
  Write-Host 'RELEASE LOG ........... TIMESTAMP INVALID'; exit 1
}
$releaseAt = [datetime]::ParseExact($matches[1], 'yyyy-MM-dd HH:mm:ss,fff',
  [Globalization.CultureInfo]::InvariantCulture)
if ($lastHeartbeatAt -lt $releaseAt) { Write-Host 'BACKEND HEARTBEAT ..... FROM PREVIOUS RELEASE'; exit 1 }
Write-Host "BACKEND HEARTBEAT ..... FRESH ($([int]$heartbeatAge.TotalSeconds)s)"

$configPath = Join-Path $InstallRoot 'config\.worker.env'
if (-not (Test-Path -LiteralPath $configPath)) { Write-Host 'BACKEND HEALTH ........ CONFIG MISSING'; exit 1 }
$backendLine = Get-Content -LiteralPath $configPath -Encoding UTF8 |
  Where-Object { $_ -match '^COPY_TRADING_BACKEND_URL=' } | Select-Object -First 1
if (-not $backendLine) { Write-Host 'BACKEND HEALTH ........ URL MISSING'; exit 1 }
$backendUrl = ($backendLine -replace '^COPY_TRADING_BACKEND_URL=', '').TrimEnd('/')
if ($backendUrl -notmatch '^https://' -and $backendUrl -notmatch '^http://(localhost|127\.0\.0\.1)(:\d+)?$') {
  Write-Host 'BACKEND HEALTH ........ URL INVALID'; exit 1
}
try {
  $backendHealth = Invoke-RestMethod -Uri "$backendUrl/health" -Method Get -TimeoutSec 15
  if ($backendHealth.status -ne 'ok' -or $backendHealth.database -ne 'ok') {
    Write-Host 'BACKEND HEALTH ........ UNREADY'; exit 1
  }
  Write-Host 'BACKEND HEALTH ........ API + DATABASE OK'
} catch {
  Write-Host 'BACKEND HEALTH ........ UNREACHABLE'; exit 1
}
Write-Host 'This local check does not prove MT5 account identity or Copy Trading execution.'
Write-Host 'Check the backend member and operations views for native terminal and job confirmations.'
