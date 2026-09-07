param(
  [string]$InstallRoot = "C:\Bectanse\MT5Worker",
  [string]$TerminalRoot = "C:\MT5"
)

Stop-ScheduledTask -TaskName "BectanseMT5Worker" -ErrorAction SilentlyContinue
Get-Process terminal64,python -ErrorAction SilentlyContinue | Where-Object {
  $_.Path -like "$InstallRoot*" -or $_.Path -like "$TerminalRoot*"
} | Stop-Process -Force
