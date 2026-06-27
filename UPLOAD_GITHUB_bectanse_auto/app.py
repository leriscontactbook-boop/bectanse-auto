import os, json, secrets, string, requests, time, threading
from datetime import datetime, timedelta
from functools import wraps
from flask import send_from_directory, Flask, render_template, request, redirect, url_for, session, jsonify
import pg8000.native

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bectanse2026secretkeyprod")

BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "8673691177:AAGWihA4Ch_T73nuJCLUq49Yr_3OiFdOoHs")
ADMIN_ID   = os.environ.get("ADMIN_ID",   "6164373751")
ADMIN_KEY  = os.environ.get("ADMIN_KEY",  "bectanse_admin_2026")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
ECO_BOT_TOKEN = os.environ.get("ECO_BOT_TOKEN", "8565312655:AAFyfFQvKEiFtFJYA0yDQE1bLdH8N50UX4c")
ECO_CANAL    = os.environ.get("ECO_CANAL", "@BECTANSE_ACADEMIE")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")

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
            if isinstance(m.get(k), str):
                try: m[k] = json.loads(m[k])
                except: m[k] = {} if k=="params" else []
        if m.get("copy_actif") is None: m["copy_actif"] = True
        return m
    except Exception as e:
        app.logger.error(f"get_member: {e}")
        return None

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

def send_telegram(text, reply_markup=None, chat_id=None):
    if not BOT_TOKEN: return
    try:
        target = str(chat_id) if chat_id else str(ADMIN_ID)
        payload = {"chat_id": target, "text": text, "parse_mode": "Markdown"}
        if reply_markup: payload["reply_markup"] = reply_markup
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=10)
        app.logger.info(f"Telegram → {target}: {r.status_code}")
    except Exception as e:
        app.logger.error(f"send_telegram error: {e}")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "member_code" not in session:
            return redirect(url_for("login"))
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
    return render_template("faq.html", member=member)

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
    return render_template("formation.html", member=member)

@app.route("/accueil")
@login_required
def accueil():
    code = session["member_code"]
    member = get_member(code)
    if not member:
        return redirect(url_for("login"))
    params = member.get("params") or default_params()
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
    return render_template("accueil.html",
        member=member, params=params,
        copy_actif=copy_actif,
        date_souscription=date_souscription,
        date_fin=date_fin,
        jours_restants=jours_restants,
        statut_abo=statut_abo,
        notif_type=notif_type,
        notif_message=notif_message,
        afficher_notif=afficher_notif
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

# ── MEMBRES ──
@app.route("/admin/api/membres", methods=["GET"])
def admin_api_membres():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT code,nom,capital,actif,copy_actif,date_fin,email,telephone,telegram,parrain_code,filleuls_count,gains_parrainage FROM members ORDER BY created_at DESC")
        cols = ["code","nom","capital","actif","copy_actif","date_fin","email","telephone","telegram","parrain_code","filleuls_count","gains_parrainage"]
        membres = []
        from datetime import datetime
        for r in rows:
            m = dict(zip(cols, r))
            if m["date_fin"] and hasattr(m["date_fin"],"strftime"):
                delta = m["date_fin"] - datetime.now()
                m["jours_restants"] = max(0, delta.days)
                m["date_fin"] = m["date_fin"].strftime("%d/%m/%Y")
            else:
                m["jours_restants"] = 0
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
        code = data.get("code")
        conn = get_conn()
        if "capital" in data:
            conn.run("UPDATE members SET capital=:v WHERE code=:c", v=data["capital"], c=code)
        if "actif" in data:
            conn.run("UPDATE members SET actif=:v WHERE code=:c", v=data["actif"], c=code)
        if "copy_actif" in data:
            conn.run("UPDATE members SET copy_actif=:v WHERE code=:c", v=data["copy_actif"], c=code)
        if "jours" in data:
            from datetime import datetime, timedelta
            rows = conn.run("SELECT date_fin FROM members WHERE code=:c", c=code)
            date_fin = rows[0][0] if rows else datetime.now()
            if date_fin and date_fin > datetime.now():
                nouvelle = date_fin + timedelta(days=int(data["jours"]))
            else:
                nouvelle = datetime.now() + timedelta(days=int(data["jours"]))
            conn.run("UPDATE members SET date_fin=:df WHERE code=:c", df=nouvelle, c=code)
        conn.close()
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
        rows = conn.run("SELECT COUNT(*) FROM members WHERE actif=TRUE")
        total = rows[0][0]
        conn.run("UPDATE members SET notif_type=:t, notif_message=:m, notif_lue=FALSE WHERE actif=TRUE", t=notif_type, m=contenu)
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
        message = request.json.get("message","")
        send_telegram(message, chat_id=canal)
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

# ── STATS ──
@app.route("/admin/api/stats", methods=["GET"])
def admin_api_stats():
    key = request.args.get("key","")
    if key != ADMIN_KEY: return jsonify({"ok":False}), 403
    try:
        conn = get_conn()
        from datetime import datetime
        total = conn.run("SELECT COUNT(*) FROM members")[0][0]
        actifs = conn.run("SELECT COUNT(*) FROM members WHERE actif=TRUE")[0][0]
        copy_on = conn.run("SELECT COUNT(*) FROM members WHERE copy_actif=TRUE AND actif=TRUE")[0][0]
        expires = conn.run("SELECT COUNT(*) FROM members WHERE date_fin < NOW() AND actif=TRUE")[0][0]
        nouveaux = conn.run("SELECT COUNT(*) FROM members WHERE created_at > NOW() - INTERVAL '7 days'")[0][0]
        conn.close()
        return jsonify({"ok":True,"total":total,"actifs":actifs,"copy_on":copy_on,"expires":expires,"nouveaux_7j":nouveaux})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET","POST"])
def login():
    if "member_code" in session:
        return redirect(url_for("accueil"))
    error = None
    if request.method == "POST":
        code = request.form.get("code","").strip().upper()
        member = get_member(code)
        if not member:
            error = "Code invalide. Vérifie ton code et réessaie."
        elif not member.get("actif", True):
            error = "Accès désactivé. Contacte le support Bectanse."
        else:
            session["member_code"] = member["code"]
            try:
                conn = get_conn()
                conn.run("UPDATE members SET last_login=NOW() WHERE code=:c", c=code)
                conn.close()
            except: pass
            return redirect(url_for("accueil"))
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
    )

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
        confirm_url = f"https://bectanse-auto.up.railway.app/confirm/{code}"
        problem_url = f"https://bectanse-auto.up.railway.app/problem/{code}"
        markup = {"inline_keyboard":[[
            {"text":"✅ Appliqué sur notre système","url":confirm_url},
            {"text":"❌ Problème — Contacter","url":problem_url}
        ]]}
        try:
            tg_msg = build_notif(member, p, code)
            send_telegram(tg_msg, reply_markup=markup)
            app.logger.info(f"Telegram envoyé pour {code}")
        except Exception as tg_err:
            app.logger.error(f"Telegram save error: {tg_err}")
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
            p=json.dumps(default_params()), h=json.dumps([])
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
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id":ADMIN_ID,"text":notif,"parse_mode":"Markdown","reply_markup":markup},
            timeout=5)
    except: pass
    return jsonify({"ok": True, "code": code})

@app.route("/confirm/<code>")
def confirm_params(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom, historique FROM members WHERE code=:c", c=code)
        if not rows: return "<h2 style='padding:40px'>❌ Introuvable</h2>"
        nom = rows[0][0]
        hist = json.loads(rows[0][1]) if rows[0][1] else []
        for h in reversed(hist):
            if h.get("statut") == "en_attente":
                h["statut"] = "applique"
                break
        conn.run("UPDATE members SET historique=:h WHERE code=:c", h=json.dumps(hist), c=code)
        conn.close()
        send_telegram(f"✅ *Paramètres appliqués !*\n\n👤 *{nom}* — compte mis à jour. 🚀")
        return f"<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center'><h1 style='color:#059669'>✅ Appliqué !</h1><p>{nom}</p></body></html>"
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

@app.route("/problem/<code>")
def problem_params(code):
    if request.args.get("t","") != ADMIN_KEY:
        return "<h2 style='padding:40px;color:red'>⛔ Non autorisé</h2>", 403
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c", c=code)
        nom = rows[0][0] if rows else "Membre"
        conn.close()
        send_telegram(f"⚠️ *Problème signalé*\n\n👤 *{nom}* (`{code}`) — à contacter.")
        return f"<html><body style='font-family:sans-serif;padding:40px;background:#0d0d0d;color:#fff;text-align:center'><h1 style='color:#F59E0B'>⚠️ Signalé</h1><p>Contacter <strong>{nom}</strong></p></body></html>"
    except Exception as e:
        return f"<h2>Erreur: {e}</h2>"

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
                 c=code, n=nom, cap=capital, p=json.dumps(default_params()), h=json.dumps([]))
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


def handle_notif_globale(chat_id, contenu, notif_type):
    """Envoie une notification à TOUS les membres actifs"""
    if not contenu:
        bot_send(chat_id, "❌ Message vide.")
        return
    try:
        conn = get_conn()
        rows = conn.run("SELECT COUNT(*) FROM members WHERE actif=TRUE")
        total = rows[0][0] if rows else 0
        conn.run("""UPDATE members 
                    SET notif_type=:t, notif_message=:m, notif_lue=FALSE 
                    WHERE actif=TRUE""", t=notif_type, m=contenu)
        conn.close()
        icons = {"alerte": "🔴", "message": "💜", "resultat": "🟢", "maintenance": "🔧"}
        icon = icons.get(notif_type, "📢")
        bot_send(chat_id, 
            f"{icon} *Notification envoyée à {total} membres !*\n\n"
            f"Type : `{notif_type}`\n"
            f"Message : {contenu}")
    except Exception as e:
        bot_send(chat_id, f"❌ Erreur : {e}")


def handle_notif_individuelle(chat_id, code_dest, contenu):
    """Envoie une notification privée à UN membre spécifique"""
    if not contenu:
        bot_send(chat_id, "❌ Message vide.")
        return
    try:
        conn = get_conn()
        rows = conn.run("SELECT nom FROM members WHERE code=:c AND actif=TRUE", c=code_dest)
        if not rows:
            conn.close()
            bot_send(chat_id, f"❌ Membre `{code_dest}` introuvable ou inactif.")
            return
        nom = rows[0][0]
        conn.run("""UPDATE members 
                    SET notif_type='individuelle', notif_message=:m, notif_lue=FALSE 
                    WHERE code=:c""", m=contenu, c=code_dest)
        conn.close()
        bot_send(chat_id,
            f"✅ *Message envoyé à {nom} !*\n\n"
            f"Code : `{code_dest}`\n"
            f"Message : {contenu}")
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
        "/message TEXTE — Annonce violette 💜\n"
        "/resultat TEXTE — Performance verte 🟢\n"
        "/maintenance TEXTE — Bannière maintenance 🔧\n\n"
        "💬 *Message individuel*\n"
        "/msg BCT-XXXXXXXX TEXTE — Notif privée à un membre\n\n"
        "💡 Exemple : /alerte BUY XAUUSD — Entrée 2345 TP 2360"
    )
    markup = {"inline_keyboard": [[
        {"text": "📋 Voir les membres", "callback_data": "liste_membres"}
    ]]}
    bot_send(chat_id, msg, markup)


def handle_stats(chat_id):
    try:
        conn = get_conn()
        total    = conn.run("SELECT COUNT(*) FROM members")[0][0]
        actifs   = conn.run("SELECT COUNT(*) FROM members WHERE actif=TRUE")[0][0]
        expires  = conn.run("SELECT COUNT(*) FROM members WHERE date_fin < NOW() AND actif=TRUE")[0][0]
        copy_on  = conn.run("SELECT COUNT(*) FROM members WHERE copy_actif=TRUE AND actif=TRUE")[0][0]
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


# ── BOT CALENDRIER ÉCONOMIQUE ─────────────────────────────────────────────────

IMPACT_ICONS = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
FLAG_MAP = {
    "USD":"🇺🇸","EUR":"🇪🇺","GBP":"🇬🇧","JPY":"🇯🇵",
    "AUD":"🇦🇺","CAD":"🇨🇦","CHF":"🇨🇭","NZD":"🇳🇿","CNY":"🇨🇳"
}
JOURS_FR = {"Monday":"Lundi","Tuesday":"Mardi","Wednesday":"Mercredi",
    "Thursday":"Jeudi","Friday":"Vendredi","Saturday":"Samedi","Sunday":"Dimanche"}
MOIS_FR = {"January":"janvier","February":"février","March":"mars","April":"avril",
    "May":"mai","June":"juin","July":"juillet","August":"août",
    "September":"septembre","October":"octobre","November":"novembre","December":"décembre"}

def get_eco_calendar():
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        data = r.json()
        today_str = datetime.now().strftime("%Y-%m-%d")
        events = []
        for event in data:
            try:
                event_date = event.get("date","")[:10]
                if event_date == today_str and event.get("impact") in ["High","Medium"]:
                    events.append(event)
            except: pass
        return sorted(events, key=lambda x: x.get("date",""))
    except Exception as e:
        app.logger.error(f"eco_calendar: {e}")
        return []

def send_eco_message():
    try:
        events = get_eco_calendar()
        now = datetime.now()
        date_fr = now.strftime("%A %d %B %Y")
        for en, fr in JOURS_FR.items(): date_fr = date_fr.replace(en, fr)
        for en, fr in MOIS_FR.items(): date_fr = date_fr.replace(en, fr)
        date_fr = date_fr.capitalize()

        if not events:
            msg = (
                f"📅 *CALENDRIER ÉCONOMIQUE — {date_fr}*\n\n"
                f"✅ Aucune annonce majeure aujourd\'hui.\n"
                f"Journée calme — trading normal.\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔥 *Bectanse AUTO — Copy Trading Automatique*\n📲 bectanse-academie.com/lerisluketoVIP"
            )
        else:
            high_count = sum(1 for e in events if e.get("impact") == "High")
            msg = f"📅 *CALENDRIER ÉCONOMIQUE — {date_fr}*\n\n"
            if high_count > 0:
                msg += f"⚠️ *{high_count} annonce(s) à fort impact*\n\n"
            for event in events:
                try:
                    currency = event.get("currency","")
                    title = event.get("title","")
                    impact = event.get("impact","Low")
                    forecast = event.get("forecast","—") or "—"
                    previous = event.get("previous","—") or "—"
                    date_str = event.get("date","")
                    try:
                        from datetime import timezone
                        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z")
                        heure = dt.strftime("%H:%M")
                    except:
                        heure = "—"
                    flag = FLAG_MAP.get(currency, "🌐")
                    icon = IMPACT_ICONS.get(impact,"⚪")
                    msg += f"{icon} *{heure}* {flag} {title}\n"
                    if forecast != "—":
                        msg += f"   Prévision: `{forecast}` | Précédent: `{previous}`\n"
                    msg += "\n"
                except: pass
            msg += "━━━━━━━━━━━━━━━\n"
            msg += "🔴 Fort impact  🟡 Moyen impact\n\n"
            msg += "🔥 *Bectanse AUTO — Copy Trading Automatique*\n"
            msg += "📲 bectanse-auto.up.railway.app"

        requests.post(
            f"https://api.telegram.org/bot{ECO_BOT_TOKEN}/sendMessage",
            json={"chat_id": ECO_CANAL, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        app.logger.info("Calendrier économique envoyé")
    except Exception as e:
        app.logger.error(f"send_eco_message: {e}")



# ── WEB PUSH ──────────────────────────────────────────────────────────────────

@app.route("/api/push/vapid-public")
def vapid_public():
    return jsonify({"key": VAPID_PUBLIC})

@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if "member_code" not in session:
        return jsonify({"error": "non connecté"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "pas de données"}), 400
    code     = session["member_code"]
    endpoint = data.get("endpoint", "")
    p256dh   = data.get("keys", {}).get("p256dh", "")
    auth_key = data.get("keys", {}).get("auth", "")
    if not endpoint or not p256dh or not auth_key:
        return jsonify({"error": "données incomplètes"}), 400
    try:
        conn = get_conn()
        conn.run(
            """INSERT INTO push_subscriptions (member_code, endpoint, p256dh, auth)
               VALUES (:code, :ep, :p256dh, :auth)
               ON CONFLICT (endpoint) DO UPDATE SET
               member_code=:code, p256dh=:p256dh, auth=:auth""",
            code=code, ep=endpoint, p256dh=p256dh, auth=auth_key
        )
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    data = request.get_json()
    if data and data.get("endpoint"):
        try:
            conn = get_conn()
            conn.run("DELETE FROM push_subscriptions WHERE endpoint=:ep", ep=data["endpoint"])
            conn.close()
        except: pass
    return jsonify({"ok": True})

def send_push_to_all(title, body, url="/canal"):
    """Envoie une Web Push à tous les membres abonnés."""
    try:
        from pywebpush import webpush, WebPushException
        conn = get_conn()
        subs = conn.run("SELECT endpoint, p256dh, auth FROM push_subscriptions")
        conn.close()
        if not subs:
            return
        payload = json.dumps({"title": title, "body": body, "url": url})
        dead = []
        for ep, p256dh, auth_key in subs:
            try:
                webpush(
                    subscription_info={"endpoint": ep, "keys": {"p256dh": p256dh, "auth": auth_key}},
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE,
                    vapid_claims=VAPID_CLAIMS
                )
            except WebPushException as ex:
                if ex.response and ex.response.status_code in (404, 410):
                    dead.append(ep)
            except Exception:
                pass
        if dead:
            conn2 = get_conn()
            for ep in dead:
                try:
                    conn2.run("DELETE FROM push_subscriptions WHERE endpoint=:ep", ep=ep)
                except: pass
            conn2.close()
    except Exception as e:
        app.logger.error(f"send_push_to_all: {e}")

# ── CANAL VIP ─────────────────────────────────────────────────────────────────

CANAL_BOT_TOKEN = "8895323708:AAFNFHv8BXada_wFDnZhh69rKPLKs2oGAco"
CANAL_GROUP_ID  = -1003605441967
CANAL_ADMIN_CODE = "BCT-LERIS"
VAPID_PUBLIC  = "BI5TQpefuRvs_HIPgRzXnBQqcQ5V9puh2hteQmdRp8pQFMEh-XyvgPGpYrO5ioPak9Z7ml6laSl2WnNh96RFrv8"
VAPID_PRIVATE = "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgmeZdDREE6AdkScXLD0GeI65NwMQ3C7kBzmRN49e-XTqhRANCAASOU0KXn7kb7PxyD4Ec15wUKnEOVfabodobXkJnUafKUBTBIfl8r4DxqWKzuYqD2pPWe5pepWkpdlpzYfekRa7_"
VAPID_CLAIMS  = {"sub": "mailto:bectanse@gmail.com"}  # code membre admin qui peut publier depuis webapp

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

        # Photo
        photos = msg.get("photo")
        if photos:
            file_id = photos[-1]["file_id"]
            try:
                r = requests.get(
                    f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getFile",
                    params={"file_id": file_id}, timeout=8
                )
                file_path = r.json()["result"]["file_path"]
                photo_url = f"https://api.telegram.org/file/bot{CANAL_BOT_TOKEN}/{file_path}"
            except:
                photo_url = None

        # Audio / Message vocal
        voice = msg.get("voice")
        audio = msg.get("audio")
        media = voice or audio
        if media:
            file_id = media["file_id"]
            try:
                r = requests.get(
                    f"https://api.telegram.org/bot{CANAL_BOT_TOKEN}/getFile",
                    params={"file_id": file_id}, timeout=8
                )
                file_path = r.json()["result"]["file_path"]
                audio_url = f"https://api.telegram.org/file/bot{CANAL_BOT_TOKEN}/{file_path}"
                if not text_content:
                    text_content = "🎙️ Message vocal" if voice else "🎵 Audio"
            except:
                audio_url = None

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
            threading.Thread(target=send_push_to_all, args=(label, preview), daemon=True).start()
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


@app.route("/api/canal/restaurer/<int:msg_id>", methods=["POST"])
@login_required
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
    return render_template("canal.html", member=member, is_admin=is_admin)


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
        # Scheduler calendrier économique 08:00
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone='Europe/Paris')
        scheduler.add_job(send_eco_message, 'cron', hour=8, minute=0)
        scheduler.add_job(check_and_send_relances, 'cron', hour=9, minute=0)
        scheduler.start()
        app.logger.info("Scheduler calendrier démarré")
    except Exception as e:
        app.logger.error(f"startup: {e}")

threading.Thread(target=_startup, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
