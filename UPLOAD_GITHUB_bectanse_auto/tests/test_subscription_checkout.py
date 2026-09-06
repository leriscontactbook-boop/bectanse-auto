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
        if "SELECT email,nom" in " ".join(sql.split()):
            return [["client@example.com", "Client", "", "", False]]
        return []

    def close(self):
        return None


class _StripeResponse:
    ok = True

    def json(self):
        return {
            "id": "cs_live_standard",
            "url": "https://checkout.stripe.com/c/pay/standard",
        }


class SubscriptionCheckoutTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    def test_disabled_promotion_query_never_reaches_stripe(self):
        client = app.app.test_client()
        with client.session_transaction() as session:
            session["member_code"] = "BCT-EXPLORER"

        with patch.object(app, "STRIPE_SECRET_KEY", "rk_test"), \
             patch.object(app, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             patch.object(app, "get_conn", return_value=_CheckoutConnection()), \
             patch.object(app, "record_checkout_session"), \
             patch.object(app.requests, "post", return_value=_StripeResponse()) as stripe_post:
            response = client.get(
                "/abonnement/checkout/1_month?promo=OLD_DISABLED_CODE",
            )

        self.assertEqual(response.status_code, 303)
        form = stripe_post.call_args.kwargs["data"]
        self.assertFalse(any(key.startswith("discounts[") for key in form))
        self.assertFalse(any("promotion" in key for key in form))
        self.assertNotIn("promo=", form["cancel_url"])

    def test_login_redirect_preserves_only_the_selected_plan(self):
        client = app.app.test_client()
        response = client.get(
            "/abonnement/checkout/1_month?promo=OLD_DISABLED_CODE",
        )

        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session["pending_academy_plan"], "1_month")
            self.assertNotIn("pending_academy_promo", session)


if __name__ == "__main__":
    unittest.main()
