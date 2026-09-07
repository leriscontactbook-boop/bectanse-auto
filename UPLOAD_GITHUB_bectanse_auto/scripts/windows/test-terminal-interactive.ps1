param(
  [string]$InstallRoot = "C:\Bectanse\MT5Worker",
  [string]$TerminalPath = "C:\MT5\NODE-01\terminal64.exe",
  [string]$ResultPath = "C:\Bectanse\MT5Worker\logs\terminal-smoke.json",
  [string]$Login = "",
  [string]$Server = "",
  [string]$Password = ""
)
$ErrorActionPreference = "Continue"
$context = [pscustomobject]@{
  identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  session_id = (Get-Process -Id $PID).SessionId
}
$context | ConvertTo-Json -Compress | Set-Content "$ResultPath.context" -Encoding ascii
$commandArgs = @(
  "$InstallRoot\tools\test_mt5_terminal_boot.py",
  "--terminal-path", $TerminalPath
)
if ($Login -or $Server -or $Password) {
  if (-not ($Login -and $Server -and $Password)) {
    throw "Login, Server et Password doivent être fournis ensemble."
  }
  $commandArgs += @("--login", $Login, "--server", $Server, "--password", $Password)
}
& "$InstallRoot\venv\Scripts\python.exe" @commandArgs *> $ResultPath
exit $LASTEXITCODE
