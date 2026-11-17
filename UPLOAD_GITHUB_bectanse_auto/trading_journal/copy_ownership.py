"""Explicit copy terminals own their accounts; journal pools must never log them in."""
import json
import os


def reserved_account_ids():
    # Keep reservations while the pilot is paused. A pause does not transfer ownership.
    data = json.loads(os.environ.get("COPY_DEMO_BINDINGS", "{}"))
    if not isinstance(data, dict):
        raise ValueError("INVALID_COPY_RESERVATIONS")
    ids = set()
    for binding in data.values():
        if (not isinstance(binding, dict) or type(binding.get("account_id")) is not int
                or binding["account_id"] <= 0 or not isinstance(binding.get("member_code"), str)
                or not binding["member_code"]):
            raise ValueError("INVALID_COPY_RESERVATIONS")
        ids.add(binding["account_id"])
    return sorted(ids)
