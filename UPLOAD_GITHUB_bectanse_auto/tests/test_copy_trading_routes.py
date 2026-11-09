import json
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, session

from copy_trading.routes import register_copy_trading
from copy_trading.security import canonical_copy_signature, terminal_account_fingerprint


def _identity(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        return view(*args, **kwargs)
    return wrapped


class _StatusConnection:
    def __init__(self, *, blocked=False, terminal_state="CONNECTED", failure_code="",
                 diagnostic_code=None,
                 account_fingerprint="", probe_age_seconds=0):
        self.blocked = blocked
        self.terminal_state = terminal_state
        self.failure_code = failure_code
        self.diagnostic_code = diagnostic_code
        self.account_fingerprint = account_fingerprint
        self.probe_age_seconds = probe_age_seconds

    def run(self, sql, **params):
        if "SELECT enabled FROM copy_trading_master_accounts" in sql:
            return [(True,)]
        if "SELECT consent_version,consented_at,revoked_at" in sql:
            return [("consent-v1", datetime.now(timezone.utc), None)]
        if "SELECT slot_role,status,last_seen_at" in sql:
            now = datetime.now(timezone.utc)
            return [("MASTER", "ONLINE", now), ("FOLLOWER", "ONLINE", now)]
        if "FROM copy_trading_terminal_probes WHERE member_code" in sql:
            now = datetime.now(timezone.utc) - timedelta(seconds=self.probe_age_seconds)
            return [(self.terminal_state, self.failure_code, self.diagnostic_code, now, 1234, 1,
                     self.account_fingerprint, "Broker-Demo", self.terminal_state == "CONNECTED")]
        if "SELECT status,COUNT(*) FROM copy_trading_jobs" in sql:
            return []
        if "SELECT state,COUNT(*) FROM copy_trading_symbol_mappings" in sql:
            return []
        if "SELECT 1 FROM copy_trading_jobs" in sql:
            # Demo acceptance is a prerequisite for live mode too.
            assert set(params) == {"code"}
            return [(1,)]
        if "SELECT blocked,blocked_reason FROM copy_trading_follower_locks" in sql:
            return [(self.blocked, "MASTER_CLOSE_EVENT_EXPIRED" if self.blocked else "")]
        if "FROM copy_trading_account_protections WHERE member_code" in sql:
            return []
        raise AssertionError(f"Unexpected status query: {sql}")

    @staticmethod
    def close():
        pass


def _status_client(monkeypatch, *, blocked=False, terminal_state="CONNECTED", failure_code="",
                   diagnostic_code=None, probe_age_seconds=0, member_params=None):
    monkeypatch.setenv("COPY_TRADING_TERMINAL_PROBE_TTL_SECONDS", "90")
    monkeypatch.setenv("COPY_TRADING_MODE", "live")
    monkeypatch.setenv("COPY_TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("COPY_TRADING_LIVE_RELEASE_APPROVED", "true")
    monkeypatch.setenv("COPY_TRADING_CONSENT_VERSION", "consent-v1")
    monkeypatch.setenv("COPY_TRADING_MASTER_MEMBER_CODE", "MASTER")
    follower_secret = "f" * 40
    monkeypatch.setenv("COPY_TRADING_FOLLOWER_WORKER_SECRET", follower_secret)
    terminal_fingerprint = terminal_account_fingerprint(follower_secret, "123", "Broker-Demo")
    member = {
        "code": "FOLLOWER", "copy_actif": True,
        "params": {
            "mt_login": "123", "serveur": "Broker-Demo", "mt_password": "encrypted",
            **(member_params or {}),
        },
    }
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: _StatusConnection(blocked=blocked, terminal_state=terminal_state,
                                       failure_code=failure_code,
                                       diagnostic_code=diagnostic_code,
                                       account_fingerprint=terminal_fingerprint,
                                       probe_age_seconds=probe_age_seconds), lambda _code: member,
        _identity, _identity, _identity, lambda _member: True,
    )
    client = app.test_client()
    with client.session_transaction() as client_session:
        client_session["member_code"] = "FOLLOWER"
    return client


def test_live_readiness_still_requires_recent_demo_e2e_evidence(monkeypatch):
    response = _status_client(monkeypatch).get("/api/copy-trading/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["demo_account_end_to_end_verified"] is True
    assert body["operational_readiness"] == "READY"


def test_calendar_filter_without_provider_is_an_operational_blocker(monkeypatch):
    monkeypatch.delenv("COPY_TRADING_CALENDAR_PROVIDER", raising=False)
    response = _status_client(monkeypatch, member_params={"filtre_news": True}).get(
        "/api/copy-trading/status"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["settings"]["calendar_filter_state"] == "CALENDAR_PROVIDER_NOT_CONFIGURED"
    assert body["operational_readiness"] == "BLOCKED"
    assert "CALENDAR_FEED_STALE_OR_UNAVAILABLE" in body["operational_blockers"]
    assert body["terminal_connection"]["state"] == "CONNECTED"
    assert body["terminal_connection"]["matches_account_profile"] is True
    assert body["terminal_connection"]["freshness_ttl_seconds"] == 90


def test_recent_terminal_connection_failure_blocks_readiness_with_native_code(monkeypatch):
    response = _status_client(monkeypatch, terminal_state="FAILED",
                              failure_code="TERMINAL_CONNECTION_FAILED",
                              diagnostic_code=-10005).get(
        "/api/copy-trading/status"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["operational_readiness"] == "BLOCKED"
    assert "TERMINAL_CONNECTION_FAILED_-10005" in body["operational_blockers"]
    assert body["terminal_connection"]["diagnostic_code"] == -10005


def test_stale_connected_terminal_probe_is_never_reported_as_connected(monkeypatch):
    response = _status_client(monkeypatch, probe_age_seconds=91).get(
        "/api/copy-trading/status"
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["operational_readiness"] == "BLOCKED"
    assert body["terminal_connection"]["state"] == "STALE"
    assert body["terminal_connection"]["last_observed_state"] == "CONNECTED"
    assert body["terminal_connection"]["recent"] is False
    assert "TERMINAL_PROBE_STALE" in body["operational_blockers"]


def test_stale_native_connection_failure_keeps_its_mt5_error_visible(monkeypatch):
    response = _status_client(
        monkeypatch, terminal_state="FAILED", failure_code="TERMINAL_CONNECTION_FAILED",
        diagnostic_code=-10005, probe_age_seconds=120,
    ).get("/api/copy-trading/status")
    body = response.get_json()
    assert body["operational_readiness"] == "BLOCKED"
    assert body["terminal_connection"]["state"] == "FAILED"
    assert "TERMINAL_CONNECTION_FAILED_-10005" in body["operational_blockers"]


def test_status_no_longer_blocks_supported_pending_order_preference(monkeypatch):
    response = _status_client(
        monkeypatch, member_params={"copier_ordres_en_attente": True}
    ).get("/api/copy-trading/status")
    body = response.get_json()
    assert body["operational_readiness"] == "READY"
    assert "PENDING_ORDER_COPY_NOT_SUPPORTED" not in body["operational_blockers"]
    assert body["settings"]["enabled_unsupported_pending_settings"] == []


def test_reconciliation_lock_is_visible_and_prevents_ready_status(monkeypatch):
    response = _status_client(monkeypatch, blocked=True).get("/api/copy-trading/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["operational_readiness"] == "BLOCKED"
    assert "POSITION_RECONCILIATION_REQUIRED" in body["operational_blockers"]
    assert body["position_reconciliation_reason"] == "MASTER_CLOSE_EVENT_EXPIRED"


class _OperationsConnection:
    def run(self, sql, **params):
        if "FROM copy_trading_workers ORDER BY" in sql:
            return []
        if "FROM copy_trading_terminal_probes ORDER BY" in sql:
            return []
        if "GROUP BY execution_action,status" in sql:
            return [("OPEN", "COMPLETE", 2), ("CLOSE", "COMPLETE", 1)]
        if "FROM copy_trading_events" in sql:
            return [("COMPLETE", 3)]
        if "native_evidence #>> '{market,is_demo}'='true'" in sql:
            return [("BCT-MASTER", "BCT-FOLLOWER", "OPEN", 2),
                    ("BCT-MASTER", "BCT-FOLLOWER", "CLOSE", 1)]
        if "FROM copy_trading_position_links" in sql:
            return [("OPEN", 1), ("CLOSED", 1)]
        if "status='VERIFY_REQUIRED'" in sql:
            return [(0,)]
        if "FROM copy_trading_jobs" in sql and "GROUP BY status" in sql:
            return [("COMPLETE", 3)]
        if "FROM copy_trading_audit_events" in sql or "FROM copy_trading_follower_locks" in sql:
            return []
        raise AssertionError(f"Unexpected operations query: {sql}")

    @staticmethod
    def close():
        pass


def test_admin_operations_report_exposes_read_only_demo_and_recovery_evidence(monkeypatch):
    monkeypatch.setenv("COPY_TRADING_MODE", "demo")
    monkeypatch.setenv("COPY_TRADING_EXECUTION_ENABLED", "true")
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, _OperationsConnection, lambda _code: {},
        _identity, _identity, _identity, lambda _member: True,
    )

    response = app.test_client().get("/admin/api/copy-trading/operations")
    body = response.get_json()

    assert response.status_code == 200
    assert body["events_24h"] == {"COMPLETE": 3}
    assert body["job_actions_24h"] == {
        "OPEN": {"COMPLETE": 2}, "CLOSE": {"COMPLETE": 1},
    }
    assert body["demo_native_confirmations_by_pair_24h"] == [
        {"master_member_code": "BCT-MASTER", "follower_member_code": "BCT-FOLLOWER",
         "action": "open", "count": 2},
        {"master_member_code": "BCT-MASTER", "follower_member_code": "BCT-FOLLOWER",
         "action": "close", "count": 1},
    ]
    assert body["position_links_by_state"] == {"OPEN": 1, "CLOSED": 1}
    assert body["recovery_required_jobs_24h"] == 0


class _HeartbeatConnection:
    def __init__(self, statements):
        self.statements = statements

    def run(self, sql, **params):
        if "DELETE FROM copy_trading_worker_nonces" in sql:
            return []
        if "INSERT INTO copy_trading_worker_nonces" in sql:
            return [(params["nonce"],)]
        if "SELECT status FROM copy_trading_workers" in sql:
            return []
        if "SELECT follower_member_code FROM copy_trading_jobs" in sql:
            return [("FOLLOWER",)]
        self.statements.append((sql, params))
        return []

    @staticmethod
    def close():
        pass


def test_signed_worker_heartbeat_persists_native_connection_failure(monkeypatch):
    secret = "f" * 40
    monkeypatch.setenv("COPY_TRADING_FOLLOWER_WORKER_SECRET", secret)
    monkeypatch.setenv("COPY_TRADING_MASTER_MEMBER_CODE", "MASTER")
    statements = []
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: _HeartbeatConnection(statements), lambda _code: {},
        _identity, _identity, _identity, lambda _member: True,
    )

    path = "/internal/copy-trading/workers/heartbeat"
    payload = {
        "role": "FOLLOWER", "instance_id": "node-follower-01", "status": "DEGRADED",
        "current_job_id": "job-1", "terminal_probe": {
            "member_code": "FOLLOWER",
            "account_fingerprint": terminal_account_fingerprint(secret, "123", "Broker-Demo"),
            "server": "Broker-Demo",
            "state": "FAILED", "failure_code": "TERMINAL_CONNECTION_FAILED",
            "terminal_path_verified": False, "diagnostic_code": -10005,
            "process_id": 4321, "session_id": 2,
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    nonce = "unique-test-nonce"
    headers = {
        "Content-Type": "application/json", "X-COPY-Worker-ID": "node-follower-01",
        "X-COPY-Worker-Role": "FOLLOWER", "X-COPY-Timestamp": timestamp,
        "X-COPY-Nonce": nonce,
        "X-COPY-Signature": canonical_copy_signature(
            secret, "node-follower-01", "FOLLOWER", "POST", path, timestamp, raw, nonce,
        ),
    }

    response = app.test_client().post(path, data=raw, headers=headers)

    assert response.status_code == 200
    probe_insert = next((params for sql, params in statements
                         if "INSERT INTO copy_trading_terminal_probes" in sql), None)
    assert probe_insert is not None
    assert probe_insert["state"] == "FAILED"
    assert probe_insert["failure_code"] == "TERMINAL_CONNECTION_FAILED"
    assert probe_insert["diagnostic"] == -10005
    assert probe_insert["path_verified"] is False
    assert probe_insert["process_id"] == 4321
    assert probe_insert["session_id"] == 2
