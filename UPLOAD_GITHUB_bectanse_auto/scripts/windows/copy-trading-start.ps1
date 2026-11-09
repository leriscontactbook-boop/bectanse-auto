Start-ScheduledTask -TaskName 'BectanseCopyTradingWorker'
Start-Sleep -Seconds 2
Get-ScheduledTaskInfo -TaskName 'BectanseCopyTradingWorker'
