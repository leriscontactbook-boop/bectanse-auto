import os
import unittest
from datetime import datetime, timedelta
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


class _WalletConnection:
    def __init__(self, access_level):
        self.access_level = access_level
        self.balance = None

    def run(self, sql, **params):
        compact = " ".join(sql.split()).lower()
        if "select coalesce(access_level" in compact:
            return [[self.access_level]]
        if "insert into analysis_wallets" in compact:
            if self.balance is None:
                self.balance = int(params["initial"])
            return []
        if "select balance, lifetime_granted" in compact:
            return [[self.balance, self.balance, 0]]
        raise AssertionError(f"Unexpected query: {compact}")


class AccessModelTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = app.app.test_client()

    def _login(self, code="BCT-FREE0001"):
        with self.client.session_transaction() as session:
            session["member_code"] = code

    def test_explorer_expired_and_suspended_accounts_are_observation_mode(self):
        now = datetime.now()
        explorer = {"code": "BCT-FREE0001", "access_level": "explorer", "actif": True}
        expired = {"code": "BCT-OLD00001", "access_level": "member", "actif": True,
                   "date_fin": now - timedelta(seconds=1)}
        suspended = {"code": "BCT-STOP0001", "access_level": "member", "actif": False,
                     "date_fin": now + timedelta(days=20)}
        active = {"code": "BCT-PAID0001", "access_level": "member", "actif": True,
                  "date_fin": now + timedelta(days=20)}

        self.assertTrue(app._current_demo_mode(member=explorer))
        self.assertTrue(app._current_demo_mode(member=expired))
        self.assertTrue(app._current_demo_mode(member=suspended))
        self.assertFalse(app._current_demo_mode(member=active))

    def test_explorer_wallet_starts_at_zero_but_member_wallet_starts_at_two(self):
        explorer_wallet = app._analysis_wallet(_WalletConnection("explorer"), "BCT-FREE0001")
        member_wallet = app._analysis_wallet(_WalletConnection("member"), "BCT-PAID0001")
        self.assertEqual(explorer_wallet["balance"], 0)
        self.assertEqual(member_wallet["balance"], app.ANALYSIS_INITIAL_CREDITS)

    def test_explorer_cannot_read_vip_channel_api(self):
        self._login()
        explorer = {"code": "BCT-FREE0001", "access_level": "explorer", "actif": True}
        with patch.object(app, "enforce_member_access_state"), patch.object(app, "get_member", return_value=explorer):
            response = self.client.get("/api/canal/messages")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.get_json()["locked"])
        self.assertEqual(response.get_json()["upgrade_url"], "/vip")

    def test_explorer_paid_pages_redirect_to_vip(self):
        self._login()
        explorer = {"code": "BCT-FREE0001", "access_level": "explorer", "actif": True}
        with patch.object(app, "enforce_member_access_state"), patch.object(app, "get_member", return_value=explorer):
            response = self.client.get("/support")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/vip")

    def test_legacy_shared_demo_session_must_create_an_individual_account(self):
        self._login("BCT-DEMO2026")
        response = self.client.get("/accueil")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_email_confirmation_falls_back_to_configured_smtp(self):
        with patch.object(app, "brevo_email_delivery_available", return_value=False), \
             patch.object(app, "send_email", return_value=True) as smtp:
            result = app.send_brevo_member_verification(
                "client@example.com", "Client", "https://example.com/confirm")
        self.assertTrue(result["ok"])
        self.assertEqual(result["message_id"], "gmail-smtp")
        smtp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
