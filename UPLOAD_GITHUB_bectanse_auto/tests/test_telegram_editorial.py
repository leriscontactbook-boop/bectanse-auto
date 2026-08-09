import os
import csv
import io
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

os.environ["BECTANSE_SKIP_STARTUP"] = "1"

import app


class TelegramEditorialCalendarTests(unittest.TestCase):
    def test_calendar_contains_four_complete_weeks(self):
        calendar = app.load_telegram_editorial_calendar()
        self.assertEqual(len(calendar["weeks"]), 4)
        self.assertTrue(all(len(week) == 7 for week in calendar["weeks"]))

    def test_weekdays_are_in_publication_order(self):
        expected = [
            "lundi", "mardi", "mercredi", "jeudi",
            "vendredi", "samedi", "dimanche"
        ]
        calendar = app.load_telegram_editorial_calendar()
        for week in calendar["weeks"]:
            self.assertEqual([post["weekday"] for post in week], expected)

    def test_all_28_posts_are_unique_safe_and_within_telegram_limit(self):
        start = date(2026, 8, 10)  # lundi
        messages = [
            app.build_daily_editorial_post(start + timedelta(days=offset))
            for offset in range(28)
        ]
        self.assertEqual(len(set(messages)), 28)
        for message in messages:
            self.assertLessEqual(len(message), 4096)
            self.assertIn("Contenu éducatif", message)
            self.assertIn("risque de perte", message)

    def test_scheduled_send_records_success(self):
        claim_conn = Mock()
        claim_conn.run.return_value = [["editorial-2026-08-10"]]
        finish_conn = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {
            "ok": True,
            "result": {"message_id": 12345}
        }

        with patch.object(app, "ECO_BOT_TOKEN", "test-token"), \
             patch.object(app, "ECO_CANAL", "@test-channel"), \
             patch.object(app, "get_conn", side_effect=[claim_conn, finish_conn]), \
             patch.object(app.requests, "post", return_value=response) as post:
            sent = app._send_scheduled_telegram(
                "message de test",
                "editorial-2026-08-10",
                "editorial"
            )

        self.assertTrue(sent)
        self.assertEqual(post.call_count, 1)
        self.assertIn("sendMessage", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "@test-channel")
        self.assertIn("UPDATE scheduled_publications", finish_conn.run.call_args.args[0])

    def test_locked_slot_is_not_sent_twice(self):
        claim_conn = Mock()
        claim_conn.run.return_value = []

        with patch.object(app, "ECO_BOT_TOKEN", "test-token"), \
             patch.object(app, "ECO_CANAL", "@test-channel"), \
             patch.object(app, "get_conn", return_value=claim_conn), \
             patch.object(app.requests, "post") as post:
            sent = app._send_scheduled_telegram(
                "message de test",
                "editorial-2026-08-10",
                "editorial"
            )

        self.assertFalse(sent)
        post.assert_not_called()

    def test_weekly_and_rotation_schedules_are_detected_in_paris_time(self):
        now = datetime(2026, 8, 10, 18, 34, tzinfo=app.PARIS_TZ)  # lundi
        weekly = {
            "schedule_type": "weekly", "weekdays": "0,2",
            "publish_time": "18:30", "enabled": True, "deleted": False
        }
        rotation = {
            **weekly,
            "schedule_type": "rotation",
            "rotation_week": (now.date().isocalendar().week - 1) % 4
        }
        self.assertTrue(app.scheduled_telegram_post_is_due(weekly, now=now))
        self.assertTrue(app.scheduled_telegram_post_is_due(rotation, now=now))
        self.assertFalse(app.scheduled_telegram_post_is_due(weekly, now=now + timedelta(minutes=10)))

    def test_post_payload_validates_image_caption_and_button(self):
        payload = app._validate_telegram_post_payload({
            "name": "Test image",
            "message": "Message éducatif",
            "image_url": "https://res.cloudinary.com/demo/image.jpg",
            "schedule_type": "weekly",
            "weekdays": [0, 3],
            "publish_time": "18:30",
            "channel": "@BECTANSE_ACADEMIE",
            "button_text": "Découvrir",
            "button_url": "https://acces.bectanse-academie.com",
            "enabled": True
        })
        self.assertEqual(payload["weekdays"], "0,3")
        self.assertEqual(payload["button_text"], "Découvrir")

    def test_once_payload_keeps_the_paris_wall_clock_time_when_edited(self):
        payload = app._validate_telegram_post_payload({
            "name": "Bon dimanche",
            "message": "Bon dimanche l’équipe",
            "schedule_type": "once",
            "scheduled_for": "2026-08-09T12:00:00+02:00",
            "publish_time": "12:00",
            "channel": "@BECTANSE_ACADEMIE",
            "enabled": True
        })

        self.assertEqual(payload["scheduled_for"], datetime(2026, 8, 9, 12, 0))
        self.assertIsNone(payload["scheduled_for"].tzinfo)

    def test_scheduled_send_supports_photo_and_button(self):
        claim_conn = Mock()
        claim_conn.run.return_value = [["telegram-post-1-20260810-1830"]]
        finish_conn = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"message_id": 678}}
        image_response = Mock()
        image_response.headers = {"Content-Type": "image/jpeg", "Content-Length": "12"}
        image_response.content = b"jpeg-content"
        image_response.raise_for_status.return_value = None

        with patch.object(app, "ECO_BOT_TOKEN", "test-token"), \
             patch.object(app, "get_conn", side_effect=[claim_conn, finish_conn]), \
             patch.object(app.requests, "get", return_value=image_response) as get, \
             patch.object(app.requests, "post", return_value=response) as post:
            sent = app._send_scheduled_telegram(
                "Légende de test", "telegram-post-1-20260810-1830", "custom-editorial",
                image_url="https://res.cloudinary.com/demo/image.jpg",
                channel="@test-channel", button_text="Découvrir",
                button_url="https://example.com", post_id=1
            )

        self.assertTrue(sent)
        get.assert_called_once()
        self.assertIn("sendPhoto", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["data"]["caption"], "Légende de test")
        reply_markup = json.loads(post.call_args.kwargs["data"]["reply_markup"])
        self.assertEqual(reply_markup["inline_keyboard"][0][0]["text"], "Découvrir")
        self.assertEqual(post.call_args.kwargs["files"]["photo"][1], b"jpeg-content")

    def test_photo_download_rejects_a_web_page(self):
        response = Mock()
        response.headers = {"Content-Type": "text/html"}
        response.content = b"<html></html>"
        response.raise_for_status.return_value = None
        with patch.object(app.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "JPEG ou PNG"):
                app._download_telegram_photo("https://example.com/photo")

    def test_bectanse_photo_is_read_locally_without_an_http_loop(self):
        with patch.object(app.requests, "get") as get:
            filename, image_bytes, content_type = app._download_telegram_photo(
                "https://bectanse-auto.up.railway.app/static/telegram-visuals/10-cta-avancer-avec-cadre-v3.png"
            )
        get.assert_not_called()
        self.assertEqual(filename, "10-cta-avancer-avec-cadre-v3.png")
        self.assertEqual(content_type, "image/png")
        self.assertTrue(image_bytes.startswith(b"\x89PNG"))

    def test_quiz_payload_creates_a_native_clickable_telegram_quiz(self):
        claim_conn = Mock()
        claim_conn.run.return_value = [["telegram-quiz-test"]]
        finish_conn = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"message_id": 901}}

        with patch.object(app, "ECO_BOT_TOKEN", "test-token"), \
             patch.object(app, "get_conn", side_effect=[claim_conn, finish_conn]), \
             patch.object(app.requests, "post", return_value=response) as post:
            sent = app._send_scheduled_telegram(
                "Question", "telegram-quiz-test", "custom-editorial",
                channel="@test-channel", post_type="quiz",
                poll_question="Quel est le ratio 1:2 ?",
                poll_options=["Risque double", "Gain potentiel double", "Sans risque"],
                poll_correct_option_ids=[1],
                poll_explanation="Le gain potentiel représente deux fois le risque."
            )

        self.assertTrue(sent)
        self.assertIn("sendPoll", post.call_args.args[0])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["type"], "quiz")
        self.assertEqual(payload["correct_option_ids"], [1])
        self.assertEqual(payload["options"][1]["text"], "Gain potentiel double")

    def test_quiz_validation_requires_options_and_a_correct_answer(self):
        payload = app._validate_telegram_post_payload({
            "name": "Quiz ratio",
            "post_type": "quiz",
            "poll_question": "Quel ratio vise deux unités de gain ?",
            "poll_options": ["1:1", "1:2", "2:1"],
            "poll_correct_option_ids": [1],
            "schedule_type": "weekly",
            "weekdays": [1],
            "publish_time": "18:30",
            "channel": "@BECTANSE_ACADEMIE"
        })
        self.assertEqual(payload["post_type"], "quiz")
        self.assertEqual(payload["poll_correct_option_ids"], "[1]")

    def test_week_csv_template_contains_seven_valid_native_posts(self):
        with open(app.TELEGRAM_CSV_TEMPLATE_PATH, encoding="utf-8-sig", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file, delimiter=";"))
        values = [
            app._csv_row_to_telegram_payload(row, line_number)
            for line_number, row in enumerate(rows, start=2)
        ]
        self.assertEqual(len(values), 7)
        self.assertIn("quiz", {item["post_type"] for item in values})
        self.assertIn("poll", {item["post_type"] for item in values})
        self.assertTrue(all(item["schedule_type"] == "once" for item in values))

    def test_master_week_csv_contains_54_valid_scheduled_publications(self):
        path = os.path.join(
            app.APP_DIR, "content",
            "planning_telegram_2026-08-10_au_2026-08-16.csv"
        )
        with open(path, encoding="utf-8-sig", newline="") as csv_file:
            rows = list(csv.DictReader(csv_file, delimiter=";"))
        values = [
            app._csv_row_to_telegram_payload(row, line_number)
            for line_number, row in enumerate(rows, start=2)
        ]
        self.assertEqual(len(values), 54)
        self.assertEqual(sum(item["post_type"] == "quiz" for item in values), 4)
        self.assertEqual(sum(item["post_type"] == "poll" for item in values), 2)
        self.assertTrue(all(item["enabled"] for item in values))
        self.assertTrue(all(item["publish_all_channels"] for item in values))
        self.assertTrue(all(item["button_text"] and item["button_url"] for item in values))
        self.assertEqual(sum(bool(item["image_url"]) for item in values), 24)
        self.assertTrue(all(
            not item["image_url"] for item in values
            if item["post_type"] in {"quiz", "poll"}
        ))
        self.assertTrue(all(
            len(item["message"]) <= (1024 if item["image_url"] else 4096)
            for item in values if item["post_type"] == "message"
        ))

    def test_visual_catalog_contains_market_and_conversion_templates(self):
        manifest_path = os.path.join(
            app.APP_DIR, "static", "telegram-visuals",
            "bectanse-market-signal-manifest-v2.json"
        )
        with open(manifest_path, encoding="utf-8") as source:
            manifest = json.load(source)
        self.assertEqual(manifest["version"], 3)
        self.assertEqual(len(manifest["templates"]), 10)
        self.assertEqual(sum(template["category"] == "conversion" for template in manifest["templates"]), 3)
        self.assertTrue(all(template["ctaText"] and template["ctaUrl"] for template in manifest["templates"]))

    def test_catalog_image_is_normalized_to_public_https_before_save(self):
        payload = app._validate_telegram_post_payload({
            "name": "CTA catalogue",
            "message": "Un message mentor.",
            "image_url": "/static/telegram-visuals/10-cta-avancer-avec-cadre-v3.webp",
            "schedule_type": "weekly",
            "weekdays": [0],
            "publish_time": "18:30",
            "publish_all_channels": True,
        })
        self.assertEqual(
            payload["image_url"],
            "https://acces.bectanse-academie.com/static/telegram-visuals/10-cta-avancer-avec-cadre-v3.png",
        )

    def test_telegram_photo_delivery_url_uses_supported_formats(self):
        self.assertEqual(
            app._telegram_photo_delivery_url(
                "https://bectanse-auto.up.railway.app/static/telegram-visuals/10-cta-avancer-avec-cadre-v3.webp"
            ),
            "https://bectanse-auto.up.railway.app/static/telegram-visuals/10-cta-avancer-avec-cadre-v3.png",
        )
        self.assertEqual(
            app._telegram_photo_delivery_url(
                "https://res.cloudinary.com/dqgd441is/image/upload/v1/catalogue.webp"
            ),
            "https://res.cloudinary.com/dqgd441is/image/upload/f_jpg,q_auto/v1/catalogue.jpg",
        )

    def test_custom_visual_catalog_payload_requires_a_real_cta_pair(self):
        payload = app._validate_telegram_media_payload({
            "title": "Mon visuel coaching",
            "image_url": "https://res.cloudinary.com/demo/image.webp",
            "category": "conversion",
            "caption": "Un message mentor.",
            "cta_text": "Découvrir Bectanse",
            "cta_url": "https://acces.bectanse-academie.com/",
        })
        self.assertEqual(payload["category"], "conversion")
        internal_payload = app._validate_telegram_media_payload({
            "title": "Visuel Bectanse interne",
            "image_url": "static/telegram-visuals/08-cta-systeme-bectanse-v3.webp",
        })
        self.assertEqual(
            internal_payload["image_url"],
            "https://acces.bectanse-academie.com/static/telegram-visuals/08-cta-systeme-bectanse-v3.webp",
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            app._validate_telegram_media_payload({
                "title": "Visuel non sécurisé",
                "image_url": "http://example.com/image.webp",
            })
        with self.assertRaisesRegex(ValueError, "ensemble"):
            app._validate_telegram_media_payload({
                "title": "CTA incomplet",
                "image_url": "https://example.com/image.webp",
                "cta_text": "Cliquer",
                "cta_url": "",
            })

    def test_admin_image_upload_returns_a_permanent_https_url(self):
        client = app.app.test_client()
        secure_url = "https://res.cloudinary.com/bectanse/image/upload/catalogue-test.png"
        telegram_url = "https://res.cloudinary.com/bectanse/image/upload/f_jpg,q_auto/catalogue-test.jpg"
        with patch.object(app, "upload_to_cloudinary", return_value=secure_url) as uploader:
            response = client.post(
                "/admin/api/telegram/upload",
                data={
                    "key": app.ADMIN_KEY,
                    "image": (io.BytesIO(b"test-image"), "catalogue-test.png"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], telegram_url)
        uploader.assert_called_once()

    def test_targeted_post_requires_at_least_one_channel(self):
        with self.assertRaisesRegex(ValueError, "Choisis au moins un canal"):
            app._validate_telegram_post_payload({
                "name": "Publication ciblée",
                "message": "Message éducatif",
                "schedule_type": "weekly",
                "weekdays": [0],
                "publish_time": "18:30",
                "publish_all_channels": False,
                "channel_ids": [],
            })

    def test_broadcast_sends_once_to_each_active_channel(self):
        targets = [
            {"id": 1, "name": "Académie", "chat_id": "@academie"},
            {"id": 2, "name": "VIP", "chat_id": "@vip"},
        ]
        with patch.object(app, "_resolve_telegram_targets", return_value=targets), \
             patch.object(app, "_send_scheduled_telegram", return_value=True) as send:
            delivery = app._broadcast_scheduled_telegram(
                "Message", "publication-20260810-1830", "custom-editorial",
                publish_all_channels=True
            )

        self.assertEqual(delivery["total"], 2)
        self.assertEqual(delivery["sent"], 2)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            {call.kwargs["channel"] for call in send.call_args_list},
            {"@academie", "@vip"}
        )
        slot_keys = {call.kwargs["slot_key"] for call in send.call_args_list}
        self.assertEqual(len(slot_keys), 2)

    def test_broadcast_recognizes_a_channel_already_delivered_during_retry(self):
        targets = [
            {"id": 1, "name": "Académie", "chat_id": "@academie"},
            {"id": 2, "name": "VIP", "chat_id": "@vip"},
        ]
        with patch.object(app, "_resolve_telegram_targets", return_value=targets), \
             patch.object(app, "_send_scheduled_telegram", side_effect=[False, True]), \
             patch.object(app, "_scheduled_publication_status", return_value="sent"):
            delivery = app._broadcast_scheduled_telegram(
                "Message", "publication-20260810-1830", "custom-editorial",
                publish_all_channels=True
            )

        self.assertEqual(delivery["sent"], 2)
        self.assertEqual(delivery["sent_now"], 1)
        self.assertEqual(delivery["failed"], 0)

    def test_economic_calendar_fails_closed_when_source_is_unavailable(self):
        with patch.object(app, "get_eco_calendar", return_value=None), \
             patch.object(app, "_send_scheduled_telegram") as public_send, \
             patch.object(app, "send_telegram") as admin_alert:
            sent = app.send_eco_message(date(2026, 8, 10))

        self.assertFalse(sent)
        public_send.assert_not_called()
        admin_alert.assert_called_once()

    def test_economic_calendar_uses_a_real_telegram_button(self):
        with patch.object(app, "get_eco_calendar", return_value=[]), \
             patch.object(app, "_broadcast_scheduled_telegram", return_value={
                 "total": 2, "sent": 2, "failed": 0, "channels": []
             }) as public_send, \
             patch.object(app.threading, "Thread"):
            sent = app.send_eco_message(date(2026, 8, 10))

        self.assertTrue(sent)
        self.assertEqual(public_send.call_args.kwargs["button_text"], "ACCÉDER À L’ESPACE")
        self.assertEqual(
            public_send.call_args.kwargs["button_url"],
            "https://acces.bectanse-academie.com/"
        )
        self.assertNotIn(
            "https://acces.bectanse-academie.com",
            public_send.call_args.args[0]
        )

    def test_admin_automation_page_renders_for_valid_key(self):
        client = app.app.test_client()
        response = client.get(f"/admin/telegram-automation?key={app.ADMIN_KEY}")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Centre de commande", page)
        self.assertIn("Bectanse Visual Catalog", page)
        self.assertIn("save-library-upload", page)

    def test_local_preview_renders_with_styles_and_demo_mode(self):
        client = app.app.test_client()
        response = client.get("/preview-admin-telegram", headers={"Host": "127.0.0.1:5000"})
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/static/css/admin-telegram-automation.css", page)
        self.assertIn('data-preview-mode="true"', page)

    def test_due_database_post_is_processed_once(self):
        now = datetime(2026, 8, 10, 18, 32, tzinfo=app.PARIS_TZ)
        row = (
            7, "Post du lundi", "Message", "", "weekly", "0", None,
            "18:30", None, "Europe/Paris", "@BECTANSE_ACADEMIE", "", "",
            False, True, None, False, None, now.replace(tzinfo=None), now.replace(tzinfo=None)
        )
        list_conn = Mock()
        list_conn.run.return_value = [row]
        update_conn = Mock()

        with patch.object(app, "get_conn", side_effect=[list_conn, update_conn]), \
             patch.object(app, "_send_saved_post_to_channels", return_value={
                 "total": 1, "sent": 1, "failed": 0, "channels": []
             }) as send:
            count = app.process_scheduled_telegram_posts(now=now)

        self.assertEqual(count, 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0]["id"], 7)
        self.assertIn("UPDATE telegram_scheduled_posts", update_conn.run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
