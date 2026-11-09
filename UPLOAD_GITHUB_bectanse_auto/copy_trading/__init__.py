"""Isolated master-to-follower execution service.

This package is deliberately separate from ``trading_journal``: the journal
provider remains read-only, while this package owns the explicit execution
boundary and its release gates.
"""

from .schema import ensure_copy_schema

__all__ = ["ensure_copy_schema"]
