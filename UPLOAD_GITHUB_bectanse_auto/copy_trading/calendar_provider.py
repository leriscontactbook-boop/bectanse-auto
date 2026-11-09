"""Fail-closed adapter for Trading Economics' UTC economic calendar API."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests


SOURCE = "tradingeconomics"
BASE_URL = "https://api.tradingeconomics.com/calendar/country/All"


class CalendarProviderError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CalendarProviderError("CALENDAR_INVALID_EVENT_DATE")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise CalendarProviderError("CALENDAR_INVALID_EVENT_DATE")
    if parsed.tzinfo is None:
        # Trading Economics documents calendar timestamps in UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _currency_code(value: Any, country: Any = "") -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "C$": "CAD", "CAN$": "CAD", "A$": "AUD", "AU$": "AUD",
        "NZ$": "NZD", "HK$": "HKD", "S$": "SGD", "US$": "USD",
        "R$": "BRL", "MX$": "MXN", "NT$": "TWD",
    }
    if raw in aliases:
        return aliases[raw]
    if raw:
        if len(raw) == 3 and raw.isalpha():
            return raw
        raise CalendarProviderError("CALENDAR_INVALID_CURRENCY")
    country_key = " ".join(str(country or "").strip().casefold().split())
    country_currencies = {
        "united states": "USD", "usa": "USD", "united kingdom": "GBP", "uk": "GBP",
        "euro area": "EUR", "euro zone": "EUR", "canada": "CAD", "australia": "AUD",
        "new zealand": "NZD", "japan": "JPY", "china": "CNY", "switzerland": "CHF",
        "india": "INR", "south korea": "KRW", "brazil": "BRL", "mexico": "MXN",
        "south africa": "ZAR", "sweden": "SEK", "norway": "NOK", "denmark": "DKK",
        "singapore": "SGD", "hong kong": "HKD", "taiwan": "TWD", "russia": "RUB",
        "turkey": "TRY", "poland": "PLN", "czech republic": "CZK", "hungary": "HUF",
        "israel": "ILS", "indonesia": "IDR", "malaysia": "MYR", "thailand": "THB",
        "philippines": "PHP", "saudi arabia": "SAR", "united arab emirates": "AED",
        "argentina": "ARS", "chile": "CLP", "colombia": "COP", "iceland": "ISK",
    }
    return country_currencies.get(country_key, "")


def normalize_events(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise CalendarProviderError("CALENDAR_INVALID_RESPONSE")
    if len(rows) >= 1000:
        # The API caps a response at 1,000 entries; treating a full page as a
        # complete snapshot could silently omit events, so keep the old feed
        # and report the sync as failed instead.
        raise CalendarProviderError("CALENDAR_RESPONSE_LIMIT_REACHED")
    events = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CalendarProviderError("CALENDAR_INVALID_EVENT")
        external_id = str(row.get("CalendarId") or row.get("CalendarID") or "").strip()
        if not external_id or external_id in seen:
            raise CalendarProviderError("CALENDAR_INVALID_EVENT_ID")
        seen.add(external_id)
        importance = row.get("Importance")
        try:
            importance = int(importance)
        except (TypeError, ValueError, OverflowError):
            raise CalendarProviderError("CALENDAR_INVALID_IMPORTANCE")
        if importance not in (1, 2, 3):
            raise CalendarProviderError("CALENDAR_INVALID_IMPORTANCE")
        event_at = _utc_datetime(row.get("Date"))
        estimated = str(row.get("DateSpan") or "0").strip() == "1"
        if estimated:
            starts_at = datetime.combine(event_at.date(), time.min, tzinfo=timezone.utc)
            ends_at = starts_at + timedelta(days=1)
        else:
            starts_at = event_at
            ends_at = event_at + timedelta(seconds=1)
        currency = _currency_code(row.get("Currency"), row.get("Country"))
        events.append({
            "external_id": external_id[:160],
            "region": str(row.get("Country") or "ALL").strip()[:40] or "ALL",
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "currency": currency,
            "importance": importance,
            "event_title": str(row.get("Event") or row.get("Category") or "Economic event").strip()[:200],
            "estimated_time": estimated,
        })
    return events


def fetch_events(api_key: str, *, start_date: date, end_date: date,
                 session=requests, timeout=(5, 20)) -> list[dict[str, Any]]:
    """Fetch and validate an inclusive UTC date range; never expose credentials."""
    secret = (api_key or "").strip()
    if not secret:
        raise CalendarProviderError("CALENDAR_CREDENTIAL_MISSING")
    if end_date < start_date or (end_date - start_date).days > 31:
        raise CalendarProviderError("CALENDAR_RANGE_INVALID")
    url = f"{BASE_URL}/{start_date.isoformat()}/{end_date.isoformat()}"
    try:
        response = session.get(
            url, params={"f": "json"}, headers={"Authorization": secret},
            timeout=timeout,
        )
    except requests.RequestException as error:
        raise CalendarProviderError("CALENDAR_PROVIDER_UNREACHABLE") from error
    if not response.ok:
        raise CalendarProviderError("CALENDAR_PROVIDER_REJECTED")
    try:
        payload = response.json()
    except (ValueError, requests.RequestException) as error:
        raise CalendarProviderError("CALENDAR_INVALID_RESPONSE") from error
    return normalize_events(payload)


def utc_coverage(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = datetime.combine(current.date() - timedelta(days=1), time.min, tzinfo=timezone.utc)
    end = datetime.combine(current.date() + timedelta(days=15), time.min, tzinfo=timezone.utc)
    return start, end
