"""Validated configuration for the production MT5 journal components."""

from __future__ import annotations

import base64
import os


class JournalConfigurationError(RuntimeError):
    pass


def is_production() -> bool:
    value = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").lower()
    return value in {"production", "prod"} or bool(os.environ.get("RAILWAY_ENVIRONMENT"))


def worker_secret() -> str:
    return (
        os.environ.get("INTERNAL_WORKER_SECRET")
        or os.environ.get("INTERNAL_MT5_WORKER_SECRET")
        or ""
    ).strip()


def validate_backend_config(*, production: bool | None = None) -> None:
    """Fail fast when a production backend could accept unsafe MT5 traffic."""
    production = is_production() if production is None else production
    provider = os.environ.get("TRADING_PROVIDER", "mt5" if production else "mock").lower()
    if production and provider != "mt5":
        raise JournalConfigurationError("TRADING_PROVIDER=mock is forbidden in production")
    if not production:
        return
    if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        raise JournalConfigurationError("DATABASE_URL must be a PostgreSQL URL")
    secret = worker_secret()
    if len(secret) < 32:
        raise JournalConfigurationError("INTERNAL_WORKER_SECRET must contain at least 32 characters")
    encoded_key = os.environ.get("MT5_CREDENTIAL_MASTER_KEY", "").strip()
    try:
        raw_key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
    except Exception as exc:
        raise JournalConfigurationError("MT5_CREDENTIAL_MASTER_KEY is invalid") from exc
    if len(raw_key) != 32:
        raise JournalConfigurationError("MT5_CREDENTIAL_MASTER_KEY must encode exactly 32 bytes")


def validate_worker_config() -> dict:
    """Return canonical worker settings after strict validation."""
    backend_url = (os.environ.get("BACKEND_URL") or os.environ.get("MT5_BACKEND_URL") or "").strip()
    secret = worker_secret()
    terminal_paths = [
        value.strip()
        for value in (
            os.environ.get("MT5_TERMINAL_PATHS")
            or os.environ.get("MT5_TERMINAL_PATH")
            or ""
        ).split(";")
        if value.strip()
    ]
    count = max(1, int(os.environ.get("MT5_WORKER_COUNT") or os.environ.get("WORKER_SLOT_COUNT") or "1"))
    if not backend_url:
        raise JournalConfigurationError("BACKEND_URL is required")
    if len(secret) < 32:
        raise JournalConfigurationError("INTERNAL_WORKER_SECRET must contain at least 32 characters")
    if len(terminal_paths) < count:
        raise JournalConfigurationError("Configure one distinct MT5 terminal path per worker slot")
    if len(set(path.lower() for path in terminal_paths[:count])) != count:
        raise JournalConfigurationError("Each worker slot must own a distinct MT5 terminal")
    return {"backend_url": backend_url, "secret": secret, "terminal_paths": terminal_paths, "count": count}
