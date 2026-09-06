# Windows VPS production deployment

## Prerequisites

Use Windows Server 2022, one distinct MT5 installation per concurrent slot, and
an outbound-only firewall policy. The backend never connects inbound to the VPS.
You need the VPS IP, Administrator credentials, the repository release archive,
the production backend URL, and the worker shared secret from Railway.

## Install

1. Connect with Remote Desktop and run Windows Update.
2. Install each broker terminal into a unique directory, for example
   `C:\MT5\NODE-01\terminal64.exe`. Start it once and accept broker updates.
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
  -TerminalPaths @("C:\MT5\NODE-01\terminal64.exe") `
  -WorkerId "NODE-01"
.\scripts\windows\start.ps1
.\scripts\windows\health.ps1
```

The scheduled task runs as SYSTEM at boot and restarts after failure. The config
file ACL is restricted to SYSTEM and Administrators. Do not email or log it.

## Network and capacity

Allow outbound HTTPS 443 to the Bectanse backend and broker traffic required by
MT5. Do not create an inbound worker endpoint. Keep
`MAX_CONCURRENT_MT5_SESSIONS` at or above `MT5_WORKER_COUNT`, with one terminal
path per slot. Scale by adding nodes, not one VPS per customer.

## Replacement

Prepare a fresh VPS, copy the same release, reinstall terminals, run bootstrap
and configure with a new worker ID, verify its heartbeat, then stop the old node.
No database or MT5 credential export is stored on the worker.
