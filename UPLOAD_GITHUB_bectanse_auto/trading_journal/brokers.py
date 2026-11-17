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
    "BullWaves",
    "Exness",
    "FXS",
    "PU Prime",
    "Vantage",
    "VT Markets",
)


_BROKER_IDENTITIES = {
    "axi": ("axi", "axitrader"),
    "bullwaves": ("bullwaves",),
    "exness": ("exness",),
    "fxs": ("fxs",),
    "puprime": ("puprime", "pumarkets", "pultd"),
    "vantage": ("vantage", "vantageinternational", "vantagemarkets"),
    "vtmarkets": ("vtmarkets",),
}


# Only exact MT5 names published by the broker are seeded here. Axi, FXS and
# Vantage explicitly assign/return the exact server in the client portal or the
# account confirmation e-mail, so their entries intentionally start empty and
# are enriched from successful Bectanse connections below.
VERIFIED_SERVERS = {
    # The exact live endpoint is observed on an established MT5 account.
    # Other BullWaves endpoints remain available through the searchable
    # catalog once verified, or via the exact server field supplied by MT5.
    "BullWaves": {"BullWaves-LIVE"},
    "Exness": {"Exness-MT5Real32", "Exness-MT5Real34"},
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


def broker_identity(value: str) -> str:
    """Return a stable broker family without trusting display-name spelling.

    MT5 returns legal company names (for example ``Exness Technologies Ltd``)
    while the app catalog uses consumer brands.  Treating those as different
    brokers created empty duplicate accounts when a user selected the wrong
    server from the same broker family.
    """
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    if normalized == "pu":
        return "puprime"
    for identity, aliases in _BROKER_IDENTITIES.items():
        if any(alias in normalized for alias in aliases):
            return identity
    return normalized


def _valid_server(server: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._\-/]{1,118}[A-Za-z0-9]", server))


def server_identity(server: str) -> str:
    """Return the comparison key MT5 uses for cosmetic server aliases.

    Broker welcome e-mails and the MetaTrader directory sometimes render the
    same server with different spaces or hyphens (for example
    ``PU-Prime-Live6`` and ``PUPrime-Live 6``).  The account login remains the
    authority; this key only accepts aliases whose alphanumeric content is
    identical.
    """
    return re.sub(r"[^a-z0-9]", "", str(server or "").casefold())


def server_connection_candidates(server: str) -> tuple[str, ...]:
    """Build a small, deterministic list of safe MT5 server spellings.

    The exact user-provided value is always attempted first.  We then cover
    common directory formatting differences without guessing a different
    broker or server number.
    """
    exact = str(server or "").strip()[:120]
    if not _valid_server(exact):
        return (exact,) if exact else ()
    candidates = [exact]
    compact_suffix = re.sub(r"(?i)(live|demo)\s+(\d+)$", r"\1\2", exact)
    spaced_suffix = re.sub(r"(?i)(live|demo)(\d+)$", r"\1 \2", exact)
    candidates.extend((compact_suffix, spaced_suffix))
    if server_identity(exact).startswith("puprime"):
        tail = re.sub(r"(?i)^pu(?:[ -]?prime)[ -]?", "", exact)
        candidates.extend((f"PUPrime-{tail}", f"PU-Prime-{tail}"))
        candidates.extend(
            (
                re.sub(r"(?i)(live|demo)\s+(\d+)$", r"\1\2", value)
                for value in tuple(candidates)
            )
        )
    unique = []
    for value in candidates:
        value = str(value or "").strip()[:120]
        if value and _valid_server(value) and value.casefold() not in {
            existing.casefold() for existing in unique
        }:
            unique.append(value)
    return tuple(unique[:6])


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
