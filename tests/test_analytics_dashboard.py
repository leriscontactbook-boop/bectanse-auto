import base64
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test-public")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test-private")
os.environ["BECTANSE_SKIP_STARTUP"] = "1"

import app as bectanse_app


def test_seven_day_window_uses_seven_paris_calendar_days():
    now = datetime(2026, 8, 28, 22, 53, tzinfo=ZoneInfo("Europe/Paris"))
    window = bectanse_app._analytics_period_window("7d", now=now)

    assert window["since_paris"].isoformat() == "2026-08-22T00:00:00+02:00"
    assert window["until_paris"] == now
    assert window["bucket"] == "day"
    assert window["previous_until"] - window["previous_since"] == window["until"] - window["since"]


def test_day_window_contains_exactly_twenty_four_hour_buckets():
    now = datetime(2026, 8, 28, 22, 53, tzinfo=ZoneInfo("Europe/Paris"))
    window = bectanse_app._analytics_period_window("1d", now=now)

    assert window["since_paris"].isoformat() == "2026-08-27T23:00:00+02:00"
    assert int((window["until_paris"].replace(minute=0) - window["since_paris"]).total_seconds() / 3600) + 1 == 24
    assert window["previous_until"] - window["previous_since"] == window["until"] - window["since"]


def test_change_never_invents_percentage_without_previous_value():
    assert bectanse_app._analytics_change(0, 0) == 0.0
    assert bectanse_app._analytics_change(5, 0) is None
    assert bectanse_app._analytics_change(15, 10) == 50.0
    assert bectanse_app._analytics_change(5, 10) == -50.0


def test_stripe_context_keeps_only_auditable_payment_fields():
    context = bectanse_app._stripe_subscription_context(
        "invoice.paid",
        {
            "id": "in_test",
            "customer": "cus_test",
            "subscription": "sub_test",
            "amount_paid": 50000,
            "currency": "eur",
            "billing_reason": "subscription_cycle",
            "lines": {"data": [{"price": {"id": next(iter(bectanse_app.ACADEMY_SUBSCRIPTION_PLANS))}}]},
        },
    )

    assert context["invoice_id"] == "in_test"
    assert context["amount_paid_cents"] == 50000
    assert context["currency"] == "eur"
    assert context["billing_reason"] == "subscription_cycle"


def test_admin_dashboard_explains_each_metric_and_separates_units():
    template = Path("templates/admin_panel.html").read_text()
    assert "Visiteurs uniques" in template
    assert "Pages vues" in template
    assert "Sessions" in template
    assert "Lecture certifiée" in template
    assert template.count('class="metric-info"') >= 20
    assert "Activité par visiteur" in template


def test_tracker_records_engagement_without_collecting_form_values():
    tracker = Path("static/analytics.js").read_text()
    assert "page_engaged" in tracker
    assert "scroll_depth" in tracker
    assert "page_exit" in tracker
    assert "active_seconds" in tracker
    assert "FormData" not in tracker

