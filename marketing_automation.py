"""Automatisation marketing Bectanse Académie.

Le moteur reste volontairement piloté par la base de l'application : Brevo assure
la délivrabilité, tandis que les segments, priorités, arrêts et preuves d'envoi
restent auditables dans l'administration Bectanse.
"""

import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import jsonify, redirect, render_template, request, url_for


PARIS_TZ = ZoneInfo("Europe/Paris")
BASE_URL = "https://acces.bectanse-academie.com"
SUPPORT_URL = "https://t.me/m/PAt88QgeZDhk"


EXPLORER_STAGES = [
    {
        "stage": "jour-1", "delay_hours": 20,
        "subject": "{prenom}, voici quoi regarder en premier",
        "eyebrow": "TON PARCOURS EXPLORER",
        "title": "Ne visite pas l’espace au hasard",
        "body": [
            "Ton accès Explorer est là pour te permettre de comprendre le système avant de prendre une décision",
            "Commence par observer l’accueil, le Canal VIP, le Trader Lab et l’Analyse IA. Les espaces verrouillés te montrent exactement ce qui devient disponible avec un abonnement",
        ],
        "highlight": "Tu ne découvres pas une simple formation mais un environnement conçu pour apprendre, analyser et agir au même endroit",
        "cta": "Découvrir l’application gratuitement",
    },
    {
        "stage": "jour-3", "delay_hours": 68,
        "subject": "Bectanse ne se limite pas à des signaux",
        "eyebrow": "UN ÉCOSYSTÈME COMPLET",
        "title": "Tout a été relié pour te faire progresser",
        "body": [
            "Une information seule ne change rien si tu ne sais pas quoi en faire",
            "Bectanse réunit la formation, les outils de gestion du risque, le suivi, l’Analyse IA, le Canal VIP et Bectanse Auto dans une seule expérience",
        ],
        "highlight": "L’objectif est de remplacer l’improvisation par un processus clair que tu peux répéter",
        "cta": "Voir tout ce qui est inclus",
    },
    {
        "stage": "jour-5", "delay_hours": 116,
        "subject": "Imagine ton quotidien avec le bon système",
        "eyebrow": "AVANT DE CHOISIR",
        "title": "Regarde comment chaque outil s’intègre à ton quotidien",
        "body": [
            "L’application ne sert pas seulement à consulter du contenu. Elle centralise ton parcours, tes outils et les informations utiles au même endroit",
            "Tu peux déjà parcourir l’interface en mode Explorer puis voir précisément ce que l’abonnement débloque",
        ],
        "highlight": "Moins de dispersion, plus de structure et une expérience pensée pour mobile",
        "cta": "Revoir l’expérience Bectanse",
    },
    {
        "stage": "jour-7", "delay_hours": 164,
        "subject": "Ce que ton abonnement débloque vraiment",
        "eyebrow": "LA VALEUR DU SYSTÈME",
        "title": "Tu n’achètes pas une vidéo de plus",
        "body": [
            "L’abonnement donne accès à une formation complète de plus de 500 pages, au Canal VIP, à Bectanse Auto, aux outils de calcul et de suivi ainsi qu’à l’accompagnement membre",
            "Tu retrouves aussi le Trader Lab, le journal intelligent, les exercices de psychologie et l’Analyse IA dans le même espace",
        ],
        "highlight": "Chaque élément a été pensé pour fonctionner avec les autres au lieu de rester isolé",
        "cta": "Découvrir les formules",
    },
    {
        "stage": "jour-10", "delay_hours": 236,
        "subject": "Regarde les preuves avant de décider",
        "eyebrow": "ILS L’ONT VÉCU",
        "title": "Les témoignages racontent mieux le système que nous",
        "body": [
            "La présentation rassemble des retours audio, des captures de membres et les résultats documentés déjà partagés par l’Académie",
            "Prends quelques minutes pour les regarder dans leur format original puis fais-toi ton propre avis",
        ],
        "highlight": "Aucune promesse automatique, seulement un environnement réel, des outils concrets et des preuves consultables",
        "cta": "Voir les témoignages",
    },
    {
        "stage": "jour-14", "delay_hours": 332,
        "subject": "Quel accès correspond à ton objectif",
        "eyebrow": "CHOISIR SANS SE TROMPER",
        "title": "Un mois, trois mois ou un an",
        "body": [
            "La formule un mois permet de découvrir l’écosystème complet. Trois mois donnent le temps de construire une vraie routine et l’accès annuel accompagne une transformation plus profonde",
            "Toutes les formules ouvrent le même environnement membre. La différence se joue sur la durée de ton accompagnement",
        ],
        "highlight": "Si tu hésites, le support peut t’aider à choisir selon ta situation sans te pousser vers une formule inutile",
        "cta": "Comparer les accès",
    },
    {
        "stage": "jour-21", "delay_hours": 500,
        "subject": "Ton espace Explorer reste ouvert",
        "eyebrow": "À TON RYTHME",
        "title": "La prochaine étape dépend de toi",
        "body": [
            "Tu as eu le temps de parcourir l’application et de comprendre ce qui est disponible derrière les sections verrouillées",
            "Si tu veux maintenant passer de l’observation à l’utilisation complète, retrouve les offres et choisis le rythme qui te convient",
        ],
        "highlight": "Ton compte BCT est déjà créé. Après un paiement confirmé, l’accès se débloque automatiquement sur ce même compte",
        "cta": "Passer à l’accès complet",
    },
]


EXPLORER_WEEKLY_CONTENT = [
    {
        "stage": "hebdo-systeme", "subject": "Ce que Bectanse remplace dans ton quotidien",
        "eyebrow": "UN SEUL ÉCOSYSTÈME", "title": "Arrête de disperser tes outils",
        "body": ["L’application rassemble la formation, le risque, les analyses, le suivi et le Canal VIP", "Ton compte Explorer te permet de revoir la structure autant que nécessaire avant de débloquer les fonctions membres"],
        "highlight": "Un environnement cohérent est plus utile qu’une accumulation de contenus isolés", "cta": "Revoir l’écosystème complet",
    },
    {
        "stage": "hebdo-preuves", "subject": "Les preuves sont toujours visibles",
        "eyebrow": "TÉMOIGNAGES DOCUMENTÉS", "title": "Regarde les retours dans leur format original",
        "body": ["Les témoignages audio, les captures membres et la vidéo documentée sont consultables sur la présentation", "Prends le temps de vérifier ce qui est montré avant de décider si l’Académie correspond à ton objectif"],
        "highlight": "Les performances passées et témoignages ne garantissent aucun résultat futur. Le trading comporte un risque de perte", "cta": "Consulter les témoignages",
    },
    {
        "stage": "hebdo-outils", "subject": "As-tu exploré les nouveaux outils Bectanse",
        "eyebrow": "TRADER LAB", "title": "Analyse, journal, simulateur et psychologie",
        "body": ["Le Trader Lab a été conçu pour transformer une idée en processus observable", "L’Analyse IA, le journal intelligent, le simulateur et les exercices psychologiques complètent la formation et l’accompagnement"],
        "highlight": "Les outils sont visibles en Explorer puis utilisables avec l’accès membre", "cta": "Découvrir le Trader Lab",
    },
    {
        "stage": "hebdo-decision", "subject": "Ton compte est prêt si tu veux passer à l’action",
        "eyebrow": "PROCHAINE ÉTAPE", "title": "Tu n’as rien à recréer",
        "body": ["Ton code BCT et ton adresse confirmée sont déjà reliés", "Lorsque Stripe confirme ton abonnement, le même compte passe automatiquement du mode Explorer à l’accès membre"],
        "highlight": "Choisis uniquement la durée qui correspond à ton rythme. Le support peut t’aider si nécessaire", "cta": "Comparer les abonnements",
    },
]


PENDING_OPTIN_STAGES = [
    {
        "stage": "confirmation-2h", "delay_hours": 2,
        "subject": "Il reste une étape pour ouvrir ton accès Explorer",
        "eyebrow": "CONFIRMATION EN ATTENTE", "title": "Ton espace n’est pas encore activé",
        "body": ["Tu as demandé un accès Explorer mais ton adresse e-mail n’a pas encore été confirmée", "Ouvre le premier message reçu et clique sur le lien. Si tu ne le retrouves pas, retourne sur le site pour demander un nouveau lien"],
        "highlight": "Aucun compte marketing n’est ajouté à Brevo tant que l’adresse n’est pas confirmée", "cta": "Renvoyer mon lien de confirmation", "target_url": BASE_URL + "/?explorer=confirmation",
    },
    {
        "stage": "confirmation-20h", "delay_hours": 20,
        "subject": "Ton lien Explorer va bientôt expirer",
        "eyebrow": "DERNIER RAPPEL DE CONFIRMATION", "title": "Active ton accès avant l’expiration du lien",
        "body": ["Le lien de confirmation reste valable pendant 24 heures", "Après cette échéance, tu pourras toujours demander un nouvel accès depuis le site mais cette courte série de rappels s’arrêtera"],
        "highlight": "La confirmation protège la qualité de la liste et empêche l’utilisation de fausses adresses", "cta": "Confirmer mon accès Explorer", "target_url": BASE_URL + "/?explorer=confirmation",
    },
]


CHECKOUT_STAGES = [
    {
        "stage": "rappel-1h", "delay_hours": 1,
        "subject": "Tu as ouvert le paiement sans aller au bout",
        "eyebrow": "TON ACCÈS EST À PORTÉE DE MAIN",
        "title": "Une question t’a peut-être arrêté",
        "body": [
            "Tu as consulté une page de paiement Bectanse mais aucun abonnement confirmé n’est encore rattaché à ton compte",
            "Si tu as simplement été interrompu, tu peux reprendre tranquillement. Si quelque chose n’était pas clair, le support est disponible",
        ],
        "highlight": "Ton compte actuel sera activé automatiquement dès que Stripe confirmera le paiement",
        "cta": "Reprendre mon inscription",
    },
    {
        "stage": "rappel-24h", "delay_hours": 24,
        "subject": "Avant de choisir, vérifie ces trois points",
        "eyebrow": "PRENDRE UNE DÉCISION CLAIRE",
        "title": "Le système doit correspondre à ce que tu recherches",
        "body": [
            "Bectanse réunit la formation, les outils, l’application, le Canal VIP et l’accompagnement dans un seul abonnement",
            "Tu conserves ton code BCT, tu accèdes depuis mobile et ton compte est relié automatiquement au paiement Stripe confirmé",
        ],
        "highlight": "Consulte la présentation complète et les témoignages avant de reprendre. Tu dois savoir exactement ce que tu rejoins",
        "cta": "Revoir la présentation complète",
    },
    {
        "stage": "rappel-72h", "delay_hours": 72,
        "subject": "On clôture ce rappel mais ton accès reste disponible",
        "eyebrow": "DERNIER RAPPEL DE PAIEMENT",
        "title": "Ne reste pas bloqué par une simple question",
        "body": [
            "Nous arrêtons ici cette courte série de rappels liée à ta visite sur Stripe",
            "La présentation et ton compte Explorer restent accessibles. Tu peux revenir quand tu le souhaites ou parler directement au support si tu veux une réponse humaine",
        ],
        "highlight": "Aucune relance de panier supplémentaire ne sera envoyée pour cette tentative",
        "cta": "Voir les accès disponibles",
    },
]


LEGACY_LEAD_STAGES = [
    {
        "stage": "reveil-1", "delay_hours": 1,
        "subject": "Bectanse a complètement changé depuis notre premier échange",
        "eyebrow": "UNE NOUVELLE PHASE",
        "title": "Ce que tu avais découvert n’existe plus sous cette forme",
        "body": [
            "Tu nous avais contactés pour découvrir Bectanse. Depuis, l’Académie a évolué à un niveau totalement différent",
            "Nous avons construit une véritable application membre avec Bectanse Auto, le Canal VIP, une formation de plus de 500 pages, l’Analyse IA, le Trader Lab, un journal intelligent, un simulateur et un espace psychologie",
        ],
        "highlight": "Tu peux maintenant créer gratuitement un compte Explorer et observer l’application avant de choisir un abonnement",
        "cta": "Découvrir gratuitement la nouvelle Académie",
    },
    {
        "stage": "reveil-2", "delay_hours": 72,
        "subject": "Voici tout ce que tu n’avais pas pu découvrir",
        "eyebrow": "BIEN PLUS QU’UNE FORMATION",
        "title": "Un système complet a été construit entre-temps",
        "body": [
            "L’objectif n’est plus de te donner du contenu isolé. Chaque outil communique avec le reste de ton parcours",
            "Tu peux apprendre, suivre le marché, travailler ton risque, consulter le Canal VIP, analyser une capture et retrouver tes progrès au même endroit depuis ton téléphone",
        ],
        "highlight": "Regarde la présentation, les témoignages audio, les captures membres et le fonctionnement de l’application puis fais-toi ton propre avis",
        "cta": "Voir tout ce qui a changé",
    },
    {
        "stage": "reveil-3", "delay_hours": 168,
        "subject": "Dernière invitation pour redécouvrir Bectanse",
        "eyebrow": "TON ACCÈS EXPLORER",
        "title": "Entre dans l’application sans payer",
        "body": [
            "C’est le dernier message de cette campagne de redécouverte",
            "Si le projet t’intéresse toujours, crée ton accès Explorer gratuit. Tu verras les sections, les outils et la structure réelle avant de décider si tu veux rejoindre les membres",
        ],
        "highlight": "Après cette invitation, nous arrêtons cette séquence. Tu pourras revenir librement quand tu le souhaites",
        "cta": "Créer mon accès Explorer gratuit",
    },
]


RENEWAL_STAGES = {
    "j-7": {
        "subject": "Ton accès arrive à échéance dans 7 jours", "eyebrow": "ANTICIPER SANS INTERRUPTION",
        "title": "Ton environnement Bectanse est toujours actif",
        "body": ["Ton accès arrive à échéance dans une semaine", "Si ton abonnement n’est pas renouvelé automatiquement par Stripe, tu peux préparer la suite dès maintenant et conserver la continuité de ton espace"],
        "highlight": "Tes réglages et ton historique restent associés à ton code BCT", "cta": "Gérer mon renouvellement",
    },
    "j-3": {
        "subject": "Plus que 3 jours avant la fin de ton accès", "eyebrow": "TON ACCÈS MEMBRE",
        "title": "Évite une coupure inutile",
        "body": ["Il reste trois jours avant l’échéance indiquée sur ton compte", "Renouvelle depuis la présentation officielle ou contacte le support si ta situation nécessite une vérification"],
        "highlight": "Après confirmation du paiement, le même compte est réactivé automatiquement", "cta": "Renouveler mon accès",
    },
    "j-1": {
        "subject": "Ton accès expire demain", "eyebrow": "DERNIÈRES 24 HEURES",
        "title": "Ton espace peut rester actif sans interruption",
        "body": ["Ton échéance est prévue demain", "Si tu souhaites continuer, tu peux choisir ta formule maintenant. Une question de paiement ou d’accès peut aussi être traitée par le support"],
        "highlight": "Le paiement confirmé par Stripe déclenche l’activation du compte", "cta": "Continuer avec Bectanse",
    },
    "j0": {
        "subject": "Ton accès arrive à échéance aujourd’hui", "eyebrow": "ÉCHÉANCE AUJOURD’HUI",
        "title": "Décide si tu souhaites poursuivre",
        "body": ["Ton accès membre arrive à sa date d’échéance", "Sans renouvellement confirmé, les fonctionnalités payantes seront verrouillées mais tes informations resteront conservées pour une future réactivation"],
        "highlight": "Tu ne dois pas recréer de compte pour reprendre", "cta": "Renouveler maintenant",
    },
}


REACTIVATION_STAGES = [
    {
        "stage": "expire-0", "delay_days": 0,
        "subject": "Ton espace est prêt à être réactivé", "eyebrow": "TON COMPTE EST CONSERVÉ",
        "title": "Tu peux reprendre là où tu t’étais arrêté",
        "body": ["Ton abonnement n’est plus actif mais ton compte BCT et tes informations sont toujours conservés", "Depuis ton départ, l’espace membre a évolué avec l’Analyse IA, le Trader Lab, le journal intelligent, le simulateur et une navigation mobile repensée"],
        "highlight": "Un paiement confirmé suffit pour réactiver les fonctionnalités membres", "cta": "Découvrir toutes les nouveautés",
    },
    {
        "stage": "expire-7", "delay_days": 7,
        "subject": "Tu n’as peut-être pas vu tout ce qui a changé", "eyebrow": "NOUVELLE EXPÉRIENCE BECTANSE",
        "title": "L’Académie ne ressemble plus à celle que tu as quittée",
        "body": ["L’application centralise désormais la progression, les outils de risque, l’analyse, le suivi et le Canal VIP", "Reconnecte-toi avec ton ancien code pour observer l’interface puis consulte la présentation si tu veux retrouver l’accès complet"],
        "highlight": "Ton compte reste identifiable même après l’expiration", "cta": "Redécouvrir l’Académie",
    },
    {
        "stage": "expire-14", "delay_days": 14,
        "subject": "Ton ancien compte peut reprendre vie", "eyebrow": "REVENIR SANS RECOMMENCER",
        "title": "Tout ton environnement est encore là",
        "body": ["Tu n’as pas besoin d’une nouvelle inscription ni d’un nouveau code", "Choisis une formule, utilise la même adresse e-mail et l’activation Stripe rattache automatiquement l’abonnement à ton compte"],
        "highlight": "Besoin d’aide avant de reprendre, le support peut vérifier ton compte", "cta": "Voir les formules de retour",
    },
    {
        "stage": "expire-21", "delay_days": 21,
        "subject": "Regarde les nouveautés avant de décider", "eyebrow": "PREUVES ET FONCTIONNALITÉS",
        "title": "Prends cinq minutes pour comparer",
        "body": ["La présentation officielle montre le fonctionnement de l’application, les outils, les témoignages audio et les captures membres", "Regarde les éléments documentés puis décide si le nouvel écosystème correspond mieux à ce que tu recherches aujourd’hui"],
        "highlight": "L’objectif n’est pas de te faire revenir à l’aveugle mais de te montrer ce qui existe réellement", "cta": "Voir la nouvelle présentation",
    },
    {
        "stage": "expire-30", "delay_days": 30,
        "subject": "Dernier message de cette série", "eyebrow": "TON COMPTE RESTE DISPONIBLE",
        "title": "Nous arrêtons les rappels de réactivation",
        "body": ["C’est le dernier e-mail de cette séquence", "Ton compte reste conservé et tu pourras revenir plus tard depuis la présentation officielle ou contacter le support si tu souhaites vérifier ton ancien accès"],
        "highlight": "Tu gardes la liberté de revenir quand le moment sera adapté", "cta": "Garder le lien de l’Académie",
    },
]


def _now():
    return datetime.now(PARIS_TZ).replace(tzinfo=None)


def ensure_marketing_schema(conn):
    """Schéma additive-only du moteur marketing."""
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_contacts (
        member_code TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        first_name TEXT NOT NULL DEFAULT '',
        segment TEXT NOT NULL DEFAULT 'explorer',
        consent_source TEXT NOT NULL DEFAULT '',
        consent_at TIMESTAMP,
        unsubscribed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_contacts_segment_idx
        ON marketing_contacts (segment, updated_at DESC)""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_contacts_email_idx
        ON marketing_contacts (LOWER(email))""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_checkout_intents (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        email TEXT NOT NULL,
        destination TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'direct',
        medium TEXT NOT NULL DEFAULT '',
        campaign TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        event_count INTEGER NOT NULL DEFAULT 1,
        started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        last_started_at TIMESTAMP NOT NULL DEFAULT NOW(),
        converted_at TIMESTAMP,
        conversion_reference TEXT NOT NULL DEFAULT ''
    )""")
    conn.run("""ALTER TABLE marketing_checkout_intents
        ADD COLUMN IF NOT EXISTS stripe_session_id TEXT NOT NULL DEFAULT ''""")
    conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS marketing_checkout_stripe_idx
        ON marketing_checkout_intents (stripe_session_id)
        WHERE stripe_session_id<>''""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_checkout_open_idx
        ON marketing_checkout_intents (member_code, status, started_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_email_log (
        id BIGSERIAL PRIMARY KEY,
        member_code TEXT NOT NULL,
        recipient_email TEXT NOT NULL,
        journey TEXT NOT NULL,
        stage TEXT NOT NULL,
        reference_key TEXT NOT NULL DEFAULT 'default',
        subject TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        provider_message_id TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        due_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        sent_at TIMESTAMP,
        UNIQUE (member_code, journey, stage, reference_key)
    )""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS provider_status TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_email_log
        ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_email_log_status_idx
        ON marketing_email_log (status, sent_at DESC, created_at DESC)""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        daily_send_limit INTEGER NOT NULL DEFAULT 180,
        weekly_contact_limit INTEGER NOT NULL DEFAULT 4,
        min_gap_hours INTEGER NOT NULL DEFAULT 20,
        batch_limit INTEGER NOT NULL DEFAULT 30,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_campaign_enabled BOOLEAN NOT NULL DEFAULT FALSE""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_daily_limit INTEGER NOT NULL DEFAULT 1000""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_started_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_settings
        ADD COLUMN IF NOT EXISTS legacy_paused_reason TEXT NOT NULL DEFAULT ''""")
    conn.run("""INSERT INTO marketing_settings (id) VALUES (1)
        ON CONFLICT (id) DO NOTHING""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_legacy_leads (
        id BIGSERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        first_name TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'anciens-prospects-2025',
        status TEXT NOT NULL DEFAULT 'active',
        imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
        converted_at TIMESTAMP,
        unsubscribed_at TIMESTAMP,
        last_contact_at TIMESTAMP
    )""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS phone TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS country TEXT NOT NULL DEFAULT ''""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS consent_basis TEXT NOT NULL DEFAULT 'demande-formulaire'""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMP""")
    conn.run("""ALTER TABLE marketing_legacy_leads
        ADD COLUMN IF NOT EXISTS bounced_at TIMESTAMP""")
    conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS marketing_legacy_leads_email_idx
        ON marketing_legacy_leads (LOWER(email))""")
    conn.run("""CREATE TABLE IF NOT EXISTS marketing_email_events (
        id BIGSERIAL PRIMARY KEY,
        event_hash TEXT UNIQUE NOT NULL,
        recipient_email TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        provider_message_id TEXT NOT NULL DEFAULT '',
        journey TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT '',
        event_at TIMESTAMP NOT NULL DEFAULT NOW(),
        payload TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""")
    conn.run("""CREATE INDEX IF NOT EXISTS marketing_email_events_type_idx
        ON marketing_email_events (event_type,event_at DESC)""")


def sync_marketing_segments(conn):
    """Recalcule les segments sans mélanger prospects, actifs et expirés."""
    ensure_marketing_schema(conn)
    conn.run("""INSERT INTO marketing_contacts
        (member_code,email,first_name,segment,consent_source,consent_at,created_at,updated_at)
        SELECT m.code,LOWER(TRIM(m.email)),SPLIT_PART(TRIM(m.nom),' ',1),
            CASE
                WHEN COALESCE(m.admin_suspended,FALSE) THEN 'suspended'
                WHEN COALESCE(m.billing_status,'') IN ('past_due','unpaid','canceled','paused')
                     AND COALESCE(m.stripe_subscription_id,'')<>'' THEN 'expired'
                WHEN COALESCE(m.access_level,'member') IN ('explorer','demo') THEN 'explorer'
                WHEN m.actif=FALSE OR (m.date_fin IS NOT NULL AND m.date_fin<=NOW()) THEN 'expired'
                WHEN m.date_fin IS NOT NULL AND m.date_fin<=NOW()+INTERVAL '7 days'
                     AND (COALESCE(m.stripe_subscription_id,'')=''
                          OR COALESCE(m.billing_cancel_at_period_end,FALSE)) THEN 'expiring'
                ELSE 'active'
            END,
            CASE WHEN COALESCE(m.access_level,'member') IN ('explorer','demo')
                 THEN 'double-opt-in-explorer' ELSE 'relation-membre' END,
            COALESCE(m.email_verified_at,m.created_at),m.created_at,NOW()
        FROM members m
        WHERE m.code<>'BCT-DEMO2026' AND m.email IS NOT NULL
          AND TRIM(m.email)<>'' AND m.email_verified_at IS NOT NULL
        ON CONFLICT (member_code) DO UPDATE SET
            email=EXCLUDED.email,first_name=EXCLUDED.first_name,
            segment=EXCLUDED.segment,consent_source=EXCLUDED.consent_source,
            consent_at=COALESCE(marketing_contacts.consent_at,EXCLUDED.consent_at),
            updated_at=NOW()""")
    conn.run("""UPDATE marketing_legacy_leads leads SET status='converted',converted_at=NOW()
        WHERE status='active' AND EXISTS (
            SELECT 1 FROM marketing_contacts contacts
            WHERE LOWER(contacts.email)=LOWER(leads.email)
        )""")


def upsert_marketing_contact_for_member(conn, member_code):
    ensure_marketing_schema(conn)
    sync_marketing_segments(conn)
    return conn.run("""SELECT member_code,segment FROM marketing_contacts
        WHERE member_code=:code""", code=member_code)


def record_checkout_start(conn, member_code, destination="", source="direct", medium="", campaign=""):
    """Associe un clic Stripe au compte connecté, sans stocker un visiteur anonyme."""
    if not member_code:
        return None
    ensure_marketing_schema(conn)
    rows = conn.run("""SELECT email FROM members WHERE code=:code
        AND email_verified_at IS NOT NULL AND TRIM(COALESCE(email,''))<>''""", code=member_code)
    if not rows:
        return None
    email = str(rows[0][0]).strip().lower()
    open_rows = conn.run("""SELECT id FROM marketing_checkout_intents
        WHERE member_code=:code AND status='open'
          AND last_started_at>NOW()-INTERVAL '7 days'
        ORDER BY started_at DESC LIMIT 1""", code=member_code)
    if open_rows:
        intent_id = int(open_rows[0][0])
        conn.run("""UPDATE marketing_checkout_intents SET
            destination=:destination,source=:source,medium=:medium,campaign=:campaign,
            last_started_at=NOW(),event_count=event_count+1 WHERE id=:id""",
            destination=str(destination or "")[:500], source=str(source or "direct")[:100],
            medium=str(medium or "")[:100], campaign=str(campaign or "")[:120], id=intent_id)
        return intent_id
    created = conn.run("""INSERT INTO marketing_checkout_intents
        (member_code,email,destination,source,medium,campaign)
        VALUES (:code,:email,:destination,:source,:medium,:campaign) RETURNING id""",
        code=member_code, email=email, destination=str(destination or "")[:500],
        source=str(source or "direct")[:100], medium=str(medium or "")[:100],
        campaign=str(campaign or "")[:120])
    return int(created[0][0]) if created else None


def record_checkout_session(conn, member_code, stripe_session_id, destination="",
                            source="direct", medium="", campaign=""):
    """Relie la session Stripe réellement créée à l'intention marketing ouverte."""
    intent_id = record_checkout_start(
        conn, member_code, destination=destination, source=source,
        medium=medium, campaign=campaign,
    )
    if intent_id and stripe_session_id:
        conn.run("""UPDATE marketing_checkout_intents SET stripe_session_id=:session
            WHERE id=:id""", session=str(stripe_session_id)[:160], id=intent_id)
    return intent_id


def mark_checkout_expired(conn, stripe_session_id):
    """Conserve l'intention ouverte : elle devient éligible à la séquence d'abandon."""
    ensure_marketing_schema(conn)
    if not stripe_session_id:
        return
    conn.run("""UPDATE marketing_checkout_intents SET last_started_at=NOW()
        WHERE stripe_session_id=:session AND status='open'""",
        session=str(stripe_session_id)[:160])


def mark_marketing_conversion(conn, member_code, email="", reference=""):
    """Stoppe toutes les relances de paiement dès la confirmation Stripe."""
    ensure_marketing_schema(conn)
    if member_code:
        conn.run("""UPDATE marketing_checkout_intents SET status='converted',
            converted_at=NOW(),conversion_reference=:reference
            WHERE member_code=:code AND status='open'""",
            reference=str(reference or "")[:160], code=member_code)
        conn.run("""UPDATE marketing_contacts SET segment='active',updated_at=NOW()
            WHERE member_code=:code""", code=member_code)
    elif email:
        conn.run("""UPDATE marketing_checkout_intents SET status='converted',
            converted_at=NOW(),conversion_reference=:reference
            WHERE LOWER(email)=LOWER(:email) AND status='open'""",
            reference=str(reference or "")[:160], email=email)


def _utm_url(journey, stage, anchor=""):
    query = urlencode({
        "utm_source": "brevo", "utm_medium": "email",
        "utm_campaign": "bectanse_" + journey.replace("_", "-"),
        "utm_content": stage,
    })
    suffix = ("#" + anchor.lstrip("#")) if anchor else ""
    return f"{BASE_URL}/vip?{query}{suffix}"


def _email_html(prenom, content, journey, stage, unsubscribe_url):
    safe_name = html.escape(prenom or "Bonjour")
    paragraphs = "".join(
        f"<p style='margin:0 0 15px;color:#c8c8c8;font-size:15px;line-height:1.72'>{html.escape(text)}</p>"
        for text in content["body"]
    )
    cta_url = content.get("target_url") or _utm_url(
        journey, stage, "offres" if journey not in {"explorer", "legacy_reactivation"} else "capture")
    return f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'></head>
<body style='margin:0;background:#080908;font-family:Arial,Helvetica,sans-serif;color:#fff'>
<div style='display:none;max-height:0;overflow:hidden;color:#080908'>{html.escape(content['highlight'])}</div>
<div style='max-width:620px;margin:0 auto;padding:28px 16px'>
  <div style='padding:18px 8px 26px;text-align:center'>
    <div style='font-size:22px;font-weight:900;letter-spacing:1px'>BECTANSE <span style='color:#ff641f'>ACADÉMIE</span></div>
  </div>
  <div style='background:#111310;border:1px solid #2b2e29;border-radius:24px;overflow:hidden'>
    <div style='height:4px;background:linear-gradient(90deg,#ff4d16,#ff9b20)'></div>
    <div style='padding:34px 28px'>
      <div style='color:#ff7a36;font-size:11px;letter-spacing:1.7px;font-weight:800;margin-bottom:12px'>{html.escape(content['eyebrow'])}</div>
      <p style='margin:0 0 8px;color:#8c9189;font-size:14px'>Bonjour {safe_name}</p>
      <h1 style='margin:0 0 22px;font-size:29px;line-height:1.12;color:#fff'>{html.escape(content['title'])}</h1>
      {paragraphs}
      <div style='margin:24px 0;padding:18px;border-radius:14px;background:#171a16;border-left:3px solid #ff641f;color:#f0f0ef;font-size:14px;line-height:1.65'>{html.escape(content['highlight'])}</div>
      <a href='{cta_url}' style='display:block;padding:16px 18px;border-radius:13px;background:#ff641f;color:#fff;text-decoration:none;text-align:center;font-weight:900;font-size:15px'>{html.escape(content['cta'])} →</a>
      <p style='margin:18px 0 0;text-align:center;color:#8d918a;font-size:12px'>Une question&nbsp;? <a href='{SUPPORT_URL}' style='color:#ff8a50;text-decoration:none'>Parler au support</a></p>
    </div>
  </div>
  <div style='padding:24px 16px;text-align:center;color:#686c66;font-size:11px;line-height:1.65'>
    Bectanse Académie · LERIS CORP FZCO<br>
    Tu reçois ce message à la suite de ton inscription ou de ta relation membre<br>
    <a href='{unsubscribe_url}' style='color:#8b8f88'>Gérer mes préférences ou arrêter ces e-mails</a>
  </div>
</div></body></html>"""


def _already_sent(conn, code, journey, stage, reference):
    return bool(conn.run("""SELECT 1 FROM marketing_email_log
        WHERE member_code=:code AND journey=:journey AND stage=:stage
          AND reference_key=:reference AND status='sent' LIMIT 1""",
        code=code, journey=journey, stage=stage, reference=reference))


def _contact_rate_allowed(conn, code, weekly_limit, min_gap_hours):
    sent_7d = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
        WHERE member_code=:code AND status='sent'
          AND sent_at>NOW()-INTERVAL '7 days'""", code=code)[0][0] or 0)
    if sent_7d >= weekly_limit:
        return False
    last_rows = conn.run("""SELECT MAX(sent_at) FROM (
        SELECT sent_at FROM marketing_email_log WHERE member_code=:code AND status='sent'
        UNION ALL
        SELECT sent_at FROM renewal_email_log WHERE member_code=:code AND status='sent'
    ) history""", code=code)
    last_sent = last_rows[0][0] if last_rows else None
    return not last_sent or (_now() - last_sent) >= timedelta(hours=min_gap_hours)


def _checkout_candidate(conn, contact):
    code = contact[0]
    rows = conn.run("""SELECT id,started_at FROM marketing_checkout_intents
        WHERE member_code=:code AND status='open'
        ORDER BY started_at DESC LIMIT 1""", code=code)
    if not rows:
        return None
    intent_id, started_at = int(rows[0][0]), rows[0][1]
    age_hours = (_now() - started_at).total_seconds() / 3600
    reference = str(intent_id)
    for content in CHECKOUT_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "checkout_abandon", content["stage"], reference):
            return "checkout_abandon", content, reference, started_at + timedelta(hours=content["delay_hours"])
    return None


def _renewal_candidate(conn, contact):
    code, _email, _first, segment, _created, date_fin = contact[:6]
    if not date_fin:
        return None
    reference = date_fin.date().isoformat() if hasattr(date_fin, "date") else str(date_fin)[:10]
    if segment == "expiring":
        days = (date_fin.date() - _now().date()).days
        for threshold, stage in ((0, "j0"), (1, "j-1"), (3, "j-3"), (7, "j-7")):
            if days <= threshold and not _already_sent(conn, code, "renewal", stage, reference):
                return "renewal", {"stage": stage, **RENEWAL_STAGES[stage]}, reference, _now()
        return None
    if segment != "expired":
        return None
    initial = REACTIVATION_STAGES[0]
    if not _already_sent(conn, code, "reactivation", initial["stage"], reference):
        return "reactivation", initial, reference, _now()
    rows = conn.run("""SELECT sent_at FROM marketing_email_log
        WHERE member_code=:code AND journey='reactivation' AND stage='expire-0'
          AND reference_key=:reference AND status='sent' LIMIT 1""", code=code, reference=reference)
    base = rows[0][0] if rows else _now()
    for content in REACTIVATION_STAGES[1:]:
        due = base + timedelta(days=content["delay_days"])
        if _now() >= due and not _already_sent(
                conn, code, "reactivation", content["stage"], reference):
            return "reactivation", content, reference, due
    return None


def _explorer_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "explorer":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    for content in EXPLORER_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "explorer", content["stage"], "onboarding"):
            return "explorer", content, "onboarding", created_at + timedelta(hours=content["delay_hours"])
    initial_sent = int(conn.run("""SELECT COUNT(DISTINCT stage) FROM marketing_email_log
        WHERE member_code=:code AND journey='explorer' AND reference_key='onboarding'
          AND status='sent'""", code=code)[0][0] or 0)
    if initial_sent >= len(EXPLORER_STAGES):
        week_number = max(4, int((_now() - created_at).days // 7))
        content = dict(EXPLORER_WEEKLY_CONTENT[(week_number - 4) % len(EXPLORER_WEEKLY_CONTENT)])
        reference = f"week-{week_number}"
        if not _already_sent(conn, code, "explorer_weekly", content["stage"], reference):
            return "explorer_weekly", content, reference, _now()
    return None


def _pending_optin_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "pending_optin":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    reference = created_at.strftime("%Y%m%d%H%M")
    for content in PENDING_OPTIN_STAGES:
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "email_confirmation", content["stage"], reference):
            return ("email_confirmation", content, reference,
                    created_at + timedelta(hours=content["delay_hours"]))
    return None


def _legacy_lead_candidate(conn, contact):
    code, _email, _first, segment, created_at = contact[:5]
    if segment != "legacy_lead":
        return None
    age_hours = (_now() - created_at).total_seconds() / 3600
    lead_id = int(code[5:])
    engagement = conn.run("""SELECT clicked_at FROM marketing_legacy_leads
        WHERE id=:id AND status='active' AND unsubscribed_at IS NULL""", id=lead_id)
    if not engagement:
        return None
    clicked_at = engagement[0][0]
    for content in LEGACY_LEAD_STAGES:
        # Le troisième message est réservé aux personnes qui ont réellement
        # cliqué. Les autres reçoivent les deux prises de contact annoncées,
        # puis la séquence s'arrête pour préserver la délivrabilité.
        if content["stage"] == "reveil-3" and not clicked_at:
            continue
        if age_hours >= content["delay_hours"] and not _already_sent(
                conn, code, "legacy_reactivation", content["stage"], "2025-relaunch"):
            return ("legacy_reactivation", content, "2025-relaunch",
                    created_at + timedelta(hours=content["delay_hours"]))
    return None


def _legacy_delivery_health(conn):
    sent = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
        WHERE journey='legacy_reactivation' AND status='sent'
          AND sent_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0)
    counts = {str(row[0]).lower(): int(row[1]) for row in conn.run("""
        SELECT event_type,COUNT(*) FROM marketing_email_events
        WHERE journey='legacy_reactivation' AND event_at>NOW()-INTERVAL '24 hours'
        GROUP BY event_type""")}
    hard = sum(counts.get(name, 0) for name in ("hardbounce", "invalid", "blocked"))
    spam = counts.get("spam", 0)
    unsubscribed = counts.get("unsubscribed", 0)
    health = {"sent": sent, "hard": hard, "spam": spam, "unsubscribed": unsubscribed}
    if sent < 50:
        return True, "", health
    hard_rate = hard * 100 / sent
    spam_rate = spam * 100 / sent
    unsubscribe_rate = unsubscribed * 100 / sent
    if hard_rate >= 1.5:
        return False, f"Pause automatique: rebonds définitifs {hard_rate:.2f}%", health
    if unsubscribe_rate >= 0.8:
        return False, f"Pause automatique: désabonnements {unsubscribe_rate:.2f}%", health
    if spam_rate >= 0.1:
        return False, f"Pause automatique: plaintes {spam_rate:.2f}%", health
    return True, "", health


def _claim_email(conn, contact, journey, content, reference, due_at):
    code, email = contact[0], contact[1]
    return conn.run("""INSERT INTO marketing_email_log
        (member_code,recipient_email,journey,stage,reference_key,subject,status,due_at)
        VALUES (:code,:email,:journey,:stage,:reference,:subject,'pending',:due_at)
        ON CONFLICT (member_code,journey,stage,reference_key) DO UPDATE SET
            status='pending',error='',created_at=NOW(),due_at=:due_at
        WHERE marketing_email_log.status='failed'
          AND marketing_email_log.created_at<NOW()-INTERVAL '2 hours'
        RETURNING id""", code=code, email=email, journey=journey,
        stage=content["stage"], reference=reference,
        subject=content["subject"][:250], due_at=due_at)


def run_marketing_automation(get_conn, send_email, action_token, dry_run=False, force=False):
    """Sélectionne puis envoie au maximum un message pertinent par contact."""
    conn = get_conn()
    try:
        ensure_marketing_schema(conn)
        sync_marketing_segments(conn)
        settings = conn.run("""SELECT enabled,daily_send_limit,weekly_contact_limit,
            min_gap_hours,batch_limit,legacy_campaign_enabled,legacy_daily_limit,
            legacy_started_at,legacy_paused_reason FROM marketing_settings WHERE id=1""")[0]
        (enabled, daily_limit, weekly_limit, min_gap, batch_limit,
         legacy_enabled, legacy_daily_limit, legacy_started_at,
         legacy_paused_reason) = settings
        if not enabled and not dry_run:
            return {"ok": True, "paused": True, "sent": 0, "candidates": []}
        hour = datetime.now(PARIS_TZ).hour
        if not force and not dry_run and not 9 <= hour < 20:
            return {"ok": True, "outside_window": True, "sent": 0, "candidates": []}
        sent_today = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>=CURRENT_DATE""")[0][0] or 0)
        remaining = max(0, min(int(batch_limit), int(daily_limit) - sent_today))
        legacy_sent_today = int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE journey='legacy_reactivation' AND status='sent'
              AND sent_at>=CURRENT_DATE""")[0][0] or 0)
        legacy_remaining = 0
        if legacy_enabled:
            healthy, pause_reason, _health = _legacy_delivery_health(conn)
            if not healthy:
                conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=FALSE,
                    legacy_paused_reason=:reason,updated_at=NOW() WHERE id=1""",
                    reason=pause_reason)
                legacy_enabled = False
                legacy_paused_reason = pause_reason
            else:
                start = legacy_started_at or _now()
                ramp_day = max(0, (_now().date() - start.date()).days)
                ramp_limit = 300 if ramp_day == 0 else 700 if ramp_day == 1 else int(legacy_daily_limit)
                legacy_remaining = max(0, min(int(legacy_daily_limit), ramp_limit) - legacy_sent_today)
        contacts = conn.run("""SELECT mc.member_code,mc.email,mc.first_name,mc.segment,
            COALESCE(m.created_at,mc.created_at),m.date_fin,m.billing_status,
            m.stripe_subscription_id,m.billing_cancel_at_period_end
            FROM marketing_contacts mc JOIN members m ON m.code=mc.member_code
            WHERE mc.unsubscribed_at IS NULL
              AND mc.email LIKE '%@%'
            ORDER BY CASE mc.segment WHEN 'suspended' THEN 0 WHEN 'active' THEN 1
                WHEN 'expiring' THEN 2 WHEN 'expired' THEN 3
                WHEN 'explorer' THEN 4 ELSE 5 END,mc.created_at""")
        legacy_rows = conn.run("""SELECT 'LEAD-'||id,email,first_name,'legacy_lead',
            imported_at,NULL,'','','' FROM marketing_legacy_leads
            WHERE status='active' AND unsubscribed_at IS NULL
            ORDER BY imported_at""")
        pending_rows = conn.run("""SELECT 'PENDING-'||SUBSTRING(MD5(LOWER(email)),1,16),
            LOWER(email),prenom,'pending_optin',created_at,NULL,'','',''
            FROM prospect_email_verifications
            WHERE source='explorer' AND status='pending' AND expires_at>NOW()
            ORDER BY created_at""")
        # Une confirmation Explorer en attente doit toujours passer avant une
        # ancienne séquence de reconquête portant sur la même adresse.
        contacts = list(contacts) + list(pending_rows) + list(legacy_rows)
        candidates = []
        seen_emails = set()
        legacy_selected = 0
        for contact in contacts:
            if len(candidates) >= (int(batch_limit) if dry_run else remaining):
                break
            normalized_email = str(contact[1] or "").strip().lower()
            if not normalized_email or normalized_email in seen_emails:
                continue
            seen_emails.add(normalized_email)
            # Un statut actif ou suspendu sur une adresse protège cette personne
            # de toute relance émise depuis un ancien code BCT dupliqué.
            if contact[3] in {"active", "suspended"}:
                continue
            if contact[3] == "legacy_lead":
                if not legacy_enabled or legacy_selected >= legacy_remaining:
                    continue
            if not _contact_rate_allowed(conn, contact[0], int(weekly_limit), int(min_gap)):
                continue
            candidate = _checkout_candidate(conn, contact)
            if not candidate:
                candidate = _renewal_candidate(conn, contact)
            if not candidate:
                candidate = _explorer_candidate(conn, contact)
            if not candidate:
                candidate = _legacy_lead_candidate(conn, contact)
            if not candidate:
                candidate = _pending_optin_candidate(conn, contact)
            if candidate:
                journey, content, reference, due_at = candidate
                candidates.append((contact, journey, content, reference, due_at))
                if journey == "legacy_reactivation":
                    legacy_selected += 1
        if dry_run:
            return {"ok": True, "dry_run": True, "sent": 0,
                    "candidates": [{"member_code": c[0], "segment": c[3],
                                    "journey": j, "stage": content["stage"],
                                    "due_at": due.isoformat() if due else None}
                                   for c, j, content, _reference, due in candidates]}
    finally:
        conn.close()

    sent = 0
    failed = 0
    results = []
    for contact, journey, content, reference, due_at in candidates:
        code, email, first_name = contact[0], contact[1], contact[2]
        conn = get_conn()
        try:
            claimed = _claim_email(conn, contact, journey, content, reference, due_at)
        finally:
            conn.close()
        if not claimed:
            continue
        log_id = int(claimed[0][0])
        unsubscribe_token = action_token(
            "marketing_preferences", {
                "member_code": code if not code.startswith("LEAD-") else "",
                "lead_id": int(code[5:]) if code.startswith("LEAD-") else None,
                "pending_email": email if code.startswith("PENDING-") else "",
                "email": email,
            },
            lifetime_seconds=60 * 60 * 24 * 730,
        )
        unsubscribe_url = f"{BASE_URL}/email/preferences/{unsubscribe_token}"
        subject = content["subject"].format(prenom=first_name or "Bonjour")
        body = _email_html(first_name, content, journey, content["stage"], unsubscribe_url)
        result = send_email(email, first_name or "Membre Bectanse", subject, body,
                            f"marketing-{journey}-{content['stage']}")
        conn = get_conn()
        try:
            if result.get("ok"):
                conn.run("""UPDATE marketing_email_log SET status='sent',sent_at=NOW(),
                    provider_message_id=:message,error='' WHERE id=:id""",
                    message=str(result.get("message_id", ""))[:250], id=log_id)
                sent += 1
                if code.startswith("LEAD-"):
                    conn.run("""UPDATE marketing_legacy_leads SET last_contact_at=NOW()
                        WHERE id=:id""", id=int(code[5:]))
            else:
                conn.run("""UPDATE marketing_email_log SET status='failed',error=:error
                    WHERE id=:id""", error=str(result.get("error", "Erreur d'envoi"))[:500], id=log_id)
                failed += 1
        finally:
            conn.close()
        results.append({"member_code": code, "journey": journey,
                        "stage": content["stage"], "ok": bool(result.get("ok"))})
    return {"ok": True, "sent": sent, "failed": failed, "candidates": results}


def _marketing_dashboard_data(conn):
    ensure_marketing_schema(conn)
    sync_marketing_segments(conn)
    segment_rows = conn.run("""SELECT segment,COUNT(*) FROM marketing_contacts
        GROUP BY segment ORDER BY segment""")
    segments = {str(row[0]): int(row[1]) for row in segment_rows}
    settings = conn.run("""SELECT enabled,daily_send_limit,weekly_contact_limit,
        min_gap_hours,batch_limit,updated_at,legacy_campaign_enabled,
        legacy_daily_limit,legacy_started_at,legacy_paused_reason
        FROM marketing_settings WHERE id=1""")[0]
    stats = {
        "sent_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
        "sent_7d": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='sent' AND sent_at>NOW()-INTERVAL '7 days'""")[0][0] or 0),
        "failed_7d": int(conn.run("""SELECT COUNT(*) FROM marketing_email_log
            WHERE status='failed' AND created_at>NOW()-INTERVAL '7 days'""")[0][0] or 0),
        "open_checkouts": int(conn.run("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE status='open'""")[0][0] or 0),
        "converted_checkouts": int(conn.run("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE status='converted'""")[0][0] or 0),
        "unsubscribed": int(conn.run("""SELECT COUNT(*) FROM marketing_contacts
            WHERE unsubscribed_at IS NOT NULL""")[0][0] or 0),
        "legacy_leads": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE status='active' AND unsubscribed_at IS NULL""")[0][0] or 0),
        "legacy_clicked": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE clicked_at IS NOT NULL AND unsubscribed_at IS NULL""")[0][0] or 0),
        "legacy_suppressed": int(conn.run("""SELECT COUNT(*) FROM marketing_legacy_leads
            WHERE status='suppressed' OR unsubscribed_at IS NOT NULL""")[0][0] or 0),
        "hard_bounces_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_events
            WHERE event_type IN ('hardbounce','invalid','blocked')
              AND event_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
        "clicks_24h": int(conn.run("""SELECT COUNT(*) FROM marketing_email_events
            WHERE event_type='click' AND event_at>NOW()-INTERVAL '24 hours'""")[0][0] or 0),
    }
    recent = conn.run("""SELECT l.sent_at,l.member_code,c.first_name,l.journey,l.stage,
        l.status,l.error FROM marketing_email_log l
        LEFT JOIN marketing_contacts c ON c.member_code=l.member_code
        ORDER BY l.created_at DESC LIMIT 40""")
    return segments, settings, stats, recent


def _normalize_brevo_event(value):
    key = re.sub(r"[^a-z]", "", str(value or "").lower())
    aliases = {
        "hardbounce": "hardbounce", "softbounce": "softbounce",
        "delivered": "delivered", "click": "click", "clicked": "click",
        "spam": "spam", "complaint": "spam", "invalid": "invalid",
        "blocked": "blocked", "unsubscribed": "unsubscribed",
        "opened": "opened", "uniqueopened": "opened", "deferred": "deferred",
        "sent": "sent", "request": "sent",
    }
    return aliases.get(key, key[:40])


def _brevo_tags(payload):
    tags = payload.get("tags", payload.get("tag", []))
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
            tags = parsed if isinstance(parsed, list) else [tags]
        except Exception:
            tags = [tags]
    return [str(tag)[:160] for tag in (tags or [])]


def _record_brevo_events(conn, payload):
    events = payload if isinstance(payload, list) else [payload]
    accepted = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        tags = _brevo_tags(item)
        if not any(tag.startswith("marketing-") for tag in tags):
            continue
        email = str(item.get("email") or item.get("to") or "").strip().lower()[:254]
        if "@" not in email:
            continue
        event_type = _normalize_brevo_event(item.get("event") or item.get("type"))
        if not event_type:
            continue
        provider_id = str(item.get("message-id") or item.get("messageId") or "")[:250]
        canonical = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        log_rows = conn.run("""SELECT id,journey,stage FROM marketing_email_log
            WHERE LOWER(recipient_email)=LOWER(:email)
            ORDER BY CASE WHEN provider_message_id<>'' AND provider_message_id=:provider THEN 0 ELSE 1 END,
                created_at DESC LIMIT 1""", email=email, provider=provider_id)
        journey = str(log_rows[0][1]) if log_rows else ""
        stage = str(log_rows[0][2]) if log_rows else ""
        inserted = conn.run("""INSERT INTO marketing_email_events
            (event_hash,recipient_email,event_type,provider_message_id,journey,stage,payload)
            VALUES (:hash,:email,:event,:provider,:journey,:stage,:payload)
            ON CONFLICT (event_hash) DO NOTHING RETURNING id""",
            hash=event_hash, email=email, event=event_type, provider=provider_id,
            journey=journey, stage=stage, payload=canonical[:4000])
        if not inserted:
            continue
        accepted += 1
        if log_rows:
            log_id = int(log_rows[0][0])
            if event_type == "delivered":
                conn.run("""UPDATE marketing_email_log SET provider_status='delivered',
                    delivered_at=COALESCE(delivered_at,NOW()) WHERE id=:id""", id=log_id)
            elif event_type == "click":
                conn.run("""UPDATE marketing_email_log SET provider_status='clicked',
                    clicked_at=COALESCE(clicked_at,NOW()) WHERE id=:id""", id=log_id)
            elif event_type in {"hardbounce", "softbounce", "invalid", "blocked", "spam", "unsubscribed"}:
                conn.run("""UPDATE marketing_email_log SET provider_status=:status
                    WHERE id=:id""", status=event_type, id=log_id)
        if event_type == "delivered":
            conn.run("""UPDATE marketing_legacy_leads SET delivered_at=COALESCE(delivered_at,NOW())
                WHERE LOWER(email)=LOWER(:email)""", email=email)
        elif event_type == "click":
            conn.run("""UPDATE marketing_legacy_leads SET clicked_at=COALESCE(clicked_at,NOW())
                WHERE LOWER(email)=LOWER(:email) AND status='active'""", email=email)
        elif event_type in {"hardbounce", "invalid", "blocked", "spam", "unsubscribed"}:
            conn.run("""UPDATE marketing_legacy_leads SET status='suppressed',
                unsubscribed_at=COALESCE(unsubscribed_at,NOW()),bounced_at=CASE
                    WHEN :event IN ('hardbounce','invalid','blocked') THEN COALESCE(bounced_at,NOW())
                    ELSE bounced_at END WHERE LOWER(email)=LOWER(:email)""",
                event=event_type, email=email)
            conn.run("""UPDATE marketing_contacts SET unsubscribed_at=COALESCE(unsubscribed_at,NOW()),
                updated_at=NOW() WHERE LOWER(email)=LOWER(:email)""", email=email)
    return accepted


def register_marketing_routes(app, get_conn, send_email, action_token, action_payload, admin_required):
    @app.route("/webhooks/brevo/marketing", methods=["POST"])
    def brevo_marketing_webhook():
        secret = os.environ.get("BREVO_WEBHOOK_SECRET", "")
        authorization = request.headers.get("Authorization", "")
        if not secret or not hmac.compare_digest(authorization, "Bearer " + secret):
            return jsonify({"ok": False}), 403
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"ok": False, "error": "JSON invalide"}), 400
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            accepted = _record_brevo_events(conn, payload)
        finally:
            conn.close()
        return jsonify({"ok": True, "accepted": accepted})

    @app.route("/admin/marketing")
    @admin_required
    def admin_marketing():
        conn = get_conn()
        try:
            segments, settings, stats, recent = _marketing_dashboard_data(conn)
        finally:
            conn.close()
        return render_template("admin_marketing.html", segments=segments,
            settings=settings, stats=stats, recent=recent,
            explorer_stages=EXPLORER_STAGES, checkout_stages=CHECKOUT_STAGES,
            reactivation_stages=REACTIVATION_STAGES)

    @app.route("/admin/marketing/run", methods=["POST"])
    @admin_required
    def admin_marketing_run():
        data = request.get_json(silent=True) or {}
        result = run_marketing_automation(
            get_conn, send_email, action_token,
            dry_run=bool(data.get("dry_run", True)), force=bool(data.get("force", False)))
        return jsonify(result)

    @app.route("/admin/marketing/toggle", methods=["POST"])
    @admin_required
    def admin_marketing_toggle():
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled") is True
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            conn.run("""UPDATE marketing_settings SET enabled=:enabled,updated_at=NOW()
                WHERE id=1""", enabled=enabled)
        finally:
            conn.close()
        return jsonify({"ok": True, "enabled": enabled})

    @app.route("/admin/marketing/legacy-toggle", methods=["POST"])
    @admin_required
    def admin_marketing_legacy_toggle():
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled") is True
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=:enabled,
                legacy_started_at=CASE WHEN :enabled AND legacy_started_at IS NULL
                    THEN NOW() ELSE legacy_started_at END,
                legacy_paused_reason=CASE WHEN :enabled THEN '' ELSE 'Pause manuelle' END,
                updated_at=NOW() WHERE id=1""", enabled=enabled)
        finally:
            conn.close()
        return jsonify({"ok": True, "legacy_campaign_enabled": enabled})

    @app.route("/admin/marketing/import-leads", methods=["POST"])
    @admin_required
    def admin_marketing_import_leads():
        upload = request.files.get("file")
        if not upload:
            return jsonify({"ok": False, "error": "Fichier CSV manquant"}), 400
        try:
            text = upload.stream.read(2 * 1024 * 1024).decode("utf-8-sig")
        except Exception:
            return jsonify({"ok": False, "error": "Le fichier doit être un CSV UTF-8"}), 400
        sample = text[:4000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
            dialect.delimiter = ";"
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        imported = duplicate = invalid = 0
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            for raw in reader:
                normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
                email = (normalized.get("email") or normalized.get("e-mail") or
                         normalized.get("mail") or normalized.get("adresse email") or "").lower()[:254]
                first = (normalized.get("prenom") or normalized.get("prénom") or
                         normalized.get("first_name") or normalized.get("nom") or "")[:80]
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    invalid += 1
                    continue
                rows = conn.run("""INSERT INTO marketing_legacy_leads
                    (email,first_name,source,status) VALUES (:email,:first,'anciens-prospects-2025','active')
                    ON CONFLICT (LOWER(email)) DO NOTHING RETURNING id""", email=email, first=first)
                if rows:
                    imported += 1
                else:
                    duplicate += 1
        finally:
            conn.close()
        return jsonify({"ok": True, "imported": imported, "duplicates": duplicate, "invalid": invalid})

    @app.route("/email/preferences/<token>", methods=["GET", "POST"])
    def marketing_preferences(token):
        payload = action_payload(token, "marketing_preferences")
        if not payload:
            return render_template("email_preferences.html", invalid=True,
                                   unsubscribed=False, email=""), 400
        code = str(payload.get("member_code", ""))[:80]
        lead_id = payload.get("lead_id")
        pending_email = str(payload.get("pending_email", "")).strip().lower()[:254]
        email = str(payload.get("email", "")).strip().lower()[:254]
        conn = get_conn()
        try:
            ensure_marketing_schema(conn)
            if pending_email:
                rows = conn.run("""SELECT CASE WHEN status='declined' THEN NOW() ELSE NULL END
                    FROM prospect_email_verifications WHERE LOWER(email)=LOWER(:email)
                      AND source='explorer'""", email=pending_email)
            elif lead_id:
                rows = conn.run("""SELECT unsubscribed_at FROM marketing_legacy_leads
                    WHERE id=:id AND LOWER(email)=LOWER(:email)""", id=int(lead_id), email=email)
            else:
                rows = conn.run("""SELECT unsubscribed_at FROM marketing_contacts
                    WHERE member_code=:code AND LOWER(email)=LOWER(:email)""", code=code, email=email)
            if not rows:
                return render_template("email_preferences.html", invalid=True,
                                       unsubscribed=False, email=""), 404
            if request.method == "POST":
                if pending_email:
                    conn.run("""UPDATE prospect_email_verifications SET status='declined'
                        WHERE LOWER(email)=LOWER(:email) AND source='explorer' AND status='pending'""",
                        email=pending_email)
                elif lead_id:
                    conn.run("""UPDATE marketing_legacy_leads SET unsubscribed_at=NOW(),
                        status='unsubscribed' WHERE id=:id AND LOWER(email)=LOWER(:email)""",
                        id=int(lead_id), email=email)
                else:
                    conn.run("""UPDATE marketing_contacts SET unsubscribed_at=NOW(),updated_at=NOW()
                        WHERE member_code=:code AND LOWER(email)=LOWER(:email)""", code=code, email=email)
                return redirect(url_for("marketing_preferences", token=token, done="1"))
            unsubscribed = bool(rows[0][0]) or request.args.get("done") == "1"
        finally:
            conn.close()
        return render_template("email_preferences.html", invalid=False,
                               unsubscribed=unsubscribed, email=email)
