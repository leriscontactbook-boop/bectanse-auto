"""Versioned copy configuration and a separately authorized, demo-only MT5 pilot."""

import os


def enabled():
    return os.environ.get("COPY_CONFIG_ENABLED", "false").lower() in {"true", "1", "yes"}
