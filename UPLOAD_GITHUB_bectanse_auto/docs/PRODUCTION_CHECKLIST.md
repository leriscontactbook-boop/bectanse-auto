# Production checklist

## Backend and database

- [ ] Production config validation passes
- [ ] Idempotent migrations pass on a production-like PostgreSQL database
- [ ] Railway `/health` and `/health/database` return `ok`
- [ ] Deal uniqueness and active-job uniqueness constraints exist
- [ ] PostgreSQL backup retention and restore procedure are verified

## Security

- [ ] AES-256-GCM master key is present only in Railway secrets
- [ ] Worker secret is at least 32 random characters
- [ ] HMAC timestamp and nonce replay protection pass
- [ ] Static read-only test finds no MT5 execution API calls
- [ ] Worker accepts only HTTPS and stores no account credential
- [ ] Production refuses `TRADING_PROVIDER=mock`

## Synchronization

- [ ] Lease recovery, overlap, retry and circuit-breaker tests pass
- [ ] Five-minute incremental scheduling is active
- [ ] Seven-day daily reconciliation is active
- [ ] Ninety-day weekly reconciliation is active
- [ ] Worker heartbeat and queue monitoring are visible

## Product and billing

- [ ] Academy member receives `ACADEMY_INCLUDED`
- [ ] Standalone PRO and ELITE Stripe Price IDs are configured
- [ ] Stripe webhook redelivery is idempotent
- [ ] Academy activation schedules standalone cancellation at period end
- [ ] Journal and Coach paywalls match effective entitlements
- [ ] Stripe Tax is enabled only after required tax registrations are active

## Real account release gate

- [ ] Every item in `FIRST_REAL_ACCOUNT_VALIDATION.md` is signed off
- [ ] Failure recovery test is recorded
- [ ] MT5-vs-database comparison returns zero missing/extra and P&L delta ≤ 0.01
