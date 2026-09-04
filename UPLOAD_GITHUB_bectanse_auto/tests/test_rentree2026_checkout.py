import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATA_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test")
os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")

import app


class _CheckoutConnection:
    def run(self, sql, **params):
        compact = " ".join(sql.split())
        if compact.startswith("SELECT email,nom"):
            return [["client@example.com", "Client", "", "", False]]
        return []

    def close(self):
        return None


class _StripeResponse:
    ok = True

    def json(self):
        return {
            "id": "cs_live_rentree2026",
            "url": "https://checkout.stripe.com/c/pay/test",
        }


class _PromoNotificationConnection:
    def __init__(self, updated=True):
        self.updated = updated
        self.calls = []

    def run(self, sql, **params):
        self.calls.append((sql, params))
        return [[params["code"]]] if self.updated else []


class Rentree2026CheckoutTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["member_code"] = "BCT-EXPLORER"

    def test_promotion_is_applied_server_side_to_the_one_month_checkout(self):
        with patch.object(app, "STRIPE_SECRET_KEY", "rk_test"), \
             patch.object(app, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             patch.object(app, "STRIPE_RENTREE2026_PROMOTION_CODE_ID", "promo_test"), \
             patch.object(app, "get_conn", return_value=_CheckoutConnection()), \
             patch.object(app, "rentree2026_offer_active", return_value=True), \
             patch.object(app, "rentree2026_member_audience",
                          return_value="explorer_no_subscription"), \
             patch.object(app, "record_checkout_session"), \
             patch.object(app.requests, "post", return_value=_StripeResponse()) as stripe_post:
            response = self.client.get(
                "/abonnement/checkout/1_month?promo=RENTREE2026",
            )

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].startswith(
            "https://checkout.stripe.com/"))
        form = stripe_post.call_args.kwargs["data"]
        self.assertEqual(form["discounts[0][promotion_code]"], "promo_test")
        self.assertEqual(form["metadata[promotion_code]"], "RENTREE2026")
        self.assertEqual(
            form["metadata[promotion_audience]"], "explorer_no_subscription")

    def test_promotion_is_refused_for_an_ineligible_account(self):
        with patch.object(app, "STRIPE_SECRET_KEY", "rk_test"), \
             patch.object(app, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             patch.object(app, "STRIPE_RENTREE2026_PROMOTION_CODE_ID", "promo_test"), \
             patch.object(app, "get_conn", return_value=_CheckoutConnection()), \
             patch.object(app, "rentree2026_offer_active", return_value=True), \
             patch.object(app, "rentree2026_member_audience", return_value=""), \
             patch.object(app.requests, "post") as stripe_post:
            response = self.client.get(
                "/abonnement/checkout/1_month?promo=RENTREE2026",
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("promo=indisponible", response.headers["Location"])
        stripe_post.assert_not_called()

    def test_promotion_cannot_be_used_on_a_longer_plan(self):
        with patch.object(app, "STRIPE_SECRET_KEY", "rk_test"), \
             patch.object(app, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             patch.object(app, "STRIPE_RENTREE2026_PROMOTION_CODE_ID", "promo_test"), \
             patch.object(app, "get_conn", return_value=_CheckoutConnection()), \
             patch.object(app, "rentree2026_offer_active", return_value=True), \
             patch.object(app, "rentree2026_member_audience",
                          return_value="explorer_no_subscription"), \
             patch.object(app.requests, "post") as stripe_post:
            response = self.client.get(
                "/abonnement/checkout/3_months?promo=RENTREE2026",
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("promo=indisponible", response.headers["Location"])
        stripe_post.assert_not_called()

    def test_promotion_survives_the_account_login_redirect(self):
        client = app.app.test_client()
        response = client.get(
            "/abonnement/checkout/1_month?promo=RENTREE2026",
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session["pending_academy_plan"], "1_month")
            self.assertEqual(session["pending_academy_promo"], "RENTREE2026")

    def test_new_explorer_created_during_offer_gets_countdown_context(self):
        member = {
            "code": "BCT-NEW2026",
            "access_level": "explorer",
            "created_at": datetime(2026, 9, 4, 9, 0),
            "stripe_subscription_id": "",
            "billing_status": "",
        }
        offer = app._new_explorer_promo_context(
            member,
            now=datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("Europe/Paris")),
        )

        self.assertIsNotNone(offer)
        self.assertEqual(offer["code"], "RENTREE2026")
        self.assertEqual(offer["regular_price"], 500)
        self.assertEqual(offer["promo_price"], 350)
        self.assertEqual(offer["discount"], 150)
        self.assertEqual(offer["deadline_iso"], "2026-09-07T00:00:00+02:00")
        self.assertIn("promo=RENTREE2026", offer["checkout_url"])

    def test_old_or_paid_explorer_does_not_get_new_account_offer(self):
        old_member = {
            "code": "BCT-OLD",
            "access_level": "explorer",
            "created_at": datetime(2026, 9, 2, 21, 59),
            "stripe_subscription_id": "",
            "billing_status": "",
        }
        paid_member = {
            **old_member,
            "code": "BCT-PAID",
            "created_at": datetime(2026, 9, 4, 9, 0),
            "stripe_subscription_id": "sub_123",
            "billing_status": "active",
        }
        now = datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("Europe/Paris"))

        self.assertIsNone(app._new_explorer_promo_context(old_member, now=now))
        self.assertIsNone(app._new_explorer_promo_context(paid_member, now=now))

    def test_countdown_offer_disappears_at_sunday_deadline(self):
        member = {
            "code": "BCT-NEW2026",
            "access_level": "explorer",
            "created_at": datetime(2026, 9, 4, 9, 0),
            "stripe_subscription_id": "",
            "billing_status": "",
        }
        deadline = datetime(2026, 9, 7, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))

        self.assertIsNone(app._new_explorer_promo_context(member, now=deadline))

    def test_creation_notification_is_attached_only_when_offer_is_active(self):
        conn = _PromoNotificationConnection()
        with patch.object(app, "rentree2026_offer_active", return_value=True):
            attached = app._assign_new_explorer_promo_notification(conn, "BCT-NEW2026")

        self.assertTrue(attached)
        sql, params = conn.calls[0]
        self.assertIn("access_level,'member')='explorer'", sql)
        self.assertIn("stripe_subscription_id", sql)
        self.assertEqual(params["code"], "BCT-NEW2026")
        self.assertIn("150 € offerts", params["message"])
        self.assertIn("RENTREE2026", params["message"])


if __name__ == "__main__":
    unittest.main()
