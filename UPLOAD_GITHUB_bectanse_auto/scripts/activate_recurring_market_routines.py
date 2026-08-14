"""Active les routines de marché permanentes du lundi au vendredi.

Les rendez-vous Londres, pré-session US et alertes deviennent hebdomadaires.
Les annonces macro restent datées et sont complétées par le calendrier dynamique
afin de ne jamais publier une fausse annonce économique majeure.
"""

import argparse
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://acces.bectanse-academie.com"
SITE = "https://acces.bectanse-academie.com/"
VISUAL_BASE = f"{SITE.rstrip('/')}/static/telegram-visuals"
LONDON_IMAGE = f"{VISUAL_BASE}/01-session-londres-ouverte-v2.webp"
US_IMAGE = f"{VISUAL_BASE}/02-session-americaine-t30-v2.webp"
ALERT_IMAGE = f"{VISUAL_BASE}/07-derniere-alerte-disponible-v2.webp"


ROUTINES = {
    "Ouverture Londres — cap sur le plan": {
        "weekday": 0, "time": "09:00", "image_url": LONDON_IMAGE,
        "button_text": "VOIR LE PLAN LONDRES",
    },
    "Londres — mardi pédagogique": {
        "weekday": 1, "time": "09:00", "image_url": LONDON_IMAGE,
        "button_text": "VOIR LE PLAN LONDRES",
    },
    "Londres — mercredi de patience": {
        "weekday": 2, "time": "09:00", "image_url": LONDON_IMAGE,
        "button_text": "VOIR LE PLAN LONDRES",
        "message": """🇬🇧 *LONDRES EST OUVERTE*

Ce matin, ton avantage ne viendra peut-être pas d’une entrée rapide. Il viendra de ta capacité à attendre une configuration que tu peux expliquer et invalider clairement.

Ne dépense pas ton capital mental sur un mouvement moyen.

Observe. Laisse le prix parler. Puis décide.""",
    },
    "Londres — jeudi expertise": {
        "weekday": 3, "time": "09:00", "image_url": LONDON_IMAGE,
        "button_text": "VOIR LE PLAN LONDRES",
    },
    "Londres — vendredi de maîtrise": {
        "weekday": 4, "time": "09:00", "image_url": LONDON_IMAGE,
        "button_text": "VOIR LE PLAN LONDRES",
    },
    "Session US Bectanse — lundi": {
        "weekday": 0, "time": "15:00", "image_url": US_IMAGE,
        "button_text": "PRÉPARER LA SESSION US",
        "message": """🇺🇸 *SESSION US DANS 30 MINUTES*

Avant l’ouverture, je veux trois choses sur ton plan : le niveau qui t’intéresse, l’invalidation et le risque maximal.

Si l’une manque, tu n’es pas encore prêt à cliquer.

On se retrouve à l’ouverture, concentrés et sans précipitation. 🎯""",
    },
    "Session US Bectanse — mardi": {
        "weekday": 1, "time": "15:00", "image_url": US_IMAGE,
        "button_text": "PRÉPARER LA SESSION US",
        "message": """🇺🇸 *SESSION US DANS 30 MINUTES*

Plus de volume ne veut pas dire meilleure opportunité. Garde la même exigence qu’à Londres : contexte, confirmation, invalidation.

Le marché peut accélérer. Ton processus, lui, ne change pas.

Prépare-toi avec l’équipe. 🔥""",
    },
    "Session US Bectanse — mercredi": {
        "weekday": 2, "time": "15:00", "image_url": US_IMAGE,
        "button_text": "PRÉPARER LA SESSION US",
        "legacy_name": "Après l’IPC — revenir au plan",
        "message": """🇺🇸 *SESSION US DANS 30 MINUTES*

À mi-semaine, la fatigue peut faire baisser tes critères. C’est précisément maintenant que ton plan doit devenir plus strict.

Ne poursuis pas un prix déjà parti. Attends une zone, une confirmation et un risque cohérent.

On prépare la session ensemble. 🎯""",
    },
    "Session US Bectanse — jeudi": {
        "weekday": 3, "time": "15:00", "image_url": US_IMAGE,
        "button_text": "PRÉPARER LA SESSION US",
        "message": """🇺🇸 *SESSION US DANS 30 MINUTES*

Aujourd’hui, regarde moins la vitesse et davantage la qualité : le prix accepte-t-il réellement le niveau ou ne fait-il que le traverser ?

Une bonne lecture te donne aussi une raison claire de ne pas entrer.

Le desk se prépare. À toi d’arriver avec ton plan.""",
    },
    "Session US Bectanse — vendredi": {
        "weekday": 4, "time": "15:00", "image_url": US_IMAGE,
        "button_text": "PRÉPARER LA SESSION US",
        "message": """🇺🇸 *SESSION US DANS 30 MINUTES*

Dernière ouverture américaine de la semaine. Ne transforme pas ce rendez-vous en obligation de résultat.

Une configuration propre mérite ton attention. Une configuration moyenne mérite d’être laissée tranquille.

Finissons avec maîtrise. 🔥""",
    },
    "Vérification des alertes — lundi": {
        "weekday": 0, "time": "16:00", "image_url": ALERT_IMAGE,
        "button_text": "OUVRIR LES ALERTES",
    },
    "Alertes et taille de position": {
        "weekday": 1, "time": "16:00", "image_url": ALERT_IMAGE,
        "button_text": "OUVRIR LES ALERTES",
    },
    "Alertes après volatilité": {
        "weekday": 2, "time": "16:00", "image_url": ALERT_IMAGE,
        "button_text": "OUVRIR LES ALERTES",
        "message": """🔔 *LE MARCHÉ A BOUGÉ. LE PLAN A-T-IL CONFIRMÉ ?*

Un mouvement visible n’est pas encore une opportunité. Il faut un contexte, une invalidation et un risque cohérent.

Consulte l’espace pour vérifier si une alerte exploitable a réellement été publiée.

Pas d’alerte propre ? Pas de trade forcé.""",
    },
    "Vérification des opportunités — jeudi": {
        "weekday": 3, "time": "16:00", "image_url": ALERT_IMAGE,
        "button_text": "OUVRIR LES ALERTES",
    },
    "Dernier point alertes": {
        "weekday": 4, "time": "16:00", "image_url": ALERT_IMAGE,
        "button_text": "OUVRIR LES ALERTES",
    },
}

PAYLOAD_FIELDS = {
    "id", "name", "message", "image_url", "post_type", "poll_question",
    "poll_options", "poll_correct_option_ids", "poll_explanation",
    "poll_anonymous", "poll_multiple", "publish_all_channels", "channel_ids",
    "schedule_type", "weekdays", "rotation_week", "publish_time",
    "scheduled_for", "channel", "button_text", "button_url",
    "disable_notification", "enabled",
}


def api_json(url, *, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Réponse API invalide")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--key", default=os.environ.get("BECTANSE_ADMIN_KEY", ""))
    args = parser.parse_args()
    if not args.key:
        parser.error("La clé admin est requise via --key ou BECTANSE_ADMIN_KEY")

    query = urlencode({"key": args.key})
    data = api_json(f"{args.base_url.rstrip('/')}/admin/api/telegram/posts?{query}")
    posts = {post["name"]: post for post in data.get("posts", [])}
    missing = sorted(
        name for name, routine in ROUTINES.items()
        if name not in posts and routine.get("legacy_name") not in posts
    )
    if missing:
        raise RuntimeError(f"Routines introuvables : {', '.join(missing)}")

    payloads = []
    for name, routine in ROUTINES.items():
        post = posts.get(name) or posts[routine.get("legacy_name")]
        payload = {field: post.get(field) for field in PAYLOAD_FIELDS if field in post}
        payload.update({
            "key": args.key,
            "name": name,
            "message": routine.get("message", post["message"]),
            "image_url": routine["image_url"],
            "schedule_type": "weekly",
            "weekdays": [routine["weekday"]],
            "rotation_week": None,
            "publish_time": routine["time"],
            "scheduled_for": "",
            "timezone": "Europe/Paris",
            "button_text": routine["button_text"],
            "button_url": SITE,
            "enabled": True,
        })
        payloads.append(payload)

    print(f"Contrôle OK : {len(payloads)} routines hebdomadaires prêtes.")
    if not args.apply:
        print("Mode contrôle uniquement : aucune donnée n'a été modifiée.")
        return
    save_url = f"{args.base_url.rstrip('/')}/admin/api/telegram/posts/save"
    for payload in payloads:
        api_json(save_url, payload=payload)
        print(f"✓ {payload['name']} · {payload['publish_time']} · jour {payload['weekdays'][0]}")
    print("Routines de marché permanentes activées.")


if __name__ == "__main__":
    main()
