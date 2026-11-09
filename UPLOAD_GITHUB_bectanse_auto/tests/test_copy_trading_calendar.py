import json
import time
from datetime import datetime, timezone

import pytest
from flask import Flask

from copy_trading.calendar_provider import (
    CalendarProviderError, fetch_events, normalize_events, utc_coverage,
)
from copy_trading.calendar_worker import build_snapshot
from copy_trading.domain import academy_settings
from copy_trading.routes import register_copy_trading
from copy_trading.security import canonical_copy_signature
from copy_trading.service import CopyTradingService


def _te_row(**overrides):
    row = {
        "CalendarId": "12345", "Date": "2026-11-09T14:30:00",
        "Country": "United States", "Event": "Employment report",
        "DateSpan": "0", "Importance": 3, "Currency": "USD",
    }
    return {**row, **overrides}


def test_calendar_provider_normalizes_exact_and_estimated_events_to_utc():
    exact, estimated = normalize_events([
        _te_row(Currency="C$"), _te_row(CalendarId="12346", Date="2026-11-10T00:00:00Z", DateSpan="1"),
    ])
    assert exact["starts_at"] == "2026-11-09T14:30:00+00:00"
    assert exact["currency"] == "CAD" and exact["importance"] == 3
    assert estimated["starts_at"] == "2026-11-10T00:00:00+00:00"
    assert estimated["ends_at"] == "2026-11-11T00:00:00+00:00"
    assert estimated["estimated_time"] is True


@pytest.mark.parametrize("country, currency", [
    ("United States", "USD"), ("Euro Area", "EUR"), ("United Kingdom", "GBP"),
])
def test_calendar_provider_falls_back_to_country_currency_when_provider_field_is_blank(country, currency):
    event = normalize_events([_te_row(Country=country, Currency="")])[0]
    assert event["currency"] == currency


def test_calendar_provider_keeps_unknown_blank_currency_unassigned():
    event = normalize_events([_te_row(Country="Unmapped Region", Currency="")])[0]
    assert event["currency"] == ""


@pytest.mark.parametrize("row", [
    _te_row(CalendarId=""), _te_row(Importance=0), _te_row(Date="not a date"),
    _te_row(Currency="USD!"),
])
def test_calendar_provider_rejects_untrustworthy_rows(row):
    with pytest.raises(CalendarProviderError):
        normalize_events([row])


def test_calendar_provider_rejects_possible_truncated_response():
    with pytest.raises(CalendarProviderError, match="LIMIT"):
        normalize_events([_te_row(CalendarId=str(index)) for index in range(1000)])


def test_calendar_fetch_uses_authenticated_https_request_without_key_in_query():
    class FakeResponse:
        ok = True

        @staticmethod
        def json():
            return [_te_row()]

    class FakeSession:
        called = None

        def get(self, *args, **kwargs):
            self.called = (args, kwargs)
            return FakeResponse()

    session = FakeSession()
    events = fetch_events(
        "private-key", start_date=datetime(2026, 11, 8).date(),
        end_date=datetime(2026, 11, 10).date(), session=session,
    )
    args, kwargs = session.called
    assert args[0].startswith("https://api.tradingeconomics.com/calendar/country/All/")
    assert "private-key" not in args[0] and "c" not in kwargs["params"]
    assert kwargs["headers"] == {"Authorization": "private-key"}
    assert events[0]["external_id"] == "12345"


def test_calendar_worker_reports_missing_provider_without_faking_success():
    snapshot = build_snapshot(
        now=datetime(2026, 11, 9, 12, tzinfo=timezone.utc),
        provider="", api_key="",
    )
    assert snapshot["status"] == "FAILED"
    assert snapshot["configured"] is False
    assert snapshot["failure_code"] == "CALENDAR_PROVIDER_NOT_CONFIGURED"
    assert snapshot["events"] == []


def test_calendar_utc_coverage_is_calendar_day_based_and_dst_independent():
    start, end = utc_coverage(datetime(2026, 11, 9, 00, 30, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-11-08T00:00:00+00:00"
    assert end.isoformat() == "2026-11-24T00:00:00+00:00"


class _CalendarConnection:
    def __init__(self, *, fresh=True, active=False, configured=True, failure=""):
        now = datetime.now(timezone.utc)
        self.state = (configured, now, now, failure, now.replace(hour=0), now.replace(hour=23, minute=59))
        self.fresh = fresh
        self.active = active
        self.calls = []

    def run(self, sql, **params):
        self.calls.append((sql, params))
        if "SELECT configured,last_attempt_at" in sql:
            return [self.state]
        if "SELECT 1 FROM copy_trading_calendar_sources" in sql:
            return [(1,)] if self.fresh else []
        if "SELECT 1 FROM copy_trading_calendar_events" in sql:
            return [(1,)] if self.active else []
        raise AssertionError(sql)

    @staticmethod
    def close():
        pass


def test_calendar_disabled_does_not_query_database(monkeypatch):
    service = CopyTradingService(
        lambda: pytest.fail("calendar OFF must not query DB"), lambda _code: {},
        lambda _member: True, consent_version="v1",
    )
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": False}))
    assert (allowed, reason) == (True, "FILTER_DISABLED")


def test_calendar_filter_defaults_to_block_when_provider_is_unconfigured(monkeypatch):
    monkeypatch.delenv("COPY_TRADING_CALENDAR_PROVIDER", raising=False)
    monkeypatch.delenv("COPY_TRADING_CALENDAR_FAILURE_POLICY", raising=False)
    service = CopyTradingService(lambda: pytest.fail("unconfigured source should fail before DB"),
                                 lambda _code: {}, lambda _member: True, consent_version="v1")
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": True}))
    assert (allowed, reason) == (False, "CALENDAR_PROVIDER_NOT_CONFIGURED")


def test_calendar_failure_policy_is_explicit_and_fail_open_is_reported(monkeypatch):
    monkeypatch.delenv("COPY_TRADING_CALENDAR_PROVIDER", raising=False)
    monkeypatch.setenv("COPY_TRADING_CALENDAR_FAILURE_POLICY", "ALLOW")
    service = CopyTradingService(lambda: pytest.fail("unconfigured source should fail before DB"),
                                 lambda _code: {}, lambda _member: True, consent_version="v1")
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": True}))
    assert (allowed, reason) == (True, "CALENDAR_UNAVAILABLE_FAIL_OPEN")


@pytest.mark.parametrize(
    "fresh,active,failure,expected",
    [(False, False, "", "CALENDAR_STALE_OR_UNAVAILABLE"),
     (True, True, "", "NEWS_WINDOW_ACTIVE"),
     (True, False, "", "NO_BLOCKING_WINDOW"),
     (True, False, "PROVIDER_DOWN", "CALENDAR_STALE_OR_UNAVAILABLE")],
)
def test_calendar_decision_uses_fresh_durable_feed_and_event_match(
        monkeypatch, fresh, active, failure, expected):
    monkeypatch.setenv("COPY_TRADING_CALENDAR_PROVIDER", "tradingeconomics")
    monkeypatch.setenv("COPY_TRADING_CALENDAR_FAILURE_POLICY", "BLOCK")
    connection = _CalendarConnection(fresh=fresh, active=active, failure=failure)
    service = CopyTradingService(lambda: connection, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    allowed, reason = service.calendar_allows(
        academy_settings({"filtre_news": True}), region="United States",
        preferences={"calendar_currencies": "USD,EUR", "calendar_min_importance": 3,
                     "calendar_minutes_before": 20, "calendar_minutes_after": 45},
    )
    assert reason == expected
    assert allowed is (expected == "NO_BLOCKING_WINDOW")
    event_query = next((params for sql, params in connection.calls
                        if "copy_trading_calendar_events" in sql), None)
    if fresh and not failure:
        assert event_query == {
            "source": "tradingeconomics", "region": "United States", "importance": 3,
            "currencies": "EUR,USD", "before": 20, "after": 45,
        }


def test_calendar_status_formats_matching_event_in_iana_timezone_across_dst(monkeypatch):
    class ActiveEventConnection(_CalendarConnection):
        def run(self, sql, **params):
            if "SELECT event_title,currency,importance" in sql:
                return [(
                    "Employment report", "USD", 3,
                    datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
                    datetime(2026, 10, 25, 0, 30, 1, tzinfo=timezone.utc), False,
                )]
            return super().run(sql, **params)

    connection = ActiveEventConnection(active=True)
    service = CopyTradingService(lambda: connection, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    settings = academy_settings({"filtre_news": True})
    monkeypatch.setenv("COPY_TRADING_CALENDAR_PROVIDER", "tradingeconomics")
    monkeypatch.setenv("COPY_TRADING_CALENDAR_FAILURE_POLICY", "BLOCK")
    window = service.calendar_active_window(
        settings, preferences={"calendar_timezone": "Europe/Paris", "calendar_min_importance": 3},
    )
    assert window["starts_at_utc"] == "2026-10-25T00:30:00+00:00"
    assert window["starts_at_local"].endswith("+02:00")
    assert window["timezone"] == "Europe/Paris"


def _signed_calendar_post(client, payload, *, secret, worker_id="calendar-test"):
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    nonce = f"nonce-{time.time_ns()}"
    signature = canonical_copy_signature(
        secret, worker_id, "CALENDAR", "POST", "/internal/copy-trading/calendar/snapshot",
        timestamp, body, nonce,
    )
    return client.post(
        "/internal/copy-trading/calendar/snapshot", data=body,
        content_type="application/json", headers={
            "X-COPY-Worker-ID": worker_id, "X-COPY-Worker-Role": "CALENDAR",
            "X-COPY-Timestamp": timestamp, "X-COPY-Nonce": nonce,
            "X-COPY-Signature": signature,
        },
    )


class _CalendarRouteConnection:
    def __init__(self):
        self.calls = []

    def run(self, sql, **params):
        self.calls.append((sql, params))
        if "INSERT INTO copy_trading_worker_nonces" in sql:
            return [(params["nonce"],)]
        return []

    @staticmethod
    def close():
        pass


def test_signed_calendar_snapshot_replaces_complete_feed_atomically(monkeypatch):
    secret = "c" * 40
    monkeypatch.setenv("COPY_TRADING_CALENDAR_WORKER_SECRET", secret)
    connection = _CalendarRouteConnection()
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: connection, lambda _code: {}, lambda fn: fn, lambda fn: fn,
        lambda fn: fn, lambda _member: True,
    )
    payload = {
        "source": "tradingeconomics", "status": "OK", "configured": True,
        "failure_code": "", "coverage_start": "2026-11-08T00:00:00+00:00",
        "coverage_end": "2026-11-24T00:00:00+00:00",
        "events": [{
            "external_id": "12345", "region": "United States",
            "starts_at": "2026-11-09T14:30:00+00:00",
            "ends_at": "2026-11-09T14:30:01+00:00", "currency": "USD",
            "importance": 3, "event_title": "Employment report", "estimated_time": False,
        }],
    }
    response = _signed_calendar_post(app.test_client(), payload, secret=secret)
    assert response.status_code == 200
    sql_calls = [sql for sql, _params in connection.calls]
    assert sql_calls[0] == "DELETE FROM copy_trading_worker_nonces WHERE created_at<NOW()-INTERVAL '1 day'"
    assert "BEGIN" in sql_calls
    assert any("DELETE FROM copy_trading_calendar_events" in sql for sql in sql_calls)
    assert any("INSERT INTO copy_trading_calendar_sources" in sql for sql in sql_calls)
    assert "COMMIT" in sql_calls
    assert response.get_json()["event_count"] == 1


def test_signed_calendar_snapshot_rejects_invalid_or_duplicated_events(monkeypatch):
    secret = "d" * 40
    monkeypatch.setenv("COPY_TRADING_CALENDAR_WORKER_SECRET", secret)
    connection = _CalendarRouteConnection()
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: connection, lambda _code: {}, lambda fn: fn, lambda fn: fn,
        lambda fn: fn, lambda _member: True,
    )
    item = {
        "external_id": "12345", "region": "United States",
        "starts_at": "2026-11-09T14:30:00+00:00", "ends_at": "2026-11-09T14:30:01+00:00",
        "currency": "USD", "importance": 3, "event_title": "Employment report",
        "estimated_time": False,
    }
    payload = {
        "source": "tradingeconomics", "status": "OK", "configured": True,
        "failure_code": "", "coverage_start": "2026-11-08T00:00:00+00:00",
        "coverage_end": "2026-11-24T00:00:00+00:00", "events": [item, item],
    }
    response = _signed_calendar_post(app.test_client(), payload, secret=secret)
    assert response.status_code == 400
    assert not any("copy_trading_calendar_events" in sql for sql, _params in connection.calls)
