"""Contrôle puis programme la semaine Telegram du 31 août au 6 septembre 2026.

Sans ``--apply``, le script est strictement en lecture seule. Avec ``--apply``,
il désactive les anciennes routines répétitives puis synchronise les posts de
la semaine sur le seul canal Bectanse Académie.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLAN_PATH = ROOT / "content" / "planning_telegram_2026-08-31_au_2026-09-06.json"
BASE_URL = "https://acces.bectanse-academie.com"
LEGACY_ACTIVE_IDS = (30, 35, 36, 39, 44, 45, 48, 53, 54, 57, 62, 63, 66, 71, 72, 484, 1605, 2110)


def load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def materialize_post(raw: dict, plan: dict) -> dict:
    post_type = raw["post_type"]
    links = plan["links"]
    image_url = f"{BASE_URL}{raw['image_path']}" if raw.get("image_path") else ""
    button_url = links.get(raw.get("button_link", ""), "")
    return {
        "source_key": raw["source_key"],
        "name": raw["name"],
        "message": raw.get("message") or raw.get("poll_question", ""),
        "image_url": image_url,
        "post_type": post_type,
        "poll_question": raw.get("poll_question", ""),
        "poll_options": json.dumps(raw.get("poll_options", []), ensure_ascii=False),
        "poll_correct_option_ids": json.dumps(raw.get("poll_correct_option_ids", [])),
        "poll_explanation": raw.get("poll_explanation", ""),
        "poll_anonymous": True,
        "poll_multiple": False,
        "schedule_type": "once",
        "weekdays": "",
        "rotation_week": None,
        "publish_time": raw["scheduled_for"][11:16],
        "scheduled_for": datetime.fromisoformat(raw["scheduled_for"]),
        "timezone": plan["timezone"],
        "channel": plan["channel"],
        "button_text": raw.get("button_text", ""),
        "button_url": button_url,
        "disable_notification": False,
        "enabled": True,
        "deleted": False,
        "publish_all_channels": False,
    }


def validate(posts: list[dict], plan: dict) -> list[str]:
    errors: list[str] = []
    seen_keys: set[str] = set()
    seen_slots: set[datetime] = set()
    start = datetime.fromisoformat(f"{plan['period']['start']}T00:00:00")
    end = datetime.fromisoformat(f"{plan['period']['end']}T23:59:59")
    for post in posts:
        key = post["source_key"]
        slot = post["scheduled_for"]
        if key in seen_keys:
            errors.append(f"source_key en double : {key}")
        if slot in seen_slots:
            errors.append(f"horaire en double : {slot.isoformat()}")
        seen_keys.add(key)
        seen_slots.add(slot)
        if not start <= slot <= end:
            errors.append(f"post hors période : {key}")
        if post["timezone"] != "Europe/Paris":
            errors.append(f"mauvais fuseau : {key}")
        if post["disable_notification"]:
            errors.append(f"notification silencieuse interdite : {key}")
        if post["publish_all_channels"]:
            errors.append(f"diffusion multi-canaux interdite : {key}")
        if post["post_type"] == "message":
            limit = 1024 if post["image_url"] else 4096
            if not post["message"] or len(post["message"]) > limit:
                errors.append(f"longueur de message invalide : {key}")
            if post["image_url"] and not post["image_url"].startswith("https://"):
                errors.append(f"image non HTTPS : {key}")
            if bool(post["button_text"]) != bool(post["button_url"]):
                errors.append(f"bouton incomplet : {key}")
        else:
            options = json.loads(post["poll_options"])
            if post["image_url"]:
                errors.append(f"média interdit sur sondage/quiz : {key}")
            if not 2 <= len(options) <= 12:
                errors.append(f"nombre de réponses invalide : {key}")
            if post["post_type"] == "quiz" and not json.loads(post["poll_correct_option_ids"]):
                errors.append(f"quiz sans bonne réponse : {key}")

    for day in range(7):
        target = start.date().fromordinal(start.date().toordinal() + day)
        if not any(post["scheduled_for"].date() == target and post["publish_time"] == "21:00" for post in posts):
            errors.append(f"CTA de 21h manquant le {target.isoformat()}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="programme réellement la semaine")
    args = parser.parse_args()

    plan = load_plan()
    posts = [materialize_post(raw, plan) for raw in plan["posts"]]
    errors = validate(posts, plan)
    if errors:
        raise SystemExit("Contrôle refusé :\n- " + "\n- ".join(errors))

    messages = sum(post["post_type"] == "message" for post in posts)
    polls = sum(post["post_type"] == "poll" for post in posts)
    quizzes = sum(post["post_type"] == "quiz" for post in posts)
    images = sum(bool(post["image_url"]) for post in posts)
    print(f"Contrôle OK : {len(posts)} posts, {messages} messages, {polls} sondages, {quizzes} quiz, {images} visuels")
    if not args.apply:
        print("Mode contrôle uniquement : aucune donnée modifiée")
        return

    from app import get_conn

    conn = get_conn()
    try:
        channel_rows = conn.run(
            """SELECT id FROM telegram_channels
               WHERE LOWER(chat_id)=LOWER(:chat_id) AND active=TRUE AND deleted=FALSE""",
            chat_id=plan["channel"],
        )
        if len(channel_rows) != 1:
            raise RuntimeError("Le canal Bectanse Académie est introuvable ou ambigu")
        channel_id = int(channel_rows[0][0])

        conn.run(
            """UPDATE telegram_scheduled_posts
               SET enabled=FALSE, updated_at=NOW()
               WHERE id = ANY(:legacy_ids)
                 AND enabled=TRUE AND deleted=FALSE""",
            legacy_ids=list(LEGACY_ACTIVE_IDS),
        )

        synced_ids: list[int] = []
        for post in posts:
            rows = conn.run(
                """INSERT INTO telegram_scheduled_posts
                   (name, message, image_url, post_type, poll_question, poll_options,
                    poll_correct_option_ids, poll_explanation, poll_anonymous,
                    poll_multiple, schedule_type, weekdays, rotation_week,
                    publish_time, scheduled_for, timezone, channel, button_text,
                    button_url, disable_notification, enabled, source_key, deleted,
                    publish_all_channels, updated_at)
                   VALUES (:name, :message, :image_url, :post_type, :poll_question,
                           :poll_options, :poll_correct_option_ids, :poll_explanation,
                           :poll_anonymous, :poll_multiple, :schedule_type, :weekdays,
                           :rotation_week, :publish_time, :scheduled_for, :timezone,
                           :channel, :button_text, :button_url, :disable_notification,
                           :enabled, :source_key, :deleted, :publish_all_channels, NOW())
                   ON CONFLICT (source_key) DO UPDATE SET
                       name=EXCLUDED.name,
                       message=EXCLUDED.message,
                       image_url=EXCLUDED.image_url,
                       post_type=EXCLUDED.post_type,
                       poll_question=EXCLUDED.poll_question,
                       poll_options=EXCLUDED.poll_options,
                       poll_correct_option_ids=EXCLUDED.poll_correct_option_ids,
                       poll_explanation=EXCLUDED.poll_explanation,
                       poll_anonymous=EXCLUDED.poll_anonymous,
                       poll_multiple=EXCLUDED.poll_multiple,
                       schedule_type=EXCLUDED.schedule_type,
                       weekdays=EXCLUDED.weekdays,
                       rotation_week=EXCLUDED.rotation_week,
                       publish_time=EXCLUDED.publish_time,
                       scheduled_for=EXCLUDED.scheduled_for,
                       timezone=EXCLUDED.timezone,
                       channel=EXCLUDED.channel,
                       button_text=EXCLUDED.button_text,
                       button_url=EXCLUDED.button_url,
                       disable_notification=EXCLUDED.disable_notification,
                       enabled=TRUE,
                       deleted=FALSE,
                       publish_all_channels=FALSE,
                       updated_at=NOW()
                   RETURNING id""",
                **post,
            )
            post_id = int(rows[0][0])
            synced_ids.append(post_id)
            conn.run("DELETE FROM telegram_post_channels WHERE post_id=:post_id", post_id=post_id)
            conn.run(
                """INSERT INTO telegram_post_channels (post_id, channel_id)
                   VALUES (:post_id, :channel_id)""",
                post_id=post_id,
                channel_id=channel_id,
            )

        verified = conn.run(
            """SELECT COUNT(*),
                      COUNT(*) FILTER (WHERE publish_time='21:00'),
                      COUNT(*) FILTER (WHERE publish_all_channels=FALSE),
                      COUNT(*) FILTER (WHERE disable_notification=FALSE)
               FROM telegram_scheduled_posts
               WHERE id = ANY(:post_ids) AND enabled=TRUE AND deleted=FALSE""",
            post_ids=synced_ids,
        )[0]
        if tuple(int(value) for value in verified) != (len(posts), 7, len(posts), len(posts)):
            raise RuntimeError(f"Vérification finale inattendue : {verified}")
        print(f"Planning appliqué : {len(posts)} posts actifs, 7 CTA à 21h, canal #{channel_id} uniquement")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
