# First real account validation

This checklist is the release gate from Level 2 to Level 5. Record timestamps,
masked account ID and reconciliation report ID. Never paste a password.

- [ ] Windows VPS online and patched
- [ ] Worker heartbeat online in `/health/workers`
- [ ] Dedicated MT5 terminal online for each worker slot
- [ ] Investor/read-only password accepted
- [ ] Account login and server match the requested account
- [ ] Balance matches MT5
- [ ] Equity matches MT5
- [ ] Deal count matches for the selected period
- [ ] Net P&L matches within 0.01 of the account currency
- [ ] Daily calendar matches MT5 in the member timezone
- [ ] A newly closed trade appears after the incremental cycle
- [ ] Stop the worker and confirm its lease expires
- [ ] Close a test trade while the worker is stopped
- [ ] Restart the worker and confirm the missed deal is recovered
- [ ] Run daily reconciliation and obtain `PASS`
- [ ] Run deep reconciliation and obtain `PASS`
- [ ] Re-run the same range and confirm no duplicate deal
- [ ] Confirm only the investor password was supplied
- [ ] Run the static execution-API test and confirm no execution capability

Commands on the VPS:

```powershell
C:\Bectanse\MT5Worker\scripts\test-mt5.ps1 -TerminalPath "C:\MT5\NODE-01\terminal64.exe"
C:\Bectanse\MT5Worker\scripts\health.ps1
```

Comparison from an authorized maintenance shell:

```powershell
$env:DATABASE_URL = "<temporary maintenance URL>"
C:\Bectanse\MT5Worker\venv\Scripts\python.exe C:\Bectanse\MT5Worker\tools\compare_mt5_vs_db.py 123 --days 7 --terminal-path "C:\MT5\NODE-01\terminal64.exe"
Remove-Item Env:DATABASE_URL
```
