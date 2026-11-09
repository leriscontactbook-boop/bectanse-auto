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
    """CREATE TABLE IF NOT EXISTS copy_trading_master_orders (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        order_ticket TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        pending_type TEXT NOT NULL,
        volume NUMERIC NOT NULL CHECK (volume > 0),
        price_open NUMERIC NOT NULL,
        stop_loss NUMERIC NOT NULL DEFAULT 0,
        take_profit NUMERIC NOT NULL DEFAULT 0,
        stop_limit NUMERIC NOT NULL DEFAULT 0,
        expiration_at TIMESTAMPTZ,
        time_type INTEGER NOT NULL DEFAULT 0,
        source_version TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        missing_snapshots INTEGER NOT NULL DEFAULT 0,
        last_seen_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code,order_ticket)
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
    "ALTER TABLE copy_trading_events ADD COLUMN IF NOT EXISTS pending_type TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE copy_trading_events ADD COLUMN IF NOT EXISTS stop_limit NUMERIC NOT NULL DEFAULT 0",
    "ALTER TABLE copy_trading_events ADD COLUMN IF NOT EXISTS expiration_at TIMESTAMPTZ",
    "ALTER TABLE copy_trading_events ADD COLUMN IF NOT EXISTS time_type INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE copy_trading_events DROP CONSTRAINT IF EXISTS copy_trading_events_action_check",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='copy_trading_events_action_v2') THEN
            ALTER TABLE copy_trading_events ADD CONSTRAINT copy_trading_events_action_v2
              CHECK (action IN ('OPEN','RECONCILE','CLOSE','PENDING_CREATE','PENDING_MODIFY','PENDING_CANCEL','PENDING_EXPIRE'));
        END IF;
    END $$""",
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
        protection_close BOOLEAN NOT NULL DEFAULT FALSE,
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
    "ALTER TABLE copy_trading_jobs ADD COLUMN IF NOT EXISTS protection_close BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE copy_trading_jobs DROP CONSTRAINT IF EXISTS copy_trading_jobs_execution_action_check",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='copy_trading_jobs_execution_action_v2') THEN
            ALTER TABLE copy_trading_jobs ADD CONSTRAINT copy_trading_jobs_execution_action_v2
              CHECK (execution_action IN ('OPEN','CLOSE','PROTECT','RESIZE_OPEN','RESIZE_CLOSE','NOOP',
                'PENDING_CREATE','PENDING_MODIFY','PENDING_CANCEL','PENDING_EXPIRE'));
        END IF;
    END $$""",
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
    """CREATE TABLE IF NOT EXISTS copy_trading_pending_order_links (
        master_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        follower_member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        master_order_ticket TEXT NOT NULL,
        follower_order_ticket TEXT NOT NULL,
        master_symbol TEXT NOT NULL,
        follower_symbol TEXT NOT NULL,
        master_type TEXT NOT NULL,
        follower_type TEXT NOT NULL,
        current_volume NUMERIC NOT NULL CHECK (current_volume > 0),
        current_price NUMERIC NOT NULL,
        current_stop_loss NUMERIC NOT NULL DEFAULT 0,
        current_take_profit NUMERIC NOT NULL DEFAULT 0,
        current_stop_limit NUMERIC NOT NULL DEFAULT 0,
        expiration_at TIMESTAMPTZ,
        time_type INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','CLOSED','VERIFY_REQUIRED')),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (master_member_code,follower_member_code,master_order_ticket)
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_calendar_events (
        source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        region TEXT NOT NULL DEFAULT '',
        starts_at TIMESTAMPTZ NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        impact TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT '',
        importance SMALLINT NOT NULL DEFAULT 0,
        event_title TEXT NOT NULL DEFAULT '',
        estimated_time BOOLEAN NOT NULL DEFAULT FALSE,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload_hash TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (source, external_id),
        CHECK (ends_at > starts_at)
    )""",
    "ALTER TABLE copy_trading_calendar_events ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE copy_trading_calendar_events ADD COLUMN IF NOT EXISTS importance SMALLINT NOT NULL DEFAULT 0",
    "ALTER TABLE copy_trading_calendar_events ADD COLUMN IF NOT EXISTS event_title TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE copy_trading_calendar_events ADD COLUMN IF NOT EXISTS estimated_time BOOLEAN NOT NULL DEFAULT FALSE",
    """CREATE INDEX IF NOT EXISTS copy_trading_calendar_window_v2_idx
        ON copy_trading_calendar_events (starts_at, ends_at, region, currency, importance)""",
    """CREATE TABLE IF NOT EXISTS copy_trading_calendar_sources (
        source TEXT PRIMARY KEY,
        configured BOOLEAN NOT NULL DEFAULT FALSE,
        last_attempt_at TIMESTAMPTZ,
        last_success_at TIMESTAMPTZ,
        last_failure_code TEXT NOT NULL DEFAULT '',
        event_count INTEGER NOT NULL DEFAULT 0,
        coverage_start TIMESTAMPTZ,
        coverage_end TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (coverage_end IS NULL OR coverage_start IS NULL OR coverage_end > coverage_start)
    )""",
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
    """CREATE TABLE IF NOT EXISTS copy_trading_terminal_probes (
        worker_id TEXT NOT NULL,
        slot_role TEXT NOT NULL CHECK (slot_role IN ('MASTER','FOLLOWER')),
        member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        account_fingerprint TEXT NOT NULL,
        server TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL CHECK (state IN ('CONNECTED','FAILED')),
        failure_code TEXT NOT NULL DEFAULT '',
        terminal_path_verified BOOLEAN NOT NULL DEFAULT FALSE,
        diagnostic_code INTEGER,
        process_id BIGINT,
        session_id INTEGER,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (worker_id, member_code)
    )""",
    """CREATE INDEX IF NOT EXISTS copy_trading_terminal_probes_member_idx
        ON copy_trading_terminal_probes (member_code, checked_at DESC)""",
    """CREATE TABLE IF NOT EXISTS copy_trading_follower_locks (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        job_id UUID,
        lease_expires_at TIMESTAMPTZ,
        blocked BOOLEAN NOT NULL DEFAULT FALSE,
        blocked_reason TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_trading_account_protections (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        guard_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        period_key TEXT NOT NULL DEFAULT '',
        baseline_balance NUMERIC,
        baseline_equity NUMERIC,
        risk_anchor_equity NUMERIC,
        peak_equity NUMERIC,
        last_balance NUMERIC,
        last_equity NUMERIC,
        loss_drawdown_pct NUMERIC,
        gain_pct NUMERIC,
        objective_pct NUMERIC,
        suspended BOOLEAN NOT NULL DEFAULT FALSE,
        suspension_reason TEXT NOT NULL DEFAULT '',
        triggered_at TIMESTAMPTZ,
        resume_requested BOOLEAN NOT NULL DEFAULT FALSE,
        resume_requested_at TIMESTAMPTZ,
        objective_resume_pending BOOLEAN NOT NULL DEFAULT FALSE,
        monitor_worker_id TEXT NOT NULL DEFAULT '',
        monitor_token_hash TEXT NOT NULL DEFAULT '',
        monitor_lease_expires_at TIMESTAMPTZ,
        last_sample_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "ALTER TABLE copy_trading_account_protections ADD COLUMN IF NOT EXISTS objective_resume_pending BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS copy_trading_protection_monitor_idx ON copy_trading_account_protections(last_sample_at,monitor_lease_expires_at) WHERE guard_enabled=TRUE OR suspended=TRUE",
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
