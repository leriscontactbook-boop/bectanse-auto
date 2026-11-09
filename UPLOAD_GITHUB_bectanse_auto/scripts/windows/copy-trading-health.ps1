param([string]$InstallRoot = "C:\Bectanse\CopyTrading")
$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName 'BectanseCopyTradingWorker' -ErrorAction SilentlyContinue
if (-not $task) { Write-Host 'TASK ................. NOT INSTALLED'; exit 1 }
$info = Get-ScheduledTaskInfo -TaskName 'BectanseCopyTradingWorker'
Write-Host "TASK .................. $($task.State)"
Write-Host "LAST RUN .............. $($info.LastRunTime)"
Write-Host "LAST RESULT ........... $($info.LastTaskResult)"
$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  $_.CommandLine -like '*-m copy_trading*'
}
if (-not $workers) { Write-Host 'COPY WORKER ........... NOT RUNNING'; exit 1 }
$workers | Select-Object ProcessId,SessionId,CommandLine | Format-Table -AutoSize
$log = Join-Path $InstallRoot 'logs\copy-trading-worker.log'
$lastHeartbeat = if (Test-Path $log) {
  Get-Content $log -Tail 500 | Where-Object { $_ -match 'worker_heartbeat role=' } | Select-Object -Last 1
}
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
Write-Host "BACKEND HEARTBEAT ..... FRESH ($([int]$heartbeatAge.TotalSeconds)s)"
Write-Host 'Note: check admin operations page for the authoritative last_seen_at timestamp.'
