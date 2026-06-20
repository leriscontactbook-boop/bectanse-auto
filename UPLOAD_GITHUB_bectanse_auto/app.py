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
                    copy_actif        BOOLEAN DEFAULT TRUE,
                    date_souscription  TIMESTAMP DEFAULT NOW(),
                    date_fin           TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days'),
                    email              TEXT DEFAULT '',
                    telephone          TEXT DEFAULT '',
                    telegram           TEXT DEFAULT '',
                    historique  TEXT DEFAULT \'[]\'
                )
            """)
            # Ajouter copy_actif si elle n'existe pas encore
            try:
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS copy_actif BOOLEAN DEFAULT TRUE")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS date_souscription TIMESTAMP DEFAULT NOW()")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS date_fin TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days')")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS telephone TEXT DEFAULT ''")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS telegram TEXT DEFAULT ''")
                conn.run("ALTER TABLE members ADD COLUMN IF NOT EXISTS alerte_lue BOOLEAN DEFAULT FALSE")
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
    obj  = (f"+{p['objectif_gain_pct']}% / -{p['objectif_perte_pct']}% ({p['objectif_periode']})"
            if p["objectif_actif"] else "Désactivé")

    # Ligne mode enrichie selon le type
    mode_detail = ""
    if p.get("mode_risque") == "Risque en %":
        mode_detail = f"  └ Risque : `{p.get('risque_pct','—')}%`\n"
    elif p.get("mode_risque") == "Copier les lots de l\'envoyeur":
        mode_detail = f"  └ Multiplicateur : `{p.get('multiplicateur','—')}x`\n"
    elif p.get("mode_risque") == "Risque par solde (Balance)":
        mode_detail = f"  └ Risque balance : `{p.get('risque_balance_pct','—')}%`\n"
    elif p.get("mode_risque") == "Risque par capitaux (Equity)":
        mode_detail = f"  └ Risque equity : `{p.get('risque_equity_pct','—')}%`\n"

    # Symboles modifiés si mode Lot par symbole
    sym_lines = ""
    if p.get("mode_risque") == "Lot par symbole" and p.get("lot_symboles"):
        modifies = [(s, l) for s, l in p["lot_symboles"].items() if float(l) != 0.01]
        if modifies:
            sym_lines = "\n📋 *SYMBOLES CONFIGURÉS*\n"
            sym_lines += "".join([f"  `{s}` : `{l}` lots\n" for s, l in modifies])

    return (
        f"🔔 *DEMANDE MODIFICATION PARAMÈTRES*\n\n"
        f"👤 *{member['nom']}*  |  Code : `{code}`\n"
        f"💰 Capital : *{member['capital']}*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y à %H:%M')}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ *MODE* : {p['mode_risque']}\n"
        f"📊 Lots : `{p['lots']}` | Max : `{p['lots_max']}` | Slip : `{p['slippage']}`\n"
        + mode_detail
        + sym_lines
        + f"\n🔧 *OPTIONS*\n"
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
    email     = data.get("email", "").strip()
    telephone = data.get("telephone", "").strip()
    telegram  = data.get("telegram", "").strip()
    plateforme= data.get("plateforme", "MT4")
    serveur   = data.get("serveur", "PUPrime-Live")
    mt_login  = data.get("mt_login", "").strip()
    mt_pass   = data.get("mt_password", "").strip()

    if not all([prenom, nom_fam, capital, email, telephone, mt_login, mt_pass]):
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
    tg_line = f"  Telegram : {telegram}\n" if telegram else "  Telegram : non renseigné\n"
    notif = (
        f"🆕 *NOUVELLE INSCRIPTION BECTANSE AUTO*\n\n"
        f"👤 *{nom_complet}*\n"
        f"💰 Capital : *{capital}*\n"
        f"🔑 Code d\'accès : `{code}`\n\n"
        f"📞 *CONTACT MEMBRE*\n"
        f"  Email : `{email}`\n"
        f"  Téléphone : `{telephone}`\n"
        + tg_line +
        f"\n📊 *CONNEXION METATRADER*\n"
        f"  Plateforme : *{plateforme}*\n"
        f"  Serveur : *{serveur}*\n"
        f"  Login : `{mt_login}`\n"
        f"  Mot de passe investisseur : `{mt_pass}`\n\n"
        f"⚡ *ACTION REQUISE* — Connecter ce membre sur Sociate Trade\n"
        f"Une fois connecté, le membre peut accéder à son espace avec son code."
    )
    # Ajouter bouton URL pour définir les dates directement depuis Telegram
    set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
    markup = {
        "inline_keyboard": [[
            {"text": "📅 Définir les dates d'abonnement", "url": set_dates_url}
        ]]
    }
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": notif, "parse_mode": "Markdown",
                  "reply_markup": markup},
            timeout=5
        )
    except Exception as e:
        app.logger.error(f"Telegram notif inscription: {e}")

    # Code conservé dans la notif admin — envoi manuel si besoin

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

@app.route("/set-dates/<code>", methods=["GET", "POST"])
def set_dates(code):
    """Page admin pour définir les dates d'abonnement d'un membre"""
    # Vérif sécurité basique via token admin
    token_param = request.args.get("t", "")
    if token_param != ADMIN_KEY:
        return "<h2 style='font-family:sans-serif;padding:40px;color:red'>⛔ Non autorisé</h2>", 403

    _, member = get_member(code) if False else (None, None)
    # Récupérer le membre
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, capital, date_souscription, date_fin FROM members WHERE code=:c", c=code)
        conn.close()
        if not rows:
            return "<h2 style='font-family:sans-serif;padding:40px'>❌ Membre introuvable</h2>"
        nom, capital, date_sous, date_fin_actuelle = rows[0]
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

    if request.method == "POST":
        debut   = request.form.get("debut", "")
        duree   = int(request.form.get("duree", 30))
        try:
            from datetime import datetime, timedelta
            date_debut = datetime.strptime(debut, "%Y-%m-%d")
            date_fin_new = date_debut + timedelta(days=duree)
            conn = get_conn()
            conn.run(
                "UPDATE members SET date_souscription=:ds, date_fin=:df WHERE code=:c",
                ds=date_debut, df=date_fin_new, c=code
            )
            conn.close()
            send_telegram(
                f"✅ *Dates d'abonnement définies*\n\n"
                f"👤 *{nom}*\n"
                f"📅 Début : *{date_debut.strftime('%d/%m/%Y')}*\n"
                f"📅 Fin : *{date_fin_new.strftime('%d/%m/%Y')}*\n"
                f"⏱ Durée : *{duree} jours*"
            )
            return f"""<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center;'>
                <h1 style='color:#059669'>✅ Dates définies !</h1>
                <p><strong>{nom}</strong></p>
                <p>Début : <strong>{date_debut.strftime('%d/%m/%Y')}</strong></p>
                <p>Fin : <strong>{date_fin_new.strftime('%d/%m/%Y')}</strong> ({duree} jours)</p>
                <p style='color:#6B7280;font-size:14px'>Le membre voit ses dates dans son espace.</p>
                </body></html>"""
        except Exception as e:
            return f"<h2>Erreur: {e}</h2>"

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""<html>
    <head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Définir les dates — {nom}</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background:#0d0d0d; color:#fff; 
               display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
        .card {{ background:#1F2937; border:1px solid rgba(91,33,182,0.3); border-radius:16px; 
                padding:36px 32px; width:100%; max-width:400px; }}
        h2 {{ color:#F59E0B; font-size:20px; margin-bottom:4px; }}
        .sub {{ color:rgba(255,255,255,0.4); font-size:13px; margin-bottom:24px; }}
        label {{ display:block; font-size:11px; font-weight:700; letter-spacing:0.12em; 
                text-transform:uppercase; color:rgba(255,255,255,0.4); margin-bottom:6px; }}
        input, select {{ width:100%; background:rgba(255,255,255,0.06); border:1px solid rgba(91,33,182,0.3);
                border-radius:8px; padding:11px 14px; color:#fff; font-size:14px; margin-bottom:16px;
                box-sizing:border-box; outline:none; }}
        .pills {{ display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }}
        .pill {{ padding:7px 16px; border-radius:20px; border:1px solid rgba(91,33,182,0.3);
                background:rgba(255,255,255,0.04); color:rgba(255,255,255,0.5); 
                cursor:pointer; font-size:13px; font-weight:600; }}
        .pill:hover, .pill.active {{ background:#5B21B6; border-color:#5B21B6; color:#fff; }}
        button {{ width:100%; background:#5B21B6; color:#fff; border:none; border-radius:10px;
                 padding:14px; font-size:15px; font-weight:700; cursor:pointer; margin-top:4px; }}
        button:hover {{ background:#4C1D95; }}
    </style>
    </head>
    <body>
    <div class='card'>
        <h2>📅 Définir l'abonnement</h2>
        <div class='sub'>👤 {nom} — 💰 {capital}</div>
        <form method='POST'>
            <label>Date de début</label>
            <input type='date' name='debut' value='{today}' required>
            <label>Durée</label>
            <div class='pills'>
                <div class='pill' onclick="setDuree(30,this)">30 jours</div>
                <div class='pill active' onclick="setDuree(30,this)">1 mois</div>
                <div class='pill' onclick="setDuree(60,this)">2 mois</div>
                <div class='pill' onclick="setDuree(90,this)">3 mois</div>
            </div>
            <label>Nombre de jours exact</label>
            <input type='number' name='duree' id='duree' value='30' min='1' max='365' required>
            <button type='submit'>✅ Enregistrer les dates</button>
        </form>
    </div>
    <script>
    function setDuree(n, el) {{
        document.getElementById('duree').value = n;
        document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
        el.classList.add('active');
    }}
    </script>
    </body></html>"""

# ─── SCHEDULER — VÉRIFICATION EXPIRATIONS ────────────────────────────────────

def verifier_expirations():
    """Tourne tous les jours à 9h — vérifie les abonnements qui expirent"""
    try:
        conn = get_conn()
        # Membres qui expirent dans exactement 7 jours
        rows_7j = conn.run("""
            SELECT code, nom, capital, date_fin
            FROM members
            WHERE actif = TRUE
            AND date_fin::date = (CURRENT_DATE + INTERVAL '7 days')::date
        """)
        # Membres qui expirent dans exactement 3 jours
        rows_3j = conn.run("""
            SELECT code, nom, capital, date_fin
            FROM members
            WHERE actif = TRUE
            AND date_fin::date = (CURRENT_DATE + INTERVAL '3 days')::date
        """)
        # Membres expirés aujourd'hui
        rows_0j = conn.run("""
            SELECT code, nom, capital, date_fin
            FROM members
            WHERE actif = TRUE
            AND date_fin::date = CURRENT_DATE
        """)
        conn.close()

        # ── Notif équipe J-7 ──
        for row in rows_7j:
            code, nom, capital, date_fin = row
            df = date_fin.strftime('%d/%m/%Y') if date_fin else '—'
            set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
            msg = (
                f"⚠️ *ABONNEMENT EXPIRE DANS 7 JOURS*\n\n"
                f"👤 *{nom}*\n"
                f"💰 Capital : *{capital}*\n"
                f"📅 Expiration : *{df}*\n\n"
                f"💬 Pense à le relancer pour renouveler."
            )
            markup = {"inline_keyboard": [[
                {"text": "🔄 Renouveler l'abonnement", "url": set_dates_url}
            ]]}
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "Markdown",
                          "reply_markup": markup},
                    timeout=5
                )
            except: pass

        # ── Notif équipe J-3 ──
        for row in rows_3j:
            code, nom, capital, date_fin = row
            df = date_fin.strftime('%d/%m/%Y') if date_fin else '—'
            set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
            msg = (
                f"🔴 *ABONNEMENT EXPIRE DANS 3 JOURS*\n\n"
                f"👤 *{nom}*\n"
                f"💰 Capital : *{capital}*\n"
                f"📅 Expiration : *{df}*\n\n"
                f"⚡ *URGENT* — Contacter maintenant pour renouveler."
            )
            markup = {"inline_keyboard": [[
                {"text": "🔄 Renouveler l'abonnement", "url": set_dates_url}
            ]]}
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "Markdown",
                          "reply_markup": markup},
                    timeout=5
                )
            except: pass

        # ── Notif équipe J=0 (expiré aujourd'hui) ──
        for row in rows_0j:
            code, nom, capital, date_fin = row
            df = date_fin.strftime('%d/%m/%Y') if date_fin else '—'
            set_dates_url = f"https://bectanse-auto.up.railway.app/set-dates/{code}?t={ADMIN_KEY}"
            msg = (
                f"🚨 *ABONNEMENT EXPIRÉ AUJOURD'HUI*\n\n"
                f"👤 *{nom}*\n"
                f"💰 Capital : *{capital}*\n"
                f"📅 Expiré le : *{df}*\n\n"
                f"⛔ Penser à désactiver le copy sur Sociate Trade si non renouvelé."
            )
            markup = {"inline_keyboard": [[
                {"text": "🔄 Renouveler", "url": set_dates_url},
                {"text": "⛔ Désactiver", "url": f"https://bectanse-auto.up.railway.app/desactiver/{code}?t={ADMIN_KEY}"}
            ]]}
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "Markdown",
                          "reply_markup": markup},
                    timeout=5
                )
            except: pass

    except Exception as e:
        app.logger.error(f"verifier_expirations error: {e}")


def envoyer_relances():
    """Envoie des messages de relance automatiques aux membres via Telegram"""
    try:
        conn = get_conn()
        rows = conn.run("""
            SELECT code, nom, telegram, date_fin
            FROM members
            WHERE actif = TRUE
            AND date_fin::date IN (
                CURRENT_DATE + INTERVAL '7 days',
                CURRENT_DATE + INTERVAL '3 days',
                CURRENT_DATE + INTERVAL '1 day'
            )
        """)
        conn.close()

        for row in rows:
            code, nom, telegram_handle, date_fin = row
            df = date_fin.strftime('%d/%m/%Y') if date_fin else '—'
            delta_days = (date_fin.date() - __import__('datetime').date.today()).days
            df = date_fin.strftime('%d/%m/%Y') if date_fin else '—'
            delta_days = (date_fin.date() - __import__('datetime').date.today()).days

            if delta_days == 7:
                msg = (
                    f"👋 Bonjour *{nom.split()[0]}* !\n\n"
                    f"Ton abonnement *Bectanse AUTO* expire dans *7 jours* ({df}).\n\n"
                    f"Pour continuer à bénéficier du copy trading sans interruption, "
                    f"contacte-nous dès maintenant pour renouveler. 🚀\n\n"
                    f"👉 @LERISGANGSUPPORT"
                )
            elif delta_days == 3:
                msg = (
                    f"⚠️ *{nom.split()[0]}*, ton abonnement expire dans *3 jours* ({df}) !\n\n"
                    f"Pour ne pas perdre l'accès à ton espace et au copy trading, "
                    f"renouvelle maintenant.\n\n"
                    f"👉 @LERISGANGSUPPORT"
                )
            elif delta_days == 1:
                msg = (
                    f"🚨 *{nom.split()[0]}*, ton abonnement expire *demain* ({df}) !\n\n"
                    f"Dernière chance pour renouveler sans interruption de service.\n\n"
                    f"Contacte-nous immédiatement 👇\n"
                    f"👉 @LERISGANGSUPPORT"
                )
            else:
                continue

            # Telegram si username dispo
            if telegram_handle:
                try:
                    handle = telegram_handle.lstrip("@")
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={"chat_id": f"@{handle}", "text": msg, "parse_mode": "Markdown"},
                        timeout=5
                    )
                except: pass

            # Email si pas de Telegram — récupérer depuis DB
            if not telegram_handle:
                try:
                    conn2 = get_conn()
                    email_rows = conn2.run("SELECT email FROM members WHERE code=:c", c=code)
                    conn2.close()
                    if email_rows and email_rows[0][0]:
                        envoyer_email_relance(email_rows[0][0], nom, delta_days, df)
                except: pass

            # Marquer alerte à afficher dans l'espace membre
            try:
                conn3 = get_conn()
                conn3.run("UPDATE members SET alerte_lue=FALSE WHERE code=:c", c=code)
                conn3.close()
            except: pass

    except Exception as e:
        app.logger.error(f"envoyer_relances error: {e}")


def envoyer_email_relance(email, nom, jours, date_fin_str):
    """Envoie un email de relance via SMTP Gmail gratuit"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    GMAIL_USER = os.environ.get("GMAIL_USER", "")
    GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
    if not GMAIL_USER or not GMAIL_PASS:
        return

    prenom = nom.split()[0]
    if jours == 7:
        sujet = f"⚠️ Ton abonnement Bectanse AUTO expire dans 7 jours"
        corps = f"""Bonjour {prenom},

Ton abonnement Bectanse AUTO expire dans 7 jours, le {date_fin_str}.

Pour continuer à bénéficier du copy trading sans interruption, contacte-nous dès maintenant pour renouveler.

👉 Contacte notre support : @LERISGANGSUPPORT

L'équipe Bectanse
bectanse-auto.up.railway.app"""
    elif jours == 3:
        sujet = f"🔴 URGENT — Ton abonnement Bectanse AUTO expire dans 3 jours"
        corps = f"""Bonjour {prenom},

URGENT : Ton abonnement Bectanse AUTO expire dans 3 jours, le {date_fin_str}.

Pour ne pas perdre l'accès à ton espace membre et au copy trading, renouvelle maintenant.

👉 Contacte notre support : @LERISGANGSUPPORT

L'équipe Bectanse
bectanse-auto.up.railway.app"""
    elif jours == 1:
        sujet = f"🚨 Dernière chance — Ton abonnement expire DEMAIN"
        corps = f"""Bonjour {prenom},

Ton abonnement Bectanse AUTO expire DEMAIN ({date_fin_str}).

C'est ta dernière chance pour renouveler sans interruption de service.

👉 Contacte immédiatement notre support : @LERISGANGSUPPORT

L'équipe Bectanse
bectanse-auto.up.railway.app"""
    else:
        return

    try:
        msg = MIMEMultipart()
        msg['From']    = f"Bectanse AUTO <{GMAIL_USER}>"
        msg['To']      = email
        msg['Subject'] = sujet
        msg.attach(MIMEText(corps, 'plain', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, email, msg.as_string())
    except Exception as e:
        app.logger.error(f"Email relance error: {e}")


# Route pour désactiver un membre depuis Telegram
@app.route("/desactiver/<code>")
def desactiver_membre(code):
    token_param = request.args.get("t", "")
    if token_param != ADMIN_KEY:
        return "<h2 style='color:red;padding:40px;font-family:sans-serif'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c", c=code)
        nom = rows[0][0] if rows else "Membre"
        conn.run("UPDATE members SET actif=FALSE, copy_actif=FALSE WHERE code=:c", c=code)
        conn.close()
        send_telegram(f"⛔ *{nom}* désactivé — accès coupé.")
        return f"""<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center;'>
            <h1 style='color:#DC2626'>⛔ Membre désactivé</h1>
            <p><strong>{nom}</strong> n'a plus accès à l'espace membre.</p>
            </body></html>"""
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"


@app.route("/marquer-alerte-lue", methods=["POST"])
@login_required
def marquer_alerte_lue():
    code = session["member_code"]
    try:
        conn = get_conn()
        conn.run("UPDATE members SET alerte_lue=TRUE WHERE code=:c", c=code)
        conn.close()
        return jsonify({"ok": True})
    except:
        return jsonify({"ok": False})

@app.route("/offres")
@login_required
def offres():
    code = session["member_code"]
    member = get_member(code)
    return render_template("offres.html", member=member)

@app.route("/health")@app.route("/marquer-alerte-lue", methods=["POST"])
@login_required
def marquer_alerte_lue():
    code = session["member_code"]
    try:
        conn = get_conn()
        conn.run("UPDATE members SET alerte_lue=TRUE WHERE code=:c", c=code)
        conn.close()
        return jsonify({"ok": True})
    except:
        return jsonify({"ok": False})

@app.route("/offres")
@login_required
def offres():
    code = session["member_code"]
    member = get_member(code)
    return render_template("offres.html", member=member)

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
        confirm_url = f"https://bectanse-auto.up.railway.app/confirm/{code}"
        problem_url = f"https://bectanse-auto.up.railway.app/problem/{code}"
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
    try:
        init_db()
    except Exception as e:
        app.logger.error(f"DB init: {e}")

# ── SCHEDULER ──────────────────────────────────────────────────────────────────
def start_scheduler():
    try:
                scheduler = BackgroundScheduler(timezone="Europe/Paris")
        scheduler.add_job(verifier_expirations, 'cron', hour=9, minute=0)
        scheduler.add_job(envoyer_relances,     'cron', hour=10, minute=0)
        scheduler.start()
        import atexit
        atexit.register(lambda: scheduler.shutdown(wait=False))
        app.logger.info("Scheduler démarré")
    except Exception as e:
        app.logger.error(f"Scheduler error: {e}")

# Démarrer le scheduler seulement en production (pas pendant les tests)
import os as _os
if _os.environ.get("RAILWAY_ENVIRONMENT") or _os.environ.get("DATABASE_URL"):
    start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(_os.environ.get("PORT", 5000)))
