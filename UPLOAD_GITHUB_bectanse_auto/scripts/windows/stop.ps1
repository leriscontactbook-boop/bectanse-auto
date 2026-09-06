Stop-ScheduledTask -TaskName "BectanseMT5Worker" -ErrorAction SilentlyContinue
Get-Process terminal64,python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "C:\Bectanse\MT5Worker*" } | Stop-Process -Force
