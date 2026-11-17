"""Additive, repeatable PostgreSQL migration. No destructive rollback."""
DDL = (
"""CREATE TABLE IF NOT EXISTS billing_deliveries (
 delivery_key TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS billing_effects (
 id BIGSERIAL PRIMARY KEY,invoice_id TEXT NOT NULL UNIQUE,member_code TEXT NOT NULL,
 details JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT NOT NULL DEFAULT '',retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS billing_events (
 event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,reference JSONB NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT NOT NULL DEFAULT '',received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 processed_at TIMESTAMPTZ,next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS billing_incidents (
 id BIGSERIAL PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
 member_code TEXT NOT NULL DEFAULT '',customer_id TEXT NOT NULL DEFAULT '',
 subscription_id TEXT NOT NULL DEFAULT '',invoice_id TEXT NOT NULL DEFAULT '',
 amount BIGINT,currency TEXT NOT NULL DEFAULT '',payment_state TEXT NOT NULL DEFAULT '',
 access_state TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'new'
 CHECK(status IN ('new','in_progress','resolved','review')),
 attempts INTEGER NOT NULL DEFAULT 1,last_error TEXT NOT NULL DEFAULT '',
 details JSONB NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),resolved_at TIMESTAMPTZ)""",
"""CREATE TABLE IF NOT EXISTS billing_actions (
 id BIGSERIAL PRIMARY KEY,incident_id BIGINT REFERENCES billing_incidents(id),
 member_code TEXT NOT NULL DEFAULT '',actor TEXT NOT NULL,action TEXT NOT NULL,
 details JSONB NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS billing_runs (
 id BIGSERIAL PRIMARY KEY,actor TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'running',
 started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),finished_at TIMESTAMPTZ,
 summary JSONB NOT NULL DEFAULT '{}')""",
"""CREATE TABLE IF NOT EXISTS billing_checkouts (
 member_code TEXT NOT NULL,product TEXT NOT NULL,plan TEXT NOT NULL,
 attempt_id TEXT NOT NULL UNIQUE,session_id TEXT NOT NULL DEFAULT '',
 url TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'creating',
 expires_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(member_code,product))""",
"""CREATE TABLE IF NOT EXISTS billing_access_exceptions (
 id BIGSERIAL PRIMARY KEY,member_code TEXT NOT NULL,kind TEXT NOT NULL
 CHECK(kind IN ('external_payment','gift','manual_extension','historical','test')),
 reason TEXT NOT NULL,source TEXT NOT NULL,actor TEXT NOT NULL,
 verified BOOLEAN NOT NULL DEFAULT FALSE,expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),revoked_at TIMESTAMPTZ)""",
"CREATE INDEX IF NOT EXISTS billing_events_retry_idx ON billing_events(status,next_attempt_at)",
"CREATE INDEX IF NOT EXISTS billing_incidents_status_idx ON billing_incidents(status,checked_at)",
"ALTER TABLE members ADD COLUMN IF NOT EXISTS billing_verified_at TIMESTAMPTZ",
"ALTER TABLE members ADD COLUMN IF NOT EXISTS billing_access_reason TEXT NOT NULL DEFAULT ''",
"ALTER TABLE members ADD COLUMN IF NOT EXISTS billing_period_start TIMESTAMPTZ",
)


def ensure_billing_schema(conn):
    conn.run('BEGIN')
    try:
        for sql in DDL:
            conn.run(sql)
        conn.run('COMMIT')
    except Exception:
        conn.run('ROLLBACK')
        raise
