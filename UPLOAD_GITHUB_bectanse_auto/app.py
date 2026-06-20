import os, json, secrets, string, requests, time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bectanse2026secretkeyprod")

BOT_TOKEN    = os.environ.get("BOT_TOKEN",    "8673691177:AAGWihA4Ch_T73nuJCLUq49Yr_3OiFdOoHs")
ADMIN_ID     = os.environ.get("ADMIN_ID",     "6164373751")
ADMIN_KEY    = os.environ.get("ADMIN_KEY",    "bectanse_admin_2026")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_conn():
    url = DATABASE_URL
    # Railway préfixe avec postgres:// — psycopg veut postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row)

def init_db():
    for attempt in range(5):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS members (
                            code        TEXT PRIMARY KEY,
                            nom         TEXT NOT NULL,
                            capital     TEXT NOT NULL,
                            actif       BOOLEAN DEFAULT TRUE,
                            created_at  TIMESTAMP DEFAULT NOW(),
                            last_login  TIMESTAMP,
                            params      JSONB DEFAULT '{}'::jsonb,
                            historique  JSONB DEFAULT '[]'::jsonb
                        );
                    """)
                conn.commit()
            app.logger.info("DB initialisée avec succès")
            return True
        except Exception as e:
            app.logger.warning(f"DB init tentative {attempt+1}/5 : {e}")
            time.sleep(3)
    app.logger.error("DB init échouée après 5 tentatives")
    return False

def default_params():
    return {
        "mode_risque": "Lots fixes",
        "lots": 0.01, "lots_max": 5, "slippage": 100,
        "forcer_lot_minimum": False,
        "inverser_trades": False,
        "copier_ordres_en_attente": True,
        "convertir_pending_invalide": False,
        "copier_sl": True,
        "drawdown_actif": False, "drawdown_pct": 5.0,
        "drawdown_gain_actif": False, "drawdown_gain_pct": 5.0,
        "objectif_actif": False,
        "objectif_gain_pct": 5.0, "objectif_perte_pct": 3.0,
        "objectif_periode": "Mensuel",
        "filtre_news": False,
    }

def get_member(code):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM members WHERE UPPER(code)=UPPER(%s)", (code,))
                return cur.fetchone()
    except Exception as e:
        app.logger.error(f"get_member error: {e}")
        return None

def update_login(code):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE members SET last_login=NOW() WHERE code=%s", (code,))
            conn.commit()
    except:
        pass

def save_params(code, params, hist_entry):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE members
                    SET params=%s,
                        historique = historique || %s::jsonb,
                        last_login = NOW()
                    WHERE code=%s
                """, (json.dumps(params), json.dumps([hist_entry]), code))
            conn.commit()
        return True
    except Exception as e:
        app.logger.error(f"save_params error: {e}")
        return False

def bool_icon(v): return "✅" if v else "❌"

def build_notif(member, params, code):
    p = params
    dd_p = f"`{p['drawdown_pct']}%`"      if p['drawdown_actif']      else "Désactivé"
    dd_g = f"`{p['drawdown_gain_pct']}%`" if p['drawdown_gain_actif'] else "Désactivé"
    obj  = (f"+{p['objectif_gain_pct']}% / -{p['objectif_perte_pct']}% ({p['objectif_periode']})"
            if p['objectif_actif'] else "Désactivé")
    return (
        f"🔔 *DEMANDE MODIFICATION PARAMÈTRES*\n\n"
        f"👤 *{member['nom']}*  |  Code : `{code}`\n"
        f"💰 Capital : *{member['capital']}*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ *MODE* : {p['mode_risque']}\n"
        f"📊 Lots : `{p['lots']}` | Max : `{p['lots_max']}` | Slip : `{p['slippage']}`\n\n"
        f"🔧 *OPTIONS*\n"
        f"  {bool_icon(p['forcer_lot_minimum'])} Forcer lot min\n"
        f"  {bool_icon(p['inverser_trades'])} Inverser trades\n"
        f"  {bool_icon(p['copier_ordres_en_attente'])} Copier ordres en attente\n"
        f"  {bool_icon(p['convertir_pending_invalide'])} Convertir pending invalide\n"
        f"  {bool_icon(p['copier_sl'])} Copier SL\n\n"
        f"🛡️ *DRAWDOWN*\n"
        f"  Perte : {bool_icon(p['drawdown_actif'])} {dd_p}\n"
        f"  Gain  : {bool_icon(p['drawdown_gain_actif'])} {dd_g}\n\n"
        f"🎯 *OBJECTIF* : {bool_icon(p['objectif_actif'])} {obj}\n"
        f"📅 *FILTRE NEWS* : {bool_icon(p['filtre_news'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 Appliquer sur Sociate Trade."
    )

def send_telegram(text):
    if not BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        app.logger.error(f"Telegram error: {e}")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "member_code" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET", "POST"])
def login():
    if "member_code" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        code   = request.form.get("code", "").strip().upper()
        member = get_member(code)
        if not member:
            error = "Code invalide. Vérifie ton code et réessaie."
        elif not member["actif"]:
            error = "Accès désactivé. Contacte le support Bectanse."
        else:
            session["member_code"] = member["code"]
            update_login(member["code"])
            return redirect(url_for("dashboard"))
    return render_template("login.html", error=error)

@app.route("/dashboard")
@login_required
def dashboard():
    code   = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    params = member["params"] if member["params"] else default_params()
    hist   = list(reversed((member["historique"] or [])[-10:]))
    return render_template("dashboard.html", member=member, params=params, historique=hist)

@app.route("/save", methods=["POST"])
@login_required
def save():
    code   = session["member_code"]
    member = get_member(code)
    if not member:
        return jsonify({"ok": False, "error": "Membre introuvable"})
    data = request.get_json()
    p = {
        "mode_risque":              data.get("mode_risque", "Lots fixes"),
        "lots":                     float(data.get("lots", 0.01)),
        "lots_max":                 float(data.get("lots_max", 5)),
        "slippage":                 int(data.get("slippage", 100)),
        "forcer_lot_minimum":       bool(data.get("forcer_lot_minimum")),
        "inverser_trades":          bool(data.get("inverser_trades")),
        "copier_ordres_en_attente": bool(data.get("copier_ordres_en_attente")),
        "convertir_pending_invalide": bool(data.get("convertir_pending_invalide")),
        "copier_sl":                bool(data.get("copier_sl")),
        "drawdown_actif":           bool(data.get("drawdown_actif")),
        "drawdown_pct":             float(data.get("drawdown_pct", 5)),
        "drawdown_gain_actif":      bool(data.get("drawdown_gain_actif")),
        "drawdown_gain_pct":        float(data.get("drawdown_gain_pct", 5)),
        "objectif_actif":           bool(data.get("objectif_actif")),
        "objectif_gain_pct":        float(data.get("objectif_gain_pct", 5)),
        "objectif_perte_pct":       float(data.get("objectif_perte_pct", 3)),
        "objectif_periode":         data.get("objectif_periode", "Mensuel"),
        "filtre_news":              bool(data.get("filtre_news")),
    }
    hist_entry = {
        "date":   datetime.now().strftime("%d/%m/%Y %H:%M"),
        "statut": "en_attente",
        "params": p,
    }
    ok = save_params(code, p, hist_entry)
    if ok:
        send_telegram(build_notif(dict(member), p, code))
    return jsonify({"ok": ok})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin/add", methods=["POST"])
def admin_add():
    if request.headers.get("X-Admin-Key", "") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    data    = request.get_json()
    nom     = data.get("nom", "")
    capital = data.get("capital", "")
    chars   = string.ascii_uppercase + string.digits
    code    = "BCT-" + "".join(secrets.choice(chars) for _ in range(8))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO members (code,nom,capital,params,historique) VALUES (%s,%s,%s,%s,%s)",
                    (code, nom, capital, json.dumps(default_params()), json.dumps([]))
                )
            conn.commit()
        notif = (f"✅ *Nouveau membre créé*\n\n"
                 f"👤 *{nom}*  |  Capital : *{capital}*\n"
                 f"🔑 Code d'accès : `{code}`\n\n"
                 f"Envoie ce code au membre.")
        send_telegram(notif)
        return jsonify({"ok": True, "code": code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# Init DB au démarrage — non bloquant
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
