"""Explicit operator allowlist for a demo pilot; simulation validation is not consent."""

import json
import os
import re
import time
from datetime import datetime, timezone

from flask import jsonify, request

from trading_journal.entitlements import academy_membership_state
from trading_journal.security import verify_worker_request
from .admin import control_allows
from . import enabled
from .service import decoded
from .access import copy_access


def bindings():
    if not enabled() or os.environ.get("COPY_DEMO_ENABLED", "false").lower() != "true":
        return {}
    data = json.loads(os.environ.get("COPY_DEMO_BINDINGS", "{}"))
    if not isinstance(data, dict):
        raise ValueError("Invalid demo bindings")
    targets = set()
    for worker, binding in data.items():
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", worker)
                or not isinstance(binding, dict) or set(binding) != {"member_code", "account_id"}
                or not isinstance(binding["member_code"], str)
                or type(binding["account_id"]) is not int or binding["account_id"] <= 0):
            raise ValueError("Invalid demo binding")
        target = (binding["member_code"], binding["account_id"])
        if target in targets:
            raise ValueError("Multiple executors for one account are forbidden")
        targets.add(target)
    return data


def pilot_authorized(member_code):
    return any(b["member_code"] == member_code for b in bindings().values())


def runtime_status(conn, member_code):
    authorized = pilot_authorized(member_code)
    rows = conn.run("""SELECT payload,received_at FROM copy_demo_runtime
        WHERE member_code=:code ORDER BY received_at DESC LIMIT 1""", code=member_code)
    result = {"authorized": authorized, "mode": "DEMO_ONLY", "online": False, "report": None}
    if rows:
        report, received = rows[0]
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        result.update(online=0 <= (datetime.now(timezone.utc) - received).total_seconds() <= 15,
                      report=decoded(report), received_at=received.isoformat())
    return result


def register_demo_routes(app, service, get_member):
    def identity():
        data = request.get_json(silent=True)
        worker = request.headers.get("X-MT5-Worker-ID", "")
        nonce = request.headers.get("X-MT5-Nonce", "")
        assigned = bindings()
        binding = assigned.get(worker)
        secrets = json.loads(os.environ.get("COPY_DEMO_WORKER_SECRETS", "{}"))
        secret = secrets.get(worker, "") if isinstance(secrets, dict) else ""
        if not secret and len(assigned) == 1:
            secret = os.environ.get("COPY_DEMO_WORKER_SECRET", "")
        if (not binding or len(secret) < 32 or not isinstance(data, dict)
                or data.get("worker_id") != worker or not 16 <= len(nonce) <= 128
                or not verify_worker_request(secret, request.method, request.path,
                    request.headers.get("X-MT5-Timestamp", ""), request.get_data(cache=True),
                    request.headers.get("X-MT5-Signature", ""), nonce=nonce)
                or not service.accept_nonce(nonce)):
            raise PermissionError()
        from .agent_backend import node_secrets, lease_valid
        if worker in node_secrets():
            conn=service.get_conn()
            try:
                if not lease_valid(conn,worker,data.get("instance_id", "")):raise PermissionError()
            finally:conn.close()
        return worker, binding, data

    @app.route("/internal/copy/demo/configuration", methods=["POST"])
    def demo_configuration():
        try:
            worker, binding, data = identity()
            conn = service.get_conn()
            try:
                from .agent_backend import node_secrets, plan_metadata
                if worker in node_secrets():
                    plan=plan_metadata(conn,worker)
                    if plan.get("state")!="READY" or data.get("plan_id")!=plan.get("plan_id"):
                        return jsonify(ok=False,error="AGENT_PLAN_CHANGED"),409
                rows = conn.run("""SELECT c.version,c.payload,c.status,a.login,a.server FROM copy_configurations c
                    JOIN trading_accounts a ON a.id=c.account_id AND a.user_id=c.member_code
                    WHERE c.member_code=:code AND c.version=(SELECT MAX(version) FROM copy_configurations
                        WHERE member_code=:code) AND c.account_id=:account
                    AND a.provider='MT5' AND a.platform='MT5'""",
                    code=binding["member_code"], account=binding["account_id"])
                reviewed = bool(rows) and control_allows(conn, binding["member_code"], rows[0][0])
            finally:
                conn.close()
            if not rows:
                return jsonify(ok=False, error="CONFIGURATION_NOT_BOUND"), 409
            version, payload, status, login, server = rows[0]
            payload = decoded(payload)
            if payload["login"] != login or payload["server"] != server:
                return jsonify(ok=False, error="ACCOUNT_IDENTITY_CHANGED"), 409
            access_allowed, access_until = copy_access(get_member(binding["member_code"]))
            allowed = (reviewed and status == "VALIDATED_SIMULATION" and payload["requested_enabled"] is True
                       and access_allowed)
            return jsonify(ok=True, mode="DEMO_ONLY", issued_at=time.time(), configuration={
                "version": version, "account_id": binding["account_id"], "login": login, "server": server,
                "settings": payload["settings"], "enabled": allowed, "access_until": access_until,
            })
        except PermissionError:
            return jsonify(ok=False), 401
        except Exception:
            app.logger.error("Demo configuration unavailable")
            return jsonify(ok=False), 503

    @app.route("/internal/copy/demo/status", methods=["POST"])
    def demo_status():
        try:
            worker, binding, data = identity()
            report = data.get("report")
            if not isinstance(report, dict) or report.get("mode") != "DEMO_ONLY":
                raise ValueError()
            if (any(type(report.get(key)) is not int or not 0 <= report[key] <= 10000
                    for key in ("open_positions", "pending_commands", "version"))
                    or not isinstance(report.get("errors"), list) or len(report["errors"]) > 50
                    or any(not isinstance(e, str) or not re.fullmatch(r"[A-Z0-9_]{1,100}", e) for e in report["errors"])
                    or not isinstance(report.get("positions"), list) or len(report["positions"]) > 50):
                raise ValueError()
            positions = []
            for position in report["positions"]:
                if not isinstance(position, dict):
                    raise ValueError()
                positions.append({key: str(position.get(key) or "")[:100] for key in
                                  ("source_id", "position_id", "symbol", "state", "version", "error")})
            clean = {key: report[key] for key in ("mode", "open_positions", "pending_commands", "version", "errors")}
            clean["positions"] = positions
            conn = service.get_conn()
            try:
                conn.run("""INSERT INTO copy_demo_runtime(worker_id,member_code,account_id,payload)
                    VALUES (:worker,:code,:account,CAST(:payload AS JSONB)) ON CONFLICT(worker_id)
                    DO UPDATE SET member_code=EXCLUDED.member_code,account_id=EXCLUDED.account_id,
                    payload=EXCLUDED.payload,received_at=NOW()""", worker=worker, code=binding["member_code"],
                    account=binding["account_id"], payload=json.dumps(clean))
            finally:
                conn.close()
            return jsonify(ok=True)
        except PermissionError:
            return jsonify(ok=False), 401
        except ValueError:
            return jsonify(ok=False, error="INVALID_STATUS"), 400
        except Exception:
            app.logger.error("Demo status unavailable")
            return jsonify(ok=False), 503
