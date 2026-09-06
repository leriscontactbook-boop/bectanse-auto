"""Additive PostgreSQL schema for the automatic trading journal."""

from __future__ import annotations


STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS trading_profiles (
        user_id TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
        onboarding_completed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS trading_subscriptions (
        user_id TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        organization_id TEXT,
        product TEXT NOT NULL DEFAULT 'JOURNAL',
        plan TEXT NOT NULL DEFAULT 'JOURNAL_PRO',
        subscription_status TEXT NOT NULL DEFAULT 'inactive',
        stripe_customer_id TEXT NOT NULL DEFAULT '',
        stripe_subscription_id TEXT NOT NULL DEFAULT '',
        stripe_price_id TEXT NOT NULL DEFAULT '',
        cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
        payment_failed_at TIMESTAMPTZ,
        current_period_end TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """ALTER TABLE trading_subscriptions
        ADD COLUMN IF NOT EXISTS product TEXT NOT NULL DEFAULT 'JOURNAL'""",
    """ALTER TABLE trading_subscriptions
        ADD COLUMN IF NOT EXISTS stripe_price_id TEXT NOT NULL DEFAULT ''""",
    """ALTER TABLE trading_subscriptions
        ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE trading_subscriptions
        ADD COLUMN IF NOT EXISTS payment_failed_at TIMESTAMPTZ""",
    """ALTER TABLE trading_subscriptions ALTER COLUMN plan SET DEFAULT 'JOURNAL_PRO'""",
    """UPDATE trading_subscriptions SET plan='JOURNAL_PRO',subscription_status='inactive',updated_at=NOW()
        WHERE plan='FREE'""",
    """CREATE UNIQUE INDEX IF NOT EXISTS trading_subscriptions_stripe_subscription_idx
        ON trading_subscriptions (stripe_subscription_id)
        WHERE stripe_subscription_id <> ''""",
    """CREATE INDEX IF NOT EXISTS trading_subscriptions_customer_idx
        ON trading_subscriptions (stripe_customer_id)
        WHERE stripe_customer_id <> ''""",
    """CREATE TABLE IF NOT EXISTS product_entitlements (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        feature_key TEXT NOT NULL,
        source TEXT NOT NULL,
        source_reference TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        valid_until TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, feature_key, source, source_reference)
    )""",
    """CREATE INDEX IF NOT EXISTS product_entitlements_active_idx
        ON product_entitlements (user_id, feature_key, valid_until)
        WHERE status = 'ACTIVE'""",
    """CREATE TABLE IF NOT EXISTS stripe_journal_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        stripe_object_id TEXT NOT NULL DEFAULT '',
        subscription_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'processing',
        error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        processed_at TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS stripe_journal_events_status_idx
        ON stripe_journal_events (status, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_accounts (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        organization_id TEXT,
        provider TEXT NOT NULL DEFAULT 'MT5',
        platform TEXT NOT NULL DEFAULT 'MT5',
        display_name TEXT NOT NULL DEFAULT '',
        login TEXT NOT NULL,
        login_masked TEXT NOT NULL,
        broker TEXT NOT NULL DEFAULT '',
        server TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT '',
        account_type TEXT NOT NULL DEFAULT '',
        balance NUMERIC(24,8),
        equity NUMERIC(24,8),
        margin NUMERIC(24,8),
        free_margin NUMERIC(24,8),
        leverage INTEGER,
        access_mode TEXT NOT NULL DEFAULT 'PENDING',
        status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION',
        sync_status TEXT NOT NULL DEFAULT 'PENDING',
        encrypted_password BYTEA,
        credential_nonce BYTEA,
        credential_tag BYTEA,
        last_sync_at TIMESTAMPTZ,
        last_successful_sync_at TIMESTAMPTZ,
        last_manual_sync_at TIMESTAMPTZ,
        last_reconciliation_at TIMESTAMPTZ,
        last_deep_reconciliation_at TIMESTAMPTZ,
        last_error_code TEXT NOT NULL DEFAULT '',
        last_error_message TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, provider, server, login)
    )""",
    """ALTER TABLE trading_accounts ADD COLUMN IF NOT EXISTS account_type TEXT NOT NULL DEFAULT ''""",
    """ALTER TABLE trading_accounts ADD COLUMN IF NOT EXISTS last_reconciliation_at TIMESTAMPTZ""",
    """ALTER TABLE trading_accounts ADD COLUMN IF NOT EXISTS last_deep_reconciliation_at TIMESTAMPTZ""",
    """CREATE TABLE IF NOT EXISTS trading_credentials (
        trading_account_id BIGINT PRIMARY KEY REFERENCES trading_accounts(id) ON DELETE CASCADE,
        encrypted_password BYTEA NOT NULL,
        nonce BYTEA NOT NULL,
        auth_tag BYTEA NOT NULL,
        key_version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """INSERT INTO trading_credentials
        (trading_account_id,encrypted_password,nonce,auth_tag,key_version)
        SELECT id,encrypted_password,credential_nonce,credential_tag,1
        FROM trading_accounts WHERE encrypted_password IS NOT NULL
          AND credential_nonce IS NOT NULL AND credential_tag IS NOT NULL
        ON CONFLICT (trading_account_id) DO NOTHING""",
    """UPDATE trading_accounts SET encrypted_password=NULL,credential_nonce=NULL,credential_tag=NULL
        WHERE encrypted_password IS NOT NULL OR credential_nonce IS NOT NULL OR credential_tag IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS trading_accounts_user_idx
        ON trading_accounts (user_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS trading_accounts_due_idx
        ON trading_accounts (status, last_successful_sync_at)
        WHERE status <> 'DISCONNECTED'""",
    """CREATE TABLE IF NOT EXISTS trading_deals (
        id BIGSERIAL PRIMARY KEY,
        trading_account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        mt5_deal_ticket BIGINT NOT NULL,
        mt5_order_ticket BIGINT,
        mt5_position_id BIGINT,
        symbol TEXT NOT NULL DEFAULT '',
        deal_type TEXT NOT NULL,
        entry_type TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        is_trading_deal BOOLEAN NOT NULL DEFAULT TRUE,
        volume NUMERIC(24,8) NOT NULL DEFAULT 0,
        price NUMERIC(24,8) NOT NULL DEFAULT 0,
        profit NUMERIC(24,8) NOT NULL DEFAULT 0,
        commission NUMERIC(24,8) NOT NULL DEFAULT 0,
        swap NUMERIC(24,8) NOT NULL DEFAULT 0,
        fee NUMERIC(24,8) NOT NULL DEFAULT 0,
        magic_number BIGINT,
        comment TEXT NOT NULL DEFAULT '',
        executed_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (trading_account_id, mt5_deal_ticket)
    )""",
    """CREATE INDEX IF NOT EXISTS trading_deals_account_time_idx
        ON trading_deals (trading_account_id, executed_at DESC)""",
    """CREATE INDEX IF NOT EXISTS trading_deals_account_position_idx
        ON trading_deals (trading_account_id, mt5_position_id, executed_at)
        WHERE is_trading_deal = TRUE""",
    """CREATE INDEX IF NOT EXISTS trading_deals_calendar_idx
        ON trading_deals (trading_account_id, executed_at)
        WHERE is_trading_deal = TRUE""",
    """CREATE TABLE IF NOT EXISTS trading_account_snapshots (
        id BIGSERIAL PRIMARY KEY,
        trading_account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        balance NUMERIC(24,8),
        equity NUMERIC(24,8),
        margin NUMERIC(24,8),
        free_margin NUMERIC(24,8),
        captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_snapshots_account_time_idx
        ON trading_account_snapshots (trading_account_id, captured_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_sync_jobs (
        id TEXT PRIMARY KEY,
        trading_account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        job_type TEXT NOT NULL DEFAULT 'INCREMENTAL',
        status TEXT NOT NULL DEFAULT 'PENDING',
        priority INTEGER NOT NULL DEFAULT 10,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 5,
        not_before TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        worker_id TEXT NOT NULL DEFAULT '',
        instance_id TEXT NOT NULL DEFAULT '',
        locked_at TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ,
        lease_expires_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        range_from TIMESTAMPTZ,
        range_to TIMESTAMPTZ,
        imported_deals INTEGER NOT NULL DEFAULT 0,
        received_deals INTEGER NOT NULL DEFAULT 0,
        last_error_code TEXT NOT NULL DEFAULT '',
        last_error_message TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS range_from TIMESTAMPTZ""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS range_to TIMESTAMPTZ""",
    """ALTER TABLE trading_sync_jobs ADD COLUMN IF NOT EXISTS received_deals INTEGER NOT NULL DEFAULT 0""",
    """DROP INDEX IF EXISTS trading_sync_one_active_account_idx""",
    """CREATE UNIQUE INDEX IF NOT EXISTS trading_sync_one_active_account_idx
        ON trading_sync_jobs (trading_account_id)
        WHERE status IN ('PENDING','LEASED','RUNNING','RETRY')""",
    """DROP INDEX IF EXISTS trading_sync_claim_idx""",
    """CREATE INDEX IF NOT EXISTS trading_sync_claim_idx
        ON trading_sync_jobs (status, not_before, priority DESC, created_at)
        WHERE status IN ('PENDING','RETRY')""",
    """CREATE TABLE IF NOT EXISTS trading_sync_runs (
        id TEXT PRIMARY KEY,
        sync_job_id TEXT NOT NULL REFERENCES trading_sync_jobs(id) ON DELETE CASCADE,
        trading_account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        worker_id TEXT NOT NULL,
        job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'RUNNING',
        range_from TIMESTAMPTZ,
        range_to TIMESTAMPTZ,
        deals_received INTEGER NOT NULL DEFAULT 0,
        deals_inserted INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT '',
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS trading_sync_runs_account_idx
        ON trading_sync_runs (trading_account_id, started_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_sync_batches (
        sync_job_id TEXT NOT NULL REFERENCES trading_sync_jobs(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL,
        received_count INTEGER NOT NULL,
        inserted_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (sync_job_id, batch_id)
    )""",
    """CREATE TABLE IF NOT EXISTS trading_reconciliation_reports (
        id BIGSERIAL PRIMARY KEY,
        trading_account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        sync_job_id TEXT NOT NULL,
        range_from TIMESTAMPTZ NOT NULL,
        range_to TIMESTAMPTZ NOT NULL,
        mt5_count INTEGER NOT NULL DEFAULT 0,
        db_count INTEGER NOT NULL DEFAULT 0,
        missing_count INTEGER NOT NULL DEFAULT 0,
        repaired_count INTEGER NOT NULL DEFAULT 0,
        pnl_delta NUMERIC(24,8) NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_reconciliation_account_idx
        ON trading_reconciliation_reports (trading_account_id, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_sync_events (
        id BIGSERIAL PRIMARY KEY,
        trading_account_id BIGINT REFERENCES trading_accounts(id) ON DELETE CASCADE,
        sync_job_id TEXT,
        event_name TEXT NOT NULL,
        worker_id TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER,
        deals_count INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_sync_events_account_idx
        ON trading_sync_events (trading_account_id, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_audit_logs (
        id BIGSERIAL PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        trading_account_id BIGINT REFERENCES trading_accounts(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT 'SUCCESS',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_audit_user_idx
        ON trading_audit_logs (user_id, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_server_circuits (
        server TEXT PRIMARY KEY,
        failure_count INTEGER NOT NULL DEFAULT 0,
        window_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        opened_until TIMESTAMPTZ,
        last_error_code TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS trading_workers (
        worker_id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ONLINE',
        version TEXT NOT NULL DEFAULT '',
        terminal_fingerprint TEXT NOT NULL DEFAULT '',
        current_job_id TEXT NOT NULL DEFAULT '',
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_workers_seen_idx
        ON trading_workers (last_seen_at DESC)""",
    """CREATE TABLE IF NOT EXISTS trading_worker_nonces (
        nonce TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS trading_worker_nonces_created_idx
        ON trading_worker_nonces (created_at)""",
)


def ensure_trading_schema(conn) -> None:
    for statement in STATEMENTS:
        conn.run(statement)
