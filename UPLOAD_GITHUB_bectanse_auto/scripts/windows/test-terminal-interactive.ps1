param(
  [Parameter(Mandatory=$true)][string]$InstallRoot,
  [Parameter(Mandatory=$true)][string]$ReleaseRoot,
  [Parameter(Mandatory=$true)][string]$TerminalPath,
  [string]$ExpectedLogin = '',
  [string]$ResultPath = '',
  [switch]$PromptDemoCredentials
)
$ErrorActionPreference = 'Stop'
$TerminalPath = (Resolve-Path -LiteralPath $TerminalPath).Path
$terminalRoot = Split-Path -Parent $TerminalPath
$managedMarker = Join-Path $terminalRoot '.bectanse-copy-trading-managed.json'
if (-not (Test-Path -LiteralPath $managedMarker -PathType Leaf)) {
  throw 'Refus de sonder un terminal non dédié/géré par le paquet Copy Trading.'
}
$python = Join-Path $ReleaseRoot 'venv\Scripts\python.exe'
$probe = Join-Path $ReleaseRoot 'tools\probe_mt5_terminal_demo.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $probe -PathType Leaf)) { throw "Release Copy Trading invalide: $ReleaseRoot" }
$ResultPath = if ($ResultPath) { $ResultPath } else { Join-Path $InstallRoot 'logs\terminal-smoke.json' }
$resultDirectory = Split-Path -Parent $ResultPath
New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null
$context = [pscustomobject]@{
  identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  session_id = (Get-Process -Id $PID).SessionId
  terminal_path = $TerminalPath
  terminal_installation = (Get-Content -LiteralPath $managedMarker -Raw -Encoding UTF8 | ConvertFrom-Json)
}
$context | ConvertTo-Json -Compress | Set-Content "$ResultPath.context" -Encoding UTF8
$commandArgs = @($probe, '--terminal-path', $TerminalPath)
if ($ExpectedLogin) { $commandArgs += @('--expected-login', $ExpectedLogin) }
if ($PromptDemoCredentials) { $commandArgs += '--prompt-demo-credentials' }
& $python @commandArgs *> $ResultPath
exit $LASTEXITCODE
