import unittest
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from marketing_automation import (
    EXPLORER_STAGES,
    MEMBER_ONBOARDING_START,
    _email_html,
    _explorer_candidate,
    _member_onboarding_candidate,
    _now,
    _personalized_subject,
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


if __name__ == "__main__":
    unittest.main()
