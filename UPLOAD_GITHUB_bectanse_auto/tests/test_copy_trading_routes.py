from datetime import datetime, timezone
from functools import wraps

from flask import Flask, session

from copy_trading.routes import register_copy_trading


def _identity(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        return view(*args, **kwargs)
    return wrapped


class _StatusConnection:
    def __init__(self, *, blocked=False):
        self.blocked = blocked

    def run(self, sql, **params):
        if "SELECT enabled FROM copy_trading_master_accounts" in sql:
            return [(True,)]
        if "SELECT consent_version,consented_at,revoked_at" in sql:
            return [("consent-v1", datetime.now(timezone.utc), None)]
        if "SELECT slot_role,status,last_seen_at" in sql:
            now = datetime.now(timezone.utc)
            return [("MASTER", "ONLINE", now), ("FOLLOWER", "ONLINE", now)]
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


def _status_client(monkeypatch, *, blocked=False):
    monkeypatch.setenv("COPY_TRADING_MODE", "live")
    monkeypatch.setenv("COPY_TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("COPY_TRADING_LIVE_RELEASE_APPROVED", "true")
    monkeypatch.setenv("COPY_TRADING_CONSENT_VERSION", "consent-v1")
    monkeypatch.setenv("COPY_TRADING_MASTER_MEMBER_CODE", "MASTER")
    member = {
        "code": "FOLLOWER", "copy_actif": True,
        "params": {"mt_login": "123", "serveur": "Broker-Demo", "mt_password": "encrypted"},
    }
    app = Flask(__name__)
    app.secret_key = "test-only"
    register_copy_trading(
        app, lambda: _StatusConnection(blocked=blocked), lambda _code: member,
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


def test_reconciliation_lock_is_visible_and_prevents_ready_status(monkeypatch):
    response = _status_client(monkeypatch, blocked=True).get("/api/copy-trading/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["operational_readiness"] == "BLOCKED"
    assert "POSITION_RECONCILIATION_REQUIRED" in body["operational_blockers"]
    assert body["position_reconciliation_reason"] == "MASTER_CLOSE_EVENT_EXPIRED"
