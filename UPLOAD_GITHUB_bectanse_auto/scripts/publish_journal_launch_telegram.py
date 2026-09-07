#!/usr/bin/env python3
"""Publie la campagne Journal Bectanse sur les canaux Telegram sélectionnés.

Le verrou ``scheduled_publications`` de l'application rend chaque publication
idempotente : relancer ce script avec la même référence ne crée pas de doublon.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")
SCRIPT_ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(SCRIPT_ROOT))

import app as bectanse_app  # noqa: E402


BASE_URL = "https://acces.bectanse-academie.com"
CAMPAIGN = "journal_launch_2026"
ASSET_BASE = BASE_URL + "/static/marketing/journal-launch/final"


POSTS = (
    {
        "key": "reveal",
        "image": ASSET_BASE + "/story-01-hook.png",
        "text": (
            "🟠 *NOUVEAU — TES TRADES PARLENT ENFIN*\n\n"
            "Ton broker conserve ton historique. Bectanse le transforme en "
            "plan de progression clair.\n\n"
            "Le nouveau *Journal Bectanse* analyse tes données MT5 pour t’aider "
            "à comprendre tes résultats, tes habitudes et ta discipline.\n\n"
            "Découvre ce que tes trades disent réellement de toi."
        ),
        "button_text": "DÉCOUVRIR LE JOURNAL",
        "button_url": (
            BASE_URL + "/vip?utm_source=telegram&utm_medium=organic"
            f"&utm_campaign={CAMPAIGN}&utm_content=reveal#journal"
        ),
    },
    {
        "key": "dashboard",
        "image": ASSET_BASE + "/story-02-produit.png",
        "text": (
            "📊 *CE N’EST PAS UN SIMPLE JOURNAL*\n\n"
            "Performance nette, taux de réussite, profit factor, calendrier, "
            "Trade Score et coach : tout est réuni dans un seul dashboard.\n\n"
            "Tu ne regardes plus seulement si tu as gagné ou perdu. Tu comprends "
            "*pourquoi* et tu sais quoi travailler ensuite.\n\n"
            "✅ Déjà disponible pour les membres Bectanse\n"
            "✅ Application accessible depuis mobile et ordinateur"
        ),
        "button_text": "EXPLORER L’APPLICATION",
        "button_url": (
            BASE_URL + "/vip?utm_source=telegram&utm_medium=organic"
            f"&utm_campaign={CAMPAIGN}&utm_content=dashboard#capture"
        ),
    },
    {
        "key": "action",
        "image": ASSET_BASE + "/story-03-cta.png",
        "text": (
            "🚀 *LE TRADE EST TERMINÉ. L’APPRENTISSAGE COMMENCE.*\n\n"
            "Chaque session peut maintenant devenir une leçon concrète.\n\n"
            "Crée gratuitement ton accès Explorer, entre dans l’application et "
            "découvre le Journal Bectanse avant de choisir ton accès.\n\n"
            "Aucune carte bancaire demandée."
        ),
        "button_text": "CRÉER MON ACCÈS GRATUIT",
        "button_url": (
            BASE_URL + "/vip?utm_source=telegram&utm_medium=organic"
            f"&utm_campaign={CAMPAIGN}&utm_content=action#capture"
        ),
    },
)


def _active_channels():
    conn = bectanse_app.get_conn()
    try:
        rows = conn.run(
            """SELECT id, name FROM telegram_channels
               WHERE active=TRUE AND deleted=FALSE ORDER BY id"""
        )
    finally:
        conn.close()
    return {int(channel_id): str(name) for channel_id, name in rows}


def publish(channel_ids: list[int], reference: str, post_keys: set[str] | None = None):
    channels = _active_channels()
    selected = [channel_id for channel_id in channel_ids if channel_id in channels]
    if not selected:
        raise SystemExit("Aucun canal actif ne correspond à la sélection")

    results = []
    for post in POSTS:
        if post_keys and post["key"] not in post_keys:
            continue
        delivery = bectanse_app._broadcast_scheduled_telegram(
            post["text"],
            base_slot_key=f"{reference}-{post['key']}",
            post_kind="journal-launch",
            post={"channel_ids": selected},
            image_url=post["image"],
            button_text=post["button_text"],
            button_url=post["button_url"],
            disable_notification=False,
        )
        results.append({"post": post["key"], **delivery})

    print({
        "channels": [{"id": channel_id, "name": channels[channel_id]} for channel_id in selected],
        "posts": results,
    })
    if any(result["failed"] for result in results):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channels", required=True, help="Identifiants séparés par des virgules")
    parser.add_argument("--reference", default="journal-launch-20260907")
    parser.add_argument("--posts", default="", help="Clés reveal,dashboard,action")
    args = parser.parse_args()
    channel_ids = [int(value.strip()) for value in args.channels.split(",") if value.strip()]
    post_keys = {value.strip() for value in args.posts.split(",") if value.strip()} or None
    publish(channel_ids, args.reference, post_keys)


if __name__ == "__main__":
    main()
