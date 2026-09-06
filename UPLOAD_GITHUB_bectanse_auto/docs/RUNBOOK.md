# Bectanse Journal production runbook

Never include a full login, password, encryption key or worker secret in a ticket.

| Incident | Symptom and diagnostic | Repair | Verification |
|---|---|---|---|
| Worker offline | `/health/workers` reports offline; run `status.ps1` | Run `restart.ps1`; replace node if heartbeat stays absent | Heartbeat online within 30 seconds |
| MT5 frozen | Worker logs `TERMINAL_ERROR`; terminal unresponsive | Run `restart.ps1`; open terminal once if broker update is pending | Real read-only diagnostic passes |
| Broker outage | Many `BROKER_UNAVAILABLE` for one server | Leave circuit breaker open; contact broker; do not retry manually in a loop | Circuit closes and a sync succeeds |
| Authentication error | Account status `AUTH_ERROR` | Member supplies a new investor password | Manual sync reaches `SYNCED` |
| Queue blocked | Oldest pending exceeds 15 minutes | Check worker capacity and DB; retry only dead jobs after cause is fixed | Queue age falls and success counter rises |
| Database unavailable | `/health/database` returns 503 | Use Railway incident view and restore connectivity | Migration and `SELECT 1` pass |
| Sync delayed | UI shows `SYNC DELAYED` | Check worker and queue, then force reconciliation | Account integrity returns `HEALTHY` |
| Reconciliation mismatch | Report status `MISMATCH` | Run admin deep reconciliation; compare MT5 vs DB | Count and P&L delta match |

Admin repair endpoints require the existing admin session:

```text
POST /admin/trading/jobs/{job_id}/retry
POST /admin/trading/accounts/{account_id}/reconcile  {"days":90}
```

## Disaster recovery

Use Railway/PostgreSQL backups according to the active infrastructure plan;
verify the provider retention in the Railway dashboard before launch. Restore to
a separate database first, run migrations, compare row counts, then switch the
application. Workers are disposable and can be rebuilt from the scripts.
Credentials stay encrypted in PostgreSQL; a database restore is unusable without
the corresponding master-key version.

## Key rotation

Keep `MT5_CREDENTIAL_KEY_VERSION`. Add the previous key as a temporary secret,
decrypt and re-encrypt each credential in a controlled maintenance job, verify a
sample, then retire the old key only after all rows report the new version. Never
rotate by replacing the secret before re-encryption.
