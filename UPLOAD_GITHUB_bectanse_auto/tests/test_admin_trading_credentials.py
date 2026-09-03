import json
import os
from datetime import datetime
from unittest.mock import patch


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test")
os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")

import app


class CredentialConnection:
    def __init__(self, params=None, claim=True):
        self.params = params or {}
        self.claim = claim
        self.calls = []

    def run(self, query, **params):
        compact = " ".join(query.split()).lower()
        self.calls.append((compact, params))
        if compact.startswith("select params from members"):
            return [[json.dumps(self.params)]]
        if compact.startswith("insert into admin_sensitive_access_log"):
            return []
        if compact.startswith("insert into admin_payment_notifications"):
            return [[params["key"]]] if self.claim else []
        if compact.startswith("update admin_payment_notifications"):
            return []
        raise AssertionError(f"Unexpected query: {compact}")

    def close(self):
        pass


def test_admin_can_reveal_mt_password_without_storing_it_in_plaintext():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    encrypted = app._protect_params({
        "mt_login": "998877",
        "serveur": "PUPrime-Live",
        "plateforme": "MT5",
        "mt_password": "secret-investisseur",
    })
    connection = CredentialConnection(encrypted)
    client = app.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["admin_authenticated"] = True

    with patch.object(app, "get_conn", return_value=connection):
        response = client.post("/admin/api/membre/mt-credentials", json={
            "code": "BCT-TEST1234",
        })

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["mt_login"] == "998877"
    assert payload["mt_server"] == "PUPrime-Live"
    assert payload["mt_password"] == "secret-investisseur"
    assert response.headers["Cache-Control"].startswith("no-store")
    assert "secret-investisseur" not in json.dumps(encrypted)
    assert any("admin_sensitive_access_log" in query for query, _ in connection.calls)


def test_payment_notification_contains_trading_identity_but_not_password():
    claim_connection = CredentialConnection(claim=True)
    update_connection = CredentialConnection()
    member = {
        "nom": "Client Test",
        "email": "client@example.com",
        "telephone": "+33600000000",
        "telegram": "@client",
        "params": {
            "plateforme": "MT5",
            "serveur": "PUPrime-Live",
            "mt_login": "445566",
            "mt_password": "secret-investisseur",
        },
    }
    context = {
        "subscription_id": "sub_test",
        "stripe_object_id": "cs_test",
        "invoice_id": "in_test",
        "payment_status": "paid",
        "billing_reason": "subscription_create",
        "amount_paid_cents": 50000,
        "period_end": datetime(2026, 10, 3, 12, 0),
        "plan": {"label": "1 mois", "amount_cents": 50000},
        "email": "client@example.com",
    }

    with patch.object(app, "get_conn", side_effect=[claim_connection, update_connection]), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "send_telegram", return_value=True) as telegram:
        sent = app._send_academy_payment_admin_notification(
            "evt_test", "checkout.session.completed", context, "BCT-TEST1234")

    assert sent is True
    message = telegram.call_args.args[0]
    markup = telegram.call_args.kwargs["reply_markup"]
    assert "445566" in message
    assert "PUPrime-Live" in message
    assert "secret-investisseur" not in message
    assert "fiche admin sécurisée" in message
    assert markup["inline_keyboard"][0][0]["url"].endswith("member=BCT-TEST1234")


def test_payment_notification_identity_separates_initial_and_renewal():
    base = {
        "subscription_id": "sub_test",
        "stripe_object_id": "cs_test",
        "invoice_id": "in_cycle",
        "payment_status": "paid",
        "billing_reason": "subscription_cycle",
    }
    assert app._payment_admin_notification_identity(
        "checkout.session.completed", base) == (
            "initial", "academy-payment:initial:sub_test")
    assert app._payment_admin_notification_identity(
        "invoice.paid", base) == (
            "renewal", "academy-payment:renewal:in_cycle")
