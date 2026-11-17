"""Autonomous Telegram Control Center scheduler.

The worker has one responsibility: publish due rows from
``telegram_scheduled_posts``. PostgreSQL claims make every channel delivery
idempotent, including restarts and overlapping worker instances.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import time
from datetime import datetime
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import pg8000.native
import requests


PARIS = ZoneInfo("Europe/Paris")
POLL_SECONDS = max(10, int(os.environ.get("TELEGRAM_SCHEDULER_POLL_SECONDS", "20")))
GRACE_MINUTES = max(10, int(os.environ.get("TELEGRAM_SCHEDULER_GRACE_MINUTES", "15")))
BOT_TOKEN = os.environ.get("ECO_BOT_TOKEN", "").strip() or os.environ.get("CANAL_BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

POST_COLUMNS = [
    "id", "name", "message", "image_url", "schedule_type", "weekdays",
    "rotation_week", "publish_time", "scheduled_for", "timezone", "channel",
    "button_text", "button_url", "disable_notification", "enabled", "source_key",
    "deleted", "last_sent_at", "post_type", "poll_question", "poll_options",
    "poll_correct_option_ids", "poll_explanation", "poll_anonymous",
    "poll_multiple", "publish_all_channels",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bectanse.telegram.scheduler")


def connection():
    parsed = urlparse(DATABASE_URL)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise RuntimeError("DATABASE_URL invalide")
    conn = pg8000.native.Connection(
        user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
        host=parsed.hostname, port=parsed.port or 5432,
        database=parsed.path.lstrip("/"), ssl_context=True, timeout=30,
    )
    conn.run("SET TIME ZONE 'UTC'")
    return conn


def paris_datetime(value):
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=PARIS) if parsed.tzinfo is None else parsed.astimezone(PARIS)


def publish_time(value):
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def matches_day(post, target_date):
    if post["schedule_type"] == "once":
        scheduled = paris_datetime(post["scheduled_for"])
        return bool(scheduled and scheduled.date() == target_date)
    try:
        weekdays = {int(day) for day in str(post["weekdays"] or "").split(",") if day != ""}
    except ValueError:
        return False
    if target_date.weekday() not in weekdays:
        return False
    if post["schedule_type"] == "rotation":
        return ((target_date.isocalendar().week - 1) % 4) == int(post["rotation_week"] or 0)
    return post["schedule_type"] == "weekly"


def due_at(post, now):
    if not post["enabled"] or post["deleted"]:
        return None
    if post["schedule_type"] == "once":
        if post["last_sent_at"]:
            return None
        scheduled = paris_datetime(post["scheduled_for"])
    else:
        clock = publish_time(post["publish_time"])
        if not clock or not matches_day(post, now.date()):
            return None
        scheduled = datetime.combine(now.date(), clock, tzinfo=PARIS)
    if not scheduled:
        return None
    delay = (now - scheduled).total_seconds()
    return scheduled if 0 <= delay < GRACE_MINUTES * 60 else None


def parse_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def targets(conn, post):
    if post["publish_all_channels"]:
        rows = conn.run(
            """SELECT id,name,chat_id FROM telegram_channels
               WHERE active=TRUE AND deleted=FALSE AND audience='public' ORDER BY id"""
        )
    else:
        rows = conn.run(
            """SELECT c.id,c.name,c.chat_id FROM telegram_post_channels t
               JOIN telegram_channels c ON c.id=t.channel_id
               WHERE t.post_id=:post_id AND c.active=TRUE AND c.deleted=FALSE
               ORDER BY c.id""",
            post_id=post["id"],
        )
    return [{"id": int(row[0]), "name": row[1], "chat_id": str(row[2])} for row in rows]


def claim(conn, slot, post, chat_id):
    rows = conn.run(
        """INSERT INTO scheduled_publications
           (slot_key,post_kind,status,content,attempts,post_id,target_channel)
           VALUES (:slot,'custom-editorial','sending',:content,1,:post_id,:target)
           ON CONFLICT (slot_key) DO UPDATE SET
             status='sending',content=:content,post_id=:post_id,target_channel=:target,
             attempts=scheduled_publications.attempts+1,error='',created_at=NOW()
           WHERE scheduled_publications.status='failed'
           RETURNING slot_key""",
        slot=slot, content=post["message"] or post["poll_question"],
        post_id=post["id"], target=chat_id,
    )
    if rows:
        return "claimed"
    status = conn.run("SELECT status FROM scheduled_publications WHERE slot_key=:slot", slot=slot)
    return str(status[0][0]) if status else "blocked"


def finish(conn, slot, status, message_id=None, error=""):
    conn.run(
        """UPDATE scheduled_publications SET status=:status,
           telegram_message_id=:message_id,error=:error,
           sent_at=CASE WHEN :status='sent' THEN NOW() ELSE sent_at END
           WHERE slot_key=:slot""",
        status=status, message_id=message_id, error=(error or "")[:1000], slot=slot,
    )


def keyboard(post):
    if post["button_text"] and post["button_url"]:
        return {"inline_keyboard": [[{"text": post["button_text"], "url": post["button_url"]}]]}
    return None


def send(post, chat_id):
    reply_markup = keyboard(post)
    if post["post_type"] in {"poll", "quiz"}:
        options = [{"text": str(option)} for option in parse_list(post["poll_options"])]
        payload = {
            "chat_id": chat_id,
            "question": post["poll_question"],
            "options": options,
            "type": "quiz" if post["post_type"] == "quiz" else "regular",
            "is_anonymous": bool(post["poll_anonymous"]),
            "allows_multiple_answers": bool(post["poll_multiple"]),
            "disable_notification": bool(post["disable_notification"]),
        }
        if post["post_type"] == "quiz":
            correct = parse_list(post["poll_correct_option_ids"])
            payload["correct_option_id"] = int(correct[0]) if correct else 0
            if post["poll_explanation"]:
                payload["explanation"] = post["poll_explanation"]
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll", json=payload, timeout=30,
        )
    elif post["image_url"]:
        media = requests.get(
            post["image_url"], timeout=30, allow_redirects=True,
            headers={"User-Agent": "Bectanse-Telegram-Publisher/2.0"},
        )
        media.raise_for_status()
        content_type = str(media.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"média non image: {content_type or 'inconnu'}")
        data = {
            "chat_id": chat_id,
            "caption": post["message"],
            "parse_mode": "Markdown",
            "disable_notification": str(bool(post["disable_notification"])).lower(),
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=data, files={"photo": ("bectanse.jpg", media.content, content_type)}, timeout=45,
        )
    else:
        payload = {
            "chat_id": chat_id, "text": post["message"], "parse_mode": "Markdown",
            "disable_web_page_preview": True,
            "disable_notification": bool(post["disable_notification"]),
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=30,
        )
    result = response.json()
    if not response.ok or not result.get("ok"):
        raise RuntimeError(result.get("description") or f"Telegram HTTP {response.status_code}")
    return int(result["result"]["message_id"])


def process_cycle():
    now = datetime.now(PARIS)
    conn = connection()
    try:
        rows = conn.run(
            """SELECT id,name,message,image_url,schedule_type,weekdays,rotation_week,
               publish_time,scheduled_for,timezone,channel,button_text,button_url,
               disable_notification,enabled,source_key,deleted,last_sent_at,post_type,
               poll_question,poll_options,poll_correct_option_ids,poll_explanation,
               poll_anonymous,poll_multiple,publish_all_channels
               FROM telegram_scheduled_posts
               WHERE enabled=TRUE AND deleted=FALSE ORDER BY id"""
        )
        sent_posts = 0
        for row in rows:
            post = dict(zip(POST_COLUMNS, row))
            scheduled = due_at(post, now)
            if not scheduled:
                continue
            destinations = targets(conn, post)
            if not destinations:
                log.error("post %s has no active Telegram destination", post["id"])
                continue
            complete = True
            sent_now = False
            for destination in destinations:
                digest = hashlib.sha256(destination["chat_id"].lower().encode()).hexdigest()[:12]
                slot = f"telegram-post-{post['id']}-{scheduled.strftime('%Y%m%d-%H%M')}-{digest}"
                state = claim(conn, slot, post, destination["chat_id"])
                if state == "sent":
                    continue
                if state != "claimed":
                    complete = False
                    continue
                error = "erreur Telegram inconnue"
                for attempt in range(3):
                    try:
                        if attempt:
                            time.sleep(2)
                        message_id = send(post, destination["chat_id"])
                        finish(conn, slot, "sent", message_id=message_id)
                        log.info("sent post=%s channel=%s message=%s", post["id"], destination["name"], message_id)
                        sent_now = True
                        break
                    except Exception as exc:
                        error = str(exc)
                        log.warning("attempt %s failed post=%s channel=%s: %s", attempt + 1, post["id"], destination["name"], error)
                else:
                    finish(conn, slot, "failed", error=error)
                    complete = False
            if complete:
                conn.run(
                    """UPDATE telegram_scheduled_posts SET last_sent_at=CASE WHEN :sent THEN NOW() ELSE last_sent_at END,
                       enabled=CASE WHEN schedule_type='once' THEN FALSE ELSE enabled END,updated_at=NOW()
                       WHERE id=:id""",
                    id=post["id"], sent=sent_now,
                )
                sent_posts += int(sent_now)
        return sent_posts
    finally:
        conn.close()


def main():
    if not BOT_TOKEN:
        raise SystemExit("ECO_BOT_TOKEN absent")
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL absent")
    stopping = [False]

    def stop(_signal, _frame):
        stopping[0] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("scheduler started cadence=%ss grace=%smin timezone=Europe/Paris", POLL_SECONDS, GRACE_MINUTES)
    while not stopping[0]:
        started = time.monotonic()
        try:
            process_cycle()
        except Exception:
            log.exception("scheduler cycle failed")
        time.sleep(max(1.0, POLL_SECONDS - (time.monotonic() - started)))
    log.info("scheduler stopped")


if __name__ == "__main__":
    main()
