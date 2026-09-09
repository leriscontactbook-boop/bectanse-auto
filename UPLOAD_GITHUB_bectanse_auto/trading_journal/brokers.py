"""Searchable MT5 broker/server catalog.

MetaQuotes does not publish a static exhaustive server API. The catalog therefore
combines verified seeds, validated servers already used by the application, and
optional production entries supplied through MT5_BROKER_CATALOG_JSON.
"""

from __future__ import annotations

import json
import os
import re


POPULAR_BROKERS = (
    "Axi",
    "Exness",
    "FXS",
    "PU Prime",
    "Vantage",
    "VT Markets",
)


# Only exact MT5 names published by the broker are seeded here. Axi, FXS and
# Vantage explicitly assign/return the exact server in the client portal or the
# account confirmation e-mail, so their entries intentionally start empty and
# are enriched from successful Bectanse connections below.
VERIFIED_SERVERS = {
    "Exness": {"Exness-MT5Real34"},
    "PU Prime": {
        "PUPrime-Demo",
        "PUPrime-Live",
        "PUPrime-Live 4",
        "PUPrime-Live 5",
        "PUPrime-Live 6",
        "PUPrime-Live2",
    },
    "VT Markets": {"VTMarkets-Demo", "VTMarkets-Live"},
}


def _valid_server(server: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._\-/]{1,118}[A-Za-z0-9]", server))


def broker_name_from_server(server: str) -> str:
    prefix = re.split(r"[- _]", str(server or "").strip(), maxsplit=1)[0]
    return prefix if 1 < len(prefix) <= 80 else "Autre broker"


def _configured_servers() -> dict[str, set[str]]:
    raw = os.environ.get("MT5_BROKER_CATALOG_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    result: dict[str, set[str]] = {}
    if not isinstance(payload, dict):
        return result
    for broker, servers in payload.items():
        name = str(broker or "").strip()[:80]
        if not name or not isinstance(servers, list):
            continue
        result[name] = {
            str(server).strip()[:120]
            for server in servers
            if _valid_server(str(server or "").strip())
        }
    return result


def build_broker_catalog(database_rows=()) -> list[dict]:
    merged = {broker: set() for broker in POPULAR_BROKERS}
    for broker, servers in VERIFIED_SERVERS.items():
        merged.setdefault(broker, set()).update(servers)
    for broker, servers in _configured_servers().items():
        merged.setdefault(broker, set()).update(servers)
    for broker, server in database_rows:
        server_name = str(server or "").strip()[:120]
        if not _valid_server(server_name):
            continue
        broker_name = str(broker or "").strip()[:80] or broker_name_from_server(server_name)
        merged.setdefault(broker_name, set()).add(server_name)
    return [
        {
            "name": broker,
            "servers": sorted(servers, key=str.casefold),
            "manual_server_allowed": True,
        }
        for broker, servers in sorted(merged.items(), key=lambda row: row[0].casefold())
    ]
