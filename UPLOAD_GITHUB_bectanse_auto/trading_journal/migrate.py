"""Idempotent Bectanse database migrations run before the web process starts."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pg8000.native

from copy_trading import ensure_copy_schema

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
        ensure_copy_schema(conn)
    finally:
        conn.close()
    print("trading journal and copy trading migrations: OK")


if __name__ == "__main__":
    main()
