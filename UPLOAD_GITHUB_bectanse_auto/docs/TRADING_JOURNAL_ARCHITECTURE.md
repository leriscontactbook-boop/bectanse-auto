# Bectanse Automatic Trading Journal — Architecture V1

## Product promise

The user connects a MetaTrader 5 account once with an investor/read-only password. Bectanse then builds and refreshes the journal without an EA, CSV import, local application, or an open terminal on the user's computer.

## Existing repository audit

- Web application: Flask 3/Jinja, with server-rendered pages and small vanilla JavaScript modules.
- Authentication: existing `members` session (`member_code`) and `login_required`; the journal does not introduce a second identity system.
- Database: PostgreSQL through `pg8000.native`; schema changes are additive and startup-safe.
- Billing: Stripe Checkout, webhook processing and Customer Portal already exist for Academy subscriptions. Existing paid Academy members inherit journal PRO rights.
- Infrastructure: Railway, Docker and Gunicorn for the web application. The worker is intentionally a separate Windows process.
- Security conventions: secure session cookies, same-origin mutation checks, authenticated encryption for existing sensitive data and server-side ownership filters.
- Tests: pytest with mocked database/provider dependencies; no live MetaTrader dependency in CI.
- Existing manual journal: preserved at `/journal/manual`. The automatic journal owns `/journal`.

## Runtime architecture

```text
Browser / installed web app
        |
        | member session + same-origin API
        v
Flask SaaS application on Railway
  - account API
  - calendar/stats/analytics API
  - entitlement checks
  - AES-256-GCM credential vault
  - PostgreSQL job orchestrator
        |
        | PostgreSQL (source of truth)
        v
trading_accounts / trading_deals / trading_sync_jobs / snapshots
        ^
        | HTTPS + timestamped HMAC request signature
        |
Windows MT5 worker pool (outbound connection only)
  - one distinct terminal installation per worker thread
  - MetaQuotes MetaTrader5 Python package
  - read-only provider methods only
        |
        v
MetaTrader 5 terminals -> broker MT5 servers
```

The worker has no public endpoint and needs no inbound firewall rule. It polls the main API over HTTPS, claims one PostgreSQL-backed job, decrypts credentials only for the short-lived response, connects to the broker, uploads normalized deals in batches, and closes the terminal session.

## Modules

- `trading_journal/schema.py`: additive production schema.
- `trading_journal/security.py`: AES-256-GCM and HMAC signing.
- `trading_journal/entitlements.py`: Academy-included and standalone PRO/ELITE rights in one place.
- `trading_journal/coach.py`: deterministic behavioral detectors, reviews and scoring.
- `trading_journal/providers/base.py`: provider-neutral contract and data records.
- `trading_journal/providers/mt5.py`: MT5 read-only implementation.
- `trading_journal/providers/mock.py`: deterministic CI implementation; never selected by production routes.
- `trading_journal/service.py`: ownership, sync queue, idempotent import and metrics.
- `trading_journal/routes.py`: user API and private worker API.
- `trading_journal/worker.py`: configurable Windows worker pool.

Future providers implement `TradingProvider.connect()`, `validate_credentials()`, `get_account_info()`, `get_deals()` and `disconnect()` without changing the web application or analytics.

## Data model

### `trading_accounts`

One user can own one or several provider accounts according to entitlement. Password bytes, nonce, authentication tag and key version live only in `trading_credentials`. The API serializer has an explicit allowlist and never returns those columns or the full login.

The uniqueness key `(user_id, provider, server, login)` prevents accidental duplicate connections. `organization_id` is nullable groundwork for a future workspace/white-label layer.

### `trading_deals`

All MT5 history operations are stored, including non-trading balance operations, because they are useful for reconciliation. `is_trading_deal` controls performance calculations. The uniqueness constraint `(trading_account_id, mt5_deal_ticket)` makes overlapping and repeated synchronization idempotent.

Indexes cover account/time calendar scans and position reconstruction.

### `trading_sync_jobs`

PostgreSQL is the V1 queue. A partial unique index allows only one `PENDING`, `LEASED`, `RUNNING` or `RETRY` job per account. Workers use `FOR UPDATE SKIP LOCKED` when claiming. Expiring leases recover abandoned jobs; retry delays are 30 seconds, 2 minutes, 5 minutes, 15 minutes and 1 hour before `DEAD`.

This removes the need for Redis in V1 while preserving a clean boundary for a later queue adapter.

### Other tables

- `trading_profiles`: display timezone and onboarding state.
- `trading_subscriptions`: standalone journal plan/status fields without coupling rights to prices.
- `trading_account_snapshots`: balance/equity snapshots after successful sync.
- `trading_sync_events`: credential-free operational events and durations.

## Synchronization flow

1. The account endpoint validates the platform, login, server and credential shape.
2. Entitlements enforce the account limit.
3. The password is sealed with AES-256-GCM using account/user identity as authenticated data.
4. A `FULL_HISTORY_SYNC` job is inserted atomically with the account.
5. A worker claims the job through the signed internal API.
6. MT5 is initialized for that account. If `trade_allowed` is true and `MT5_REQUIRE_READ_ONLY=true`, the credential is rejected.
7. History is requested month by month and uploaded in batches of at most 1,000 deals.
8. The API validates every deal and upserts it on the stable MT5 ticket key.
9. Account information and a balance/equity snapshot are saved.
10. The account becomes `SYNCED`; the scheduler creates incremental, daily 7-day and weekly 90-day reconciliation jobs when due.

Incremental jobs begin at `last_successful_sync_at - MT5_SYNC_OVERLAP_SECONDS`. The overlap handles late broker events and clock differences; the unique deal key prevents duplication.

## P&L and trade definitions

- Net P&L is `profit + commission + swap + fee`, preserving MT5's signs.
- Deposits, withdrawals, credits, bonuses and balance operations are stored but excluded from trade performance.
- `deals` means individual MT5 executions.
- The primary UI metric `positions`/`trades` means distinct closed MT5 positions, reconstructed from `IN`, `OUT`, `OUT_BY` and `INOUT` legs.
- Partial closes remain one closed position; all their legs contribute to its net P&L.
- Calendar net P&L and trade count are assigned to the local day of the final closing leg.
- All timestamps are stored in UTC. Calendar grouping converts them with the user's IANA timezone.
- Monthly return uses month trading P&L divided by the reconciled opening balance. Opening balance is derived from the current balance minus every balance-affecting MT5 deal in the month. It is not presented when the denominator is zero.

## API

User routes all require the existing session and apply `user_id` ownership in SQL:

- `POST /api/trading/accounts/connect`
- `GET /api/trading/accounts`
- `GET /api/trading/accounts/:id`
- `DELETE /api/trading/accounts/:id`
- `POST /api/trading/accounts/:id/sync`
- `PATCH /api/trading/profile`
- `GET /api/trading/calendar`
- `GET /api/trading/days/:date`
- `GET /api/trading/stats`
- `GET /api/trading/equity`
- `GET /api/trading/analytics`

Private routes require a timestamped HMAC-SHA256 signature over method, path, one-time nonce and body digest:

- `POST /internal/mt5/jobs/claim`
- `POST /internal/mt5/jobs/:id/heartbeat`
- `POST /internal/mt5/jobs/:id/batch`
- `POST /internal/mt5/jobs/:id/complete`
- `POST /internal/mt5/jobs/:id/fail`

## Subscription foundation

Plan behavior lives in `entitlements.py`:

| Plan | Accounts | History | Advanced analytics | Export |
| --- | ---: | --- | --- | --- |
| JOURNAL_PRO | 1 | Complete | No | No |
| JOURNAL_ELITE | 10 | Complete | Yes | Yes |
| ACADEMY_INCLUDED | Configurable (at least PRO) | Complete | Configurable | Configurable |

Prices, monthly/annual intervals, coupons and trials remain Stripe catalog concerns. They are not hardcoded into entitlement checks. Use one Stripe Product per tier and separate monthly/annual Prices for the same Product. Tax collection must only be enabled after the appropriate Stripe Tax registrations are active.

## Security controls

- AES-256-GCM master key comes only from the API runtime secret store.
- Ciphertext, 96-bit nonce and 128-bit authentication tag are separate database values.
- Account identity is authenticated data, preventing ciphertext from being moved between users/accounts.
- Worker requests expire after 90 seconds and use constant-time signature comparison.
- Internal credential responses are `Cache-Control: no-store` and require HTTPS.
- User serializers use explicit safe fields.
- Connect, sync and delete mutations are rate-limited and protected by existing same-origin checks.
- No provider or endpoint contains `order_send` or any position/order mutation.
- Structured worker events contain account/job identifiers, counts and error codes, never credentials.

## Scaling path

- 100–1,000 users: one Windows node with several isolated terminal installations, one PostgreSQL database.
- 1,000–10,000 users: several Windows nodes using the same claim protocol. PostgreSQL `SKIP LOCKED` distributes work safely.
- 10,000+ users: replace the queue adapter with a managed queue, partition deals by account/time, add materialized daily/position aggregates and send workers to regional pools.
- 100,000 users: isolate the trading-data API, use tenant-aware data partitions, incremental aggregate tables and dedicated worker capacity registration.

## Known V1 limitations

- A real broker connection must be certified on Windows; CI uses `MockTradingProvider` only.
- Each parallel worker requires a distinct terminal installation. Sharing one terminal process between simultaneous accounts is unsupported.
- The in-process user rate limiter is appropriate for the initial Railway topology. A distributed limiter is needed when the web tier scales horizontally.
- Large lifetime analytics currently calculate on the API from indexed deal rows. Introduce materialized aggregates before histories reach hundreds of thousands of deals per account.
- Financial aggregation across different account currencies is rejected; no approximate FX conversion is performed.
- Exotic MT5 netting histories with repeated `INOUT` reversals can require broker-specific position-cycle refinement. Raw deals remain preserved, so the reconstruction algorithm can evolve without reimporting data.

## Verification

Run the full suite:

```bash
python -m pytest -q
```

Run syntax checks:

```bash
python -m py_compile app.py academy_features.py trading_journal/*.py trading_journal/providers/*.py
node --check static/trading-journal.js
```

The live Windows acceptance procedure is in `docs/MT5_WORKER_DEPLOYMENT.md`.
