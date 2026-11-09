"""Railway/background process that syncs the durable economic calendar feed."""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from .calendar_provider import SOURCE, CalendarProviderError, fetch_events, utc_coverage
from .worker_client import CopyTradingClient, CopyWorkerAPIError


log = logging.getLogger("copy_trading.calendar")
ENDPOINT = "/internal/copy-trading/calendar/snapshot"


def build_snapshot(*, now=None, provider=None, api_key=None, fetcher=fetch_events):
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    coverage_start, coverage_end = utc_coverage(current)
    provider_name = (provider if provider is not None else
                    os.environ.get("COPY_TRADING_CALENDAR_PROVIDER", "")).strip().lower()
    secret = (api_key if api_key is not None else
              os.environ.get("TRADING_ECONOMICS_API_KEY", "")).strip()
    configured = provider_name == SOURCE and bool(secret)
    if not configured:
        return {
            "source": SOURCE, "status": "FAILED", "configured": False,
            "failure_code": "CALENDAR_PROVIDER_NOT_CONFIGURED",
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(), "events": [],
        }
    try:
        events = fetcher(secret, start_date=coverage_start.date(), end_date=coverage_end.date())
        return {
            "source": SOURCE, "status": "OK", "configured": True,
            "failure_code": "", "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(), "events": events,
        }
    except CalendarProviderError as error:
        return {
            "source": SOURCE, "status": "FAILED", "configured": True,
            "failure_code": error.code,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(), "events": [],
        }


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("COPY_TRADING_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    backend_url = (os.environ.get("COPY_TRADING_BACKEND_URL") or
                   os.environ.get("BACKEND_URL") or "").strip().rstrip("/")
    secret = (os.environ.get("COPY_TRADING_CALENDAR_WORKER_SECRET") or "").strip()
    worker_id = (os.environ.get("COPY_TRADING_CALENDAR_WORKER_ID") or
                 f"calendar-{socket.gethostname()}").strip()[:100]
    if not backend_url or len(secret) < 32 or not worker_id:
        raise SystemExit("Calendar worker requires backend URL, worker secret (32+ chars), and worker ID.")
    try:
        interval = int(os.environ.get("COPY_TRADING_CALENDAR_SYNC_SECONDS", "300"))
    except (TypeError, ValueError):
        interval = 300
    interval = min(900, max(30, interval))
    client = CopyTradingClient(backend_url, worker_id, "CALENDAR", secret)
    while True:
        snapshot = build_snapshot()
        try:
            result = client.post(ENDPOINT, snapshot)
            log.info("calendar_sync status=%s event_count=%s", result.get("status"),
                     result.get("event_count", 0))
        except CopyWorkerAPIError as error:
            # The worker client reports only sanitized error codes.
            log.warning("calendar_sync_backend_error code=%s", error.code)
        time.sleep(interval)


if __name__ == "__main__":
    main()
