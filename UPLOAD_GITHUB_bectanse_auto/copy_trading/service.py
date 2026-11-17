"""Transactional configuration handoff, independent of Telegram and order execution."""

import hashlib
import json
import secrets
from datetime import datetime

from .settings import validate


def digest(payload):
    # JSONB/clients may round-trip 5.0 as 5: their numerical meaning is identical.
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    return hashlib.sha256(json.dumps(canonical(payload), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


class CopyConfigurationService:
    def __init__(self, get_conn, protect_params, protect_history):
        self.get_conn = get_conn
        self.protect_params = protect_params
        self.protect_history = protect_history

    def save(self, code, data, *, toggle=False):
        if not isinstance(data, dict):
            raise ValueError("Objet JSON requis.")
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            # Serialize every configuration change for this member, including initial saves.
            members = conn.run("SELECT params,historique FROM members WHERE code=:code FOR UPDATE", code=code)
            if not members:
                raise ValueError("Membre introuvable.")
            previous = conn.run("""SELECT version,payload,status FROM copy_configurations
                WHERE member_code=:code ORDER BY version DESC LIMIT 1""", code=code)
            old = decoded(previous[0][1]) if previous else None
            if toggle and not old:
                raise ValueError("Enregistrez d’abord le compte destinataire et ses paramètres.")
            account_id = old["account_id"] if toggle else data.get("account_id")
            if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
                raise ValueError("Sélectionnez un compte MT5 destinataire.")
            accounts = conn.run("""SELECT login,server FROM trading_accounts
                WHERE id=:id AND user_id=:code AND provider='MT5' AND platform='MT5'
                FOR SHARE""", id=account_id, code=code)
            if not accounts:
                raise ValueError("Ce compte MT5 n’appartient pas au membre connecté.")
            settings = validate(old["settings"] if toggle else data)
            requested = (not old["requested_enabled"]) if toggle else bool(old and old["requested_enabled"])
            if toggle and requested and old and 'master_assignment' in old:
                # A member intention is not confirmation from a real executor.
                from .onboarding_server import status as preparation_status
                preparation = preparation_status(conn, code)
                reason = (preparation or {}).get('message') or 'La préparation du compte et le moteur réel ne sont pas confirmés.'
                raise ValueError(reason)
            payload = {"account_id": account_id, "login": accounts[0][0], "server": accounts[0][1],
                       "settings": settings, "requested_enabled": requested, "mode": "SIMULATION"}
            if old and 'master_assignment' in old:
                # A member edit must never erase an administrator's detachment.
                payload['master_assignment'] = old['master_assignment'] if old['account_id'] == account_id else None
            version = int(previous[0][0]) + 1 if previous else 1
            # A volume-only change on the same validated account needs no new manual review.
            volume_only = bool(old and not toggle and previous[0][2] == 'VALIDATED_SIMULATION'
                and old['account_id'] == account_id and old['login'] == accounts[0][0]
                and old['server'] == accounts[0][1] and settings['mode_risque'] == 'Lots fixes'
                and settings['lots'] <= 1
                and {k:v for k,v in old['settings'].items() if k not in ('lots','lots_max')}
                    == {k:v for k,v in settings.items() if k not in ('lots','lots_max')})
            next_status = 'VALIDATED_SIMULATION' if volume_only else 'PENDING'
            conn.run("""UPDATE copy_configurations SET status='SUPERSEDED',lease_token=NULL,lease_until=NULL
                WHERE member_code=:code AND status='PENDING'""", code=code)
            conn.run("""INSERT INTO copy_configurations(member_code,version,account_id,payload,digest,status,confirmed_at)
                VALUES (:code,:version,:account_id,CAST(:payload AS JSONB),:digest,:status,
                CASE WHEN :status='VALIDATED_SIMULATION' THEN NOW() ELSE NULL END)""",
                code=code, version=version, account_id=account_id,
                payload=json.dumps(payload, allow_nan=False), digest=digest(payload), status=next_status)
            if volume_only:
                conn.run("UPDATE copy_admin_controls SET version=:new,updated_at=NOW() WHERE member_code=:code AND version=:old", new=version, code=code, old=version-1)
            if 'master_assignment' in payload:
                from .onboarding_server import enqueue
                enqueue(conn,code,version,account_id,payload['master_assignment'] is not None)
            # Work from the locked row, preserving encrypted profile fields and concurrent saves.
            params = dict(decoded(members[0][0]) or {})
            params.update(settings)
            history = list(decoded(members[0][1]) or [])
            history.append({"date": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "statut": "simulation_en_attente", "copy_version": version, "params": settings})
            conn.run("UPDATE members SET params=:params,historique=:history WHERE code=:code",
                     code=code, params=json.dumps(self.protect_params(params)),
                     history=json.dumps(self.protect_history(history[-50:])))
            conn.run("COMMIT")
            return {"version": version, "status": next_status, "mode": "SIMULATION",
                    "requested_enabled": requested, "execution_enabled": False}
        except Exception:
            conn.run("ROLLBACK")
            raise
        finally:
            conn.close()

    def status(self, code):
        from .demo_backend import runtime_status
        conn = self.get_conn()
        try:
            accounts = conn.run("""SELECT id,display_name,login_masked,server FROM trading_accounts
                WHERE user_id=:code AND provider='MT5' AND platform='MT5' ORDER BY id""", code=code)
            rows = conn.run("""SELECT version,account_id,payload,status,confirmed_at,error_code
                FROM copy_configurations WHERE member_code=:code ORDER BY version DESC LIMIT 1""", code=code)
            current = None
            if rows:
                version, account_id, payload, status, confirmed, error = rows[0]
                current = {"version": version, "account_id": account_id,
                           "requested_enabled": decoded(payload)["requested_enabled"],
                           "status": status, "confirmed_at": confirmed.isoformat() if confirmed else None,
                           "error_code": error, "assigned": bool(decoded(payload).get("master_assignment"))}
            from .onboarding_server import status as preparation_status
            from .real_preparation import public_observation
            from .client_status import status as client_status
            return {"mode": "SIMULATION", "execution_enabled": False, "current": current,
                    "preparation": preparation_status(conn, code),
                    "onboarding": client_status(conn, code),
                    "real_connection": public_observation(conn,code,account_id,version) if current else None,
                    "pilot": member_runtime_status(runtime_status(conn, code)),
                    "accounts": [dict(zip(("id", "name", "login_masked", "server"), row)) for row in accounts]}
        finally:
            conn.close()

    def accept_nonce(self, nonce):
        conn = self.get_conn()
        try:
            conn.run("DELETE FROM copy_worker_nonces WHERE created_at < NOW() - INTERVAL '10 minutes'")
            return bool(conn.run("""INSERT INTO copy_worker_nonces(nonce) VALUES (:nonce)
                ON CONFLICT DO NOTHING RETURNING nonce""", nonce=nonce))
        finally:
            conn.close()

    def claim(self, worker_id):
        conn = self.get_conn()
        try:
            token = secrets.token_hex(32)
            rows = conn.run("""UPDATE copy_configurations SET worker_id=:worker,
                lease_token=:token,lease_until=NOW() + INTERVAL '60 seconds'
                WHERE (member_code,version) = (
                  SELECT member_code,version FROM copy_configurations
                  WHERE status='PENDING' AND (lease_until IS NULL OR lease_until<NOW())
                  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING member_code,version,payload,digest""", worker=worker_id, token=token)
            if not rows:
                return None
            code, version, payload, checksum = rows[0]
            return {"member_code": code, "version": version, "payload": decoded(payload),
                    "digest": checksum, "lease_token": token}
        finally:
            conn.close()

    def acknowledge(self, worker_id, data):
        if (isinstance(data.get("version"), bool) or not isinstance(data.get("version"), int)
                or data["version"] <= 0
                or any(not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 128
                       for key in ("member_code", "digest", "lease_token"))):
            raise ValueError("Identité de confirmation invalide.")
        status = data.get("status")
        if status not in ("VALIDATED_SIMULATION", "FAILED"):
            raise ValueError("Seule la validation en simulation est disponible.")
        error = data.get("error_code", "")
        if error not in ("", "INVALID_CONFIG", "DIGEST_MISMATCH") or (status == "FAILED") != bool(error):
            raise ValueError("Résultat de validation invalide.")
        conn = self.get_conn()
        try:
            rows = conn.run("""UPDATE copy_configurations AS c SET status=:status,error_code=:error,
                confirmed_at=COALESCE(c.confirmed_at,NOW()),lease_until=NULL
                WHERE c.member_code=:code AND c.version=:version AND c.worker_id=:worker
                AND c.digest=:digest AND c.lease_token=:token
                AND ((c.status='PENDING' AND c.lease_until>NOW()) OR (c.status=:status AND c.error_code=:error))
                AND NOT EXISTS (SELECT 1 FROM copy_configurations newer
                    WHERE newer.member_code=c.member_code AND newer.version>c.version)
                AND EXISTS (SELECT 1 FROM trading_accounts a WHERE a.id=c.account_id
                    AND a.user_id=c.member_code AND a.login=c.payload->>'login'
                    AND a.server=c.payload->>'server' AND a.provider='MT5' AND a.platform='MT5')
                RETURNING c.version""", status=status, error=error, code=data.get("member_code"),
                version=data.get("version"), worker=worker_id, digest=data.get("digest"), token=data.get("lease_token"))
            if not rows:
                raise ValueError("Version remplacée, compte modifié ou confirmation expirée.")
            return {"version": rows[0][0], "status": status, "execution_enabled": False}
        finally:
            conn.close()


def member_runtime_status(value):
    """Member projection: never serialize the worker account/source identities."""
    result={k:value[k] for k in ('authorized','mode','online','received_at') if k in value}
    report=value.get('report')
    result['report']=None
    if isinstance(report,dict):
        result['report']={k:report[k] for k in ('version','open_positions','pending_commands','errors') if k in report}
        result['report']['positions']=[{k:p[k] for k in ('symbol','state','error') if k in p}
            for p in report.get('positions',[]) if isinstance(p,dict)]
    return result
