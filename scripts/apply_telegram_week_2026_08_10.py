"""Synchronise la direction éditoriale V2 avec le planning Telegram en production.

Le script met à jour les publications existantes par leur nom interne. Il ne
crée, ne supprime et n'envoie aucun post. Sans ``--apply``, il effectue
uniquement un contrôle complet.
"""

import argparse
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "content" / "planning_telegram_2026-08-10_au_2026-08-16.csv"
DEFAULT_BASE_URL = "https://acces.bectanse-academie.com"
PAYLOAD_FIELDS = {
    "id", "name", "message", "image_url", "post_type", "poll_question",
    "poll_options", "poll_correct_option_ids", "poll_explanation",
    "poll_anonymous", "poll_multiple", "publish_all_channels", "channel_ids",
    "schedule_type", "weekdays", "rotation_week", "publish_time",
    "scheduled_for", "channel", "button_text", "button_url",
    "disable_notification", "enabled",
}


def api_json(url, *, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Réponse API invalide")
    return result


def load_editorial_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source, delimiter=";"))
    by_name = {row["nom"].strip(): row for row in rows}
    if len(by_name) != len(rows):
        raise ValueError("Le CSV contient des noms internes en double")
    return by_name


def updated_payload(post, row, key):
    payload = {field: post.get(field) for field in PAYLOAD_FIELDS if field in post}
    payload.update({
        "key": key,
        "message": row["message"].strip() if post.get("post_type") == "message" else post.get("message", ""),
        "image_url": row["image_url"].strip() if post.get("post_type") == "message" else "",
        "button_text": row["texte_bouton"].strip(),
        "button_url": row["lien_bouton"].strip(),
        "timezone": "Europe/Paris",
    })
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Applique réellement les mises à jour")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--key", default=os.environ.get("BECTANSE_ADMIN_KEY", ""))
    args = parser.parse_args()
    if not args.key:
        parser.error("La clé admin est requise via --key ou BECTANSE_ADMIN_KEY")

    editorial = load_editorial_rows(args.csv)
    query = urlencode({"key": args.key})
    result = api_json(f"{args.base_url.rstrip('/')}/admin/api/telegram/posts?{query}")
    live_posts = result.get("posts", [])
    live_by_name = {post.get("name", ""): post for post in live_posts}
    missing = sorted(set(editorial) - set(live_by_name))
    if missing:
        raise RuntimeError(f"{len(missing)} publication(s) introuvable(s) : {', '.join(missing)}")

    payloads = [updated_payload(live_by_name[name], row, args.key) for name, row in editorial.items()]
    message_posts = [payload for payload in payloads if payload.get("post_type") == "message"]
    native_posts = [payload for payload in payloads if payload.get("post_type") != "message"]
    images = [payload for payload in message_posts if payload.get("image_url")]
    assert all(payload.get("button_text") and payload.get("button_url") for payload in payloads)
    assert all(not payload.get("image_url") for payload in native_posts)
    assert all(len(payload.get("message", "")) <= (1024 if payload.get("image_url") else 4096) for payload in message_posts)
    assert all(payload.get("timezone") == "Europe/Paris" for payload in payloads)

    print(
        f"Contrôle OK : {len(payloads)} publications, {len(images)} visuels, "
        f"{len(native_posts)} quiz/sondages natifs, {len(payloads)} CTA."
    )
    if not args.apply:
        print("Mode contrôle uniquement : aucune donnée n'a été modifiée.")
        return

    save_url = f"{args.base_url.rstrip('/')}/admin/api/telegram/posts/save"
    for index, payload in enumerate(payloads, start=1):
        api_json(save_url, payload=payload)
        print(f"[{index:02d}/{len(payloads)}] {payload['name']}")
    print("Direction éditoriale V2 appliquée au planning de production.")


if __name__ == "__main__":
    main()
