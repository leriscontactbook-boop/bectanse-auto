import json
import time
from datetime import datetime, timezone
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
                 account_fingerprint=""):
        self.blocked = blocked
        self.terminal_state = terminal_state
        self.failure_code = failure_code
        self.diagnostic_code = diagnostic_code
        self.account_fingerprint = account_fingerprint

    def run(self, sql, **params):
        if "SELECT enabled FROM copy_trading_master_accounts" in sql:
            return [(True,)]
        if "SELECT consent_version,consented_at,revoked_at" in sql:
            return [("consent-v1", datetime.now(timezone.utc), None)]
        if "SELECT slot_role,status,last_seen_at" in sql:
            now = datetime.now(timezone.utc)
            return [("MASTER", "ONLINE", now), ("FOLLOWER", "ONLINE", now)]
        if "FROM copy_trading_terminal_probes WHERE member_code" in sql:
            now = datetime.now(timezone.utc)
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
        raise AssertionError(f"Unexpected status query: {sql}")

    @staticmethod
    def close():
        pass


def _status_client(monkeypatch, *, blocked=False, terminal_state="CONNECTED", failure_code="",
                   diagnostic_code=None):
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
        "params": {"mt_login": "123", "serveur": "Broker-Demo", "mt_password": "encrypted"},
    }
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: _StatusConnection(blocked=blocked, terminal_state=terminal_state,
                                       failure_code=failure_code,
                                       diagnostic_code=diagnostic_code,
                                       account_fingerprint=terminal_fingerprint), lambda _code: member,
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
    assert body["terminal_connection"]["state"] == "CONNECTED"
    assert body["terminal_connection"]["matches_account_profile"] is True


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


def test_reconciliation_lock_is_visible_and_prevents_ready_status(monkeypatch):
    response = _status_client(monkeypatch, blocked=True).get("/api/copy-trading/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["operational_readiness"] == "BLOCKED"
    assert "POSITION_RECONCILIATION_REQUIRED" in body["operational_blockers"]
    assert body["position_reconciliation_reason"] == "MASTER_CLOSE_EVENT_EXPIRED"


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
