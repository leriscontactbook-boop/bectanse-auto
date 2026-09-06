"""Idempotent journal migration runner used during deployments."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pg8000.native

from .schema import ensure_trading_schema


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
        ensure_trading_schema(conn)
    finally:
        conn.close()
    print("trading journal migrations: OK")


if __name__ == "__main__":
    main()
