$task = Get-ScheduledTask -TaskName "BectanseMT5Worker" -ErrorAction SilentlyContinue
if (-not $task) { Write-Host "SERVICE ............... NOT INSTALLED"; exit 1 }
$info = Get-ScheduledTaskInfo -TaskName "BectanseMT5Worker"
Write-Host "SERVICE ............... $($task.State)"
Write-Host "LAST RUN .............. $($info.LastRunTime)"
Write-Host "LAST RESULT ........... $($info.LastTaskResult)"
Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object Id,StartTime,Path
