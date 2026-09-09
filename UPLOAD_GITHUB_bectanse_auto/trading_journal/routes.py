"""Flask routes for the user-facing journal and the private worker protocol."""

from __future__ import annotations

import os
import re
import secrets
import string
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import jsonify, redirect, render_template, request, session

from .service import JournalService
from .security import verify_worker_request
from .config import worker_secret
from .coach import answer_question as coach_answer_question, review as coach_review
from .billing import create_checkout, create_portal
from .apple_iap import process_notification, storekit_context, synchronize_transaction


_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)


def _rate_limited(name: str, limit: int, window_seconds: int):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            identity = str(session.get("member_code") or request.remote_addr or "anonymous")
            key = f"{name}:{identity}"
            now = time.monotonic()
            with _RATE_LOCK:
                bucket = _RATE_BUCKETS[key]
                while bucket and bucket[0] <= now - window_seconds:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return jsonify({
                        "ok": False,
                        "error": "Trop de demandes. Patientez quelques instants avant de réessayer.",
                    }), 429
                bucket.append(now)
            return function(*args, **kwargs)
        return wrapped
    return decorator


def _api_error(error: Exception):
    if isinstance(error, PermissionError):
        return jsonify({"ok": False, "error": str(error), "upgrade_required": True}), 403
    if isinstance(error, LookupError):
        return jsonify({"ok": False, "error": str(error)}), 404
    if isinstance(error, ValueError):
        return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": False, "error": "Le service est momentanément indisponible."}), 500


def _checkout_failure_code(error: Exception) -> str:
    """Return a safe public status for the browser checkout redirect."""
    if isinstance(error, PermissionError):
        return "already-active"
    if isinstance(error, ValueError):
        return "account-required"
    return "unavailable"


def register_trading_journal(app, get_conn, get_member, login_required, admin_required=None):
    service = JournalService(get_conn, get_member, app.logger)

    @app.route("/bectanse-track/legal/<slug>")
    def bectanse_track_legal(slug):
        if slug not in {"conditions", "confidentialite"}:
            return "Page introuvable", 404
        return render_template("bectanse_track_legal.html", slug=slug)

    def mobile_session_payload(user_id: str, *, recovery_code: str | None = None) -> dict:
        member = get_member(user_id) or {}
        entitlements = service.entitlements(user_id)
        profile = service.profile(user_id)
        accounts = (
            service.list_accounts(user_id, entitlements["max_accounts"])
            if entitlements["allowed"] else []
        )
        full_name = str(member.get("nom") or "Trader").strip()[:120] or "Trader"
        payload = {
            "ok": True,
            "authenticated": True,
            "member": {
                "name": full_name,
                "first_name": full_name.split(" ", 1)[0],
                "email": str(member.get("email") or "")[:254],
                "access_level": str(member.get("access_level") or "member")[:40],
            },
            "profile": profile,
            "entitlements": entitlements,
            "accounts": accounts,
        }
        if recovery_code:
            payload["recovery_code"] = recovery_code
        return payload

    def install_mobile_session(member: dict) -> None:
        session.clear()
        session.permanent = True
        session["member_code"] = member["code"]
        try:
            conn = get_conn()
            conn.run("UPDATE members SET last_login=NOW() WHERE code=:code", code=member["code"])
            conn.close()
        except Exception as error:
            app.logger.warning("Mobile last-login update failed: %s", error)

    @app.route("/api/mobile/auth/code", methods=["POST"])
    @_rate_limited("mobile-code-login", 10, 15 * 60)
    def mobile_code_login():
        payload = request.get_json(silent=True) or {}
        code = re.sub(r"\s+", "", str(payload.get("code") or "")).upper()[:64]
        member = get_member(code) if code else None
        if not member or code == "BCT-DEMO2026":
            return jsonify({"ok": False, "error": "Code Bectanse invalide."}), 401
        try:
            install_mobile_session(member)
            response = jsonify(mobile_session_payload(member["code"]))
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as error:
            app.logger.error("Mobile code login %s: %s", code, error)
            session.clear()
            return _api_error(error)

    @app.route("/api/mobile/auth/trial", methods=["POST"])
    @_rate_limited("mobile-trial", 3, 24 * 60 * 60)
    def mobile_trial_start():
        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").strip().split())[:120]
        email = str(payload.get("email") or "").strip().lower()[:254]
        if len(name) < 2:
            return jsonify({"ok": False, "error": "Indiquez votre nom."}), 400
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return jsonify({"ok": False, "error": "Adresse e-mail invalide."}), 400
        try:
            conn = get_conn()
        except Exception as error:
            app.logger.error("Mobile trial database unavailable: %s", error)
            return _api_error(error)
        try:
            conn.run("BEGIN")
            # Serialize registrations for the same normalized address so two
            # simultaneous requests cannot create duplicate standalone users.
            conn.run("SELECT pg_advisory_xact_lock(hashtext(LOWER(:email)))", email=email)
            if conn.run("SELECT 1 FROM members WHERE LOWER(email)=LOWER(:email) LIMIT 1", email=email):
                conn.run("ROLLBACK")
                return jsonify({
                    "ok": False,
                    "error": "Un accès existe déjà pour cette adresse. Connectez-vous avec votre code Bectanse.",
                }), 409
            code = ""
            alphabet = string.ascii_uppercase + string.digits
            for _ in range(20):
                candidate = "BTT-" + "".join(secrets.choice(alphabet) for _ in range(10))
                if not conn.run("SELECT 1 FROM members WHERE code=:code", code=candidate):
                    code = candidate
                    break
            if not code:
                raise RuntimeError("mobile_trial_code_generation_failed")
            conn.run("""INSERT INTO members
                (code,nom,capital,actif,copy_actif,date_souscription,date_fin,email,
                 params,historique,access_level,email_verified_at)
                VALUES (:code,:name,'—',FALSE,FALSE,NOW(),NOW(),:email,'{}','[]','journal',NULL)""",
                code=code, name=name, email=email)
            conn.run("""INSERT INTO trading_subscriptions
                (user_id,product,plan,subscription_status,current_period_end,billing_provider)
                VALUES (:code,'JOURNAL','JOURNAL_ELITE','inactive',NULL,'apple')""",
                code=code)
            conn.run("""INSERT INTO trading_audit_logs (user_id,action,metadata)
                VALUES (:code,'APPLE_TRIAL_ACCOUNT_CREATED',jsonb_build_object(
                    'access_granted',FALSE,'payment_required',TRUE))""", code=code)
            conn.run("COMMIT")
        except Exception as error:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            app.logger.error("Mobile trial creation failed: %s", error)
            return _api_error(error)
        finally:
            conn.close()
        try:
            member = get_member(code)
            if not member:
                raise LookupError("Le compte Bectanse Track n’a pas pu être ouvert.")
            install_mobile_session(member)
            response = jsonify(mobile_session_payload(code, recovery_code=code))
            response.status_code = 201
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as error:
            app.logger.error("Mobile trial session failed %s: %s", code, error)
            session.clear()
            return _api_error(error)

    @app.route("/api/mobile/session", methods=["GET"])
    def mobile_session():
        user_id = str(session.get("member_code") or "")
        if not user_id or not get_member(user_id):
            session.clear()
            return jsonify({"ok": True, "authenticated": False}), 401
        try:
            response = jsonify(mobile_session_payload(user_id))
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except Exception as error:
            app.logger.error("Mobile session bootstrap %s: %s", user_id, error)
            return _api_error(error)

    @app.route("/api/mobile/logout", methods=["POST"])
    def mobile_logout():
        session.clear()
        response = jsonify({"ok": True, "authenticated": False})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/mobile/storekit/context", methods=["GET"])
    def mobile_storekit_context():
        user_id = str(session.get("member_code") or "")
        if not user_id or not get_member(user_id):
            session.clear()
            return jsonify({"ok": False, "error": "Votre session a expiré."}), 401
        try:
            response = jsonify({"ok": True, **storekit_context(get_conn, user_id)})
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except Exception as error:
            app.logger.error("StoreKit context %s: %s", user_id, error)
            return _api_error(error)

    @app.route("/api/mobile/storekit/sync", methods=["POST"])
    @_rate_limited("mobile-storekit-sync", 30, 15 * 60)
    def mobile_storekit_sync():
        user_id = str(session.get("member_code") or "")
        if not user_id or not get_member(user_id):
            session.clear()
            return jsonify({"ok": False, "error": "Votre session a expiré."}), 401
        payload = request.get_json(silent=True) or {}
        try:
            result = synchronize_transaction(get_conn, user_id, payload.get("signed_transaction") or "")
            response = jsonify({"ok": True, **result, "session": mobile_session_payload(user_id)})
            response.headers["Cache-Control"] = "private, no-store"
            return response
        except Exception as error:
            if not isinstance(error, (ValueError, PermissionError)):
                app.logger.error("StoreKit sync %s: %s", user_id, error)
            return _api_error(error)

    @app.route("/api/mobile/storekit/notifications", methods=["POST"])
    @_rate_limited("apple-storekit-notification", 300, 60 * 60)
    def mobile_storekit_notifications():
        payload = request.get_json(silent=True) or {}
        try:
            result = process_notification(get_conn, payload.get("signedPayload") or "")
            return jsonify({"ok": True, **result})
        except ValueError as error:
            app.logger.warning("Invalid App Store notification: %s", error)
            return jsonify({"ok": False, "error": "Notification Apple invalide."}), 400
        except LookupError as error:
            app.logger.warning("Unmatched App Store notification: %s", error)
            return jsonify({"ok": False, "error": "Abonnement Apple non associé."}), 404
        except Exception as error:
            app.logger.error("App Store notification failed: %s", error)
            return jsonify({"ok": False, "error": "Notification Apple non traitée."}), 500

    @app.route("/journal")
    @login_required
    def automatic_trading_journal():
        user_id = session["member_code"]
        member = get_member(user_id)
        overview = None
        try:
            profile = service.profile(user_id)
            entitlements = service.entitlements(user_id)
            accounts = service.list_accounts(user_id, entitlements["max_accounts"]) if entitlements["allowed"] else []
        except Exception as error:
            app.logger.error("Journal bootstrap %s: %s", user_id, error)
            accounts, profile = [], {"timezone": "Europe/Paris"}
            entitlements = {"allowed": False, "plan": "NONE", "max_accounts": 0,
                            "advanced_analytics": False, "export": False, "features": {}}
        if accounts and entitlements["allowed"]:
            try:
                currencies = {str(account.get("currency") or "") for account in accounts}
                currencies.discard("")
                aggregate_accounts = len(accounts) > 1 and len(currencies) <= 1
                scope = "all" if aggregate_accounts else str(accounts[0]["id"])
                month = datetime.now(ZoneInfo(profile["timezone"])).strftime("%Y-%m")
                overview = service.overview(user_id, scope, month, profile["timezone"])
            except Exception as error:
                app.logger.error("Journal overview bootstrap %s: %s", user_id, error)
        else:
            aggregate_accounts = False
        return render_template(
            "trading_journal.html",
            member=member,
            accounts=accounts,
            profile=profile,
            entitlements=entitlements,
            overview=overview,
            aggregate_accounts=aggregate_accounts,
        )

    @app.route("/api/trading/accounts", methods=["GET"])
    @app.route("/trading/accounts", methods=["GET"])
    @login_required
    def trading_accounts_list():
        try:
            entitlements = service.require_access(session["member_code"])
            return jsonify({"ok": True, "accounts": service.list_accounts(
                                session["member_code"], entitlements["max_accounts"]),
                            "entitlements": entitlements})
        except Exception as error:
            app.logger.error("Liste comptes trading %s: %s", session["member_code"], error)
            return _api_error(error)

    @app.route("/api/trading/brokers", methods=["GET"])
    @login_required
    def trading_brokers_list():
        try:
            return jsonify({"ok": True, "brokers": service.broker_catalog()})
        except Exception as error:
            app.logger.error("Catalogue brokers MT5: %s", error)
            return _api_error(error)

    @app.route("/journal/checkout/<plan>", methods=["GET"])
    @login_required
    def trading_journal_checkout(plan):
        try:
            url = create_checkout(get_conn, get_member(session["member_code"]) or {},
                                  session["member_code"], plan, request.url_root)
            return redirect(url, code=303)
        except Exception as error:
            app.logger.error("Journal Checkout %s: %s", session["member_code"], error)
            return redirect(f"/journal?checkout={_checkout_failure_code(error)}", code=303)

    @app.route("/api/trading/billing/portal", methods=["POST"])
    @login_required
    @_rate_limited("trading-billing-portal", 8, 15 * 60)
    def trading_journal_billing_portal():
        try:
            entitlements = service.entitlements(session["member_code"])
            if entitlements.get("source") != "JOURNAL_SUBSCRIPTION":
                raise PermissionError("Aucun abonnement Journal autonome n’est à gérer sur ce compte.")
            url = create_portal(get_conn, session["member_code"], request.url_root)
            return jsonify({"ok": True, "url": url})
        except Exception as error:
            if not isinstance(error, (LookupError, PermissionError)):
                app.logger.error("Journal Billing Portal %s: %s", session["member_code"], error)
            return _api_error(error)

    @app.route("/api/trading/accounts/connect", methods=["POST"])
    @app.route("/trading/accounts/connect", methods=["POST"])
    @login_required
    @_rate_limited("trading-connect", 5, 15 * 60)
    def trading_account_connect():
        try:
            account = service.create_account(session["member_code"], request.get_json(silent=True) or {})
            response = jsonify({"ok": True, "account": account, "message": "Connexion sécurisée en cours…"})
            response.status_code = 202
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as error:
            if not isinstance(error, (ValueError, PermissionError)):
                app.logger.error("Connexion compte trading %s: %s", session["member_code"], error)
            return _api_error(error)

    @app.route("/api/trading/accounts/<int:account_id>", methods=["GET"])
    @app.route("/trading/accounts/<int:account_id>", methods=["GET"])
    @login_required
    def trading_account_detail(account_id):
        try:
            entitlements = service.require_access(session["member_code"])
            account = service.get_account(
                session["member_code"], account_id, entitlements["max_accounts"])
            if not account:
                raise LookupError("Compte de trading introuvable.")
            return jsonify({"ok": True, "account": account})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/accounts/<int:account_id>", methods=["DELETE"])
    @app.route("/trading/accounts/<int:account_id>", methods=["DELETE"])
    @login_required
    @_rate_limited("trading-delete", 10, 60 * 60)
    def trading_account_delete(account_id):
        payload = request.get_json(silent=True) or {}
        delete_data = str(payload.get("mode") or "disconnect") == "delete"
        try:
            if not service.disconnect_account(session["member_code"], account_id, delete_data):
                raise LookupError("Compte de trading introuvable.")
            return jsonify({"ok": True, "deleted": delete_data})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/accounts/<int:account_id>/sync", methods=["POST"])
    @app.route("/trading/accounts/<int:account_id>/sync", methods=["POST"])
    @login_required
    @_rate_limited("trading-sync", 10, 10 * 60)
    def trading_account_sync(account_id):
        try:
            result = service.request_sync(session["member_code"], account_id)
            return jsonify({"ok": True, **result}), 202 if result["queued"] else 429
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/profile", methods=["PATCH"])
    @login_required
    def trading_profile_update():
        try:
            payload = request.get_json(silent=True) or {}
            return jsonify({"ok": True, **service.update_timezone(
                session["member_code"], str(payload.get("timezone") or "")
            )})
        except Exception as error:
            return _api_error(error)

    def query_context():
        service.require_access(session["member_code"])
        scope = str(request.args.get("account_id") or "all")
        timezone_name = str(request.args.get("timezone") or service.profile(session["member_code"])["timezone"])
        return scope, timezone_name

    def live_json(payload):
        response = jsonify(payload)
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        return response

    @app.route("/api/trading/calendar", methods=["GET"])
    @app.route("/trading/calendar", methods=["GET"])
    @login_required
    def trading_calendar():
        try:
            scope, timezone_name = query_context()
            month = str(request.args.get("month") or time.strftime("%Y-%m"))
            return live_json({"ok": True, **service.calendar(session["member_code"], scope, month, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/days/<date_value>", methods=["GET"])
    @app.route("/trading/days/<date_value>", methods=["GET"])
    @login_required
    def trading_day(date_value):
        try:
            scope, timezone_name = query_context()
            return live_json({"ok": True, **service.day(session["member_code"], scope, date_value, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/stats", methods=["GET"])
    @app.route("/trading/stats", methods=["GET"])
    @login_required
    def trading_stats():
        try:
            scope, timezone_name = query_context()
            return jsonify({"ok": True, **service.stats(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/overview", methods=["GET"])
    @login_required
    def trading_overview():
        try:
            scope, timezone_name = query_context()
            month = str(request.args.get("month") or time.strftime("%Y-%m"))
            response = jsonify({
                "ok": True,
                **service.overview(session["member_code"], scope, month, timezone_name),
            })
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            return response
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/equity", methods=["GET"])
    @app.route("/trading/equity", methods=["GET"])
    @login_required
    def trading_equity():
        try:
            scope, timezone_name = query_context()
            return jsonify({"ok": True, **service.equity_curve(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/analytics", methods=["GET"])
    @app.route("/trading/analytics", methods=["GET"])
    @login_required
    def trading_analytics():
        try:
            entitlements = service.entitlements(session["member_code"])
            if not entitlements["advanced_analytics"]:
                raise PermissionError("Les analytics avancés sont disponibles avec la formule ELITE.")
            scope, timezone_name = query_context()
            return jsonify({"ok": True, **service.analytics(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/trades", methods=["GET"])
    @app.route("/trading/trades", methods=["GET"])
    @login_required
    def trading_trades():
        try:
            scope, timezone_name = query_context()
            return live_json({"ok": True, **service.trades(
                session["member_code"], scope, timezone_name, request.args.get("limit", 100)
            )})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/accounts/<int:account_id>/health", methods=["GET"])
    @app.route("/trading/accounts/<int:account_id>/health", methods=["GET"])
    @login_required
    def trading_account_health(account_id):
        try:
            service.require_access(session["member_code"])
            return jsonify({"ok": True, **service.verify_account_integrity(session["member_code"], account_id)})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/coach/<review_type>", methods=["GET"])
    @app.route("/trading/coach/<review_type>", methods=["GET"])
    @login_required
    def trading_coach(review_type):
        try:
            entitlements = service.require_access(session["member_code"])
            feature = f"coach.{review_type.lower()}"
            if not entitlements.get("features", {}).get(feature, False):
                raise PermissionError("Cette revue Bectanse Coach n’est pas incluse dans votre formule.")
            scope, timezone_name = query_context()
            conn = get_conn()
            try:
                account_ids, _ = service._scope_account_ids(conn, session["member_code"], scope)
                from datetime import datetime, timedelta, timezone
                start = datetime.now(timezone.utc) - timedelta(days=365)
                deals = service._fetch_deals(conn, account_ids, start)
                behavior_events = service._fetch_behavior_events(conn, account_ids, start)
            finally:
                conn.close()
            return live_json({"ok": True, **coach_review(
                deals, timezone_name, review_type, behavior_events=behavior_events
            )})
        except Exception as error:
            return _api_error(error)

    @app.route("/api/trading/coach/ask", methods=["POST"])
    @login_required
    @_rate_limited("trading-coach-ask", 30, 15 * 60)
    def trading_coach_ask():
        try:
            entitlements = service.require_access(session["member_code"])
            if not entitlements.get("features", {}).get("coach.ai_explanations", False):
                raise PermissionError("Le Coach interactif n’est pas inclus dans votre formule.")
            payload = request.get_json(silent=True) or {}
            scope = str(payload.get("account_id") or "all")
            timezone_name = str(payload.get("timezone") or service.profile(session["member_code"])["timezone"])
            from datetime import datetime, timedelta, timezone
            start = datetime.now(timezone.utc) - timedelta(days=365)
            conn = get_conn()
            try:
                account_ids, _ = service._scope_account_ids(conn, session["member_code"], scope)
                deals = service._fetch_deals(conn, account_ids, start)
                behavior_events = service._fetch_behavior_events(conn, account_ids, start)
            finally:
                conn.close()
            return jsonify({"ok": True, **coach_answer_question(
                deals, behavior_events, timezone_name, payload.get("question")
            )})
        except Exception as error:
            return _api_error(error)

    def account_query_context(account_id):
        service.require_access(session["member_code"])
        timezone_name = str(request.args.get("timezone") or service.profile(session["member_code"])["timezone"])
        return str(account_id), timezone_name

    @app.route("/trading/accounts/<int:account_id>/calendar", methods=["GET"])
    @login_required
    def trading_account_calendar(account_id):
        try:
            scope, timezone_name = account_query_context(account_id)
            month = str(request.args.get("month") or time.strftime("%Y-%m"))
            return jsonify({"ok": True, **service.calendar(session["member_code"], scope, month, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/trading/accounts/<int:account_id>/days/<date_value>", methods=["GET"])
    @login_required
    def trading_account_day(account_id, date_value):
        try:
            scope, timezone_name = account_query_context(account_id)
            return jsonify({"ok": True, **service.day(session["member_code"], scope, date_value, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/trading/accounts/<int:account_id>/stats", methods=["GET"])
    @login_required
    def trading_account_stats(account_id):
        try:
            scope, timezone_name = account_query_context(account_id)
            return jsonify({"ok": True, **service.stats(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/trading/accounts/<int:account_id>/equity", methods=["GET"])
    @login_required
    def trading_account_equity(account_id):
        try:
            scope, timezone_name = account_query_context(account_id)
            return jsonify({"ok": True, **service.equity_curve(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    @app.route("/trading/accounts/<int:account_id>/analytics", methods=["GET"])
    @login_required
    def trading_account_analytics(account_id):
        try:
            entitlements = service.require_access(session["member_code"])
            if not entitlements["advanced_analytics"]:
                raise PermissionError("Les analytics avancés sont disponibles avec la formule ELITE.")
            scope, timezone_name = account_query_context(account_id)
            return jsonify({"ok": True, **service.analytics(session["member_code"], scope, timezone_name)})
        except Exception as error:
            return _api_error(error)

    def worker_authorized():
        raw_body = request.get_data(cache=True)
        nonce = str(request.headers.get("X-MT5-Nonce") or "")
        worker_id = str(request.headers.get("X-MT5-Worker-ID") or "")[:100]
        signature_valid = verify_worker_request(
            worker_secret(),
            request.method,
            request.path,
            request.headers.get("X-MT5-Timestamp", ""),
            raw_body,
            request.headers.get("X-MT5-Signature", ""),
            nonce=nonce,
        )
        return signature_valid and bool(worker_id) and service.accept_worker_nonce(worker_id, nonce)

    def worker_identity():
        worker_id = str(request.headers.get("X-MT5-Worker-ID") or "")[:100]
        instance_id = str(request.headers.get("X-MT5-Instance-ID") or worker_id)[:100]
        if not worker_id:
            raise ValueError("Worker identity missing")
        return worker_id, instance_id

    def worker_guard(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not worker_authorized():
                return jsonify({"ok": False}), 401
            return function(*args, **kwargs)
        return wrapped

    @app.route("/internal/mt5/jobs/claim", methods=["POST"])
    @worker_guard
    def mt5_job_claim():
        try:
            worker_id, instance_id = worker_identity()
            service.enqueue_due_accounts()
            job = service.claim_job(worker_id, instance_id)
            response = jsonify({"ok": True, "job": job})
            response.status_code = 200 if job else 204
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as error:
            app.logger.error("MT5 worker claim failed: %s", error)
            return jsonify({"ok": False, "error": "worker_claim_failed"}), 500

    @app.route("/internal/mt5/workers/heartbeat", methods=["POST"])
    @worker_guard
    def mt5_worker_heartbeat():
        try:
            worker_id, instance_id = worker_identity()
            return jsonify({"ok": True, **service.worker_heartbeat(
                worker_id, instance_id, request.get_json(silent=True) or {}
            )})
        except Exception as error:
            app.logger.error("MT5 worker heartbeat failed: %s", error)
            return jsonify({"ok": False, "error": "worker_heartbeat_failed"}), 400

    @app.route("/internal/mt5/jobs/<job_id>/heartbeat", methods=["POST"])
    @worker_guard
    def mt5_job_heartbeat(job_id):
        try:
            worker_id, _ = worker_identity()
            if not service.heartbeat(job_id, worker_id):
                raise LookupError("job_not_found")
            return jsonify({"ok": True})
        except Exception:
            return jsonify({"ok": False, "error": "job_not_found"}), 404

    @app.route("/internal/mt5/jobs/<job_id>/batch", methods=["POST"])
    @worker_guard
    def mt5_job_batch(job_id):
        try:
            worker_id, _ = worker_identity()
            payload = request.get_json(silent=True) or {}
            return jsonify({"ok": True, **service.import_batch(
                job_id, worker_id, payload.get("deals"), str(payload.get("batch_id") or "")
            )})
        except (ValueError, LookupError) as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        except Exception as error:
            app.logger.error("MT5 worker batch %s failed: %s", job_id, error)
            return jsonify({"ok": False, "error": "batch_failed"}), 500

    @app.route("/internal/mt5/jobs/<job_id>/complete", methods=["POST"])
    @worker_guard
    def mt5_job_complete(job_id):
        try:
            worker_id, _ = worker_identity()
            payload = request.get_json(silent=True) or {}
            result = service.complete_job(
                job_id, worker_id, payload.get("account") or {}, payload.get("duration_ms") or 0,
                payload.get("received_deals"), payload.get("source_pnl"),
                payload.get("positions"), payload.get("orders"), payload.get("terminal"),
            )
            return jsonify({"ok": True, **result})
        except (ValueError, LookupError, PermissionError) as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        except Exception as error:
            app.logger.error("MT5 worker completion %s failed: %s", job_id, error)
            return jsonify({"ok": False, "error": "completion_failed"}), 500

    @app.route("/internal/mt5/jobs/<job_id>/fail", methods=["POST"])
    @worker_guard
    def mt5_job_fail(job_id):
        try:
            worker_id, _ = worker_identity()
            payload = request.get_json(silent=True) or {}
            result = service.fail_job(job_id, worker_id, str(payload.get("code") or "SYNC_ERROR"), bool(payload.get("retryable")))
            return jsonify({"ok": True, **result})
        except Exception as error:
            app.logger.error("MT5 worker failure report %s failed: %s", job_id, error)
            return jsonify({"ok": False, "error": "failure_report_failed"}), 500

    def _admin_guard(function):
        return admin_required(function) if admin_required else login_required(function)

    @app.route("/health/database", methods=["GET"])
    def trading_database_health():
        try:
            conn = get_conn()
            try:
                conn.run("SELECT 1")
            finally:
                conn.close()
            return jsonify({"status": "ok"})
        except Exception:
            return jsonify({"status": "unavailable"}), 503

    @app.route("/health/queue", methods=["GET"])
    @_admin_guard
    def trading_queue_health():
        return jsonify(service.queue_health())

    @app.route("/health/workers", methods=["GET"])
    @app.route("/health/mt5", methods=["GET"])
    @_admin_guard
    def trading_workers_health():
        return jsonify(service.worker_health())

    @app.route("/admin/trading/jobs/<job_id>/retry", methods=["POST"])
    @_admin_guard
    def trading_admin_retry_job(job_id):
        try:
            return jsonify({"ok": True, **service.retry_dead_job(job_id)})
        except Exception as error:
            return _api_error(error)

    @app.route("/admin/trading/accounts/<int:account_id>/reconcile", methods=["POST"])
    @_admin_guard
    def trading_admin_reconcile(account_id):
        try:
            payload = request.get_json(silent=True) or {}
            return jsonify({"ok": True, **service.force_reconciliation(account_id, days=payload.get("days"))})
        except Exception as error:
            return _api_error(error)

    return service
