import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
