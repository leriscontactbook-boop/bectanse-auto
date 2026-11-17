# Railway deployment recovery

## Production baseline

This recovery branch starts from `267e6026ad6ef9c279a2415912313e405ee99895` and restores Python, HTML, JavaScript and CSS files read from the running Bectanse container on 2026-11-17. Of 344 inspected production files, 238 were missing from that Git branch. The current Telegram worker is recovered separately from its own running service.

This is a recovery candidate, not an approved production release. Static binary assets, JSON content, SQL files and downloadable releases still need reconciliation against the running container. No production database or credentials are included. The source-only recovery is deliberately kept separate from the production branch.

## Canonical delivery paths

| Service | Repository root directory | Configuration file | Build context |
| --- | --- | --- | --- |
| Web production | `/UPLOAD_GITHUB_bectanse_auto` | `/railway.json` | `UPLOAD_GITHUB_bectanse_auto` |
| Web staging smoke test | `/UPLOAD_GITHUB_bectanse_auto` | `/railway.staging.json` | `UPLOAD_GITHUB_bectanse_auto` |
| Telegram worker | `/UPLOAD_GITHUB_bectanse_auto/deploy/telegram-scheduler` | Dedicated worker settings | Worker directory only |

Deploy from GitHub with an identified commit. Do not upload a flattened subdirectory into a service expecting the full repository layout. The root and application railway.json files must remain identical; the deployment check rejects divergence.

Web package versions are recorded in requirements.lock from the observed production installation. The worker uses its separately validated pg8000/scramp pair. The Python base-image tag still needs immutable digest pinning after Linux image validation.

## Isolated staging smoke test

The staging environment starts with no copied production variables, database or volumes. Set BECTANSE_SKIP_STARTUP=1 and use disposable placeholder credentials with DATABASE_URL pointing to localhost. Do not supply real payment, email, Telegram or trading credentials. The staging command deliberately skips migrations and background startup.

`/live` confirms only that the application imports and serves HTTP, and reports the Git commit when Railway supplies it. `/health` remains the production database/scheduler readiness check and is expected to return 503 in this smoke-test environment. This is not full functional staging. A dedicated test database and synthetic fixtures must be added before business workflows can be validated.

## Promotion gates

1. Reconcile remaining production assets and data-independent configuration.
2. Reconcile the legacy test suite with the current application interfaces. Current collection errors reference removed register_copy_trading and CopyTradingService symbols; do not suppress these errors or add compatibility shims just to make tests green.
3. Obtain passing Linux builds, dependency checks and regression tests.
4. Provision an isolated staging database and validate migrations and representative business workflows with external effects disabled.
5. Record a database backup and the current production deployment for rollback. Database migration rollback requires its own review; restoring an image does not undo schema changes.
6. Only then remove the production startup copies from `/app/persistent/restore-promo-20261114`. Preserve the intended current behavior in Git first.
7. Promote the tested commit, verify `/health` and business indicators, and monitor errors before declaring completion.

Production currently uses `/health`; never replace it with `/live` to conceal database or scheduler failures. The existing GitHub branch and live production startup command are intentionally untouched by this recovery branch.

## Other projects

BECTANSE and LEBONPLUG are the clarified project names. Bectanse and modest-growth each have an empty staging environment created without duplication. Lebonplug's existing staging hosts the current application and must not be treated as disposable.

Pending external actions: identify the DNS provider and restore api.lebonplug.com, resolve the API-Football account suspension, identify the sporting application's name and source repository, and choose alert destinations. Do not delete the UPLOAD_GITHUB_bectanse_auto project until its role is confirmed.
