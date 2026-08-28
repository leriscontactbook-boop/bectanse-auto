import os, json, secrets, string, requests, time, threading, csv, io, hashlib, hmac, unicodedata, base64, uuid, re
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from flask import send_from_directory, Flask, render_template, request, redirect, url_for, session, jsonify, Response
from werkzeug.utils import secure_filename
import pg8000.native
from cryptography.fernet import Fernet, InvalidToken
from academy_features import ensure_growth_schema, register_growth_features
from marketing_automation import (
    ensure_marketing_schema,
    mark_checkout_expired,
    mark_marketing_conversion,
    record_checkout_start,
    record_checkout_session,
    register_marketing_routes,
    run_marketing_automation,
    upsert_marketing_contact_for_member,
)

def _required_secret(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Configuration sécurisée manquante : {name}")
    return value


app = Flask(__name__)
app.secret_key = _required_secret("SECRET_KEY")
DATA_CIPHER = Fernet(_required_secret("DATA_ENCRYPTION_KEY").encode("utf-8"))


def _encrypt_value(value):
    text = str(value or "")
    if not text or text.startswith("enc:v1:"):
        return text
    return "enc:v1:" + DATA_CIPHER.encrypt(text.encode("utf-8")).decode("ascii")


def _decrypt_value(value):
    text = str(value or "")
    if not text.startswith("enc:v1:"):
        return text
    try:
        return DATA_CIPHER.decrypt(text[7:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _protect_params(params):
    protected = dict(params or {})
    if protected.get("mt_password"):
        protected["mt_password"] = _encrypt_value(protected["mt_password"])
    return protected


def _reveal_params(params):
    revealed = dict(params or {})
    if revealed.get("mt_password"):
        revealed["mt_password"] = _decrypt_value(revealed["mt_password"])
    return revealed


def _protect_history(history):
    protected = []
    for entry in history or []:
        item = dict(entry) if isinstance(entry, dict) else entry
        if isinstance(item, dict) and isinstance(item.get("params"), dict):
            item["params"] = _protect_params(item["params"])
        protected.append(item)
    return protected


def _is_masked_password(value):
    """Détecte les valeurs copiées de champs mot de passe déjà masqués."""
    txt = str(value or "").strip()
    if not txt or len(txt) < 3:
        return False
    return all(ch in {"*", "•", "x", "X"} for ch in txt)


def _extract_profile_payload(payload):
    """Normalise les champs de profil côté membre/admin."""
    if payload is None:
        payload = {}

    member_payload = {}
    params_payload = {}

    def set_member_field(field, max_len=240):
        if field in payload:
            val = str(payload.get(field, "") or "").strip()
            if field == "nom" and not val:
                raise ValueError("Le nom ne peut pas être vide.")
            if field == "email" and val and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", val):
                raise ValueError("Le format de l'email n'est pas valide.")
            member_payload[field] = val[:max_len]

    for member_field in ("nom", "email", "telephone", "telegram", "capital"):
        set_member_field(member_field)

    # Données de connexion Trading, conservées dans params pour compatibilité.
    if "plateforme" in payload:
        plateforme = str(payload.get("plateforme", "") or "").strip() or "MT4"
        params_payload["plateforme"] = plateforme[:50]
    if "mt_login" in payload:
        params_payload["mt_login"] = str(payload.get("mt_login", "") or "").strip()[:80]
    if "mt_server" in payload:
        params_payload["serveur"] = str(payload.get("mt_server", "") or "").strip()[:120]
    if "serveur" in payload and "mt_server" not in payload:
        params_payload["serveur"] = str(payload.get("serveur", "") or "").strip()[:120]

    if "mt_password" in payload:
        raw_password = str(payload.get("mt_password", "") or "").strip()
        if raw_password and not _is_masked_password(raw_password):
            params_payload["mt_password"] = raw_password[:200]

    return member_payload, params_payload


def _merge_member_profile(conn, code, member_payload, params_payload):
    """Applique les modifications profil (membres + params) de manière atomique."""
    row = conn.run("SELECT params FROM members WHERE code=:c", c=code)
    if not row:
        raise ValueError("Membre introuvable")

    raw_params = row[0][0] if row[0] and row[0][0] is not None else {}
    try:
        current_params = json.loads(raw_params) if isinstance(raw_params, str) else (raw_params or {})
    except Exception:
        current_params = {}
    current_params = _reveal_params(current_params)

    # Mise à jour colonne membre
    if member_payload:
        set_clause = ", ".join([f"{field}=:{field}" for field in member_payload.keys()])
        conn.run(f"UPDATE members SET {set_clause} WHERE code=:c", **member_payload, c=code)

    # Mise à jour des champs dans params
    if params_payload:
        merged = dict(current_params or {})
        merged.update(params_payload)
        conn.run("UPDATE members SET params=:p WHERE code=:c", p=json.dumps(_protect_params(merged)), c=code)



def _action_token(action, payload, lifetime_seconds=86400):
    body = {"action": action, "payload": payload, "expires": int(time.time()) + lifetime_seconds}
    return DATA_CIPHER.encrypt(json.dumps(body, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _action_payload(token, expected_action):
    try:
        body = json.loads(DATA_CIPHER.decrypt(str(token).encode("ascii")).decode("utf-8"))
        if body.get("action") != expected_action or int(body.get("expires", 0)) < int(time.time()):
            return None
        return body.get("payload") or {}
    except Exception:
        return None
app.config.update(
    # Une WebApp installée reste connectée. Le cookie est renouvelé à chaque usage
    # et n'est supprimé que par la route /logout ou par l'utilisateur sur son appareil.
    PERMANENT_SESSION_LIFETIME=timedelta(days=3650),
    SESSION_REFRESH_EACH_REQUEST=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=26 * 1024 * 1024,
)

PUBLIC_ORIGINS = {
    "https://acces.bectanse-academie.com",
    "https://bectanse-auto.up.railway.app",
}
WEBHOOK_PATHS = {"/bot-webhook", "/canal-webhook", "/api/stripe/analyse-credits"}


@app.before_request
def reject_cross_origin_mutations():
    """Bloque les POST externes et protège les sessions contre les requêtes forgées."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or request.path in WEBHOOK_PATHS:
        return None
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin not in PUBLIC_ORIGINS:
        return jsonify({"ok": False, "error": "Origine non autorisée"}), 403
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
        return jsonify({"ok": False, "error": "Requête intersite refusée"}), 403
    return None

@app.before_request
def keep_installed_webapp_session_persistent():
    """Convertit aussi les connexions déjà existantes en sessions persistantes."""
    if session.get("member_code") and not session.permanent:
        session.permanent = True

@app.after_request
def inject_analytics_tracker(response):
    """Ajoute le suivi aux pages HTML rendues sans toucher aux API ni à l'administration."""
    path = request.path or "/"
    content_type = response.headers.get("Content-Type", "")
    if (response.status_code != 200 or not content_type.startswith("text/html")):
        return _secure_response(response)
    if path.startswith(("/admin", "/analyse-ia", "/api/", "/static/")) or response.direct_passthrough:
        return _secure_response(response)
    try:
        body = response.get_data(as_text=True)
        marker = '<script src="/static/analytics.js" defer></script>'
        if marker not in body and "</body>" in body:
            response.set_data(body.replace("</body>", marker + "\n</body>", 1))
            response.headers.pop("Content-Length", None)
    except Exception:
        pass
    return _secure_response(response)


def _secure_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

BOT_TOKEN  = _required_secret("BOT_TOKEN")
ADMIN_ID   = _required_secret("ADMIN_ID")
ADMIN_KEY  = _required_secret("ADMIN_KEY")
DATABASE_URL = _required_secret("DATABASE_URL")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
AGENTMAIL_API_KEY = (
    os.environ.get("AGENTMAIL_API_KEY", "")
    or os.environ.get("AGENTMAIL_AGENTMAIL_API_KEY", "")
)
AGENTMAIL_INBOX_ID = os.environ.get(
    "AGENTMAIL_INBOX_ID", "bectanse-academie@agentmail.to"
)
VAPID_PUBLIC_KEY  = _required_secret("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = _required_secret("VAPID_PRIVATE_KEY")
VAPID_CLAIMS      = {"sub": "mailto:bectanseacademie@gmail.com"}
CLOUDINARY_CLOUD  = os.environ.get("CLOUDINARY_CLOUD", "")
CLOUDINARY_KEY    = os.environ.get("CLOUDINARY_KEY", "")
CLOUDINARY_SECRET = os.environ.get("CLOUDINARY_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_ANALYSIS_MODEL = os.environ.get("OPENAI_ANALYSIS_MODEL", "gpt-5.6-luna")
ANALYSIS_INITIAL_CREDITS = int(os.environ.get("ANALYSIS_INITIAL_CREDITS", "2"))
ANALYSIS_MAX_IMAGE_BYTES = 6 * 1024 * 1024
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PAYMENT_SUCCESS_URL = os.environ.get(
    "STRIPE_PAYMENT_SUCCESS_URL",
    "https://t.me/m/Pnl8NciPZmZk",
)
STRIPE_ACADEMY_PORTAL_CONFIGURATION = os.environ.get(
    "STRIPE_ACADEMY_PORTAL_CONFIGURATION",
    "bpc_1U8QMWDE6HxqPs7GiRK7sYSk",
)
POSTHOG_PROJECT_KEY = os.environ.get("POSTHOG_PROJECT_KEY", "")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://eu.i.posthog.com").rstrip("/")
ANALYSIS_PACKS = {
    "5": {"credits": 5, "amount_cents": 990, "label": "Pack Découverte"},
    "20": {"credits": 20, "amount_cents": 2490, "label": "Pack Trader"},
    "50": {"credits": 50, "amount_cents": 4990, "label": "Pack Pro"},
}
ACADEMY_SUBSCRIPTION_PLANS = {
    "price_1U8Q3DDE6HxqPs7GiK8j8f9N": {
        "id": "1_month", "label": "1 mois", "amount_cents": 50000, "days": 30,
        "payment_link_id": "plink_1U8Q7fDE6HxqPs7G8pn1mwIE",
        "payment_link": "https://buy.stripe.com/14A6oH5qE51iaJZ6ckgfu0H",
    },
    "price_1U8Q4FDE6HxqPs7GbCKnQb18": {
        "id": "3_months", "label": "3 mois", "amount_cents": 100000, "days": 90,
        "payment_link_id": "plink_1U8Q7CDE6HxqPs7GrDWCpXIU",
        "payment_link": "https://buy.stripe.com/7sY8wP2esbpG6tJ0S0gfu0G",
    },
    "price_1U8Q54DE6HxqPs7GhmeoI1pa": {
        "id": "1_year", "label": "1 an", "amount_cents": 400000, "days": 365,
        "payment_link_id": "plink_1U8Q6aDE6HxqPs7GoQWMhER2",
        "payment_link": "https://buy.stripe.com/8x23cv4mAdxO7xN0S0gfu0F",
    },
}
ACADEMY_PLAN_BY_PAYMENT_LINK = {
    plan["payment_link_id"]: (price_id, plan)
    for price_id, plan in ACADEMY_SUBSCRIPTION_PLANS.items()
}
ACADEMY_PLAN_BY_ID = {
    plan["id"]: (price_id, plan)
    for price_id, plan in ACADEMY_SUBSCRIPTION_PLANS.items()
}
ECO_BOT_TOKEN = os.environ.get("ECO_BOT_TOKEN", "")
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
                    historique        TEXT DEFAULT '[]',
                    access_level      TEXT NOT NULL DEFAULT 'member',
                    email_verified_at TIMESTAMP DEFAULT NOW()
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
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id            BIGSERIAL PRIMARY KEY,
                    visitor_id    TEXT NOT NULL,
                    session_id    TEXT NOT NULL,
                    member_code   TEXT NOT NULL DEFAULT '',
                    event_name    TEXT NOT NULL,
                    page_path     TEXT NOT NULL DEFAULT '',
                    source        TEXT NOT NULL DEFAULT 'direct',
                    medium        TEXT NOT NULL DEFAULT '',
                    campaign      TEXT NOT NULL DEFAULT '',
                    referrer_host TEXT NOT NULL DEFAULT '',
                    device_type   TEXT NOT NULL DEFAULT '',
                    browser       TEXT NOT NULL DEFAULT '',
                    properties    TEXT NOT NULL DEFAULT '{}',
                    created_at    TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.run("CREATE INDEX IF NOT EXISTS analytics_events_date_idx ON analytics_events (created_at DESC)")
            conn.run("CREATE INDEX IF NOT EXISTS analytics_events_name_idx ON analytics_events (event_name, created_at DESC)")
            conn.run("CREATE INDEX IF NOT EXISTS analytics_events_visitor_idx ON analytics_events (visitor_id, created_at DESC)")
            conn.run("CREATE INDEX IF NOT EXISTS analytics_events_session_idx ON analytics_events (session_id, created_at)")
            try:
                conn.run("ALTER TABLE analytics_events ADD COLUMN IF NOT EXISTS audience_segment TEXT NOT NULL DEFAULT 'anonymous'")
                conn.run("CREATE INDEX IF NOT EXISTS analytics_events_segment_idx ON analytics_events (audience_segment, created_at DESC)")
                conn.run("""UPDATE analytics_events events SET audience_segment=CASE
                    WHEN COALESCE(members.access_level,'member') IN ('explorer','demo') THEN 'explorer'
                    WHEN members.actif=TRUE AND (members.date_fin IS NULL OR members.date_fin>NOW()) THEN 'active_member'
                    ELSE 'expired_member' END
                    FROM members WHERE events.member_code=members.code
                      AND events.member_code<>'' AND events.audience_segment='anonymous'""")
            except Exception:
                pass
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
                    payload     TEXT NOT NULL DEFAULT '',
                    account_code TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMP DEFAULT NOW(),
                    expires_at  TIMESTAMP NOT NULL,
                    verified_at TIMESTAMP
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS prospect_verification_status_idx
                         ON prospect_email_verifications (status, expires_at)""")
            conn.run("""
                CREATE TABLE IF NOT EXISTS explorer_journey (
                    member_code     TEXT PRIMARY KEY,
                    welcome_seen_at TIMESTAMP,
                    academy_seen_at TIMESTAMP,
                    lab_seen_at     TIMESTAMP,
                    trust_seen_at   TIMESTAMP,
                    offer_seen_at   TIMESTAMP,
                    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_accounts (
                    code        TEXT PRIMARY KEY,
                    email       TEXT UNIQUE NOT NULL,
                    prenom      TEXT NOT NULL DEFAULT '',
                    actif       BOOLEAN NOT NULL DEFAULT TRUE,
                    verified_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_login  TIMESTAMP,
                    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_wallets (
                    member_code      TEXT PRIMARY KEY,
                    balance          INTEGER NOT NULL DEFAULT 2 CHECK (balance >= 0),
                    lifetime_granted INTEGER NOT NULL DEFAULT 2,
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
            conn.run("""CREATE TABLE IF NOT EXISTS security_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT NOW()
            )""")
            vapid_rotation = conn.run("""INSERT INTO security_migrations (migration_key)
                VALUES ('vapid-rotation-2026-08-14-v2')
                ON CONFLICT (migration_key) DO NOTHING RETURNING migration_key""")
            if vapid_rotation:
                conn.run("DELETE FROM push_subscriptions")
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
            conn.run("""
                CREATE TABLE IF NOT EXISTS analysis_legal_acceptances (
                    id                SERIAL PRIMARY KEY,
                    member_code       TEXT NOT NULL,
                    legal_version     TEXT NOT NULL,
                    pack_id           TEXT NOT NULL,
                    stripe_session_id TEXT UNIQUE,
                    ip_address        TEXT,
                    user_agent        TEXT,
                    accepted_at       TIMESTAMP DEFAULT NOW()
                )
            """)
            for col, typ, default in [
                ("stripe_customer_id", "TEXT", "''"),
                ("stripe_subscription_id", "TEXT", "''"),
                ("stripe_price_id", "TEXT", "''"),
                ("billing_status", "TEXT", "'legacy'"),
                ("billing_current_period_end", "TIMESTAMP", "NULL"),
                ("billing_cancel_at_period_end", "BOOLEAN", "FALSE"),
                ("admin_suspended", "BOOLEAN", "FALSE"),
            ]:
                try:
                    conn.run(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except Exception:
                    pass
            conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS members_stripe_customer_idx
                         ON members (stripe_customer_id)
                         WHERE stripe_customer_id IS NOT NULL AND stripe_customer_id <> ''""")
            conn.run("""CREATE UNIQUE INDEX IF NOT EXISTS members_stripe_subscription_idx
                         ON members (stripe_subscription_id)
                         WHERE stripe_subscription_id IS NOT NULL AND stripe_subscription_id <> ''""")
            conn.run("""
                CREATE TABLE IF NOT EXISTS stripe_academy_events (
                    event_id      TEXT PRIMARY KEY,
                    event_type    TEXT NOT NULL,
                    member_code   TEXT NOT NULL DEFAULT '',
                    status        TEXT NOT NULL DEFAULT 'processing',
                    error         TEXT NOT NULL DEFAULT '',
                    created_at    TIMESTAMP DEFAULT NOW(),
                    processed_at  TIMESTAMP
                )
            """)
            conn.run("""CREATE INDEX IF NOT EXISTS stripe_academy_events_status_idx
                         ON stripe_academy_events (status, created_at DESC)""")
            for stripe_col, stripe_type, stripe_default in [
                ("stripe_object_id", "TEXT", "''"),
                ("invoice_id", "TEXT", "''"),
                ("subscription_id", "TEXT", "''"),
                ("payment_status", "TEXT", "''"),
                ("billing_reason", "TEXT", "''"),
                ("amount_paid_cents", "INTEGER", "0"),
                ("currency", "TEXT", "''"),
            ]:
                try:
                    conn.run(
                        f"ALTER TABLE stripe_academy_events ADD COLUMN IF NOT EXISTS "
                        f"{stripe_col} {stripe_type} DEFAULT {stripe_default}"
                    )
                except Exception:
                    pass
            ensure_growth_schema(conn)
            ensure_marketing_schema(conn)
            # Migration colonnes canal_messages
            for ccol, ctyp, cdef in [
                ("audio_url","TEXT","''"),
                ("deleted","BOOLEAN","FALSE"),
                ("push_notified_at","TIMESTAMP","NULL")
            ]:
                try:
                    conn.run(f"ALTER TABLE canal_messages ADD COLUMN IF NOT EXISTS {ccol} {ctyp} DEFAULT {cdef}")
                except: pass
            for pcol, pdef in [
                ("updated_at", "TIMESTAMP DEFAULT NOW()"),
                ("last_delivery_at", "TIMESTAMP"),
                ("failure_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ]:
                try:
                    conn.run(f"ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS {pcol} {pdef}")
                except: pass
            for col, typ, default in [
                ("copy_actif","BOOLEAN","TRUE"),
                ("date_souscription","TIMESTAMP","NOW()"),
                ("date_fin","TIMESTAMP","NOW() + INTERVAL '30 days'"),
                ("email","TEXT","''"),
                ("telephone","TEXT","''"),
                ("telegram","TEXT","''"),
                ("alerte_lue","BOOLEAN","TRUE"),
                ("access_level","TEXT","'member'"),
                ("email_verified_at","TIMESTAMP","NOW()"),
            ]:
                try:
                    conn.run(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except: pass
            for col, typ, default in [
                ("payload", "TEXT", "''"),
                ("account_code", "TEXT", "''"),
            ]:
                try:
                    conn.run(f"ALTER TABLE prospect_email_verifications ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except: pass
            try:
                conn.run("UPDATE members SET access_level='demo' WHERE code='BCT-DEMO2026'")
            except Exception:
                pass
            try:
                conn.run("""UPDATE prospect_email_verifications
                            SET status='expired', payload=''
                            WHERE status='pending' AND expires_at <= NOW()""")
            except Exception:
                pass
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
                ("access_level", "TEXT", "'member'"),
                ("email_verified_at", "TIMESTAMP", "NOW()"),
            ]
            for col, typ, default in cols_to_add:
                try:
                    conn.run(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {default}")
                except: pass
            # Chiffrement progressif des anciennes données sensibles encore en clair.
            try:
                sensitive_rows = conn.run("""SELECT code, params, historique, paiement_iban,
                    paiement_bic, paiement_titulaire, paiement_crypto_adresse FROM members""")
                for member_code, raw_params, raw_history, iban, bic, holder, crypto_address in sensitive_rows:
                    try:
                        parsed_params = json.loads(raw_params) if isinstance(raw_params, str) else (raw_params or {})
                    except Exception:
                        parsed_params = {}
                    try:
                        parsed_history = json.loads(raw_history) if isinstance(raw_history, str) else (raw_history or [])
                    except Exception:
                        parsed_history = []
                    conn.run("""UPDATE members SET params=:params, historique=:history,
                        paiement_iban=:iban, paiement_bic=:bic, paiement_titulaire=:holder,
                        paiement_crypto_adresse=:crypto WHERE code=:code""",
                        params=json.dumps(_protect_params(parsed_params)),
                        history=json.dumps(_protect_history(parsed_history)),
                        iban=_encrypt_value(iban), bic=_encrypt_value(bic), holder=_encrypt_value(holder),
                        crypto=_encrypt_value(crypto_address), code=member_code)
            except Exception as encryption_error:
                app.logger.error("Migration chiffrement membres: %s", encryption_error)
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
        m["params"] = _reveal_params(m.get("params"))
        for field in ("paiement_iban", "paiement_bic", "paiement_titulaire", "paiement_crypto_adresse"):
            if m.get(field):
                m[field] = _decrypt_value(m[field])
        if m.get("copy_actif") is None: m["copy_actif"] = True
        return m
    except Exception as e:
        app.logger.error(f"get_member: {e}")
        return None


def _member_is_explorer(member):
    """Vrai pour les comptes d'observation gratuits, jamais pour un membre payant."""
    if not member:
        return False
    return (str(member.get("access_level") or "member").lower() in {"explorer", "demo"}
            or str(member.get("code") or "").upper() == "BCT-DEMO2026")


EXPLORER_JOURNEY_STEPS = (
    {
        "id": "academy",
        "column": "academy_seen_at",
        "number": "01",
        "label": "Comprendre la méthode",
        "title": "Découvre comment l’Académie te fait progresser",
        "description": "Parcours structuré, exercices et validation des acquis. Tu vois le système avant de prendre une décision.",
        "duration": "2 min",
        "href": "/academie",
        "cta": "Découvrir l’Académie",
        "icon": "academy",
    },
    {
        "id": "lab",
        "column": "lab_seen_at",
        "number": "02",
        "label": "Explorer les outils",
        "title": "Entre dans le Trader Lab",
        "description": "Analyse IA, simulateur, journal et Trade Score. Découvre comment chaque outil améliore la qualité des décisions.",
        "duration": "2 min",
        "href": "/trader-lab",
        "cta": "Explorer le Trader Lab",
        "icon": "lab",
    },
    {
        "id": "trust",
        "column": "trust_seen_at",
        "number": "03",
        "label": "Vérifier le système",
        "title": "Regarde ce que le système fait vraiment",
        "description": "Méthode, limites et sécurité. Pas de promesse floue, tu comprends précisément ce que tu rejoins.",
        "duration": "1 min",
        "href": "/centre-confiance",
        "cta": "Ouvrir le centre de confiance",
        "icon": "trust",
    },
    {
        "id": "offer",
        "column": "offer_seen_at",
        "number": "04",
        "label": "Choisir ton accès",
        "title": "Débloque ton parcours complet",
        "description": "Choisis la formule adaptée et transforme cet aperçu en véritable espace de progression personnel.",
        "duration": "1 min",
        "href": "/vip#offres",
        "cta": "Voir les formules",
        "icon": "unlock",
    },
)
EXPLORER_STEP_COLUMNS = {step["id"]: step["column"] for step in EXPLORER_JOURNEY_STEPS}


def _explorer_journey_state(conn, member_code, mark_welcome=False):
    """Construit l'expérience guidée sans modifier les accès d'un membre payant."""
    conn.run("""INSERT INTO explorer_journey (member_code, welcome_seen_at)
        VALUES (:code, CASE WHEN :welcome THEN NOW() ELSE NULL END)
        ON CONFLICT (member_code) DO UPDATE SET
            welcome_seen_at=CASE WHEN :welcome THEN COALESCE(explorer_journey.welcome_seen_at,NOW())
                                 ELSE explorer_journey.welcome_seen_at END,
            updated_at=NOW()""", code=member_code, welcome=bool(mark_welcome))
    rows = conn.run("""SELECT welcome_seen_at,academy_seen_at,lab_seen_at,trust_seen_at,offer_seen_at
        FROM explorer_journey WHERE member_code=:code""", code=member_code)
    values = rows[0] if rows else (None, None, None, None, None)
    completed_by_id = {
        "academy": bool(values[1]),
        "lab": bool(values[2]),
        "trust": bool(values[3]),
        "offer": bool(values[4]),
    }
    steps = []
    current_index = next(
        (index for index, step in enumerate(EXPLORER_JOURNEY_STEPS)
         if not completed_by_id[step["id"]]),
        len(EXPLORER_JOURNEY_STEPS) - 1,
    )
    completed_count = sum(1 for done in completed_by_id.values() if done)
    for index, definition in enumerate(EXPLORER_JOURNEY_STEPS):
        step = dict(definition)
        step["completed"] = completed_by_id[step["id"]]
        step["current"] = index == current_index and not step["completed"]
        step["locked"] = index > current_index and not step["completed"]
        steps.append(step)
    return {
        "steps": steps,
        "current": steps[current_index],
        "completed_count": completed_count,
        "progress": completed_count * 25,
        "finished": completed_count == len(EXPLORER_JOURNEY_STEPS),
    }


def _member_has_academy_access(member):
    if not member or _member_is_explorer(member) or not member.get("actif", False):
        return False
    date_fin = member.get("date_fin")
    return not date_fin or date_fin > datetime.now()


def _current_demo_mode(code=None, member=None):
    if member is None:
        code = code or session.get("member_code", "")
        member = get_member(code)
    # Tous les comptes sans abonnement actif restent connectés en observation.
    # Les contrôles serveur continuent de bloquer les services réservés.
    return not _member_has_academy_access(member)


def academy_access_required(f):
    """Protection serveur des actions réservées à l'abonnement Académie."""
    @wraps(f)
    def decorated(*args, **kwargs):
        code = session.get("member_code", "")
        member = get_member(code)
        if not _member_has_academy_access(member):
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"ok": False, "locked": True,
                                "error": "Cette action est réservée aux membres Bectanse Académie.",
                                "upgrade_url": "/vip"}), 403
            return redirect(url_for("vip_landing"))
        return f(*args, **kwargs)
    return decorated


def _new_member_code(conn):
    for _ in range(20):
        code = "BCT-" + "".join(
            secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        if not conn.run("SELECT 1 FROM members WHERE code=:code", code=code):
            return code
    raise RuntimeError("Impossible de générer un code d’accès unique")


def _migrate_legacy_analysis_account(conn, email, member_code):
    """Rattache les éventuels achats BAI historiques au nouveau compte BCT personnel."""
    rows = conn.run("SELECT code FROM analysis_accounts WHERE LOWER(email)=LOWER(:email)", email=email)
    if not rows:
        return
    old_code = rows[0][0]
    wallet = conn.run("SELECT balance, lifetime_granted, lifetime_spent FROM analysis_wallets WHERE member_code=:code",
                      code=old_code)
    target = conn.run("SELECT balance, lifetime_granted, lifetime_spent FROM analysis_wallets WHERE member_code=:code",
                      code=member_code)
    if wallet and target:
        conn.run("""UPDATE analysis_wallets SET balance=:balance, lifetime_granted=:granted,
                    lifetime_spent=:spent, updated_at=NOW() WHERE member_code=:code""",
                 balance=int(wallet[0][0]) + int(target[0][0]),
                 granted=int(wallet[0][1]) + int(target[0][1]),
                 spent=int(wallet[0][2]) + int(target[0][2]), code=member_code)
        conn.run("DELETE FROM analysis_wallets WHERE member_code=:code", code=old_code)
    elif wallet:
        conn.run("UPDATE analysis_wallets SET member_code=:new WHERE member_code=:old",
                 new=member_code, old=old_code)
    for table in ("analysis_jobs", "analysis_credit_ledger", "analysis_purchases",
                  "analysis_legal_acceptances"):
        conn.run(f"UPDATE {table} SET member_code=:new WHERE member_code=:old",
                 new=member_code, old=old_code)
    conn.run("UPDATE analysis_accounts SET actif=FALSE WHERE code=:code", code=old_code)


def _grant_member_beta_credits(conn, member_code):
    """Crédite une seule fois les 2 analyses incluses lors de l'activation membre."""
    conn.run("""INSERT INTO analysis_wallets
        (member_code,balance,lifetime_granted,lifetime_spent)
        VALUES (:code,0,0,0) ON CONFLICT (member_code) DO NOTHING""", code=member_code)
    wallet = conn.run("""SELECT balance FROM analysis_wallets
        WHERE member_code=:code FOR UPDATE""", code=member_code)
    balance = int(wallet[0][0])
    reference = f"member-beta:{member_code}"
    inserted = conn.run("""INSERT INTO analysis_credit_ledger
        (member_code,delta,balance_after,reason,reference)
        VALUES (:code,:credits,:balance,'member_beta',:reference)
        ON CONFLICT (reference) DO NOTHING RETURNING id""",
        code=member_code, credits=ANALYSIS_INITIAL_CREDITS,
        balance=balance + ANALYSIS_INITIAL_CREDITS, reference=reference)
    if inserted:
        conn.run("""UPDATE analysis_wallets SET
            balance=balance+:credits,lifetime_granted=lifetime_granted+:credits,
            updated_at=NOW() WHERE member_code=:code""",
            credits=ANALYSIS_INITIAL_CREDITS, code=member_code)


def _create_or_reuse_explorer(conn, email, prenom):
    rows = conn.run("""SELECT code, COALESCE(access_level,'member') FROM members
        WHERE LOWER(email)=LOWER(:email) ORDER BY created_at DESC LIMIT 1""", email=email)
    if rows:
        code, access_level = rows[0]
        if access_level not in {"explorer", "demo"}:
            # Le détenteur de l'adresse peut retrouver son compte membre sans créer de doublon.
            conn.run("UPDATE members SET email_verified_at=COALESCE(email_verified_at,NOW()), last_login=NOW() WHERE code=:code",
                     code=code)
            return code
        conn.run("""UPDATE members SET nom=:nom, actif=TRUE, copy_actif=FALSE,
                    access_level='explorer', email_verified_at=NOW(), last_login=NOW(),
                    date_fin=NULL WHERE code=:code""", nom=prenom or "Compte Explorer", code=code)
    else:
        code = _new_member_code(conn)
        conn.run("""INSERT INTO members
            (code,nom,capital,actif,copy_actif,date_souscription,date_fin,email,
             params,historique,access_level,email_verified_at)
            VALUES (:code,:nom,'—',TRUE,FALSE,NOW(),NULL,:email,:params,'[]','explorer',NOW())""",
            code=code, nom=prenom or "Compte Explorer", email=email,
            params=json.dumps(default_params()))
    _migrate_legacy_analysis_account(conn, email, code)
    return code


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
            WHERE COALESCE(access_level, 'member') NOT IN ('explorer', 'demo')
              AND (actif=FALSE OR (date_fin IS NOT NULL AND date_fin <= NOW()))
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
            vapid_claims=dict(VAPID_CLAIMS),
            timeout=10,
            ttl=86400,
            headers={"Urgency": "high"}
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
    """Compatibilité historique : utilise désormais le stockage Web Push actif."""
    return send_push_to_all(title, body, url)


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
        if str(session.get("member_code", "")).upper() == "BCT-DEMO2026":
            # L'ancien compte partagé n'est plus une identité valide : chacun
            # doit confirmer son e-mail et recevoir son propre code BCT.
            session.clear()
            return redirect(url_for("login"))
        # Un compte expiré ou suspendu conserve son espace d'observation.
        # Les actions payantes sont contrôlées indépendamment par
        # academy_access_required afin qu'aucune requête directe ne les contourne.
        try:
            enforce_member_access_state()
            if not get_member(session["member_code"]):
                session.clear()
                return redirect(url_for("login"))
        except Exception as error:
            app.logger.error("Validation session membre: %s", error)
            return "Service temporairement indisponible", 503
        return f(*args, **kwargs)
    return decorated

# ── ROUTES ───────────────────────────────────────────────────────────────────


@app.route("/vip")
def vip_landing():
    return send_from_directory("static/vip", "index.html")


@app.route("/support", methods=["GET", "POST"])
@login_required
@academy_access_required
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
            demo_mode=_current_demo_mode(code, member))

@app.route("/parrainage")
@login_required
@academy_access_required
def parrainage():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    # Le compteur et les gains sont crédités sur le compte du parrain au moment
    # où le filleul est validé. L'ancienne requête additionnait par erreur les
    # compteurs des filleuls et pouvait donc afficher 0 € malgré un solde réel.
    recent_referrals = []
    conn = None
    try:
        conn = get_conn()
        rows = conn.run("""SELECT COALESCE(filleuls_count,0), COALESCE(gains_parrainage,0)
                           FROM members WHERE UPPER(code)=UPPER(:code)""", code=code)
        total_filleuls = int(rows[0][0] or 0) if rows else 0
        gains = int(rows[0][1] or 0) if rows else 0
        referrals = conn.run("""SELECT nom, created_at
                                FROM members
                                WHERE UPPER(parrain_code)=UPPER(:code)
                                ORDER BY created_at DESC LIMIT 6""", code=code)
        for nom, created_at in referrals:
            parts = str(nom or "Membre Bectanse").strip().split()
            display_name = parts[0]
            if len(parts) > 1 and parts[-1]:
                display_name += f" {parts[-1][0].upper()}."
            recent_referrals.append({"nom": display_name, "date": created_at})
        niveau = "Starter"
        if total_filleuls >= 20: niveau = "Elite"
        elif total_filleuls >= 10: niveau = "Ambassador"
        elif total_filleuls >= 5: niveau = "Bronze"
        parrain_stats = {"total": total_filleuls, "gains": gains, "niveau": niveau}
    except Exception as error:
        app.logger.error("Chargement parrainage %s: %s", code, error)
        parrain_stats = {
            "total": int(member.get("filleuls_count") or 0),
            "gains": int(member.get("gains_parrainage") or 0),
            "niveau": "Starter"
        }
    finally:
        if conn:
            try: conn.close()
            except Exception: pass
    return render_template("parrainage.html", member=member, parrain_stats=parrain_stats,
                           recent_referrals=recent_referrals)

@app.route("/rejoindre/<parrain_code>")
def rejoindre(parrain_code):
    """Landing page parrainage — accessible sans connexion"""
    conn = None
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
    activation_token = _action_token("activate_prospect", {
        "nom": nom_complet, "email": email, "tel": telephone,
        "offre": offre, "parrain": parrain_code,
    })
    activer_url = f"https://acces.bectanse-academie.com/activer-prospect?token={activation_token}"
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
    payload = _action_payload(request.args.get("token", ""), "activate_prospect")
    if not payload:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403

    nom_complet = str(payload.get("nom", ""))
    email       = str(payload.get("email", ""))
    telephone   = str(payload.get("tel", ""))
    offre       = str(payload.get("offre", ""))
    parrain_code= str(payload.get("parrain", "")).upper()

    # Créer le membre
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    try:
        conn = get_conn()
        conn.run(
            "INSERT INTO members (code,nom,capital,email,telephone,parrain_code,params,historique) VALUES (:c,:n,:cap,:e,:t,:pr,:p,:h)",
            c=code, n=nom_complet, cap=offre, e=email, t=telephone,
            pr=parrain_code, p=json.dumps(default_params()), h=json.dumps([])
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
        set_dates_url = f"https://acces.bectanse-academie.com/set-dates/{code}?token={_action_token('set_dates', {'code': code}, 604800)}"
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
@academy_access_required
def save_paiement():
    """Sauvegarde les infos de paiement du membre pour recevoir ses commissions"""
    code = session["member_code"]
    data = request.get_json(silent=True) or {}
    ptype = str(data.get("type", "")).strip().lower()
    if ptype not in {"virement", "crypto"}:
        return jsonify({"ok": False, "error": "Mode de paiement invalide."}), 400

    titulaire = str(data.get("titulaire", "")).strip()[:120]
    iban = re.sub(r"\s+", "", str(data.get("iban", "")).upper())[:34]
    bic = re.sub(r"\s+", "", str(data.get("bic", "")).upper())[:11]
    reseau = str(data.get("reseau", "")).strip().upper()
    adresse = str(data.get("adresse", "")).strip()[:220]

    if ptype == "virement":
        if len(titulaire) < 2:
            return jsonify({"ok": False, "error": "Indique le nom complet du titulaire."}), 400
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
            return jsonify({"ok": False, "error": "Le format de l’IBAN n’est pas valide."}), 400
        if not re.fullmatch(r"[A-Z0-9]{8}(?:[A-Z0-9]{3})?", bic):
            return jsonify({"ok": False, "error": "Le format du BIC/SWIFT n’est pas valide."}), 400
    else:
        allowed_networks = {"TRC20", "ERC20", "BEP20", "BTC", "ETH"}
        if reseau not in allowed_networks:
            return jsonify({"ok": False, "error": "Réseau crypto non pris en charge."}), 400
        if len(adresse) < 15:
            return jsonify({"ok": False, "error": "L’adresse du wallet semble incomplète."}), 400
    try:
        conn = get_conn()
        if ptype == "virement":
            conn.run("""UPDATE members SET paiement_type=:t, paiement_iban=:i,
                       paiement_bic=:b, paiement_titulaire=:ti WHERE code=:c""",
                     t="virement", i=_encrypt_value(iban), b=_encrypt_value(bic),
                     ti=_encrypt_value(titulaire), c=code)
        else:
            conn.run("""UPDATE members SET paiement_type=:t, paiement_crypto_reseau=:r,
                       paiement_crypto_adresse=:a WHERE code=:c""",
                     t="crypto", r=reseau, a=_encrypt_value(adresse), c=code)
        return jsonify({"ok": True, "type": ptype})
    except Exception as e:
        app.logger.error("Sauvegarde paiement parrainage %s: %s", code, e)
        return jsonify({"ok": False, "error": "Enregistrement impossible pour le moment."}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

@app.route("/sw.js")
def service_worker():
    return app.send_static_file("sw.js"), 200, {"Content-Type": "application/javascript"}

@app.route("/formation")
@login_required
def formation():
    return redirect(url_for("academie"))


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
    if _member_is_explorer(member):
        return redirect(url_for("explorer_home"))
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
    demo_mode = _current_demo_mode(code, member)
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


@app.route("/explorer")
@login_required
def explorer_home():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    if not _member_is_explorer(member):
        return redirect(url_for("accueil"))
    conn = get_conn()
    try:
        journey = _explorer_journey_state(conn, code, mark_welcome=True)
    finally:
        conn.close()
    return render_template("explorer.html", member=member, demo_mode=True, journey=journey)


@app.route("/api/explorer/progress", methods=["POST"])
@login_required
def explorer_progress():
    code = session["member_code"]
    member = get_member(code)
    if not _member_is_explorer(member):
        return jsonify({"ok": False, "error": "Ce parcours est réservé au mode Explorer."}), 403
    data = request.get_json(silent=True) or {}
    step_id = str(data.get("step", "")).strip().lower()
    column = EXPLORER_STEP_COLUMNS.get(step_id)
    if not column:
        return jsonify({"ok": False, "error": "Étape inconnue."}), 400
    conn = get_conn()
    try:
        conn.run("""INSERT INTO explorer_journey (member_code)
            VALUES (:code) ON CONFLICT (member_code) DO NOTHING""", code=code)
        conn.run(f"""UPDATE explorer_journey SET {column}=COALESCE({column},NOW()),
            updated_at=NOW() WHERE member_code=:code""", code=code)
        journey = _explorer_journey_state(conn, code)
    finally:
        conn.close()
    return jsonify({
        "ok": True,
        "step": step_id,
        "progress": journey["progress"],
        "completed_count": journey["completed_count"],
        "next_step": journey["current"]["id"] if not journey["finished"] else "complete",
    })


@app.route("/presentation-academie")
@login_required
def presentation_academie():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    explorer_account = _member_is_explorer(member)
    if explorer_account:
        conn = get_conn()
        try:
            conn.run("""INSERT INTO explorer_journey (member_code, academy_seen_at)
                VALUES (:code,NOW()) ON CONFLICT (member_code) DO UPDATE SET
                academy_seen_at=COALESCE(explorer_journey.academy_seen_at,NOW()),updated_at=NOW()""",
                code=code)
        finally:
            conn.close()
    return render_template("presentation_academie.html", member=member,
                           demo_mode=_current_demo_mode(code, member),
                           back_url="/explorer" if explorer_account else "/accueil")


@app.route("/presentation-academie/telecharger")
@login_required
def download_presentation_academie():
    return send_from_directory(
        os.path.join(app.static_folder, "guides"),
        "bectanse-academie-presentation.pdf",
        as_attachment=True,
        download_name="Bectanse-Academie-Presentation.pdf",
    )


@app.route("/preview-explorer")
def preview_explorer():
    """Aperçu local sans compte réel, inaccessible depuis le domaine public."""
    if request.host.split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
        return "Introuvable", 404
    preview_member = {
        "code": "BCT-EXPLORER",
        "nom": "Leris Explorer",
        "email": "explorer@bectanse-academie.com",
        "access_level": "explorer",
    }
    preview_steps = []
    for index, definition in enumerate(EXPLORER_JOURNEY_STEPS):
        step = dict(definition)
        step.update({"completed": index == 0, "current": index == 1, "locked": index > 1})
        preview_steps.append(step)
    preview_journey = {
        "steps": preview_steps,
        "current": preview_steps[1],
        "completed_count": 1,
        "progress": 25,
        "finished": False,
    }
    return render_template("explorer.html", member=preview_member,
                           demo_mode=True, journey=preview_journey, preview_mode=True)


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

_ADMIN_LOGIN_ATTEMPTS = {}
_ADMIN_LOGIN_LOCK = threading.Lock()


def _admin_session_valid():
    return bool(session.get("admin_authenticated"))


def _admin_request_allowed():
    if _admin_session_valid():
        return True
    supplied = request.headers.get("X-Admin-Key", "")
    return bool(supplied) and hmac.compare_digest(str(supplied), str(ADMIN_KEY))


def _safe_admin_next(candidate):
    """Conserve uniquement une destination interne à l'administration."""
    target = str(candidate or "").strip()
    if not target:
        return "/admin-panel"
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/admin"):
        return "/admin-panel"
    return target


@app.before_request
def inject_server_side_admin_credential():
    """Compatibilité des anciennes routes sans jamais renvoyer la clé au navigateur."""
    if not _admin_session_valid() or not request.path.startswith(("/admin", "/api/canal/", "/analyse-ia", "/api/analyse-ia")):
        return None
    args = request.args.copy()
    args["key"] = ADMIN_KEY
    request.__dict__["args"] = args
    if request.is_json:
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            data["key"] = ADMIN_KEY
    return None

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _admin_request_allowed():
            if request.method == "GET" and request.accept_mimetypes.accept_html:
                destination = request.full_path.rstrip("?")
                return redirect(url_for("admin_panel", next=destination))
            return jsonify({"ok": False, "error": "Non autorisé"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route("/admin-panel")
def admin_panel():
    next_path = _safe_admin_next(request.args.get("next"))
    if not _admin_session_valid():
        return render_template("admin_login.html", next_path=next_path)
    if next_path != "/admin-panel":
        return redirect(next_path)
    return render_template("admin_panel.html", admin_key="")

@app.route("/admin-panel/login", methods=["POST"])
def admin_panel_login():
    key = request.form.get("key","")
    next_path = _safe_admin_next(request.form.get("next"))
    remote = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    now = time.time()
    with _ADMIN_LOGIN_LOCK:
        recent = [stamp for stamp in _ADMIN_LOGIN_ATTEMPTS.get(remote, []) if now - stamp < 900]
        if len(recent) >= 5:
            return render_template(
                "admin_login.html",
                error="Trop de tentatives. Réessaie dans 15 minutes.",
                next_path=next_path,
            ), 429
    if hmac.compare_digest(str(key), str(ADMIN_KEY)):
        session.clear()
        session["admin_authenticated"] = True
        session.permanent = True
        with _ADMIN_LOGIN_LOCK:
            _ADMIN_LOGIN_ATTEMPTS.pop(remote, None)
        return redirect(next_path)
    with _ADMIN_LOGIN_LOCK:
        recent.append(now)
        _ADMIN_LOGIN_ATTEMPTS[remote] = recent
    return render_template("admin_login.html", error="Clé incorrecte", next_path=next_path)


@app.route("/admin-panel/logout", methods=["POST"])
def admin_panel_logout():
    session.pop("admin_authenticated", None)
    return redirect("/admin-panel")


@app.route("/admin/telegram-automation")
def admin_telegram_automation():
    if not _admin_session_valid():
        return redirect("/admin-panel")
    return render_template(
        "admin_telegram_automation.html",
        admin_key="",
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
        rows = conn.run("SELECT code,nom,capital,actif,copy_actif,date_fin,email,telephone,telegram,parrain_code,filleuls_count,gains_parrainage,created_at,COALESCE(access_level,'member') FROM members WHERE code <> 'BCT-DEMO2026' ORDER BY created_at DESC")
        cols = ["code","nom","capital","actif","copy_actif","date_fin","email","telephone","telegram","parrain_code","filleuls_count","gains_parrainage","created_at","access_level"]
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
        membre = conn.run("SELECT date_fin, actif, params FROM members WHERE code=:c", c=code)
        if not membre:
            conn.close()
            return jsonify({"ok":False,"error":"Membre introuvable"}), 404

        # Champs modifiables via le profil (membre + admin)
        profile_payload_member, profile_payload_params = _extract_profile_payload(data)
        if profile_payload_member or profile_payload_params:
            _merge_member_profile(conn, code, profile_payload_member, profile_payload_params)

        # Champs de gestion abonnement/copy/training
        if "actif" in data:
            if not isinstance(data["actif"], bool):
                conn.close()
                return jsonify({"ok":False,"error":"Statut invalide"}), 400
            if data["actif"]:
                conn.run("""UPDATE members SET actif=TRUE,admin_suspended=FALSE WHERE code=:c
                    AND (date_fin IS NULL OR date_fin > NOW())""", c=code)
            else:
                conn.run("""UPDATE members SET actif=FALSE,copy_actif=FALSE,
                    admin_suspended=TRUE WHERE code=:c""", c=code)
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
    except ValueError as e:
        return jsonify({"ok":False,"error":str(e)}), 400
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/profil", methods=["POST"])
@login_required
def api_profil_update():
    code = session["member_code"]
    data = request.get_json(silent=True) or {}
    try:
        member_payload, params_payload = _extract_profile_payload(data)
        if not member_payload and not params_payload:
            return jsonify({"ok": False, "error": "Aucune modification fournie"}), 400

        conn = get_conn()
        _merge_member_profile(conn, code, member_payload, params_payload)
        conn.close()
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        app.logger.error("api/profil: %s", e)
        return jsonify({"ok": False, "error": "Impossible de mettre à jour le profil"}), 500

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
        if not str(contenu).strip():
            return jsonify({"ok":False,"error":"Message vide"}), 400
        enforce_member_access_state()
        conn = get_conn()
        rows = conn.run("""SELECT COUNT(*) FROM members
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")
        total = rows[0][0]
        conn.run("""UPDATE members SET notif_type=:t, notif_message=:m, notif_lue=FALSE
            WHERE actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""",
            t=notif_type, m=contenu)
        conn.close()
        titles = {"alerte":"🔴 Alerte Bectanse","message":"💜 Message Bectanse",
                  "resultat":"🟢 Résultat Bectanse","maintenance":"🔧 Maintenance Bectanse"}
        push_result = send_push_to_all(titles.get(notif_type,"Bectanse AUTO"), contenu, "/accueil")
        return jsonify({"ok":True,"total":total,"push":push_result})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/admin/api/notif/individuelle", methods=["POST"])
def admin_api_notif_individuelle():
    key = request.json.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        code = request.json.get("code")
        contenu = request.json.get("contenu","")
        if not str(contenu).strip():
            return jsonify({"ok":False,"error":"Message vide"}), 400
        enforce_member_access_state()
        conn = get_conn()
        rows = conn.run("""SELECT nom FROM members WHERE code=:c AND actif=TRUE
            AND (date_fin IS NULL OR date_fin>NOW())""", c=code)
        if not rows:
            conn.close()
            return jsonify({"ok":False,"error":"Membre introuvable"})
        conn.run("UPDATE members SET notif_type='individuelle', notif_message=:m, notif_lue=FALSE WHERE code=:c", m=contenu, c=code)
        conn.close()
        push_result = send_push_to_member(code, "💬 Message personnel", contenu, "/accueil")
        return jsonify({"ok":True,"nom":rows[0][0],"push":push_result})
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

# ── ANALYTIQUE SITE & CONVERSION ─────────────────────────────────────────────
ANALYTICS_EVENTS = {
    "page_view", "cta_click", "telegram_click", "registration_start",
    "registration_submit", "registration_complete", "checkout_start",
    "checkout_complete", "app_explore", "notification_interest",
    "notification_enabled", "login_success", "form_submit",
    "analysis_start", "analysis_complete", "explorer_step", "explorer_offer_view",
    "page_engaged", "scroll_depth", "page_exit", "media_play", "faq_open"
}

def _analytics_client_info(user_agent):
    ua = (user_agent or "").lower()
    device = "mobile" if any(x in ua for x in ("iphone", "android", "mobile")) else "desktop"
    if "ipad" in ua or "tablet" in ua:
        device = "tablet"
    if "edg/" in ua: browser = "Edge"
    elif "firefox/" in ua: browser = "Firefox"
    elif "chrome/" in ua or "crios/" in ua: browser = "Chrome"
    elif "safari/" in ua: browser = "Safari"
    else: browser = "Autre"
    return device, browser

def _posthog_capture(payload):
    if not POSTHOG_PROJECT_KEY:
        return
    try:
        properties = dict(payload.get("properties") or {})
        properties.update({
            "distinct_id": payload["visitor_id"],
            "$session_id": payload["session_id"],
            "$current_url": payload.get("page_path", ""),
            "$referrer": payload.get("referrer_host", ""),
            "source": payload.get("source", "direct"),
            "medium": payload.get("medium", ""),
            "campaign": payload.get("campaign", ""),
            "device_type": payload.get("device_type", ""),
            "browser": payload.get("browser", ""),
            "audience_segment": payload.get("audience_segment", "anonymous")
        })
        requests.post(
            f"{POSTHOG_HOST}/i/v0/e/",
            json={"api_key": POSTHOG_PROJECT_KEY, "event": payload["event_name"], "properties": properties},
            timeout=5
        )
    except Exception as exc:
        app.logger.warning("PostHog capture failed: %s", exc)

@app.route("/api/analytics/event", methods=["POST"])
def analytics_event():
    data = request.get_json(silent=True) or {}
    event_name = str(data.get("event_name", ""))[:60]
    visitor_id = str(data.get("visitor_id", ""))[:80]
    session_id = str(data.get("session_id", ""))[:80]
    if event_name not in ANALYTICS_EVENTS:
        return jsonify({"ok": False, "error": "Événement invalide"}), 400
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", visitor_id) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", session_id):
        return jsonify({"ok": False, "error": "Identifiant invalide"}), 400
    page_path = str(data.get("page_path", "/"))[:300]
    if page_path.startswith("/admin") or page_path.startswith("/analyse-ia"):
        return ("", 204)
    source = str(data.get("source", "direct") or "direct")[:100]
    medium = str(data.get("medium", ""))[:100]
    campaign = str(data.get("campaign", ""))[:120]
    try:
        referrer_host = urlparse(str(data.get("referrer", ""))).hostname or ""
    except Exception:
        referrer_host = ""
    raw_properties = data.get("properties") if isinstance(data.get("properties"), dict) else {}
    properties = {}
    for key, value in list(raw_properties.items())[:12]:
        if isinstance(value, (str, int, float, bool)):
            properties[str(key)[:50]] = str(value)[:250] if isinstance(value, str) else value
    device_type, browser = _analytics_client_info(request.headers.get("User-Agent", ""))
    member_code = str(session.get("member_code", ""))[:80]
    payload = {
        "visitor_id": visitor_id, "session_id": session_id, "member_code": member_code,
        "event_name": event_name, "page_path": page_path, "source": source,
        "medium": medium, "campaign": campaign, "referrer_host": referrer_host,
        "device_type": device_type, "browser": browser, "properties": properties,
        "audience_segment": "anonymous",
    }
    conn = None
    try:
        conn = get_conn()
        if member_code:
            member_rows = conn.run("""SELECT COALESCE(access_level,'member'),actif,date_fin
                FROM members WHERE code=:code LIMIT 1""", code=member_code)
            if member_rows:
                access_level, member_active, member_end = member_rows[0]
                if str(access_level) in {"explorer", "demo"}:
                    payload["audience_segment"] = "explorer"
                elif bool(member_active) and (member_end is None or member_end > datetime.now()):
                    payload["audience_segment"] = "active_member"
                else:
                    payload["audience_segment"] = "expired_member"
        conn.run("""INSERT INTO analytics_events
            (visitor_id, session_id, member_code, event_name, page_path, source, medium,
             campaign, referrer_host, device_type, browser, properties,audience_segment)
            VALUES (:visitor_id, :session_id, :member_code, :event_name, :page_path, :source,
                    :medium, :campaign, :referrer_host, :device_type, :browser, :properties,
                    :audience_segment)""",
            **{**payload, "properties": json.dumps(properties, ensure_ascii=False)})
        if event_name == "checkout_start" and member_code:
            record_checkout_start(
                conn, member_code, properties.get("destination", ""),
                source=source, medium=medium, campaign=campaign,
            )
    except Exception as exc:
        app.logger.error("Analytics storage failed: %s", exc)
        return jsonify({"ok": False}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass
    if POSTHOG_PROJECT_KEY:
        threading.Thread(target=_posthog_capture, args=(payload,), daemon=True).start()
    return ("", 204)

def _analytics_period_window(period, now=None):
    """Retourne des bornes UTC naïves, calculées selon les jours Europe/Paris."""
    period = period if period in {"1d", "7d", "30d", "90d"} else "7d"
    now_paris = now or datetime.now(PARIS_TZ)
    if now_paris.tzinfo is None:
        now_paris = now_paris.replace(tzinfo=PARIS_TZ)
    else:
        now_paris = now_paris.astimezone(PARIS_TZ)
    if period == "1d":
        since_paris = now_paris.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        previous_since_paris = since_paris - timedelta(hours=24)
        bucket = "hour"
        label = "24 créneaux horaires"
    else:
        days = int(period[:-1])
        start_day = now_paris.date() - timedelta(days=days - 1)
        since_paris = datetime.combine(start_day, datetime.min.time(), tzinfo=PARIS_TZ)
        previous_since_paris = since_paris - timedelta(days=days)
        bucket = "day"
        label = f"{days} jours calendaires"
    previous_until_paris = previous_since_paris + (now_paris - since_paris)
    utc = ZoneInfo("UTC")
    to_db = lambda value: value.astimezone(utc).replace(tzinfo=None)
    return {
        "period": period,
        "since": to_db(since_paris),
        "until": to_db(now_paris),
        "previous_since": to_db(previous_since_paris),
        "previous_until": to_db(previous_until_paris),
        "since_paris": since_paris,
        "until_paris": now_paris,
        "bucket": bucket,
        "label": label,
    }


def _analytics_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous == 0:
        return None if current else 0.0
    return round((current - previous) / previous * 100, 1)


def _analytics_local_iso(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(PARIS_TZ).isoformat(timespec="seconds")


def _analytics_visitor_payload(rows):
    return [{
        "visitor_id": str(r[0]), "visitor_label": f"VIS-{str(r[0])[:8].upper()}",
        "member_code": str(r[1] or ""), "name": str(r[2] or ""),
        "page_views": int(r[3]), "sessions": int(r[4]),
        "first_seen": _analytics_local_iso(r[5]), "last_seen": _analytics_local_iso(r[6]),
        "source": str(r[7]), "landing_page": str(r[8] or "/"),
        "last_page": str(r[9] or "/"), "device": str(r[10]),
        "returning": bool(r[11]), "segment": str(r[12]),
    } for r in rows]


@app.route("/admin/api/analytics", methods=["GET"])
def admin_api_analytics():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    window = _analytics_period_window(request.args.get("period", "7d"))
    params = {"since": window["since"], "until": window["until"]}
    previous_params = {
        "since": window["previous_since"], "until": window["previous_until"]
    }
    conn = None
    try:
        conn = get_conn()

        def scalar(sql, query_params=None):
            return int(conn.run(sql, **(query_params or params))[0][0] or 0)

        traffic_sql = """SELECT COUNT(*),COUNT(DISTINCT visitor_id),COUNT(DISTINCT session_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view'"""
        page_views, visitors, sessions_count = [int(x or 0) for x in conn.run(traffic_sql, **params)[0]]
        previous_page_views, previous_visitors, previous_sessions = [
            int(x or 0) for x in conn.run(traffic_sql, **previous_params)[0]
        ]
        prospect_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view'
              AND audience_segment IN ('anonymous','explorer','expired_member')""")
        previous_prospect_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view'
              AND audience_segment IN ('anonymous','explorer','expired_member')""", previous_params)
        registrations = scalar("""SELECT COUNT(*) FROM analytics_events
            WHERE created_at>=:since AND created_at<:until
              AND event_name='registration_complete'""")
        explorer_activations = scalar("""SELECT COUNT(*) FROM prospect_email_verifications
            WHERE verified_at>=:since AND verified_at<:until
              AND status='verified' AND source='explorer'""")
        previous_explorer = scalar("""SELECT COUNT(*) FROM prospect_email_verifications
            WHERE verified_at>=:since AND verified_at<:until
              AND status='verified' AND source='explorer'""", previous_params)
        checkout_starts = scalar("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE started_at>=:since AND started_at<:until AND stripe_session_id<>''""")
        previous_checkouts = scalar("""SELECT COUNT(*) FROM marketing_checkout_intents
            WHERE started_at>=:since AND started_at<:until AND stripe_session_id<>''""", previous_params)
        cta_clicks = scalar("""SELECT COUNT(*) FROM analytics_events
            WHERE created_at>=:since AND created_at<:until
              AND event_name IN ('cta_click','telegram_click','registration_start',
                                 'checkout_start','app_explore')""")

        payment_sql = """WITH confirmed AS (
              SELECT COALESCE(NULLIF(invoice_id,''),NULLIF(stripe_object_id,''),event_id) AS payment_key,
                     MAX(amount_paid_cents) AS amount_paid_cents,
                     BOOL_OR(event_type='invoice.paid' AND billing_reason='subscription_cycle') AS renewal
              FROM stripe_academy_events
              WHERE processed_at>=:since AND processed_at<:until AND status='processed'
                AND (event_type='invoice.paid' OR
                     (event_type='checkout.session.completed'
                      AND payment_status IN ('paid','no_payment_required')))
              GROUP BY 1
            )
            SELECT COUNT(*) FILTER (WHERE NOT renewal),COUNT(*) FILTER (WHERE renewal),
                   COALESCE(SUM(amount_paid_cents),0) FROM confirmed"""
        confirmed_payments, confirmed_renewals, revenue_cents = [
            int(x or 0) for x in conn.run(payment_sql, **params)[0]
        ]
        previous_payments, previous_renewals, previous_revenue = [
            int(x or 0) for x in conn.run(payment_sql, **previous_params)[0]
        ]

        session_row = conn.run("""WITH session_rollup AS (
              SELECT session_id,COUNT(*) AS pages,MIN(created_at) AS first_seen,
                     MAX(created_at) AS last_seen
              FROM analytics_events
              WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
              GROUP BY session_id
            )
            SELECT COALESCE(AVG(pages),0),
                   COALESCE(100.0*COUNT(*) FILTER (WHERE pages=1)/NULLIF(COUNT(*),0),0),
                   COALESCE(AVG(LEAST(EXTRACT(EPOCH FROM (last_seen-first_seen)),1800))
                            FILTER (WHERE pages>1),0)
            FROM session_rollup""", **params)[0]
        pages_per_session = round(float(session_row[0] or 0), 2)
        single_page_rate = round(float(session_row[1] or 0), 1)
        avg_session_seconds = int(float(session_row[2] or 0))
        returning_visitors = scalar("""SELECT COUNT(DISTINCT current_events.visitor_id)
            FROM analytics_events current_events
            WHERE current_events.created_at>=:since AND current_events.created_at<:until
              AND current_events.event_name='page_view'
              AND EXISTS (SELECT 1 FROM analytics_events historic
                          WHERE historic.visitor_id=current_events.visitor_id
                            AND historic.event_name='page_view'
                            AND historic.created_at<:since)""")
        identified_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view' AND member_code<>''""")
        active_member_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view' AND audience_segment='active_member'""")
        explorer_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view' AND audience_segment='explorer'""")
        expired_member_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_view' AND audience_segment='expired_member'""")
        engaged_visitors = scalar("""SELECT COUNT(DISTINCT visitor_id)
            FROM analytics_events WHERE created_at>=:since AND created_at<:until
              AND event_name='page_engaged'""")
        media_plays = scalar("""SELECT COUNT(*) FROM analytics_events
            WHERE created_at>=:since AND created_at<:until AND event_name='media_play'""")
        engagement_rows = conn.run("""SELECT event_name,properties FROM analytics_events
            WHERE created_at>=:since AND created_at<:until
              AND event_name IN ('page_exit','scroll_depth')""", **params)
        active_durations = []
        exit_scroll_depths = []
        for event_name, raw_properties in engagement_rows:
            try:
                props = json.loads(raw_properties or "{}") if isinstance(raw_properties, str) else (raw_properties or {})
            except Exception:
                props = {}
            if event_name == "page_exit":
                try:
                    active_durations.append(max(0, min(86400, int(props.get("active_seconds", 0)))))
                    exit_scroll_depths.append(max(0, min(100, int(props.get("max_scroll", 0)))))
                except (TypeError, ValueError):
                    pass
        avg_active_seconds = int(sum(active_durations) / len(active_durations)) if active_durations else 0
        avg_scroll_depth = round(sum(exit_scroll_depths) / len(exit_scroll_depths), 1) if exit_scroll_depths else 0

        bucket_sql = (
            "DATE_TRUNC('hour', created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Paris')"
            if window["bucket"] == "hour" else
            "DATE(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Paris')"
        )
        trend_rows = conn.run(f"""SELECT {bucket_sql} AS bucket,COUNT(*),
                COUNT(DISTINCT visitor_id),COUNT(DISTINCT session_id)
            FROM analytics_events
            WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
            GROUP BY 1 ORDER BY 1""", **params)
        measured_trend = {}
        for row in trend_rows:
            bucket = row[0]
            label = bucket.isoformat(timespec="minutes") if isinstance(bucket, datetime) else bucket.isoformat()
            measured_trend[label] = {"date": label, "page_views": int(row[1]),
                                     "visitors": int(row[2]), "sessions": int(row[3])}
        trend = []
        if window["bucket"] == "hour":
            cursor = window["since_paris"].replace(tzinfo=None)
            final_bucket = window["until_paris"].replace(minute=0, second=0, microsecond=0, tzinfo=None)
            while cursor <= final_bucket:
                key = cursor.isoformat(timespec="minutes")
                trend.append(measured_trend.get(key, {"date": key, "page_views": 0, "visitors": 0, "sessions": 0}))
                cursor += timedelta(hours=1)
        else:
            cursor = window["since_paris"].date()
            final_day = window["until_paris"].date()
            while cursor <= final_day:
                key = cursor.isoformat()
                trend.append(measured_trend.get(key, {"date": key, "page_views": 0, "visitors": 0, "sessions": 0}))
                cursor += timedelta(days=1)

        def grouped(column, empty_label="Non renseigné", limit=8):
            allowed = {"page_path", "source", "medium", "campaign", "device_type", "browser"}
            if column not in allowed:
                return []
            rows = conn.run(f"""SELECT COALESCE(NULLIF({column},''),:empty_label),COUNT(*),
                       COUNT(DISTINCT visitor_id),COUNT(DISTINCT session_id)
                FROM analytics_events
                WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
                GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT {int(limit)}""",
                **params, empty_label=empty_label)
            return [{"label": str(r[0]), "value": int(r[1]), "page_views": int(r[1]),
                     "visitors": int(r[2]), "sessions": int(r[3])} for r in rows]

        def session_edges(direction="entry"):
            order = "ASC" if direction == "entry" else "DESC"
            rows = conn.run(f"""WITH ranked AS (
                  SELECT session_id,visitor_id,page_path,
                         ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY created_at {order},id {order}) AS rank
                  FROM analytics_events
                  WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
                )
                SELECT page_path,COUNT(*),COUNT(DISTINCT visitor_id)
                FROM ranked WHERE rank=1 GROUP BY page_path ORDER BY COUNT(*) DESC LIMIT 8""", **params)
            return [{"label": str(r[0] or "/"), "value": int(r[1]),
                     "sessions": int(r[1]), "visitors": int(r[2])} for r in rows]

        path_rows = conn.run("""WITH ordered AS (
              SELECT session_id,page_path,
                     LEAD(page_path) OVER (PARTITION BY session_id ORDER BY created_at,id) AS next_page
              FROM analytics_events
              WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
            )
            SELECT page_path,next_page,COUNT(*) FROM ordered
            WHERE next_page IS NOT NULL AND page_path<>next_page
            GROUP BY page_path,next_page ORDER BY COUNT(*) DESC LIMIT 10""", **params)
        paths = [{"label": f"{r[0]} → {r[1]}", "value": int(r[2])} for r in path_rows]

        visitor_rows = conn.run("""WITH activity AS (
              SELECT visitor_id,MAX(NULLIF(member_code,'')) AS member_code,
                     COUNT(*) AS page_views,COUNT(DISTINCT session_id) AS sessions,
                     MIN(created_at) AS first_seen,MAX(created_at) AS last_seen,
                     (ARRAY_AGG(source ORDER BY created_at,id))[1] AS source,
                     (ARRAY_AGG(page_path ORDER BY created_at,id))[1] AS landing_page,
                     (ARRAY_AGG(page_path ORDER BY created_at DESC,id DESC))[1] AS last_page,
                     (ARRAY_AGG(device_type ORDER BY created_at DESC,id DESC))[1] AS device_type
              FROM analytics_events
              WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
              GROUP BY visitor_id
            )
            SELECT activity.visitor_id,COALESCE(activity.member_code,''),COALESCE(m.nom,''),
                   activity.page_views,activity.sessions,activity.first_seen,activity.last_seen,
                   COALESCE(activity.source,'direct'),activity.landing_page,activity.last_page,
                   COALESCE(activity.device_type,'Inconnu'),
                   CASE WHEN historic.visitor_id IS NULL THEN FALSE ELSE TRUE END,
                   CASE WHEN activity.member_code IS NULL THEN 'Visiteur anonyme'
                        WHEN COALESCE(m.access_level,'member') IN ('explorer','demo') THEN 'Explorer'
                        WHEN m.actif=TRUE AND (m.date_fin IS NULL OR m.date_fin>NOW()) THEN 'Membre actif'
                        ELSE 'Membre expiré' END AS segment
            FROM activity
            LEFT JOIN members m ON m.code=activity.member_code
            LEFT JOIN LATERAL (
              SELECT prior.visitor_id FROM analytics_events prior
              WHERE prior.visitor_id=activity.visitor_id AND prior.event_name='page_view'
                AND prior.created_at<:since LIMIT 1
            ) historic ON TRUE
            ORDER BY activity.last_seen DESC LIMIT 51""", **params)
        visitors_has_more = len(visitor_rows) > 50
        visitor_activity = _analytics_visitor_payload(visitor_rows[:50])

        funnel_values = [prospect_visitors, explorer_activations, checkout_starts, confirmed_payments]
        funnel_labels = [
            "Acquisition · prospects uniques", "Activation · Explorer confirmés",
            "Intention · sessions Stripe créées", "Vente · nouveaux paiements confirmés",
        ]
        funnel = []
        for index, (label, value) in enumerate(zip(funnel_labels, funnel_values)):
            previous_value = funnel_values[index - 1] if index else prospect_visitors
            funnel.append({
                "label": label, "value": value,
                "step_rate": round(value / previous_value * 100, 1) if previous_value else 0,
                "global_rate": round(value / prospect_visitors * 100, 1) if prospect_visitors else 0,
            })

        changes = {
            "page_views": _analytics_change(page_views, previous_page_views),
            "visitors": _analytics_change(visitors, previous_visitors),
            "prospect_visitors": _analytics_change(prospect_visitors, previous_prospect_visitors),
            "sessions": _analytics_change(sessions_count, previous_sessions),
            "explorer_activations": _analytics_change(explorer_activations, previous_explorer),
            "checkout_starts": _analytics_change(checkout_starts, previous_checkouts),
            "confirmed_payments": _analytics_change(confirmed_payments, previous_payments),
            "confirmed_renewals": _analytics_change(confirmed_renewals, previous_renewals),
            "revenue_cents": _analytics_change(revenue_cents, previous_revenue),
        }
        return jsonify({
            "ok": True, "period": window["period"], "posthog_connected": bool(POSTHOG_PROJECT_KEY),
            "window": {"label": window["label"], "timezone": "Europe/Paris",
                       "from": window["since_paris"].isoformat(timespec="minutes"),
                       "to": window["until_paris"].isoformat(timespec="minutes"),
                       "bucket": window["bucket"]},
            "kpis": {"page_views": page_views, "visitors": visitors, "sessions": sessions_count,
                     "registrations": registrations, "explorer_activations": explorer_activations,
                     "checkout_starts": checkout_starts, "confirmed_payments": confirmed_payments,
                     "confirmed_renewals": confirmed_renewals, "revenue_cents": revenue_cents,
                     "conversion_rate": round((confirmed_payments / prospect_visitors * 100) if prospect_visitors else 0, 2),
                     "cta_clicks": cta_clicks, "pages_per_session": pages_per_session,
                     "single_page_rate": single_page_rate,
                     "avg_session_seconds": avg_session_seconds,
                     "avg_active_seconds": avg_active_seconds,
                     "avg_scroll_depth": avg_scroll_depth,
                     "engaged_visitors": engaged_visitors,
                     "media_plays": media_plays,
                     "returning_visitors": returning_visitors,
                     "new_visitors": max(0, visitors - returning_visitors),
                     "prospect_visitors": prospect_visitors,
                     "active_member_visitors": active_member_visitors,
                     "explorer_visitors": explorer_visitors,
                     "expired_member_visitors": expired_member_visitors,
                     "identified_visitors": identified_visitors,
                     "anonymous_visitors": max(0, visitors - identified_visitors)},
            "changes": changes, "trend": trend,
            "pages": grouped("page_path", "/"), "sources": grouped("source", "direct"),
            "mediums": grouped("medium", "Non renseigné"),
            "campaigns": grouped("campaign", "Sans campagne"),
            "devices": grouped("device_type", "Inconnu"),
            "browsers": grouped("browser", "Inconnu"),
            "entry_pages": session_edges("entry"), "exit_pages": session_edges("exit"),
            "paths": paths, "visitors": visitor_activity,
            "visitors_has_more": visitors_has_more, "funnel": funnel,
        })
    except Exception as exc:
        app.logger.exception("Chargement statistiques détaillées")
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


@app.route("/admin/api/analytics/visitor/<visitor_id>", methods=["GET"])
def admin_api_analytics_visitor(visitor_id):
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", visitor_id):
        return jsonify({"ok": False, "error": "Visiteur invalide"}), 400
    conn = None
    try:
        conn = get_conn()
        rows = conn.run("""SELECT event_name,page_path,source,medium,campaign,device_type,
                   browser,properties,created_at,member_code
            FROM analytics_events WHERE visitor_id=:visitor_id
            ORDER BY created_at DESC,id DESC LIMIT 150""", visitor_id=visitor_id)
        events = []
        for row in rows:
            try:
                properties = json.loads(row[7] or "{}") if isinstance(row[7], str) else (row[7] or {})
            except Exception:
                properties = {}
            events.append({
                "event": str(row[0]), "page": str(row[1] or "/"),
                "source": str(row[2] or "direct"), "medium": str(row[3] or ""),
                "campaign": str(row[4] or ""), "device": str(row[5] or "Inconnu"),
                "browser": str(row[6] or "Inconnu"), "properties": properties,
                "at": _analytics_local_iso(row[8]), "member_code": str(row[9] or ""),
            })
        return jsonify({"ok": True, "visitor_id": visitor_id, "events": events})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


@app.route("/admin/api/analytics/visitors", methods=["GET"])
def admin_api_analytics_visitors():
    if request.args.get("key", "") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    window = _analytics_period_window(request.args.get("period", "7d"))
    try:
        page = max(1, min(100, int(request.args.get("page", "1") or 1)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Pagination invalide"}), 400
    query_text = str(request.args.get("q", "") or "").strip().lower()[:80]
    params = {
        "since": window["since"], "until": window["until"],
        "query_text": query_text, "query": f"%{query_text}%",
        "limit": 51, "offset": (page - 1) * 50,
    }
    conn = None
    try:
        conn = get_conn()
        rows = conn.run("""WITH activity AS (
              SELECT visitor_id,MAX(NULLIF(member_code,'')) AS member_code,
                     COUNT(*) AS page_views,COUNT(DISTINCT session_id) AS sessions,
                     MIN(created_at) AS first_seen,MAX(created_at) AS last_seen,
                     (ARRAY_AGG(source ORDER BY created_at,id))[1] AS source,
                     (ARRAY_AGG(page_path ORDER BY created_at,id))[1] AS landing_page,
                     (ARRAY_AGG(page_path ORDER BY created_at DESC,id DESC))[1] AS last_page,
                     (ARRAY_AGG(device_type ORDER BY created_at DESC,id DESC))[1] AS device_type
              FROM analytics_events
              WHERE created_at>=:since AND created_at<:until AND event_name='page_view'
              GROUP BY visitor_id
            ), enriched AS (
              SELECT activity.visitor_id,COALESCE(activity.member_code,'') AS member_code,
                     COALESCE(m.nom,'') AS name,activity.page_views,activity.sessions,
                     activity.first_seen,activity.last_seen,COALESCE(activity.source,'direct') AS source,
                     activity.landing_page,activity.last_page,
                     COALESCE(activity.device_type,'Inconnu') AS device_type,
                     CASE WHEN historic.visitor_id IS NULL THEN FALSE ELSE TRUE END AS is_returning,
                     CASE WHEN activity.member_code IS NULL THEN 'Visiteur anonyme'
                          WHEN COALESCE(m.access_level,'member') IN ('explorer','demo') THEN 'Explorer'
                          WHEN m.actif=TRUE AND (m.date_fin IS NULL OR m.date_fin>NOW()) THEN 'Membre actif'
                          ELSE 'Membre expiré' END AS segment
              FROM activity
              LEFT JOIN members m ON m.code=activity.member_code
              LEFT JOIN LATERAL (
                SELECT prior.visitor_id FROM analytics_events prior
                WHERE prior.visitor_id=activity.visitor_id AND prior.event_name='page_view'
                  AND prior.created_at<:since LIMIT 1
              ) historic ON TRUE
            )
            SELECT visitor_id,member_code,name,page_views,sessions,first_seen,last_seen,
                   source,landing_page,last_page,device_type,is_returning,segment
            FROM enriched
            WHERE :query_text='' OR LOWER(visitor_id) LIKE :query
               OR LOWER(member_code) LIKE :query OR LOWER(name) LIKE :query
            ORDER BY last_seen DESC LIMIT :limit OFFSET :offset""", **params)
        has_more = len(rows) > 50
        return jsonify({"ok": True, "page": page, "has_more": has_more,
                        "visitors": _analytics_visitor_payload(rows[:50])})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

# ── STATS ──
@app.route("/admin/api/stats", methods=["GET"])
def admin_api_stats():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        enforce_member_access_state()
        conn = get_conn()
        from datetime import datetime
        paid_filter = "COALESCE(access_level,'member') NOT IN ('explorer','demo')"
        total = conn.run(f"SELECT COUNT(*) FROM members WHERE {paid_filter}")[0][0]
        actifs = conn.run("""SELECT COUNT(*) FROM members
            WHERE COALESCE(access_level,'member') NOT IN ('explorer','demo')
              AND actif=TRUE AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        copy_on = conn.run("""SELECT COUNT(*) FROM members
            WHERE COALESCE(access_level,'member') NOT IN ('explorer','demo')
              AND copy_actif=TRUE AND actif=TRUE
              AND (date_fin IS NULL OR date_fin > NOW())""")[0][0]
        expires = conn.run(f"SELECT COUNT(*) FROM members WHERE {paid_filter} AND date_fin <= NOW()")[0][0]
        nouveaux = conn.run(f"SELECT COUNT(*) FROM members WHERE {paid_filter} AND created_at > NOW() - INTERVAL '7 days'")[0][0]
        explorers = conn.run("""SELECT COUNT(*) FROM members
            WHERE COALESCE(access_level,'member') IN ('explorer','demo') AND code <> 'BCT-DEMO2026'""")[0][0]
        conn.close()
        return jsonify({"ok":True,"total":total,"actifs":actifs,"copy_on":copy_on,
                        "expires":expires,"nouveaux_7j":nouveaux,"explorers":explorers})
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
        APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
        if not APPS_SCRIPT_URL:
            raise RuntimeError("Connecteur prospect non configuré")
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
            "code": r[0], "nom": r[1], "email": r[2] or "",
            "telephone": r[3] or "", "telegram": r[4] or "",
            "capital": r[5] or "", "actif": r[6], "copy_actif": r[7],
            "date_souscription": fmt(r[8]), "date_fin": fmt(r[9]),
            "parrain_code": r[10] or "—", "filleuls_count": r[11] or 0,
            "gains_parrainage": r[12] or 0,
            "paiement_type": r[13] or "—", "paiement_iban": _decrypt_value(r[14]) or "—",
            "paiement_bic": _decrypt_value(r[15]) or "—", "paiement_titulaire": _decrypt_value(r[16]) or "—",
            "paiement_crypto_reseau": r[17] or "—",
            "paiement_crypto_adresse": _decrypt_value(r[18]) or "—",
            "params": r[19], "historique": r[20],
            "created_at": fmt(r[21]), "last_login": fmt(r[22]),
        }
        # Extraire infos MT4 depuis params
        import json as _json
        try:
            p = _reveal_params(_json.loads(r[19]) if isinstance(r[19], str) else (r[19] or {}))
            member["mt_login"]    = p.get("mt_login","") or ""
            member["mt_server"]   = p.get("serveur","") or ""
            member["mt_password_masked"] = bool(p.get("mt_password", "").strip())
            member["mt_password"] = "••••••" if member["mt_password_masked"] else "—"
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
    initial_credits = ANALYSIS_INITIAL_CREDITS
    if str(member_code).startswith("BAI-"):
        initial_credits = 0
    elif str(member_code).startswith("BCT-"):
        access_rows = conn.run(
            "SELECT COALESCE(access_level,'member') FROM members WHERE code=:code",
            code=member_code)
        if access_rows and access_rows[0][0] in {"explorer", "demo"}:
            initial_credits = 0
    conn.run("""INSERT INTO analysis_wallets
        (member_code, balance, lifetime_granted, lifetime_spent)
        VALUES (:code, :initial, :initial, 0)
        ON CONFLICT (member_code) DO NOTHING""",
        code=member_code, initial=initial_credits)
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
                    "y": {"type": "number", "minimum": 0, "maximum": 100},
                    "price": {"type": "number"}
                }, "required": ["x", "y", "price"]
            }}
        },
        "required": ["type", "role", "label", "price", "x_start", "x_end",
                     "y_start", "y_end", "label_x", "label_y", "points"]
    }
    annotation["properties"]["price_low"] = {"type": "number"}
    annotation["properties"]["price_high"] = {"type": "number"}
    annotation["required"].extend(["price_low", "price_high"])
    price_anchor = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "price": {"type": "number"},
            "y": {"type": "number", "minimum": 0, "maximum": 100}
        },
        "required": ["price", "y"]
    }
    price_axis = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "top_price": {"type": "number"}, "bottom_price": {"type": "number"},
            "top_y": {"type": "number", "minimum": 0, "maximum": 100},
            "bottom_y": {"type": "number", "minimum": 0, "maximum": 100},
            "tick_interval": {"type": "number", "exclusiveMinimum": 0},
            "decimals": {"type": "integer", "minimum": 0, "maximum": 5},
            "anchors": {"type": "array", "items": price_anchor, "minItems": 3, "maxItems": 8}
        },
        "required": ["top_price", "bottom_price", "top_y", "bottom_y", "tick_interval", "decimals", "anchors"]
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
            "statut": {"type": "string", "enum": ["VALIDE", "NO TRADE"]},
            "entree": {"type": "string"}, "declencheur": {"type": "string"},
            "stop_loss": {"type": "string"},
            "objectif_1": {"type": "string"}, "objectif_2": {"type": "string"},
            "objectif_3": {"type": "string"},
            "rr_tp1": {"type": "string"}, "rr_tp2": {"type": "string"},
            "rr_tp3": {"type": "string"},
            "invalidation": {"type": "string"}, "ratio": {"type": "string"}
        },
        "required": ["direction", "qualite", "statut", "entree", "declencheur", "stop_loss",
                     "objectif_1", "objectif_2", "objectif_3", "rr_tp1", "rr_tp2", "rr_tp3",
                     "invalidation", "ratio"]
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
            "price_axis": price_axis,
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
        "required": ["biais_global", "confiance", "structure", "resume", "prix_visible", "price_axis",
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
- Calcule séparément le R/R de TP1, TP2 et TP3 à partir de l'entrée conditionnelle et du stop.
  Un scénario est VALIDE seulement si les niveaux visibles permettent un risque/rendement cohérent :
  vise au minimum TP1 1:1, TP2 1:2 et TP3 1:3. Ne déplace jamais artificiellement un objectif
  pour atteindre ces ratios. Si ce n'est pas techniquement possible ou si les prix sont illisibles,
  statut=NO TRADE et écris « non calculable » dans les R/R concernés.
- Retourne aussi les annotations à superposer sur la capture originale. Les coordonnées sont des
  pourcentages de l'image complète : x de gauche à droite et y de haut en bas, entre 0 et 100.
- Avant toute annotation, calibre obligatoirement price_axis avec 3 à 8 graduations chiffrées
  réellement visibles sur l'axe de droite. Pour chacune, renseigne dans anchors son prix exact et
  le centre vertical y en pourcentage de l'image. Inclus la graduation la plus haute et la plus
  basse lisibles, l'intervalle exact entre deux graduations et le nombre de décimales. Répartis les
  anchors sur toute la hauteur de l'axe : ne sélectionne jamais uniquement des graduations voisines.
  top_price/top_y et bottom_price/bottom_y correspondent obligatoirement aux extrêmes visibles.
  Vérifie l'alignement sur une graduation intermédiaire et le prix courant.
- Chaque annotation contient price_low et price_high sous forme numérique. Pour une zone, ce sont
  exactement ses bornes basse et haute. Pour une ligne ou un point, elles sont identiques. Chaque
  point d'une trajectoire contient aussi son prix numérique exact. Les coordonnées Y proposées ne
  sont qu'indicatives : le serveur les recalculera depuis l'axe des prix.
- Le texte label décrit uniquement le rôle (« SUPPLY », « SUPPORT », « DEMAND », « PIVOT ») et ne
  contient jamais de prix. Le champ price contient uniquement la valeur ou plage canonique, afin
  qu'aucun niveau ne soit répété deux fois dans le même libellé.
- Contrôle final obligatoire : chaque borne annoncée doit tomber exactement en face de la même
  valeur sur l'axe droit. Si une valeur ne concorde pas, retire l'annotation au lieu de l'approximer.
  Pour une ligne horizontale, utilise y_start = y_end. Pour une zone, encadre ses deux limites.
  Pour une trajectoire, type=path et renseigne 4 à 8 points formant une évolution naturelle terminée
  par la cible; les autres types ont points=[]. La trajectoire démarre sur la zone d'entrée, effectue
  au maximum deux respirations naturelles puis finit sur la cible majeure. Pas de segment vertical,
  d'angle extrême ni de zigzag artificiel. Pour un path, label vaut uniquement « SCÉNARIO HAUSSIER »
  ou « SCÉNARIO BAISSIER ». label_x/label_y fixe l'emplacement exact du libellé.
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


def _format_chart_price(value, decimals):
    decimals = max(0, min(5, int(decimals)))
    formatted = f"{float(value):,.{decimals}f}"
    return formatted.replace(",", "\u202f").replace(".", ",")


def _normalize_analysis_geometry(result):
    """Aligne déterministement les annotations sur l'échelle de prix visible."""
    axis = result.get("price_axis") or {}
    try:
        declared_top_price = float(axis["top_price"])
        declared_bottom_price = float(axis["bottom_price"])
        declared_top_y = float(axis["top_y"])
        declared_bottom_y = float(axis["bottom_y"])
        tick = float(axis.get("tick_interval", 0))
        decimals = int(axis.get("decimals", 0))
        anchors = [(float(item["price"]), float(item["y"])) for item in axis.get("anchors", [])]
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("L’axe des prix n’a pas pu être calibré avec certitude.")

    if len(anchors) < 3 or tick <= 0:
        raise RuntimeError("L’axe des prix n’a pas pu être calibré avec certitude.")

    mean_price = sum(price for price, _ in anchors) / len(anchors)
    mean_y = sum(y for _, y in anchors) / len(anchors)
    denominator = sum((price - mean_price) ** 2 for price, _ in anchors)
    if denominator <= 0:
        raise RuntimeError("Les graduations de prix ne sont pas suffisamment distinctes.")

    slope = sum((price - mean_price) * (y - mean_y) for price, y in anchors) / denominator
    intercept = mean_y - slope * mean_price
    residual = max(abs((slope * price + intercept) - y) for price, y in anchors)
    if slope >= 0 or residual > 1.25:
        raise RuntimeError("Les graduations visibles ne permettent pas une annotation assez précise.")

    anchor_top = max(price for price, _ in anchors)
    anchor_bottom = min(price for price, _ in anchors)
    inferred_top = (0.0 - intercept) / slope
    inferred_bottom = (100.0 - intercept) / slope
    declared_is_sane = declared_top_price > declared_bottom_price
    top_price = max(anchor_top, inferred_top, declared_top_price if declared_is_sane else anchor_top)
    bottom_price = min(anchor_bottom, inferred_bottom, declared_bottom_price if declared_is_sane else anchor_bottom)
    top_y = slope * top_price + intercept
    bottom_y = slope * bottom_price + intercept
    axis.update({"top_price": top_price, "bottom_price": bottom_price,
                 "top_y": round(top_y, 3), "bottom_y": round(bottom_y, 3)})

    def price_to_y(price):
        return round(max(0.0, min(100.0, slope * float(price) + intercept)), 3)

    tolerance = tick * 1.25
    cleaned = []
    for annotation in result.get("annotations_graphique") or []:
        try:
            low = float(annotation.get("price_low"))
            high = float(annotation.get("price_high"))
        except (TypeError, ValueError):
            continue
        if low > high:
            low, high = high, low
        if low < bottom_price - tolerance or high > top_price + tolerance:
            continue
        annotation["price_low"], annotation["price_high"] = low, high
        semantic_label = re.sub(r"\s*[·:|–—-]?\s*\d[\d\s.,/–—-]*$", "", str(annotation.get("label", ""))).strip()
        annotation["label"] = semantic_label or str(annotation.get("role", "NIVEAU")).replace("_", " ").upper()
        annotation["price"] = (_format_chart_price(low, decimals) if abs(high - low) < 10 ** (-max(decimals, 1))
                               else f"{_format_chart_price(low, decimals)}–{_format_chart_price(high, decimals)}")
        if annotation.get("type") == "zone":
            annotation["y_start"] = price_to_y(high)
            annotation["y_end"] = price_to_y(low)
            annotation["label_y"] = round((annotation["y_start"] + annotation["y_end"]) / 2, 3)
        elif annotation.get("type") == "path":
            points = []
            for point in annotation.get("points") or []:
                try:
                    point_price = float(point.get("price"))
                    if bottom_price - tolerance <= point_price <= top_price + tolerance:
                        points.append({"x": float(point["x"]), "y": price_to_y(point_price), "price": point_price})
                except (TypeError, ValueError, KeyError):
                    continue
            if len(points) < 2:
                continue
            annotation["points"] = points
            annotation["y_start"], annotation["y_end"] = points[0]["y"], points[-1]["y"]
        else:
            level_y = price_to_y((low + high) / 2)
            annotation["y_start"] = annotation["y_end"] = level_y
            annotation["label_y"] = level_y
        cleaned.append(annotation)
    if not cleaned:
        raise RuntimeError("Aucune annotation ne concorde avec l’axe des prix visible.")
    result["annotations_graphique"] = cleaned
    result["price_axis"]["calibrated"] = True
    return result


def _openai_analysis(image_data_url, market, timeframe, session_name, trading_style, economic_events):
    if not OPENAI_API_KEY:
        raise RuntimeError("Le moteur d’analyse n’est pas encore connecté.")
    payload = {
        "model": OPENAI_ANALYSIS_MODEL,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 2600,
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
        result = _normalize_analysis_geometry(json.loads(output_text))
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
    return _admin_request_allowed()


def _analysis_identity():
    if _admin_session_valid():
        return ANALYSIS_ADMIN_CODE, True
    analysis_code = session.get("analysis_account_code", "")
    if analysis_code:
        conn = get_conn()
        try:
            rows = conn.run("SELECT actif FROM analysis_accounts WHERE code=:code", code=analysis_code)
        finally:
            conn.close()
        if rows and rows[0][0]:
            return analysis_code, False
    code = session.get("member_code", "")
    if not code or code == "BCT-DEMO2026":
        return None, False
    member = get_member(code)
    if not member or not member.get("actif"):
        return None, False
    date_fin = member.get("date_fin")
    if date_fin and date_fin <= datetime.now():
        return None, False
    return code, False


@app.route("/admin/api/analyse-diagnostics")
def admin_analyse_diagnostics():
    """Diagnostic protégé des dernières exécutions, sans capture ni donnée membre."""
    if not _analysis_admin_allowed():
        return jsonify({"ok": False}), 403
    conn = get_conn()
    try:
        rows = conn.run("""SELECT status, model, error, input_tokens, output_tokens,
                            created_at, completed_at
                     FROM analysis_jobs WHERE member_code=:code
                     ORDER BY created_at DESC LIMIT 20""", code=ANALYSIS_ADMIN_CODE)
        return jsonify({"ok": True, "engine_ready": bool(OPENAI_API_KEY), "jobs": [{
            "status": row[0], "model": row[1], "error": row[2] or "",
            "input_tokens": int(row[3] or 0), "output_tokens": int(row[4] or 0),
            "created_at": row[5].isoformat() if row[5] else None,
            "completed_at": row[6].isoformat() if row[6] else None,
        } for row in rows]})
    finally:
        conn.close()


@app.route("/admin/api/security-status")
def admin_security_status():
    if not _admin_request_allowed():
        return jsonify({"ok": False}), 403
    conn = get_conn()
    try:
        rows = conn.run("""SELECT code, params, paiement_iban, paiement_bic,
            paiement_titulaire, paiement_crypto_adresse FROM members""")
        plaintext_passwords = 0
        plaintext_payments = 0
        for _, raw_params, *payment_fields in rows:
            try:
                params = json.loads(raw_params) if isinstance(raw_params, str) else (raw_params or {})
            except Exception:
                params = {}
            password = str(params.get("mt_password") or "")
            plaintext_passwords += int(bool(password) and not password.startswith("enc:v1:"))
            plaintext_payments += sum(
                1 for value in payment_fields
                if value and not str(value).startswith("enc:v1:")
            )
        wallets = conn.run("""SELECT balance, COUNT(*) FROM analysis_wallets
            WHERE member_code != :admin GROUP BY balance ORDER BY balance""",
            admin=ANALYSIS_ADMIN_CODE)
        return jsonify({
            "ok": plaintext_passwords == 0 and plaintext_payments == 0,
            "plaintext_passwords": plaintext_passwords,
            "plaintext_payments": plaintext_payments,
            "member_wallets": [{"balance": int(row[0]), "count": int(row[1])} for row in wallets],
            "initial_credits": ANALYSIS_INITIAL_CREDITS,
            "admin_key_exposed_to_templates": False,
        })
    finally:
        conn.close()


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
    code, is_admin = _analysis_identity()
    if not code:
        return redirect(url_for("login"))
    analysis_only = str(code).startswith("BAI-")
    if is_admin:
        _refund_stale_admin_analyses()
        member = {"code": code, "nom": "Administration Bectanse"}
    elif analysis_only:
        conn = get_conn()
        try:
            rows = conn.run("SELECT code, prenom, email FROM analysis_accounts WHERE code=:code", code=code)
            member = {"code": rows[0][0], "nom": rows[0][1] or "Compte Analyse", "email": rows[0][2]}
        finally:
            conn.close()
    else:
        member = get_member(code)
    conn = get_conn()
    try:
        wallet = ({"balance": None, "unlimited": True,
                   "lifetime_granted": 0, "lifetime_spent": 0}
                  if is_admin else {**_analysis_wallet(conn, code), "unlimited": False})
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
                           admin_beta=is_admin, admin_key="", analysis_only=analysis_only,
                           account_locked=bool(not is_admin and wallet["balance"] <= 0))


@app.route("/api/analyse-ia/run", methods=["POST"])
def analyse_ia_run():
    code, is_admin = _analysis_identity()
    if not code:
        return jsonify({"ok": False, "error": "Accès membre actif requis."}), 403
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
        processing = conn.run("""SELECT COUNT(*) FROM analysis_jobs
            WHERE member_code=:code AND status='processing'
              AND created_at > NOW() - INTERVAL '4 minutes'""", code=code)[0][0]
        recent = conn.run("""SELECT COUNT(*) FROM analysis_jobs
            WHERE member_code=:code AND created_at > NOW() - INTERVAL '1 hour'""", code=code)[0][0]
        if processing:
            conn.run("ROLLBACK")
            return jsonify({"ok": False, "error": "Une analyse est déjà en cours."}), 429
        if recent >= (30 if is_admin else 6):
            conn.run("ROLLBACK")
            return jsonify({"ok": False, "error": "Limite horaire atteinte. Réessaie plus tard."}), 429
        new_balance = None
        if not is_admin:
            wallet = _analysis_wallet(conn, code, lock=True)
            if wallet["balance"] < 1:
                conn.run("ROLLBACK")
                return jsonify({"ok": False, "error": "Tu n’as plus de crédit disponible.", "balance": 0}), 402
            new_balance = wallet["balance"] - 1
            conn.run("""UPDATE analysis_wallets SET balance=:balance,
                lifetime_spent=lifetime_spent+1, updated_at=NOW() WHERE member_code=:code""",
                balance=new_balance, code=code)
            conn.run("""INSERT INTO analysis_credit_ledger
                (member_code, delta, balance_after, reason, reference)
                VALUES (:code, -1, :balance, 'analysis', :reference)""",
                code=code, balance=new_balance, reference=f"analysis:{job_id}")
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


@app.route("/abonnement/checkout/<plan_id>")
def academy_subscription_checkout(plan_id):
    """Crée un Checkout Stripe traçable pour récupérer les abandons identifiés."""
    plan_entry = ACADEMY_PLAN_BY_ID.get(str(plan_id))
    if not plan_entry:
        return redirect("/vip#offres")
    if "member_code" not in session:
        session["pending_academy_plan"] = str(plan_id)
        session["login_notice"] = (
            "Crée ou connecte ton compte Explorer pour sécuriser le paiement "
            "et rattacher automatiquement l’abonnement."
        )
        return redirect(url_for("login", checkout="identification", plan=plan_id))
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return redirect("/vip?checkout=indisponible#offres")

    code = str(session["member_code"])
    price_id, plan = plan_entry
    conn = get_conn()
    try:
        rows = conn.run("""SELECT email,nom,COALESCE(stripe_subscription_id,''),
            COALESCE(billing_status,''),COALESCE(billing_cancel_at_period_end,FALSE)
            FROM members WHERE code=:code AND email_verified_at IS NOT NULL""", code=code)
    finally:
        conn.close()
    if not rows or "@" not in str(rows[0][0] or ""):
        session["login_notice"] = "Confirme d’abord ton adresse e-mail pour continuer vers Stripe."
        return redirect(url_for("login"))
    email, member_name, subscription_id, billing_status, cancel_at_period_end = rows[0]
    if subscription_id and billing_status in {"active", "trialing"} and not cancel_at_period_end:
        return redirect(url_for("abonnement"))

    root = request.url_root.rstrip("/")
    form = {
        "mode": "subscription",
        "success_url": STRIPE_PAYMENT_SUCCESS_URL,
        "cancel_url": root + "/vip?checkout=cancelled#offres",
        "client_reference_id": code,
        "customer_email": str(email).strip().lower(),
        "metadata[member_code]": code,
        "metadata[academy_plan_id]": str(plan_id),
        "subscription_data[metadata][member_code]": code,
        "subscription_data[metadata][academy_plan_id]": str(plan_id),
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "billing_address_collection": "auto",
    }
    try:
        stripe_response = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(STRIPE_SECRET_KEY, ""), data=form, timeout=25,
        )
        stripe_data = stripe_response.json()
        if not stripe_response.ok:
            raise RuntimeError(stripe_data.get("error", {}).get("message", "Checkout indisponible"))
        checkout_url = str(stripe_data.get("url") or "")
        checkout_id = str(stripe_data.get("id") or "")
        if not checkout_url.startswith("https://checkout.stripe.com/") or not checkout_id:
            raise RuntimeError("Réponse Stripe incomplète")
        conn = get_conn()
        try:
            record_checkout_session(
                conn, code, checkout_id, destination=checkout_url,
                source=str(request.args.get("utm_source", "direct"))[:100],
                medium=str(request.args.get("utm_medium", ""))[:100],
                campaign=str(request.args.get("utm_campaign", ""))[:120],
            )
        finally:
            conn.close()
        return redirect(checkout_url, code=303)
    except Exception as error:
        app.logger.error("Création abonnement Stripe %s %s: %s", code, plan_id, error)
        return redirect("/vip?checkout=error#offres")


@app.route("/api/analyse-ia/checkout", methods=["POST"])
def analyse_ia_checkout():
    code, is_admin = _analysis_identity()
    if not code or is_admin:
        return jsonify({"ok": False, "error": "Crée ou connecte ton compte avant le paiement."}), 403
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Les recharges seront ouvertes après la validation de la bêta."}), 503
    checkout_request = request.get_json(silent=True) or {}
    pack_id = str(checkout_request.get("pack", ""))
    if checkout_request.get("accept_terms") is not True:
        return jsonify({"ok": False, "error": "Accepte les CGV et la fourniture immédiate avant de continuer."}), 400
    pack = ANALYSIS_PACKS.get(pack_id)
    if not pack:
        return jsonify({"ok": False, "error": "Pack inconnu."}), 400
    root = request.url_root.rstrip("/")
    form = {
        "mode": "payment",
        "success_url": STRIPE_PAYMENT_SUCCESS_URL,
        "cancel_url": root + "/analyse-ia?checkout=cancelled",
        "client_reference_id": code,
        "metadata[member_code]": code,
        "metadata[credits]": str(pack["credits"]),
        "payment_intent_data[metadata][member_code]": code,
        "payment_intent_data[metadata][credits]": str(pack["credits"]),
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
        conn = get_conn()
        try:
            remote = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()[:80]
            conn.run("""INSERT INTO analysis_legal_acceptances
                (member_code, legal_version, pack_id, stripe_session_id, ip_address, user_agent)
                VALUES (:code, '2026-08-14', :pack, :session_id, :ip, :agent)
                ON CONFLICT (stripe_session_id) DO NOTHING""",
                code=code, pack=pack_id, session_id=stripe_data["id"], ip=remote,
                agent=request.headers.get("User-Agent", "")[:500])
        finally:
            conn.close()
        return jsonify({"ok": True, "url": stripe_data["url"]})
    except Exception as error:
        app.logger.error("Création paiement crédits: %s", error)
        return jsonify({"ok": False, "error": "Impossible d’ouvrir le paiement pour le moment."}), 502


@app.route("/api/analyse-ia/wallet")
def analyse_ia_wallet():
    code, is_admin = _analysis_identity()
    if not code or is_admin:
        return jsonify({"ok": False, "error": "Compte Analyse requis."}), 403
    conn = get_conn()
    try:
        wallet = _analysis_wallet(conn, code)
        purchases = conn.run("""SELECT credits, amount_cents, created_at
            FROM analysis_purchases WHERE member_code=:code AND status='paid'
            ORDER BY created_at DESC LIMIT 5""", code=code)
        return jsonify({
            "ok": True,
            "balance": wallet["balance"],
            "purchases": [
                {"credits": int(row[0]), "amount_cents": int(row[1]),
                 "created_at": row[2].isoformat() if row[2] else None}
                for row in purchases
            ],
        })
    finally:
        conn.close()


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


def _stripe_id(value):
    if isinstance(value, dict):
        return str(value.get("id", "") or "")
    return str(value or "")


def _stripe_subscription_context(event_type, stripe_object):
    """Normalise les objets Checkout, Subscription et Invoice sans appel API annexe."""
    obj = stripe_object or {}
    customer_id = _stripe_id(obj.get("customer"))
    subscription_id = ""
    if str(obj.get("id", "")).startswith("sub_"):
        subscription_id = str(obj.get("id"))
    subscription_id = subscription_id or _stripe_id(obj.get("subscription"))
    parent = obj.get("parent") or {}
    subscription_details = parent.get("subscription_details") or {}
    subscription_id = subscription_id or _stripe_id(subscription_details.get("subscription"))

    data_rows = []
    for container in (obj.get("items") or {}, obj.get("lines") or {}):
        data_rows.extend(container.get("data") or [])
    price_id = ""
    period_end = obj.get("current_period_end")
    for row in data_rows:
        price = row.get("price") or {}
        pricing = row.get("pricing") or {}
        price_details = pricing.get("price_details") or {}
        candidate = _stripe_id(price) or _stripe_id(price_details.get("price"))
        if candidate in ACADEMY_SUBSCRIPTION_PLANS:
            price_id = candidate
            period_end = period_end or row.get("current_period_end") or (row.get("period") or {}).get("end")
            break

    payment_link_id = _stripe_id(obj.get("payment_link"))
    if not price_id and payment_link_id:
        price_id = (ACADEMY_PLAN_BY_PAYMENT_LINK.get(payment_link_id) or ("", {}))[0]
    if not price_id:
        academy_plan_id = str((obj.get("metadata") or {}).get("academy_plan_id", ""))
        price_id = (ACADEMY_PLAN_BY_ID.get(academy_plan_id) or ("", {}))[0]
    plan = ACADEMY_SUBSCRIPTION_PLANS.get(price_id)

    customer_details = obj.get("customer_details") or {}
    email = str(customer_details.get("email") or obj.get("customer_email") or "").strip().lower()
    customer_name = str(customer_details.get("name") or "").strip()
    stripe_object_id = _stripe_id(obj.get("id"))
    invoice_id = _stripe_id(obj.get("invoice"))
    if event_type.startswith("invoice.") and stripe_object_id.startswith("in_"):
        invoice_id = stripe_object_id
    payment_status = str(obj.get("payment_status") or "").lower()
    billing_reason = str(obj.get("billing_reason") or "").lower()
    amount_paid_cents = int(obj.get("amount_paid") or obj.get("amount_total") or 0)
    currency = str(obj.get("currency") or "").lower()
    status = str(obj.get("status") or "").lower()
    if event_type == "checkout.session.completed":
        status = "active" if str(obj.get("payment_status", "")).lower() in {"paid", "no_payment_required"} else status
    elif event_type == "invoice.paid":
        status = "active"
    elif event_type == "invoice.payment_failed":
        status = "past_due"
    elif event_type == "customer.subscription.deleted":
        status = "canceled"
    elif event_type == "customer.subscription.paused":
        status = "paused"
    elif event_type == "customer.subscription.resumed":
        status = "active"

    try:
        period_end_dt = datetime.fromtimestamp(int(period_end)) if period_end else None
    except (TypeError, ValueError, OSError):
        period_end_dt = None
    if not period_end_dt and plan and status in {"active", "trialing"}:
        period_end_dt = datetime.now() + timedelta(days=int(plan["days"]))

    return {
        "customer_id": customer_id,
        "subscription_id": subscription_id,
        "price_id": price_id,
        "plan": plan,
        "email": email,
        "customer_name": customer_name,
        "stripe_object_id": stripe_object_id,
        "invoice_id": invoice_id,
        "payment_status": payment_status,
        "billing_reason": billing_reason,
        "amount_paid_cents": max(0, amount_paid_cents),
        "currency": currency,
        "status": status,
        "period_end": period_end_dt,
        "cancel_at_period_end": bool(obj.get("cancel_at_period_end", False)),
    }


def _process_academy_stripe_event(event):
    """Rattache Stripe au compte BCT et applique l'accès de façon idempotente."""
    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))
    stripe_object = (event.get("data") or {}).get("object") or {}
    context = _stripe_subscription_context(event_type, stripe_object)
    # Un Checkout provenant d'un autre produit du même compte Stripe ne doit
    # jamais être rattaché à l'Académie par simple correspondance d'e-mail.
    if event_type == "checkout.session.completed" and not context["plan"]:
        return {"handled": False}
    if not context["plan"] and not context["customer_id"] and not context["subscription_id"]:
        return {"handled": False}

    conn = get_conn()
    member_code = ""
    created_member = False
    member_email = context["email"]
    member_name = context["customer_name"] or "Nouveau membre"
    try:
        conn.run("BEGIN")
        inserted = conn.run("""INSERT INTO stripe_academy_events
            (event_id,event_type,status,stripe_object_id,invoice_id,subscription_id,
             payment_status,billing_reason,amount_paid_cents,currency)
            VALUES (:event_id,:event_type,'processing',:stripe_object_id,:invoice_id,
                    :subscription_id,:payment_status,:billing_reason,:amount_paid_cents,:currency)
            ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
            event_id=event_id, event_type=event_type,
            stripe_object_id=context["stripe_object_id"], invoice_id=context["invoice_id"],
            subscription_id=context["subscription_id"], payment_status=context["payment_status"],
            billing_reason=context["billing_reason"], amount_paid_cents=context["amount_paid_cents"],
            currency=context["currency"])
        if not inserted:
            conn.run("ROLLBACK")
            return {"handled": True, "duplicate": True}

        rows = []
        if context["subscription_id"]:
            rows = conn.run("""SELECT code,email,nom,stripe_subscription_id FROM members
                WHERE stripe_subscription_id=:subscription LIMIT 1""",
                subscription=context["subscription_id"])
        if not rows and context["customer_id"]:
            rows = conn.run("""SELECT code,email,nom,stripe_subscription_id FROM members
                WHERE stripe_customer_id=:customer LIMIT 1""", customer=context["customer_id"])
        if not rows and context["email"]:
            rows = conn.run("""SELECT code,email,nom,stripe_subscription_id FROM members
                WHERE LOWER(email)=LOWER(:email) ORDER BY created_at DESC LIMIT 1""",
                email=context["email"])
        if rows:
            member_code, stored_email, stored_name, stored_subscription = rows[0]
            member_email = member_email or str(stored_email or "")
            member_name = str(stored_name or member_name)
            if (not context["plan"] and context["subscription_id"] and
                    (not stored_subscription or context["subscription_id"] != stored_subscription)):
                conn.run("""UPDATE stripe_academy_events SET status='ignored',member_code=:code,
                    error='unknown_subscription',processed_at=NOW() WHERE event_id=:event_id""",
                    code=member_code, event_id=event_id)
                conn.run("COMMIT")
                return {"handled": True, "ignored": "unknown_subscription"}
            if (event_type != "checkout.session.completed" and context["subscription_id"] and
                    stored_subscription and context["subscription_id"] != stored_subscription):
                conn.run("""UPDATE stripe_academy_events SET status='ignored',member_code=:code,
                    error='stale_subscription',processed_at=NOW() WHERE event_id=:event_id""",
                    code=member_code, event_id=event_id)
                conn.run("COMMIT")
                return {"handled": True, "ignored": "stale_subscription"}
        elif context["email"] and context["plan"]:
            member_code = _create_or_reuse_explorer(conn, context["email"], member_name)
            created_member = True
        else:
            conn.run("""UPDATE stripe_academy_events SET status='ignored',
                error='unmatched_customer',processed_at=NOW() WHERE event_id=:event_id""",
                event_id=event_id)
            conn.run("COMMIT")
            return {"handled": True, "ignored": "unmatched_customer"}

        status = context["status"]
        if status in {"active", "trialing"} and context["plan"]:
            conn.run("""UPDATE members SET
                stripe_customer_id=CASE WHEN :customer<>'' THEN :customer ELSE stripe_customer_id END,
                stripe_subscription_id=CASE WHEN :subscription<>'' THEN :subscription ELSE stripe_subscription_id END,
                stripe_price_id=:price_id,billing_status=:status,
                billing_current_period_end=:period_end,
                billing_cancel_at_period_end=:cancel_at_period_end,
                access_level='member',actif=CASE WHEN admin_suspended THEN FALSE ELSE TRUE END,
                copy_actif=CASE WHEN admin_suspended THEN FALSE ELSE copy_actif END,
                date_souscription=COALESCE(date_souscription,NOW()),date_fin=:period_end,
                email_verified_at=COALESCE(email_verified_at,NOW())
                WHERE code=:code""",
                customer=context["customer_id"], subscription=context["subscription_id"],
                price_id=context["price_id"], status=status, period_end=context["period_end"],
                cancel_at_period_end=context["cancel_at_period_end"], code=member_code)
            _grant_member_beta_credits(conn, member_code)
            mark_marketing_conversion(conn, member_code, member_email, event_id)
        elif status == "past_due":
            conn.run("""UPDATE members SET billing_status='past_due',
                stripe_customer_id=CASE WHEN :customer<>'' THEN :customer ELSE stripe_customer_id END,
                stripe_subscription_id=CASE WHEN :subscription<>'' THEN :subscription ELSE stripe_subscription_id END
                WHERE code=:code""", customer=context["customer_id"],
                subscription=context["subscription_id"], code=member_code)
        elif status in {"canceled", "unpaid", "paused"}:
            conn.run("""UPDATE members SET billing_status=:status,
                billing_cancel_at_period_end=FALSE,billing_current_period_end=COALESCE(:period_end,NOW()),
                date_fin=LEAST(COALESCE(date_fin,NOW()),NOW()),access_level='explorer',
                actif=TRUE,copy_actif=FALSE WHERE code=:code""",
                status=status, period_end=context["period_end"], code=member_code)
        else:
            conn.run("""UPDATE members SET
                stripe_customer_id=CASE WHEN :customer<>'' THEN :customer ELSE stripe_customer_id END,
                stripe_subscription_id=CASE WHEN :subscription<>'' THEN :subscription ELSE stripe_subscription_id END,
                billing_status=CASE WHEN :status<>'' THEN :status ELSE billing_status END
                WHERE code=:code""", customer=context["customer_id"],
                subscription=context["subscription_id"], status=status, code=member_code)

        conn.run("""UPDATE stripe_academy_events SET status='processed',member_code=:code,
            processed_at=NOW() WHERE event_id=:event_id""", code=member_code, event_id=event_id)
        conn.run("COMMIT")
    except Exception as error:
        try: conn.run("ROLLBACK")
        except Exception: pass
        app.logger.error("Synchronisation abonnement Stripe %s: %s", event_id, error)
        raise
    finally:
        conn.close()

    if context["status"] in {"active", "trialing"}:
        try:
            sync_brevo_member_contact(member_email)
            if created_member:
                email_bienvenue_membre(member_name.split()[0], member_email, member_code)
        except Exception as notification_error:
            app.logger.error("Notification activation Stripe %s: %s", member_code, notification_error)
    return {"handled": True, "member_code": member_code, "status": context["status"]}


@app.route("/api/stripe/analyse-credits", methods=["POST"])
def stripe_analysis_credits_webhook():
    raw_body = request.get_data(cache=False)
    if not _stripe_signature_valid(raw_body, request.headers.get("Stripe-Signature", "")):
        return jsonify({"ok": False}), 400
    try:
        event = json.loads(raw_body.decode("utf-8"))
        event_type = str(event.get("type", ""))
        stripe_object = (event.get("data") or {}).get("object") or {}
        academy_event_types = {
            "checkout.session.completed",
            "checkout.session.expired",
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "customer.subscription.paused",
            "customer.subscription.resumed",
            "invoice.paid",
            "invoice.payment_failed",
        }
        if event_type == "checkout.session.expired":
            conn = get_conn()
            try:
                mark_checkout_expired(conn, str(stripe_object.get("id", "")))
            finally:
                conn.close()
            return jsonify({"ok": True, "handled": True, "status": "checkout_expired"})
        if (event_type in academy_event_types and
                (event_type != "checkout.session.completed" or stripe_object.get("mode") == "subscription")):
            result = _process_academy_stripe_event(event)
            return jsonify({"ok": True, **result})
        if event.get("type") != "checkout.session.completed":
            return jsonify({"ok": True})
        checkout = stripe_object
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


LEGAL_PAGES = {"mentions-legales", "cgv", "confidentialite", "cookies", "retractation"}


@app.route("/legal/<slug>")
def legal_page(slug):
    if slug not in LEGAL_PAGES:
        abort(404)
    return render_template("legal.html", slug=slug)

@app.route("/calculateur")
@login_required
def calculateur():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    return render_template("calculateur.html", member=member,
                           demo_mode=_current_demo_mode(code, member))

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET","POST"])
def login():
    if "member_code" in session:
        session_member = get_member(session.get("member_code", ""))
        return redirect(url_for("explorer_home" if _member_is_explorer(session_member) else "accueil"))
    if "analysis_account_code" in session:
        return redirect(url_for("analyse_ia"))
    error = None
    notice = session.pop("login_notice", None)
    # L'accès Explorer exige toujours une adresse confirmée. Aucun contournement
    # vers le compte démo partagé n'est autorisé si le prestataire e-mail est indisponible.
    explorer_gate_enabled = True
    if request.method == "POST":
        remote = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        now = time.time()
        with _ADMIN_LOGIN_LOCK:
            recent = [stamp for stamp in _ADMIN_LOGIN_ATTEMPTS.get("member:" + remote, []) if now - stamp < 900]
        if len(recent) >= 10:
            return render_template("login.html", error="Trop de tentatives. Réessaie dans 15 minutes.", notice=None,
                                   explorer_gate_enabled=explorer_gate_enabled), 429
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
                        (email, prenom, token_hash, source, status, payload, account_code,
                         created_at, expires_at, verified_at)
                        VALUES (:email, :prenom, :token_hash, 'explorer', 'pending', '', '',
                                NOW(), NOW() + INTERVAL '24 hours', NULL)
                        ON CONFLICT (email) DO UPDATE SET prenom=:prenom, token_hash=:token_hash,
                        source='explorer', status='pending', payload='', account_code='', created_at=NOW(),
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
            with _ADMIN_LOGIN_LOCK:
                recent.append(now)
                _ADMIN_LOGIN_ATTEMPTS["member:" + remote] = recent
            error = "Code invalide. Vérifie ton code et réessaie."
        else:
            with _ADMIN_LOGIN_LOCK:
                _ADMIN_LOGIN_ATTEMPTS.pop("member:" + remote, None)
            session.permanent = True
            session["member_code"] = member["code"]
            try:
                conn = get_conn()
                conn.run("UPDATE members SET last_login=NOW() WHERE code=:c", c=code)
                conn.close()
            except: pass
            pending_plan = session.pop("pending_academy_plan", "")
            if pending_plan in ACADEMY_PLAN_BY_ID:
                return redirect(url_for("academy_subscription_checkout", plan_id=pending_plan))
            return redirect(url_for("explorer_home" if _member_is_explorer(member) else "accueil"))
    return render_template("login.html", error=error, notice=notice,
                           explorer_gate_enabled=explorer_gate_enabled)


@app.route("/explorer/confirmer/<token>")
def confirm_explorer_email(token):
    token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    try:
        conn = get_conn()
        rows = conn.run("""SELECT email, prenom FROM prospect_email_verifications
            WHERE token_hash=:token_hash AND source='explorer'
              AND status='pending' AND expires_at > NOW()""",
            token_hash=token_hash)
        if not rows:
            conn.close()
            return render_template("login.html",
                error="Ce lien de confirmation est invalide ou a expiré.", notice=None,
                explorer_gate_enabled=True), 400
        email, prenom = rows[0]
        member_code = _create_or_reuse_explorer(conn, email, prenom)
        conn.run("""UPDATE prospect_email_verifications SET status='verified', verified_at=NOW(),
                    account_code=:code WHERE token_hash=:token_hash""",
                 code=member_code, token_hash=token_hash)
        upsert_marketing_contact_for_member(conn, member_code)
        conn.close()
        sync_result = sync_brevo_prospect_contact(email, prenom, "Explorer confirmé")
        if not sync_result.get("ok"):
            app.logger.error("Synchronisation prospect confirme %s: %s", email, sync_result.get("error"))
        try:
            ready_result = send_brevo_explorer_ready(email, prenom, member_code)
            if not ready_result.get("ok"):
                app.logger.error("Envoi code Explorer %s: %s", email, ready_result.get("error"))
        except Exception as ready_error:
            app.logger.error("Envoi code Explorer %s: %s", email, ready_error)
        session["prospect_verified_email"] = email
        session.pop("analysis_account_code", None)
        session.permanent = True
        session["member_code"] = member_code
        pending_plan = session.pop("pending_academy_plan", "")
        if pending_plan in ACADEMY_PLAN_BY_ID:
            return redirect(url_for("academy_subscription_checkout", plan_id=pending_plan))
        return redirect(url_for("explorer_home"))
    except Exception as exc:
        app.logger.error("Confirmation prospect Explorer: %s", exc)
        return render_template("login.html",
            error="La confirmation n’a pas pu être validée. Réessaie dans quelques instants.", notice=None,
                explorer_gate_enabled=True), 500

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
        demo_mode=_current_demo_mode(code, member))


@app.route("/abonnement")
@login_required
def abonnement():
    """Espace membre de lecture et de gestion de l'abonnement Académie."""
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))

    price_id = str(member.get("stripe_price_id") or "")
    plan = ACADEMY_SUBSCRIPTION_PLANS.get(price_id)
    billing_status = str(member.get("billing_status") or "legacy").lower()
    has_academy_access = _member_has_academy_access(member)
    renewal_date = member.get("billing_current_period_end") or member.get("date_fin")
    return render_template(
        "abonnement.html",
        member=member,
        current_plan=plan,
        plans=list(ACADEMY_SUBSCRIPTION_PLANS.values()),
        billing_status=billing_status,
        has_academy_access=has_academy_access,
        renewal_date=renewal_date,
        cancel_at_period_end=bool(member.get("billing_cancel_at_period_end")),
        stripe_managed=bool(member.get("stripe_customer_id")),
        demo_mode=_current_demo_mode(code, member),
    )


@app.route("/api/billing/portal", methods=["POST"])
@login_required
def create_billing_portal_session():
    """Crée côté serveur une session courte vers le portail Stripe Bectanse."""
    member = get_member(session["member_code"])
    customer_id = str((member or {}).get("stripe_customer_id") or "")
    if not customer_id:
        return jsonify({
            "ok": False,
            "error": "Aucun abonnement Stripe n’est encore rattaché à ce compte.",
            "fallback_url": url_for("vip_landing"),
        }), 404
    if not STRIPE_SECRET_KEY:
        app.logger.error("Portail abonnement: STRIPE_SECRET_KEY absente")
        return jsonify({"ok": False, "error": "Le portail est momentanément indisponible."}), 503

    try:
        return_url = url_for("abonnement", _external=True, _scheme="https")
        response = requests.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(STRIPE_SECRET_KEY, ""),
            data={
                "customer": customer_id,
                "configuration": STRIPE_ACADEMY_PORTAL_CONFIGURATION,
                "return_url": return_url,
            },
            timeout=20,
        )
        stripe_data = response.json()
        portal_url = str(stripe_data.get("url") or "")
        if response.status_code >= 400 or not portal_url.startswith("https://billing.stripe.com/"):
            error_message = ((stripe_data.get("error") or {}).get("message") or "Erreur Stripe")
            app.logger.error("Création portail abonnement %s: %s", member.get("code"), error_message)
            return jsonify({"ok": False, "error": "Le portail est momentanément indisponible."}), 502
        return jsonify({"ok": True, "url": portal_url})
    except Exception as error:
        app.logger.error("Création portail abonnement %s: %s", member.get("code"), error)
        return jsonify({"ok": False, "error": "Le portail est momentanément indisponible."}), 502


@app.route("/guide-robot")
@login_required
def guide_robot():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    return render_template(
        "guide_robot.html",
        member=member,
        demo_mode=_current_demo_mode(code, member),
    )


@app.route("/guide-robot/telecharger")
@login_required
def telecharger_guide_robot():
    return send_from_directory(
        os.path.join(app.static_folder, "guides"),
        "bectanse-auto-guide-membre.pdf",
        as_attachment=True,
        download_name="Guide-Bectanse-AUTO.pdf",
    )

@app.route("/offres")
@login_required
def offres():
    return redirect(url_for("vip_landing"))

@app.route("/save", methods=["POST"])
@login_required
@academy_access_required
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
        # Conserver les données du profil Trading (login, serveur, mot de passe)
        # lorsque le membre ne modifie que les paramètres du robot.
        merged_params = dict(member.get("params") or {})
        merged_params.update(p)
        conn.run("UPDATE members SET params=:p, historique=:h, last_login=NOW() WHERE code=:c",
                 p=json.dumps(_protect_params(merged_params)),
                 h=json.dumps(_protect_history(hist[-50:])), c=code)
        conn.close()
        confirm_url = f"https://acces.bectanse-academie.com/confirm/{code}?token={_action_token('confirm_params', {'code': code})}"
        problem_url = f"https://acces.bectanse-academie.com/problem/{code}?token={_action_token('problem_params', {'code': code})}"
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
@academy_access_required
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
    data = request.get_json(silent=True) or {}
    prenom    = str(data.get("prenom", "")).strip()[:80]
    nom_fam   = str(data.get("nom", "")).strip()[:100]
    capital   = str(data.get("capital", "")).strip()[:50]
    email     = str(data.get("email", "")).strip().lower()[:254]
    telephone = str(data.get("telephone", "")).strip()[:50]
    telegram  = str(data.get("telegram", "")).strip()[:100]
    plateforme= str(data.get("plateforme", "MT4"))[:20]
    serveur   = str(data.get("serveur", "PUPrime-Live"))[:120]
    mt_login  = str(data.get("mt_login", "")).strip()[:80]
    mt_pass   = str(data.get("mt_password", "")).strip()[:200]
    if not all([prenom, nom_fam, capital, email, telephone, mt_login, mt_pass]):
        return jsonify({"ok": False, "error": "Tous les champs sont obligatoires."}), 400
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return jsonify({"ok": False, "error": "Adresse e-mail invalide."}), 400
    registration = {
        "prenom": prenom, "nom": nom_fam, "capital": capital, "email": email,
        "telephone": telephone, "telegram": telegram, "plateforme": plateforme,
        "serveur": serveur, "mt_login": mt_login, "mt_password": mt_pass,
        "parrain_code": str(data.get("parrain_code", "")).strip().upper()[:80],
    }
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    try:
        conn = get_conn()
        existing = conn.run("""SELECT code, COALESCE(access_level,'member') FROM members
            WHERE LOWER(email)=LOWER(:email) ORDER BY created_at DESC LIMIT 1""", email=email)
        if existing and existing[0][1] not in {"explorer", "demo"}:
            conn.close()
            return jsonify({"ok": False, "error": "Un compte existe déjà avec cette adresse. Connecte-toi avec ton code ou contacte le support."}), 409
        conn.run("""INSERT INTO prospect_email_verifications
            (email, prenom, token_hash, source, status, payload, account_code,
             created_at, expires_at, verified_at)
            VALUES (:email,:prenom,:token,'member_registration','pending',:payload,'',
                    NOW(),NOW() + INTERVAL '24 hours',NULL)
            ON CONFLICT (email) DO UPDATE SET prenom=:prenom, token_hash=:token,
                source='member_registration', status='pending', payload=:payload,
                account_code='', created_at=NOW(), expires_at=NOW() + INTERVAL '24 hours',
                verified_at=NULL""",
            email=email, prenom=prenom, token=token_hash,
            payload=_encrypt_value(json.dumps(registration, ensure_ascii=False)))
        conn.close()
        confirmation_url = request.url_root.rstrip("/") + url_for(
            "confirm_member_registration", token=raw_token)
        sent = send_brevo_member_verification(email, prenom, confirmation_url)
        if not sent.get("ok"):
            return jsonify({"ok": False, "error": "L’e-mail de confirmation n’a pas pu être envoyé. Réessaie dans quelques instants."}), 503
        return jsonify({"ok": True, "pending_verification": True, "email": email})
    except Exception as error:
        app.logger.error("Préparation inscription membre: %s", error)
        return jsonify({"ok": False, "error": "Impossible de préparer l’inscription pour le moment."}), 500


@app.route("/inscription/confirmer/<token>")
def confirm_member_registration(token):
    token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    conn = get_conn()
    try:
        conn.run("BEGIN")
        rows = conn.run("""SELECT email, payload FROM prospect_email_verifications
            WHERE token_hash=:token AND source='member_registration' AND status='pending'
              AND expires_at > NOW() FOR UPDATE""", token=token_hash)
        if not rows:
            conn.run("ROLLBACK")
            return render_template("login.html",
                error="Ce lien d’inscription est invalide, déjà utilisé ou expiré.", notice=None,
                explorer_gate_enabled=True), 400
        email, encrypted_payload = rows[0]
        payload = json.loads(_decrypt_value(encrypted_payload))
        existing = conn.run("""SELECT code, COALESCE(access_level,'member') FROM members
            WHERE LOWER(email)=LOWER(:email) ORDER BY created_at DESC LIMIT 1""", email=email)
        if existing and existing[0][1] not in {"explorer", "demo"}:
            raise RuntimeError("Un compte membre existe déjà avec cette adresse")
        code = existing[0][0] if existing else _new_member_code(conn)
        nom_complet = f"{payload['prenom']} {payload['nom']}".strip()
        params = _protect_params({**default_params(), "mt_login": payload["mt_login"],
            "mt_password": payload["mt_password"], "serveur": payload["serveur"],
            "plateforme": payload["plateforme"]})
        if existing:
            conn.run("""UPDATE members SET nom=:nom,capital=:capital,email=:email,
                telephone=:telephone,telegram=:telegram,params=:params,historique='[]',
                parrain_code=:parrain,actif=TRUE,copy_actif=FALSE,access_level='explorer',
                email_verified_at=NOW(),date_fin=NULL,last_login=NOW() WHERE code=:code""",
                nom=nom_complet, capital=payload["capital"], email=email,
                telephone=payload["telephone"], telegram=payload["telegram"],
                params=json.dumps(params), parrain=payload.get("parrain_code", ""), code=code)
        else:
            conn.run("""INSERT INTO members
                (code,nom,capital,email,telephone,telegram,params,historique,parrain_code,
                 actif,copy_actif,access_level,email_verified_at,date_souscription,date_fin)
                VALUES (:code,:nom,:capital,:email,:telephone,:telegram,:params,'[]',:parrain,
                        TRUE,FALSE,'explorer',NOW(),NOW(),NULL)""",
                code=code, nom=nom_complet, capital=payload["capital"], email=email,
                telephone=payload["telephone"], telegram=payload["telegram"],
                params=json.dumps(params), parrain=payload.get("parrain_code", ""))
        # Le parrainage financier n'est validé qu'après un paiement Stripe réussi.
        parrain_ref = ""
        referral_notice = None
        if parrain_ref:
            parrain_rows = conn.run("""SELECT code,nom,filleuls_count,gains_parrainage
                FROM members WHERE code=:code AND actif=TRUE""", code=parrain_ref)
            if parrain_rows:
                p_code, p_nom, old_count, old_gains = parrain_rows[0]
                p_fill, p_gains = int(old_count or 0) + 1, int(old_gains or 0) + 50
                conn.run("UPDATE members SET filleuls_count=:count,gains_parrainage=:gains WHERE code=:code",
                         count=p_fill, gains=p_gains, code=p_code)
                referral_notice = (p_nom, p_fill, p_gains)
        _migrate_legacy_analysis_account(conn, email, code)
        conn.run("""UPDATE prospect_email_verifications SET status='verified',
            verified_at=NOW(),account_code=:code,payload='' WHERE token_hash=:token""",
            code=code, token=token_hash)
        upsert_marketing_contact_for_member(conn, code)
        conn.run("COMMIT")
    except Exception as error:
        try: conn.run("ROLLBACK")
        except Exception: pass
        app.logger.error("Confirmation inscription membre: %s", error)
        return render_template("login.html",
            error="Cette inscription ne peut pas être confirmée. Contacte le support si le problème persiste.",
            notice=None, explorer_gate_enabled=True), 400
    finally:
        conn.close()

    if referral_notice:
        p_nom, p_fill, p_gains = referral_notice
        send_telegram(f"🎉 *Nouveau filleul confirmé !*\n\n👤 *{p_nom}* — {nom_complet} a confirmé son inscription.\n💰 +50€ ajoutés\n📊 Total : *{p_fill}* | Gains : *{p_gains}€*")
    telegram_line = f"  Telegram : {payload['telegram']}\n" if payload.get("telegram") else ""
    send_telegram(
        f"🆕 *COMPTE EXPLORER CONFIRMÉ*\n\n👤 *{nom_complet}*\n💰 Capital : *{payload['capital']}*\n"
        f"🔑 Code : `{code}`\n\n📞 *CONTACT*\n  Email : `{email}`\n  Tél : `{payload['telephone']}`\n{telegram_line}\n"
        f"📊 *MT4/MT5*\n  Plateforme : *{payload['plateforme']}*\n  Serveur : *{payload['serveur']}*\n"
        f"  Login : `{payload['mt_login']}`\n  Mot de passe investisseur : stocké chiffré\n\n🔒 Accès payant verrouillé jusqu'au paiement Stripe")
    send_brevo_explorer_ready(email, payload["prenom"], code)
    sync_brevo_prospect_contact(email, payload["prenom"], "Inscription confirmée — Explorer")
    session.clear()
    session.permanent = True
    session["member_code"] = code
    pending_plan = session.pop("pending_academy_plan", "")
    if pending_plan in ACADEMY_PLAN_BY_ID:
        return redirect(url_for("academy_subscription_checkout", plan_id=pending_plan))
    return redirect(url_for("explorer_home"))

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
    payload = _action_payload(request.args.get("token", ""), "confirm_params")
    if not payload or payload.get("code") != code:
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
            h=json.dumps(_protect_history(hist)), c=code)
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
    payload = _action_payload(request.args.get("token", ""), "problem_params")
    if not payload or payload.get("code") != code:
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
            h=json.dumps(_protect_history(hist)), c=code)
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
    payload = _action_payload(request.args.get("token", ""), "set_dates")
    if not payload or payload.get("code") != code:
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
    <div class='pill active' onclick="setD(30,this)">1 mois</div>
    <div class='pill' onclick="setD(90,this)">3 mois</div>
    <div class='pill' onclick="setD(365,this)">1 an</div></div>
    <label>Jours exact</label><input type='number' name='duree' id='dur' value='30' min='1' max='400' required>
    <button type='submit'>✅ Enregistrer</button></form></div>
    <script>function setD(n,el){{document.getElementById('dur').value=n;document.querySelectorAll('.pill').forEach(p=>p.classList.remove('active'));el.classList.add('active');}}</script>
    </body></html>"""

@app.route("/desactiver/<code>")
def desactiver_membre(code):
    if not _admin_session_valid():
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c", c=code)
        nom = rows[0][0] if rows else "Membre"
        conn.run("""UPDATE members SET actif=FALSE,copy_actif=FALSE,
            admin_suspended=TRUE WHERE code=:c""", c=code)
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
                 c=code, n=nom, cap=capital, p=json.dumps(_protect_params({**default_params(), "mt_login": mt_login, "mt_password": mt_pass, "serveur": serveur, "plateforme": plateforme})), h=json.dumps([]))
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
    ("1 mois", "500€",   "https://buy.stripe.com/14A6oH5qE51iaJZ6ckgfu0H"),
    ("3 mois", "1 000€", "https://buy.stripe.com/7sY8wP2esbpG6tJ0S0gfu0G"),
    ("1 an",   "4 000€", "https://buy.stripe.com/8x23cv4mAdxO7xN0S0gfu0F"),
]

def send_email_relance(member, jours_restants):
    """Envoie un email de relance au membre."""
    try:
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

        result = send_transactional_email(email_dest, sujet, html_body)
        if result.get("ok"):
            app.logger.info(f"Email relance envoyé à {email_dest} ({jours_restants}j)")
            return True
        app.logger.error("Relance indisponible pour %s: %s", email_dest, result.get("error"))
        return False
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
                "INSERT INTO members (code,nom,capital,email,telephone,telegram,params,historique,actif,copy_actif,access_level,date_fin) "
                "VALUES ('BCT-DEMO2026','Compte Demo','1000','demo@bectanse.com','','',\'{}\',\'[]\',TRUE,FALSE,'demo',NULL)"
            )
            app.logger.info("Compte demo BCT-DEMO2026 cree")
        conn.close()
    except Exception as e:
        app.logger.error("init_demo: %s", e)

# ── E-MAILS TRANSACTIONNELS ET BREVO MEMBRES
def send_agentmail(to_email, subject, html_content):
    """Envoie un e-mail transactionnel via AgentMail sans exposer la clé."""
    import urllib.parse as _up
    import urllib.request as _ur

    clean_email = (to_email or "").strip().lower()
    if not AGENTMAIL_API_KEY or not AGENTMAIL_INBOX_ID or "@" not in clean_email:
        return {"ok": False, "error": "AgentMail non configuré"}

    payload = {
        "to": [clean_email],
        "subject": subject,
        "html": html_content,
        "text": "Bectanse Académie — ouvre cet e-mail dans un client compatible HTML.",
    }
    if GMAIL_USER and "@" in GMAIL_USER:
        payload["reply_to"] = [GMAIL_USER]

    try:
        request = _ur.Request(
            "https://api.agentmail.to/v0/inboxes/"
            + _up.quote(AGENTMAIL_INBOX_ID, safe="")
            + "/messages/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with _ur.urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": result.get("message_id", "")}
    except Exception as error:
        app.logger.error("AgentMail vers %s: %s", clean_email, error)
        return {"ok": False, "error": str(error)[:500]}


def send_transactional_email(to_email, subject, html_content):
    """Utilise AgentMail puis Gmail en ultime secours."""
    agentmail_result = send_agentmail(to_email, subject, html_content)
    if agentmail_result.get("ok"):
        return agentmail_result
    gmail_sent = send_email(to_email, subject, html_content)
    if gmail_sent:
        return {"ok": True, "message_id": "gmail-smtp"}
    return {
        "ok": False,
        "error": agentmail_result.get("error") or "Envoi e-mail indisponible",
    }


def send_brevo_membre(to_email, to_name, subject, html_content, tag):
    import urllib.request as _ur, os as _os
    brevo_key = _os.environ.get("BREVO_KEY", "")
    is_marketing = str(tag or "").startswith("marketing-")
    if not brevo_key:
        if is_marketing:
            return {"ok": False, "error": "Brevo indisponible pour un envoi marketing"}
        app.logger.info("BREVO_KEY non definie — utilisation du relais transactionnel")
        return send_transactional_email(to_email, subject, html_content)
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
            if is_marketing:
                return {"ok": False, "error": "Crédits Brevo épuisés"}
            return send_transactional_email(to_email, subject, html_content)
        if is_marketing and credits is not None and int(credits) <= 2500:
            return {"ok": False, "error": "Réserve Brevo de 2 500 e-mails atteinte"}
        p = json.dumps({"sender":{"email":"lerisluketo@bectanse-academie.com","name":"Bectanse Académie"},"to":[{"email":to_email,"name":to_name}],"subject":subject,"htmlContent":html_content,"tags":["bectanse-membre",tag]}).encode()
        r = _ur.Request("https://api.brevo.com/v3/smtp/email",data=p,headers={"api-key":brevo_key,"Content-Type":"application/json"})
        with _ur.urlopen(r,timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": payload.get("messageId", "")}
    except Exception as e:
        app.logger.error("Brevo: %s",e)
        if is_marketing:
            return {"ok": False, "error": str(e)[:500]}
        fallback = send_transactional_email(to_email, subject, html_content)
        if not fallback.get("ok"):
            fallback["error"] = str(e)[:500]
        return fallback

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
    html = ("<!doctype html><html><body style='margin:0;background:#090909;font-family:Arial,sans-serif;color:#fff'>"
        "<div style='max-width:580px;margin:0 auto;padding:28px 18px'>"
        "<div style='border:1px solid #332014;border-radius:22px;background:#111;padding:34px'>"
        "<p style='margin:0 0 10px;color:#ff6a00;font-weight:800;font-size:12px;letter-spacing:1.4px'>BECTANSE ACADÉMIE</p>"
        "<h1 style='margin:0 0 14px;font-size:27px'>Confirme ton adresse e-mail</h1>"
        "<p style='margin:0 0 24px;color:#b7b7b7;line-height:1.6'>Un clic suffit pour ouvrir immédiatement l’espace Explorer en lecture seule.</p>"
        "<a href='"+confirmation_url+"' style='display:block;text-align:center;background:#ff6a00;color:#fff;text-decoration:none;font-weight:800;padding:16px;border-radius:12px'>CONFIRMER ET EXPLORER →</a>"
        "<p style='margin:20px 0 0;color:#777;font-size:12px;line-height:1.5'>Ce lien expire dans 24 heures. Si tu n’as pas demandé cet accès, ignore simplement cet e-mail.</p>"
        "</div></div></body></html>")
    subject = "Confirme ton accès Explorer — Bectanse Académie"
    if not brevo_email_delivery_available():
        return send_transactional_email(to_email, subject, html)
    payload = json.dumps({
        "sender": {"email": "lerisluketo@bectanse-academie.com", "name": "Bectanse Académie"},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
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
        fallback = send_transactional_email(to_email, subject, html)
        if not fallback.get("ok"):
            fallback["error"] = str(error)[:500]
        return fallback


def send_brevo_member_verification(to_email, to_name, confirmation_url):
    """Valide l'adresse avant de créer le compte membre et de stocker le contact."""
    import urllib.request as _ur
    html = ("<!doctype html><html><body style='margin:0;background:#090909;font-family:Arial,sans-serif;color:#fff'>"
        "<div style='max-width:580px;margin:0 auto;padding:28px 18px'><div style='border:1px solid #332014;border-radius:22px;background:#111;padding:34px'>"
        "<p style='margin:0 0 10px;color:#ff6a00;font-weight:800;font-size:12px;letter-spacing:1.4px'>BECTANSE ACADÉMIE</p>"
        "<h1 style='margin:0 0 14px;font-size:27px'>Confirme ton inscription</h1>"
        "<p style='margin:0 0 24px;color:#b7b7b7;line-height:1.6'>Nous devons vérifier ton adresse avant de créer ton code BCT personnel et d’activer ton espace.</p>"
        "<a href='"+confirmation_url+"' style='display:block;text-align:center;background:#ff6a00;color:#fff;text-decoration:none;font-weight:800;padding:16px;border-radius:12px'>CONFIRMER MON ADRESSE →</a>"
        "<p style='margin:20px 0 0;color:#777;font-size:12px;line-height:1.5'>Lien sécurisé, utilisable une seule fois et valable 24 heures.</p>"
        "</div></div></body></html>")
    subject = "Confirme ton inscription — Bectanse Académie"
    if not brevo_email_delivery_available():
        return send_transactional_email(to_email, subject, html)
    payload = json.dumps({
        "sender": {"email": "lerisluketo@bectanse-academie.com", "name": "Bectanse Académie"},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html, "tags": ["bectanse-inscription", "verification-email"]
    }).encode("utf-8")
    try:
        req = _ur.Request("https://api.brevo.com/v3/smtp/email", data=payload,
            headers={"api-key": os.environ["BREVO_KEY"], "Content-Type": "application/json"}, method="POST")
        with _ur.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": result.get("messageId", "")}
    except Exception as error:
        app.logger.error("Brevo verification membre: %s", error)
        fallback = send_transactional_email(to_email, subject, html)
        if not fallback.get("ok"):
            fallback["error"] = str(error)[:500]
        return fallback


def send_brevo_explorer_ready(to_email, to_name, member_code):
    """Remet le code BCT permanent uniquement après le double opt-in Explorer."""
    import urllib.request as _ur
    login_url = "https://acces.bectanse-academie.com/"
    html = ("<!doctype html><html><body style='margin:0;background:#090909;font-family:Arial,sans-serif;color:#fff'>"
        "<div style='max-width:580px;margin:0 auto;padding:28px 18px'><div style='border:1px solid #332014;border-radius:22px;background:#111;padding:34px'>"
        "<p style='margin:0 0 10px;color:#ff6a00;font-weight:800;font-size:12px;letter-spacing:1.4px'>BECTANSE EXPLORER</p>"
        "<h1 style='margin:0 0 14px;font-size:27px'>Ton espace d’observation est prêt</h1>"
        "<p style='color:#aaa;line-height:1.6'>Conserve ce code personnel pour te reconnecter sur tous tes appareils :</p>"
        "<div style='margin:22px 0;padding:18px;text-align:center;border:1px solid #ff6a00;border-radius:12px;color:#fff;font-size:25px;font-weight:900;letter-spacing:2px'>"+member_code+"</div>"
        "<a href='"+login_url+"' style='display:block;text-align:center;background:#ff6a00;color:#fff;text-decoration:none;font-weight:800;padding:16px;border-radius:12px'>OUVRIR MON ESPACE →</a>"
        "<p style='margin:20px 0 0;color:#777;font-size:12px;line-height:1.5'>Ton compte Explorer est gratuit et en lecture seule. Les fonctionnalités membres se débloquent depuis la présentation complète.</p>"
        "</div></div></body></html>")
    subject = "Ton code Explorer Bectanse"
    if not brevo_email_delivery_available():
        return send_transactional_email(to_email, subject, html)
    payload = json.dumps({
        "sender": {"email": "lerisluketo@bectanse-academie.com", "name": "Bectanse Académie"},
        "to": [{"email": to_email, "name": to_name or "Membre Explorer"}],
        "subject": subject, "htmlContent": html,
        "tags": ["bectanse-prospect", "explorer-ready"]
    }).encode("utf-8")
    try:
        req = _ur.Request("https://api.brevo.com/v3/smtp/email", data=payload,
            headers={"api-key": os.environ["BREVO_KEY"], "Content-Type": "application/json"}, method="POST")
        with _ur.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
        return {"ok": True, "message_id": result.get("messageId", "")}
    except Exception as error:
        fallback = send_transactional_email(to_email, subject, html)
        if not fallback.get("ok"):
            fallback["error"] = str(error)[:500]
        return fallback


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
        "<a href='https://acces.bectanse-academie.com/vip' style='background:#FF6A00;color:#fff;font-size:16px;font-weight:800;text-decoration:none;padding:16px 36px;border-radius:12px;'>Renouveler mon acc&egrave;s &rarr;</a>"
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

            # Les e-mails sont maintenant pilotés par le moteur marketing unifié.
            # Cette tâche conserve uniquement les alertes dans l'app et Telegram.

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
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel"},
                {"text": "💬 Contacter", "url": f"https://t.me/lerisluketobot"}
            ], [
                {"text": "👤 Voir le profil", "url": f"{BASE_URL}/admin-panel"}
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
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel"},
                {"text": "💬 Contacter", "url": f"https://t.me/lerisluketobot"}
            ], [
                {"text": "👤 Voir le profil", "url": f"{BASE_URL}/admin-panel"}
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
                {"text": "➕ Prolonger 30j", "url": f"{BASE_URL}/admin-panel"},
                {"text": "➕ Prolonger 90j", "url": f"{BASE_URL}/admin-panel"}
            ], [
                {"text": "💬 Contacter membre", "url": f"https://t.me/lerisluketobot"},
                {"text": "👤 Voir profil", "url": f"{BASE_URL}/admin-panel"}
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
            p256dh=:p256dh, auth=:auth, updated_at=NOW(), failure_count=0, last_error=''""",
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
                    vapid_claims=dict(VAPID_CLAIMS),
                    timeout=10,
                    ttl=86400,
                    headers={"Urgency": "high"}
                )
                return ep, True, False, ""
            except WebPushException as ex:
                status = ex.response.status_code if ex.response is not None else None
                response_body = ex.response.text if ex.response is not None else ""
                invalid_vapid = status == 400 and "VapidPkHashMismatch" in response_body
                return ep, False, status in (404, 410) or invalid_vapid, f"HTTP {status or 'inconnu'}"
            except Exception as error:
                app.logger.warning("Push global %s: %s", _member_code, error)
                return ep, False, False, str(error)[:220]

        with ThreadPoolExecutor(max_workers=min(12, len(subs))) as pool:
            deliveries = list(pool.map(deliver, subs))
        result["delivered"] = sum(1 for _, ok, _, _ in deliveries if ok)
        result["failed"] = result["registered"] - result["delivered"]
        dead = [endpoint for endpoint, _, expired, _ in deliveries if expired]
        conn2 = get_conn()
        try:
            for ep, delivered, expired, delivery_error in deliveries:
                if expired:
                    conn2.run("DELETE FROM push_subscriptions WHERE endpoint=:ep", ep=ep)
                elif delivered:
                    conn2.run("""UPDATE push_subscriptions SET last_delivery_at=NOW(),
                        updated_at=NOW(), failure_count=0, last_error='' WHERE endpoint=:ep""", ep=ep)
                else:
                    conn2.run("""UPDATE push_subscriptions SET updated_at=NOW(),
                        failure_count=failure_count+1, last_error=:error WHERE endpoint=:ep""",
                        ep=ep, error=delivery_error)
        finally:
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
                    vapid_claims=dict(VAPID_CLAIMS),
                    timeout=10,
                    ttl=86400,
                    headers={"Urgency": "high"}
                )
                result["delivered"] += 1
                delivery_error = ""
                try:
                    conn = get_conn()
                    conn.run("""UPDATE push_subscriptions SET last_delivery_at=NOW(),
                        updated_at=NOW(), failure_count=0, last_error='' WHERE endpoint=:endpoint""",
                        endpoint=endpoint)
                    conn.close()
                except Exception: pass
            except WebPushException as error:
                result["failed"] += 1
                status = error.response.status_code if error.response is not None else None
                response_body = error.response.text if error.response is not None else ""
                if status in (404, 410) or (status == 400 and "VapidPkHashMismatch" in response_body):
                    dead.append(endpoint)
                else:
                    try:
                        conn = get_conn()
                        conn.run("""UPDATE push_subscriptions SET updated_at=NOW(),
                            failure_count=failure_count+1,last_error=:error WHERE endpoint=:endpoint""",
                            endpoint=endpoint, error=f"HTTP {status or 'inconnu'}")
                        conn.close()
                    except Exception: pass
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

def _dispatch_canal_push(tg_msg_id, msg_type, text_content):
    """Déclenche une seule notification par publication VIP, quel que soit son chemin d'entrée."""
    conn = None
    try:
        conn = get_conn()
        claimed = conn.run("""UPDATE canal_messages SET push_notified_at=NOW()
            WHERE tg_msg_id=:mid AND push_notified_at IS NULL RETURNING id""", mid=tg_msg_id)
        if not claimed:
            return {"registered": 0, "delivered": 0, "failed": 0, "members": 0, "skipped": True}
    except Exception as error:
        app.logger.error("Claim push Canal VIP %s: %s", tg_msg_id, error)
        return {"registered": 0, "delivered": 0, "failed": 0, "members": 0, "error": str(error)}
    finally:
        if conn:
            try: conn.close()
            except: pass

    labels = {
        "signal": "📊 Nouveau signal VIP",
        "resultat": "✅ Nouveau résultat VIP",
        "alerte": "🚨 Alerte VIP prioritaire",
        "annonce": "📢 Annonce Canal VIP",
        "audio": "🎙️ Nouveau vocal VIP",
        "message": "💬 Nouveau message VIP",
    }
    clean_preview = " ".join(str(text_content or "").split())
    preview = clean_preview[:110] + ("…" if len(clean_preview) > 110 else "")
    if not preview:
        preview = "Une nouvelle publication est disponible dans ton espace membre."
    result = send_push_to_all(labels.get(msg_type, "💬 Nouveau message VIP"), preview, "/canal")
    result["skipped"] = False
    if result.get("registered", 0) and not result.get("delivered", 0):
        try:
            conn = get_conn()
            conn.run("UPDATE canal_messages SET push_notified_at=NULL WHERE tg_msg_id=:mid", mid=tg_msg_id)
            conn.close()
        except Exception: pass
    app.logger.info("Push Canal VIP %s: %s", tg_msg_id, result)
    return result

# ── CANAL VIP ─────────────────────────────────────────────────────────────────

CANAL_BOT_TOKEN = os.environ.get("CANAL_BOT_TOKEN", "")
CANAL_GROUP_ID  = int(os.environ.get("CANAL_GROUP_ID", "-1003605441967"))
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
        # Web Push prioritaire si nouveau message. Le claim atomique empêche les doublons
        # quand une publication existe à la fois dans Telegram et dans l'administration.
        if not edited:
            threading.Thread(
                target=_dispatch_canal_push,
                args=(tg_msg_id, msg_type, text_content),
                daemon=True
            ).start()
    except Exception as e:
        app.logger.error(f"canal_webhook: {e}")
    return jsonify({"ok": True})


@app.route("/api/canal/messages")
@login_required
@academy_access_required
def api_canal_messages():
    """Retourne un segment du fil du canal VIP avec pagination."""
    if "member_code" not in session:
        return jsonify({"error": "non connecté"}), 401
    try:
        def no_cache(payload):
            response = jsonify(payload)
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response

        after = request.args.get("after", 0, type=int)
        before = request.args.get("before", 0, type=int)
        limit = request.args.get("limit", 50, type=int)
        limit = max(1, min(limit, 200))
        conn = get_conn()

        if after > 0 and before > 0:
            before = 0

        if after > 0:
            rows = conn.run(
                """SELECT id, tg_msg_id, text_content, msg_type, photo_url, audio_url, edited,
                          sent_at::text FROM canal_messages
                   WHERE id > :after AND (deleted IS NULL OR deleted=FALSE) ORDER BY id ASC""",
                after=after
            )
            conn.close()
        else:
            before_clause = "AND id < :before" if before > 0 else ""
            where_clause = f"""WHERE (deleted IS NULL OR deleted=FALSE) {before_clause}"""
            params = {"limit": limit}
            if before > 0:
                params["before"] = before
            rows = conn.run(
                """SELECT id, tg_msg_id, text_content, msg_type, photo_url, audio_url, edited,
                          sent_at::text FROM canal_messages
                   """ + f"""{where_clause} ORDER BY id DESC LIMIT :limit""",
                **params
            )

            next_cursor = rows[-1][0] if rows else 0
            if next_cursor:
                remaining = conn.run(
                    "SELECT 1 FROM canal_messages WHERE id < :before AND (deleted IS NULL OR deleted=FALSE) LIMIT 1",
                    before=next_cursor
                )
                has_more_older = bool(remaining)
            else:
                has_more_older = False
            conn.close()
            msgs = [{"id": r[0], "tg_msg_id": r[1], "text_content": r[2], "msg_type": r[3],
                    "photo_url": r[4], "audio_url": r[5], "edited": r[6], "sent_at": r[7]} for r in reversed(rows)]
            return no_cache({
                "messages": msgs,
                "has_more_older": has_more_older,
                "cursor": msgs[0]["id"] if msgs else None
            })

        msgs = [{"id":r[0],"tg_msg_id":r[1],"text_content":r[2],"msg_type":r[3],
                 "photo_url":r[4],"audio_url":r[5],"edited":r[6],"sent_at":r[7]} for r in rows]
        return no_cache({"messages": msgs, "has_more_older": False, "cursor": None})
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
                return jsonify({"error": res.get("description", "Telegram error")}), 502
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
        push_result = _dispatch_canal_push(tg_msg_id, msg_type, text)
        return jsonify({"ok": True, "push": push_result})
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
    demo_mode = _current_demo_mode(code, member)
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
        lien = "https://acces.bectanse-academie.com/vip"
        sent = 0
        for code, nom, email, date_fin in membres:
            if not email or not date_fin:
                continue
            from datetime import date as _date
            now = datetime.now()
            jours = (date_fin.date() - now.date()).days if hasattr(date_fin, 'date') else (date_fin - now).days
            if jours in (7, 3, 1, 0):
                subject, html = email_relance_html(nom.split()[0], jours, lien)
                if send_transactional_email(email, subject, html).get("ok"):
                    sent += 1
                    app.logger.info(f"Relance J{jours} envoyée à {email}")
        app.logger.info(f"check_relances: {sent} emails envoyés")
    except Exception as e:
        app.logger.error(f"check_and_send_relances: {e}")

register_growth_features(
    app=app,
    get_conn=get_conn,
    get_member=get_member,
    login_required=login_required,
    academy_access_required=academy_access_required,
    current_demo_mode=_current_demo_mode,
    admin_required=admin_required,
)
register_marketing_routes(
    app=app,
    get_conn=get_conn,
    send_email=send_brevo_membre,
    action_token=_action_token,
    action_payload=_action_payload,
    admin_required=admin_required,
    notify_admin=send_telegram,
)


_marketing_job_alerted_at = 0.0


def job_marketing_lifecycle():
    """Exécute les parcours Brevo avec verrouillage et plafonds en base."""
    try:
        result = run_marketing_automation(
            get_conn=get_conn,
            send_email=send_brevo_membre,
            action_token=_action_token,
            notify_admin=send_telegram,
        )
        app.logger.info("marketing lifecycle: %s", result)
    except Exception as error:
        global _marketing_job_alerted_at
        app.logger.error("marketing lifecycle: %s", error)
        if time.time() - _marketing_job_alerted_at >= 6 * 60 * 60:
            _marketing_job_alerted_at = time.time()
            send_telegram(
                "🚨 *AUTOMATISATION MARKETING*\n\n"
                "Le moteur n'a pas terminé son dernier passage. "
                f"Erreur : `{str(error)[:450]}`\n\n"
                "Une nouvelle tentative sera lancée automatiquement dans 30 minutes."
            )

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
            job_marketing_lifecycle, 'interval', minutes=30,
            id='marketing_lifecycle', replace_existing=True,
            next_run_time=_paris_now() + timedelta(minutes=3),
            coalesce=True, max_instances=1, misfire_grace_time=900
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
