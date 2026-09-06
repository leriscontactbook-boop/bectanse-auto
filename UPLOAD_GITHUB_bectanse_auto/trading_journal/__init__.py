"""Automatic trading journal integration for the Bectanse Flask application."""

from .schema import ensure_trading_schema
from .routes import register_trading_journal

__all__ = ["ensure_trading_schema", "register_trading_journal"]
