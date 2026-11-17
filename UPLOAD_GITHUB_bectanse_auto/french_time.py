"""Explicit UTC transport and Paris presentation; database timestamps stay unchanged."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
PARIS = ZoneInfo("Europe/Paris")
def utc_iso(value):
    if not value: return value
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def paris_time(value):
    if value is None: return value
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS)
