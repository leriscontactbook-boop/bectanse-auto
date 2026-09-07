# Windows VPS production deployment

## Prerequisites

Use Windows Server 2022, one distinct MT5 installation per concurrent slot, and
an outbound-only firewall policy. The backend never connects inbound to the VPS.
You need the VPS IP, Administrator credentials, the repository release archive,
the production backend URL, and the worker shared secret from Railway.

## Install

1. Connect with Remote Desktop and run Windows Update.
2. Install each broker terminal into a unique directory, for example
   `C:\MT5\NODE-01\terminal64.exe`. For the official MetaQuotes release, run
   `scripts\windows\install-mt5-terminals.ps1 -Count 6`. Start every terminal
   once and accept broker updates. A broker-specific build can replace the
   official installer while keeping the same distinct paths.
3. Copy the release to `C:\Bectanse\release`.
4. Open PowerShell as Administrator:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process -Force
cd C:\Bectanse\release
.\scripts\windows\bootstrap.ps1
$secret = Read-Host "Worker secret" -AsSecureString
.\scripts\windows\configure.ps1 `
  -BackendUrl "https://acces.bectanse-academie.com" `
  -WorkerSecret $secret `
  -TerminalPaths (1..6 | ForEach-Object { "C:\MT5\NODE-{0:D2}\terminal64.exe" -f $_ }) `
  -WorkerId "BECTANSE-EU-01"
.\scripts\windows\provision-interactive-worker.ps1
Restart-Computer
```

After Windows restarts, reconnect as Administrator and verify:

```powershell
.\scripts\windows\health.ps1
quser
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, SessionId, CommandLine
```

The dedicated `bectanse-worker` account must be logged on and the worker Python
processes must run in the same non-zero interactive session. Every terminal slot
runs in its own spawned Python process because the MetaTrader IPC state is
process-global. The official
Sysinternals Autologon utility stores the generated service-account password as
an LSA secret; it is never printed by the provisioning script. The scheduled
task runs with limited privileges at that user's logon and restarts after
failure. The configuration ACL grants the worker read-only access to secrets,
write access only to its logs and MT5 terminal data, and no administrator role.

Do not register the MT5 worker as SYSTEM or as a Windows service: the official
MetaTrader Python integration requires an interactive desktop session.

For a one-off manual start after the worker user is already logged on:

```powershell
.\scripts\windows\start.ps1
.\scripts\windows\health.ps1
```

## Network and capacity

Allow outbound HTTPS 443 to the Bectanse backend and broker traffic required by
MT5. Do not create an inbound worker endpoint. Keep
`MAX_CONCURRENT_MT5_SESSIONS` at or above `MT5_WORKER_COUNT`, with one terminal
path per slot. Scale by adding nodes, not one VPS per customer.

## Replacement

Prepare a fresh VPS, copy the same release, reinstall terminals, run bootstrap
and configure with a new worker ID, verify its heartbeat, then stop the old node.
No database or MT5 credential export is stored on the worker.
