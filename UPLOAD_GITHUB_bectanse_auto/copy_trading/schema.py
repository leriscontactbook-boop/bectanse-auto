"""Additive schema; run after the journal schema."""

STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS copy_demo_runtime (
        worker_id TEXT PRIMARY KEY,
        member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE RESTRICT,
        payload JSONB NOT NULL,
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS copy_configurations (
        member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,
        version BIGINT NOT NULL,
        account_id BIGINT NOT NULL REFERENCES trading_accounts(id) ON DELETE RESTRICT,
        payload JSONB NOT NULL,
        digest TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING'
          CHECK (status IN ('PENDING','VALIDATED_SIMULATION','FAILED','SUPERSEDED')),
        worker_id TEXT,
        lease_token TEXT,
        lease_until TIMESTAMPTZ,
        error_code TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        confirmed_at TIMESTAMPTZ,
        PRIMARY KEY (member_code, version)
    )""",
    """CREATE INDEX IF NOT EXISTS copy_configurations_pending_idx
        ON copy_configurations (created_at) WHERE status='PENDING'""",
    """CREATE TABLE IF NOT EXISTS copy_worker_nonces (
        nonce TEXT PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
)


def ensure_copy_schema(conn):
    from .admin import ADMIN_SCHEMA
    from .agent_backend import AGENT_SCHEMA
    from .onboarding_server import SCHEMA as PREPARATION_SCHEMA
    from .member_activation import SCHEMA as COMMAND_SCHEMA
    from .real_preparation import SCHEMA as REAL_OBSERVATION_SCHEMA
    from .real_control import SCHEMA as REAL_CONTROL_SCHEMA
    from .preflight_transport import SCHEMA as PREFLIGHT_SCHEMA
    for statement in PREFLIGHT_SCHEMA + STATEMENTS + ADMIN_SCHEMA + AGENT_SCHEMA + PREPARATION_SCHEMA + COMMAND_SCHEMA + REAL_OBSERVATION_SCHEMA + REAL_CONTROL_SCHEMA:
        conn.run(statement)
