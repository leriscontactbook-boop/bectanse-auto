# MT5 Windows Worker — Deployment and Production Runbook

## 1. Infrastructure

Provision a dedicated Windows Server 2022 VPS close to the brokers you expect to support. The machine needs:

- outbound HTTPS access to the Bectanse API;
- outbound access required by MetaTrader 5 to broker servers;
- no public inbound application port;
- a dedicated non-administrator Windows service account after installation;
- encrypted system disk, automatic security updates and restricted RDP access.

Do not install this worker on the Linux/Railway web service. The official `MetaTrader5` Python package communicates with a Windows MetaTrader terminal.

## 2. Install MetaTrader 5

1. Download the MT5 terminal from MetaQuotes or, preferably, the target broker's official website.
2. Install the first terminal in a dedicated directory such as `C:\MT5\Terminal-01`.
3. Open it once, accept updates, confirm that the expected broker server is searchable, then close it.
4. For each additional parallel worker, install a fully separate terminal directory (`Terminal-02`, `Terminal-03`, and so on).
5. Do not install an EA. Do not save a customer password in a terminal profile.

The worker passes login, server and password to `initialize()` for each short-lived job and calls `shutdown()` afterward.

## 3. Install Python and the worker

Install 64-bit Python 3.13 for all users, including the Python launcher. In an elevated PowerShell:

```powershell
Set-Location C:\Bectanse\app
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-mt5-worker.txt
```

The worker dependency file pins the current tested Windows package version. Validate upgrades on staging before changing the pin.

## 4. Configure API secrets

Generate two independent secrets on an administrator workstation:

```powershell
py -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
py -c "import secrets; print(secrets.token_urlsafe(48))"
```

- Store the first value as `MT5_CREDENTIAL_MASTER_KEY` in the Railway/API secret vault only.
- Store the second value as `INTERNAL_WORKER_SECRET` in both the Railway/API secret vault and the protected Windows worker environment.
- Never put either value in PostgreSQL, Git, a support ticket or worker logs.

The worker does not need the credential master key. It receives a credential only after a signed job claim, over HTTPS, and keeps it in process memory for that job.

## 5. Configure the Windows worker

Set machine-scoped variables from an elevated PowerShell. Replace values with the production host and actual terminal paths:

```powershell
[Environment]::SetEnvironmentVariable('BACKEND_URL','https://acces.bectanse-academie.com','Machine')
[Environment]::SetEnvironmentVariable('INTERNAL_WORKER_SECRET','REPLACE_WITH_RANDOM_SECRET','Machine')
[Environment]::SetEnvironmentVariable('WORKER_ID','MT5-WORKER-NODE-01','Machine')
[Environment]::SetEnvironmentVariable('MT5_WORKER_COUNT','2','Machine')
[Environment]::SetEnvironmentVariable('MT5_TERMINAL_PATHS','C:\MT5\Terminal-01\terminal64.exe;C:\MT5\Terminal-02\terminal64.exe','Machine')
[Environment]::SetEnvironmentVariable('MT5_CONNECT_TIMEOUT_MS','45000','Machine')
[Environment]::SetEnvironmentVariable('MT5_WORKER_POLL_SECONDS','5','Machine')
[Environment]::SetEnvironmentVariable('MT5_REQUIRE_READ_ONLY','true','Machine')
```

Open a new PowerShell session after setting machine variables.

## 6. Start and smoke-test

Run in the foreground first:

```powershell
Set-Location C:\Bectanse\app
.\.venv\Scripts\python.exe -m trading_journal.worker
```

Expected first event: `worker_pool_started`. With no queued account, the worker remains quiet and polls. It must never print a login password or API secret.

## 7. Run automatically at boot

Use Windows Task Scheduler under the dedicated service account:

1. Create a task named `Bectanse MT5 Worker`.
2. Trigger: `At startup`, with a 30-second delay.
3. Program: `C:\Bectanse\app\.venv\Scripts\python.exe`.
4. Arguments: `-m trading_journal.worker`.
5. Start in: `C:\Bectanse\app`.
6. Enable `Run whether user is logged on or not` and `Restart every 1 minute` after failure.
7. Do not grant interactive login or local administrator rights to the service account after setup.

Send stdout/stderr to the platform's Windows log collector. Alert on repeated `sync_failed`, no `worker_pool_started` after reboot, and jobs with repeated `WORKER_TIMEOUT`.

## 8. Deploy the backend

1. Add the new API variables from `.env.example` to Railway's secret store.
2. Deploy the Docker service. Its start command runs the idempotent journal migration before Gunicorn.
3. The main dependency list intentionally excludes the Windows-only MT5 package.
4. Verify `/health` and sign in with a test member.
5. Open `/journal`, add the broker test account with its investor password and start the Windows worker.

## 9. Live broker acceptance test

Use a dedicated demo account first.

1. Create at least two closed positions: one winner and one loser.
2. Ensure one position has a partial close if the broker supports it.
3. Record the MT5 History totals for profit, commission, swap and fee.
4. Connect with the investor password in Bectanse.
5. Confirm the UI moves through connection, import and synced states.
6. Compare balance, equity, currency and masked login with MT5.
7. Compare the calendar net P&L with the MT5 deal total.
8. Confirm a deposit or withdrawal appears in raw storage but not as a trade or performance P&L.
9. Trigger manual sync twice. The second request must be rate-limited/cooldown-protected and deal count must not duplicate.
10. Change timezone between Europe/Paris and America/New_York around a midnight trade and confirm the displayed trading day changes correctly.
11. Try another user's account ID against every user endpoint and confirm `404`.
12. Attempt a master/trading password while `MT5_REQUIRE_READ_ONLY=true`; the worker must reject it with the investor-password message.

## 10. Production monitoring

Track these event names from `trading_sync_events` and worker logs:

- `sync_started`
- `sync_success`
- `sync_failed`
- imported deal count
- sync duration
- worker loop failures
- authentication failures

Operational alerts:

- no successful sync from a worker node for 10 minutes;
- more than three consecutive failures for one account;
- p95 initial import duration above the expected history size;
- pending job age above two sync intervals;
- database growth or query latency on account/time indexes.

## 11. Rotation and incident response

- Rotate `INTERNAL_WORKER_SECRET` on the API and all workers in one maintenance window.
- Rotate `MT5_CREDENTIAL_MASTER_KEY` only with a controlled re-encryption migration; changing it directly makes existing credentials unreadable.
- If a worker host is compromised, stop it, rotate the worker secret, revoke the machine's access, and ask affected users to rotate investor passwords.
- Credentials are erased when an account is disconnected. Full deletion cascades through deals, snapshots, jobs and sync events.

## 12. Adding capacity

For a second Windows node, deploy the same code, assign a unique `WORKER_ID`, create distinct terminal directories and reuse the signed claim protocol. No account sharding configuration is required in V1: PostgreSQL row locking distributes jobs safely.
