"""Durable high-impact economic calendar used by the MT5 entry filter.

The public provider is best-effort only: the execution path never waits on it.
Trusted workers may publish a validated snapshot over the existing signed
worker channel.  A stale snapshot is deliberately fail-closed for *new*
entries when the member enabled the news filter.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
TTL_SECONDS = 300
REFRESH_INTERVAL_SECONDS = 60
MAX_ROWS = 5000
STORE = Path(os.environ.get('COPY_NEWS_CALENDAR_STORE', '/app/persistent/news-calendar-v1.json'))
_lock = threading.Lock()
_cache = None
_refreshing = False
_attempted = 0.0
_last_error = None
_next_retry_at = 0.0


def parse(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError('CALENDAR_INVALID')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('CALENDAR_INVALID')
        if str(row.get('impact', '')).lower() != 'high':
            continue
        value = row.get('date')
        if not isinstance(value, str):
            raise ValueError('CALENDAR_TIME_REQUIRED')
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None:
            raise ValueError('CALENDAR_TIMEZONE_REQUIRED')
        result.append({
            'at': at.timestamp(),
            'currency': str(row.get('country', ''))[:16],
            'title': str(row.get('title', ''))[:160],
        })
    return result


def _record(observed_at, events, source):
    if not isinstance(observed_at, (int, float)) or observed_at <= 0:
        raise ValueError('CALENDAR_OBSERVED_AT_REQUIRED')
    return {'observed_at': float(observed_at), 'events': events, 'source': str(source)[:48]}


def _load():
    global _cache
    try:
        raw = json.loads(STORE.read_text(encoding='utf-8'))
        events = raw.get('events')
        if not isinstance(events, list):
            return
        # Stored events have already been validated. Revalidate their bounded shape.
        checked = []
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get('at'), (int, float)):
                return
            checked.append({'at': float(event['at']), 'currency': str(event.get('currency', ''))[:16],
                            'title': str(event.get('title', ''))[:160]})
        _cache = _record(raw.get('observed_at'), checked, raw.get('source', 'PERSISTED'))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def _persist(snapshot):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    os.replace(temporary, STORE)


def ingest(rows, *, observed_at=None, source='VPS_RELAY'):
    """Accept only a fresh, validated public-calendar snapshot."""
    global _cache, _last_error, _next_retry_at
    events = parse(rows)
    now = time.time()
    observed_at = now if observed_at is None else float(observed_at)
    if observed_at > now + 30 or observed_at < now - TTL_SECONDS:
        raise ValueError('CALENDAR_SNAPSHOT_STALE')
    snapshot = _record(observed_at, events, source)
    with _lock:
        _persist(snapshot)
        _cache = snapshot
        _last_error = None
        _next_retry_at = now + REFRESH_INTERVAL_SECONDS
    return {'observed_at': snapshot['observed_at'], 'event_count': len(events), 'source': snapshot['source']}


def evaluate(calendar, now):
    if not calendar or not 0 <= now - calendar['observed_at'] <= TTL_SECONDS:
        return {'blocked': True, 'reason': 'NEWS_CALENDAR_UNAVAILABLE',
                'retry_at': max(now + 1, _next_retry_at), 'expires_at': now + 1,
                'source': calendar.get('source') if calendar else None}
    events = [row for row in calendar['events'] if abs(row['at'] - now) <= 900]
    return {'blocked': bool(events), 'reason': 'NEWS_HIGH_IMPACT_WINDOW' if events else None,
            'events': events, 'observed_at': calendar['observed_at'], 'source': calendar['source'],
            'expires_at': min(now + 10, calendar['observed_at'] + TTL_SECONDS)}


def _refresh():
    global _refreshing, _last_error, _next_retry_at
    try:
        import requests
        response = requests.get(URL, timeout=(3, 8))
        response.raise_for_status()
        rows = response.json()
        week = datetime.now(timezone.utc).isocalendar()[:2]
        if not any(isinstance(row, dict) and isinstance(row.get('date'), str)
                   and datetime.fromisoformat(row['date'].replace('Z', '+00:00')).astimezone(timezone.utc).isocalendar()[:2] == week
                   for row in rows):
            raise ValueError('CALENDAR_WEEK_STALE')
        ingest(rows, source='RAILWAY_PROVIDER')
    except Exception as error:
        with _lock:
            _last_error = type(error).__name__
            _next_retry_at = time.time() + REFRESH_INTERVAL_SECONDS
    finally:
        with _lock:
            _refreshing = False


def current(now=None):
    global _refreshing, _attempted
    now = time.time() if now is None else now
    with _lock:
        if _cache is None:
            _load()
        if not _refreshing and now >= _next_retry_at and now - _attempted >= REFRESH_INTERVAL_SECONDS:
            _attempted = now
            _refreshing = True
            threading.Thread(target=_refresh, daemon=True, name='copy-news-calendar').start()
        result = evaluate(_cache, now)
        if _last_error:
            result['last_error'] = _last_error
        return result
