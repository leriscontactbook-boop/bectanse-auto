"""Automatic trading journal integration for the Bectanse Flask application."""

from .schema import ensure_trading_schema


def register_trading_journal(*args, **kwargs):
    """Load Flask routes only in the web process, not in the Windows worker."""
    from .routes import register_trading_journal as register

    return register(*args, **kwargs)

__all__ = ["ensure_trading_schema", "register_trading_journal"]
