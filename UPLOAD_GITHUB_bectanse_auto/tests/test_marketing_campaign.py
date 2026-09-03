import unittest
from datetime import datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from marketing_automation import (
    EXPIRED_MONTHLY_CONTENT,
    EXPLORER_STAGES,
    MEMBER_ONBOARDING_START,
    RENTREE2026_CODE,
    RENTREE2026_STAGES,
    _email_html,
    _explorer_candidate,
    _member_onboarding_candidate,
    _legacy_delivery_health,
    _now,
    _personalized_subject,
    _rentree2026_candidate,
    _renewal_candidate,
    rentree2026_member_audience,
    rentree2026_offer_active,
)


class _EmailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "img":
            self.images.append(attributes)


class _NoQueryConnection:
    def run(self, sql, **params):
        raise AssertionError("Aucune requête ne doit être exécutée pour ce statut")


class _DeliveryConnection:
    def __init__(self, sent, events):
        self.sent = sent
        self.events = events

    def run(self, sql, **params):
        if "SELECT COUNT(*) FROM marketing_email_log" in sql:
            return [[self.sent]]
        if "SELECT event_type,COUNT(*) FROM marketing_email_events" in sql:
            return [[name, count] for name, count in self.events.items()]
        raise AssertionError("Requête de délivrabilité inattendue")


class _MonthlyReactivationConnection:
    def __init__(self, latest_sent, monthly_already_sent=False):
        self.latest_sent = latest_sent
        self.monthly_already_sent = monthly_already_sent

    def run(self, sql, **params):
        compact = " ".join(sql.split())
        if compact.startswith("SELECT 1 FROM marketing_email_log"):
            if params["journey"] == "reactivation" and params["stage"] == "expire-0":
                return [[1]]
            if params["journey"] == "reactivation_monthly":
                return [[1]] if self.monthly_already_sent else []
            return [[1]]
        if compact.startswith("SELECT MAX(sent_at) FROM marketing_email_log"):
            return [[self.latest_sent]]
        if compact.startswith("SELECT sent_at FROM marketing_email_log"):
            return [[self.latest_sent]]
        raise AssertionError(f"Requête mensuelle inattendue: {compact}")


class _RentreeConnection:
    def __init__(self, member_row, sent_stages=None):
        self.member_row = member_row
        self.sent_stages = set(sent_stages or [])

    def run(self, sql, **params):
        compact = " ".join(sql.split())
        if compact.startswith("SELECT COALESCE(m.access_level"):
            return [self.member_row]
        if compact.startswith("SELECT 1 FROM marketing_email_log"):
            return [[1]] if params.get("stage") in self.sent_stages else []
        raise AssertionError(f"Requête RENTREE2026 inattendue: {compact}")


class MarketingCampaignTests(unittest.TestCase):
    def test_internal_clock_matches_railway_utc_storage(self):
        self.assertLess(abs((_now() - datetime.utcnow()).total_seconds()), 3)

    def test_explorer_sequence_has_ten_unique_ordered_stages(self):
        self.assertEqual(len(EXPLORER_STAGES), 10)
        self.assertEqual(len({item["stage"] for item in EXPLORER_STAGES}), 10)
        delays = [item["delay_hours"] for item in EXPLORER_STAGES]
        self.assertEqual(delays, sorted(delays))
        for item in EXPLORER_STAGES:
            self.assertTrue(item.get("preheader"))
            self.assertTrue(item.get("cta"))
            self.assertTrue(item.get("target_url", "").startswith(
                "https://acces.bectanse-academie.com/"))

    def test_premium_email_has_one_primary_cta_and_one_discreet_unsubscribe(self):
        for item in EXPLORER_STAGES:
            rendered = _email_html(
                "Leris", item, "explorer", item["stage"],
                "https://example.test/unsubscribe",
            )
            parser = _EmailParser()
            parser.feed(rendered)
            tracked_links = [link for link in parser.links if "utm_campaign=" in link]
            self.assertEqual(len(tracked_links), 1, item["stage"])
            params = parse_qs(urlsplit(tracked_links[0]).query)
            self.assertEqual(params["utm_source"], ["brevo"])
            self.assertEqual(params["utm_medium"], ["email"])
            self.assertEqual(params["utm_content"], [item["stage"]])
            self.assertEqual(rendered.count("se désinscrire"), 1)
            self.assertIn("font-size:9px", rendered)
            self.assertIn("width=device-width", rendered)
            self.assertTrue(all(image.get("alt", "").strip()
                                for image in parser.images))

    def test_missing_first_name_never_produces_bonjour_bonjour(self):
        item = EXPLORER_STAGES[0]
        rendered = _email_html(
            "", item, "explorer", item["stage"],
            "https://example.test/unsubscribe",
        )
        self.assertNotIn("Bonjour Bonjour", rendered)
        self.assertEqual(
            _personalized_subject(item["subject"], ""),
            "pourquoi Bectanse existe vraiment",
        )

    def test_paid_member_is_immediately_out_of_explorer_journey(self):
        contact = (
            "BCT-PAID0001", "client@example.com", "Client", "active",
            datetime.now() - timedelta(days=5),
        )
        self.assertIsNone(_explorer_candidate(_NoQueryConnection(), contact))

    def test_historical_member_does_not_receive_new_onboarding_retroactively(self):
        old_activation = MEMBER_ONBOARDING_START - timedelta(days=30)
        contact = (
            "BCT-OLD00001", "ancien@example.com", "Ancien", "active",
            old_activation, None, "active", "sub_old", False, old_activation,
        )
        self.assertIsNone(
            _member_onboarding_candidate(_NoQueryConnection(), contact))

    def test_one_unsubscribe_on_small_sample_does_not_block_the_whole_base(self):
        healthy, reason, health = _legacy_delivery_health(
            _DeliveryConnection(50, {"unsubscribed": 1, "delivered": 47})
        )
        self.assertTrue(healthy)
        self.assertEqual(reason, "")
        self.assertEqual(health["unsubscribed"], 1)

    def test_confirmed_unsubscribe_trend_still_pauses_reactivation(self):
        healthy, reason, _health = _legacy_delivery_health(
            _DeliveryConnection(250, {"unsubscribed": 3, "delivered": 240})
        )
        self.assertFalse(healthy)
        self.assertIn("désabonnements", reason)

    def test_expired_member_receives_one_monthly_campaign_at_month_start(self):
        current = datetime(2026, 9, 3, 10, 0)
        contact = (
            "BCT-OLD00002", "ancien@example.com", "Ancien", "expired",
            datetime(2025, 1, 1), datetime(2026, 7, 1),
        )
        connection = _MonthlyReactivationConnection(
            latest_sent=current - timedelta(days=4),
        )
        with patch("marketing_automation._now", return_value=current):
            candidate = _renewal_candidate(connection, contact)

        self.assertIsNotNone(candidate)
        journey, content, reference, _due = candidate
        self.assertEqual(journey, "reactivation_monthly")
        self.assertEqual(reference, "2026-09")
        self.assertIn(content, EXPIRED_MONTHLY_CONTENT)

    def test_monthly_reactivation_emails_keep_tracking_and_unsubscribe(self):
        for content in EXPIRED_MONTHLY_CONTENT:
            rendered = _email_html(
                "Leris", content, "reactivation_monthly", content["stage"],
                "https://example.test/unsubscribe",
            )
            parser = _EmailParser()
            parser.feed(rendered)
            tracked_links = [link for link in parser.links if "utm_campaign=" in link]
            self.assertEqual(len(tracked_links), 1, content["stage"])
            params = parse_qs(urlsplit(tracked_links[0]).query)
            self.assertEqual(params["utm_campaign"], ["bectanse_reactivation-monthly"])
            self.assertEqual(rendered.count("se désinscrire"), 1)

    def test_monthly_campaign_waits_after_a_recent_reactivation(self):
        current = datetime(2026, 9, 3, 10, 0)
        contact = (
            "BCT-OLD00003", "ancien2@example.com", "Ancien", "expired",
            datetime(2025, 1, 1), datetime(2026, 7, 1),
        )
        connection = _MonthlyReactivationConnection(
            latest_sent=current - timedelta(days=1),
        )
        with patch("marketing_automation._now", return_value=current):
            candidate = _renewal_candidate(connection, contact)

        self.assertIsNone(candidate)

    def test_active_member_never_enters_monthly_reactivation(self):
        contact = (
            "BCT-ACTIVE01", "active@example.com", "Active", "active",
            datetime(2026, 8, 1), datetime(2026, 10, 1),
        )
        self.assertIsNone(_renewal_candidate(_NoQueryConnection(), contact))

    def test_rentree2026_separates_expired_members_and_never_paid_explorers(self):
        expired_row = (
            "member", False, "canceled", "sub_old", False,
            datetime(2026, 8, 1), False,
        )
        explorer_row = (
            "explorer", False, "", "", True, None, False,
        )
        active_row = (
            "member", False, "active", "sub_active", True,
            datetime(2026, 10, 1), True,
        )
        self.assertEqual(
            rentree2026_member_audience(_RentreeConnection(expired_row), "BCT-OLD"),
            "expired_members",
        )
        self.assertEqual(
            rentree2026_member_audience(_RentreeConnection(explorer_row), "BCT-NEW"),
            "explorer_no_subscription",
        )
        self.assertEqual(
            rentree2026_member_audience(_RentreeConnection(active_row), "BCT-ACTIVE"),
            "",
        )

    def test_rentree2026_candidate_uses_the_correct_audience_journey(self):
        current = datetime(2026, 9, 3, 10, 30)
        expired_contact = (
            "BCT-OLD", "ancien@example.com", "Ancien", "expired",
            datetime(2025, 1, 1), datetime(2026, 8, 1),
        )
        expired_row = (
            "member", False, "canceled", "sub_old", False,
            datetime(2026, 8, 1), False,
        )
        with patch("marketing_automation._now", return_value=current):
            candidate = _rentree2026_candidate(
                _RentreeConnection(expired_row), expired_contact)
        self.assertIsNotNone(candidate)
        journey, content, reference, _due = candidate
        self.assertEqual(journey, "promo_rentree2026_expired")
        self.assertEqual(content["stage"], "jeudi-retour")
        self.assertEqual(reference, RENTREE2026_CODE)

    def test_rentree2026_late_entry_receives_the_current_day_message(self):
        current = datetime(2026, 9, 4, 10, 30)
        explorer_contact = (
            "BCT-NEW", "nouveau@example.com", "Nouveau", "explorer",
            datetime(2026, 8, 20), None,
        )
        explorer_row = ("explorer", False, "", "", True, None, False)
        with patch("marketing_automation._now", return_value=current):
            candidate = _rentree2026_candidate(
                _RentreeConnection(explorer_row), explorer_contact)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate[1]["stage"], "vendredi-experience")

    def test_rentree2026_is_closed_after_sunday_2359_paris(self):
        self.assertTrue(rentree2026_offer_active(datetime(2026, 9, 6, 21, 59)))
        self.assertFalse(rentree2026_offer_active(datetime(2026, 9, 6, 22, 0)))

    def test_rentree2026_emails_are_simple_tracked_and_legally_clear(self):
        for audience, contents in RENTREE2026_STAGES.items():
            journey = "promo_rentree2026_" + (
                "expired" if audience == "expired_members" else "explorer")
            for content in contents:
                rendered = _email_html(
                    "Leris", content, journey, content["stage"],
                    "https://example.test/unsubscribe",
                )
                parser = _EmailParser()
                parser.feed(rendered)
                tracked_links = [link for link in parser.links if "utm_campaign=" in link]
                self.assertEqual(len(tracked_links), 1, content["stage"])
                self.assertEqual(rendered.count("se désinscrire"), 1)
                self.assertIn("350 € au lieu de 500 €", rendered)
                self.assertIn("Renouvellement ensuite à 500 € par mois", rendered)


if __name__ == "__main__":
    unittest.main()
