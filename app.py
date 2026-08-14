import os, json, secrets, string, requests, time, threading, csv, io, hashlib, hmac, unicodedata, base64, uuid, re
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from flask import send_from_directory, Flask, render_template, request, redirect, url_for, session, jsonify, Response
from werkzeug.utils import secure_filename
import pg8000.native

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bectanse2026secretkeyprod")
app.config["PERMANENT_SESSION_LIFETIME"] = __import__("datetime").timedelta(days=30)

BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "8673691177:AAGWihA4Ch_T73nuJCLUq49Yr_3OiFdOoHs")
ADMIN_ID   = os.environ.get("ADMIN_ID",   "6164373751")
ADMIN_KEY  = os.environ.get("ADMIN_KEY",  "bectanse_admin_2026")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "BI5TQpefuRvs_HIPgRzXnBQqcQ5V9puh2hteQmdRp8pQFMEh-XyvgPGpYrO5ioPak9Z7ml6laSl2WnNh96RFrv8")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgmeZdDREE6AdkScXLD0GeI65NwMQ3C7kBzmRN49e-XTqhRANCAASOU0KXn7kb7PxyD4Ec15wUKnEOVfabodobXkJnUafKUBTBIfl8r4DxqWKzuYqD2pPWe5pepWkpdlpzYfekRa7_")
VAPID_CLAIMS      = {"sub": "mailto:bectanseacademie@gmail.com"}
CLOUDINARY_CLOUD  = os.environ.get("CLOUDINARY_CLOUD", "dqgd441is")
CLOUDINARY_KEY    = os.environ.get("CLOUDINARY_KEY", "631288474842446")
CLOUDINARY_SECRET = os.environ.get("CLOUDINARY_SECRET", "GqmAD-4OOtkLGhu6boCcnwUXXUE")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_ANALYSIS_MODEL = os.environ.get("OPENAI_ANALYSIS_MODEL", "gpt-5.6-luna")
ANALYSIS_INITIAL_CREDITS = int(os.environ.get("ANALYSIS_INITIAL_CREDITS", "5"))
ANALYSIS_MAX_IMAGE_BYTES = 6 * 1024 * 1024
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ANALYSIS_PACKS = {
    "10": {"credits": 10, "amount_cents": 990, "label": "Pack Découverte"},
    "30": {"credits": 30, "amount_cents": 2490, "label": "Pack Trader"},
    "100": {"credits": 100, "amount_cents": 5990, "label": "Pack Pro"},
}
ECO_BOT_TOKEN = os.environ.get("ECO_BOT_TOKEN", "8565312655:AAFyfFQvKEiFtFJYA0yDQE1bLdH8N50UX4c")
ECO_CANAL    = os.environ.get("ECO_CANAL", "@BECTANSE_ACADEMIE")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PARIS_TZ = ZoneInfo("Europe/Paris")
TELEGRAM_EDITORIAL_PATH = os.environ.get(
    "TELEGRAM_EDITORIAL_PATH",
    os.path.join(APP_DIR, "content", "telegram_posts.json")
)
TELEGRAM_CSV_TEMPLATE_PATH = os.path.join(
    APP_DIR, "content", "modele_planning_telegram_semaine.csv"
)


def _format_editorial_entry(calendar, post):
    parts = [f"🔥 *{post['title'].strip()}*", "", "\n".join(post["body"]).strip()]
    if post.get("cta"):
        parts.extend(["", f"💬 {post['cta'].strip()}"])
    parts.extend([
        "",
        "━━━━━━━━━━━━━━━",
        calendar["footer"].strip(),
        f"_{calendar['disclaimer'].strip()}_"
    ])
    return "\n".join(parts)

# ── DB ────────────────────────────────────────────────────────────────────────

def parse_db_url(url):
    url = url.replace("postgres://","").replace("postgresql://","")
    user_pass, rest = url.split("@")
    user, password = user_pass.split(":",1)
    host_port, database = rest.split("/",1)
    host, port = (host_port.split(":") if ":" in host_port else [host_port,"5432"])
    return {"user":user,"password":password,"host":host,"port":int(port),"database":database}

def get_conn():
    p = parse_db_url(DATABASE_URL)
    return pg8000.native.Connection(
        user=p["user"], password=p["password"],
        host=p["host"], port=p["port"], database=p["database"], ssl_context=True
    )

def init_db():
    for attempt in range(5):
        try:
            conn = get_conn()
            conn.run("""
                CREATE TABLE IF NOT EXISTS members (
                    code              TEXT PRIMARY KEY,
                    nom               TEXT NOT NULL,
                    capital           TEXT NOT NULL,
                    actif             BOOLEAN DEFAULT TRUE,
                    created_at        TIMESTAMP DEFAULT NOW(),
                    last_login        TIMESTAMP,
                    params            TEXT DEFAULT '{}',
                    copy_actif        BOOLEAN DEFAULT TRUE,
                    date_souscription TIMESTAMP DEFAULT NOW(),
                    date_fin          TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days'),
                    email             TEXT DEFAULT '',
                    telephone         TEXT DEFAULT '',
                    telegram          TEXT DEFAULT '',
                    alerte_lue        BOOLEAN DEFAULT TRUE,
                    parrain_code      TEXT DEFAULT '',
                    filleuls_count    INTEGER DEFAULT 0,
                    gains_parrainage  INTEGER DEFAULT 0,
                    paiement_iban     TEXT DEFAULT '',
                    paiement_bic      TEXT DEFAULT '',
                    paiement_titulaire TEXT DEFAULT '',
                    paiement_crypto_reseau TEXT DEFAULT '',
                    paiement_crypto_adresse TEXT DEFAULT '',
                    paiement_type     TEXT DEFAULT '',
                    historique        TEXT DEFAULT '[]'
                )
            """)
            conn.run("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id          SERIAL PRIMARY KEY,
                    member_code TEXT NOT NULL,
                    endpoint    TEXT UNIQUE NOT NULL,
                    p256dh      TEXT NOT NULL,
                    auth        TEXT NOT NULL,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.run("""
                CREATE TABLE IF NOT EXISTS renewal_email_log (
                    member_code         TEXT NOT NULL,
                    expiry_date         DATE NOT NULL,
                    stage               TEXT NOT NULL,
                    recipient_email     TEXT NOT NULL,
                    status              TEXT NOT NULL DEFAULT 'pending',
                    provider_message_id TEXT NOT NULL DEFAULT '',
                    error               TEXT NOT NULL DEFAULT '',
                    created_at          TIMESTAMP DEFAULT NOW(),
                    sent_at             TIMESTAMP,
                    PRIMARY KEY (member_code, expiry_date, stage)
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS renewal_email_log_status_idx
                         ON renewal_email_log (status, created_at)""")
            conn.run("""
                CREATE TABLE IF NOT EXISTS prospect_email_verifications (
                    email       TEXT PRIMARY KEY,
                    prenom      TEXT NOT NULL DEFAULT '',
                    token_hash  TEXT UNIQUE NOT NULL,
                    source      TEXT NOT NULL DEFAULT 'explorer',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    created_at  TIMESTAMP DEFAULT NOW(),
                    expires_at  TIMESTAMP NOT NULL,
                    verified_at TIMESTAMP
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS prospect_verification_status_idx
                         ON prospect_email_verifications (status, expires_at)""")
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_wallets (
                    member_code      TEXT PRIMARY KEY,
                    balance          INTEGER NOT NULL DEFAULT 5 CHECK (balance >= 0),
                    lifetime_granted INTEGER NOT NULL DEFAULT 5,
                    lifetime_spent   INTEGER NOT NULL DEFAULT 0,
                    created_at       TIMESTAMP DEFAULT NOW(),
                    updated_at       TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id               TEXT PRIMARY KEY,
                    member_code      TEXT NOT NULL,
                    status           TEXT NOT NULL DEFAULT 'processing',
                    timeframe        TEXT NOT NULL,
                    session_name     TEXT NOT NULL,
                    trading_style    TEXT NOT NULL,
                    market           TEXT NOT NULL DEFAULT 'XAU/USD',
                    image_mime       TEXT NOT NULL,
                    result_json      TEXT NOT NULL DEFAULT '',
                    error            TEXT NOT NULL DEFAULT '',
                    model            TEXT NOT NULL DEFAULT '',
                    input_tokens     INTEGER NOT NULL DEFAULT 0,
                    output_tokens    INTEGER NOT NULL DEFAULT 0,
                    search_calls     INTEGER NOT NULL DEFAULT 0,
                    created_at       TIMESTAMP DEFAULT NOW(),
                    completed_at     TIMESTAMP
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS analysis_jobs_member_idx
                         ON analysis_jobs (member_code, created_at DESC)""")
            try:
                conn.run("ALTER TABLE analysis_jobs ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'XAU/USD'")
            except Exception:
                pass
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_credit_ledger (
                    id            SERIAL PRIMARY KEY,
                    member_code   TEXT NOT NULL,
                    delta         INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason        TEXT NOT NULL,
                    reference     TEXT UNIQUE NOT NULL,
                    created_at    TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS analysis_ledger_member_idx
                         ON analysis_credit_ledger (member_code, created_at DESC)""")
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_purchases (
                    id                SERIAL PRIMARY KEY,
                    stripe_session_id TEXT UNIQUE NOT NULL,
                    member_code       TEXT NOT NULL,
                    credits           INTEGER NOT NULL,
                    amount_cents      INTEGER NOT NULL,
                    currency          TEXT NOT NULL DEFAULT 'eur',
                    status            TEXT NOT NULL DEFAULT 'paid',
                    created_at        TIMESTAMP DEFAULT NOW()
                )
            """)
            # Migration colonnes canal_messages
            for ccol, ctyp, cdef in [
                ("audio_url","TEXT","''"),
                ("deleted","BOOLEAN","FALSE")
            ]:
                try:
                    conn.run(f"ALTER TABLE canal_messages ADD COLUMN IF NOT EXISTS {ccol} {ctyp} DEFAULT {cdef}")
                except: pass
            for col, typ, default in [
                ("copy_actif","BOOLEAN","TRUE"),
                ("date_souscription","TIMESTAMP","NOW()"),
                ("date_fin","TIMESTAMP","NOW() + INTERVAL '30 days'"),
                ("email","TEXT","''"),
                ("telephone","TEXT","''"),
                ("telegram","TEXT","''"),
                ("alerte_lue","BOOLEAN","TRUE"),
            ]:
                try:
                    conn.run(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except: pass
            # Tables formation et annonces
            conn.run("""CREATE TABLE IF NOT EXISTS formation_videos (
                id SERIAL PRIMARY KEY,
                num INTEGER, titre TEXT, youtube_id TEXT,
                duree TEXT, ordre INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
            conn.run("""CREATE TABLE IF NOT EXISTS formation_pdfs (
                id SERIAL PRIMARY KEY,
                num INTEGER, titre TEXT, drive_id TEXT,
                taille TEXT, ordre INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
            conn.run("""CREATE TABLE IF NOT EXISTS annonces (
                id SERIAL PRIMARY KEY,
                titre TEXT, contenu TEXT,
                type TEXT DEFAULT 'message',
                audio_url TEXT DEFAULT '',
                cible TEXT DEFAULT 'tous',
                cible_code TEXT DEFAULT '',
                actif BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
            conn.run("""CREATE TABLE IF NOT EXISTS scheduled_publications (
                slot_key            TEXT PRIMARY KEY,
                post_kind           TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'sending',
                content             TEXT NOT NULL DEFAULT '',
                telegram_message_id BIGINT,
                attempts            INTEGER NOT NULL DEFAULT 1,
                error               TEXT NOT NULL DEFAULT '',
                created_at          TIMESTAMP DEFAULT NOW(),
                sent_at             TIMESTAMP
            )""")
            conn.run("ALTER TABLE scheduled_publications ADD COLUMN IF NOT EXISTS post_id INTEGER")
            conn.run("ALTER TABLE scheduled_publications ADD COLUMN IF NOT EXISTS target_channel TEXT NOT NULL DEFAULT ''")
            conn.run("""CREATE TABLE IF NOT EXISTS telegram_scheduled_posts (
                id                   SERIAL PRIMARY KEY,
                name                 TEXT NOT NULL,
                message              TEXT NOT NULL,
                image_url            TEXT NOT NULL DEFAULT '',
                schedule_type        TEXT NOT NULL DEFAULT 'weekly',
                weekdays             TEXT NOT NULL DEFAULT '0',
                rotation_week        INTEGER,
                publish_time         TEXT NOT NULL DEFAULT '18:30',
                scheduled_for        TIMESTAMP,
                timezone             TEXT NOT NULL DEFAULT 'Europe/Paris',
                channel              TEXT NOT NULL DEFAULT '@BECTANSE_ACADEMIE',
                button_text          TEXT NOT NULL DEFAULT '',
                button_url           TEXT NOT NULL DEFAULT '',
                disable_notification BOOLEAN NOT NULL DEFAULT FALSE,
                enabled              BOOLEAN NOT NULL DEFAULT TRUE,
                source_key           TEXT UNIQUE,
                deleted              BOOLEAN NOT NULL DEFAULT FALSE,
                last_sent_at         TIMESTAMP,
                created_at           TIMESTAMP DEFAULT NOW(),
                updated_at           TIMESTAMP DEFAULT NOW()
            )""")
            for column, definition in [
                ("post_type", "TEXT NOT NULL DEFAULT 'message'"),
                ("poll_question", "TEXT NOT NULL DEFAULT ''"),
                ("poll_options", "TEXT NOT NULL DEFAULT '[]'"),
                ("poll_correct_option_ids", "TEXT NOT NULL DEFAULT '[]'"),
                ("poll_explanation", "TEXT NOT NULL DEFAULT ''"),
                ("poll_anonymous", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("poll_multiple", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("publish_all_channels", "BOOLEAN NOT NULL DEFAULT TRUE"),
            ]:
                conn.run(
                    f"ALTER TABLE telegram_scheduled_posts "
                    f"ADD COLUMN IF NOT EXISTS {column} {definition}"
                )
            conn.run("""CREATE INDEX IF NOT EXISTS telegram_scheduled_posts_due_idx
                         ON telegram_scheduled_posts (enabled, deleted, publish_time)""")
            conn.run("""CREATE TABLE IF NOT EXISTS telegram_channels (
                id                   SERIAL PRIMARY KEY,
                name                 TEXT NOT NULL,
                chat_id              TEXT UNIQUE NOT NULL,
                active               BOOLEAN NOT NULL DEFAULT TRUE,
                deleted              BOOLEAN NOT NULL DEFAULT FALSE,
                last_check_status    TEXT NOT NULL DEFAULT '',
                last_check_at        TIMESTAMP,
                created_at           TIMESTAMP DEFAULT NOW(),
                updated_at           TIMESTAMP DEFAULT NOW()
            )""")
            conn.run("""CREATE TABLE IF NOT EXISTS telegram_media_library (
                id                   SERIAL PRIMARY KEY,
                title                TEXT NOT NULL,
                image_url            TEXT UNIQUE NOT NULL,
                category             TEXT NOT NULL DEFAULT 'personal',
                caption              TEXT NOT NULL DEFAULT '',
                cta_text             TEXT NOT NULL DEFAULT '',
                cta_url              TEXT NOT NULL DEFAULT '',
                source_type          TEXT NOT NULL DEFAULT 'custom',
                deleted              BOOLEAN NOT NULL DEFAULT FALSE,
                created_at           TIMESTAMP DEFAULT NOW(),
                updated_at           TIMESTAMP DEFAULT NOW()
            )""")
            conn.run("""CREATE TABLE IF NOT EXISTS telegram_post_channels (
                post_id              INTEGER NOT NULL REFERENCES telegram_scheduled_posts(id) ON DELETE CASCADE,
                channel_id           INTEGER NOT NULL REFERENCES telegram_channels(id) ON DELETE CASCADE,
                PRIMARY KEY (post_id, channel_id)
            )""")
            if ECO_CANAL:
                conn.run(
                    """INSERT INTO telegram_channels (name, chat_id, active, deleted)
                       VALUES ('Bectanse Académie', :chat_id, TRUE, FALSE)
                       ON CONFLICT (chat_id) DO UPDATE SET deleted=FALSE""",
                    chat_id=ECO_CANAL
                )

            # Import initial des 28 publications éditoriales. source_key et le
            # soft-delete empêchent toute recréation après une suppression admin.
            try:
                with open(TELEGRAM_EDITORIAL_PATH, "r", encoding="utf-8") as content_file:
                    editorial_calendar = json.load(content_file)
                default_time = editorial_calendar.get("publish_time", "18:30")
                for week_index, week in enumerate(editorial_calendar.get("weeks", [])):
                    for weekday_index, post in enumerate(week):
                        conn.run(
                            """INSERT INTO telegram_scheduled_posts
                               (name, message, schedule_type, weekdays, rotation_week,
                                publish_time, channel, enabled, source_key)
                               VALUES (:name, :message, 'rotation', :weekday, :rotation,
                                       :publish_time, :channel, TRUE, :source_key)
                               ON CONFLICT (source_key) DO NOTHING""",
                            name=f"{post.get('weekday', '').capitalize()} — {post.get('title', '')}",
                            message=_format_editorial_entry(editorial_calendar, post),
                            weekday=str(weekday_index), rotation=week_index,
                            publish_time=default_time, channel=ECO_CANAL,
                            source_key=f"editorial-v1-{week_index}-{weekday_index}"
                        )
            except Exception as seed_error:
                app.logger.warning(f"telegram editorial seed: {seed_error}")
            # Migration robuste — ajouter toutes les colonnes une par une
            cols_to_add = [
                ("parrain_code", "TEXT", "''"),
                ("filleuls_count", "INTEGER", "0"),
                ("gains_parrainage", "INTEGER", "0"),
                ("paiement_type", "TEXT", "''"),
                ("paiement_iban", "TEXT", "''"),
                ("paiement_bic", "TEXT", "''"),
                ("paiement_titulaire", "TEXT", "''"),
                ("paiement_crypto_reseau", "TEXT", "''"),
                ("paiement_crypto_adresse", "TEXT", "''"),
                ("notif_type", "TEXT", "''"),
                ("notif_message", "TEXT", "''"),
                ("notif_lue", "BOOLEAN", "TRUE"),
                ("alerte_lue", "BOOLEAN", "TRUE"),
                ("telegram", "TEXT", "''"),
                ("email", "TEXT", "''"),
                ("telephone", "TEXT", "''"),
                ("copy_actif", "BOOLEAN", "TRUE"),
                ("date_souscription", "TIMESTAMP", "NOW()"),
                ("date_fin", "TIMESTAMP", "NOW() + INTERVAL \'30 days\'"),
            ]
            for col, typ, default in cols_to_add:
                try:
                    conn.run(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except: pass
            conn.close()
            return True
        except Exception as e:
            app.logger.warning(f"init_db attempt {attempt+1}: {e}")
            time.sleep(2)
    return False

def get_member(code):
    try:
        conn = get_conn()
        rows = conn.run("SELECT * FROM members WHERE UPPER(code)=UPPER(:c)", c=code)
        if not rows:
            conn.close()
            return None
        cols = [c["name"] for c in conn.columns]
        m = dict(zip(cols, rows[0]))
        conn.close()
        for k in ("params","historique"):
            v = m.get(k)
            if isinstance(v, str):
                try: m[k] = json.loads(v)
                except: m[k] = {} if k=="params" else []
            elif v is None:
                m[k] = {} if k=="params" else []
        if m.get("copy_actif") is None: m["copy_actif"] = True
        return m
    except Exception as e:
        app.logger.error(f"get_member: {e}")
        return None


def enforce_member_access_state():
    """Désactive définitivement les accès techniques des comptes expirés ou suspendus."""
    conn = get_conn()
    try:
        changed = conn.run("""UPDATE members
            SET actif=CASE
                    WHEN date_fin IS NOT NULL AND date_fin <= NOW() THEN FALSE
                    ELSE actif
                END,
                copy_actif=CASE
                    WHEN actif=FALSE OR (date_fin IS NOT NULL AND date_fin <= NOW()) THEN FALSE
                    ELSE copy_actif
                END
            WHERE actif=FALSE
               OR (date_fin IS NOT NULL AND date_fin <= NOW())
            RETURNING code""")
        return len(changed or [])
    finally:
        conn.close()


def default_params():
    return {"mode_risque":"Lots fixes","lots":0.01,"lots_max":5,"slippage":100,
            "forcer_lot_minimum":False,"inverser_trades":False,
            "copier_ordres_en_attente":True,"convertir_pending_invalide":False,
            "copier_sl":True,"drawdown_actif":False,"drawdown_pct":5.0,
            "drawdown_gain_actif":False,"drawdown_gain_pct":5.0,
            "objectif_actif":False,"objectif_gain_pct":5.0,"objectif_perte_pct":3.0,
            "objectif_periode":"Mensuel","filtre_news":False,
            "risque_pct":1.0,"multiplicateur":1.0,"risque_balance_pct":1.0,
            "risque_equity_pct":1.0,"lot_symboles":{}}

def bool_icon(v): return "✅" if v else "❌"

def build_notif(member, params, code):
    p = params
    dd_p = f"`{p['drawdown_pct']}%`" if p.get("drawdown_actif") else "Désactivé"
    dd_g = f"`{p['drawdown_gain_pct']}%`" if p.get("drawdown_gain_actif") else "Désactivé"
    obj  = (f"+{p['objectif_gain_pct']}% / -{p['objectif_perte_pct']}% ({p['objectif_periode']})"
            if p.get("objectif_actif") else "Désactivé")
    mode_detail = ""
    mode = p.get("mode_risque","")
    if mode == "Risque en %": mode_detail = f"  └ Risque : `{p.get('risque_pct','—')}%`\n"
    elif mode == "Copier les lots de l'envoyeur": mode_detail = f"  └ Multiplicateur : `{p.get('multiplicateur','—')}x`\n"
    elif mode == "Risque par solde (Balance)": mode_detail = f"  └ Balance : `{p.get('risque_balance_pct','—')}%`\n"
    elif mode == "Risque par capitaux (Equity)": mode_detail = f"  └ Equity : `{p.get('risque_equity_pct','—')}%`\n"
    sym_lines = ""
    if mode == "Lot par symbole" and p.get("lot_symboles"):
        modifies = [(s,l) for s,l in p["lot_symboles"].items() if float(l) != 0.01]
        if modifies:
            sym_lines = "\n📋 *SYMBOLES*\n" + "".join([f"  `{s}` : `{l}`\n" for s,l in modifies])
    return (
        f"🔔 *DEMANDE PARAMÈTRES*\n\n"
        f"👤 *{member['nom']}* | `{code}`\n"
        f"💰 *{member['capital']}*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚙️ *MODE* : {mode}\n"
        f"📊 Lots : `{p.get('lots')}` | Max : `{p.get('lots_max')}` | Slip : `{p.get('slippage')}`\n"
        + mode_detail + sym_lines +
        f"\n🔧 *OPTIONS*\n"
        f"  {bool_icon(p.get('forcer_lot_minimum'))} Lot min\n"
        f"  {bool_icon(p.get('inverser_trades'))} Inverser\n"
        f"  {bool_icon(p.get('copier_ordres_en_attente'))} Ordres attente\n"
        f"  {bool_icon(p.get('convertir_pending_invalide'))} Convertir pending\n"
        f"  {bool_icon(p.get('copier_sl'))} Copier SL\n\n"
        f"🛡️ DD Perte:{bool_icon(p.get('drawdown_actif'))} {dd_p} | Gain:{bool_icon(p.get('drawdown_gain_actif'))} {dd_g}\n"
        f"🎯 Objectif:{bool_icon(p.get('objectif_actif'))} {obj}\n"
        f"📅 News:{bool_icon(p.get('filtre_news'))}\n"
        f"━━━━━━━━━━━━━━━━"
    )

def send_webpush_notification(subscription_info, title, body, url="/accueil"):
    """Envoie une notification Web Push standard via VAPID."""
    try:
        import json as _json
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=subscription_info,
            data=_json.dumps({"title": title, "body": body, "url": url, "icon": "/static/icons/icon-192.png"}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
            timeout=10
        )
        return True
    except Exception as e:
        app.logger.debug(f"webpush: {e}")
        return False

def send_fcm_push(tokens, title, body, url="/accueil"):
    """Alias — envoie via Web Push à une liste de tokens (subscriptions JSON)."""
    try:
        conn = get_conn()
        rows = conn.run("SELECT subscription FROM push_tokens WHERE token = ANY(:t)", t=tokens)
        conn.close()
    except:
        rows = []
    for row in rows:
        try:
            import json as _json
            sub = _json.loads(row[0]) if row[0] else None
            if sub:
                send_webpush_notification(sub, title, body, url)
        except: pass

def send_push_to_all_fcm(title, body, url="/accueil"):
    """Envoie une notification Web Push à tous les membres abonnés."""
    try:
        import json as _json
        conn = get_conn()
        rows = conn.run("SELECT subscription FROM push_tokens WHERE subscription IS NOT NULL")
        conn.close()
        for row in rows:
            try:
                sub = _json.loads(row[0]) if row[0] else None
                if sub:
                    threading.Thread(target=send_webpush_notification, args=(sub, title, body, url), daemon=True).start()
            except: pass
    except Exception as e:
        app.logger.error(f"send_push_to_all_fcm: {e}")


def send_telegram(text, reply_markup=None, chat_id=None):
    """Envoie un message Telegram — 3 tentatives, alerte si échec total."""
    if not BOT_TOKEN: return
    target = str(chat_id) if chat_id else str(ADMIN_ID)
    payload = {"chat_id": target, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup

    for attempt in range(3):
        try:
            import time
            if attempt > 0: time.sleep(3)
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload, timeout=15
            )
            result = r.json()
            if result.get("ok"):
                return  # Succès
            # Erreur API Telegram
            err = result.get("description", "erreur inconnue")
            app.logger.error(f"Telegram tentative {attempt+1}: {err}")
        except Exception as e:
            app.logger.error(f"Telegram tentative {attempt+1} exception: {e}")

    # 3 tentatives échouées — envoyer alerte via bot de secours (ECO_BOT)
    try:
        alert = (
            f"🚨 *ALERTE SYSTÈME*\n\n"
            f"Une notification n'a pas pu être envoyée sur le bot admin.\n"
            f"Vérifie immédiatement.\n\n"
            f"Destinataire : `{target}`\n"
            f"Message : {text[:200]}..."
        )
        requests.post(
            f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": alert, "parse_mode": "Markdown"},
            timeout=10
        )
    except: pass

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "member_code" not in session:
            return redirect(url_for("login"))
        # Vérifier expiration — rediriger vers page blocage sauf routes autorisées
        allowed = ["accueil_expire", "logout", "marquer_alerte_lue", "marquer_notif_lue",
                   "offres", "service_worker", "health", "push_register"]
        if f.__name__ not in allowed:
            try:
                from datetime import datetime
                code = session["member_code"]
                enforce_member_access_state()
                conn = get_conn()
                rows = conn.run("SELECT date_fin, actif FROM members WHERE code=:c", c=code)
                conn.close()
                if rows:
                    date_fin, actif = rows[0]
                    if not actif:
                        return redirect(url_for("accueil_expire"))
                    if date_fin and date_fin <= datetime.now():
                        if f.__name__ != "offres":
                            return redirect(url_for("accueil_expire"))
            except: pass
        return f(*args, **kwargs)
    return decorated

# ── ROUTES ───────────────────────────────────────────────────────────────────


@app.route("/vip")
def vip_landing():
    return send_from_directory("static/vip", "index.html")


@app.route("/support", methods=["GET", "POST"])
@login_required
def support():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    
    if request.method == "POST":
        data = request.get_json()
        sujet   = data.get("sujet", "").strip()
        message = data.get("message", "").strip()
        if not sujet or not message:
            return jsonify({"ok": False, "error": "Champs manquants"})
        
        # Notif Telegram à l'équipe
        notif = (
            f"💬 *NOUVEAU MESSAGE SUPPORT*\n\n"
            f"👤 *{member['nom']}* | Code : `{code}`\n"
            f"💰 Capital : *{member['capital']}*\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
            f"📋 *Sujet :* {sujet}\n\n"
            f"💬 *Message :*\n{message}"
        )
        send_telegram(notif)
        
        # Sauvegarder en DB
        try:
            conn = get_conn()
            rows = conn.run("SELECT historique FROM members WHERE code=:c", c=code)
            hist = json.loads(rows[0][0]) if rows and rows[0][0] else []
            hist.append({
                "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "type": "support",
                "statut": "en_attente",
                "sujet": sujet,
                "message": message
            })
            conn.run("UPDATE members SET historique=:h WHERE code=:c", h=json.dumps(hist[-50:]), c=code)
            conn.close()
        except: pass
        
        return jsonify({"ok": True})
    
    # GET — afficher la page support
    hist = member.get("historique") or []
    messages_support = [h for h in reversed(hist) if h.get("type") == "support"][-10:]
    return render_template("support.html", member=member, messages=messages_support)

@app.route("/faq")
@login_required
def faq():
        code = session["member_code"]
        member = get_member(code)
        return render_template("faq.html", member=member,
            demo_mode=(code == "BCT-DEMO2026"))

@app.route("/parrainage")
@login_required
def parrainage():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    # Stats parrainage
    try:
        conn = get_conn()
        rows = conn.run("SELECT COUNT(*), COALESCE(SUM(filleuls_count)*50,0) FROM members WHERE parrain_code=:c AND actif=TRUE", c=code)
        total_filleuls = rows[0][0] if rows else 0
        gains = rows[0][1] if rows else 0
        conn.close()
        niveau = "Standard"
        if total_filleuls >= 20: niveau = "ELITE 🐐"
        elif total_filleuls >= 10: niveau = "Ambassador"
        elif total_filleuls >= 5: niveau = "Bronze"
        parrain_stats = {"total": total_filleuls, "gains": gains, "niveau": niveau}
    except:
        parrain_stats = {"total": 0, "gains": 0, "niveau": "Standard"}
    return render_template("parrainage.html", member=member, parrain_stats=parrain_stats)

@app.route("/rejoindre/<parrain_code>")
def rejoindre(parrain_code):
    """Landing page parrainage — accessible sans connexion"""
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c AND actif=TRUE", c=parrain_code.upper())
        conn.close()
        if not rows:
            return redirect(url_for("login"))
        parrain_nom = rows[0][0].split()[0]  # Prénom seulement
    except:
        parrain_nom = "un membre"
    return render_template("rejoindre.html",
        parrain_code=parrain_code.upper(),
        parrain_nom=parrain_nom)


@app.route("/rejoindre/<parrain_code>/submit", methods=["POST"])
def rejoindre_submit(parrain_code):
    """Reçoit le formulaire prospect et notifie l'équipe sur Telegram"""
    data      = request.get_json()
    prenom    = data.get("prenom","").strip()
    nom       = data.get("nom","").strip()
    email     = data.get("email","").strip()
    telephone = data.get("telephone","").strip()
    offre     = data.get("offre","").strip()

    if not all([prenom, nom, email, telephone]):
        return jsonify({"ok": False, "error": "Champs manquants"})

    nom_complet = f"{prenom} {nom}"

    # Récupérer le parrain
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c AND actif=TRUE", c=parrain_code.upper())
        conn.close()
        parrain_nom = rows[0][0] if rows else "Inconnu"
    except:
        parrain_nom = "Inconnu"

    # Stocker le prospect temporairement dans l'historique
    prospect_key = f"PROSPECT_{parrain_code}_{prenom}_{nom}".replace(" ","_")

    # Notif Telegram avec boutons Activer / Pas payé
    activer_url = f"https://bectanse-auto.up.railway.app/activer-prospect?nom={nom_complet.replace(' ','%20')}&email={email}&tel={telephone}&offre={offre.replace(' ','%20')}&parrain={parrain_code}&key={ADMIN_KEY}"
    notif = (
        f"💰 *NOUVEAU PROSPECT — À VÉRIFIER SUR STRIPE*\n\n"
        f"👤 *{nom_complet}*\n"
        f"📧 `{email}`\n"
        f"📱 `{telephone}`\n"
        f"💰 Offre : *{offre}*\n"
        f"🎁 Parrainé par : *{parrain_nom}* (`{parrain_code}`)\n\n"
        f"👉 Vérifie le paiement sur Stripe puis clique ✅"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Paiement confirmé — Activer", "url": activer_url},
        {"text": "❌ Pas payé", "callback_data": f"prospect_nopay_{prospect_key}"}
    ]]}
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": notif, "parse_mode": "Markdown",
                  "reply_markup": markup}, timeout=5)
    except: pass

    return jsonify({"ok": True})


@app.route("/activer-prospect")
def activer_prospect():
    """Admin clique depuis Telegram pour activer un prospect après vérification Stripe"""
    if request.args.get("key","") != ADMIN_KEY:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403

    nom_complet = request.args.get("nom","")
    email       = request.args.get("email","")
    telephone   = request.args.get("tel","")
    offre       = request.args.get("offre","")
    parrain_code= request.args.get("parrain","").upper()

    # Créer le membre
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    try:
        conn = get_conn()
        conn.run(
            "INSERT INTO members (code,nom,capital,email,telephone,parrain_code,params,historique) VALUES (:c,:n,:cap,:e,:t,:pr,:p,:h)",
            c=code, n=nom_complet, cap=offre, e=email, t=telephone,
            pr=parrain_code, p=json.dumps({**default_params(), "mt_login": mt_login, "mt_password": mt_pass, "serveur": serveur, "plateforme": plateforme}), h=json.dumps([])
        )

        # Créditer le parrain
        if parrain_code:
            p_rows = conn.run("SELECT nom, filleuls_count, gains_parrainage FROM members WHERE code=:c AND actif=TRUE", c=parrain_code)
            if p_rows:
                p_nom   = p_rows[0][0]
                p_fill  = (p_rows[0][1] or 0) + 1
                p_gains = (p_rows[0][2] or 0) + 50
                conn.run("UPDATE members SET filleuls_count=:f, gains_parrainage=:g WHERE code=:c",
                         f=p_fill, g=p_gains, c=parrain_code)

                # Notif au parrain
                palier_msg = ""
                if p_fill == 5:  palier_msg = "\n\n🥉 *PALIER BRONZE ATTEINT ! +250€ bonus versé sous 10 jours !*"
                if p_fill == 10: palier_msg = "\n\n🥈 *STATUT AMBASSADOR ! +1000€ bonus versé sous 10 jours !*"
                if p_fill == 20: palier_msg = "\n\n🥇 *STATUT ELITE ATTEINT ! +2000€ + Voyage Dubai ! On te contacte très vite !*"

                send_telegram(
                    f"🎉 *Nouveau filleul activé !*\n\n"
                    f"👤 {p_nom} — *{nom_complet}* a rejoint Bectanse AUTO !\n"
                    f"💰 +50€ à percevoir\n"
                    f"📊 Total filleuls : *{p_fill}* | Gains cumulés : *{p_gains}€*"
                    + palier_msg
                )

        conn.close()

        # Notif admin confirmation
        set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
        send_telegram(
            f"✅ *Membre activé !*\n\n"
            f"👤 *{nom_complet}*\n"
            f"🔑 Code : `{code}`\n"
            f"💰 Offre : *{offre}*\n"
            f"📧 {email} | 📱 {telephone}",
            reply_markup={"inline_keyboard": [[{"text": "📅 Définir les dates", "url": set_dates_url}]]}
        )

        return f"""<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center;'>
            <h1 style='color:#059669;font-size:48px;'>✅</h1>
            <h2 style='color:#fff;margin-bottom:12px;'>{nom_complet} activé !</h2>
            <p style='color:#6B7280;'>Code : <strong style='color:#F59E0B;font-size:20px;'>{code}</strong></p>
            <p style='color:#6B7280;margin-top:8px;'>Envoie ce code au membre.</p>
            <p style='color:#6B7280;margin-top:8px;'>Parrain crédité de +50€</p>
            </body></html>"""
    except Exception as e:
        return f"<h2 style='padding:40px'>Erreur: {e}</h2>"


@app.route("/save-paiement", methods=["POST"])
@login_required
def save_paiement():
    """Sauvegarde les infos de paiement du membre pour recevoir ses commissions"""
    code = session["member_code"]
    data = request.get_json()
    ptype = data.get("type","")
    try:
        conn = get_conn()
        if ptype == "virement":
            conn.run("""UPDATE members SET paiement_type=:t, paiement_iban=:i,
                       paiement_bic=:b, paiement_titulaire=:ti WHERE code=:c""",
                     t="virement", i=data.get("iban",""), b=data.get("bic",""),
                     ti=data.get("titulaire",""), c=code)
        elif ptype == "crypto":
            conn.run("""UPDATE members SET paiement_type=:t, paiement_crypto_reseau=:r,
                       paiement_crypto_adresse=:a WHERE code=:c""",
                     t="crypto", r=data.get("reseau",""), a=data.get("adresse",""), c=code)
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/sw.js")
def service_worker():
    return app.send_static_file("sw.js"), 200, {"Content-Type": "application/javascript"}

@app.route("/formation")
@login_required
def formation():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    demo_mode = (code == "BCT-DEMO2026")
    # En mode demo : ne pas envoyer les vraies URLs au client
    if demo_mode:
        return render_template("formation.html", member=member,
            demo_mode=True,
            pdfs=[],
            videos=[])
    return render_template("formation.html", member=member, demo_mode=False)


@app.route("/expire")
def accueil_expire():
    """Page d'accès bloqué — abonnement expiré."""
    if "member_code" not in session:
        return redirect(url_for("login"))
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    # Si pas expiré → rediriger vers accueil
    try:
        from datetime import datetime
        conn = get_conn()
        rows = conn.run("SELECT date_fin, actif FROM members WHERE code=:c", c=code)
        conn.close()
        if rows:
            date_fin, actif = rows[0]
        else:
            date_fin, actif = None, False
        if actif and date_fin and (date_fin - datetime.now()).days >= 0:
            return redirect(url_for("accueil"))
    except: pass
    return render_template("expire.html", member=member)

@app.route("/accueil")
@login_required
def accueil():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    raw_params = member.get("params") or {}
    # Fusionner avec les defaults pour les clés manquantes
    dp = default_params()
    dp.update(raw_params)
    params = dp
    copy_actif = member.get("copy_actif", True)
    date_souscription = member.get("date_souscription")
    date_fin = member.get("date_fin")
    jours_restants = None
    statut_abo = "actif"
    if date_fin:
        from datetime import datetime
        now = datetime.now()
        if hasattr(date_fin, 'year'):
            delta = date_fin - now
            jours_restants = max(0, delta.days)
            if jours_restants == 0: statut_abo = "expiré"
            elif jours_restants <= 7: statut_abo = "expire_bientot"
    notif_type    = member.get("notif_type", "") or ""
    notif_message = member.get("notif_message", "") or ""
    notif_lue     = member.get("notif_lue", True)
    afficher_notif = bool(notif_type and notif_message and not notif_lue)
    demo_mode = (code == "BCT-DEMO2026")
    # Direction artistique fintech validée : la logique et les données restent
    # identiques, seule la présentation de l'espace membre est modernisée.
    modern_preview = True
    return render_template("accueil.html",
        member=member, params=params,
        copy_actif=copy_actif,
        date_souscription=date_souscription,
        date_fin=date_fin,
        jours_restants=jours_restants,
        statut_abo=statut_abo,
        notif_type=notif_type,
        notif_message=notif_message,
        afficher_notif=afficher_notif,
        demo_mode=demo_mode,
        modern_preview=modern_preview
    )


@app.route("/preview-espace-membre")
def preview_espace_membre():
    """Aperçu isolé disponible uniquement depuis la machine locale."""
    if request.host.split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
        return "Aperçu local uniquement", 404
    member = {
        "code": "BCT-PREVIEW",
        "nom": "Malcom Dides",
        "capital": "200",
        "copy_actif": True,
        "notif_type": "message",
        "notif_message": "Tes paramètres sont synchronisés avec Bectanse AUTO.",
        "notif_lue": False,
    }
    return render_template(
        "accueil.html",
        member=member,
        params=default_params(),
        copy_actif=True,
        date_souscription=datetime.now(),
        date_fin=datetime.now() + timedelta(days=25),
        jours_restants=25,
        statut_abo="actif",
        notif_type="message",
        notif_message=member["notif_message"],
        afficher_notif=True,
        demo_mode=True,
        modern_preview=True,
    )


@app.route("/api/eco")
def api_eco():
    """Proxy backend vers Forex Factory calendar — évite les erreurs CORS côté client."""
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
        data = r.json()
        return jsonify(data)
    except Exception as e:
        return jsonify([]), 200


@app.route("/notif/lue", methods=["POST"])
def notif_lue():
    """Marque la notification du membre connecté comme lue."""
    if "member_code" not in session:
        return jsonify({"error": "non connecté"}), 401
    code = session["member_code"]
    try:
        conn = get_conn()
        conn.run(
            "UPDATE membres SET notif_lue=TRUE WHERE code=:code",
            code=code
        )
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True}), 200


# ══════════════════════════════════════════════════════════════
# PAGE ADMIN COMPLÈTE
# ══════════════════════════════════════════════════════════════

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Admin-Key") or request.args.get("key") or request.json.get("key","") if request.is_json else request.form.get("key","") or request.args.get("key","")
        if key != ADMIN_KEY:
            return jsonify({"ok": False, "error": "Non autorisé"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route("/admin-panel")
def admin_panel():
    key = request.args.get("key","")
    if key != ADMIN_KEY:
        return render_template("admin_login.html")
    return render_template("admin_panel.html", admin_key=ADMIN_KEY)

@app.route("/admin-panel/login", methods=["POST"])
def admin_panel_login():
    key = request.form.get("key","")
    if key == ADMIN_KEY:
        return redirect(f"/admin-panel?key={ADMIN_KEY}")
    return render_template("admin_login.html", error="Clé incorrecte")


@app.route("/admin/telegram-automation")
def admin_telegram_automation():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return redirect("/admin-panel")
    return render_template(
        "admin_telegram_automation.html",
        admin_key=ADMIN_KEY,
        preview_mode=False
    )


@app.route("/preview-admin-telegram")
def preview_admin_telegram():
    """Aperçu visuel local, sans base de données ni envoi Telegram."""
    if request.host.split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
        return "Aperçu local uniquement", 404
    return render_template(
        "admin_telegram_automation.html",
        admin_key="preview",
        preview_mode=True
    )

# ── MEMBRES ──
@app.route("/admin/api/membres", methods=["GET"])
def admin_api_membres():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        enforce_member_access_state()
        conn = get_conn()
        rows = conn.run("SELECT code,nom,capital,actif,copy_actif,date_fin,email,telephone,telegram,parrain_code,filleuls_count,gains_parrainage,created_at FROM members ORDER BY created_at DESC")
        cols = ["code","nom","capital","actif","copy_actif","date_fin","email","telephone","telegram","parrain_code","filleuls_count","gains_parrainage","created_at"]
        membres = []
        from datetime import datetime, timedelta
        maintenant = datetime.now()
        for r in rows:
            m = dict(zip(cols, r))
            m["est_expire"] = bool(m["date_fin"] and m["date_fin"] <= maintenant)
            m["nouveau_7j"] = bool(m["created_at"] and m["created_at"] > maintenant - timedelta(days=7))
            if m["date_fin"] and hasattr(m["date_fin"],"strftime"):
                delta = m["date_fin"] - maintenant
                m["jours_restants"] = max(0, delta.days)
                m["date_fin"] = m["date_fin"].strftime("%d/%m/%Y")
            else:
                m["jours_restants"] = 0
            m.pop("created_at", None)
            membres.append(m)
        conn.close()
        return jsonify({"ok":True,"membres":membres})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/membre/update", methods=["POST"])
def admin_api_membre_update():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        data = request.json
        code = (data.get("code") or "").strip()
        if not code:
            return jsonify({"ok":False,"error":"Code membre manquant"}), 400
        conn = get_conn()
        membre = conn.run("SELECT date_fin, actif FROM members WHERE code=:c", c=code)
        if not membre:
            conn.close()
            return jsonify({"ok":False,"error":"Membre introuvable"}), 404
        if "capital" in data:
            conn.run("UPDATE members SET capital=:v WHERE code=:c", v=data["capital"], c=code)
        if "actif" in data:
            if not isinstance(data["actif"], bool):
                conn.close()
                return jsonify({"ok":False,"error":"Statut invalide"}), 400
            if data["actif"]:
                conn.run("""UPDATE members SET actif=TRUE WHERE code=:c
                    AND (date_fin IS NULL OR date_fin > NOW())""", c=code)
            else:
                conn.run("UPDATE members SET actif=FALSE, copy_actif=FALSE WHERE code=:c", c=code)
        if "copy_actif" in data:
            if bool(data["copy_actif"]):
                conn.run("""UPDATE members SET copy_actif=TRUE WHERE code=:c
                    AND actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""", c=code)
            else:
                conn.run("UPDATE members SET copy_actif=FALSE WHERE code=:c", c=code)
        if "jours" in data:
            from datetime import datetime, timedelta
            try:
                jours = int(data["jours"])
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"ok":False,"error":"Nombre de jours invalide"}), 400
            if jours == 0 or abs(jours) > 3650:
                conn.close()
                return jsonify({"ok":False,"error":"Indique un ajustement entre -3650 et +3650 jours"}), 400
            date_fin = membre[0][0] if membre else datetime.now()
            if date_fin and date_fin > datetime.now():
                nouvelle = date_fin + timedelta(days=jours)
            else:
                nouvelle = datetime.now() + timedelta(days=jours)
            conn.run("UPDATE members SET date_fin=:df WHERE code=:c", df=nouvelle, c=code)
        conn.close()
        enforce_member_access_state()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/membre/supprimer", methods=["POST"])
def admin_api_membre_supprimer():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        code = request.json.get("code")
        conn = get_conn()
        conn.run("DELETE FROM members WHERE code=:c", c=code)
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── NOTIFICATIONS ──
@app.route("/admin/api/notif/globale", methods=["POST"])
def admin_api_notif_globale():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        notif_type = request.json.get("type","message")
        contenu = request.json.get("contenu","")
        conn = get_conn()
        rows = conn.run("""SELECT COUNT(*) FROM members
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")
        total = rows[0][0]
        conn.run("""UPDATE members SET notif_type=:t, notif_message=:m, notif_lue=FALSE
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""",
            t=notif_type, m=contenu)
        conn.close()
        return jsonify({"ok":True,"total":total})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/notif/individuelle", methods=["POST"])
def admin_api_notif_individuelle():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        code = request.json.get("code")
        contenu = request.json.get("contenu","")
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c AND actif=TRUE", c=code)
        if not rows:
            conn.close()
            return jsonify({"ok":False,"error":"Membre introuvable"})
        conn.run("UPDATE members SET notif_type='individuelle', notif_message=:m, notif_lue=FALSE WHERE code=:c", m=contenu, c=code)
        conn.close()
        return jsonify({"ok":True,"nom":rows[0][0]})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── ANNONCES ──
@app.route("/admin/api/annonces", methods=["GET"])
def admin_api_annonces():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT id,titre,contenu,type,audio_url,cible,cible_code,actif,created_at FROM annonces ORDER BY created_at DESC")
        cols = ["id","titre","contenu","type","audio_url","cible","cible_code","actif","created_at"]
        annonces = []
        for r in rows:
            a = dict(zip(cols, r))
            if a["created_at"]: a["created_at"] = a["created_at"].strftime("%d/%m/%Y %H:%M")
            annonces.append(a)
        conn.close()
        return jsonify({"ok":True,"annonces":annonces})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/annonces/add", methods=["POST"])
def admin_api_annonces_add():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        d = request.json
        conn = get_conn()
        conn.run("INSERT INTO annonces (titre,contenu,type,audio_url,cible,cible_code) VALUES (:ti,:co,:ty,:au,:ci,:cc)",
            ti=d.get("titre",""), co=d.get("contenu",""), ty=d.get("type","message"),
            au=d.get("audio_url",""), ci=d.get("cible","tous"), cc=d.get("cible_code",""))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/annonces/update", methods=["POST"])
def admin_api_annonces_update():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        d = request.json
        conn = get_conn()
        conn.run("UPDATE annonces SET titre=:ti,contenu=:co,type=:ty,audio_url=:au,actif=:ac WHERE id=:id",
            ti=d.get("titre",""), co=d.get("contenu",""), ty=d.get("type","message"),
            au=d.get("audio_url",""), ac=d.get("actif",True), id=d.get("id"))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/annonces/supprimer", methods=["POST"])
def admin_api_annonces_supprimer():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        conn.run("DELETE FROM annonces WHERE id=:id", id=request.json.get("id"))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── FORMATION ──
@app.route("/admin/api/formation/pdfs", methods=["GET"])
def admin_api_formation_pdfs():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT id,num,titre,drive_id,taille,ordre FROM formation_pdfs ORDER BY ordre,num")
        cols = ["id","num","titre","drive_id","taille","ordre"]
        pdfs = [dict(zip(cols,r)) for r in rows]
        conn.close()
        return jsonify({"ok":True,"pdfs":pdfs})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/formation/pdfs/add", methods=["POST"])
def admin_api_formation_pdfs_add():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        d = request.json
        conn = get_conn()
        conn.run("INSERT INTO formation_pdfs (num,titre,drive_id,taille,ordre) VALUES (:n,:t,:d,:ta,:o)",
            n=d.get("num",0), t=d.get("titre",""), d=d.get("drive_id",""),
            ta=d.get("taille",""), o=d.get("ordre",0))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/formation/pdfs/update", methods=["POST"])
def admin_api_formation_pdfs_update():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        d = request.json
        conn = get_conn()
        conn.run("UPDATE formation_pdfs SET num=:n,titre=:t,drive_id=:d,taille=:ta,ordre=:o WHERE id=:id",
            n=d.get("num",0), t=d.get("titre",""), d=d.get("drive_id",""),
            ta=d.get("taille",""), o=d.get("ordre",0), id=d.get("id"))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/formation/pdfs/supprimer", methods=["POST"])
def admin_api_formation_pdfs_supprimer():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        conn.run("DELETE FROM formation_pdfs WHERE id=:id", id=request.json.get("id"))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/formation/videos/add", methods=["POST"])
def admin_api_formation_videos_add():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        d = request.json
        conn = get_conn()
        conn.run("INSERT INTO formation_videos (num,titre,youtube_id,duree,ordre) VALUES (:n,:t,:y,:du,:o)",
            n=d.get("num",0), t=d.get("titre",""), y=d.get("youtube_id",""),
            du=d.get("duree",""), o=d.get("ordre",0))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/formation/videos/supprimer", methods=["POST"])
def admin_api_formation_videos_supprimer():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        conn.run("DELETE FROM formation_videos WHERE id=:id", id=request.json.get("id"))
        conn.close()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── TELEGRAM ADMIN ──
@app.route("/admin/api/telegram/envoyer", methods=["POST"])
def admin_api_telegram_envoyer():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        canal = request.json.get("canal", "@BECTANSE_ACADEMIE")
        message = request.json.get("message","").strip()
        sent = _send_scheduled_telegram(
            message,
            slot_key=f"telegram-quick-{_paris_now().strftime('%Y%m%d-%H%M%S-%f')}",
            post_kind="manual-editorial",
            channel=canal
        )
        if not sent:
            return jsonify({"ok":False,"error":"Telegram n’a pas confirmé l’envoi"}), 502
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


def _serialize_telegram_post(post):
    serialized = dict(post)
    if serialized.get("scheduled_for"):
        serialized["scheduled_for"] = _parse_scheduled_datetime(
            serialized["scheduled_for"]
        ).isoformat()
    for field in ("last_sent_at", "created_at", "updated_at"):
        value = serialized.get(field)
        if value and hasattr(value, "isoformat"):
            serialized[field] = value.isoformat()
    serialized["weekdays"] = [
        int(day) for day in str(serialized.get("weekdays") or "").split(",") if day != ""
    ]
    for field in ("poll_options", "poll_correct_option_ids"):
        value = serialized.get(field)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = []
        serialized[field] = value if isinstance(value, list) else []
    serialized["next_run"] = next_run_for_telegram_post(post)
    return serialized


def _payload_list(value, separator="|"):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            pass
    return [item.strip() for item in text.split(separator) if item.strip()]


def _validate_telegram_post_payload(data):
    name = str(data.get("name") or "").strip()
    message = str(data.get("message") or "").strip()
    image_url = str(data.get("image_url") or "").strip()
    post_type = str(data.get("post_type") or "message").strip().lower()
    schedule_type = str(data.get("schedule_type") or "weekly").strip()
    publish_time = str(data.get("publish_time") or "18:30").strip()
    channel = str(data.get("channel") or ECO_CANAL).strip()
    button_text = str(data.get("button_text") or "").strip()
    button_url = str(data.get("button_url") or "").strip()
    poll_question = str(data.get("poll_question") or "").strip()
    poll_options = [str(option).strip() for option in _payload_list(data.get("poll_options"))]
    poll_explanation = str(data.get("poll_explanation") or "").strip()
    publish_all_channels = bool(data.get("publish_all_channels", True))
    raw_channel_ids = data.get("channel_ids") or []
    channel_targets = [str(target).strip() for target in _payload_list(data.get("channel_targets"))]
    if isinstance(raw_channel_ids, str):
        raw_channel_ids = _payload_list(raw_channel_ids)
    try:
        channel_ids = sorted({int(channel_id) for channel_id in raw_channel_ids})
    except (TypeError, ValueError):
        raise ValueError("Sélection de canaux invalide")
    if any(channel_id <= 0 for channel_id in channel_ids):
        raise ValueError("Sélection de canaux invalide")
    if not publish_all_channels and not channel_ids and not channel_targets:
        raise ValueError("Choisis au moins un canal ou active la diffusion sur tous les canaux")

    if not name or len(name) > 120:
        raise ValueError("Le nom interne est obligatoire et limité à 120 caractères")
    if post_type not in {"message", "quiz", "poll"}:
        raise ValueError("Le format doit être message, quiz ou sondage")
    if post_type == "message":
        if not message:
            raise ValueError("Le message Telegram est obligatoire")
        if image_url and not image_url.startswith("https://"):
            raise ValueError("L’image doit utiliser une adresse HTTPS")
        max_length = 1024 if image_url else 4096
        if len(message) > max_length:
            raise ValueError(f"Le message est limité à {max_length} caractères dans ce format")
        poll_question, poll_options, poll_explanation = "", [], ""
        poll_correct_option_ids = []
        poll_anonymous = True
        poll_multiple = False
    else:
        if image_url:
            raise ValueError("Une image séparée n’est pas compatible avec un quiz ou sondage natif")
        if not poll_question or len(poll_question) > 300:
            raise ValueError("La question est obligatoire et limitée à 300 caractères")
        if not 2 <= len(poll_options) <= 12:
            raise ValueError("Ajoute entre 2 et 12 réponses")
        if any(not option or len(option) > 100 for option in poll_options):
            raise ValueError("Chaque réponse est obligatoire et limitée à 100 caractères")
        if len(poll_explanation) > 200 or poll_explanation.count("\n") > 2:
            raise ValueError("L’explication est limitée à 200 caractères et 2 retours à la ligne")
        raw_correct_ids = _payload_list(data.get("poll_correct_option_ids"))
        try:
            poll_correct_option_ids = sorted({int(index) for index in raw_correct_ids})
        except (TypeError, ValueError):
            raise ValueError("Les bonnes réponses du quiz sont invalides")
        if post_type == "quiz" and not poll_correct_option_ids:
            raise ValueError("Choisis au moins une bonne réponse pour le quiz")
        if any(index < 0 or index >= len(poll_options) for index in poll_correct_option_ids):
            raise ValueError("Une bonne réponse ne correspond à aucune option")
        if post_type == "poll":
            poll_correct_option_ids = []
            poll_explanation = ""
        poll_anonymous = bool(data.get("poll_anonymous", True))
        poll_multiple = bool(data.get("poll_multiple", False))
        if len(poll_correct_option_ids) > 1:
            poll_multiple = True
        message = message or poll_question
    if schedule_type not in {"weekly", "rotation", "once"}:
        raise ValueError("Type de programmation invalide")
    if not channel or any(char.isspace() for char in channel):
        raise ValueError("Destination Telegram invalide")
    if bool(button_text) != bool(button_url):
        raise ValueError("Le texte et le lien du bouton doivent être renseignés ensemble")
    if button_url and not button_url.startswith(("https://", "http://")):
        raise ValueError("Le lien du bouton doit commencer par https:// ou http://")

    weekdays_raw = data.get("weekdays", [])
    if isinstance(weekdays_raw, str):
        weekdays_raw = [day for day in weekdays_raw.split(",") if day != ""]
    try:
        weekdays = sorted({int(day) for day in weekdays_raw})
    except (TypeError, ValueError):
        raise ValueError("Jours de publication invalides")
    if any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("Jours de publication invalides")

    rotation_week = data.get("rotation_week")
    scheduled_for = None
    if schedule_type in {"weekly", "rotation"}:
        if not _parse_publish_time(publish_time):
            raise ValueError("Heure de publication invalide")
        if not weekdays:
            raise ValueError("Choisis au moins un jour de publication")
        if schedule_type == "rotation":
            try:
                rotation_week = int(rotation_week)
            except (TypeError, ValueError):
                raise ValueError("Semaine de rotation invalide")
            if rotation_week not in range(4):
                raise ValueError("La semaine de rotation doit être comprise entre 1 et 4")
    else:
        try:
            scheduled_for = datetime.fromisoformat(str(data.get("scheduled_for") or ""))
        except ValueError:
            raise ValueError("Date et heure de l’envoi unique invalides")
        # La colonne PostgreSQL est un TIMESTAMP sans fuseau : on y conserve
        # donc l'heure murale de Paris. Sans cette normalisation, rééditer un
        # post renvoyé par l'API avec son décalage +02:00 le reculait de 2 h.
        scheduled_for = _parse_scheduled_datetime(scheduled_for).replace(tzinfo=None)
        weekdays = []
        rotation_week = None

    return {
        "name": name,
        "message": message,
        "image_url": image_url,
        "post_type": post_type,
        "poll_question": poll_question,
        "poll_options": json.dumps(poll_options, ensure_ascii=False),
        "poll_correct_option_ids": json.dumps(poll_correct_option_ids),
        "poll_explanation": poll_explanation,
        "poll_anonymous": poll_anonymous,
        "poll_multiple": poll_multiple,
        "publish_all_channels": publish_all_channels,
        "channel_ids": channel_ids,
        "channel_targets": channel_targets,
        "schedule_type": schedule_type,
        "weekdays": ",".join(str(day) for day in weekdays),
        "rotation_week": rotation_week,
        "publish_time": publish_time,
        "scheduled_for": scheduled_for,
        "timezone": "Europe/Paris",
        "channel": channel,
        "button_text": button_text,
        "button_url": button_url,
        "disable_notification": bool(data.get("disable_notification", False)),
        "enabled": bool(data.get("enabled", True))
    }


TELEGRAM_CSV_COLUMNS = [
    "nom", "type", "date", "heure", "rythme", "jours", "semaine_rotation",
    "canal", "message", "image_url", "texte_bouton", "lien_bouton",
    "question", "reponses", "bonnes_reponses", "explication", "anonyme",
    "choix_multiples", "silencieux", "actif", "tous_les_canaux", "canaux"
]


def _normalize_csv_label(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.strip().lower().replace(" ", "_").replace("-", "_")


def _csv_boolean(value, default=False):
    text = _normalize_csv_label(value)
    if not text:
        return default
    if text in {"1", "oui", "o", "true", "vrai", "yes", "actif"}:
        return True
    if text in {"0", "non", "n", "false", "faux", "no", "inactif"}:
        return False
    raise ValueError(f"valeur oui/non invalide : {value}")


def _csv_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    for date_format in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError("date invalide, utilise JJ/MM/AAAA")


def _csv_weekdays(value):
    aliases = {
        "0": 0, "lun": 0, "lundi": 0,
        "1": 1, "mar": 1, "mardi": 1,
        "2": 2, "mer": 2, "mercredi": 2,
        "3": 3, "jeu": 3, "jeudi": 3,
        "4": 4, "ven": 4, "vendredi": 4,
        "5": 5, "sam": 5, "samedi": 5,
        "6": 6, "dim": 6, "dimanche": 6,
    }
    tokens = str(value or "").replace(",", "|").split("|")
    days = []
    for token in tokens:
        normalized = _normalize_csv_label(token)
        if not normalized:
            continue
        if normalized not in aliases:
            raise ValueError(f"jour inconnu : {token.strip()}")
        days.append(aliases[normalized])
    return sorted(set(days))


def _csv_row_to_telegram_payload(row, line_number):
    normalized = {_normalize_csv_label(key): value for key, value in row.items() if key}
    post_type = _normalize_csv_label(normalized.get("type") or "message")
    post_type = {"photo": "message", "texte": "message", "sondage": "poll"}.get(
        post_type, post_type
    )
    rhythm = _normalize_csv_label(normalized.get("rythme") or "")
    rhythm = {
        "unique": "once", "ponctuel": "once", "once": "once",
        "hebdomadaire": "weekly", "semaine": "weekly", "weekly": "weekly",
        "rotation": "rotation", "rotation_4_semaines": "rotation"
    }.get(rhythm, rhythm)
    target_date = _csv_date(normalized.get("date"))
    if target_date:
        rhythm = "once"
    rhythm = rhythm or "weekly"
    publish_time = str(normalized.get("heure") or "18:30").strip()
    scheduled_for = ""
    weekdays = []
    rotation_week = 0
    if rhythm == "once":
        if not target_date:
            raise ValueError("une date est obligatoire pour un envoi unique")
        if not _parse_publish_time(publish_time):
            raise ValueError("heure invalide, utilise HH:MM")
        scheduled_for = datetime.combine(target_date, _parse_publish_time(publish_time)).isoformat()
    else:
        weekdays = _csv_weekdays(normalized.get("jours"))
        if rhythm == "rotation":
            try:
                rotation_value = int(str(normalized.get("semaine_rotation") or "1").strip())
            except ValueError:
                raise ValueError("semaine de rotation invalide")
            rotation_week = rotation_value - 1 if 1 <= rotation_value <= 4 else rotation_value

    options = _payload_list(normalized.get("reponses"))
    correct_values = _payload_list(
        str(normalized.get("bonnes_reponses") or "").replace(",", "|")
    )
    try:
        correct_ids = [int(value) - 1 for value in correct_values]
    except ValueError:
        raise ValueError("bonnes réponses invalides, utilise leurs numéros")

    payload = {
        "name": str(normalized.get("nom") or f"Publication ligne {line_number}").strip(),
        "post_type": post_type,
        "message": str(normalized.get("message") or "").strip(),
        "image_url": str(normalized.get("image_url") or "").strip(),
        "poll_question": str(normalized.get("question") or "").strip(),
        "poll_options": options,
        "poll_correct_option_ids": correct_ids,
        "poll_explanation": str(normalized.get("explication") or "").strip(),
        "poll_anonymous": _csv_boolean(normalized.get("anonyme"), True),
        "poll_multiple": _csv_boolean(normalized.get("choix_multiples"), False),
        "schedule_type": rhythm,
        "weekdays": weekdays,
        "rotation_week": rotation_week,
        "publish_time": publish_time,
        "scheduled_for": scheduled_for,
        "channel": str(normalized.get("canal") or ECO_CANAL).strip(),
        "button_text": str(normalized.get("texte_bouton") or "").strip(),
        "button_url": str(normalized.get("lien_bouton") or "").strip(),
        "disable_notification": _csv_boolean(normalized.get("silencieux"), False),
        "enabled": _csv_boolean(normalized.get("actif"), True),
        "publish_all_channels": _csv_boolean(normalized.get("tous_les_canaux"), True),
        "channel_targets": _payload_list(normalized.get("canaux")),
    }
    return _validate_telegram_post_payload(payload)


def _telegram_csv_source_key(values):
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return f"csv-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def _csv_download(rows, filename):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=TELEGRAM_CSV_COLUMNS, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        "\ufeff" + stream.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def _telegram_post_to_csv_row(post):
    serialized = _serialize_telegram_post(post)
    scheduled_for = _parse_scheduled_datetime(post.get("scheduled_for"))
    type_labels = {"message": "message", "quiz": "quiz", "poll": "sondage"}
    rhythm_labels = {"weekly": "hebdomadaire", "rotation": "rotation", "once": "unique"}
    day_labels = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    correct_ids = serialized.get("poll_correct_option_ids") or []
    return {
        "nom": post.get("name") or "",
        "type": type_labels.get(post.get("post_type") or "message", "message"),
        "date": scheduled_for.strftime("%d/%m/%Y") if scheduled_for else "",
        "heure": scheduled_for.strftime("%H:%M") if scheduled_for else post.get("publish_time") or "",
        "rythme": rhythm_labels.get(post.get("schedule_type"), post.get("schedule_type") or ""),
        "jours": "|".join(day_labels[day] for day in serialized.get("weekdays", [])),
        "semaine_rotation": (int(post.get("rotation_week")) + 1) if post.get("rotation_week") is not None else "",
        "canal": post.get("channel") or "",
        "message": post.get("message") if (post.get("post_type") or "message") == "message" else "",
        "image_url": post.get("image_url") or "",
        "texte_bouton": post.get("button_text") or "",
        "lien_bouton": post.get("button_url") or "",
        "question": post.get("poll_question") or "",
        "reponses": "|".join(serialized.get("poll_options") or []),
        "bonnes_reponses": "|".join(str(index + 1) for index in correct_ids),
        "explication": post.get("poll_explanation") or "",
        "anonyme": "oui" if post.get("poll_anonymous", True) else "non",
        "choix_multiples": "oui" if post.get("poll_multiple", False) else "non",
        "silencieux": "oui" if post.get("disable_notification") else "non",
        "actif": "oui" if post.get("enabled") else "non",
        "tous_les_canaux": "oui" if post.get("publish_all_channels", True) else "non",
        "canaux": "|".join(post.get("channel_targets") or []),
    }


def _telegram_csv_request_allowed(key):
    local_preview = (
        key == "preview" and request.host.split(":", 1)[0] in {"127.0.0.1", "localhost"}
    )
    return key == ADMIN_KEY or local_preview


@app.route("/admin/api/telegram/csv/template", methods=["GET"])
def admin_api_telegram_csv_template():
    if not _telegram_csv_request_allowed(request.args.get("key", "")):
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    try:
        with open(TELEGRAM_CSV_TEMPLATE_PATH, "r", encoding="utf-8-sig", newline="") as template:
            content = template.read()
        return Response(
            "\ufeff" + content,
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="modele-planning-telegram-semaine.csv"'}
        )
    except OSError as error:
        return jsonify({"ok": False, "error": str(error)}), 500


@app.route("/admin/api/telegram/csv/export", methods=["GET"])
def admin_api_telegram_csv_export():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, name, message, image_url, schedule_type, weekdays,
                      rotation_week, publish_time, scheduled_for, timezone, channel,
                      button_text, button_url, disable_notification, enabled, source_key,
                      deleted, last_sent_at, created_at, updated_at, post_type,
                      poll_question, poll_options, poll_correct_option_ids,
                      poll_explanation, poll_anonymous, poll_multiple,
                      publish_all_channels
               FROM telegram_scheduled_posts
               WHERE deleted=FALSE ORDER BY id"""
        )
        posts = [_telegram_post_from_row(row) for row in rows]
        target_rows = conn.run(
            """SELECT targets.post_id, channels.chat_id
               FROM telegram_post_channels AS targets
               JOIN telegram_channels AS channels ON channels.id=targets.channel_id
               WHERE channels.deleted=FALSE ORDER BY targets.post_id, channels.id"""
        )
        target_names_by_post = {}
        for post_id, chat_id in target_rows:
            target_names_by_post.setdefault(int(post_id), []).append(chat_id)
        for post in posts:
            post["channel_targets"] = target_names_by_post.get(int(post["id"]), [])
        return _csv_download(
            [_telegram_post_to_csv_row(post) for post in posts],
            f"planning-telegram-{_paris_now().strftime('%Y-%m-%d')}.csv"
        )
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/csv/import", methods=["POST"])
def admin_api_telegram_csv_import():
    key = request.form.get("key", "")
    if not _telegram_csv_request_allowed(key):
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    csv_file = request.files.get("file")
    if not csv_file or not csv_file.filename:
        return jsonify({"ok": False, "error": "Choisis un fichier CSV"}), 400
    raw = csv_file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Le fichier CSV est limité à 2 Mo"}), 400
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Enregistre le CSV au format UTF-8"}), 400
    try:
        delimiter = csv.Sniffer().sniff(decoded[:4096], delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter)
    if not reader.fieldnames:
        return jsonify({"ok": False, "error": "Le CSV ne contient pas d’en-têtes"}), 400

    values_to_import = []
    errors = []
    for line_number, row in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue
        try:
            values = _csv_row_to_telegram_payload(row, line_number)
            values_to_import.append((line_number, values))
        except (TypeError, ValueError) as error:
            errors.append({"line": line_number, "error": str(error)})
    if not values_to_import and not errors:
        errors.append({"line": 1, "error": "Le CSV ne contient aucune publication"})
    if errors:
        return jsonify({
            "ok": False,
            "error": f"{len(errors)} ligne(s) à corriger",
            "errors": errors[:50]
        }), 400

    summary = {
        "total": len(values_to_import),
        "messages": sum(1 for _, values in values_to_import if values["post_type"] == "message"),
        "quizzes": sum(1 for _, values in values_to_import if values["post_type"] == "quiz"),
        "polls": sum(1 for _, values in values_to_import if values["post_type"] == "poll"),
    }
    dry_run = _csv_boolean(request.form.get("dry_run"), True)
    preview_mode = key == "preview"
    if dry_run or preview_mode:
        return jsonify({"ok": True, "summary": summary, "preview_mode": preview_mode})

    conn = None
    imported = 0
    duplicates = 0
    try:
        conn = get_conn()
        for _, values in values_to_import:
            source_key = _telegram_csv_source_key(values)
            db_values = dict(values)
            channel_ids = db_values.pop("channel_ids", [])
            channel_targets = db_values.pop("channel_targets", [])
            if not db_values["publish_all_channels"] and not channel_ids:
                for target in channel_targets:
                    target_rows = conn.run(
                        """SELECT id FROM telegram_channels
                           WHERE LOWER(chat_id)=LOWER(:chat_id) AND deleted=FALSE""",
                        chat_id=target
                    )
                    if not target_rows:
                        raise ValueError(f"Canal CSV introuvable dans l’admin : {target}")
                    channel_ids.append(int(target_rows[0][0]))
            rows = conn.run(
                """INSERT INTO telegram_scheduled_posts
                   (name, message, image_url, post_type, poll_question, poll_options,
                    poll_correct_option_ids, poll_explanation, poll_anonymous,
                    poll_multiple, schedule_type, weekdays, rotation_week,
                    publish_time, scheduled_for, timezone, channel, button_text,
                    button_url, disable_notification, enabled, source_key,
                    publish_all_channels)
                   VALUES (:name, :message, :image_url, :post_type, :poll_question,
                           :poll_options, :poll_correct_option_ids, :poll_explanation,
                           :poll_anonymous, :poll_multiple, :schedule_type, :weekdays,
                           :rotation_week, :publish_time, :scheduled_for, :timezone,
                           :channel, :button_text, :button_url,
                           :disable_notification, :enabled, :source_key,
                           :publish_all_channels)
                   ON CONFLICT (source_key) DO NOTHING RETURNING id""",
                source_key=source_key, **db_values
            )
            if rows:
                imported += 1
                post_id = int(rows[0][0])
                for channel_id in channel_ids:
                    conn.run(
                        """INSERT INTO telegram_post_channels (post_id, channel_id)
                           SELECT :post_id, id FROM telegram_channels
                           WHERE id=:channel_id AND deleted=FALSE
                           ON CONFLICT DO NOTHING""",
                        post_id=post_id, channel_id=channel_id
                    )
            else:
                duplicates += 1
        return jsonify({
            "ok": True, "summary": summary,
            "imported": imported, "duplicates": duplicates
        })
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


def _validate_telegram_channel_payload(data):
    name = str(data.get("name") or "").strip()
    chat_id = str(data.get("chat_id") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Le nom du canal est obligatoire et limité à 80 caractères")
    if not chat_id or any(char.isspace() for char in chat_id):
        raise ValueError("L’identifiant Telegram du canal est invalide")
    if not chat_id.startswith("@"):
        try:
            int(chat_id)
        except ValueError:
            raise ValueError("Utilise @nom_du_canal ou son identifiant numérique")
    return {"name": name, "chat_id": chat_id, "active": bool(data.get("active", True))}


def _validate_telegram_media_payload(data):
    title = str(data.get("title") or "").strip()
    image_url = str(data.get("image_url") or "").strip()
    category = str(data.get("category") or "personal").strip().lower()
    caption = str(data.get("caption") or "").strip()
    cta_text = str(data.get("cta_text") or "").strip()
    cta_url = str(data.get("cta_url") or "").strip()
    if not title or len(title) > 100:
        raise ValueError("Le nom du visuel est obligatoire et limité à 100 caractères")
    if not image_url.startswith(("https://", "http://")):
        raise ValueError("L’adresse du visuel doit commencer par https:// ou http://")
    if category not in {"personal", "conversion", "market", "community"}:
        raise ValueError("Catégorie de visuel invalide")
    if len(caption) > 1024:
        raise ValueError("La légende du visuel est limitée à 1 024 caractères")
    if len(cta_text) > 64:
        raise ValueError("Le texte du CTA est limité à 64 caractères")
    if bool(cta_text) != bool(cta_url):
        raise ValueError("Le texte et le lien du CTA doivent être renseignés ensemble")
    if cta_url and not cta_url.startswith(("https://", "http://")):
        raise ValueError("Le lien du CTA doit commencer par https:// ou http://")
    return {
        "title": title, "image_url": image_url, "category": category,
        "caption": caption, "cta_text": cta_text, "cta_url": cta_url,
    }


@app.route("/admin/api/telegram/media-library", methods=["GET"])
def admin_api_telegram_media_library():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, title, image_url, category, caption, cta_text,
                      cta_url, source_type, created_at, updated_at
               FROM telegram_media_library WHERE deleted=FALSE
               ORDER BY updated_at DESC, id DESC"""
        )
        media = []
        for row in rows:
            item = dict(zip([
                "id", "title", "image_url", "category", "caption", "cta_text",
                "cta_url", "source_type", "created_at", "updated_at"
            ], row))
            for field in ("created_at", "updated_at"):
                if item.get(field) and hasattr(item[field], "isoformat"):
                    item[field] = item[field].isoformat()
            media.append(item)
        return jsonify({"ok": True, "media": media})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/media-library/save", methods=["POST"])
def admin_api_telegram_media_library_save():
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        values = _validate_telegram_media_payload(data)
        media_id = data.get("id")
        conn = get_conn()
        if media_id:
            conn.run(
                """UPDATE telegram_media_library SET title=:title,
                          image_url=:image_url, category=:category, caption=:caption,
                          cta_text=:cta_text, cta_url=:cta_url, deleted=FALSE,
                          updated_at=NOW() WHERE id=:id""",
                id=int(media_id), **values
            )
        else:
            rows = conn.run(
                """INSERT INTO telegram_media_library
                   (title, image_url, category, caption, cta_text, cta_url, source_type, deleted)
                   VALUES (:title, :image_url, :category, :caption, :cta_text, :cta_url, 'custom', FALSE)
                   ON CONFLICT (image_url) DO UPDATE SET
                     title=:title, category=:category, caption=:caption,
                     cta_text=:cta_text, cta_url=:cta_url, deleted=FALSE, updated_at=NOW()
                   RETURNING id""",
                **values
            )
            media_id = rows[0][0]
        return jsonify({"ok": True, "id": int(media_id)})
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/media-library/<int:media_id>/delete", methods=["POST"])
def admin_api_telegram_media_library_delete(media_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE telegram_media_library SET deleted=TRUE, updated_at=NOW()
               WHERE id=:id AND source_type='custom'""",
            id=media_id
        )
        return jsonify({"ok": True})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/channels", methods=["GET"])
def admin_api_telegram_channels():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, name, chat_id, active, last_check_status,
                      last_check_at, created_at, updated_at
               FROM telegram_channels WHERE deleted=FALSE
               ORDER BY active DESC, id"""
        )
        channels = []
        for row in rows:
            channel = dict(zip([
                "id", "name", "chat_id", "active", "last_check_status",
                "last_check_at", "created_at", "updated_at"
            ], row))
            for field in ("last_check_at", "created_at", "updated_at"):
                if channel.get(field) and hasattr(channel[field], "isoformat"):
                    channel[field] = channel[field].isoformat()
            channels.append(channel)
        return jsonify({"ok": True, "channels": channels})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/channels/save", methods=["POST"])
def admin_api_telegram_channel_save():
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        values = _validate_telegram_channel_payload(data)
        channel_id = data.get("id")
        conn = get_conn()
        if channel_id:
            conn.run(
                """UPDATE telegram_channels SET name=:name, chat_id=:chat_id,
                          active=:active, deleted=FALSE, updated_at=NOW()
                   WHERE id=:id AND deleted=FALSE""",
                id=int(channel_id), **values
            )
        else:
            rows = conn.run(
                """INSERT INTO telegram_channels (name, chat_id, active, deleted)
                   VALUES (:name, :chat_id, :active, FALSE)
                   ON CONFLICT (chat_id) DO UPDATE SET
                       name=:name, active=:active, deleted=FALSE, updated_at=NOW()
                   RETURNING id""",
                **values
            )
            channel_id = rows[0][0]
        return jsonify({"ok": True, "id": int(channel_id)})
    except (TypeError, ValueError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/channels/<int:channel_id>/toggle", methods=["POST"])
def admin_api_telegram_channel_toggle(channel_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE telegram_channels SET active=:active, updated_at=NOW()
               WHERE id=:id AND deleted=FALSE""",
            id=channel_id, active=bool(data.get("active"))
        )
        return jsonify({"ok": True})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/channels/<int:channel_id>/delete", methods=["POST"])
def admin_api_telegram_channel_delete(channel_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE telegram_channels SET active=FALSE, deleted=TRUE, updated_at=NOW()
               WHERE id=:id""",
            id=channel_id
        )
        return jsonify({"ok": True})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/channels/<int:channel_id>/test", methods=["POST"])
def admin_api_telegram_channel_test(channel_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    status = "error"
    detail = "Test impossible"
    try:
        conn = get_conn()
        rows = conn.run(
            "SELECT chat_id FROM telegram_channels WHERE id=:id AND deleted=FALSE",
            id=channel_id
        )
        if not rows:
            return jsonify({"ok": False, "error": "Canal introuvable"}), 404
        chat_id = rows[0][0]
        me_response = requests.get(
            f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/getMe", timeout=15
        ).json()
        if not me_response.get("ok"):
            raise ValueError(me_response.get("description", "Bot Telegram indisponible"))
        bot_id = me_response["result"]["id"]
        member_response = requests.get(
            f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": bot_id}, timeout=15
        ).json()
        if not member_response.get("ok"):
            raise ValueError(member_response.get("description", "Canal inaccessible"))
        member = member_response.get("result", {})
        role = member.get("status")
        can_publish = role == "creator" or (
            role == "administrator" and member.get("can_post_messages", True)
        )
        status = "ready" if can_publish else "permission_missing"
        detail = "Robot prêt à publier" if can_publish else "Autorisation de publication manquante"
        conn.run(
            """UPDATE telegram_channels SET last_check_status=:status,
                      last_check_at=NOW(), updated_at=NOW() WHERE id=:id""",
            id=channel_id, status=status
        )
        return jsonify({"ok": True, "status": status, "detail": detail})
    except (ValueError, requests.RequestException) as error:
        detail = str(error)
        if conn:
            try:
                conn.run(
                    """UPDATE telegram_channels SET last_check_status='error',
                              last_check_at=NOW(), updated_at=NOW() WHERE id=:id""",
                    id=channel_id
                )
            except Exception:
                pass
        return jsonify({"ok": False, "error": detail}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/posts", methods=["GET"])
def admin_api_telegram_posts():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, name, message, image_url, schedule_type, weekdays,
                      rotation_week, publish_time, scheduled_for, timezone, channel,
                      button_text, button_url, disable_notification, enabled, source_key,
                      deleted, last_sent_at, created_at, updated_at, post_type,
                      poll_question, poll_options, poll_correct_option_ids,
                      poll_explanation, poll_anonymous, poll_multiple,
                      publish_all_channels
               FROM telegram_scheduled_posts
               WHERE deleted=FALSE ORDER BY enabled DESC, id DESC"""
        )
        posts = [_serialize_telegram_post(_telegram_post_from_row(row)) for row in rows]
        target_rows = conn.run(
            """SELECT targets.post_id, targets.channel_id
               FROM telegram_post_channels AS targets
               JOIN telegram_scheduled_posts AS posts ON posts.id=targets.post_id
               WHERE posts.deleted=FALSE"""
        )
        channel_ids_by_post = {}
        for post_id, channel_id in target_rows:
            channel_ids_by_post.setdefault(int(post_id), []).append(int(channel_id))
        for post in posts:
            post["channel_ids"] = channel_ids_by_post.get(int(post["id"]), [])
        return jsonify({
            "ok": True,
            "posts": posts,
            "stats": {
                "total": len(posts),
                "active": sum(1 for post in posts if post["enabled"]),
                "with_image": sum(1 for post in posts if post.get("image_url")),
                "interactive": sum(
                    1 for post in posts if post.get("post_type") in {"quiz", "poll"}
                ),
                "scheduled": sum(1 for post in posts if post.get("next_run"))
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/posts/save", methods=["POST"])
def admin_api_telegram_posts_save():
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        values = _validate_telegram_post_payload(data)
        channel_ids = values.pop("channel_ids", [])
        values.pop("channel_targets", None)
        post_id = data.get("id")
        conn = get_conn()
        for channel_id in channel_ids:
            if not conn.run(
                "SELECT id FROM telegram_channels WHERE id=:id AND deleted=FALSE",
                id=channel_id
            ):
                raise ValueError("Un canal sélectionné n’existe plus")
        if post_id:
            rows = conn.run(
                "SELECT id FROM telegram_scheduled_posts WHERE id=:id AND deleted=FALSE",
                id=int(post_id)
            )
            if not rows:
                return jsonify({"ok": False, "error": "Publication introuvable"}), 404
            conn.run(
                """UPDATE telegram_scheduled_posts SET
                   name=:name, message=:message, image_url=:image_url,
                   post_type=:post_type, poll_question=:poll_question,
                   poll_options=:poll_options,
                   poll_correct_option_ids=:poll_correct_option_ids,
                   poll_explanation=:poll_explanation,
                   poll_anonymous=:poll_anonymous, poll_multiple=:poll_multiple,
                   publish_all_channels=:publish_all_channels,
                   schedule_type=:schedule_type, weekdays=:weekdays,
                   rotation_week=:rotation_week, publish_time=:publish_time,
                   scheduled_for=:scheduled_for, timezone=:timezone, channel=:channel,
                   button_text=:button_text, button_url=:button_url,
                   disable_notification=:disable_notification, enabled=:enabled,
                   updated_at=NOW() WHERE id=:id""",
                id=int(post_id), **values
            )
        else:
            rows = conn.run(
                """INSERT INTO telegram_scheduled_posts
                   (name, message, image_url, post_type, poll_question, poll_options,
                    poll_correct_option_ids, poll_explanation, poll_anonymous,
                    poll_multiple, publish_all_channels, schedule_type, weekdays, rotation_week,
                    publish_time, scheduled_for, timezone, channel, button_text,
                    button_url, disable_notification, enabled)
                   VALUES (:name, :message, :image_url, :post_type, :poll_question,
                           :poll_options, :poll_correct_option_ids, :poll_explanation,
                           :poll_anonymous, :poll_multiple, :publish_all_channels,
                           :schedule_type, :weekdays,
                           :rotation_week, :publish_time, :scheduled_for, :timezone,
                           :channel, :button_text, :button_url,
                           :disable_notification, :enabled)
                   RETURNING id""",
                **values
            )
            post_id = rows[0][0]
        conn.run("DELETE FROM telegram_post_channels WHERE post_id=:post_id", post_id=int(post_id))
        if not values["publish_all_channels"]:
            for channel_id in channel_ids:
                conn.run(
                    """INSERT INTO telegram_post_channels (post_id, channel_id)
                       VALUES (:post_id, :channel_id) ON CONFLICT DO NOTHING""",
                    post_id=int(post_id), channel_id=channel_id
                )
        return jsonify({"ok": True, "id": int(post_id)})
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/posts/<int:post_id>/toggle", methods=["POST"])
def admin_api_telegram_post_toggle(post_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE telegram_scheduled_posts
               SET enabled=:enabled, updated_at=NOW()
               WHERE id=:id AND deleted=FALSE""",
            enabled=bool(data.get("enabled")), id=post_id
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/posts/<int:post_id>/delete", methods=["POST"])
def admin_api_telegram_post_delete(post_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE telegram_scheduled_posts
               SET deleted=TRUE, enabled=FALSE, updated_at=NOW()
               WHERE id=:id""",
            id=post_id
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/admin/api/telegram/posts/<int:post_id>/send-now", methods=["POST"])
def admin_api_telegram_post_send_now(post_id):
    data = request.get_json(silent=True) or {}
    if data.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, name, message, image_url, schedule_type, weekdays,
                      rotation_week, publish_time, scheduled_for, timezone, channel,
                      button_text, button_url, disable_notification, enabled, source_key,
                      deleted, last_sent_at, created_at, updated_at, post_type,
                      poll_question, poll_options, poll_correct_option_ids,
                      poll_explanation, poll_anonymous, poll_multiple,
                      publish_all_channels
               FROM telegram_scheduled_posts WHERE id=:id AND deleted=FALSE""",
            id=post_id
        )
        if not rows:
            return jsonify({"ok": False, "error": "Publication introuvable"}), 404
        post = _telegram_post_from_row(rows[0])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

    slot_key = f"telegram-manual-{post_id}-{_paris_now().strftime('%Y%m%d-%H%M%S-%f')}"
    delivery = _send_saved_post_to_channels(post, slot_key, "manual-editorial")
    if not delivery["sent"]:
        return jsonify({
            "ok": False,
            "error": "Aucun canal n’a confirmé l’envoi",
            "delivery": delivery
        }), 502
    update_conn = None
    try:
        update_conn = get_conn()
        update_conn.run(
            "UPDATE telegram_scheduled_posts SET last_sent_at=NOW(), updated_at=NOW() WHERE id=:id",
            id=post_id
        )
    except Exception as e:
        app.logger.error(f"manual Telegram post update {post_id}: {e}")
    finally:
        if update_conn:
            try: update_conn.close()
            except: pass
    return jsonify({"ok": True, "delivery": delivery})


@app.route("/admin/api/telegram/upload", methods=["POST"])
def admin_api_telegram_upload():
    if request.form.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    image = request.files.get("image")
    if not image or not image.filename:
        return jsonify({"ok": False, "error": "Choisis une image"}), 400
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if image.mimetype not in allowed_types:
        return jsonify({"ok": False, "error": "Format accepté : JPG, PNG, WebP ou GIF"}), 400
    file_bytes = image.read(8 * 1024 * 1024 + 1)
    if len(file_bytes) > 8 * 1024 * 1024:
        return jsonify({"ok": False, "error": "L’image ne doit pas dépasser 8 Mo"}), 400
    image_url = upload_to_cloudinary(file_bytes, "image", secure_filename(image.filename))
    if not image_url:
        return jsonify({"ok": False, "error": "L’image n’a pas pu être enregistrée"}), 502
    return jsonify({"ok": True, "url": image_url})


@app.route("/admin/api/telegram/history", methods=["GET"])
def admin_api_telegram_history():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Non autorisé"}), 403
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT publications.slot_key, publications.post_kind,
                      publications.target_channel,
                      publications.status, publications.content,
                      publications.telegram_message_id, publications.attempts,
                      publications.error, publications.created_at,
                      publications.sent_at, posts.name
               FROM scheduled_publications AS publications
               LEFT JOIN telegram_scheduled_posts AS posts
                 ON posts.id=publications.post_id
               ORDER BY publications.created_at DESC LIMIT 60"""
        )
        history = []
        for row in rows:
            item = dict(zip([
                "slot_key", "post_kind", "target_channel", "status", "content", "message_id",
                "attempts", "error", "created_at", "sent_at", "name"
            ], row))
            for field in ("created_at", "sent_at"):
                if item.get(field) and hasattr(item[field], "isoformat"):
                    item[field] = item[field].isoformat()
            history.append(item)
        return jsonify({"ok": True, "history": history})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

# ── STATS ──
@app.route("/admin/api/stats", methods=["GET"])
def admin_api_stats():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        enforce_member_access_state()
        conn = get_conn()
        from datetime import datetime
        total = conn.run("SELECT COUNT(*) FROM members")[0][0]
        actifs = conn.run("""SELECT COUNT(*) FROM members
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        copy_on = conn.run("""SELECT COUNT(*) FROM members
            WHERE copy_actif=TRUE AND actif=TRUE
              AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        expires = conn.run("SELECT COUNT(*) FROM members WHERE date_fin <= NOW()")[0][0]
        nouveaux = conn.run("SELECT COUNT(*) FROM members WHERE created_at > NOW() - INTERVAL '7 days'")[0][0]
        conn.close()
        return jsonify({"ok":True,"total":total,"actifs":actifs,"copy_on":copy_on,"expires":expires,"nouveaux_7j":nouveaux})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/push/register", methods=["POST"])
@login_required
def push_register():
    """Compatibilité anciens clients : enregistre dans le stockage Web Push unique."""
    data = request.get_json(silent=True) or {}
    return _save_push_subscription(data.get("subscription") or data)


# ── ESSAI GRATUIT ─────────────────────────────────────────────────────────────

@app.route("/testgratuit")
def testgratuit():
    return send_from_directory("static/essai-gratuit", "index.html")

@app.route("/api/essai/register", methods=["POST"])
def essai_register():
    import urllib.request as _ur
    import urllib.parse as _up
    data = request.get_json()
    prenom   = data.get("prenom", "").strip()
    email    = data.get("email", "").strip()
    phone    = data.get("phone", "").strip()
    age      = data.get("age", "").strip()
    pays     = data.get("pays", "").strip()
    source   = data.get("source", "Landing Essai Gratuit")
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    if not email:
        return jsonify({"ok": False, "error": "Email requis"}), 400

    # Appel Apps Script — celui-ci gere Sheet + Telegram
    try:
        APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxibVo5o_9O6h8Uc9XoNRRRSlSXMUC09EStS0FHjz6trpVa9J-MpS-n8U3cTZwRV4A/exec"
        payload = json.dumps({
            "prenom": prenom, "email": email, "phone": phone,
            "age": age, "pays": pays, "date": date_str, "source": source
        }).encode()
        req = _ur.Request(APPS_SCRIPT_URL, data=payload, headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=15)
    except Exception as e:
        app.logger.error(f"essai Apps Script: {e}")

    return jsonify({"ok": True})


@app.route("/admin/export-emails")
def admin_export_emails():
    key = request.args.get("key","")
    if key != ADMIN_KEY:
        return "Non autorise", 403
    try:
        conn = get_conn()
        rows = conn.run("""
            SELECT nom, email, telephone, capital, date_fin
            FROM members
            WHERE actif=TRUE AND email IS NOT NULL AND email != ''
            ORDER BY nom
        """)
        conn.close()
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Nom", "Email", "Telephone", "Capital", "Date expiration"])
        for nom, email, tel, capital, date_fin in rows:
            writer.writerow([
                nom or "",
                email or "",
                tel or "",
                capital or "",
                date_fin.strftime("%d/%m/%Y") if date_fin and hasattr(date_fin,"strftime") else ""
            ])
        output.seek(0)
        from flask import Response
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=bectanse_membres_emails.csv"}
        )
    except Exception as e:
        return f"Erreur: {e}", 500


@app.route("/admin/api/membre/profil")
def admin_api_membre_profil():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    code = request.args.get("code","")
    try:
        conn = get_conn()
        rows = conn.run("""
            SELECT code, nom, email, telephone, telegram,
                   capital, actif, copy_actif,
                   date_souscription, date_fin,
                   parrain_code, filleuls_count, gains_parrainage,
                   paiement_type, paiement_iban, paiement_bic,
                   paiement_titulaire, paiement_crypto_reseau,
                   paiement_crypto_adresse, params, historique,
                   created_at, last_login
            FROM members WHERE code=:c
        """, c=code)
        conn.close()
        if not rows: return jsonify({"ok":False,"error":"Introuvable"})
        r = rows[0]
        from datetime import datetime
        def fmt(d): return d.strftime("%d/%m/%Y %H:%M") if d and hasattr(d,"strftime") else "—"
        member = {
            "code": r[0], "nom": r[1], "email": r[2] or "—",
            "telephone": r[3] or "—", "telegram": r[4] or "—",
            "capital": r[5] or "—", "actif": r[6], "copy_actif": r[7],
            "date_souscription": fmt(r[8]), "date_fin": fmt(r[9]),
            "parrain_code": r[10] or "—", "filleuls_count": r[11] or 0,
            "gains_parrainage": r[12] or 0,
            "paiement_type": r[13] or "—", "paiement_iban": r[14] or "—",
            "paiement_bic": r[15] or "—", "paiement_titulaire": r[16] or "—",
            "paiement_crypto_reseau": r[17] or "—",
            "paiement_crypto_adresse": r[18] or "—",
            "params": r[19], "historique": r[20],
            "created_at": fmt(r[21]), "last_login": fmt(r[22]),
        }
        # Extraire infos MT4 depuis params
        import json as _json
        try:
            p = _json.loads(r[19]) if isinstance(r[19], str) else (r[19] or {})
            member["mt_login"]    = p.get("mt_login","—") or "—"
            member["mt_server"]   = p.get("serveur","—") or "—"
            member["mt_password"] = p.get("mt_password","—") or "—"
            member["mode_risque"] = p.get("mode_risque","—") or "—"
            member["lots"]        = p.get("lots","—")
            member["plateforme"]  = p.get("plateforme","MT4") or "MT4"
        except Exception as ep:
            app.logger.error(f"profil params parse: {ep}")
        return jsonify({"ok":True,"member":member})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


# ── BECTANSE ANALYSE IA — BÊTA PRIVÉE ───────────────────────────────────────

ANALYSIS_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"}
ANALYSIS_SESSIONS = {"Asie", "Londres", "New York", "Overlap", "Hors session"}
ANALYSIS_STYLES = {"Scalping", "Intraday", "Swing", "Position", "Multi-TF"}
ANALYSIS_EVENT_IMPACTS = {"high", "medium", "low"}
ANALYSIS_MARKETS = {"XAU/USD", "XAG/USD", "BTC/USD", "ETH/USD", "EUR/USD", "GBP/USD",
                    "USD/JPY", "NAS100", "US30", "SPX500", "WTI", "GER40"}


def _analysis_wallet(conn, member_code, lock=False):
    conn.run("""INSERT INTO analysis_wallets
        (member_code, balance, lifetime_granted, lifetime_spent)
        VALUES (:code, :initial, :initial, 0)
        ON CONFLICT (member_code) DO NOTHING""",
        code=member_code, initial=ANALYSIS_INITIAL_CREDITS)
    suffix = " FOR UPDATE" if lock else ""
    rows = conn.run(
        "SELECT balance, lifetime_granted, lifetime_spent FROM analysis_wallets "
        "WHERE member_code=:code" + suffix,
        code=member_code)
    return {
        "balance": int(rows[0][0]),
        "lifetime_granted": int(rows[0][1]),
        "lifetime_spent": int(rows[0][2]),
    }


def _analysis_schema():
    annotation = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "type": {"type": "string", "enum": ["zone", "line", "point", "path"]},
            "role": {"type": "string", "enum": [
                "support", "resistance", "supply", "demand", "liquidity", "pivot",
                "entry", "target", "invalidation", "structure", "bullish_path", "bearish_path"
            ]},
            "label": {"type": "string"},
            "price": {"type": "string"},
            "x_start": {"type": "number", "minimum": 0, "maximum": 100},
            "x_end": {"type": "number", "minimum": 0, "maximum": 100},
            "y_start": {"type": "number", "minimum": 0, "maximum": 100},
            "y_end": {"type": "number", "minimum": 0, "maximum": 100},
            "label_x": {"type": "number", "minimum": 0, "maximum": 100},
            "label_y": {"type": "number", "minimum": 0, "maximum": 100},
            "points": {"type": "array", "maxItems": 8, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "x": {"type": "number", "minimum": 0, "maximum": 100},
                    "y": {"type": "number", "minimum": 0, "maximum": 100}
                }, "required": ["x", "y"]
            }}
        },
        "required": ["type", "role", "label", "price", "x_start", "x_end",
                     "y_start", "y_end", "label_x", "label_y", "points"]
    }
    zone = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "type": {"type": "string"}, "niveau": {"type": "string"},
            "force": {"type": "string"}, "description": {"type": "string"}
        },
        "required": ["type", "niveau", "force", "description"]
    }
    plan = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "direction": {"type": "string"}, "qualite": {"type": "string"},
            "entree": {"type": "string"}, "declencheur": {"type": "string"},
            "objectif_1": {"type": "string"}, "objectif_2": {"type": "string"},
            "objectif_3": {"type": "string"},
            "invalidation": {"type": "string"}, "ratio": {"type": "string"}
        },
        "required": ["direction", "qualite", "entree", "declencheur",
                     "objectif_1", "objectif_2", "objectif_3", "invalidation", "ratio"]
    }
    checklist = {
        "type": "object", "additionalProperties": False,
        "properties": {"point": {"type": "string"}, "statut": {"type": "string"}},
        "required": ["point", "statut"]
    }
    news_impact = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "event": {"type": "string"}, "impact": {"type": "string"},
            "direction_attendue": {"type": "string"}, "conseil": {"type": "string"}
        },
        "required": ["event", "impact", "direction_attendue", "conseil"]
    }
    institutional = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "tendance": {"type": "string"}, "liquidite": {"type": "string"},
            "order_blocks": {"type": "string"}, "fvg": {"type": "string"},
            "smc_ict": {"type": "string"}, "volume": {"type": "string"},
            "setup_recommande": {"type": "string"}, "etat_marche": {"type": "string"},
            "regime_marche": {"type": "string"}, "mtf_alignment": {"type": "string"},
            "kill_zone": {"type": "string"}, "phase_wyckoff": {"type": "string"},
            "zone_prix": {"type": "string"}, "zone_ote": {"type": "string"},
            "score_confluence": {"type": "integer", "minimum": 0, "maximum": 15},
            "verdict": {"type": "string"}
        },
        "required": ["tendance", "liquidite", "order_blocks", "fvg", "smc_ict", "volume",
                     "setup_recommande", "etat_marche", "regime_marche", "mtf_alignment",
                     "kill_zone", "phase_wyckoff", "zone_prix", "zone_ote",
                     "score_confluence", "verdict"]
    }
    intelligence = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "actualites": {"type": "string"}, "macro": {"type": "string"},
            "geopolitique": {"type": "string"}, "session": {"type": "string"},
            "anticipation": {"type": "string"}
        },
        "required": ["actualites", "macro", "geopolitique", "session", "anticipation"]
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "biais_global": {"type": "string"},
            "confiance": {"type": "integer", "minimum": 0, "maximum": 100},
            "structure": {"type": "string"},
            "resume": {"type": "string"},
            "prix_visible": {"type": "string"},
            "annotations_graphique": {"type": "array", "items": annotation, "maxItems": 12},
            "zones": {"type": "array", "items": zone, "maxItems": 6},
            "plans": {"type": "array", "items": plan, "minItems": 1, "maxItems": 2},
            "risque": {"type": "string"},
            "risques_detectes": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "contexte_marche": {"type": "string"},
            "lecture_institutionnelle": institutional,
            "intelligence_marche": intelligence,
            "annonces_impact": {"type": "array", "items": news_impact, "maxItems": 12},
            "checklist": {"type": "array", "items": checklist, "maxItems": 10},
            "conclusion": {"type": "string"},
            "avertissement": {"type": "string"}
        },
        "required": ["biais_global", "confiance", "structure", "resume", "prix_visible",
                     "annotations_graphique",
                     "zones", "plans", "risque", "risques_detectes", "contexte_marche",
                     "lecture_institutionnelle", "intelligence_marche", "annonces_impact",
                     "checklist", "conclusion", "avertissement"]
    }


def _analysis_prompt(market, timeframe, session_name, trading_style, economic_events):
    today = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    events_context = json.dumps(economic_events, ensure_ascii=False) if economic_events else "Aucune annonce sélectionnée"
    return f"""Tu es l'assistant d'analyse graphique éducative de Bectanse Académie.
Bectanse possède une expertise historique sur XAU/USD, mais cet outil analyse aussi les autres
marchés. Le marché choisi est {market}. Analyse uniquement cet instrument sur la capture jointe.

Contexte utilisateur : marché {market}, timeframe {timeframe}, session {session_name}, style {trading_style}.
Date et heure Europe/Paris : {today}.
Événements signalés par le membre : {events_context}.

Le contexte économique transmis par le membre est la seule source d'événements. N'invente aucune
actualité ni aucun chiffre absent. Adapte simplement les facteurs de risque à l'instrument choisi.

RÈGLES ABSOLUES :
- Lis les prix uniquement sur l'axe visible. N'invente jamais un niveau illisible.
- Si un prix est ambigu, écris « niveau non lisible sur la capture ».
- Distingue observation, scénario conditionnel et invalidation.
- Fournis un plan en trois objectifs distincts TP1, TP2 et TP3 pour chaque scénario. Si un
  objectif fiable n'est pas lisible, écris « à déterminer après confirmation » au lieu d'inventer.
- Retourne aussi les annotations à superposer sur la capture originale. Les coordonnées sont des
  pourcentages de l'image complète : x de gauche à droite et y de haut en bas, entre 0 et 100.
  Pour une ligne horizontale, utilise y_start = y_end. Pour une zone, encadre ses deux limites.
  Pour une trajectoire, type=path et renseigne 4 à 8 points formant un zigzag réaliste terminé par
  la cible; les autres types ont points=[]. label_x/label_y fixe l'emplacement exact du libellé.
- Le rendu doit ressembler à une analyse TradingView professionnelle : 2 ou 3 grandes zones
  horizontales translucides (Supply, Support, Demand), 1 ou 2 niveaux clés (Pivot, liquidité),
  puis une trajectoire haussière verte et/ou baissière rouge avec flèche. Les zones s'étendent sur
  une largeur utile du graphique et leur libellé reste à l'intérieur, loin de l'axe des prix.
- Ne crée jamais une colonne d'étiquettes empilées à droite. Ne superpose aucun libellé. N'annote
  pas chaque donnée du rapport : 8 à 10 éléments visuels lisibles maximum. TP1/TP2/TP3 restent
  détaillés dans le plan écrit; sur l'image, la trajectoire et ses niveaux majeurs suffisent.
- Produis une lecture institutionnelle séparée : tendance, liquidité, Order Blocks, FVG,
  concepts SMC/ICT et volume visible. Indique clairement ce qui n'est pas lisible.
- Inclus tous les diagnostics historiques du Bectanse Bot Analyser : setup recommandé,
  état du marché (NO TRADE / risqué / valide), régime, alignement MTF H4/H1/TF principal,
  Kill Zone ICT, phase Wyckoff, Premium/Discount, zone OTE, score de confluence sur 15
  et verdict final avec conditions validées.
- Structure l'intelligence marché en cinq angles : actualités, macro, géopolitique,
  session active et anticipation de la prochaine session.
- Pour chaque événement sélectionné, explique son impact potentiel sans inventer son résultat.
- Ne promets jamais de gain et ne présente jamais un scénario comme certain.
- Les plans sont éducatifs et conditionnels, pas des ordres ni un conseil financier personnalisé.
- Réponds en français, de façon concise, avec le schéma JSON imposé.
- Priorité absolue au plan exploitable : biais, zone d'entrée conditionnelle, invalidation/SL,
  TP1, TP2, TP3 et conditions de déclenchement. Deux scénarios maximum : principal et alternatif.
- Une seule phrase courte par champ. Ne répète jamais la même information dans plusieurs sections.
- Le rapport complet doit rester sous 1 500 mots, annotations JSON comprises.
- Le champ avertissement doit rappeler que l'analyse automatisée peut se tromper et ne remplace
  ni une vérification humaine ni une gestion du risque adaptée."""


def _openai_analysis(image_data_url, market, timeframe, session_name, trading_style, economic_events):
    if not OPENAI_API_KEY:
        raise RuntimeError("Le moteur d’analyse n’est pas encore connecté.")
    payload = {
        "model": OPENAI_ANALYSIS_MODEL,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 2200,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": _analysis_prompt(market, timeframe, session_name, trading_style, economic_events)},
                {"type": "input_image", "image_url": image_data_url, "detail": "high"}
            ]
        }],
        "text": {"format": {
            "type": "json_schema", "name": "bectanse_chart_analysis",
            "strict": True, "schema": _analysis_schema()
        }}
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=150)
    if not response.ok:
        try:
            api_error = response.json().get("error", {}).get("message", "")
        except Exception:
            api_error = ""
        raise RuntimeError(api_error or f"Erreur du moteur ({response.status_code})")
    body = response.json()
    output_text = body.get("output_text", "")
    if not output_text:
        for item in body.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        output_text += content.get("text", "")
    if not output_text:
        raise RuntimeError("Le moteur n’a produit aucun résultat exploitable.")
    try:
        result = json.loads(output_text)
    except Exception as error:
        raise RuntimeError("Le résultat reçu est incomplet. Le crédit sera remboursé.") from error
    usage = body.get("usage") or {}
    search_calls = 0
    return result, {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "search_calls": search_calls,
    }


def _refund_analysis_credit(member_code, job_id, error_message):
    conn = get_conn()
    try:
        conn.run("BEGIN")
        changed = conn.run("""UPDATE analysis_jobs SET status='failed', error=:error,
            completed_at=NOW() WHERE id=:job AND member_code=:code AND status='processing'
            RETURNING id""", error=error_message[:800], job=job_id, code=member_code)
        if changed and member_code != ANALYSIS_ADMIN_CODE:
            wallet = _analysis_wallet(conn, member_code, lock=True)
            new_balance = wallet["balance"] + 1
            conn.run("""UPDATE analysis_wallets SET balance=:balance,
                lifetime_spent=GREATEST(0, lifetime_spent-1), updated_at=NOW()
                WHERE member_code=:code""", balance=new_balance, code=member_code)
            conn.run("""INSERT INTO analysis_credit_ledger
                (member_code, delta, balance_after, reason, reference)
                VALUES (:code, 1, :balance, 'refund_failed_analysis', :ref)
                ON CONFLICT (reference) DO NOTHING""",
                code=member_code, balance=new_balance, ref=f"refund:{job_id}")
        conn.run("COMMIT")
    except Exception:
        try: conn.run("ROLLBACK")
        except Exception: pass
        raise
    finally:
        conn.close()


ANALYSIS_ADMIN_CODE = "BCT-ADMIN-BETA"


def _analysis_admin_allowed():
    supplied = request.headers.get("X-Admin-Key") or request.args.get("key", "")
    return hmac.compare_digest(str(supplied), str(ADMIN_KEY))


def _refund_stale_admin_analyses():
    """Rembourse les essais interrompus par un redémarrage ou un timeout serveur."""
    conn = get_conn()
    try:
        rows = conn.run("""SELECT id FROM analysis_jobs
            WHERE member_code=:code AND status='processing'
              AND created_at < NOW() - INTERVAL '2 minutes'""",
            code=ANALYSIS_ADMIN_CODE)
    finally:
        conn.close()
    for row in rows:
        try:
            _refund_analysis_credit(ANALYSIS_ADMIN_CODE, row[0], "Analyse interrompue — crédit remboursé")
        except Exception as error:
            app.logger.warning("Remboursement analyse admin %s: %s", row[0], error)


@app.route("/analyse-ia")
def analyse_ia():
    if not _analysis_admin_allowed():
        return redirect("/admin-panel")
    code = ANALYSIS_ADMIN_CODE
    _refund_stale_admin_analyses()
    member = {"code": code, "nom": "Administration Bectanse"}
    conn = get_conn()
    try:
        wallet = {"balance": None, "unlimited": True,
                  "lifetime_granted": 0, "lifetime_spent": 0}
        rows = conn.run("""SELECT id, status, market, timeframe, session_name, trading_style,
            result_json, error, created_at FROM analysis_jobs
            WHERE member_code=:code ORDER BY created_at DESC LIMIT 12""", code=code)
        history = []
        for row in rows:
            item = dict(zip(["id", "status", "market", "timeframe", "session_name", "trading_style",
                             "result_json", "error", "created_at"], row))
            if item["result_json"]:
                try: item["result"] = json.loads(item["result_json"])
                except Exception: item["result"] = None
            else: item["result"] = None
            history.append(item)
    finally:
        conn.close()
    return render_template("analyse_ia.html", member=member, wallet=wallet, history=history,
                           engine_ready=bool(OPENAI_API_KEY), demo_mode=False,
                           admin_beta=True, admin_key=ADMIN_KEY)


@app.route("/api/analyse-ia/run", methods=["POST"])
def analyse_ia_run():
    if not _analysis_admin_allowed():
        return jsonify({"ok": False, "error": "Bêta privée réservée à l’administration."}), 403
    code = ANALYSIS_ADMIN_CODE
    if not OPENAI_API_KEY:
        return jsonify({"ok": False, "error": "La bêta attend encore la connexion du moteur OpenAI."}), 503
    data = request.get_json(silent=True) or {}
    market = str(data.get("market", "XAU/USD")).upper().strip()
    timeframe = str(data.get("timeframe", "M15"))
    session_name = str(data.get("session", "Londres"))
    trading_style = str(data.get("style", "Intraday"))
    raw_events = data.get("events") or []
    image_data = str(data.get("image", ""))
    if market not in ANALYSIS_MARKETS and not re.fullmatch(r"[A-Z0-9][A-Z0-9/_.-]{1,19}", market):
        return jsonify({"ok": False, "error": "Symbole de marché invalide."}), 400
    if timeframe not in ANALYSIS_TIMEFRAMES or session_name not in ANALYSIS_SESSIONS or trading_style not in ANALYSIS_STYLES:
        return jsonify({"ok": False, "error": "Configuration d’analyse invalide."}), 400
    if not isinstance(raw_events, list) or len(raw_events) > 12:
        return jsonify({"ok": False, "error": "Liste d’annonces invalide."}), 400
    economic_events = []
    for event in raw_events:
        if not isinstance(event, dict):
            return jsonify({"ok": False, "error": "Annonce invalide."}), 400
        name = str(event.get("name", "")).strip()[:80]
        impact = str(event.get("impact", "medium")).lower()
        event_time = str(event.get("time", "")).strip()[:10]
        if not name or impact not in ANALYSIS_EVENT_IMPACTS:
            return jsonify({"ok": False, "error": "Annonce invalide."}), 400
        economic_events.append({"name": name, "impact": impact, "time": event_time})
    if not image_data.startswith("data:image/") or ";base64," not in image_data:
        return jsonify({"ok": False, "error": "Capture invalide."}), 400
    header, encoded = image_data.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return jsonify({"ok": False, "error": "Format accepté : JPG, PNG ou WEBP."}), 400
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return jsonify({"ok": False, "error": "La capture est illisible."}), 400
    if len(raw) > ANALYSIS_MAX_IMAGE_BYTES:
        return jsonify({"ok": False, "error": "La capture dépasse 6 Mo."}), 413

    job_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.run("BEGIN")
        new_balance = None
        conn.run("""INSERT INTO analysis_jobs
            (id, member_code, status, market, timeframe, session_name, trading_style, image_mime, model)
            VALUES (:id, :code, 'processing', :market, :tf, :session, :style, :mime, :model)""",
            id=job_id, code=code, market=market, tf=timeframe, session=session_name,
            style=trading_style, mime=mime, model=OPENAI_ANALYSIS_MODEL)
        conn.run("COMMIT")
    except Exception as error:
        try: conn.run("ROLLBACK")
        except Exception: pass
        app.logger.error("Création analyse IA: %s", error)
        return jsonify({"ok": False, "error": "Impossible de réserver le crédit."}), 500
    finally:
        conn.close()

    try:
        result, usage = _openai_analysis(image_data, market, timeframe, session_name, trading_style, economic_events)
        conn = get_conn()
        conn.run("""UPDATE analysis_jobs SET status='completed', result_json=:result,
            input_tokens=:input_tokens, output_tokens=:output_tokens,
            search_calls=:search_calls, completed_at=NOW()
            WHERE id=:id AND member_code=:code""",
            result=json.dumps(result, ensure_ascii=False),
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            search_calls=usage["search_calls"], id=job_id, code=code)
        conn.close()
        return jsonify({"ok": True, "job_id": job_id, "balance": new_balance,
                        "result": result, "usage": usage})
    except Exception as error:
        app.logger.error("Analyse IA %s: %s", job_id, error)
        try: _refund_analysis_credit(code, job_id, str(error))
        except Exception as refund_error: app.logger.error("Remboursement %s: %s", job_id, refund_error)
        return jsonify({"ok": False, "error": str(error), "refunded": False,
                        "balance": None}), 502


@app.route("/api/analyse-ia/checkout", methods=["POST"])
@login_required
def analyse_ia_checkout():
    code = session["member_code"]
    if code == "BCT-DEMO2026":
        return jsonify({"ok": False, "error": "Achat indisponible en mode Explorer."}), 403
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Les recharges seront ouvertes après la validation de la bêta."}), 503
    pack_id = str((request.get_json(silent=True) or {}).get("pack", ""))
    pack = ANALYSIS_PACKS.get(pack_id)
    if not pack:
        return jsonify({"ok": False, "error": "Pack inconnu."}), 400
    root = request.url_root.rstrip("/")
    form = {
        "mode": "payment",
        "success_url": root + "/analyse-ia?checkout=success",
        "cancel_url": root + "/analyse-ia?checkout=cancelled",
        "client_reference_id": code,
        "metadata[member_code]": code,
        "metadata[credits]": str(pack["credits"]),
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(pack["amount_cents"]),
        "line_items[0][price_data][product_data][name]": f"Bectanse Analyse IA — {pack['credits']} crédits",
        "line_items[0][price_data][product_data][description]": "1 crédit = 1 analyse complète",
    }
    try:
        stripe_response = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET_KEY, ""), data=form, timeout=25)
        stripe_data = stripe_response.json()
        if not stripe_response.ok:
            raise RuntimeError(stripe_data.get("error", {}).get("message", "Paiement indisponible"))
        return jsonify({"ok": True, "url": stripe_data["url"]})
    except Exception as error:
        app.logger.error("Création paiement crédits: %s", error)
        return jsonify({"ok": False, "error": "Impossible d’ouvrir le paiement pour le moment."}), 502


def _stripe_signature_valid(raw_body, signature_header):
    if not STRIPE_WEBHOOK_SECRET or not signature_header:
        return False
    values = {}
    for part in signature_header.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values.get("t", ["0"])[0])
    except Exception:
        return False
    if abs(int(time.time()) - timestamp) > 300:
        return False
    signed = str(timestamp).encode("utf-8") + b"." + raw_body
    expected = hmac.new(STRIPE_WEBHOOK_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in values.get("v1", []))


@app.route("/api/stripe/analyse-credits", methods=["POST"])
def stripe_analysis_credits_webhook():
    raw_body = request.get_data(cache=False)
    if not _stripe_signature_valid(raw_body, request.headers.get("Stripe-Signature", "")):
        return jsonify({"ok": False}), 400
    try:
        event = json.loads(raw_body.decode("utf-8"))
        if event.get("type") != "checkout.session.completed":
            return jsonify({"ok": True})
        checkout = (event.get("data") or {}).get("object") or {}
        if checkout.get("payment_status") != "paid":
            return jsonify({"ok": True})
        metadata = checkout.get("metadata") or {}
        code = str(metadata.get("member_code", ""))
        credits = int(metadata.get("credits", 0) or 0)
        session_id = str(checkout.get("id", ""))
        pack = next((value for value in ANALYSIS_PACKS.values() if value["credits"] == credits), None)
        if (not code or not session_id or not pack or
                int(checkout.get("amount_total", 0) or 0) != pack["amount_cents"] or
                str(checkout.get("currency", "")).lower() != "eur"):
            return jsonify({"ok": False}), 400
        conn = get_conn()
        try:
            conn.run("BEGIN")
            inserted = conn.run("""INSERT INTO analysis_purchases
                (stripe_session_id, member_code, credits, amount_cents, currency, status)
                VALUES (:session_id, :code, :credits, :amount, 'eur', 'paid')
                ON CONFLICT (stripe_session_id) DO NOTHING RETURNING id""",
                session_id=session_id, code=code, credits=credits, amount=pack["amount_cents"])
            if inserted:
                wallet = _analysis_wallet(conn, code, lock=True)
                new_balance = wallet["balance"] + credits
                conn.run("""UPDATE analysis_wallets SET balance=:balance,
                    lifetime_granted=lifetime_granted+:credits, updated_at=NOW()
                    WHERE member_code=:code""", balance=new_balance, credits=credits, code=code)
                conn.run("""INSERT INTO analysis_credit_ledger
                    (member_code, delta, balance_after, reason, reference)
                    VALUES (:code, :credits, :balance, 'stripe_purchase', :reference)
                    ON CONFLICT (reference) DO NOTHING""",
                    code=code, credits=credits, balance=new_balance,
                    reference=f"stripe:{session_id}")
            conn.run("COMMIT")
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()
        return jsonify({"ok": True})
    except Exception as error:
        app.logger.error("Webhook crédits Stripe: %s", error)
        return jsonify({"ok": False}), 500

@app.route("/calculateur")
@login_required
def calculateur():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    return render_template("calculateur.html", member=member, demo_mode=(code == "BCT-DEMO2026"))

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET","POST"])
def login():
    if "member_code" in session:
        return redirect(url_for("accueil"))
    error = None
    notice = None
    explorer_gate_enabled = brevo_email_delivery_available()
    if request.method == "POST":
        code = request.form.get("code","").strip().upper()
        if code == "BCT-DEMO2026" and explorer_gate_enabled and not session.get("prospect_verified_email"):
            prenom = request.form.get("demo_prenom", "").strip()[:80]
            email = request.form.get("demo_email", "").strip().lower()[:254]
            consent = request.form.get("demo_consent") == "yes"
            if not prenom:
                error = "Indique ton prénom pour recevoir l’accès Explorer."
            elif "@" not in email or "." not in email.rsplit("@", 1)[-1]:
                error = "Saisis une adresse e-mail valide."
            elif not consent:
                error = "Confirme ton accord pour recevoir l’accès et les informations Bectanse."
            else:
                raw_token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                try:
                    conn = get_conn()
                    conn.run("""INSERT INTO prospect_email_verifications
                        (email, prenom, token_hash, source, status, created_at, expires_at, verified_at)
                        VALUES (:email, :prenom, :token_hash, 'explorer', 'pending', NOW(), NOW() + INTERVAL '24 hours', NULL)
                        ON CONFLICT (email) DO UPDATE SET prenom=:prenom, token_hash=:token_hash,
                        source='explorer', status='pending', created_at=NOW(),
                        expires_at=NOW() + INTERVAL '24 hours', verified_at=NULL""",
                        email=email, prenom=prenom, token_hash=token_hash)
                    conn.close()
                    confirmation_url = request.url_root.rstrip("/") + url_for(
                        "confirm_explorer_email", token=raw_token)
                    result = send_brevo_prospect_verification(email, prenom, confirmation_url)
                    if result.get("ok"):
                        notice = "E-mail envoyé. Clique sur le lien reçu pour ouvrir l’espace Explorer."
                    else:
                        error = "L’e-mail de confirmation n’a pas pu partir. Réessaie dans quelques instants."
                except Exception as exc:
                    app.logger.error("Inscription prospect Explorer: %s", exc)
                    error = "Impossible de préparer ton accès pour le moment. Réessaie dans quelques instants."
            return render_template("login.html", error=error, notice=notice,
                                   explorer_gate_enabled=explorer_gate_enabled)
        member = get_member(code)
        if not member:
            error = "Code invalide. Vérifie ton code et réessaie."
        elif not member.get("actif", True):
            session["member_code"] = member["code"]
            return redirect(url_for("accueil_expire"))
        else:
            session["member_code"] = member["code"]
            try:
                conn = get_conn()
                conn.run("UPDATE members SET last_login=NOW() WHERE code=:c", c=code)
                conn.close()
            except: pass
            return redirect(url_for("accueil"))
    return render_template("login.html", error=error, notice=notice,
                           explorer_gate_enabled=explorer_gate_enabled)


@app.route("/explorer/confirmer/<token>")
def confirm_explorer_email(token):
    token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    try:
        conn = get_conn()
        rows = conn.run("""SELECT email, prenom FROM prospect_email_verifications
            WHERE token_hash=:token_hash AND status='pending' AND expires_at > NOW()""",
            token_hash=token_hash)
        if not rows:
            conn.close()
            return render_template("login.html",
                error="Ce lien de confirmation est invalide ou a expiré.", notice=None,
                explorer_gate_enabled=brevo_email_delivery_available()), 400
        email, prenom = rows[0]
        conn.run("""UPDATE prospect_email_verifications SET status='verified', verified_at=NOW()
                    WHERE token_hash=:token_hash""", token_hash=token_hash)
        conn.close()
        sync_result = sync_brevo_prospect_contact(email, prenom, "Explorer confirmé")
        if not sync_result.get("ok"):
            app.logger.error("Synchronisation prospect confirme %s: %s", email, sync_result.get("error"))
        session["prospect_verified_email"] = email
        session["member_code"] = "BCT-DEMO2026"
        return redirect(url_for("accueil"))
    except Exception as exc:
        app.logger.error("Confirmation prospect Explorer: %s", exc)
        return render_template("login.html",
            error="La confirmation n’a pas pu être validée. Réessaie dans quelques instants.", notice=None,
            explorer_gate_enabled=brevo_email_delivery_available()), 500

@app.route("/dashboard")
@login_required
def dashboard():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    raw_params = member.get("params") or {}
    # Fusionner avec les defaults pour les clés manquantes
    dp = default_params()
    dp.update(raw_params)
    params = dp
    hist   = list(reversed((member.get("historique") or [])[-10:]))
    copy_actif = member.get("copy_actif", True)
    date_souscription = member.get("date_souscription")
    date_fin = member.get("date_fin")
    jours_restants = None
    statut_abo = "actif"
    if date_fin:
        now = datetime.now()
        if hasattr(date_fin, 'year'):
            delta = date_fin - now
            jours_restants = max(0, delta.days)
            if jours_restants == 0: statut_abo = "expiré"
            elif jours_restants <= 7: statut_abo = "expire_bientot"
    alerte_lue = member.get("alerte_lue", True)
    afficher_alerte = (not alerte_lue) and jours_restants is not None and jours_restants <= 7
    # Notification bot
    notif_type    = member.get("notif_type", "") or ""
    notif_message = member.get("notif_message", "") or ""
    notif_lue     = member.get("notif_lue", True)
    afficher_notif = bool(notif_type and notif_message and not notif_lue)

    return render_template("dashboard.html",
        member=member, params=params, historique=hist,
        copy_actif=copy_actif,
        date_souscription=date_souscription,
        date_fin=date_fin,
        jours_restants=jours_restants,
        statut_abo=statut_abo,
        afficher_alerte=afficher_alerte,
        notif_type=notif_type,
        notif_message=notif_message,
        afficher_notif=afficher_notif
    ,
        demo_mode=(session.get("member_code","")=="BCT-DEMO2026"))

@app.route("/offres")
@login_required
def offres():
    return render_template("offres.html")

@app.route("/save", methods=["POST"])
@login_required
def save():
    code = session["member_code"]
    member = get_member(code)
    if not member: return jsonify({"ok": False})
    data = request.get_json()
    p = {
        "mode_risque": data.get("mode_risque","Lots fixes"),
        "lots": float(data.get("lots",0.01)),
        "lots_max": float(data.get("lots_max",5)),
        "slippage": int(data.get("slippage",100)),
        "forcer_lot_minimum": bool(data.get("forcer_lot_minimum")),
        "inverser_trades": bool(data.get("inverser_trades")),
        "copier_ordres_en_attente": bool(data.get("copier_ordres_en_attente")),
        "convertir_pending_invalide": bool(data.get("convertir_pending_invalide")),
        "copier_sl": bool(data.get("copier_sl")),
        "drawdown_actif": bool(data.get("drawdown_actif")),
        "drawdown_pct": float(data.get("drawdown_pct",5)),
        "drawdown_gain_actif": bool(data.get("drawdown_gain_actif")),
        "drawdown_gain_pct": float(data.get("drawdown_gain_pct",5)),
        "objectif_actif": bool(data.get("objectif_actif")),
        "objectif_gain_pct": float(data.get("objectif_gain_pct",5)),
        "objectif_perte_pct": float(data.get("objectif_perte_pct",3)),
        "objectif_periode": data.get("objectif_periode","Mensuel"),
        "filtre_news": bool(data.get("filtre_news")),
        "risque_pct": float(data.get("risque_pct",1)),
        "multiplicateur": float(data.get("multiplicateur",1)),
        "risque_balance_pct": float(data.get("risque_balance_pct",1)),
        "risque_equity_pct": float(data.get("risque_equity_pct",1)),
        "lot_symboles": data.get("lot_symboles",{}),
    }
    hist_entry = {"date": datetime.now().strftime("%d/%m/%Y %H:%M"), "statut": "en_attente", "params": p}
    try:
        conn = get_conn()
        rows = conn.run("SELECT historique FROM members WHERE code=:c", c=code)
        hist = json.loads(rows[0][0]) if rows and rows[0][0] else []
        hist.append(hist_entry)
        conn.run("UPDATE members SET params=:p, historique=:h, last_login=NOW() WHERE code=:c",
                 p=json.dumps(p), h=json.dumps(hist[-50:]), c=code)
        conn.close()
        confirm_url = f"https://bectanse-auto.up.railway.app/confirm/{code}?t={ADMIN_KEY}"
        problem_url = f"https://bectanse-auto.up.railway.app/problem/{code}?t={ADMIN_KEY}"
        markup = {"inline_keyboard":[[
            {"text":"✅ Appliqué sur notre système","url":confirm_url},
            {"text":"❌ Refuser la demande","url":problem_url}
        ]]}
        tg_msg = build_notif(member, p, code)
        send_telegram(tg_msg, reply_markup=markup)
        return jsonify({"ok": True})
    except Exception as e:
        app.logger.error(f"save: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/toggle-copy", methods=["POST"])
@login_required
def toggle_copy():
    code = session["member_code"]
    member = get_member(code)
    if not member: return jsonify({"ok": False})
    try:
        conn = get_conn()
        rows = conn.run("SELECT copy_actif FROM members WHERE code=:c", c=code)
        current = rows[0][0] if rows and rows[0][0] is not None else True
        new_state = not current
        conn.run("UPDATE members SET copy_actif=:s WHERE code=:c", s=new_state, c=code)
        conn.close()
        icon = "✅" if new_state else "⏸️"
        status = "ACTIVÉ" if new_state else "DÉSACTIVÉ"
        send_telegram(
            f"{icon} *COPY TRADING {status}*\n\n"
            f"👤 *{member['nom']}* | `{code}`\n"
            f"💰 *{member['capital']}*\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
            + ("✅ Copy actif." if new_state else "⚠️ *Action requise* — Désactiver sur notre système.")
        )
        return jsonify({"ok": True, "copy_actif": new_state})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/marquer-alerte-lue", methods=["POST"])
@login_required
def marquer_alerte_lue():
    code = session["member_code"]
    try:
        conn = get_conn()
        # Ne pas permettre de fermer si abonnement expiré ou expire dans 3 jours
        from datetime import datetime
        rows = conn.run("SELECT date_fin FROM members WHERE code=:c", c=code)
        if rows and rows[0][0]:
            delta = (rows[0][0] - datetime.now()).days
            if delta <= 3:  # expire dans 3 jours ou déjà expiré → bannière non fermable
                conn.close()
                return jsonify({"ok": False, "locked": True})
        conn.run("UPDATE members SET alerte_lue=TRUE WHERE code=:c", c=code)
        conn.close()
        return jsonify({"ok": True})
    except:
        return jsonify({"ok": False})

@app.route("/marquer-notif-lue", methods=["POST"])
@login_required
def marquer_notif_lue():
    code = session["member_code"]
    try:
        conn = get_conn()
        conn.run("UPDATE members SET notif_lue=TRUE, notif_message='', notif_type='' WHERE code=:c", c=code)
        conn.close()
        return jsonify({"ok": True})
    except:
        return jsonify({"ok": False})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/inscription", methods=["GET","POST"])
def inscription():
    if request.method == "GET":
        return render_template("inscription.html")
    data = request.get_json()
    prenom    = data.get("prenom","").strip()
    nom_fam   = data.get("nom","").strip()
    capital   = data.get("capital","").strip()
    email     = data.get("email","").strip()
    telephone = data.get("telephone","").strip()
    telegram  = data.get("telegram","").strip()
    plateforme= data.get("plateforme","MT4")
    serveur   = data.get("serveur","PUPrime-Live")
    mt_login  = data.get("mt_login","").strip()
    mt_pass   = data.get("mt_password","").strip()
    if not all([prenom, nom_fam, capital, email, telephone, mt_login, mt_pass]):
        return jsonify({"ok": False, "error": "Tous les champs sont obligatoires."})
    nom_complet = f"{prenom} {nom_fam}"
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    try:
        conn = get_conn()
        parrain_ref = data.get("parrain_code","").strip().upper()
        # Insérer sans parrain_code d'abord (colonne peut ne pas exister encore)
        conn.run(
            "INSERT INTO members (code,nom,capital,email,telephone,telegram,params,historique) VALUES (:c,:n,:cap,:e,:t,:tg,:p,:h)",
            c=code, n=nom_complet, cap=capital, e=email, t=telephone, tg=telegram,
            p=json.dumps({**default_params(), "mt_login": mt_login, "mt_password": mt_pass, "serveur": serveur, "plateforme": plateforme}), h=json.dumps([])
        )
        # Essayer de mettre à jour parrain_code si la colonne existe
        if parrain_ref:
            try:
                conn.run("UPDATE members SET parrain_code=:pr WHERE code=:c", pr=parrain_ref, c=code)
            except: pass
        # Créditer le parrain si code valide
        if parrain_ref:
            try:
                parrain_rows = conn.run("SELECT code, nom, filleuls_count, gains_parrainage FROM members WHERE code=:c AND actif=TRUE", c=parrain_ref)
                if parrain_rows:
                    p_code = parrain_rows[0][0]
                    p_nom  = parrain_rows[0][1]
                    p_fill = (parrain_rows[0][2] or 0) + 1
                    p_gains= (parrain_rows[0][3] or 0) + 50
                    conn.run("UPDATE members SET filleuls_count=:f, gains_parrainage=:g WHERE code=:c",
                             f=p_fill, g=p_gains, c=p_code)
                    # Notif Telegram au parrain
                    send_telegram(
                        f"🎉 *Nouveau filleul !*\n\n"
                        f"👤 *{p_nom}* — tu viens de parrainer *{nom_complet}* !\n"
                        f"💰 +50€ ajoutés à tes gains\n"
                        f"📊 Total filleuls : *{p_fill}* | Gains cumulés : *{p_gains}€*\n\n"
                        + (f"🥉 *Palier Bronze atteint ! +250€ bonus !*" if p_fill == 5 else
                           f"🥈 *Statut AMBASSADOR atteint ! +1000€ bonus !*" if p_fill == 10 else
                           f"🥇 *Statut ELITE atteint ! +2000€ + Voyage Dubai !*" if p_fill == 20 else "")
                    )
            except: pass
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    tg_line = f"  Telegram : {telegram}\n" if telegram else ""
    set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
    notif = (
        f"🆕 *NOUVELLE INSCRIPTION BECTANSE AUTO*\n\n"
        f"👤 *{nom_complet}*\n💰 Capital : *{capital}*\n🔑 Code : `{code}`\n\n"
        f"📞 *CONTACT*\n  Email : `{email}`\n  Tél : `{telephone}`\n{tg_line}\n"
        f"📊 *MT4/MT5*\n  Plateforme : *{plateforme}*\n  Serveur : *{serveur}*\n"
        f"  Login : `{mt_login}`\n  MDP investisseur : `{mt_pass}`\n\n"
        f"⚡ *ACTION REQUISE* — Connecter sur notre système"
    )
    markup = {"inline_keyboard":[[{"text":"📅 Définir les dates d'abonnement","url":set_dates_url}]]}
    send_telegram(notif, reply_markup=markup)
    try:
        email_bienvenue_membre(prenom, email, code)
    except Exception as e:
        app.logger.error("bienvenue: %s", e)
    try:
        result = sync_brevo_member_contact(email)
        if not result.get("ok"):
            app.logger.error("sync contact inscription %s: %s", email, result.get("error"))
    except Exception as e:
        app.logger.error("sync contact inscription %s: %s", email, e)
    return jsonify({"ok": True, "code": code})

@app.route("/admin/api/brevo/sync-members", methods=["POST"])
def admin_sync_brevo_members():
    """Rattrape tous les emails membres existants vers Brevo."""
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Interdit"}), 403
    conn = get_conn()
    rows = conn.run("""SELECT DISTINCT LOWER(TRIM(email)) FROM members
                        WHERE email IS NOT NULL AND TRIM(email) <> ''""")
    conn.close()
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda row: sync_brevo_member_contact(row[0]).get("ok", False), rows
        ))
    synced = sum(results)
    failed = len(rows) - synced
    return jsonify({"ok": failed == 0, "total": len(rows),
                    "synced": synced, "failed": failed})

@app.route("/confirm/<code>")
def confirm_params(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;font-family:Arial;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, historique FROM members WHERE code=:c", c=code)
        if not rows:
            return "<h2 style='padding:40px;font-family:Arial'>❌ Membre introuvable</h2>"
        nom = rows[0][0]
        # Mettre à jour le dernier statut en_attente → applique
        hist = json.loads(rows[0][1]) if rows[0][1] else []
        for i in range(len(hist)-1, -1, -1):
            if hist[i].get("statut") == "en_attente":
                hist[i]["statut"] = "applique"
                break
        # Notifier le membre dans son espace
        conn.run("""UPDATE members SET historique=:h,
            notif_type='resultat',
            notif_message='✅ Tes paramètres ont été appliqués sur le système Bectanse AUTO.',
            notif_lue=FALSE WHERE code=:c""",
            h=json.dumps(hist), c=code)
        conn.close()
        return """<html><head><meta charset='utf-8'>
        <style>body{font-family:Arial;background:#0A0A0F;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
        .box{background:#111827;border:1px solid rgba(5,150,105,0.4);border-radius:16px;padding:40px;text-align:center;max-width:400px}
        .icon{font-size:48px;margin-bottom:16px}.title{font-size:22px;font-weight:700;color:#6EE7B7;margin-bottom:8px}
        .desc{color:rgba(255,255,255,0.5);font-size:14px;line-height:1.6}</style></head>
        <body><div class='box'><div class='icon'>✅</div>
        <div class='title'>Paramètres appliqués</div>
        <div class='desc'>Les paramètres de <strong style='color:#fff'>""" + nom + """</strong> (<code style='color:#C4B5FD'>""" + code + """</code>) ont été marqués comme appliqués.<br><br>Le membre a reçu une notification dans son espace.</div>
        </div></body></html>"""
    except Exception as e:
        return f"<h2 style='padding:40px;font-family:Arial;color:red'>❌ Erreur: {e}</h2>"


@app.route("/problem/<code>")
def problem_params(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;font-family:Arial;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, historique FROM members WHERE code=:c", c=code)
        if not rows:
            return "<h2 style='padding:40px;font-family:Arial'>❌ Membre introuvable</h2>"
        nom = rows[0][0]
        # Mettre à jour le dernier statut en_attente → probleme
        hist = json.loads(rows[0][1]) if rows[0][1] else []
        for i in range(len(hist)-1, -1, -1):
            if hist[i].get("statut") == "en_attente":
                hist[i]["statut"] = "probleme"
                break
        # Notifier le membre
        conn.run("""UPDATE members SET historique=:h,
            notif_type='alerte',
            notif_message='⚠️ Un problème a été détecté sur ta demande. Notre équipe va te contacter sur WhatsApp.',
            notif_lue=FALSE WHERE code=:c""",
            h=json.dumps(hist), c=code)
        conn.close()
        return """<html><head><meta charset='utf-8'>
        <style>body{font-family:Arial;background:#0A0A0F;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
        .box{background:#111827;border:1px solid rgba(220,38,38,0.4);border-radius:16px;padding:40px;text-align:center;max-width:400px}
        .icon{font-size:48px;margin-bottom:16px}.title{font-size:22px;font-weight:700;color:#FCA5A5;margin-bottom:8px}
        .desc{color:rgba(255,255,255,0.5);font-size:14px;line-height:1.6}</style></head>
        <body><div class='box'><div class='icon'>⚠️</div>
        <div class='title'>Problème signalé</div>
        <div class='desc'>La demande de <strong style='color:#fff'>""" + nom + """</strong> (<code style='color:#C4B5FD'>""" + code + """</code>) a été marquée avec un problème.<br><br>Le membre a reçu une notification et sera contacté sur WhatsApp.</div>
        </div></body></html>"""
    except Exception as e:
        return f"<h2 style='padding:40px;font-family:Arial;color:red'>❌ Erreur: {e}</h2>"


@app.route("/set-dates/<code>", methods=["GET","POST"])
def set_dates(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, capital FROM members WHERE code=:c", c=code)
        conn.close()
        if not rows: return "<h2 style='padding:40px'>❌ Introuvable</h2>"
        nom, capital = rows[0]
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"
    if request.method == "POST":
        debut = request.form.get("debut","")
        duree = int(request.form.get("duree",30))
        try:
            date_debut = datetime.strptime(debut, "%Y-%m-%d")
            date_fin_new = date_debut + timedelta(days=duree)
            conn = get_conn()
            conn.run("UPDATE members SET date_souscription=:ds, date_fin=:df WHERE code=:c",
                     ds=date_debut, df=date_fin_new, c=code)
            conn.close()
            send_telegram(f"✅ *Dates définies*\n\n👤 *{nom}*\n📅 Début : {date_debut.strftime('%d/%m/%Y')}\n📅 Fin : {date_fin_new.strftime('%d/%m/%Y')} ({duree}j)")
            return f"<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center'><h1 style='color:#059669'>✅ Dates définies !</h1><p><strong>{nom}</strong></p><p>Fin : <strong>{date_fin_new.strftime('%d/%m/%Y')}</strong></p></body></html>"
        except Exception as e:
            return f"<h2>Erreur: {e}</h2>"
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""<html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <style>body{{font-family:sans-serif;background:#0d0d0d;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
    .card{{background:#1F2937;border:1px solid rgba(91,33,182,0.3);border-radius:16px;padding:36px 32px;width:100%;max-width:400px}}
    h2{{color:#F59E0B;margin-bottom:4px}}label{{display:block;font-size:11px;font-weight:700;color:rgba(255,255,255,0.4);margin:16px 0 6px;text-transform:uppercase}}
    input{{width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(91,33,182,0.3);border-radius:8px;padding:11px 14px;color:#fff;font-size:14px;margin-bottom:4px;box-sizing:border-box;outline:none}}
    .pills{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}}.pill{{padding:7px 14px;border-radius:20px;border:1px solid rgba(91,33,182,0.3);background:rgba(255,255,255,0.04);color:rgba(255,255,255,0.5);cursor:pointer;font-size:13px;font-weight:600}}
    .pill.active,.pill:hover{{background:#5B21B6;border-color:#5B21B6;color:#fff}}
    button{{width:100%;background:#5B21B6;color:#fff;border:none;border-radius:10px;padding:14px;font-size:15px;font-weight:700;cursor:pointer;margin-top:16px}}</style></head>
    <body><div class='card'><h2>📅 Abonnement</h2><p style='color:rgba(255,255,255,0.4);font-size:13px;margin-bottom:20px'>👤 {nom} — 💰 {capital}</p>
    <form method='POST'><label>Date de début</label><input type='date' name='debut' value='{today}' required>
    <label>Durée</label><div class='pills'>
    <div class='pill' onclick="setD(30,this)">30j</div>
    <div class='pill active' onclick="setD(30,this)">1 mois</div>
    <div class='pill' onclick="setD(60,this)">2 mois</div>
    <div class='pill' onclick="setD(90,this)">3 mois</div>
    <div class='pill' onclick="setD(180,this)">6 mois</div>
    <div class='pill' onclick="setD(365,this)">1 an</div></div>
    <label>Jours exact</label><input type='number' name='duree' id='dur' value='30' min='1' max='400' required>
    <button type='submit'>✅ Enregistrer</button></form></div>
    <script>function setD(n,el){{document.getElementById('dur').value=n;document.querySelectorAll('.pill').forEach(p=>p.classList.remove('active'));el.classList.add('active');}}</script>
    </body></html>"""

@app.route("/desactiver/<code>")
def desactiver_membre(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c", c=code)
        nom = rows[0][0] if rows else "Membre"
        conn.run("UPDATE members SET actif=FALSE, copy_actif=FALSE WHERE code=:c", c=code)
        conn.close()
        send_telegram(f"⛔ *{nom}* désactivé.")
        return f"<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center'><h1 style='color:#DC2626'>⛔ Désactivé</h1><p>{nom}</p></body></html>"
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

@app.route("/admin/add", methods=["POST"])
def admin_add():
    if request.headers.get("X-Admin-Key","") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    data = request.get_json()
    nom = data.get("nom","")
    capital = data.get("capital","")
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    try:
        conn = get_conn()
        conn.run("INSERT INTO members (code,nom,capital,params,historique) VALUES (:c,:n,:cap,:p,:h)",
                 c=code, n=nom, cap=capital, p=json.dumps({**default_params(), "mt_login": mt_login, "mt_password": mt_pass, "serveur": serveur, "plateforme": plateforme}), h=json.dumps([]))
        conn.close()
        send_telegram(f"✅ *Nouveau membre*\n\n👤 *{nom}* | 💰 *{capital}*\n🔑 `{code}`")
        return jsonify({"ok": True, "code": code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── BOT TELEGRAM WEBHOOK ──────────────────────────────────────────────────────

@app.route("/bot-webhook", methods=["POST"])
def bot_webhook():
    """Reçoit les messages/commandes du bot Telegram"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": True})

        # Message normal
        message = data.get("message", {})
        callback = data.get("callback_query", {})

        if message:
            chat_id = str(message.get("chat", {}).get("id", ""))
            text    = message.get("text", "").strip()

            # Sécurité — uniquement l'admin
            if chat_id != ADMIN_ID:
                return jsonify({"ok": True})

            if text == "/membres" or text == "/liste":
                handle_liste_membres(chat_id)

            elif text == "/stats":
                handle_stats(chat_id)

            elif text.startswith("/supprimer "):
                code = text.replace("/supprimer ", "").strip().upper()
                handle_supprimer(chat_id, code)

            elif text == "/start" or text == "/aide":
                handle_aide(chat_id)

            elif text.startswith("/alerte "):
                contenu = text[8:].strip()
                handle_notif_globale(chat_id, contenu, "alerte")

            elif text.startswith("/message "):
                contenu = text[9:].strip()
                # /message BCT-XXXXXXXX texte = message individuel.
                # /message texte = annonce globale (compatibilité historique).
                parts = contenu.split(" ", 1)
                if len(parts) == 2 and parts[0].upper().startswith("BCT-"):
                    handle_notif_individuelle(chat_id, parts[0].upper(), parts[1].strip())
                else:
                    handle_notif_globale(chat_id, contenu, "message")

            elif text.startswith("/resultat "):
                contenu = text[10:].strip()
                handle_notif_globale(chat_id, contenu, "resultat")

            elif text.startswith("/maintenance "):
                contenu = text[13:].strip()
                handle_notif_globale(chat_id, contenu, "maintenance")

            elif text.startswith("/prolonger "):
                # /prolonger BCT-XXXXXXXX 30
                parts = text[11:].strip().split(" ", 1)
                if len(parts) == 2:
                    code_p = parts[0].strip().upper()
                    try:
                        jours = int(parts[1].strip())
                        handle_prolonger(chat_id, code_p, jours)
                    except ValueError:
                        bot_send(chat_id, "❌ Format : /prolonger BCT-XXXXXXXX 30")
                else:
                    bot_send(chat_id, "❌ Format : /prolonger BCT-XXXXXXXX 30")

            elif text.startswith("/msg "):
                # /msg BCT-XXXXXXXX texte du message
                parts = text[5:].strip().split(" ", 1)
                if len(parts) == 2:
                    code_dest = parts[0].strip().upper()
                    contenu = parts[1].strip()
                    handle_notif_individuelle(chat_id, code_dest, contenu)
                else:
                    bot_send(chat_id, "❌ Format : /msg BCT-XXXXXXXX Ton message ici")

        elif callback:
            # Bouton inline cliqué
            chat_id   = str(callback.get("from", {}).get("id", ""))
            cb_data   = callback.get("data", "")
            cb_id     = callback.get("id", "")

            if chat_id != ADMIN_ID:
                return jsonify({"ok": True})

            if cb_data.startswith("del_confirm_"):
                code = cb_data.replace("del_confirm_", "")
                handle_supprimer(chat_id, code, confirmed=True)
                # Répondre au callback
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "✅ Membre supprimé"}, timeout=5)

            elif cb_data.startswith("del_cancel_"):
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "❌ Annulé"}, timeout=5)

            elif cb_data == "liste_membres":
                handle_liste_membres(chat_id)
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb_id}, timeout=5)

    except Exception as e:
        app.logger.error(f"bot_webhook: {e}")

    return jsonify({"ok": True})


def bot_send(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload, timeout=5)
    except: pass


def handle_prolonger(chat_id, code, jours):
    """Prolonge l'abonnement d'un membre de X jours"""
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, date_fin FROM members WHERE code=:c AND actif=TRUE", c=code)
        if not rows:
            conn.close()
            bot_send(chat_id, f"❌ Membre `{code}` introuvable ou inactif.")
            return
        nom = rows[0][0]
        date_fin_actuelle = rows[0][1]

        # Si pas de date_fin, partir d'aujourd'hui
        from datetime import datetime, timedelta
        now = datetime.now()
        if date_fin_actuelle and date_fin_actuelle > now:
            nouvelle_date = date_fin_actuelle + timedelta(days=jours)
        else:
            nouvelle_date = now + timedelta(days=jours)

        conn.run("UPDATE members SET date_fin=:df WHERE code=:c", df=nouvelle_date, c=code)
        conn.close()

        bot_send(chat_id,
            f"✅ *Accès prolongé !*\n\n"
            f"👤 *{nom}*\n"
            f"🔑 `{code}`\n"
            f"⏱️ +{jours} jours ajoutés\n"
            f"📅 Nouvelle expiration : *{nouvelle_date.strftime('%d/%m/%Y')}*"
        )
    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def upload_to_cloudinary(file_bytes, resource_type="image", filename="file"):
    """Upload fichier sur Cloudinary, retourne URL publique permanente."""
    try:
        import hashlib, time, io
        timestamp = str(int(time.time()))
        to_sign = f"timestamp={timestamp}{CLOUDINARY_SECRET}"
        signature = hashlib.sha1(to_sign.encode()).hexdigest()
        r = requests.post(
            f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD}/{resource_type}/upload",
            files={"file": (filename, io.BytesIO(file_bytes))},
            data={"api_key": CLOUDINARY_KEY, "timestamp": timestamp, "signature": signature},
            timeout=30
        )
        return r.json().get("secure_url", "")
    except Exception as e:
        app.logger.error(f"Cloudinary upload: {e}")
        return ""

def tg_download_and_upload(file_id, bot_token, resource_type="image"):
    """Télécharge depuis Telegram et upload sur Cloudinary."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id}, timeout=10
        )
        file_path = r.json()["result"]["file_path"]
        file_bytes = requests.get(
            f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
            timeout=30
        ).content
        ext = file_path.split(".")[-1] if "." in file_path else "jpg"
        return upload_to_cloudinary(file_bytes, resource_type, f"bectanse_{file_id}.{ext}")
    except Exception as e:
        app.logger.error(f"tg_download_and_upload: {e}")
        return ""


def handle_notif_globale(chat_id, contenu, notif_type):
    """Envoie une notification à TOUS les membres actifs"""
    if not contenu:
        bot_send(chat_id, "❌ Message vide.")
        return
    try:
        enforce_member_access_state()
        conn = get_conn()
        rows = conn.run("""SELECT COUNT(*) FROM members
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")
        total = rows[0][0] if rows else 0
        conn.run("""UPDATE members 
                    SET notif_type=:t, notif_message=:m, notif_lue=FALSE 
                    WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""",
                 t=notif_type, m=contenu)
        conn.close()
        icons = {"alerte": "🔴", "message": "💜", "resultat": "🟢", "maintenance": "🔧"}
        icon = icons.get(notif_type, "📢")
        titles = {"alerte": "🔴 Alerte Bectanse", "message": "💜 Message Bectanse",
                  "resultat": "🟢 Résultat Bectanse", "maintenance": "🔧 Maintenance Bectanse"}
        push_result = send_push_to_all(titles.get(notif_type, "Bectanse AUTO"),
                                       contenu, "/accueil")
        bot_send(chat_id, 
            f"{icon} *Notification globale enregistrée pour {total} membres*\n\n"
            f"Type : `{notif_type}`\n"
            f"Message : {contenu}\n\n"
            f"📲 Appareils livrés : *{push_result['delivered']}*\n"
            f"👥 Membres joignables : *{push_result['members']}*\n"
            f"⚠️ Échecs : *{push_result['failed']}*")
    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def handle_notif_individuelle(chat_id, code_dest, contenu):
    """Envoie une notification privée à UN membre spécifique"""
    if not contenu:
        bot_send(chat_id, "❌ Message vide.")
        return
    try:
        conn = get_conn()
        rows = conn.run("""SELECT nom FROM members WHERE code=:c AND actif=TRUE
            AND (date_fin IS NULL OR date_fin > NOW())""", c=code_dest)
        if not rows:
            conn.close()
            bot_send(chat_id, f"❌ Membre `{code_dest}` introuvable ou inactif.")
            return
        nom = rows[0][0]
        conn.run("""UPDATE members 
                    SET notif_type='individuelle', notif_message=:m, notif_lue=FALSE 
                    WHERE code=:c""", m=contenu, c=code_dest)
        conn.close()
        push_result = send_push_to_member(code_dest, "💬 Message personnel", contenu, "/accueil")
        if push_result["delivered"]:
            delivery_line = f"📲 Push livré à *{push_result['delivered']} appareil(s)*"
        elif push_result["registered"]:
            delivery_line = "⚠️ Appareil trouvé, mais Apple/Android a refusé la livraison. Le membre doit réactiver les notifications."
        else:
            delivery_line = "⚠️ Aucun téléphone abonné. Le message reste visible dans son espace membre."
        bot_send(chat_id,
            f"✅ *Message enregistré pour {nom}*\n\n"
            f"Code : `{code_dest}`\n"
            f"Message : {contenu}\n\n"
            f"{delivery_line}")
    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def handle_aide(chat_id):
    msg = (
        "🤖 *Bectanse AUTO — Bot Admin*\n\n"
        "📋 *Commandes disponibles :*\n\n"
        "📊 *Gestion membres*\n"
        "/membres — Liste tous les membres\n"
        "/stats — Statistiques générales\n"
        "/supprimer BCT-XXXXXXXX — Supprimer un membre\n"
        "/prolonger BCT-XXXXXXXX 30 — Prolonger l'accès de X jours\n\n"
        "📣 *Notifications globales (tous les membres)*\n"
        "/alerte TEXTE — Bannière rouge urgente 🔴\n"
        "/message TEXTE — Annonce violette globale 💜\n"
        "/resultat TEXTE — Performance verte 🟢\n"
        "/maintenance TEXTE — Bannière maintenance 🔧\n\n"
        "💬 *Message individuel*\n"
        "/msg BCT-XXXXXXXX TEXTE — Notif privée à un membre\n"
        "/message BCT-XXXXXXXX TEXTE — Fonctionne aussi en individuel\n\n"
        "💡 Exemple : /alerte BUY XAUUSD — Entrée 2345 TP 2360"
    )
    markup = {"inline_keyboard": [[
        {"text": "📋 Voir les membres", "callback_data": "liste_membres"}
    ]]}
    bot_send(chat_id, msg, markup)


def handle_stats(chat_id):
    try:
        enforce_member_access_state()
        conn = get_conn()
        total    = conn.run("SELECT COUNT(*) FROM members")[0][0]
        actifs   = conn.run("""SELECT COUNT(*) FROM members WHERE actif=TRUE
            AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        expires  = conn.run("SELECT COUNT(*) FROM members WHERE date_fin <= NOW()")[0][0]
        copy_on  = conn.run("""SELECT COUNT(*) FROM members WHERE copy_actif=TRUE
            AND actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        conn.close()
        msg = (
            f"📊 *STATISTIQUES BECTANSE AUTO*\n\n"
            f"👥 Total membres : *{total}*\n"
            f"✅ Membres actifs : *{actifs}*\n"
            f"⏸️ Copy trading ON : *{copy_on}*\n"
            f"⚠️ Abonnements expirés : *{expires}*\n"
        )
        bot_send(chat_id, msg)
    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def handle_liste_membres(chat_id):
    try:
        conn = get_conn()
        rows = conn.run("""
            SELECT code, nom, capital, actif, copy_actif, date_fin
            FROM members
            ORDER BY created_at DESC
            LIMIT 20
        """)
        conn.close()

        if not rows:
            bot_send(chat_id, "Aucun membre trouvé.")
            return

        # Envoyer par blocs de 5 pour éviter les messages trop longs
        blocs = [rows[i:i+5] for i in range(0, len(rows), 5)]
        for i, bloc in enumerate(blocs):
            buttons = []
            msg = f"📋 *MEMBRES ({i*5+1}-{i*5+len(bloc)}/{len(rows)})*\n\n"
            for row in bloc:
                code, nom, capital, actif, copy_actif, date_fin = row
                statut = "✅" if actif else "❌"
                copy   = "🟢" if copy_actif else "🔴"
                df     = date_fin.strftime('%d/%m/%Y') if date_fin else "—"
                msg += f"{statut} *{nom}*\n"
                msg += f"   `{code}` | 💰{capital} | {copy} | 📅{df}\n\n"
                buttons.append([{"text": f"🗑️ Suppr. {nom.split()[0]}", 
                                  "callback_data": f"del_confirm_{code}"}])
            
            markup = {"inline_keyboard": buttons}
            bot_send(chat_id, msg, markup)

    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def handle_supprimer(chat_id, code, confirmed=False):
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, capital FROM members WHERE code=:c", c=code)
        if not rows:
            bot_send(chat_id, f"❌ Membre `{code}` introuvable.")
            conn.close()
            return
        nom, capital = rows[0]

        if not confirmed:
            # Demander confirmation
            msg = (
                f"⚠️ *Supprimer ce membre ?*\n\n"
                f"👤 *{nom}*\n"
                f"💰 Capital : {capital}\n"
                f"🔑 Code : `{code}`\n\n"
                f"Cette action est *irréversible*."
            )
            markup = {"inline_keyboard": [[
                {"text": "🗑️ OUI, supprimer", "callback_data": f"del_confirm_{code}"},
                {"text": "❌ Annuler", "callback_data": f"del_cancel_{code}"}
            ]]}
            bot_send(chat_id, msg, markup)
        else:
            # Supprimer
            conn.run("DELETE FROM members WHERE code=:c", c=code)
            conn.close()
            bot_send(chat_id, f"✅ *{nom}* (`{code}`) supprimé avec succès.")

    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def register_webhook():
    """Enregistre le webhook Telegram au démarrage"""
    try:
        webhook_url = "https://bectanse-auto.up.railway.app/bot-webhook"
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": webhook_url},
            timeout=10
        )
    except: pass


# ── EMAILS RELANCE AUTOMATIQUES ─────────────────────────────────────────────

STRIPE_LINKS = [
    ("1 mois", "500€",   "https://buy.stripe.com/4gMeVdaKYctK7xN8ksgfu0p"),
    ("3 mois", "1 000€", "https://buy.stripe.com/bJefZh06k8du05leIQgfu0n"),
    ("6 mois", "2 500€", "https://buy.stripe.com/00w28q84g5s1csDgmegA804"),
    ("1 an",   "4 000€", "https://buy.stripe.com/bJecN498kbQp64f7PIgA803"),
]

def send_email_relance(member, jours_restants):
    """Envoie un email de relance au membre."""
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        email_dest = member.get("email", "")
        if not email_dest:
            return False

        prenom = member.get("nom", "").split()[0]
        code   = member.get("code", "")

        if jours_restants > 0:
            sujet = f"⚠️ Ton accès Bectanse AUTO expire dans {jours_restants} jour{'s' if jours_restants > 1 else ''}"
            intro = f"Ton abonnement Bectanse AUTO expire dans <strong>{jours_restants} jour{'s' if jours_restants > 1 else ''}</strong>."
            urgence = "🔴 Action requise" if jours_restants <= 3 else "⚠️ Rappel important"
        else:
            jours_apres = abs(jours_restants)
            sujet = f"🚨 Ton accès Bectanse AUTO a expiré il y a {jours_apres} jour{'s' if jours_apres > 1 else ''}"
            intro = f"Ton abonnement Bectanse AUTO a expiré. Tu n'as plus accès au copy trading automatique."
            urgence = "🚨 Accès suspendu"

        stripe_html = "".join([
            f'''<a href="{lien}" style="display:inline-block;margin:6px;padding:12px 20px;background:#5B21B6;color:#fff;text-decoration:none;border-radius:10px;font-weight:700;font-size:14px">{duree} — {prix}</a>'''
            for duree, prix, lien in STRIPE_LINKS
        ])

        html_body = f"""
        <div style="background:#0A0A0F;color:#fff;font-family:Arial,sans-serif;max-width:560px;margin:0 auto;border-radius:16px;overflow:hidden">
          <div style="background:linear-gradient(135deg,#5B21B6,#1e0a40);padding:28px 24px;text-align:center">
            <div style="font-size:24px;font-weight:900;letter-spacing:0.06em;color:#F59E0B">B€CTAN$€ AUTO</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.5);margin-top:4px;letter-spacing:0.2em;text-transform:uppercase">Copy Trading Automatique</div>
          </div>
          <div style="padding:28px 24px">
            <div style="font-size:12px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#F59E0B;margin-bottom:8px">{urgence}</div>
            <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:16px">Bonjour {prenom} 👋</div>
            <p style="color:rgba(255,255,255,0.7);line-height:1.7;margin-bottom:20px">{intro}<br><br>
            Pour continuer à bénéficier du copy trading automatique sur XAU/USD, renouvelle ton accès maintenant.</p>
            <div style="background:#111827;border:1px solid rgba(91,33,182,0.3);border-radius:12px;padding:20px;margin-bottom:20px;text-align:center">
              <div style="font-size:13px;color:rgba(255,255,255,0.5);margin-bottom:14px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase">Choisir ma formule</div>
              {stripe_html}
            </div>
            <p style="color:rgba(255,255,255,0.4);font-size:12px;line-height:1.6">
              Une question ? Contacte-nous sur WhatsApp : <a href="https://wa.me/436785328698" style="color:#F59E0B">+43 678 532 8698</a><br>
              Ton code d'accès : <strong style="color:#C4B5FD">{code}</strong>
            </p>
          </div>
          <div style="background:#111827;padding:16px 24px;text-align:center;border-top:1px solid rgba(255,255,255,0.05)">
            <a href="https://bectanse-auto.up.railway.app" style="color:#5B21B6;font-size:12px;text-decoration:none">bectanse-auto.up.railway.app</a>
          </div>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = sujet
        msg["From"]    = f"Bectanse AUTO <{GMAIL_USER}>"
        msg["To"]      = email_dest
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, email_dest, msg.as_string())

        app.logger.info(f"Email relance envoyé à {email_dest} ({jours_restants}j)")
        return True
    except Exception as e:
        app.logger.error(f"send_email_relance: {e}")
        return False




def init_demo_account():
    """Créer le compte demo BCT-DEMO2026 si absent."""
    try:
        conn = get_conn()
        existing = conn.run("SELECT code FROM members WHERE code='BCT-DEMO2026'")
        if not existing:
            conn.run(
                "INSERT INTO members (code,nom,capital,email,telephone,telegram,params,historique,actif,copy_actif) "
                "VALUES ('BCT-DEMO2026','Compte Demo','1000','demo@bectanse.com','','',\'{}\',\'[]\',TRUE,FALSE)"
            )
            app.logger.info("Compte demo BCT-DEMO2026 cree")
        conn.close()
    except Exception as e:
        app.logger.error("init_demo: %s", e)

# ── BREVO MEMBRES
def send_brevo_membre(to_email, to_name, subject, html_content, tag):
    import urllib.request as _ur, os as _os
    brevo_key = _os.environ.get("BREVO_KEY", "")
    if not brevo_key:
        app.logger.info("BREVO_KEY non definie — utilisation du SMTP Gmail")
        sent = send_email(to_email, subject, html_content)
        return {"ok": bool(sent), "message_id": "gmail-smtp" if sent else "",
                "error": "Echec SMTP Gmail" if not sent else ""}
    try:
        account_req = _ur.Request(
            "https://api.brevo.com/v3/account",
            headers={"api-key": brevo_key, "Accept": "application/json"}
        )
        with _ur.urlopen(account_req, timeout=10) as account_response:
            account = json.loads(account_response.read().decode("utf-8") or "{}")
        plans = account.get("plan", [])
        email_plan = next(
            (plan for plan in plans if plan.get("creditsType") == "sendLimit"),
            plans[0] if plans else {}
        )
        credits = email_plan.get("credits")
        if credits is not None and int(credits) <= 0:
            return {"ok": False, "error": "Crédits email Brevo insuffisants"}
        p = json.dumps({"sender":{"email":"lerisluketo@bectanse-academie.com","name":"Leris - Bectanse AUTO"},"to":[{"email":to_email,"name":to_name}],"subject":subject,"htmlContent":html_content,"tags":["bectanse-membre",tag]}).encode()
        r = _ur.Request("https://api.brevo.com/v3/smtp/email",data=p,headers={"api-key":brevo_key,"Content-Type":"application/json"})
        with _ur.urlopen(r,timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": payload.get("messageId", "")}
    except Exception as e:
        app.logger.error("Brevo: %s",e)
        return {"ok": False, "error": str(e)[:500]}

def sync_brevo_member_contact(email):
    """Crée ou actualise le contact dans la liste Membres Bectanse."""
    import urllib.request as _ur, urllib.error as _ue
    brevo_key = os.environ.get("BREVO_KEY", "")
    list_id = os.environ.get("BREVO_MEMBERS_LIST_ID", "")
    clean_email = (email or "").strip().lower()
    if not brevo_key or not list_id or "@" not in clean_email:
        return {"ok": False, "error": "Configuration ou email invalide"}
    try:
        payload_data = {"email": clean_email, "listIds": [int(list_id)],
                        "updateEnabled": True}
        prospect_list_id = os.environ.get("BREVO_PROSPECTS_LIST_ID", "")
        if prospect_list_id:
            payload_data["unlinkListIds"] = [int(prospect_list_id)]
        payload = json.dumps(payload_data).encode("utf-8")
        req = _ur.Request("https://api.brevo.com/v3/contacts", data=payload,
            headers={"api-key": brevo_key, "Content-Type": "application/json",
                     "Accept": "application/json"}, method="POST")
        with _ur.urlopen(req, timeout=10) as response:
            response.read()
        return {"ok": True}
    except _ue.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        app.logger.error("Brevo contact %s: HTTP %s %s", clean_email, error.code, detail)
        return {"ok": False, "error": f"HTTP {error.code}: {detail}"}
    except Exception as error:
        app.logger.error("Brevo contact %s: %s", clean_email, error)
        return {"ok": False, "error": str(error)[:500]}


_brevo_delivery_cache = {"checked_at": 0.0, "available": False}

def brevo_email_delivery_available():
    """Vérifie périodiquement que Brevo peut réellement envoyer des e-mails."""
    import urllib.request as _ur
    now = time.time()
    if now - _brevo_delivery_cache["checked_at"] < 300:
        return _brevo_delivery_cache["available"]
    available = False
    brevo_key = os.environ.get("BREVO_KEY", "")
    if brevo_key:
        try:
            req = _ur.Request("https://api.brevo.com/v3/account",
                headers={"api-key": brevo_key, "Accept": "application/json"})
            with _ur.urlopen(req, timeout=8) as response:
                account = json.loads(response.read().decode("utf-8") or "{}")
            plans = account.get("plan", [])
            send_plan = next((p for p in plans if p.get("creditsType") == "sendLimit"), {})
            available = int(send_plan.get("credits", 0) or 0) > 0
        except Exception as error:
            app.logger.warning("Verification credits Brevo: %s", error)
    _brevo_delivery_cache.update(checked_at=now, available=available)
    return available


def send_brevo_prospect_verification(to_email, to_name, confirmation_url):
    """Envoie uniquement l'e-mail technique de double opt-in prospect."""
    import urllib.request as _ur
    if not brevo_email_delivery_available():
        return {"ok": False, "error": "Service de confirmation temporairement indisponible"}
    html = ("<!doctype html><html><body style='margin:0;background:#090909;font-family:Arial,sans-serif;color:#fff'>"
        "<div style='max-width:580px;margin:0 auto;padding:28px 18px'>"
        "<div style='border:1px solid #332014;border-radius:22px;background:#111;padding:34px'>"
        "<p style='margin:0 0 10px;color:#ff6a00;font-weight:800;font-size:12px;letter-spacing:1.4px'>BECTANSE ACADÉMIE</p>"
        "<h1 style='margin:0 0 14px;font-size:27px'>Confirme ton adresse e-mail</h1>"
        "<p style='margin:0 0 24px;color:#b7b7b7;line-height:1.6'>Un clic suffit pour ouvrir immédiatement l’espace Explorer en lecture seule.</p>"
        "<a href='"+confirmation_url+"' style='display:block;text-align:center;background:#ff6a00;color:#fff;text-decoration:none;font-weight:800;padding:16px;border-radius:12px'>CONFIRMER ET EXPLORER →</a>"
        "<p style='margin:20px 0 0;color:#777;font-size:12px;line-height:1.5'>Ce lien expire dans 24 heures. Si tu n’as pas demandé cet accès, ignore simplement cet e-mail.</p>"
        "</div></div></body></html>")
    payload = json.dumps({
        "sender": {"email": "lerisluketo@bectanse-academie.com", "name": "Bectanse Académie"},
        "to": [{"email": to_email, "name": to_name}],
        "subject": "Confirme ton accès Explorer — Bectanse Académie",
        "htmlContent": html,
        "tags": ["bectanse-prospect", "verification-email"]
    }).encode("utf-8")
    try:
        req = _ur.Request("https://api.brevo.com/v3/smtp/email", data=payload,
            headers={"api-key": os.environ["BREVO_KEY"], "Content-Type": "application/json"},
            method="POST")
        with _ur.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": result.get("messageId", "")}
    except Exception as error:
        app.logger.error("Brevo verification prospect: %s", error)
        return {"ok": False, "error": str(error)[:500]}


def sync_brevo_prospect_contact(email, prenom="", source="Explorer"):
    """Ajoute un e-mail confirmé à la liste Prospects, jamais à la liste Membres."""
    import urllib.request as _ur, urllib.error as _ue
    brevo_key = os.environ.get("BREVO_KEY", "")
    list_id = os.environ.get("BREVO_PROSPECTS_LIST_ID", "")
    clean_email = (email or "").strip().lower()
    if not brevo_key or not list_id or "@" not in clean_email:
        return {"ok": False, "error": "Configuration ou email invalide"}
    payload = json.dumps({"email": clean_email,
        "attributes": {"PRENOM": (prenom or "").strip()},
        "listIds": [int(list_id)], "updateEnabled": True}).encode("utf-8")
    try:
        req = _ur.Request("https://api.brevo.com/v3/contacts", data=payload,
            headers={"api-key": brevo_key, "Content-Type": "application/json",
                     "Accept": "application/json"}, method="POST")
        with _ur.urlopen(req, timeout=10) as response:
            response.read()
        return {"ok": True}
    except _ue.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        app.logger.error("Brevo prospect %s: HTTP %s %s", clean_email, error.code, detail)
        return {"ok": False, "error": f"HTTP {error.code}: {detail}"}
    except Exception as error:
        return {"ok": False, "error": str(error)[:500]}

def email_bienvenue_membre(prenom, email, code_acces):
    html = ("<!DOCTYPE html><html><head><meta charset=UTF-8></head><body style='background:#0b0b0b;font-family:Arial;margin:0;padding:20px;'>"
        "<div style='max-width:600px;margin:0 auto;'>"
        "<div style='background:#FF6A00;padding:20px;border-radius:0 0 16px 16px;margin-bottom:8px;'>"
        "<span style='font-size:20px;font-weight:900;color:#fff;'>BectanseAUTO</span></div>"
        "<div style='background:#111;border-radius:16px;padding:32px;margin-bottom:8px;'>"
        "<p style='color:#FF6A00;font-size:11px;text-transform:uppercase;font-weight:700;margin:0 0 8px;'>Bienvenue dans la famille</p>"
        "<h1 style='color:#fff;font-size:26px;font-weight:900;margin:0 0 16px;'>Bienvenue, "+prenom+".<br>Ton acc&egrave;s est pr&ecirc;t.</h1>"
        "<p style='color:rgba(255,255,255,.7);font-size:15px;line-height:1.8;margin:0 0 20px;'>Tu fais maintenant partie des <strong style='color:#fff;'>5000+ membres</strong> Bectanse AUTO.</p>"
        "<div style='background:#0b0b0b;border:2px solid #FF6A00;border-radius:14px;padding:24px;text-align:center;margin-bottom:20px;'>"
        "<p style='color:#FF6A00;font-size:11px;text-transform:uppercase;font-weight:700;margin:0 0 10px;'>Ton code d'acc&egrave;s</p>"
        "<p style='color:#fff;font-size:32px;font-weight:900;letter-spacing:4px;font-family:monospace;margin:0 0 8px;'>"+code_acces+"</p>"
        "<p style='color:rgba(255,255,255,.4);font-size:12px;margin:0;'>Conserve ce code pour te connecter</p>"
        "</div>"
        "<p style='color:rgba(255,255,255,.7);font-size:14px;margin:0;'>Connecte-toi sur <strong style='color:#fff;'>acces.bectanse-academie.com</strong> et entre ce code.</p>"
        "</div>"
        "<div style='text-align:center;padding:20px 0;'>"
        "<a href='https://acces.bectanse-academie.com' style='background:#FF6A00;color:#fff;font-size:16px;font-weight:800;text-decoration:none;padding:16px 36px;border-radius:12px;'>Acc&eacute;der &agrave; mon espace &rarr;</a>"
        "</div>"
        "<p style='text-align:center;color:rgba(255,255,255,.2);font-size:11px;'>&copy; 2026 Bectanse Acad&eacute;mie &mdash; LERIS CORP FZCO, Dubai</p>"
        "</div></body></html>")
    send_brevo_membre(email, prenom, "Bienvenue "+prenom+" - Ton code Bectanse AUTO", html, "bienvenue")

RENEWAL_STAGE_CONTENT = {
    "j-7": ("Ton accès expire dans 7 jours", "Anticipe ton renouvellement", "Ton accès Bectanse AUTO arrive à échéance dans 7 jours. Renouvelle maintenant pour conserver ton espace et éviter toute interruption.", "#FF6A00"),
    "j-2": ("Plus que 48 h pour renouveler", "Ton accès expire dans 48 heures", "Il ne reste que deux jours avant la suspension de ton accès. Tu peux renouveler en quelques instants depuis ton espace membre.", "#FF6A00"),
    "j0": ("Ton accès expire aujourd’hui", "Dernier jour avant suspension", "Ton adhésion arrive à échéance aujourd’hui. Renouvelle maintenant pour maintenir la continuité de ton accès.", "#ef4444"),
    "expired-initial": ("Ton accès Bectanse AUTO est expiré", "Réactive ton accès", "Ton adhésion est arrivée à échéance. Ton espace et tes paramètres sont conservés : il te suffit de renouveler pour reprendre.", "#ef4444"),
    "expired-week-1": ("Ton espace est toujours prêt", "Tu peux reprendre quand tu veux", "Tes informations sont toujours conservées. Réactive ton adhésion pour retrouver ton espace Bectanse AUTO.", "#ef4444"),
    "expired-week-2": ("Besoin d’aide pour reprendre ?", "On t’accompagne pour la réactivation", "Si quelque chose bloque ton renouvellement, notre support peut t’aider. Sinon, tu peux réactiver directement depuis ton espace.", "#FF6A00"),
    "expired-week-3": ("Ton accès peut être réactivé", "Reprends là où tu t’étais arrêté", "Aucune nouvelle configuration n’est nécessaire : renouvelle ton adhésion et retrouve ton environnement membre.", "#FF6A00"),
    "expired-week-4": ("Dernier rappel de réactivation", "Dernière relance de cette séquence", "Nous clôturons cette série de rappels. Ton compte reste identifiable et tu peux renouveler ton accès dès que tu le souhaites.", "#ef4444"),
}


def email_relance_expiration(prenom, email, stage):
    if stage not in RENEWAL_STAGE_CONTENT:
        return {"ok": False, "error": "etape inconnue"}
    subject, titre, corps, couleur = RENEWAL_STAGE_CONTENT[stage]
    subject = f"{prenom}, {subject[:1].lower()}{subject[1:]}"
    html = ("<!DOCTYPE html><html><head><meta charset=UTF-8></head><body style='background:#0b0b0b;font-family:Arial;margin:0;padding:20px;'>"
        "<div style='max-width:600px;margin:0 auto;'>"
        "<div style='background:"+couleur+";padding:20px;border-radius:0 0 16px 16px;margin-bottom:8px;'>"
        "<span style='font-size:20px;font-weight:900;color:#fff;'>BectanseAUTO</span></div>"
        "<div style='background:#111;border-radius:16px;padding:32px;margin-bottom:8px;'>"
        "<h1 style='color:#fff;font-size:26px;font-weight:900;margin:0 0 16px;'>"+titre+"</h1>"
        "<p style='color:rgba(255,255,255,.7);font-size:15px;line-height:1.8;margin:0;'>"+corps+"</p>"
        "</div>"
        "<div style='text-align:center;padding:20px 0;'>"
        "<a href='https://acces.bectanse-academie.com/offres' style='background:#FF6A00;color:#fff;font-size:16px;font-weight:800;text-decoration:none;padding:16px 36px;border-radius:12px;'>Renouveler mon acc&egrave;s &rarr;</a>"
        "</div>"
        "<p style='text-align:center;color:rgba(255,255,255,.45);font-size:12px;'>Besoin d’aide ? <a href='https://t.me/m/PAt88QgeZDhk' style='color:#FF6A00;'>Contacter le support</a></p>"
        "<p style='text-align:center;color:rgba(255,255,255,.2);font-size:11px;'>&copy; 2026 Bectanse Acad&eacute;mie</p>"
        "</div></body></html>")
    return send_brevo_membre(email, prenom, subject, html, f"renouvellement-{stage}")


def _renewal_stage_for_member(conn, code, expiry_date, days_until):
    """Retourne au maximum une étape due. Les anciens expirés entrent immédiatement."""
    if days_until in (7, 2, 0):
        return {7: "j-7", 2: "j-2", 0: "j0"}[days_until]
    if days_until > 0:
        return None
    rows = conn.run(
        """SELECT stage, sent_at FROM renewal_email_log
           WHERE member_code=:code AND expiry_date=:expiry AND status='sent'
           ORDER BY sent_at""",
        code=code, expiry=expiry_date
    )
    sent = {row[0]: row[1] for row in rows}
    if "j0" not in sent and "expired-initial" not in sent:
        return "expired-initial"
    sequence = ["expired-week-1", "expired-week-2", "expired-week-3", "expired-week-4"]
    previous_at = sent.get("expired-initial") or sent.get("j0")
    for stage in sequence:
        if stage in sent:
            previous_at = sent[stage]
            continue
        if previous_at and (_paris_now().replace(tzinfo=None) - previous_at).days >= 7:
            return stage
        return None
    return None


def _send_claimed_renewal_email(code, nom, email, expiry_date, stage):
    """Réserve l'étape en base avant l'appel Brevo pour bloquer tout doublon."""
    conn = get_conn()
    claimed = conn.run(
        """INSERT INTO renewal_email_log
           (member_code, expiry_date, stage, recipient_email, status)
           VALUES (:code, :expiry, :stage, :email, 'pending')
           ON CONFLICT (member_code, expiry_date, stage) DO UPDATE
           SET recipient_email=EXCLUDED.recipient_email, status='pending',
               error='', created_at=NOW()
           WHERE renewal_email_log.status='failed'
           RETURNING stage""",
        code=code, expiry=expiry_date, stage=stage, email=email
    )
    conn.close()
    if not claimed:
        return False
    prenom = (nom or "Bonjour").split()[0]
    result = email_relance_expiration(prenom, email, stage)
    conn = get_conn()
    if result.get("ok"):
        conn.run(
            """UPDATE renewal_email_log SET status='sent', sent_at=NOW(),
               provider_message_id=:message_id, error=''
               WHERE member_code=:code AND expiry_date=:expiry AND stage=:stage""",
            message_id=result.get("message_id", ""), code=code,
            expiry=expiry_date, stage=stage
        )
    else:
        conn.run(
            """UPDATE renewal_email_log SET status='failed', error=:error
               WHERE member_code=:code AND expiry_date=:expiry AND stage=:stage""",
            error=result.get("error", "Erreur Brevo")[:500], code=code,
            expiry=expiry_date, stage=stage
        )
    conn.close()
    return bool(result.get("ok"))

def job_relances_quotidiennes():
    """Relances e-mail idempotentes + rappels Telegram admin, chaque jour à 9 h."""
    try:
        from datetime import datetime
        enforce_member_access_state()
        conn = get_conn()
        membres = conn.run("""
            SELECT code, nom, email, date_fin, capital
            FROM members WHERE date_fin IS NOT NULL
        """)
        conn.close()

        today = _paris_now().date()
        emails_sent = 0
        j7_list = []
        j2_list = []
        j0_list = []

        for code, nom, email, date_fin, capital in membres:
            if not date_fin: continue
            expiry_date = date_fin.date() if hasattr(date_fin, "date") else date_fin
            delta = (expiry_date - today).days

            # ── EMAILS : J-7, J-2, J0 puis une fois/semaine pendant 4 semaines.
            if email:
                try:
                    conn2 = get_conn()
                    stage = _renewal_stage_for_member(
                        conn2, code, expiry_date, delta
                    )
                    conn2.close()
                    if stage and _send_claimed_renewal_email(
                        code, nom, email.strip(), expiry_date, stage
                    ):
                        emails_sent += 1
                except Exception as email_error:
                    app.logger.error(
                        "relance email %s: %s", code, email_error
                    )

            # ── BANNIÈRE APP avant expiration
            if delta in [7, 2, 0]:
                try:
                    conn2 = get_conn()
                    conn2.run("""UPDATE members SET notif_type='alerte',
                        notif_message=:m, notif_lue=FALSE WHERE code=:c""",
                        m=("🚨 Ton abonnement expire aujourd'hui — Renouvelle maintenant !"
                           if delta == 0 else
                           f"⚠️ Ton abonnement expire dans {delta} jours — Renouvelle maintenant !"),
                        c=code)
                    conn2.close()
                except Exception as notif_error:
                    app.logger.error("banniere expiration %s: %s", code, notif_error)

            # ── LISTES POUR RAPPELS TELEGRAM ADMIN
            if delta == 7:
                j7_list.append(f"👤 *{nom}* | `{code}` | Capital: {capital or '—'}")
            elif delta == 2:
                j2_list.append(f"👤 *{nom}* | `{code}` | Capital: {capital or '—'}")
            elif delta == 0:
                j0_list.append(f"👤 *{nom}* | `{code}` | Capital: {capital or '—'}")

        # ── RAPPEL J-7 avec boutons par membre
        for code, nom, capital in [(m.split('|')[1].strip().strip('`'), m.split('|')[0].replace('👤 *','').replace('*','').strip(), m.split('|')[2].replace('Capital:','').strip()) for m in j7_list]:
            msg = (
                f"📅 *Expiration dans 7 jours*\n\n"
                f"👤 *{nom}*\n"
                f"🔑 `{code}`\n"
                f"💰 Capital : {capital}\n\n"
                f"_Email de relance envoyé automatiquement._"
            )
            markup = {"inline_keyboard": [[
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel?key={ADMIN_KEY}"},
                {"text": "💬 Contacter", "url": f"https://t.me/lerisluketobot"}
            ], [
                {"text": "👤 Voir le profil", "url": f"{BASE_URL}/admin/api/membre/profil?key={ADMIN_KEY}&code={code}"}
            ]]}
            send_telegram(msg, reply_markup=markup)

        # ── RAPPEL J-2 (48h) avec boutons par membre
        for code, nom, capital in [(m.split('|')[1].strip().strip('`'), m.split('|')[0].replace('👤 *','').replace('*','').strip(), m.split('|')[2].replace('Capital:','').strip()) for m in j2_list]:
            msg = (
                f"⚠️ *URGENT — Expiration dans 48h*\n\n"
                f"👤 *{nom}*\n"
                f"🔑 `{code}`\n"
                f"💰 Capital : {capital}\n\n"
                f"_Relance directe recommandée._"
            )
            markup = {"inline_keyboard": [[
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel?key={ADMIN_KEY}"},
                {"text": "💬 Contacter", "url": f"https://t.me/lerisluketobot"}
            ], [
                {"text": "👤 Voir le profil", "url": f"{BASE_URL}/admin/api/membre/profil?key={ADMIN_KEY}&code={code}"}
            ]]}
            send_telegram(msg, reply_markup=markup)

        # ── RAPPEL J=0 (expire aujourd'hui) avec boutons par membre
        for code, nom, capital in [(m.split('|')[1].strip().strip('`'), m.split('|')[0].replace('👤 *','').replace('*','').strip(), m.split('|')[2].replace('Capital:','').strip()) for m in j0_list]:
            msg = (
                f"🚨 *EXPIRATION AUJOURD\'HUI*\n\n"
                f"👤 *{nom}*\n"
                f"🔑 `{code}`\n"
                f"💰 Capital : {capital}\n\n"
                f"_Accès bloqué — en attente de renouvellement._"
            )
            markup = {"inline_keyboard": [[
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel?key={ADMIN_KEY}"},
                {"text": "➕ Prolonger 90j", "url": f"{BASE_URL}/admin-panel?key={ADMIN_KEY}"}
            ], [
                {"text": "💬 Contacter membre", "url": f"https://t.me/lerisluketobot"},
                {"text": "👤 Voir profil", "url": f"{BASE_URL}/admin/api/membre/profil?key={ADMIN_KEY}&code={code}"}
            ]]}
            send_telegram(msg, reply_markup=markup)

        app.logger.info(
            f"job_relances: {len(membres)} membres vérifiés — "
            f"emails:{emails_sent} J7:{len(j7_list)} J2:{len(j2_list)} J0:{len(j0_list)}"
        )
    except Exception as e:
        app.logger.error(f"job_relances: {e}")
        send_telegram(f"❌ *ERREUR job_relances*\n`{e}`")


@app.route("/admin/api/renewal-emails/run", methods=["POST"])
def admin_run_renewal_emails():
    """Déclenchement idempotent pour le déploiement ou une reprise contrôlée."""
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Interdit"}), 403
    job_relances_quotidiennes()
    conn = get_conn()
    stats = conn.run(
        """SELECT status, COUNT(*) FROM renewal_email_log
           GROUP BY status ORDER BY status"""
    )
    conn.close()
    return jsonify({"ok": True, "emails": {status: count for status, count in stats}})

@app.route("/admin/api/renewal-emails/reset-undelivered", methods=["POST"])
def admin_reset_undelivered_renewal_emails():
    """Réouvre les envois acceptés par l'API pendant la panne de crédits Brevo."""
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "Interdit"}), 403
    conn = get_conn()
    result = conn.run(
        """UPDATE renewal_email_log
           SET status='failed', error='Crédits email Brevo insuffisants', sent_at=NULL
           WHERE status='sent' AND provider_message_id <> ''
           RETURNING member_code"""
    )
    conn.close()
    return jsonify({"ok": True, "reset": len(result)})


JOURS_FR = {
    "Monday": "lundi", "Tuesday": "mardi", "Wednesday": "mercredi",
    "Thursday": "jeudi", "Friday": "vendredi", "Saturday": "samedi",
    "Sunday": "dimanche"
}
MOIS_FR = {
    "January": "janvier", "February": "février", "March": "mars",
    "April": "avril", "May": "mai", "June": "juin", "July": "juillet",
    "August": "août", "September": "septembre", "October": "octobre",
    "November": "novembre", "December": "décembre"
}
FLAG_MAP = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CHF": "🇨🇭", "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿",
    "CNY": "🇨🇳"
}
IMPACT_ICONS = {"High": "🔴", "Medium": "🟡", "Low": "⚪"}


def _paris_now():
    return datetime.now(PARIS_TZ)


def _claim_scheduled_publication(slot_key, post_kind, content, post_id=None, target_channel=""):
    """Réserve un créneau en base pour empêcher les doublons multi-workers."""
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """INSERT INTO scheduled_publications
               (slot_key, post_kind, status, content, attempts, post_id, target_channel)
               VALUES (:slot, :kind, 'sending', :content, 1, :post_id, :target_channel)
               ON CONFLICT (slot_key) DO UPDATE SET
                   status='sending', content=:content,
                   post_id=COALESCE(:post_id, scheduled_publications.post_id),
                   target_channel=:target_channel,
                   attempts=scheduled_publications.attempts + 1,
                   error='', created_at=NOW()
               WHERE scheduled_publications.status='failed'
               RETURNING slot_key""",
            slot=slot_key, kind=post_kind, content=content, post_id=post_id,
            target_channel=target_channel
        )
        return bool(rows)
    except Exception as e:
        # Échec fermé : sans verrou DB, ne pas risquer une double publication.
        app.logger.error(f"publication claim {slot_key}: {e}")
        return False
    finally:
        if conn:
            try: conn.close()
            except: pass


def _finish_scheduled_publication(slot_key, status, message_id=None, error=""):
    conn = None
    try:
        conn = get_conn()
        conn.run(
            """UPDATE scheduled_publications
               SET status=:status, telegram_message_id=:message_id, error=:error,
                   sent_at=CASE WHEN :status='sent' THEN NOW() ELSE sent_at END
               WHERE slot_key=:slot""",
            status=status, message_id=message_id, error=(error or "")[:1000], slot=slot_key
        )
    except Exception as e:
        app.logger.error(f"publication finish {slot_key}: {e}")
    finally:
        if conn:
            try: conn.close()
            except: pass


def _scheduled_publication_status(slot_key):
    """Relit l'état d'un créneau pour distinguer un doublon d'un échec."""
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            "SELECT status FROM scheduled_publications WHERE slot_key=:slot",
            slot=slot_key
        )
        return str(rows[0][0]) if rows else ""
    except Exception as error:
        app.logger.error(f"publication status {slot_key}: {error}")
        return ""
    finally:
        if conn:
            try: conn.close()
            except: pass


def _download_telegram_image(image_url):
    """Télécharge le média pour éviter que Telegram interprète une page Web comme une photo."""
    if not image_url:
        return None
    response = requests.get(
        image_url, timeout=20, allow_redirects=True,
        headers={"User-Agent": "Bectanse-Telegram-Publisher/1.0"}
    )
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    content = response.content
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError(f"Le lien média renvoie {content_type or 'un contenu inconnu'}, pas une image")
    if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError("L’image Telegram est vide ou dépasse 10 Mo")
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
    return (f"bectanse-publication.{extension}", content, content_type)


def _send_scheduled_telegram(
    text, slot_key, post_kind, image_url="", channel=None,
    button_text="", button_url="", disable_notification=False, post_id=None,
    post_type="message", poll_question="", poll_options=None,
    poll_correct_option_ids=None, poll_explanation="", poll_anonymous=True,
    poll_multiple=False
):
    target_channel = (channel or ECO_CANAL or "").strip()
    if not ECO_BOT_TOKEN or not target_channel:
        app.logger.error("Publication Telegram annulée : ECO_BOT_TOKEN ou canal absent")
        return False
    post_type = post_type if post_type in {"message", "quiz", "poll"} else "message"
    if post_type == "message":
        max_length = 1024 if image_url else 4096
        if not text or len(text) > max_length:
            app.logger.error(f"Publication Telegram invalide : limite {max_length} caractères")
            return False
        claim_content = text
    else:
        poll_options = _payload_list(poll_options)
        poll_correct_option_ids = [
            int(index) for index in _payload_list(poll_correct_option_ids)
        ]
        if not poll_question or not 2 <= len(poll_options) <= 12:
            app.logger.error("Quiz ou sondage Telegram invalide")
            return False
        claim_content = poll_question
    if not _claim_scheduled_publication(
        slot_key, post_kind, claim_content, post_id=post_id,
        target_channel=target_channel
    ):
        app.logger.info(f"Publication déjà traitée ou verrouillée : {slot_key}")
        return False

    image_file = None
    if image_url and post_type == "message":
        try:
            image_file = _download_telegram_image(image_url)
        except Exception as error:
            # Une URL média cassée ne doit jamais bloquer toute la publication.
            app.logger.warning(f"Média Telegram ignoré pour {slot_key}: {error}")
            image_url = ""

    last_error = "erreur Telegram inconnue"
    for attempt in range(3):
        try:
            if attempt:
                time.sleep(2)
            payload = {"chat_id": target_channel, "disable_notification": bool(disable_notification)}
            if button_text and button_url:
                payload["reply_markup"] = {
                    "inline_keyboard": [[{"text": button_text, "url": button_url}]]
                }
            if post_type in {"quiz", "poll"}:
                endpoint = "sendPoll"
                payload.update({
                    "question": poll_question,
                    "options": [{"text": str(option)} for option in poll_options],
                    "type": "quiz" if post_type == "quiz" else "regular",
                    "is_anonymous": bool(poll_anonymous),
                    "allows_multiple_answers": bool(poll_multiple),
                })
                if post_type == "quiz":
                    payload["correct_option_ids"] = poll_correct_option_ids
                    if poll_explanation:
                        payload.update({
                            "explanation": poll_explanation,
                            "explanation_parse_mode": "Markdown"
                        })
            else:
                payload["parse_mode"] = "Markdown"
                if image_url:
                    endpoint = "sendPhoto"
                    payload.update({"photo": image_url, "caption": text})
                else:
                    endpoint = "sendMessage"
                    payload.update({"text": text, "disable_web_page_preview": True})
            if endpoint == "sendPhoto" and image_file:
                multipart_payload = dict(payload)
                multipart_payload["reply_markup"] = json.dumps(
                    multipart_payload["reply_markup"], ensure_ascii=False
                ) if multipart_payload.get("reply_markup") else None
                multipart_payload = {
                    key: (str(value).lower() if isinstance(value, bool) else value)
                    for key, value in multipart_payload.items() if value is not None
                }
                multipart_payload.pop("photo", None)
                response = requests.post(
                    f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/{endpoint}",
                    data=multipart_payload, files={"photo": image_file}, timeout=30
                )
            else:
                response = requests.post(
                    f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/{endpoint}",
                    json=payload, timeout=20
                )
            result = response.json()
            if result.get("ok"):
                message_id = result.get("result", {}).get("message_id")
                _finish_scheduled_publication(slot_key, "sent", message_id=message_id)
                app.logger.info(f"Publication Telegram envoyée : {slot_key}")
                return True
            last_error = result.get("description", f"HTTP {response.status_code}")
        except Exception as e:
            last_error = str(e)
        app.logger.warning(f"Telegram {slot_key}, tentative {attempt + 1}: {last_error}")

    _finish_scheduled_publication(slot_key, "failed", error=last_error)
    send_telegram(f"❌ *Publication Telegram échouée*\n`{slot_key}`\n{last_error[:300]}")
    return False


def _resolve_telegram_targets(post=None, publish_all_channels=False, fallback_channel=None):
    post = post or {}
    use_all = bool(post.get("publish_all_channels", publish_all_channels))
    fallback = (fallback_channel or post.get("channel") or ECO_CANAL or "").strip()
    conn = None
    targets = []
    registry_checked = False
    try:
        conn = get_conn()
        if use_all:
            rows = conn.run(
                """SELECT id, name, chat_id FROM telegram_channels
                   WHERE active=TRUE AND deleted=FALSE ORDER BY id"""
            )
        elif post.get("id"):
            rows = conn.run(
                """SELECT channels.id, channels.name, channels.chat_id
                   FROM telegram_post_channels AS targets
                   JOIN telegram_channels AS channels ON channels.id=targets.channel_id
                   WHERE targets.post_id=:post_id
                     AND channels.active=TRUE AND channels.deleted=FALSE
                   ORDER BY channels.id""",
                post_id=int(post["id"])
            )
        else:
            rows = []
        registry_checked = use_all or bool(post.get("id"))
        targets = [
            {"id": int(channel_id), "name": name, "chat_id": chat_id}
            for channel_id, name, chat_id in rows
        ]
    except Exception as error:
        app.logger.error(f"telegram target resolution: {error}")
    finally:
        if conn:
            try: conn.close()
            except: pass

    if not targets and fallback and not registry_checked:
        targets = [{"id": None, "name": "Canal historique", "chat_id": fallback}]
    unique_targets = []
    seen = set()
    for target in targets:
        key = str(target["chat_id"]).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(target)
    return unique_targets


def _broadcast_scheduled_telegram(
    text, base_slot_key, post_kind, post=None, publish_all_channels=False,
    fallback_channel=None, **send_options
):
    targets = _resolve_telegram_targets(
        post=post, publish_all_channels=publish_all_channels,
        fallback_channel=fallback_channel
    )
    results = []
    for target in targets:
        target_hash = hashlib.sha256(
            str(target["chat_id"]).lower().encode("utf-8")
        ).hexdigest()[:12]
        slot_key = f"{base_slot_key}-{target_hash}"
        sent_now = _send_scheduled_telegram(
            text,
            slot_key=slot_key,
            post_kind=post_kind,
            channel=target["chat_id"],
            **send_options
        )
        status = "sent" if sent_now else _scheduled_publication_status(slot_key)
        results.append({
            **target,
            "sent": status == "sent",
            "sent_now": bool(sent_now),
            "status": status or "failed",
        })
    return {
        "total": len(results),
        "sent": sum(1 for result in results if result["sent"]),
        "sent_now": sum(1 for result in results if result["sent_now"]),
        "failed": sum(1 for result in results if not result["sent"]),
        "channels": results,
    }


def _send_saved_post_to_channels(post, base_slot_key, post_kind):
    return _broadcast_scheduled_telegram(
        post["message"], base_slot_key=base_slot_key, post_kind=post_kind,
        post=post, fallback_channel=post.get("channel"),
        image_url=post.get("image_url") or "",
        button_text=post.get("button_text") or "",
        button_url=post.get("button_url") or "",
        disable_notification=post.get("disable_notification", False),
        post_id=post.get("id"), post_type=post.get("post_type") or "message",
        poll_question=post.get("poll_question") or "",
        poll_options=post.get("poll_options") or "[]",
        poll_correct_option_ids=post.get("poll_correct_option_ids") or "[]",
        poll_explanation=post.get("poll_explanation") or "",
        poll_anonymous=post.get("poll_anonymous", True),
        poll_multiple=post.get("poll_multiple", False)
    )


def load_telegram_editorial_calendar():
    with open(TELEGRAM_EDITORIAL_PATH, "r", encoding="utf-8") as content_file:
        calendar = json.load(content_file)
    weeks = calendar.get("weeks") or []
    if not weeks or any(len(week) != 7 for week in weeks):
        raise ValueError("Le calendrier Telegram doit contenir des semaines de 7 publications")
    return calendar


def build_daily_editorial_post(target_date=None):
    target_date = target_date or _paris_now().date()
    calendar = load_telegram_editorial_calendar()
    weeks = calendar["weeks"]
    week_index = (target_date.isocalendar().week - 1) % len(weeks)
    post = weeks[week_index][target_date.weekday()]
    message = _format_editorial_entry(calendar, post)
    if len(message) > 4096:
        raise ValueError("La publication Telegram dépasse la limite de 4 096 caractères")
    return message


def send_daily_editorial_post(target_date=None):
    target_date = target_date or _paris_now().date()
    try:
        message = build_daily_editorial_post(target_date)
        delivery = _broadcast_scheduled_telegram(
            message,
            base_slot_key=f"editorial-{target_date.isoformat()}",
            post_kind="editorial", publish_all_channels=True,
            fallback_channel=ECO_CANAL
        )
        return delivery["sent"] > 0
    except Exception as e:
        app.logger.error(f"send_daily_editorial_post: {e}")
        send_telegram(f"❌ *Erreur calendrier éditorial*\n`{str(e)[:300]}`")
        return False


TELEGRAM_POST_COLUMNS = [
    "id", "name", "message", "image_url", "schedule_type", "weekdays",
    "rotation_week", "publish_time", "scheduled_for", "timezone", "channel",
    "button_text", "button_url", "disable_notification", "enabled", "source_key",
    "deleted", "last_sent_at", "created_at", "updated_at", "post_type",
    "poll_question", "poll_options", "poll_correct_option_ids",
    "poll_explanation", "poll_anonymous", "poll_multiple", "publish_all_channels"
]


def _telegram_post_from_row(row):
    return dict(zip(TELEGRAM_POST_COLUMNS, row))


def _parse_scheduled_datetime(value):
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PARIS_TZ)
    return parsed.astimezone(PARIS_TZ)


def _parse_publish_time(value):
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _post_matches_day(post, target_date):
    schedule_type = post.get("schedule_type")
    if schedule_type == "once":
        scheduled_for = _parse_scheduled_datetime(post.get("scheduled_for"))
        return bool(scheduled_for and scheduled_for.date() == target_date)
    try:
        weekdays = {int(day) for day in str(post.get("weekdays") or "").split(",") if day != ""}
    except ValueError:
        return False
    if target_date.weekday() not in weekdays:
        return False
    if schedule_type == "rotation":
        return ((target_date.isocalendar().week - 1) % 4) == int(post.get("rotation_week") or 0)
    return schedule_type == "weekly"


def scheduled_telegram_post_is_due(post, now=None, grace_minutes=10):
    now = now or _paris_now()
    if not post.get("enabled") or post.get("deleted"):
        return False
    if post.get("schedule_type") == "once":
        scheduled_at = _parse_scheduled_datetime(post.get("scheduled_for"))
    else:
        publish_time = _parse_publish_time(post.get("publish_time"))
        if not publish_time or not _post_matches_day(post, now.date()):
            return False
        scheduled_at = datetime.combine(now.date(), publish_time, tzinfo=PARIS_TZ)
    if not scheduled_at:
        return False
    delay = (now - scheduled_at).total_seconds()
    return 0 <= delay < grace_minutes * 60


def next_run_for_telegram_post(post, now=None):
    now = now or _paris_now()
    if not post.get("enabled") or post.get("deleted"):
        return None
    if post.get("schedule_type") == "once":
        scheduled_at = _parse_scheduled_datetime(post.get("scheduled_for"))
        return scheduled_at.isoformat() if scheduled_at and scheduled_at >= now else None
    publish_time = _parse_publish_time(post.get("publish_time"))
    if not publish_time:
        return None
    for offset in range(36):
        target_date = now.date() + timedelta(days=offset)
        if not _post_matches_day(post, target_date):
            continue
        candidate = datetime.combine(target_date, publish_time, tzinfo=PARIS_TZ)
        if candidate >= now:
            return candidate.isoformat()
    return None


def process_scheduled_telegram_posts(now=None):
    """Vérifie les posts dus. Le verrou DB évite les doubles envois workers."""
    now = now or _paris_now()
    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, name, message, image_url, schedule_type, weekdays,
                      rotation_week, publish_time, scheduled_for, timezone, channel,
                      button_text, button_url, disable_notification, enabled, source_key,
                      deleted, last_sent_at, created_at, updated_at, post_type,
                      poll_question, poll_options, poll_correct_option_ids,
                      poll_explanation, poll_anonymous, poll_multiple,
                      publish_all_channels
               FROM telegram_scheduled_posts
               WHERE enabled=TRUE AND deleted=FALSE
               ORDER BY id"""
        )
    except Exception as e:
        app.logger.error(f"process scheduled Telegram: {e}")
        return 0
    finally:
        if conn:
            try: conn.close()
            except: pass

    sent_count = 0
    for row in rows:
        post = _telegram_post_from_row(row)
        if not scheduled_telegram_post_is_due(post, now=now):
            continue
        if post["schedule_type"] == "once":
            scheduled_at = _parse_scheduled_datetime(post.get("scheduled_for"))
        else:
            scheduled_at = datetime.combine(
                now.date(), _parse_publish_time(post.get("publish_time")), tzinfo=PARIS_TZ
            )
        slot_key = f"telegram-post-{post['id']}-{scheduled_at.strftime('%Y%m%d-%H%M')}"
        delivery = _send_saved_post_to_channels(post, slot_key, "custom-editorial")
        if not delivery["sent"]:
            continue
        sent_now = delivery.get("sent_now", delivery["sent"])
        complete = delivery["total"] > 0 and delivery["failed"] == 0
        if sent_now:
            sent_count += 1
        if not sent_now and not (post["schedule_type"] == "once" and complete):
            continue
        update_conn = None
        try:
            update_conn = get_conn()
            update_conn.run(
                """UPDATE telegram_scheduled_posts
                   SET last_sent_at=CASE WHEN :sent_now THEN NOW() ELSE last_sent_at END,
                       enabled=CASE
                           WHEN schedule_type='once' AND :complete THEN FALSE
                           ELSE enabled
                       END,
                       updated_at=NOW()
                   WHERE id=:id""",
                id=post["id"], sent_now=bool(sent_now), complete=complete
            )
        except Exception as e:
            app.logger.error(f"scheduled Telegram post update {post['id']}: {e}")
        finally:
            if update_conn:
                try: update_conn.close()
                except: pass
    return sent_count


def get_eco_calendar(target_date=None):
    try:
        target_date = target_date or _paris_now().date()
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            app.logger.error(f"eco_calendar: source indisponible (HTTP {r.status_code})")
            return None
        events = []
        for event in r.json():
            try:
                date_str = event.get("date", "")
                event_dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z").astimezone(PARIS_TZ)
                if event_dt.date() == target_date and event.get("impact") in ("High", "Medium"):
                    enriched = dict(event)
                    enriched["paris_time"] = event_dt.strftime("%H:%M")
                    events.append(enriched)
            except Exception:
                pass
        return sorted(events, key=lambda item: item.get("date", ""))
    except Exception as e:
        app.logger.error(f"eco_calendar: {e}")
        return None


def send_eco_message(target_date=None):
    try:
        target_date = target_date or _paris_now().date()
        events = get_eco_calendar(target_date)
        if events is None:
            app.logger.error("Calendrier économique non publié : données non vérifiables")
            send_telegram(
                "❌ *Calendrier économique non publié*\n"
                "La source de données est indisponible. Aucun agenda public n’a été envoyé."
            )
            return False
        date_fr = target_date.strftime("%A %d %B %Y")
        for en, fr in JOURS_FR.items():
            date_fr = date_fr.replace(en, fr)
        for en, fr in MOIS_FR.items():
            date_fr = date_fr.replace(en, fr)
        date_fr = date_fr.capitalize()

        if not events:
            msg = (
                f"📅 *CALENDRIER ÉCONOMIQUE — {date_fr}*\n\n"
                "✅ Aucune annonce à impact fort ou moyen repérée dans le calendrier utilisé.\n"
                "Reste vigilant : d’autres événements et mouvements de marché restent possibles.\n\n"
                "━━━━━━━━━━━━━━━\n"
                "🔥 *Bectanse AUTO — Copy Trading Automatique*"
            )
        else:
            high_count = sum(1 for event in events if event.get("impact") == "High")
            msg = f"📅 *CALENDRIER ÉCONOMIQUE — {date_fr}*\n\n"
            if high_count:
                msg += f"⚠️ *{high_count} annonce(s) à fort impact*\n\n"
            for event in events:
                currency = event.get("currency", "")
                title = event.get("title", "")
                impact = event.get("impact", "Low")
                forecast = event.get("forecast", "—") or "—"
                previous = event.get("previous", "—") or "—"
                flag = FLAG_MAP.get(currency, "🌐")
                icon = IMPACT_ICONS.get(impact, "⚪")
                msg += f"{icon} *{event.get('paris_time', '—')}* {flag} {title}\n"
                if forecast != "—":
                    msg += f"   Prévision: `{forecast}` | Précédent: `{previous}`\n"
                msg += "\n"
            msg += "━━━━━━━━━━━━━━━\n"
            msg += "🔴 Fort impact  🟡 Moyen impact\n\n"
            msg += "_Un calendrier économique informe sur le timing, pas sur la direction future du marché._\n\n"
            msg += "🔥 *Bectanse AUTO — Copy Trading Automatique*"

        delivery = _broadcast_scheduled_telegram(
            msg,
            base_slot_key=f"economic-calendar-{target_date.isoformat()}",
            post_kind="economic-calendar", publish_all_channels=True,
            fallback_channel=ECO_CANAL,
            button_text="ACCÉDER À L’ESPACE",
            button_url="https://acces.bectanse-academie.com/"
        )
        if not delivery["sent"]:
            return False

        nb_high = sum(1 for event in events if event.get("impact") == "High")
        push_title = "📅 Calendrier économique"
        if nb_high:
            push_body = f"{nb_high} annonce(s) à fort impact aujourd’hui — prudence renforcée"
        else:
            push_body = "Aucune annonce à fort impact repérée dans le calendrier utilisé"
        threading.Thread(
            target=send_push_to_all_fcm,
            args=(push_title, push_body, "/accueil"),
            daemon=True
        ).start()
        return True
    except Exception as e:
        app.logger.error(f"send_eco_message: {e}")
        return False



# ── WEB PUSH ──────────────────────────────────────────────────────────────────

@app.route("/api/push/vapid-public")
def vapid_public():
    return jsonify({"key": VAPID_PUBLIC_KEY})

def _save_push_subscription(data):
    code = session.get("member_code")
    endpoint = (data or {}).get("endpoint", "")
    keys = (data or {}).get("keys", {})
    p256dh, auth_key = keys.get("p256dh", ""), keys.get("auth", "")
    if not code:
        return jsonify({"ok": False, "error": "non connecté"}), 401
    if not endpoint or not p256dh or not auth_key:
        return jsonify({"ok": False, "error": "abonnement incomplet"}), 400
    try:
        conn = get_conn()
        conn.run("""INSERT INTO push_subscriptions (member_code, endpoint, p256dh, auth)
            VALUES (:code, :endpoint, :p256dh, :auth)
            ON CONFLICT (endpoint) DO UPDATE SET member_code=:code,
            p256dh=:p256dh, auth=:auth""",
            code=code, endpoint=endpoint, p256dh=p256dh, auth=auth_key)
        conn.close()
        return jsonify({"ok": True})
    except Exception as error:
        app.logger.error("Enregistrement Web Push: %s", error)
        return jsonify({"ok": False, "error": "enregistrement impossible"}), 500

@app.route("/api/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    return _save_push_subscription(request.get_json(silent=True) or {})

@app.route("/api/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    if data and data.get("endpoint"):
        try:
            conn = get_conn()
            conn.run("DELETE FROM push_subscriptions WHERE endpoint=:ep AND member_code=:code",
                     ep=data["endpoint"], code=session["member_code"])
            conn.close()
        except: pass
    return jsonify({"ok": True})

@app.route("/api/push/status")
@login_required
def push_status():
    try:
        conn = get_conn()
        count = conn.run("SELECT COUNT(*) FROM push_subscriptions WHERE member_code=:code",
                         code=session["member_code"])[0][0]
        conn.close()
        return jsonify({"ok": True, "subscribed": count > 0, "devices": count})
    except Exception:
        return jsonify({"ok": False, "subscribed": False, "devices": 0}), 500

@app.route("/api/push/test", methods=["POST"])
@login_required
def push_test():
    """Envoie une notification de validation uniquement aux appareils du membre."""
    try:
        endpoint = (request.get_json(silent=True) or {}).get("endpoint", "")
        if not endpoint:
            return jsonify({"ok": False, "error": "Appareil non identifié"}), 400
        conn = get_conn()
        rows = conn.run("""SELECT endpoint, p256dh, auth FROM push_subscriptions
            WHERE member_code=:code AND endpoint=:endpoint""",
            code=session["member_code"], endpoint=endpoint)
        conn.close()
        if not rows:
            return jsonify({"ok": False, "error": "Appareil non abonné"}), 404
        sent = 0
        for endpoint, p256dh, auth_key in rows:
            try:
                delivered = send_webpush_notification(
                    {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth_key}},
                    "Bectanse AUTO", "Tes notifications iPhone sont bien activées.", "/accueil")
                if delivered:
                    sent += 1
            except Exception:
                pass
        return jsonify({"ok": sent > 0, "sent": sent})
    except Exception as error:
        app.logger.error("Test Web Push: %s", error)
        return jsonify({"ok": False, "error": "test impossible"}), 500

def send_push_to_all(title, body, url="/canal"):
    """Envoie un Web Push à tous les appareils des membres actifs avec bilan."""
    from concurrent.futures import ThreadPoolExecutor
    result = {"registered": 0, "delivered": 0, "failed": 0, "members": 0}
    try:
        from pywebpush import webpush, WebPushException
        enforce_member_access_state()
        conn = get_conn()
        subs = conn.run("""SELECT ps.endpoint, ps.p256dh, ps.auth, ps.member_code
            FROM push_subscriptions ps
            JOIN members m ON m.code=ps.member_code
            WHERE m.actif=TRUE AND (m.date_fin IS NULL OR m.date_fin > NOW())
              AND m.code <> 'BCT-DEMO2026'""")
        conn.close()
        result["registered"] = len(subs)
        result["members"] = len({row[3] for row in subs})
        if not subs:
            return result
        payload = json.dumps({"title": title, "body": body, "url": url,
                              "tag": f"bectanse-global-{int(time.time())}"})

        def deliver(row):
            ep, p256dh, auth_key, _member_code = row
            try:
                webpush(
                    subscription_info={"endpoint": ep, "keys": {"p256dh": p256dh, "auth": auth_key}},
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS,
                    timeout=10
                )
                return ep, True, False
            except WebPushException as ex:
                status = ex.response.status_code if ex.response else None
                return ep, False, status in (404, 410)
            except Exception as error:
                app.logger.warning("Push global %s: %s", _member_code, error)
                return ep, False, False

        with ThreadPoolExecutor(max_workers=min(12, len(subs))) as pool:
            deliveries = list(pool.map(deliver, subs))
        result["delivered"] = sum(1 for _, ok, _ in deliveries if ok)
        result["failed"] = result["registered"] - result["delivered"]
        dead = [endpoint for endpoint, _, expired in deliveries if expired]
        if dead:
            conn2 = get_conn()
            for ep in dead:
                try:
                    conn2.run("DELETE FROM push_subscriptions WHERE endpoint=:ep", ep=ep)
                except: pass
            conn2.close()
        return result
    except Exception as e:
        app.logger.error(f"send_push_to_all: {e}")
        return result

def send_push_to_member(member_code, title, body, url="/accueil"):
    """Envoie un Web Push aux appareils d'un seul membre et retourne un bilan réel."""
    from pywebpush import webpush, WebPushException
    result = {"registered": 0, "delivered": 0, "failed": 0}
    dead = []
    try:
        conn = get_conn()
        rows = conn.run("""SELECT endpoint, p256dh, auth FROM push_subscriptions
            WHERE member_code=:code""", code=member_code)
        conn.close()
        result["registered"] = len(rows)
        payload = json.dumps({"title": title, "body": body, "url": url,
                              "tag": f"bectanse-personal-{member_code}"})
        for endpoint, p256dh, auth_key in rows:
            try:
                webpush(
                    subscription_info={"endpoint": endpoint,
                        "keys": {"p256dh": p256dh, "auth": auth_key}},
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS,
                    timeout=10
                )
                result["delivered"] += 1
            except WebPushException as error:
                result["failed"] += 1
                status = error.response.status_code if error.response else None
                if status in (404, 410):
                    dead.append(endpoint)
                app.logger.warning("Push membre %s: HTTP %s", member_code, status)
            except Exception as error:
                result["failed"] += 1
                app.logger.warning("Push membre %s: %s", member_code, error)
        if dead:
            conn = get_conn()
            for endpoint in dead:
                conn.run("DELETE FROM push_subscriptions WHERE endpoint=:endpoint",
                         endpoint=endpoint)
            conn.close()
        return result
    except Exception as error:
        app.logger.error("Push individuel %s: %s", member_code, error)
        return result

# ── CANAL VIP ─────────────────────────────────────────────────────────────────

CANAL_BOT_TOKEN = "8895323708:AAFNFHv8BXada_wFDnZhh69rKPLKs2oGAco"
CANAL_GROUP_ID  = -1003605441967
CANAL_ADMIN_CODE = "BCT-LERIS"
def detect_msg_type(text):
    """Détecte automatiquement le type de message selon les mots-clés."""
    t = (text or "").lower()
    if any(w in t for w in ["signal","achat","vente","buy","sell","entrée","entry","xau","gold"]):
        return "signal"
    if any(w in t for w in ["résultat","result","tp","sl","profit","gain","perte","clôture","fermé","closed","win","loss"]):
        return "resultat"
    if any(w in t for w in ["⚠","alerte","urgent","attention","warning","risque"]):
        return "alerte"
    if any(w in t for w in ["annonce","important","news","mise à jour","update","info"]):
        return "annonce"
    return "message"

def register_canal_webhook():
    """Enregistre le webhook du bot canal VIP."""
    try:
        webhook_url = "https://bectanse-auto.up.railway.app/canal-webhook"
        r = requests.post(
            f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "edited_message"]},
            timeout=10
        )
        app.logger.info(f"Canal webhook: {r.json()}")
    except Exception as e:
        app.logger.error(f"register_canal_webhook: {e}")

@app.route("/canal-webhook", methods=["POST"])
def canal_webhook():
    """Reçoit les messages du groupe VIP Telegram et les stocke en base."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": True})

        message = data.get("message")
        edited  = data.get("edited_message")
        msg     = message or edited
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg.get("chat", {}).get("id")
        if int(chat_id) != int(CANAL_GROUP_ID):
            return jsonify({"ok": True})

        tg_msg_id = msg.get("message_id")

        # Ignorer les messages de service Telegram
        is_service = any(k in msg for k in [
            "new_chat_members","left_chat_member","new_chat_title",
            "new_chat_photo","delete_chat_photo","group_chat_created",
            "pinned_message","migrate_to_chat_id","migrate_from_chat_id"
        ])
        if is_service:
            return jsonify({"ok": True})

        text_content = msg.get("text") or msg.get("caption") or ""
        msg_type = detect_msg_type(text_content)
        photo_url = None
        audio_url = None

        # Photo → Cloudinary (URL permanente)
        photos = msg.get("photo")
        if photos:
            file_id = photos[-1]["file_id"]
            photo_url = tg_download_and_upload(file_id, CANAL_BOT_TOKEN, "image")
            if not photo_url:
                # Fallback URL Telegram directe
                try:
                    r2 = requests.get(f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getFile",
                        params={"file_id": file_id}, timeout=8)
                    fp = r2.json()["result"]["file_path"]
                    photo_url = f"https://api.telegram.org/file/bot{CANAL_BOT_TOKEN}/{fp}"
                except: photo_url = None

        # Audio / Message vocal → Cloudinary
        voice = msg.get("voice")
        audio = msg.get("audio")
        media = voice or audio
        if media:
            file_id = media["file_id"]
            audio_url = tg_download_and_upload(file_id, CANAL_BOT_TOKEN, "video")
            if not audio_url:
                try:
                    r2 = requests.get(f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getFile",
                        params={"file_id": file_id}, timeout=8)
                    fp = r2.json()["result"]["file_path"]
                    audio_url = f"https://api.telegram.org/file/bot{CANAL_BOT_TOKEN}/{fp}"
                except: audio_url = None
            if not text_content:
                text_content = "🎙️ Message vocal" if voice else "🎵 Audio"
            if audio_url:
                msg_type = "audio"

        conn = get_conn()
        if edited:
            conn.run(
                """UPDATE canal_messages SET text_content=:txt, msg_type=:typ, edited=TRUE,
                   photo_url=COALESCE(:photo, photo_url),
                   audio_url=COALESCE(:audio, audio_url)
                   WHERE tg_msg_id=:mid""",
                txt=text_content, typ=msg_type, photo=photo_url, audio=audio_url, mid=tg_msg_id
            )
        else:
            conn.run(
                """INSERT INTO canal_messages (tg_msg_id, text_content, msg_type, photo_url, audio_url, edited)
                   VALUES (:mid, :txt, :typ, :photo, :audio, FALSE)
                   ON CONFLICT (tg_msg_id) DO NOTHING""",
                mid=tg_msg_id, txt=text_content, typ=msg_type, photo=photo_url, audio=audio_url
            )
        conn.close()
        # Web Push si nouveau message
        if not edited and text_content:
            TYPE_LABELS = {"signal":"📊 Signal","resultat":"✅ Résultat","alerte":"🚨 Alerte","annonce":"📢 Annonce","message":"💬 Canal VIP"}
            label = TYPE_LABELS.get(msg_type, "💬 Canal VIP")
            preview = text_content[:80] + ("…" if len(text_content) > 80 else "")
            threading.Thread(target=send_push_to_all_fcm, args=(label, preview, "/canal"), daemon=True).start()
    except Exception as e:
        app.logger.error(f"canal_webhook: {e}")
    return jsonify({"ok": True})


@app.route("/api/canal/messages")
def api_canal_messages():
    """Retourne les messages du canal VIP (50 derniers, ou après un ID donné)."""
    if "member_code" not in session:
        return jsonify({"error": "non connecté"}), 401
    try:
        after = request.args.get("after", 0, type=int)
        conn = get_conn()
        if after > 0:
            rows = conn.run(
                """SELECT id, tg_msg_id, text_content, msg_type, photo_url, audio_url, edited,
                          sent_at::text FROM canal_messages
                   WHERE id > :after AND (deleted IS NULL OR deleted=FALSE) ORDER BY id ASC""",
                after=after
            )
        else:
            rows = conn.run(
                """SELECT id, tg_msg_id, text_content, msg_type, photo_url, audio_url, edited,
                          sent_at::text FROM canal_messages
                   WHERE (deleted IS NULL OR deleted=FALSE) ORDER BY id DESC LIMIT 50"""
            )
        conn.close()
        msgs = [{"id":r[0],"tg_msg_id":r[1],"text_content":r[2],"msg_type":r[3],
                 "photo_url":r[4],"audio_url":r[5],"edited":r[6],"sent_at":r[7]} for r in rows]
        return jsonify({"messages": msgs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/canal/messages")
def admin_api_canal_messages():
    """Retourne les derniers messages du canal, y compris ceux masqués, pour l'admin."""
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False, "error": "non autorisé"}), 403

    conn = None
    try:
        conn = get_conn()
        rows = conn.run(
            """SELECT id, tg_msg_id, text_content, msg_type, photo_url, audio_url, edited,
                      COALESCE(deleted, FALSE), sent_at::text
               FROM canal_messages
               ORDER BY id DESC
               LIMIT 100"""
        )
        messages = [
            {
                "id": row[0],
                "tg_msg_id": row[1],
                "text_content": row[2],
                "msg_type": row[3],
                "photo_url": row[4],
                "audio_url": row[5],
                "edited": row[6],
                "deleted": row[7],
                "sent_at": row[8],
            }
            for row in rows
        ]
        return jsonify({"ok": True, "messages": messages})
    except Exception as e:
        app.logger.error("admin_api_canal_messages: %s", e)
        return jsonify({"ok": False, "error": "chargement impossible"}), 500
    finally:
        if conn is not None:
            conn.close()


@app.route("/api/canal/restaurer/<int:msg_id>", methods=["POST"])
def api_canal_restaurer(msg_id):
    key = request.args.get("key","") or (request.json.get("key","") if request.is_json else "")
    is_admin_key = (key == ADMIN_KEY)
    is_canal_admin = ("member_code" in session and session.get("member_code") == CANAL_ADMIN_CODE)
    if not is_admin_key and not is_canal_admin:
        return jsonify({"error": "non autorisé"}), 403
    try:
        conn = get_conn()
        conn.run("UPDATE canal_messages SET deleted=FALSE WHERE id=:id", id=msg_id)
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/canal/supprimer/<int:msg_id>", methods=["POST"])
def api_canal_supprimer(msg_id):
    """Supprime un message du canal webapp."""
    # Accepter soit la session admin canal, soit la clé admin
    key = request.args.get("key","") or (request.json.get("key","") if request.is_json else "")
    is_admin_key = (key == ADMIN_KEY)
    is_canal_admin = ("member_code" in session and session["member_code"] == CANAL_ADMIN_CODE)
    if not is_admin_key and not is_canal_admin:
        return jsonify({"error": "non autorisé"}), 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT tg_msg_id FROM canal_messages WHERE id=:id", id=msg_id)
        if rows:
            tg_id = rows[0][0]
            try:
                requests.post(
                    f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/deleteMessage",
                    json={"chat_id": CANAL_GROUP_ID, "message_id": tg_id},
                    timeout=8
                )
            except: pass
        conn.run("UPDATE canal_messages SET deleted=TRUE WHERE id=:id", id=msg_id)
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/canal/publier", methods=["POST"])
def api_canal_publier():
    """Permet à l'admin de publier un message depuis la webapp → stocké + envoyé sur Telegram."""
    if "member_code" not in session:
        return jsonify({"error": "non connecté"}), 401
    code = session["member_code"]
    member = get_member(code)
    if not member or code != CANAL_ADMIN_CODE:
        return jsonify({"error": "non autorisé"}), 403

    text     = request.form.get("text", "").strip()
    msg_type = request.form.get("msg_type", "message")
    image    = request.files.get("image")
    photo_url = None

    try:
        if image:
            # Envoyer la photo sur Telegram
            r = requests.post(
                f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/sendPhoto",
                data={"chat_id": CANAL_GROUP_ID, "caption": text, "parse_mode": "Markdown"},
                files={"photo": (image.filename, image.stream, image.content_type)},
                timeout=15
            )
            res = r.json()
            if res.get("ok"):
                tg_msg_id = res["result"]["message_id"]
                photos = res["result"].get("photo", [])
                if photos:
                    file_id = photos[-1]["file_id"]
                    r2 = requests.get(
                        f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getFile",
                        params={"file_id": file_id}, timeout=8
                    )
                    file_path = r2.json()["result"]["file_path"]
                    photo_url = f"https://api.telegram.org/file/bot{CANAL_BOT_TOKEN}/{file_path}"
        else:
            # Envoyer texte sur Telegram
            r = requests.post(
                f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/sendMessage",
                json={"chat_id": CANAL_GROUP_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10
            )
            res = r.json()
            if res.get("ok"):
                tg_msg_id = res["result"]["message_id"]
            else:
                return jsonify({"error": "Telegram error"}), 500

        # Stocker en base
        conn = get_conn()
        conn.run(
            """INSERT INTO canal_messages (tg_msg_id, text_content, msg_type, photo_url, edited)
               VALUES (:mid, :txt, :typ, :photo, FALSE)
               ON CONFLICT (tg_msg_id) DO NOTHING""",
            mid=tg_msg_id, txt=text, typ=msg_type, photo=photo_url
        )
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/canal")
@login_required
def canal():
    """Page Canal VIP."""
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    is_admin = (code == CANAL_ADMIN_CODE)
    demo_mode = (code == "BCT-DEMO2026")
    # En mode demo : aucun message envoyé au client
    if demo_mode:
        return render_template("canal.html", member=member,
            is_admin=False, demo_mode=True, messages=[], total=0)
    return render_template("canal.html", member=member,
        is_admin=is_admin, demo_mode=False)


@app.route("/admin/canal-diag")
def canal_diag():
    """Diagnostic du bot canal + force re-register webhook."""
    from flask import send_from_directory, request as freq
    key = freq.args.get("key", "")
    if key != ADMIN_KEY:
        return "Interdit", 403
    results = {}
    try:
        # 1. Statut webhook actuel
        r = requests.get(
            f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getWebhookInfo",
            timeout=8
        )
        results["webhook_info"] = r.json()
    except Exception as e:
        results["webhook_info"] = str(e)
    try:
        # 2. Re-register webhook
        webhook_url = "https://bectanse-auto.up.railway.app/canal-webhook"
        r2 = requests.post(
            f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "edited_message"]},
            timeout=8
        )
        results["set_webhook"] = r2.json()
    except Exception as e:
        results["set_webhook"] = str(e)
    try:
        # 3. Compter messages en base
        conn = get_conn()
        count = conn.run("SELECT COUNT(*) FROM canal_messages")[0][0]
        conn.close()
        results["messages_in_db"] = count
    except Exception as e:
        results["messages_in_db"] = str(e)
    try:
        # 4. Vérifier que le bot est dans le groupe
        r3 = requests.get(
            f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getChatMember",
            params={"chat_id": CANAL_GROUP_ID, "user_id": CANAL_BOT_TOKEN.split(":")[0]},
            timeout=8
        )
        results["bot_in_group"] = r3.json()
    except Exception as e:
        results["bot_in_group"] = str(e)
    import json as _json
    return f"<pre style='background:#111;color:#0f0;padding:20px;font-size:13px'>{_json.dumps(results, indent=2, ensure_ascii=False)}</pre>"


@app.route("/admin/canal-init-db")
def canal_init_db():
    """Force la création de la table canal_messages."""
    from flask import send_from_directory, request as freq
    key = freq.args.get("key", "")
    if key != ADMIN_KEY:
        return "Interdit", 403
    try:
        conn = get_conn()
        conn.run("""
            CREATE TABLE IF NOT EXISTS canal_messages (
                id           SERIAL PRIMARY KEY,
                tg_msg_id    BIGINT UNIQUE,
                text_content TEXT DEFAULT '',
                msg_type     TEXT DEFAULT 'message',
                photo_url    TEXT DEFAULT '',
                edited       BOOLEAN DEFAULT FALSE,
                sent_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.close()
        return "<pre style='background:#111;color:#0f0;padding:20px'>✅ Table canal_messages créée avec succès.</pre>"
    except Exception as e:
        return f"<pre style='background:#111;color:#f00;padding:20px'>❌ Erreur: {e}</pre>"


@app.route("/admin/push-init-db")
def push_init_db():
    """Force la création de la table push_subscriptions."""
    from flask import send_from_directory, request as freq
    key = freq.args.get("key", "")
    if key != ADMIN_KEY:
        return "Interdit", 403
    try:
        conn = get_conn()
        conn.run("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          SERIAL PRIMARY KEY,
                member_code TEXT NOT NULL,
                endpoint    TEXT UNIQUE NOT NULL,
                p256dh      TEXT NOT NULL,
                auth        TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        count = conn.run("SELECT COUNT(*) FROM push_subscriptions")[0][0]
        conn.close()
        return f"<pre style='background:#111;color:#0f0;padding:20px'>✅ Table push_subscriptions créée.\n📊 Abonnés actuels : {count}</pre>"
    except Exception as e:
        return f"<pre style='background:#111;color:#f00;padding:20px'>❌ Erreur: {e}</pre>"


# ── RELANCES AUTOMATIQUES EMAIL ───────────────────────────────────────────────

def send_email(to, subject, html_body):
    """Envoie un email via GMAIL."""
    if not GMAIL_USER or not GMAIL_PASS or not to:
        return False
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Bectanse AUTO <{GMAIL_USER}>"
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        app.logger.error(f"send_email to {to}: {e}")
        return False

def email_relance_html(nom, jours, lien_renouvellement):
    """Génère le HTML de l'email de relance."""
    if jours > 0:
        subject = f"⚠️ Ton abonnement Bectanse AUTO expire dans {jours} jour{'s' if jours > 1 else ''}"
        headline = f"Ton accès expire dans <span style='color:#F59E0B'>{jours} jour{'s' if jours > 1 else ''}</span>"
        msg = "Ne laisse pas ton copy trading s'arrêter. Renouvelle maintenant pour continuer à profiter des signaux XAU/USD en temps réel."
        cta = "Renouveler mon abonnement →"
        color = "#F59E0B" if jours <= 3 else "#5B21B6"
    else:
        subject = "❌ Ton abonnement Bectanse AUTO a expiré"
        headline = "Ton accès <span style='color:#EF4444'>a expiré</span>"
        msg = "Ton abonnement Bectanse AUTO est terminé. Reprends maintenant pour ne manquer aucun signal et continuer le copy trading sur l'or."
        cta = "Réactiver mon accès →"
        color = "#EF4444"

    return subject, f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#0A0A14;font-family:Arial,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">
    <!-- Header -->
    <div style="text-align:center;margin-bottom:28px;">
      <div style="font-size:28px;font-weight:900;letter-spacing:0.05em;color:#F59E0B;">B€CTAN$€ AUTO</div>
      <div style="font-size:11px;color:rgba(255,255,255,0.3);letter-spacing:0.2em;text-transform:uppercase;margin-top:4px;">Copy Trading XAU/USD</div>
    </div>
    <!-- Card -->
    <div style="background:#111827;border-radius:16px;padding:28px;border:1px solid rgba(91,33,182,0.3);border-top:3px solid {color};">
      <div style="font-size:22px;font-weight:700;color:#fff;margin-bottom:12px;">Bonjour {nom} 👋</div>
      <div style="font-size:18px;color:rgba(255,255,255,0.85);margin-bottom:16px;line-height:1.5;">{headline}</div>
      <div style="font-size:14px;color:rgba(255,255,255,0.55);line-height:1.7;margin-bottom:24px;">{msg}</div>
      <!-- CTA -->
      <div style="text-align:center;">
        <a href="{lien_renouvellement}" style="display:inline-block;background:{color};color:#fff;text-decoration:none;padding:14px 32px;border-radius:10px;font-size:15px;font-weight:700;letter-spacing:0.05em;">{cta}</a>
      </div>
    </div>
    <!-- Footer -->
    <div style="text-align:center;margin-top:24px;font-size:11px;color:rgba(255,255,255,0.2);">
      Bectanse AUTO — bectanse-auto.up.railway.app<br>
      Tu reçois cet email car tu es membre Bectanse AUTO.
    </div>
  </div>
</body>
</html>"""

def check_and_send_relances():
    """Vérifie chaque matin les abonnements et envoie les emails de relance."""
    try:
        conn = get_conn()
        membres = conn.run(
            """SELECT code, nom, email, date_fin FROM membres
               WHERE actif=TRUE AND email != '' AND date_fin IS NOT NULL"""
        )
        conn.close()
        lien = "https://bectanse-auto.up.railway.app/offres"
        sent = 0
        for code, nom, email, date_fin in membres:
            if not email or not date_fin:
                continue
            from datetime import date as _date
            now = datetime.now()
            jours = (date_fin.date() - now.date()).days if hasattr(date_fin, 'date') else (date_fin - now).days
            if jours in (7, 3, 1, 0):
                subject, html = email_relance_html(nom.split()[0], jours, lien)
                if send_email(email, subject, html):
                    sent += 1
                    app.logger.info(f"Relance J{jours} envoyée à {email}")
        app.logger.info(f"check_relances: {sent} emails envoyés")
    except Exception as e:
        app.logger.error(f"check_and_send_relances: {e}")

# ── STARTUP ───────────────────────────────────────────────────────────────────

def _startup():
    time.sleep(1)
    try:
        init_db()
        app.logger.info("DB ready")
        register_webhook()
        app.logger.info("Webhook enregistré")
        register_canal_webhook()
        app.logger.info("Canal webhook enregistré")
        # Publications Telegram et relances — heure de Paris.
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone='Europe/Paris')
        scheduler.add_job(
            send_eco_message, 'cron', hour=8, minute=30,
            id='telegram_economic_calendar', replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=3600
        )
        scheduler.add_job(
            job_relances_quotidiennes, 'cron', hour=9, minute=0,
            id='member_daily_reminders', replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=3600
        )
        scheduler.add_job(
            enforce_member_access_state, 'interval', minutes=5,
            id='member_access_enforcement', replace_existing=True,
            next_run_time=_paris_now(), coalesce=True, max_instances=1,
            misfire_grace_time=300
        )
        scheduler.add_job(
            process_scheduled_telegram_posts, 'interval', minutes=1,
            id='telegram_scheduled_posts', replace_existing=True,
            next_run_time=_paris_now(), coalesce=True, max_instances=1,
            misfire_grace_time=120
        )
        init_demo_account()
        scheduler.start()
        app.logger.info(
            "✅ Schedulers: calendrier 8h30 + relances 9h + centre Telegram chaque minute (Europe/Paris)"
        )
    except Exception as e:
        app.logger.error(f"startup: {e}")

# Les aperçus locaux peuvent désactiver les services externes (DB, webhooks,
# planificateurs) sans modifier le comportement de production.
if os.environ.get("BECTANSE_SKIP_STARTUP") != "1":
    threading.Thread(target=_startup, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
