Start-ScheduledTask -TaskName "BectanseMT5Worker"
Start-Sleep -Seconds 2
Get-ScheduledTaskInfo -TaskName "BectanseMT5Worker"
