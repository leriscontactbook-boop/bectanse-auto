import os, json, secrets, string, requests, time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import pg8000.native

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bectanse2026secretkeyprod")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8673691177:AAGWihA4Ch_T73nuJCLUq49Yr_3OiFdOoHs")
ADMIN_ID  = os.environ.get("ADMIN_ID",  "6164373751")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "bectanse_admin_2026")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def parse_db_url(url):
    """Parse postgres://user:pass@host:port/db"""
    url = url.replace("postgres://", "").replace("postgresql://", "")
    user_pass, rest = url.split("@")
    user, password = user_pass.split(":", 1)
    host_port, database = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":")
    else:
        host, port = host_port, "5432"
    return {"user": user, "password": password, "host": host,
            "port": int(port), "database": database}

def get_conn():
    p = parse_db_url(DATABASE_URL)
    return pg8000.native.Connection(
        user=p["user"], password=p["password"],
        host=p["host"], port=p["port"], database=p["database"],
        ssl_context=True
    )

def row_to_dict(conn, query, params=None):
    if params:
        rows = conn.run(query, *params)
    else:
        rows = conn.run(query)
    if not rows:
        return None
    cols = [c["name"] for c in conn.columns]
    return [dict(zip(cols, row)) for row in rows]

def init_db():
    for attempt in range(5):
        try:
            conn = get_conn()
            conn.run("""
                CREATE TABLE IF NOT EXISTS members (
                    code        TEXT PRIMARY KEY,
                    nom         TEXT NOT NULL,
                    capital     TEXT NOT NULL,
                    actif       BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW(),
                    last_login  TIMESTAMP,
                    params      TEXT DEFAULT \'{}\',
                    copy_actif  BOOLEAN DEFAULT TRUE,
                    historique  TEXT DEFAULT \'[]\'
                )
            """)
            # Ajouter copy_actif si elle n'existe pas encore
            try:
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS copy_actif BOOLEAN DEFAULT TRUE")
            except:
                pass
            conn.close()
            app.logger.info("DB init OK")
            return True
        except Exception as e:
            app.logger.warning(f"DB init attempt {attempt+1}: {e}")
            time.sleep(3)
    return False

def default_params():
    return {
        "mode_risque": "Lots fixes",
        "lots": 0.01, "lots_max": 5, "slippage": 100,
        "forcer_lot_minimum": False, "inverser_trades": False,
        "copier_ordres_en_attente": True, "convertir_pending_invalide": False,
        "copier_sl": True, "drawdown_actif": False, "drawdown_pct": 5.0,
        "drawdown_gain_actif": False, "drawdown_gain_pct": 5.0,
        "objectif_actif": False, "objectif_gain_pct": 5.0,
        "objectif_perte_pct": 3.0, "objectif_periode": "Mensuel",
        "filtre_news": False,
    }

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
        if isinstance(m.get("params"), str):
            m["params"] = json.loads(m["params"])
        if isinstance(m.get("historique"), str):
            m["historique"] = json.loads(m["historique"])
        if m.get("copy_actif") is None:
            m["copy_actif"] = True
        return m
    except Exception as e:
        app.logger.error(f"get_member: {e}")
        return None

def update_login(code):
    try:
        conn = get_conn()
        conn.run("UPDATE members SET last_login=NOW() WHERE code=:c", c=code)
        conn.close()
    except: pass

def save_params_db(code, params, hist_entry):
    try:
        conn = get_conn()
        rows = conn.run("SELECT historique FROM members WHERE code=:c", c=code)
        if rows:
            hist = json.loads(rows[0][0] if isinstance(rows[0][0], str) else json.dumps(rows[0][0]))
        else:
            hist = []
        hist.append(hist_entry)
        conn.run(
            "UPDATE members SET params=:p, historique=:h, last_login=NOW() WHERE code=:c",
            p=json.dumps(params), h=json.dumps(hist[-50:]), c=code
        )
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"save_params: {e}")
        return False

def bool_icon(v): return "✅" if v else "❌"

def build_notif(member, params, code):
    p = params
    dd_p = f"`{p['drawdown_pct']}%`" if p["drawdown_actif"] else "Désactivé"
    dd_g = f"`{p['drawdown_gain_pct']}%`" if p["drawdown_gain_actif"] else "Désactivé"
    obj = (f"+{p['objectif_gain_pct']}% / -{p['objectif_perte_pct']}% ({p['objectif_periode']})"
           if p["objectif_actif"] else "Désactivé")
    return (
        f"🔔 *DEMANDE MODIFICATION PARAMÈTRES*\n\n"
        f"👤 *{member['nom']}*  |  Code : `{code}`\n"
        f"💰 Capital : *{member['capital']}*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ *MODE* : {p['mode_risque']}\n"
        f"📊 Lots : `{p['lots']}` | Max : `{p['lots_max']}` | Slip : `{p['slippage']}`\n"
        + (f"  └ Risque : `{p.get('risque_pct','—')}%`\n" if p.get('mode_risque')=='Risque en %' else "")
        + (f"  └ Multiplicateur : `{p.get('multiplicateur','—')}x`\n" if p.get('mode_risque')=="Copier les lots de l'envoyeur" else "")
        + (f"  └ Risque balance : `{p.get('risque_balance_pct','—')}%`\n" if p.get('mode_risque')=='Risque par solde (Balance)' else "")
        + (f"  └ Risque equity : `{p.get('risque_equity_pct','—')}%`\n" if p.get('mode_risque')=='Risque par capitaux (Equity)' else "")
        + "\n"
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

def send_telegram(text, reply_markup=None):
    if not BOT_TOKEN: return
    try:
        payload = {"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload, timeout=5
        )
    except: pass

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "member_code" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/confirm/<code>")
def confirm_params(code):
    """Admin clique sur ce lien depuis Telegram pour marquer comme appliqué"""
    try:
        conn = get_conn()
        rows = conn.run("SELECT historique, nom FROM members WHERE code=:c", c=code)
        if not rows:
            conn.close()
            return "<h2 style=\'font-family:sans-serif;padding:40px\'>❌ Membre introuvable</h2>"
        nom = rows[0][1]
        hist = json.loads(rows[0][0]) if rows[0][0] else []
        # Marquer la dernière demande comme appliquée
        for h in reversed(hist):
            if h.get("statut") == "en_attente":
                h["statut"] = "applique"
                break
        conn.run("UPDATE members SET historique=:h WHERE code=:c",
                 h=json.dumps(hist), c=code)
        conn.close()
        # Notifier le membre via Telegram si possible
        send_telegram(f"✅ *Paramètres appliqués !*\n\n👤 *{nom}* — ton compte Bectanse AUTO a été mis à jour.\n\nLe système tourne avec tes nouveaux réglages. 🚀")
        return f"""<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center;'>
            <h1 style='color:#059669'>✅ Appliqué !</h1>
            <p>Les paramètres de <strong>{nom}</strong> ont été marqués comme appliqués.</p>
            <p style='color:#6B7280;font-size:14px'>Le membre a reçu une confirmation automatique.</p>
            </body></html>"""
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

@app.route("/problem/<code>")
def problem_params(code):
    """Admin clique pour signaler un problème"""
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c", c=code)
        nom = rows[0][0] if rows else "Membre"
        conn.close()
        send_telegram(f"⚠️ *Problème signalé*\n\nLe membre *{nom}* (`{code}`) doit être contacté concernant sa dernière demande.")
        return f"""<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center;'>
            <h1 style='color:#F59E0B'>⚠️ Problème signalé</h1>
            <p>Contacte <strong>{nom}</strong> pour résoudre le problème.</p>
            </body></html>"""
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if request.method == "GET":
        return render_template("inscription.html")

    data      = request.get_json()
    prenom    = data.get("prenom", "").strip()
    nom_fam   = data.get("nom", "").strip()
    capital   = data.get("capital", "").strip()
    plateforme= data.get("plateforme", "MT4")
    serveur   = data.get("serveur", "PUPrime-Live")
    mt_login  = data.get("mt_login", "").strip()
    mt_pass   = data.get("mt_password", "").strip()

    if not all([prenom, nom_fam, capital, mt_login, mt_pass]):
        return jsonify({"ok": False, "error": "Tous les champs sont obligatoires."})

    nom_complet = f"{prenom} {nom_fam}"
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

    try:
        conn = get_conn()
        conn.run(
            "INSERT INTO members (code, nom, capital, params, historique) VALUES (:c, :n, :cap, :p, :h)",
            c=code, n=nom_complet, cap=capital,
            p=json.dumps(default_params()), h=json.dumps([])
        )
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Erreur base de données : {str(e)}"})

    # Notification Telegram complète à l'équipe
    notif = (
        f"🆕 *NOUVELLE INSCRIPTION BECTANSE AUTO*\n\n"
        f"👤 *{nom_complet}*\n"
        f"💰 Capital : *{capital}*\n"
        f"🔑 Code d\'accès : `{code}`\n\n"
        f"📊 *CONNEXION METATRADER*\n"
        f"  Plateforme : *{plateforme}*\n"
        f"  Serveur : *{serveur}*\n"
        f"  Login : `{mt_login}`\n"
        f"  Mot de passe investisseur : `{mt_pass}`\n\n"
        f"⚡ *ACTION REQUISE* — Connecter ce membre sur Sociate Trade\n"
        f"Une fois connecté, le membre peut accéder à son espace avec le code ci-dessus."
    )
    send_telegram(notif)

    return jsonify({"ok": True, "code": code})

@app.route("/toggle-copy", methods=["POST"])
@login_required
def toggle_copy():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return jsonify({"ok": False, "error": "membre introuvable"})
    try:
        # Lire etat actuel directement depuis DB
        conn = get_conn()
        rows = conn.run("SELECT copy_actif FROM members WHERE code=:c", c=code)
        current = rows[0][0] if rows and rows[0][0] is not None else True
        new_state = not current
        # Sauvegarder
        conn.run("UPDATE members SET copy_actif=:s WHERE code=:c", s=new_state, c=code)
        conn.close()
        # Notification Telegram avec nom du membre
        icon   = "✅" if new_state else "⏸️"
        status = "ACTIVÉ" if new_state else "DÉSACTIVÉ"
        msg = (
            f"{icon} *COPY TRADING {status}*\n\n"
            f"👤 *{member['nom']}*\n"
            f"🔑 Code : `{code}`\n"
            f"💰 Capital : *{member['capital']}*\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
            + ("✅ Copy trading actif — aucune action requise."
               if new_state else
               "⚠️ *Action requise* — Désactiver le copy sur Sociate Trade pour ce membre.")
        )
        send_telegram(msg)
        return jsonify({"ok": True, "copy_actif": new_state, "nom": member['nom']})
    except Exception as e:
        app.logger.error(f"toggle_copy error: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET", "POST"])
def login():
    if "member_code" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        member = get_member(code)
        if not member:
            error = "Code invalide. Vérifie ton code et réessaie."
        elif not member.get("actif", True):
            error = "Accès désactivé. Contacte le support Bectanse."
        else:
            session["member_code"] = member["code"]
            update_login(member["code"])
            return redirect(url_for("dashboard"))
    return render_template("login.html", error=error)

@app.route("/dashboard")
@login_required
def dashboard():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        session.clear()
        return redirect(url_for("login"))
    params = member.get("params") or default_params()
    hist = list(reversed((member.get("historique") or [])[-10:]))
    return render_template("dashboard.html", member=member, params=params, historique=hist)

@app.route("/save", methods=["POST"])
@login_required
def save():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return jsonify({"ok": False})
    data = request.get_json()
    p = {
        "mode_risque": data.get("mode_risque", "Lots fixes"),
        "lots": float(data.get("lots", 0.01)),
        "lots_max": float(data.get("lots_max", 5)),
        "slippage": int(data.get("slippage", 100)),
        "forcer_lot_minimum": bool(data.get("forcer_lot_minimum")),
        "inverser_trades": bool(data.get("inverser_trades")),
        "copier_ordres_en_attente": bool(data.get("copier_ordres_en_attente")),
        "convertir_pending_invalide": bool(data.get("convertir_pending_invalide")),
        "copier_sl": bool(data.get("copier_sl")),
        "drawdown_actif": bool(data.get("drawdown_actif")),
        "drawdown_pct": float(data.get("drawdown_pct", 5)),
        "drawdown_gain_actif": bool(data.get("drawdown_gain_actif")),
        "drawdown_gain_pct": float(data.get("drawdown_gain_pct", 5)),
        "objectif_actif": bool(data.get("objectif_actif")),
        "objectif_gain_pct": float(data.get("objectif_gain_pct", 5)),
        "objectif_perte_pct": float(data.get("objectif_perte_pct", 3)),
        "objectif_periode": data.get("objectif_periode", "Mensuel"),
        "filtre_news": bool(data.get("filtre_news")),
    }
    hist_entry = {"date": datetime.now().strftime("%d/%m/%Y %H:%M"), "statut": "en_attente", "params": p}
    ok = save_params_db(code, p, hist_entry)
    if ok:
        # Boutons inline Telegram pour confirmer ou signaler un problème
        confirm_url = f"https://bectanse-auto-eyq-production.up.railway.app/confirm/{code}"
        problem_url = f"https://bectanse-auto-eyq-production.up.railway.app/problem/{code}"
        markup = {
            "inline_keyboard": [[
                {"text": "✅ Appliqué sur Sociate Trade", "url": confirm_url},
                {"text": "❌ Problème — Contacter", "url": problem_url}
            ]]
        }
        send_telegram(build_notif(member, p, code), reply_markup=markup)
    return jsonify({"ok": ok})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin/add", methods=["POST"])
def admin_add():
    if request.headers.get("X-Admin-Key", "") != ADMIN_KEY:
        return jsonify({"ok": False}), 403
    data = request.get_json()
    nom = data.get("nom", "")
    capital = data.get("capital", "")
    code = "BCT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    try:
        conn = get_conn()
        conn.run(
            "INSERT INTO members (code,nom,capital,params,historique) VALUES (:c,:n,:cap,:p,:h)",
            c=code, n=nom, cap=capital,
            p=json.dumps(default_params()), h=json.dumps([])
        )
        conn.close()
        send_telegram(f"✅ *Nouveau membre créé*\n\n👤 *{nom}* | 💰 *{capital}*\n🔑 Code : `{code}`")
        return jsonify({"ok": True, "code": code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
