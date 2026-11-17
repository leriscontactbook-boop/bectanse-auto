"""Idempotent journal migration runner used during deployments."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pg8000.native

from broker_trial import ensure_trial_schema
from member_evidence import ensure_evidence_schema
from .schema import ensure_trading_schema
from copy_trading.schema import ensure_copy_schema
from stripe_reliability.schema import ensure_billing_schema
from stripe_reliability.bootstrap import ensure_member_base


def connection_from_environment():
    parsed = urlparse(os.environ["DATABASE_URL"])
    return pg8000.native.Connection(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path.lstrip("/"),
        ssl_context=True,
    )


def main() -> None:
    conn = connection_from_environment()
    try:
        ensure_member_base(conn)
        ensure_trial_schema(conn)
        ensure_trading_schema(conn)
        ensure_copy_schema(conn)
        ensure_billing_schema(conn)
        ensure_evidence_schema(conn)
    finally:
        conn.close()
    print("trading journal migrations: OK")


if __name__ == "__main__":
    main()
