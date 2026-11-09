"""Additive PostgreSQL schema for the separate copy-trading execution path."""

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS copy_trading_master_accounts (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_consents (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        consent_version TEXT NOT NULL,
        consented_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_symbol_mappings (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        follower_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        master_symbol TEXT NOT NULL,
        follower_symbol TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING_REVIEW'
            CHECK (state IN ('PENDING_REVIEW','VERIFIED','REJECTED')),
        native_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        evidence_hash TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT NOT NULL DEFAULT '',
        reviewed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code, follower_member_code, master_symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_symbol_candidates (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        follower_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        master_symbol TEXT NOT NULL,
        follower_symbol TEXT NOT NULL,
        native_evidence JSONB NOT NULL,
        evidence_hash TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING_REVIEW'
            CHECK (state IN ('PENDING_REVIEW','VERIFIED','REJECTED')),
        reviewed_by TEXT NOT NULL DEFAULT '',
        reviewed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code,follower_member_code,master_symbol,follower_symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_master_positions (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        position_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        volume NUMERIC NOT NULL CHECK (volume > 0),
        price_open NUMERIC NOT NULL,
        stop_loss NUMERIC NOT NULL DEFAULT 0,
        take_profit NUMERIC NOT NULL DEFAULT 0,
        time_update_msc BIGINT NOT NULL DEFAULT 0,
        is_open BOOLEAN NOT NULL DEFAULT TRUE,
        missing_snapshots INTEGER NOT NULL DEFAULT 0,
        last_seen_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code, position_id)
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_native_symbols (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        symbol TEXT NOT NULL,
        evidence JSONB NOT NULL,
        evidence_hash TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (master_member_code, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_events (
        event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        idempotency_key TEXT NOT NULL UNIQUE,
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        position_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('OPEN','RECONCILE','CLOSE')),
        symbol TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        volume NUMERIC NOT NULL CHECK (volume > 0),
        price_open NUMERIC NOT NULL,
        stop_loss NUMERIC NOT NULL DEFAULT 0,
        take_profit NUMERIC NOT NULL DEFAULT 0,
        source_version TEXT NOT NULL,
        source_time TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL,
        payload_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'READY'
            CHECK (status IN ('READY','EXPIRED','COMPLETE','REJECTED')),
        CHECK (expires_at <= source_time + INTERVAL '120 seconds')
    )""",
    """CREATE INDEX IF NOT EXISTS copy_trading_events_ready_idx
        ON copy_trading_events (expires_at, created_at) WHERE status='READY'""",
    """CREATE TABLE IF NOT EXISTS copy_trading_jobs (
        job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_id UUID NOT NULL REFERENCES copy_trading_events(event_id) ON DELETE CASCADE,
        follower_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING','LEASED','PREPARED','DISPATCHING','COMPLETE','RETRY','EXPIRED','REJECTED','VERIFY_REQUIRED')),
        expires_at TIMESTAMPTZ NOT NULL,
        worker_id TEXT NOT NULL DEFAULT '',
        lease_token_hash TEXT NOT NULL DEFAULT '',
        lease_expires_at TIMESTAMPTZ,
        attempts INTEGER NOT NULL DEFAULT 0,
        academy_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        academy_settings_hash TEXT NOT NULL DEFAULT '',
        native_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        follower_symbol TEXT NOT NULL DEFAULT '',
        execution_action TEXT NOT NULL DEFAULT 'OPEN'
            CHECK (execution_action IN ('OPEN','CLOSE','PROTECT','RESIZE_OPEN','RESIZE_CLOSE','NOOP')),
        normalized_volume NUMERIC,
        request_comment TEXT NOT NULL DEFAULT '',
        dispatch_started_at TIMESTAMPTZ,
        broker_order_ticket TEXT NOT NULL DEFAULT '',
        broker_deal_ticket TEXT NOT NULL DEFAULT '',
        broker_retcode INTEGER,
        failure_code TEXT NOT NULL DEFAULT '',
        result_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (event_id, follower_member_code)
    )""",
    """CREATE INDEX IF NOT EXISTS copy_trading_jobs_claim_idx
        ON copy_trading_jobs (expires_at, created_at) WHERE status IN ('PENDING','RETRY','LEASED','PREPARED')""",
    """CREATE INDEX IF NOT EXISTS copy_trading_jobs_member_idx
        ON copy_trading_jobs (follower_member_code, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS copy_trading_position_links (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        follower_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        master_position_id TEXT NOT NULL,
        follower_position_id TEXT NOT NULL,
        master_symbol TEXT NOT NULL,
        follower_symbol TEXT NOT NULL,
        master_initial_volume NUMERIC NOT NULL CHECK (master_initial_volume > 0),
        follower_initial_volume NUMERIC NOT NULL CHECK (follower_initial_volume > 0),
        current_master_volume NUMERIC NOT NULL CHECK (current_master_volume >= 0),
        current_follower_volume NUMERIC NOT NULL DEFAULT 0 CHECK (current_follower_volume >= 0),
        state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','CLOSED','VERIFY_REQUIRED')),
        opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        closed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code, follower_member_code, master_position_id)
    )""",
    """ALTER TABLE copy_trading_position_links ADD COLUMN IF NOT EXISTS
        current_follower_volume NUMERIC NOT NULL DEFAULT 0""",
    """CREATE TABLE IF NOT EXISTS copy_trading_calendar_events (
        source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        region TEXT NOT NULL DEFAULT '',
        starts_at TIMESTAMPTZ NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        impact TEXT NOT NULL DEFAULT '',
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload_hash TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (source, external_id),
        CHECK (ends_at > starts_at)
    )""",
    """CREATE INDEX IF NOT EXISTS copy_trading_calendar_window_idx
        ON copy_trading_calendar_events (starts_at, ends_at, region)""",
    """CREATE TABLE IF NOT EXISTS copy_trading_workers (
        worker_id TEXT PRIMARY KEY,
        instance_id TEXT NOT NULL,
        slot_role TEXT NOT NULL CHECK (slot_role IN ('MASTER','FOLLOWER')),
        terminal_fingerprint TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'ONLINE',
        current_job_id TEXT NOT NULL DEFAULT '',
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_worker_nonces (
        nonce TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """ALTER TABLE copy_trading_worker_nonces ADD COLUMN IF NOT EXISTS
        role TEXT NOT NULL DEFAULT ''""",
    """CREATE INDEX IF NOT EXISTS copy_trading_worker_nonces_created_idx
        ON copy_trading_worker_nonces (created_at)""",
    """CREATE TABLE IF NOT EXISTS copy_trading_follower_locks (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        job_id UUID,
        lease_expires_at TIMESTAMPTZ,
        blocked BOOLEAN NOT NULL DEFAULT FALSE,
        blocked_reason TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_audit_events (
        event_id BIGSERIAL PRIMARY KEY,
        event_type TEXT NOT NULL,
        master_member_code TEXT NOT NULL DEFAULT '',
        follower_member_code TEXT NOT NULL DEFAULT '',
        job_id UUID,
        idempotency_key TEXT NOT NULL DEFAULT '',
        outcome_code TEXT NOT NULL DEFAULT '',
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS copy_trading_audit_created_idx
        ON copy_trading_audit_events (created_at DESC)""",
)


def ensure_copy_schema(conn) -> None:
    """Apply safe, additive and re-runnable copy-trading migrations."""
    for statement in STATEMENTS:
        conn.run(statement)
