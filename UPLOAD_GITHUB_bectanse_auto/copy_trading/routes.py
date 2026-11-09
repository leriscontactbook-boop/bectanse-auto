"""Flask integration for member status and signed internal copy workers."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import jsonify, request, session

from .domain import (
    CopyDecisionError, academy_settings, enabled_unsupported_academy_guards,
    worker_claims_enabled,
)
from .security import terminal_account_fingerprint, verify_copy_worker_request
from .service import CopyTradingService


def register_copy_trading(app, get_conn, get_member, login_required,
                          academy_access_required, admin_required, can_copy_member):
    consent_version = (os.environ.get("COPY_TRADING_CONSENT_VERSION") or "").strip()
    worker_secrets = {
        role: (os.environ.get(f"COPY_TRADING_{role}_WORKER_SECRET") or "").strip()
        for role in ("MASTER", "FOLLOWER", "CALENDAR")
    }
    master_member_code = (os.environ.get("COPY_TRADING_MASTER_MEMBER_CODE") or "").strip().upper()
    service = CopyTradingService(
        get_conn, get_member, can_copy_member,
        consent_version=consent_version, logger=app.logger,
    )

    def audit(conn, event_type, *, master="", follower="", outcome="", details=None):
        conn.run(
            """INSERT INTO copy_trading_audit_events
               (event_type,master_member_code,follower_member_code,outcome_code,details)
               VALUES (:event,:master,:follower,:outcome,:details::jsonb)""",
            event=str(event_type)[:64], master=str(master or "")[:100],
            follower=str(follower or "")[:100], outcome=str(outcome or "")[:100],
            details=json.dumps(details or {}, separators=(",", ":"), default=str),
        )

    def api_error(error):
        if isinstance(error, CopyDecisionError):
            status = 409 if error.code in {
                "EVENT_EXPIRED", "MAPPING_REVIEW_REQUIRED", "FOLLOWER_NOT_AUTHORIZED",
                "OPERATIONAL_RELEASE_REQUIRED", "ATOMIC_HANDOFF_REJECTED",
                "TERMINAL_IDENTITY_MISMATCH", "TERMINAL_TRADING_DISABLED",
                "ACADEMY_GUARD_NOT_SUPPORTED",
            } else 400
            return jsonify({"ok": False, "code": error.code, "error": str(error)}), status
        app.logger.error("Copy-trading request failed (%s)", type(error).__name__)
        return jsonify({"ok": False, "error": "Le service de copie est momentanément indisponible."}), 500

    def internal_worker(expected_role=None):
        def decorate(function):
            @wraps(function)
            def wrapped(*args, **kwargs):
                worker_id = str(request.headers.get("X-COPY-Worker-ID", "")).strip()[:100]
                role = str(request.headers.get("X-COPY-Worker-Role", "")).strip().upper()
                secret = worker_secrets.get(role, "")
                if len(secret) < 32:
                    return jsonify({"ok": False, "error": "Worker de copie non configuré."}), 503
                raw = request.get_data(cache=True)
                timestamp = request.headers.get("X-COPY-Timestamp", "")
                nonce = request.headers.get("X-COPY-Nonce", "")
                signature = request.headers.get("X-COPY-Signature", "")
                if (not worker_id or (expected_role and role != expected_role) or not verify_copy_worker_request(
                    secret, request.method, request.path, timestamp, raw, signature,
                    nonce, worker_id, role,
                )):
                    return jsonify({"ok": False, "error": "Worker non autorisé."}), 401
                try:
                    conn = get_conn()
                    try:
                        conn.run("DELETE FROM copy_trading_worker_nonces WHERE created_at<NOW()-INTERVAL '1 day'")
                        rows = conn.run(
                            """INSERT INTO copy_trading_worker_nonces(nonce,worker_id,role)
                               VALUES (:nonce,:worker,:role) ON CONFLICT (nonce) DO NOTHING
                               RETURNING nonce""",
                            nonce=nonce, worker=worker_id, role=role,
                        )
                    finally:
                        conn.close()
                    if not rows:
                        return jsonify({"ok": False, "error": "Requête worker déjà utilisée."}), 401
                except Exception as error:
                    app.logger.warning("Copy worker nonce store unavailable (%s)", type(error).__name__)
                    return jsonify({"ok": False, "error": "Vérification worker indisponible."}), 503
                request.environ["copy_worker_id"] = worker_id
                request.environ["copy_worker_role"] = role
                response = function(*args, **kwargs)
                if hasattr(response, "headers"):
                    response.headers["Cache-Control"] = "no-store, private"
                    response.headers["Pragma"] = "no-cache"
                return response
            return wrapped
        return decorate

    @app.get("/api/copy-trading/status")
    @login_required
    @academy_access_required
    def copy_trading_status():
        code = str(session.get("member_code") or "")
        member = get_member(code) or {}
        copy_enabled = (os.environ.get("COPY_TRADING_EXECUTION_ENABLED", "false").lower()
                        in {"1", "true", "yes", "on"})
        mode = (os.environ.get("COPY_TRADING_MODE") or "simulation").strip().lower()
        live_approved = (os.environ.get("COPY_TRADING_LIVE_RELEASE_APPROVED", "false").lower()
                         in {"1", "true", "yes", "on"})
        conn = get_conn()
        try:
            master_rows = conn.run(
                "SELECT enabled FROM copy_trading_master_accounts WHERE member_code=:code",
                code=master_member_code,
            ) if master_member_code else []
            consents = conn.run(
                """SELECT consent_version,consented_at,revoked_at FROM copy_trading_consents
                   WHERE member_code=:code""", code=code,
            )
            workers = conn.run(
                """SELECT slot_role,status,last_seen_at FROM copy_trading_workers
                   WHERE last_seen_at>NOW()-INTERVAL '90 seconds' AND status='ONLINE'
                   ORDER BY slot_role,worker_id"""
            )
            terminal_probe_rows = conn.run(
                """SELECT state,failure_code,diagnostic_code,checked_at,process_id,session_id,
                          account_fingerprint,server,terminal_path_verified
                   FROM copy_trading_terminal_probes WHERE member_code=:code
                   ORDER BY checked_at DESC LIMIT 1""", code=code,
            )
            jobs = conn.run(
                """SELECT status,COUNT(*) FROM copy_trading_jobs
                   WHERE follower_member_code=:code AND created_at>NOW()-INTERVAL '7 days'
                   GROUP BY status""", code=code,
            )
            mapping = conn.run(
                """SELECT state,COUNT(*) FROM copy_trading_symbol_mappings
                   WHERE follower_member_code=:code GROUP BY state""", code=code,
            )
            native_proofs = conn.run(
                """SELECT 1 FROM copy_trading_jobs
                   WHERE follower_member_code=:code AND status='COMPLETE'
                     AND native_evidence #>> '{market,is_demo}'='true'
                     AND result_payload #>> '{native_postcondition,terminal_connected}'='true'
                     AND result_payload #>> '{native_postcondition,terminal_path_verified}'='true'
                     AND updated_at>NOW()-INTERVAL '24 hours'
                   ORDER BY updated_at DESC LIMIT 1""", code=code,
            )
            follower_locks = conn.run(
                """SELECT blocked,blocked_reason FROM copy_trading_follower_locks
                   WHERE member_code=:code""", code=code,
            )
        finally:
            conn.close()
        consent = consents[0] if consents else None
        params = dict(member.get("params") or {})
        settings = None
        settings_invalid = False
        try:
            settings = academy_settings(params)
            unsupported_guards = enabled_unsupported_academy_guards(params)
            calendar_allows, calendar_state = service.calendar_allows(
                settings, region=str(params.get("calendar_region") or "ALL"),
            )
        except Exception:
            settings_invalid = True
            unsupported_guards = ()
            calendar_allows, calendar_state = False, "CALENDAR_OR_SETTINGS_UNAVAILABLE"
        consent_accepted = bool(consent and consent[0] == consent_version and consent[1] and not consent[2])
        profile_ready = bool(params.get("mt_login") and
                             (params.get("serveur") or params.get("mt_server")) and
                             params.get("mt_password"))
        terminal_probe = terminal_probe_rows[0] if terminal_probe_rows else None
        expected_login = str(params.get("mt_login") or "").strip()
        expected_server = str(params.get("serveur") or params.get("mt_server") or "").strip()
        expected_fingerprint = terminal_account_fingerprint(
            worker_secrets["FOLLOWER"], expected_login, expected_server,
        ) if worker_secrets["FOLLOWER"] and expected_login and expected_server else ""
        terminal_probe_matches_profile = bool(
            terminal_probe and str(terminal_probe[6] or "").strip() == expected_fingerprint
            and str(terminal_probe[7] or "").strip().casefold() == expected_server.casefold()
            and terminal_probe[8] is True
        )
        terminal_probe_recent = bool(
            terminal_probe and terminal_probe[3]
            and terminal_probe[3] > datetime.now(timezone.utc) - timedelta(hours=24)
        )
        terminal_probe_ready = bool(
            terminal_probe and terminal_probe[0] == "CONNECTED"
            and terminal_probe_matches_profile and terminal_probe_recent
        )
        if terminal_probe and terminal_probe[0] == "FAILED":
            terminal_probe_blocker = str(terminal_probe[1] or "TERMINAL_CONNECTION_FAILED")
            if terminal_probe[2] is not None:
                terminal_probe_blocker += f"_{terminal_probe[2]}"
        else:
            terminal_probe_blocker = "TERMINAL_CONNECTION_NOT_VERIFIED"
        master_ready = bool(master_rows and master_rows[0][0])
        worker_ready = any(row[0] == "FOLLOWER" for row in (workers or []))
        master_worker_ready = any(row[0] == "MASTER" for row in (workers or []))
        try:
            membership_ready = bool(can_copy_member(member))
        except Exception:
            membership_ready = False
        demo_e2e_verified = bool(native_proofs)
        reconciliation_required = bool(follower_locks and follower_locks[0][0])
        runtime_release_ready = (mode == "demo" and copy_enabled) or (
            mode == "live" and copy_enabled and live_approved
        )
        calendar_ready = bool(
            settings is not None
            and (not settings.copy_news_filter or calendar_state != "CALENDAR_STALE_OR_UNAVAILABLE")
        )
        readiness = "READY" if all((member.get("copy_actif"), consent_accepted, profile_ready,
                                      master_ready, master_worker_ready, copy_enabled,
                                      mode in {"demo", "live"}, worker_ready, membership_ready,
                                      runtime_release_ready, demo_e2e_verified, not unsupported_guards,
                                      not settings_invalid, terminal_probe_ready, not reconciliation_required,
                                      calendar_ready)) else "BLOCKED"
        job_counts = {str(status): int(count) for status, count in (jobs or [])}
        mapping_counts = {str(state): int(count) for state, count in (mapping or [])}
        return jsonify({
            "ok": True,
            "member_code": code,
            "copy_preference_enabled": bool(member.get("copy_actif")),
            "account_profile_configured": profile_ready,
            "demo_account_end_to_end_verified": demo_e2e_verified,
            "terminal_connection": {
                "state": str(terminal_probe[0]) if terminal_probe else "UNKNOWN",
                "failure_code": terminal_probe[1] if terminal_probe else None,
                "diagnostic_code": terminal_probe[2] if terminal_probe else None,
                "checked_at": terminal_probe[3].isoformat() if terminal_probe and terminal_probe[3] else None,
                "worker_process_id": terminal_probe[4] if terminal_probe else None,
                "windows_session_id": terminal_probe[5] if terminal_probe else None,
                "terminal_path_verified": terminal_probe[8] is True if terminal_probe else False,
                "matches_account_profile": terminal_probe_matches_profile,
                "recent": terminal_probe_recent,
            },
            "position_reconciliation_required": reconciliation_required,
            "position_reconciliation_reason": (
                str(follower_locks[0][1] or "POSITION_RECONCILIATION_REQUIRED")
                if reconciliation_required else None
            ),
            "operational_readiness": readiness,
            "operational_blockers": [
                label for condition, label in (
                    (not member.get("copy_actif"), "CLIENT_PAUSED"),
                    (not membership_ready, "MEMBERSHIP_NOT_ELIGIBLE"),
                    (not consent_accepted, "CONSENT_REQUIRED"),
                    (not profile_ready, "MT5_PROFILE_INCOMPLETE"),
                    (not master_ready, "MASTER_NOT_RELEASED"),
                    (not master_worker_ready, "MASTER_WORKER_OFFLINE"),
                    (not copy_enabled or mode == "simulation", "EXECUTION_NOT_RELEASED"),
                    (mode == "live" and not live_approved, "LIVE_RELEASE_REQUIRED"),
                    (not worker_ready, "FOLLOWER_WORKER_OFFLINE"),
                    (not demo_e2e_verified and mode in {"demo", "live"},
                     "DEMO_ACCOUNT_END_TO_END_UNVERIFIED" if mode == "demo" else "LIVE_ACCOUNT_END_TO_END_UNVERIFIED"),
                    (not terminal_probe_ready, terminal_probe_blocker),
                    (reconciliation_required, "POSITION_RECONCILIATION_REQUIRED"),
                    (bool(unsupported_guards), "ACADEMY_GUARD_NOT_SUPPORTED"),
                    (settings_invalid, "ACADEMY_SETTINGS_INVALID"),
                    (not calendar_ready, "CALENDAR_FEED_STALE_OR_UNAVAILABLE"),
                ) if condition
            ],
            "consent": {
                "current_version": consent_version or None,
                "accepted": consent_accepted,
                "accepted_at": consent[1].isoformat() if consent and consent[1] else None,
                "revoked_at": consent[2].isoformat() if consent and consent[2] else None,
            },
            "settings": {
                "mode_risque": str(params.get("mode_risque") or "Lots fixes"),
                "filtre_news": bool(params.get("filtre_news", False)),
                "copier_sl": bool(params.get("copier_sl", True)),
                "enabled_unsupported_guards": list(unsupported_guards),
                "calendar_filter_state": calendar_state,
                "calendar_allows_dispatch": bool(calendar_allows),
            },
            "jobs_last_7_days": job_counts,
            "symbol_mappings": mapping_counts,
            "workers_online": [
                {"role": row[0], "status": row[1], "last_seen_at": row[2].isoformat()}
                for row in (workers or [])
            ],
            "execution_enabled": copy_enabled,
            "execution_mode": mode,
        })

    @app.post("/api/copy-trading/consent")
    @login_required
    @academy_access_required
    def copy_trading_accept_consent():
        payload = request.get_json(silent=True) or {}
        if not consent_version:
            return jsonify({"ok": False, "error": "La version de consentement n’a pas été publiée."}), 503
        if payload.get("accepted") is not True or payload.get("version") != consent_version:
            return jsonify({"ok": False, "error": "Une acceptation explicite de la version courante est requise."}), 400
        code = str(session.get("member_code") or "")
        conn = get_conn()
        try:
            conn.run("BEGIN")
            conn.run(
                """INSERT INTO copy_trading_consents(member_code,consent_version,consented_at,revoked_at,updated_at)
                   VALUES (:code,:version,NOW(),NULL,NOW())
                   ON CONFLICT (member_code) DO UPDATE SET consent_version=EXCLUDED.consent_version,
                     consented_at=NOW(),revoked_at=NULL,updated_at=NOW()""",
                code=code, version=consent_version,
            )
            audit(conn, "CONSENT_ACCEPTED", follower=code, outcome=consent_version)
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return jsonify({"ok": True, "accepted": True, "version": consent_version})

    @app.post("/api/copy-trading/revoke-consent")
    @login_required
    @academy_access_required
    def copy_trading_revoke_consent():
        code = str(session.get("member_code") or "")
        conn = get_conn()
        try:
            conn.run("BEGIN")
            conn.run(
                "UPDATE copy_trading_consents SET revoked_at=NOW(),updated_at=NOW() WHERE member_code=:code",
                code=code,
            )
            audit(conn, "CONSENT_REVOKED", follower=code, outcome="REVOKED")
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return jsonify({"ok": True, "accepted": False})

    @app.post("/admin/api/copy-trading/master")
    @admin_required
    def admin_configure_copy_master():
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("member_code") or "").strip().upper()
        enabled = payload.get("enabled") is True
        if not code:
            return jsonify({"ok": False, "error": "member_code est requis."}), 400
        if code != master_member_code:
            return jsonify({"ok": False, "error": "Le code ne correspond pas à COPY_TRADING_MASTER_MEMBER_CODE."}), 409
        conn = get_conn()
        try:
            conn.run("BEGIN")
            exists = conn.run("SELECT code FROM members WHERE UPPER(code)=:code", code=code)
            if not exists:
                conn.run("ROLLBACK")
                return jsonify({"ok": False, "error": "Compte maître introuvable."}), 404
            conn.run(
                """INSERT INTO copy_trading_master_accounts(member_code,enabled)
                   VALUES (:code,:enabled) ON CONFLICT (member_code) DO UPDATE
                   SET enabled=EXCLUDED.enabled,updated_at=NOW()""",
                code=code, enabled=enabled,
            )
            audit(conn, "MASTER_CONFIGURATION", master=code,
                  outcome="ENABLED" if enabled else "DISABLED")
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return jsonify({"ok": True, "enabled": enabled})

    @app.get("/admin/api/copy-trading/operations")
    @admin_required
    def admin_copy_trading_operations():
        conn = get_conn()
        try:
            workers = conn.run(
                """SELECT worker_id,slot_role,status,last_seen_at,current_job_id
                   FROM copy_trading_workers ORDER BY last_seen_at DESC LIMIT 100"""
            )
            terminal_probes = conn.run(
                """SELECT worker_id,slot_role,member_code,server,state,failure_code,
                          diagnostic_code,terminal_path_verified,process_id,session_id,checked_at
                   FROM copy_trading_terminal_probes ORDER BY checked_at DESC LIMIT 100"""
            )
            jobs = conn.run(
                """SELECT status,COUNT(*) FROM copy_trading_jobs
                   WHERE created_at>NOW()-INTERVAL '24 hours' GROUP BY status ORDER BY status"""
            )
            recent = conn.run(
                """SELECT event_type,master_member_code,follower_member_code,job_id::text,
                          outcome_code,created_at FROM copy_trading_audit_events
                   ORDER BY created_at DESC LIMIT 100"""
            )
            follower_locks = conn.run(
                """SELECT member_code,job_id::text,blocked_reason,updated_at
                   FROM copy_trading_follower_locks WHERE blocked=TRUE
                   ORDER BY updated_at DESC LIMIT 100"""
            )
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "execution_mode": os.environ.get("COPY_TRADING_MODE", "simulation"),
            "execution_enabled": os.environ.get("COPY_TRADING_EXECUTION_ENABLED", "false").lower()
                                in {"1", "true", "yes", "on"},
            "workers": [{"id": r[0], "role": r[1], "status": r[2], "last_seen_at": r[3].isoformat(),
                         "current_job_id": r[4]} for r in (workers or [])],
            "terminal_probes": [{
                "worker_id": r[0], "role": r[1], "member_code": r[2], "server": r[3],
                "state": r[4], "failure_code": r[5], "diagnostic_code": r[6],
                "terminal_path_verified": r[7], "process_id": r[8],
                "session_id": r[9], "checked_at": r[10].isoformat(),
            } for r in (terminal_probes or [])],
            "jobs_24h": {str(r[0]): int(r[1]) for r in (jobs or [])},
            "recent": [{"event": r[0], "master": r[1], "follower": r[2], "job_id": r[3],
                        "outcome": r[4], "created_at": r[5].isoformat()} for r in (recent or [])],
            "blocked_followers": [
                {"member_code": r[0], "job_id": r[1], "reason": r[2],
                 "updated_at": r[3].isoformat()}
                for r in (follower_locks or [])
            ],
        })

    @app.post("/admin/api/copy-trading/mappings/<follower_code>/review")
    @admin_required
    def admin_review_copy_mapping(follower_code):
        payload = request.get_json(silent=True) or {}
        master_symbol = str(payload.get("master_symbol") or "").strip()
        follower_symbol = str(payload.get("follower_symbol") or "").strip()
        evidence_hash = str(payload.get("evidence_hash") or "").strip()
        if not master_symbol or not follower_symbol or not evidence_hash:
            return jsonify({"ok": False, "error": "La preuve native et ses deux symboles sont requis."}), 400
        decision = str(payload.get("decision") or "approve").strip().lower()
        if decision not in {"approve", "reject"}:
            return jsonify({"ok": False, "error": "Décision de revue invalide."}), 400
        conn = get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT native_evidence,evidence_hash,state FROM copy_trading_symbol_candidates
                   WHERE master_member_code=:master AND follower_member_code=:follower
                     AND master_symbol=:symbol AND follower_symbol=:follower_symbol FOR UPDATE""",
                master=master_member_code, follower=follower_code.strip().upper(), symbol=master_symbol,
                follower_symbol=follower_symbol,
            )
            if not rows:
                conn.run("ROLLBACK")
                return jsonify({"ok": False, "error": "Aucune preuve native à examiner."}), 404
            native_evidence = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
            actual_hash = str(rows[0][1] or "")
            if (actual_hash != evidence_hash
                    or native_evidence.get("compatible") is not True
                    or str(native_evidence.get("master", {}).get("name") or "") != master_symbol
                    or str(native_evidence.get("follower", {}).get("name") or "") != follower_symbol):
                conn.run("ROLLBACK")
                return jsonify({"ok": False, "error": "La preuve a changé ; reprenez l’examen sur l’évidence native la plus récente."}), 409
            reviewer = str(session.get("admin_id") or session.get("admin_user") or "admin")[:120]
            reviewed_state = "VERIFIED" if decision == "approve" else "REJECTED"
            updated = conn.run(
                """UPDATE copy_trading_symbol_candidates SET state=:state,reviewed_by=:reviewer,
                   reviewed_at=NOW(),updated_at=NOW()
                   WHERE master_member_code=:master AND follower_member_code=:follower
                     AND master_symbol=:symbol AND follower_symbol=:follower_symbol
                     AND evidence_hash=:hash RETURNING evidence_hash""",
                state=reviewed_state,
                reviewer=reviewer, master=master_member_code, follower=follower_code.strip().upper(),
                symbol=master_symbol, follower_symbol=follower_symbol, hash=evidence_hash,
            )
            if not updated:
                conn.run("ROLLBACK")
                return jsonify({"ok": False, "error": "La preuve a changé pendant la revue."}), 409
            if decision == "approve":
                conn.run(
                    """INSERT INTO copy_trading_symbol_mappings
                       (master_member_code,follower_member_code,master_symbol,follower_symbol,state,
                        native_evidence,evidence_hash,reviewed_by,reviewed_at,updated_at)
                       VALUES (:master,:follower,:master_symbol,:follower_symbol,'VERIFIED',
                               :evidence::jsonb,:hash,:reviewer,NOW(),NOW())
                       ON CONFLICT (master_member_code,follower_member_code,master_symbol) DO UPDATE SET
                         follower_symbol=EXCLUDED.follower_symbol,state='VERIFIED',
                         native_evidence=EXCLUDED.native_evidence,evidence_hash=EXCLUDED.evidence_hash,
                         reviewed_by=EXCLUDED.reviewed_by,reviewed_at=NOW(),updated_at=NOW()""",
                    master=master_member_code, follower=follower_code.strip().upper(),
                    master_symbol=master_symbol, follower_symbol=follower_symbol,
                    evidence=json.dumps(native_evidence, separators=(",", ":")),
                    hash=evidence_hash, reviewer=reviewer,
                )
            audit(conn, "SYMBOL_MAPPING_REVIEW", master=master_member_code,
                  follower=follower_code.strip().upper(), outcome=reviewed_state,
                  details={"master_symbol": master_symbol, "follower_symbol": follower_symbol,
                           "evidence_hash": evidence_hash, "reviewer": reviewer})
            conn.run("COMMIT")
        finally:
            conn.close()
        return jsonify({"ok": True, "state": reviewed_state, "evidence_hash": evidence_hash})

    @app.get("/admin/api/copy-trading/mappings")
    @admin_required
    def admin_copy_trading_mappings():
        conn = get_conn()
        try:
            rows = conn.run(
                """SELECT master_member_code,follower_member_code,master_symbol,follower_symbol,
                          state,evidence_hash,native_evidence,reviewed_by,reviewed_at,updated_at
                   FROM copy_trading_symbol_mappings
                   UNION ALL
                   SELECT master_member_code,follower_member_code,master_symbol,follower_symbol,
                          state,evidence_hash,native_evidence,reviewed_by,reviewed_at,updated_at
                   FROM copy_trading_symbol_candidates
                   ORDER BY updated_at DESC LIMIT 500"""
            )
        finally:
            conn.close()
        return jsonify({"ok": True, "mappings": [
            {"master_member_code": r[0], "follower_member_code": r[1],
             "master_symbol": r[2], "follower_symbol": r[3], "state": r[4],
             "evidence_hash": r[5], "native_evidence": r[6], "reviewed_by": r[7],
             "reviewed_at": r[8].isoformat() if r[8] else None,
             "updated_at": r[9].isoformat() if r[9] else None}
            for r in (rows or [])
        ]})

    @app.post("/internal/copy-trading/workers/heartbeat")
    @internal_worker()
    def copy_worker_heartbeat():
        payload = request.get_json(silent=True) or {}
        worker_id = request.environ["copy_worker_id"]
        role = str(payload.get("role") or "").upper()
        if role not in {"MASTER", "FOLLOWER", "CALENDAR"} or role != request.environ.get("copy_worker_role"):
            return jsonify({"ok": False, "error": "Rôle worker invalide."}), 400
        probe = payload.get("terminal_probe")
        if probe is not None:
            if not isinstance(probe, dict) or role not in {"MASTER", "FOLLOWER"}:
                return jsonify({"ok": False, "error": "Preuve de terminal invalide."}), 400
            state = str(probe.get("state") or "").upper()
            member_code = str(probe.get("member_code") or "").strip()
            account_fingerprint = str(probe.get("account_fingerprint") or "").strip().lower()
            server = str(probe.get("server") or "").strip()
            failure_code = str(probe.get("failure_code") or "")[:80]
            terminal_path_verified = probe.get("terminal_path_verified")
            job_id = str(payload.get("current_job_id") or "").strip()
            diagnostic_code = probe.get("diagnostic_code")
            process_id = probe.get("process_id")
            session_id = probe.get("session_id")
            integer_fields = (diagnostic_code, process_id, session_id)
            if (state not in {"CONNECTED", "FAILED"} or not member_code
                    or len(account_fingerprint) != 64
                    or any(char not in "0123456789abcdef" for char in account_fingerprint)
                    or not server
                    or failure_code not in {"", "TERMINAL_CONNECTION_FAILED",
                                             "TERMINAL_IDENTITY_MISMATCH",
                                             "TERMINAL_PROCESS_PATH_MISMATCH"}
                    or not isinstance(terminal_path_verified, bool)
                    or (state == "CONNECTED" and
                        (failure_code or not terminal_path_verified or diagnostic_code is not None))
                    or (state == "FAILED" and not failure_code)
                    or any(isinstance(value, bool) or (value is not None and not isinstance(value, int))
                           for value in integer_fields)
                    or (process_id is not None and process_id <= 0)
                    or (session_id is not None and session_id < 0)):
                return jsonify({"ok": False, "error": "Identité ou état de terminal invalide."}), 400
            if role == "MASTER" and member_code.casefold() != master_member_code.casefold():
                return jsonify({"ok": False, "error": "Le terminal maître ne correspond pas au maître configuré."}), 403
        conn = get_conn()
        try:
            previous_rows = conn.run(
                "SELECT status FROM copy_trading_workers WHERE worker_id=:worker FOR UPDATE",
                worker=worker_id,
            )
            conn.run(
                """INSERT INTO copy_trading_workers
                   (worker_id,instance_id,slot_role,terminal_fingerprint,status,current_job_id,last_seen_at,updated_at)
                   VALUES (:worker,:instance,:role,:fingerprint,:status,:job,NOW(),NOW())
                   ON CONFLICT(worker_id) DO UPDATE SET instance_id=EXCLUDED.instance_id,
                     slot_role=EXCLUDED.slot_role,terminal_fingerprint=EXCLUDED.terminal_fingerprint,
                     status=EXCLUDED.status,current_job_id=EXCLUDED.current_job_id,
                     last_seen_at=NOW(),updated_at=NOW()""",
                worker=worker_id, instance=str(payload.get("instance_id") or "")[:100], role=role,
                fingerprint=str(payload.get("terminal_fingerprint") or "")[:128],
                status=str(payload.get("status") or "ONLINE")[:32],
                job=str(payload.get("current_job_id") or "")[:80],
            )
            if probe is not None:
                if role == "FOLLOWER":
                    active_job = conn.run(
                        """SELECT follower_member_code FROM copy_trading_jobs
                           WHERE job_id=:job AND worker_id=:worker
                             AND status IN ('LEASED','PREPARED','DISPATCHING')""",
                        job=job_id, worker=worker_id,
                    )
                    if not active_job or str(active_job[0][0]).casefold() != member_code.casefold():
                        return jsonify({"ok": False, "error": "La preuve MT5 ne correspond pas au bail actif."}), 409
                conn.run(
                    """INSERT INTO copy_trading_terminal_probes
                       (worker_id,slot_role,member_code,account_fingerprint,server,state,failure_code,
                        terminal_path_verified,diagnostic_code,process_id,session_id,checked_at)
                       VALUES (:worker,:role,:member,:fingerprint,:server,:state,:failure_code,
                               :path_verified,:diagnostic,
                               :process_id,:session_id,NOW())
                       ON CONFLICT(worker_id,member_code) DO UPDATE SET
                         slot_role=EXCLUDED.slot_role,account_fingerprint=EXCLUDED.account_fingerprint,
                         server=EXCLUDED.server,failure_code=EXCLUDED.failure_code,
                         terminal_path_verified=EXCLUDED.terminal_path_verified,
                         state=EXCLUDED.state,diagnostic_code=EXCLUDED.diagnostic_code,
                         process_id=EXCLUDED.process_id,session_id=EXCLUDED.session_id,
                         checked_at=NOW()""",
                    worker=worker_id, role=role, member=member_code,
                    fingerprint=account_fingerprint,
                    server=server[:120], state=state, failure_code=failure_code,
                    path_verified=terminal_path_verified, diagnostic=diagnostic_code,
                    process_id=process_id, session_id=session_id,
                )
                audit(conn, "TERMINAL_CONNECTION_PROBE", master=member_code if role == "MASTER" else "",
                      follower=member_code if role == "FOLLOWER" else "",
                      outcome=state, details={"worker_id": worker_id,
                                              "diagnostic_code": diagnostic_code,
                                              "process_id": process_id,
                                              "session_id": session_id})
            new_status = str(payload.get("status") or "ONLINE")[:32]
            if not previous_rows or str(previous_rows[0][0]) != new_status:
                audit(conn, "WORKER_STATE", outcome=new_status,
                      details={"worker_id": worker_id, "role": role})
        finally:
            conn.close()
        return jsonify({"ok": True, "server_time": time.time()})

    @app.post("/internal/copy-trading/master/credentials")
    @internal_worker("MASTER")
    def copy_master_credentials():
        worker_id = request.environ["copy_worker_id"]
        conn = get_conn()
        try:
            enabled = conn.run(
                "SELECT enabled FROM copy_trading_master_accounts WHERE member_code=:code",
                code=master_member_code,
            )
        finally:
            conn.close()
        if not master_member_code or not enabled or not enabled[0][0]:
            return jsonify({"ok": False, "error": "Compte maître non activé."}), 409
        member = get_member(master_member_code) or {}
        params = dict(member.get("params") or {})
        if not params.get("mt_login") or not (params.get("serveur") or params.get("mt_server")) or not params.get("mt_password"):
            return jsonify({"ok": False, "error": "Profil maître MT5 incomplet."}), 409
        return jsonify({
            "ok": True,
            "member_code": master_member_code,
            "login": str(params.get("mt_login")),
            "server": str(params.get("serveur") or params.get("mt_server")),
            "password": str(params.get("mt_password")),
        })

    @app.post("/internal/copy-trading/master/snapshot")
    @internal_worker("MASTER")
    def copy_master_snapshot():
        if not master_member_code:
            return jsonify({"ok": False, "error": "Compte maître non configuré."}), 503
        payload = request.get_json(silent=True) or {}
        if (not isinstance(payload.get("positions"), list) or
                not isinstance(payload.get("native_symbols"), dict) or
                not isinstance(payload.get("close_deals", []), list)):
            return jsonify({"ok": False, "error": "Snapshot MT5 incomplet."}), 400
        try:
            result = service.ingest_master_snapshot(
                master_member_code, payload.get("account") or {},
                payload["positions"], payload["native_symbols"],
                payload.get("observed_at"), payload.get("close_deals", []),
            )
            return jsonify({"ok": True, **result})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/master/close-candidates")
    @internal_worker("MASTER")
    def copy_master_close_candidates():
        if not master_member_code:
            return jsonify({"ok": False, "error": "Compte maître non configuré."}), 503
        try:
            return jsonify({"ok": True, "position_ids":
                            service.master_positions_requiring_close_evidence(master_member_code)})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/claim")
    @internal_worker("FOLLOWER")
    def copy_job_claim():
        if not consent_version:
            return jsonify({"ok": False, "error": "COPY_TRADING_CONSENT_VERSION non configuré."}), 503
        mode = (os.environ.get("COPY_TRADING_MODE") or "simulation").strip().lower()
        execution_enabled = (os.environ.get("COPY_TRADING_EXECUTION_ENABLED", "false").lower()
                            in {"1", "true", "yes", "on"})
        live_release_approved = (os.environ.get("COPY_TRADING_LIVE_RELEASE_APPROVED", "false").lower()
                                 in {"1", "true", "yes", "on"})
        if not worker_claims_enabled(
            mode=mode, execution_enabled=execution_enabled,
            live_release_approved=live_release_approved,
        ):
            return jsonify({"ok": False, "code": "OPERATIONAL_RELEASE_REQUIRED",
                            "error": "Les contrôles de libération du moteur Copy Trading ne sont pas satisfaits."}), 409
        try:
            job = service.claim_job(
                request.environ["copy_worker_id"], require_demo_e2e=(mode == "live"),
            )
            return jsonify({"ok": True, "job": job})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/prepared")
    @internal_worker("FOLLOWER")
    def copy_job_prepared(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            result = service.mark_prepared(
                job_id, payload.get("lease_token"), symbol=payload.get("symbol"),
                follower_evidence=payload.get("follower_evidence") or {},
                market=payload.get("market") or {},
                request_comment=payload.get("request_comment") or "",
            )
            return jsonify({"ok": True, **(result if isinstance(result, dict) else {"prepared": bool(result)})})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/mapping-candidates")
    @internal_worker("FOLLOWER")
    def copy_job_mapping_candidates(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            result = service.record_mapping_candidates(
                job_id, payload.get("lease_token"), payload.get("candidates") or [],
            )
            return jsonify({"ok": True, **result})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/dispatch")
    @internal_worker("FOLLOWER")
    def copy_job_dispatch(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            result = service.begin_dispatch(job_id, payload.get("lease_token"))
            return jsonify({"ok": True, "dispatch": result})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/heartbeat")
    @internal_worker("FOLLOWER")
    def copy_job_heartbeat(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            result = service.renew_job_lease(job_id, payload.get("lease_token"))
            if not result:
                return jsonify({"ok": False, "error": "Le bail a expiré ou a été révoqué."}), 409
            return jsonify({"ok": True, **result})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/complete")
    @internal_worker("FOLLOWER")
    def copy_job_complete(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify({"ok": True, **service.complete_dispatch(job_id, result=payload.get("result") or {})})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/fail")
    @internal_worker("FOLLOWER")
    def copy_job_fail(job_id):
        payload = request.get_json(silent=True) or {}
        try:
            result = service.fail_before_dispatch(
                job_id, payload.get("lease_token"),
                str(payload.get("code") or "WORKER_PREFLIGHT_FAILED"),
                retryable=payload.get("retryable") is True,
            )
            return jsonify({"ok": True, "updated": bool(result)})
        except Exception as error:
            return api_error(error)

    @app.post("/internal/copy-trading/jobs/<job_id>/position-link")
    @internal_worker("FOLLOWER")
    def copy_job_position_link(job_id):
        conn = get_conn()
        try:
            rows = conn.run(
                """SELECT e.master_member_code,e.position_id,j.follower_member_code,
                          p.follower_position_id,p.follower_symbol,p.master_initial_volume,
                          p.follower_initial_volume,p.current_master_volume,p.state
                   FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   LEFT JOIN copy_trading_position_links p ON p.master_member_code=e.master_member_code
                     AND p.follower_member_code=j.follower_member_code
                     AND p.master_position_id=e.position_id
                   WHERE j.job_id=:job""", job=job_id,
            )
            if not rows:
                return jsonify({"ok": False, "error": "Tâche inconnue."}), 404
            row = rows[0]
        finally:
            conn.close()
        return jsonify({"ok": True, "link": {
            "master_member_code": row[0], "master_position_id": row[1],
            "follower_member_code": row[2], "follower_position_id": row[3],
            "follower_symbol": row[4], "master_initial_volume": str(row[5] or ""),
            "follower_initial_volume": str(row[6] or ""),
            "current_master_volume": str(row[7] or ""), "state": row[8] or "NONE",
        }})

    @app.post("/internal/copy-trading/calendar/windows")
    @internal_worker("CALENDAR")
    def copy_calendar_windows():
        payload = request.get_json(silent=True) or {}
        windows = payload.get("windows") or []
        if not isinstance(windows, list) or len(windows) > 2000:
            return jsonify({"ok": False, "error": "Liste de fenêtres calendrier invalide."}), 400
        conn = get_conn()
        try:
            conn.run("BEGIN")
            for item in windows:
                if not isinstance(item, dict):
                    continue
                conn.run(
                    """INSERT INTO copy_trading_calendar_events
                       (source,external_id,region,starts_at,ends_at,impact,fetched_at,payload_hash)
                       VALUES (:source,:external_id,:region,:starts_at,:ends_at,:impact,NOW(),:hash)
                       ON CONFLICT(source,external_id) DO UPDATE SET
                         region=EXCLUDED.region,starts_at=EXCLUDED.starts_at,ends_at=EXCLUDED.ends_at,
                         impact=EXCLUDED.impact,fetched_at=NOW(),payload_hash=EXCLUDED.payload_hash""",
                    source=str(item.get("source") or "")[:80],
                    external_id=str(item.get("external_id") or "")[:160],
                    region=str(item.get("region") or "ALL")[:40],
                    starts_at=item.get("starts_at"), ends_at=item.get("ends_at"),
                    impact=str(item.get("impact") or "")[:40],
                    hash=hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest(),
                )
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return jsonify({"ok": True, "accepted": len(windows)})

    return service
