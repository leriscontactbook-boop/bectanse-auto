"""Parcours d'activation interactif des membres Bectanse Académie.

Le parcours guide un membre payé depuis l'ouverture de son compte de trading
jusqu'à l'installation de la WebApp. La vérification du compte broker reste
volontairement humaine : le membre envoie une demande, l'équipe la valide
depuis Telegram ou le centre d'administration, puis la suite se débloque.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from flask import abort, jsonify, redirect, render_template, request, session, url_for


BROKER_URL = "https://puvip.co/la-partners/fr/BectanseAcademie042026"
SUPPORT_URL = "https://t.me/m/PAt88QgeZDhk"

STEP_DEFINITIONS = (
    ("broker", "Créer le compte de trading"),
    ("verification", "Faire vérifier le compte"),
    ("trading", "Ouvrir le compte MT4 ou MT5"),
    ("credentials", "Connecter le compte à Bectanse"),
    ("installation", "Installer l’application"),
    ("notifications", "Activer les notifications"),
)


def ensure_activation_schema(conn):
    """Crée uniquement les tables additives du parcours d'activation."""
    conn.run(
        """
        CREATE TABLE IF NOT EXISTS member_activation_journeys (
            member_code                  TEXT PRIMARY KEY,
            broker_email                 TEXT NOT NULL DEFAULT '',
            broker_reference             TEXT NOT NULL DEFAULT '',
            broker_status                TEXT NOT NULL DEFAULT 'not_started',
            broker_opened_at             TIMESTAMP,
            broker_requested_at          TIMESTAMP,
            broker_reviewed_at           TIMESTAMP,
            broker_review_note           TEXT NOT NULL DEFAULT '',
            trading_platform             TEXT NOT NULL DEFAULT '',
            trading_account_completed_at TIMESTAMP,
            funding_completed_at         TIMESTAMP,
            credentials_completed_at     TIMESTAMP,
            app_installed_at             TIMESTAMP,
            notifications_enabled_at     TIMESTAMP,
            completed_at                 TIMESTAMP,
            created_at                   TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at                   TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.run(
        """
        CREATE TABLE IF NOT EXISTS member_activation_events (
            id          BIGSERIAL PRIMARY KEY,
            member_code TEXT NOT NULL,
            event_name  TEXT NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}',
            created_at  TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.run(
        """CREATE INDEX IF NOT EXISTS member_activation_status_idx
           ON member_activation_journeys (broker_status, updated_at DESC)"""
    )
    conn.run(
        """CREATE INDEX IF NOT EXISTS member_activation_events_member_idx
           ON member_activation_events (member_code, created_at DESC)"""
    )


def _as_json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _record_event(conn, member_code, event_name, metadata=None):
    conn.run(
        """INSERT INTO member_activation_events (member_code,event_name,metadata)
           VALUES (:code,:event,:metadata)""",
        code=member_code,
        event=event_name,
        metadata=json.dumps(metadata or {}, ensure_ascii=False),
    )


def _ensure_row(conn, member_code):
    conn.run(
        """INSERT INTO member_activation_journeys (member_code)
           VALUES (:code) ON CONFLICT (member_code) DO NOTHING""",
        code=member_code,
    )


def _bootstrap_existing_profile(conn, member):
    """Reconnaît les membres déjà configurés sans leur refaire le parcours."""
    params = _as_json(member.get("params"))
    login = str(params.get("mt_login") or "").strip()
    server = str(params.get("serveur") or params.get("mt_server") or "").strip()
    password = str(params.get("mt_password") or "").strip()
    if not (login and server and password):
        return
    platform = str(params.get("plateforme") or "MT5").upper()
    if platform not in {"MT4", "MT5"}:
        platform = "MT5"
    conn.run(
        """UPDATE member_activation_journeys SET
             broker_status=CASE WHEN broker_status='not_started' THEN 'approved' ELSE broker_status END,
             broker_opened_at=COALESCE(broker_opened_at,NOW()),
             broker_reviewed_at=COALESCE(broker_reviewed_at,NOW()),
             trading_platform=CASE WHEN trading_platform='' THEN :platform ELSE trading_platform END,
             trading_account_completed_at=COALESCE(trading_account_completed_at,NOW()),
             funding_completed_at=COALESCE(funding_completed_at,NOW()),
             credentials_completed_at=COALESCE(credentials_completed_at,NOW()),
             updated_at=NOW()
           WHERE member_code=:code""",
        code=member["code"],
        platform=platform,
    )


def _sync_push_state(conn, member_code):
    """Une souscription Push réelle vaut preuve d'activation des notifications."""
    try:
        rows = conn.run(
            "SELECT COUNT(*) FROM push_subscriptions WHERE member_code=:code",
            code=member_code,
        )
        if rows and int(rows[0][0] or 0) > 0:
            conn.run(
                """UPDATE member_activation_journeys SET
                     app_installed_at=COALESCE(app_installed_at,NOW()),
                     notifications_enabled_at=COALESCE(notifications_enabled_at,NOW()),
                     updated_at=NOW() WHERE member_code=:code""",
                code=member_code,
            )
    except Exception:
        # La table Push peut être indisponible pendant une migration. Le parcours
        # reste consultable et l'utilisateur pourra réessayer depuis l'interface.
        pass


def _row_dict(conn, member_code):
    rows = conn.run(
        """SELECT member_code,broker_email,broker_reference,broker_status,
                  broker_opened_at,broker_requested_at,broker_reviewed_at,
                  broker_review_note,trading_platform,
                  trading_account_completed_at,funding_completed_at,
                  credentials_completed_at,app_installed_at,
                  notifications_enabled_at,completed_at,created_at,updated_at
           FROM member_activation_journeys WHERE member_code=:code""",
        code=member_code,
    )
    if not rows:
        return {}
    columns = (
        "member_code", "broker_email", "broker_reference", "broker_status",
        "broker_opened_at", "broker_requested_at", "broker_reviewed_at",
        "broker_review_note", "trading_platform",
        "trading_account_completed_at", "funding_completed_at",
        "credentials_completed_at", "app_installed_at",
        "notifications_enabled_at", "completed_at", "created_at", "updated_at",
    )
    return dict(zip(columns, rows[0]))


def build_activation_state(conn, member):
    """Construit l'état public, sans mot de passe ni information sensible."""
    member_code = member["code"]
    _ensure_row(conn, member_code)
    _bootstrap_existing_profile(conn, member)
    _sync_push_state(conn, member_code)
    row = _row_dict(conn, member_code)

    complete = {
        "broker": bool(row.get("broker_opened_at")),
        "verification": row.get("broker_status") == "approved",
        "trading": bool(row.get("trading_account_completed_at") and row.get("funding_completed_at")),
        "credentials": bool(row.get("credentials_completed_at")),
        "installation": bool(row.get("app_installed_at")),
        "notifications": bool(row.get("notifications_enabled_at")),
    }
    ordered_ids = [step_id for step_id, _ in STEP_DEFINITIONS]
    completed_count = sum(1 for step_id in ordered_ids if complete[step_id])
    current = next((step_id for step_id in ordered_ids if not complete[step_id]), "complete")

    can_open = {
        "broker": True,
        "verification": complete["broker"],
        "trading": complete["verification"],
        "credentials": complete["trading"],
        "installation": complete["credentials"],
        "notifications": complete["installation"],
    }
    if completed_count == len(ordered_ids) and not row.get("completed_at"):
        conn.run(
            """UPDATE member_activation_journeys SET completed_at=NOW(),updated_at=NOW()
               WHERE member_code=:code AND completed_at IS NULL""",
            code=member_code,
        )
        _record_event(conn, member_code, "journey_completed")
        row["completed_at"] = datetime.now()

    steps = []
    for index, (step_id, label) in enumerate(STEP_DEFINITIONS, start=1):
        steps.append({
            "id": step_id,
            "number": index,
            "label": label,
            "completed": complete[step_id],
            "locked": not can_open[step_id],
            "current": step_id == current,
        })

    return {
        "member_code": member_code,
        "first_name": str(member.get("nom") or "Membre").split()[0],
        "email": str(member.get("email") or ""),
        "broker_email": row.get("broker_email") or str(member.get("email") or ""),
        "broker_reference": row.get("broker_reference") or "",
        "broker_status": row.get("broker_status") or "not_started",
        "broker_review_note": row.get("broker_review_note") or "",
        "platform": row.get("trading_platform") or "MT5",
        "steps": steps,
        "current_step": current,
        "completed_count": completed_count,
        "progress": round(completed_count / len(ordered_ids) * 100),
        "finished": completed_count == len(ordered_ids),
        "completed_at": row.get("completed_at"),
    }


def _clean_reference(value):
    return re.sub(r"[^A-Za-z0-9@._+\- ]", "", str(value or "").strip())[:100]


def _telegram_safe(value):
    return re.sub(r"[*_`\[\]()]", "", str(value or "")).strip()


def register_activation_journey(
    app,
    get_conn,
    get_member,
    login_required,
    academy_access_required,
    admin_required,
    action_token,
    action_payload,
    save_profile,
    send_telegram,
    notify_member,
):
    """Branche le parcours sur l'application Flask existante."""

    @app.route("/preview-demarrage")
    def activation_preview():
        """Aperçu local sans accès aux données réelles d'un membre."""
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            abort(404)
        preview_member = {
            "code": "BCT-PREVIEW",
            "nom": "Leris",
            "email": "membre@bectanse-academie.com",
            "access_level": "member",
        }
        preview_ids = [step_id for step_id, _ in STEP_DEFINITIONS]
        requested_stage = str(request.args.get("stage") or "broker").strip().lower()
        if requested_stage not in preview_ids and requested_stage != "complete":
            requested_stage = "broker"
        current_index = len(preview_ids) if requested_stage == "complete" else preview_ids.index(requested_stage)
        preview_steps = []
        for index, (step_id, label) in enumerate(STEP_DEFINITIONS, start=1):
            completed = index - 1 < current_index
            preview_steps.append({
                "id": step_id,
                "number": index,
                "label": label,
                "completed": completed,
                "locked": index - 1 > current_index,
                "current": index - 1 == current_index,
            })
        preview_state = {
            "member_code": preview_member["code"],
            "first_name": "Leris",
            "email": preview_member["email"],
            "broker_email": preview_member["email"],
            "broker_reference": "",
            "broker_status": "approved" if current_index >= 2 else "not_started",
            "broker_review_note": "",
            "platform": "MT5",
            "steps": preview_steps,
            "current_step": requested_stage,
            "completed_count": current_index,
            "progress": round(current_index / len(preview_ids) * 100),
            "finished": requested_stage == "complete",
            "completed_at": datetime.now() if requested_stage == "complete" else None,
        }
        return render_template(
            "activation_journey.html",
            member=preview_member,
            state=preview_state,
            broker_url=BROKER_URL,
            support_url=SUPPORT_URL,
            demo_mode=False,
            preview_mode=True,
        )

    @app.route("/demarrage")
    @login_required
    @academy_access_required
    def activation_home():
        member = get_member(session["member_code"])
        if not member:
            session.clear()
            return redirect(url_for("login"))
        conn = get_conn()
        try:
            state = build_activation_state(conn, member)
        finally:
            conn.close()
        return render_template(
            "activation_journey.html",
            member=member,
            state=state,
            broker_url=BROKER_URL,
            support_url=SUPPORT_URL,
            demo_mode=False,
        )

    @app.route("/api/demarrage/action", methods=["POST"])
    @login_required
    @academy_access_required
    def activation_action():
        member_code = session["member_code"]
        member = get_member(member_code)
        data = request.get_json(silent=True) or {}
        action = str(data.get("action") or "").strip()
        conn = get_conn()
        telegram_payload = None
        try:
            state = build_activation_state(conn, member)
            complete = {step["id"]: step["completed"] for step in state["steps"]}

            if action == "broker_opened":
                conn.run(
                    """UPDATE member_activation_journeys SET
                       broker_opened_at=COALESCE(broker_opened_at,NOW()),updated_at=NOW()
                       WHERE member_code=:code""",
                    code=member_code,
                )
                _record_event(conn, member_code, "broker_opened")

            elif action == "request_verification":
                if not complete["broker"]:
                    return jsonify({"ok": False, "error": "Confirme d’abord la création du compte."}), 409
                email = str(data.get("broker_email") or "").strip().lower()
                reference = _clean_reference(data.get("broker_reference"))
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    return jsonify({"ok": False, "error": "Entre l’adresse e-mail utilisée lors de l’inscription."}), 400
                current = _row_dict(conn, member_code)
                if current.get("broker_status") == "approved":
                    return jsonify({"ok": True, "state": build_activation_state(conn, member), "already_approved": True})
                duplicate = False
                requested_at = current.get("broker_requested_at")
                if current.get("broker_status") == "pending" and requested_at:
                    duplicate = (datetime.now() - requested_at).total_seconds() < 600
                conn.run(
                    """UPDATE member_activation_journeys SET broker_email=:email,
                       broker_reference=:reference,broker_status='pending',
                       broker_requested_at=CASE WHEN :duplicate THEN broker_requested_at ELSE NOW() END,
                       broker_review_note='',updated_at=NOW() WHERE member_code=:code""",
                    code=member_code,
                    email=email,
                    reference=reference,
                    duplicate=duplicate,
                )
                if not duplicate:
                    _record_event(conn, member_code, "broker_verification_requested")
                    approve_token = action_token(
                        "activation_review",
                        {"member_code": member_code, "decision": "approved"},
                        lifetime_seconds=7 * 86400,
                    )
                    correction_token = action_token(
                        "activation_review",
                        {"member_code": member_code, "decision": "correction"},
                        lifetime_seconds=7 * 86400,
                    )
                    base_url = request.url_root.rstrip("/")
                    telegram_payload = {
                        "text": (
                            "🔎 *NOUVELLE VÉRIFICATION À EFFECTUER*\n\n"
                            f"Membre : {_telegram_safe(member.get('nom'))}\n"
                            f"Code : `{_telegram_safe(member_code)}`\n"
                            f"E-mail membre : {_telegram_safe(member.get('email')) or 'non renseigné'}\n"
                            f"E-mail du compte : {_telegram_safe(email)}\n"
                            f"Référence : {_telegram_safe(reference) or 'non renseignée'}\n\n"
                            "Vérifie manuellement le compte puis choisis une action. "
                            "Aucun mot de passe MT n’est transmis dans Telegram."
                        ),
                        "reply_markup": {
                            "inline_keyboard": [
                                [{"text": "✅ Valider et débloquer", "url": f"{base_url}/activation/admin/review?token={approve_token}"}],
                                [{"text": "✏️ Demander une correction", "url": f"{base_url}/activation/admin/review?token={correction_token}"}],
                            ]
                        },
                    }

            elif action == "trading_ready":
                if not complete["verification"]:
                    return jsonify({"ok": False, "error": "La vérification par l’équipe est encore nécessaire."}), 409
                platform = str(data.get("platform") or "").upper()
                if platform not in {"MT4", "MT5"}:
                    return jsonify({"ok": False, "error": "Choisis MT4 ou MT5."}), 400
                if data.get("funding_confirmed") is not True:
                    return jsonify({"ok": False, "error": "Confirme que la mise en place du compte est terminée."}), 400
                conn.run(
                    """UPDATE member_activation_journeys SET trading_platform=:platform,
                       trading_account_completed_at=COALESCE(trading_account_completed_at,NOW()),
                       funding_completed_at=COALESCE(funding_completed_at,NOW()),updated_at=NOW()
                       WHERE member_code=:code""",
                    code=member_code,
                    platform=platform,
                )
                _record_event(conn, member_code, "trading_account_ready", {"platform": platform})

            elif action == "save_credentials":
                if not complete["trading"]:
                    return jsonify({"ok": False, "error": "Finalise d’abord le compte MT4 ou MT5."}), 409
                platform = str(data.get("platform") or "").upper()
                login = str(data.get("mt_login") or "").strip()
                server = str(data.get("mt_server") or "").strip()
                password = str(data.get("mt_password") or "").strip()
                if platform not in {"MT4", "MT5"}:
                    return jsonify({"ok": False, "error": "Plateforme invalide."}), 400
                if not re.fullmatch(r"[A-Za-z0-9._-]{4,80}", login):
                    return jsonify({"ok": False, "error": "Vérifie le numéro de compte MT."}), 400
                if len(server) < 3 or len(server) > 120:
                    return jsonify({"ok": False, "error": "Vérifie le nom du serveur MT."}), 400
                if len(password) < 4 or len(password) > 200:
                    return jsonify({"ok": False, "error": "Vérifie le mot de passe MT."}), 400
                save_profile(conn, member_code, {}, {
                    "plateforme": platform,
                    "mt_login": login,
                    "serveur": server,
                    "mt_password": password,
                })
                conn.run(
                    """UPDATE member_activation_journeys SET trading_platform=:platform,
                       credentials_completed_at=NOW(),updated_at=NOW()
                       WHERE member_code=:code""",
                    code=member_code,
                    platform=platform,
                )
                _record_event(conn, member_code, "credentials_saved", {"platform": platform})

            elif action == "app_installed":
                if not complete["credentials"]:
                    return jsonify({"ok": False, "error": "Connecte d’abord ton compte MT."}), 409
                conn.run(
                    """UPDATE member_activation_journeys SET app_installed_at=NOW(),updated_at=NOW()
                       WHERE member_code=:code""",
                    code=member_code,
                )
                _record_event(conn, member_code, "app_installed")

            elif action == "notifications_enabled":
                if not complete["installation"]:
                    return jsonify({"ok": False, "error": "Confirme d’abord l’installation de l’application."}), 409
                subscriptions = conn.run(
                    "SELECT COUNT(*) FROM push_subscriptions WHERE member_code=:code",
                    code=member_code,
                )
                if not subscriptions or int(subscriptions[0][0] or 0) < 1:
                    return jsonify({"ok": False, "error": "Active les notifications sur cet appareil avant de continuer."}), 409
                conn.run(
                    """UPDATE member_activation_journeys SET notifications_enabled_at=NOW(),updated_at=NOW()
                       WHERE member_code=:code""",
                    code=member_code,
                )
                _record_event(conn, member_code, "notifications_enabled")
            else:
                return jsonify({"ok": False, "error": "Action inconnue."}), 400

            updated = build_activation_state(conn, get_member(member_code) or member)
        finally:
            conn.close()

        if telegram_payload:
            delivered = bool(send_telegram(
                telegram_payload["text"],
                reply_markup=telegram_payload["reply_markup"],
            ))
            if not delivered:
                return jsonify({
                    "ok": False,
                    "error": "La demande est enregistrée, mais Telegram n’a pas répondu. L’équipe la voit aussi dans le centre d’administration.",
                    "state": updated,
                    "saved": True,
                }), 502
        return jsonify({"ok": True, "state": updated})

    def review_member(member_code, decision, reviewer="admin"):
        if decision not in {"approved", "correction"}:
            return None, "Décision invalide"
        member = get_member(member_code)
        if not member:
            return None, "Membre introuvable"
        conn = get_conn()
        try:
            _ensure_row(conn, member_code)
            current = _row_dict(conn, member_code)
            if not current.get("broker_opened_at"):
                return None, "Aucune demande de vérification active"
            if current.get("broker_status") == "approved":
                if decision == "approved":
                    return build_activation_state(conn, member), ""
                return None, "Ce compte a déjà été validé"
            if current.get("broker_status") not in {"pending", "correction"}:
                return None, "Aucune demande de vérification en attente"
            note = "Compte validé manuellement par l’équipe."
            if decision == "correction":
                note = "Vérifie l’e-mail ou la référence du compte puis renvoie la demande."
            conn.run(
                """UPDATE member_activation_journeys SET broker_status=:decision,
                   broker_reviewed_at=NOW(),broker_review_note=:note,updated_at=NOW()
                   WHERE member_code=:code""",
                code=member_code,
                decision=decision,
                note=note,
            )
            _record_event(conn, member_code, f"broker_{decision}", {"reviewer": reviewer})
            state = build_activation_state(conn, member)
        finally:
            conn.close()
        if decision == "approved":
            notify_member(
                member_code,
                "Compte vérifié",
                "Ton compte a été validé. La création MT4 ou MT5 est maintenant débloquée.",
                "/demarrage#trading",
            )
        else:
            notify_member(
                member_code,
                "Vérification à reprendre",
                "Une information doit être corrigée avant la validation de ton compte.",
                "/demarrage#verification",
            )
        return state, ""

    @app.route("/activation/admin/review")
    def activation_admin_review():
        token = request.args.get("token", "")
        payload = action_payload(token, "activation_review")
        if not payload:
            return render_template("activation_review_result.html", ok=False, error="Ce lien est invalide ou expiré."), 403
        member_code = str(payload.get("member_code") or "").upper()
        decision = str(payload.get("decision") or "")
        state, error = review_member(member_code, decision, reviewer="telegram")
        if error:
            return render_template("activation_review_result.html", ok=False, error=error), 400
        return render_template(
            "activation_review_result.html",
            ok=True,
            decision=decision,
            member_code=member_code,
            state=state,
        )

    @app.route("/admin/activations")
    @admin_required
    def admin_activations():
        return render_template("admin_activations.html")

    @app.route("/admin/api/activations")
    @admin_required
    def admin_activation_list():
        conn = get_conn()
        try:
            rows = conn.run(
                """SELECT j.member_code,m.nom,m.email,m.telephone,m.telegram,
                          j.broker_email,j.broker_reference,j.broker_status,
                          j.broker_requested_at,j.trading_platform,
                          j.credentials_completed_at,j.app_installed_at,
                          j.notifications_enabled_at,j.completed_at,j.updated_at
                   FROM member_activation_journeys j
                   JOIN members m ON m.code=j.member_code
                   WHERE COALESCE(m.access_level,'member') NOT IN ('explorer','demo')
                   ORDER BY CASE j.broker_status WHEN 'pending' THEN 0 WHEN 'correction' THEN 1 ELSE 2 END,
                            j.updated_at DESC LIMIT 500"""
            )
        finally:
            conn.close()
        keys = (
            "member_code", "name", "email", "phone", "telegram", "broker_email",
            "broker_reference", "status", "requested_at", "platform",
            "credentials_at", "installed_at", "notifications_at", "completed_at", "updated_at",
        )
        items = []
        for row in rows:
            item = dict(zip(keys, row))
            for key in ("requested_at", "credentials_at", "installed_at", "notifications_at", "completed_at", "updated_at"):
                value = item.get(key)
                item[key] = value.isoformat() if value and hasattr(value, "isoformat") else ""
            items.append(item)
        return jsonify({
            "ok": True,
            "items": items,
            "summary": {
                "pending": sum(1 for item in items if item["status"] == "pending"),
                "correction": sum(1 for item in items if item["status"] == "correction"),
                "approved": sum(1 for item in items if item["status"] == "approved"),
                "completed": sum(1 for item in items if item["completed_at"]),
            },
        })

    @app.route("/admin/api/activation/review", methods=["POST"])
    @admin_required
    def admin_activation_review_api():
        data = request.get_json(silent=True) or {}
        member_code = str(data.get("member_code") or "").strip().upper()
        decision = str(data.get("decision") or "").strip()
        if not re.fullmatch(r"BCT-[A-Z0-9-]{4,40}", member_code):
            return jsonify({"ok": False, "error": "Code membre invalide"}), 400
        state, error = review_member(member_code, decision)
        if error:
            return jsonify({"ok": False, "error": error}), 400
        return jsonify({"ok": True, "state": state})
