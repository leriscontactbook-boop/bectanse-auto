"""Application service for accounts, sync orchestration and journal analytics."""

from __future__ import annotations

import os
import re
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .calculations import (
    analytics_breakdown,
    as_utc,
    calendar_summary,
    deal_net_pnl,
    performance_stats,
    reconstruct_positions,
    validate_timezone,
)
from .entitlements import can_add_trading_account, resolve_entitlements
from .security import CredentialCipher, EncryptedCredential


ACCOUNT_STATUSES = {
    "CONNECTED", "SYNCING", "SYNCED", "AUTH_ERROR", "BROKER_UNAVAILABLE",
    "TERMINAL_ERROR", "SYNC_ERROR", "DISCONNECTED", "ACCESS_EXPIRED", "PENDING_VERIFICATION",
}
PUBLIC_ACCOUNT_FIELDS = (
    "id", "provider", "platform", "display_name", "login_masked", "broker",
    "server", "currency", "account_type", "balance", "equity", "margin", "free_margin",
    "leverage", "access_mode", "status", "sync_status", "last_sync_at",
    "last_successful_sync_at", "last_error_code", "last_error_message", "created_at",
    "active_imported_deals",
)

ACTIVE_JOB_STATUSES = ("PENDING", "LEASED", "RUNNING", "RETRY")
RETRY_DELAYS_SECONDS = (30, 120, 300, 900, 3600)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else None


def _float(value):
    return float(value) if value is not None else None


def _safe_decimal(value, *, minimum=None, maximum=None) -> Decimal:
    try:
        number = Decimal(str(value or 0))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric value is invalid") from exc
    if not number.is_finite() or (minimum is not None and number < minimum) or (
        maximum is not None and number > maximum
    ):
        raise ValueError("numeric value is outside accepted limits")
    return number


class JournalService:
    def __init__(self, get_conn, get_member, logger):
        self.get_conn = get_conn
        self.get_member = get_member
        self.logger = logger
        self.sync_interval_seconds = max(60, int(os.environ.get("MT5_SYNC_INTERVAL_SECONDS", "300")))
        self.sync_overlap_seconds = max(0, int(os.environ.get("MT5_SYNC_OVERLAP_SECONDS", "900")))
        self.manual_cooldown_seconds = max(30, int(os.environ.get("MT5_MANUAL_SYNC_COOLDOWN_SECONDS", "60")))
        self.daily_reconciliation_days = max(1, int(os.environ.get("MT5_DAILY_RECONCILIATION_DAYS", "7")))
        self.weekly_reconciliation_days = max(
            self.daily_reconciliation_days,
            int(os.environ.get("MT5_WEEKLY_RECONCILIATION_DAYS", "90")),
        )
        self.lease_seconds = max(60, int(os.environ.get("MT5_JOB_LEASE_SECONDS", "300")))

    def _cipher(self) -> CredentialCipher:
        return CredentialCipher.from_environment()

    @staticmethod
    def _credential_aad(account_id: int, user_id: str) -> str:
        return f"trading-account:{account_id}:{user_id}"

    def profile(self, user_id: str) -> dict:
        conn = self.get_conn()
        try:
            rows = conn.run(
                "SELECT timezone,onboarding_completed_at FROM trading_profiles WHERE user_id=:user_id",
                user_id=user_id,
            )
            if rows:
                return {"timezone": rows[0][0], "onboarding_completed_at": rows[0][1]}
            conn.run("INSERT INTO trading_profiles (user_id) VALUES (:user_id) ON CONFLICT DO NOTHING", user_id=user_id)
            return {"timezone": "Europe/Paris", "onboarding_completed_at": None}
        finally:
            conn.close()

    def update_timezone(self, user_id: str, timezone_name: str) -> dict:
        try:
            timezone_name = validate_timezone(timezone_name)
        except ValueError as exc:
            raise ValueError("Fuseau horaire invalide.") from exc
        conn = self.get_conn()
        try:
            conn.run("""INSERT INTO trading_profiles (user_id,timezone)
                VALUES (:user_id,:timezone) ON CONFLICT (user_id) DO UPDATE SET
                timezone=:timezone,updated_at=NOW()""", user_id=user_id, timezone=timezone_name)
        finally:
            conn.close()
        return {"timezone": timezone_name}

    def entitlements(self, user_id: str) -> dict:
        conn = self.get_conn()
        try:
            rows = conn.run("""SELECT plan,subscription_status,current_period_end
                FROM trading_subscriptions WHERE user_id=:user_id""", user_id=user_id)
            subscription = (
                {"plan": rows[0][0], "subscription_status": rows[0][1], "current_period_end": rows[0][2]}
                if rows else None
            )
            grant_rows = conn.run("""SELECT feature_key,source,status,valid_from,valid_until
                FROM product_entitlements WHERE user_id=:user_id
                AND status='ACTIVE' AND valid_from<=NOW()
                AND (valid_until IS NULL OR valid_until>NOW())""", user_id=user_id)
            grants = [dict(zip(
                ("feature_key", "source", "status", "valid_from", "valid_until"), row
            )) for row in grant_rows]
        finally:
            conn.close()
        return resolve_entitlements(subscription, self.get_member(user_id), grants).as_dict()

    def require_access(self, user_id: str) -> dict:
        entitlements = self.entitlements(user_id)
        if not entitlements["allowed"]:
            raise PermissionError(
                "Votre Journal n’est plus actif. Continuez avec Bectanse Journal pour retrouver vos données."
            )
        return entitlements

    @staticmethod
    def _serialize_account(row: tuple) -> dict:
        result = dict(zip(PUBLIC_ACCOUNT_FIELDS, row))
        for key in ("balance", "equity", "margin", "free_margin"):
            result[key] = _float(result[key])
        for key in ("last_sync_at", "last_successful_sync_at", "created_at"):
            result[key] = _iso(result[key])
        return result

    def list_accounts(self, user_id: str) -> list[dict]:
        conn = self.get_conn()
        try:
            account_fields = ",".join(f"a.{field}" for field in PUBLIC_ACCOUNT_FIELDS[:-1])
            rows = conn.run(f"""SELECT {account_fields},COALESCE((SELECT j.imported_deals
                FROM trading_sync_jobs j WHERE j.trading_account_id=a.id
                ORDER BY j.created_at DESC LIMIT 1),0) AS active_imported_deals
                FROM trading_accounts a WHERE a.user_id=:user_id ORDER BY a.created_at""", user_id=user_id)
            return [self._serialize_account(row) for row in rows]
        finally:
            conn.close()

    def get_account(self, user_id: str, account_id: int) -> dict | None:
        conn = self.get_conn()
        try:
            account_fields = ",".join(f"a.{field}" for field in PUBLIC_ACCOUNT_FIELDS[:-1])
            rows = conn.run(f"""SELECT {account_fields},COALESCE((SELECT j.imported_deals
                FROM trading_sync_jobs j WHERE j.trading_account_id=a.id
                ORDER BY j.created_at DESC LIMIT 1),0) AS active_imported_deals
                FROM trading_accounts a WHERE a.id=:id AND a.user_id=:user_id""", id=account_id, user_id=user_id)
            return self._serialize_account(rows[0]) if rows else None
        finally:
            conn.close()

    def create_account(self, user_id: str, payload: dict) -> dict:
        platform = str(payload.get("platform") or "MT5").upper().strip()
        if platform != "MT5":
            raise ValueError("MetaTrader 5 est la seule plateforme disponible en V1.")
        login = re.sub(r"\s+", "", str(payload.get("login") or ""))
        server = str(payload.get("server") or "").strip()
        password = str(payload.get("password") or "")
        display_name = str(payload.get("display_name") or "").strip()[:80]
        if not re.fullmatch(r"[0-9]{3,20}", login):
            raise ValueError("Vérifiez votre numéro de compte MetaTrader.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._\-/]{1,118}[A-Za-z0-9]", server):
            raise ValueError("Le serveur MetaTrader indiqué n’est pas valide.")
        if len(password) < 4 or len(password) > 256:
            raise ValueError("Le mot de passe investisseur n’est pas valide.")

        member = self.get_member(user_id) or {}
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            count = int(conn.run("SELECT COUNT(*) FROM trading_accounts WHERE user_id=:user_id AND status<>'DISCONNECTED'", user_id=user_id)[0][0])
            subscription_rows = conn.run("SELECT plan,subscription_status,current_period_end FROM trading_subscriptions WHERE user_id=:user_id", user_id=user_id)
            subscription = (
                {"plan": subscription_rows[0][0], "subscription_status": subscription_rows[0][1], "current_period_end": subscription_rows[0][2]}
                if subscription_rows else None
            )
            grant_rows = conn.run("""SELECT feature_key,source,status,valid_from,valid_until
                FROM product_entitlements WHERE user_id=:user_id
                AND status='ACTIVE' AND valid_from<=NOW()
                AND (valid_until IS NULL OR valid_until>NOW())""", user_id=user_id)
            grants = [dict(zip(
                ("feature_key", "source", "status", "valid_from", "valid_until"), row
            )) for row in grant_rows]
            entitlements = resolve_entitlements(subscription, member, grants)
            if not entitlements.allowed:
                conn.run("ROLLBACK")
                raise PermissionError("Un abonnement Journal actif ou une adhésion Académie est requis.")
            existing = conn.run("""SELECT id FROM trading_accounts
                WHERE user_id=:user_id AND provider='MT5' AND server=:server AND login=:login""",
                user_id=user_id, server=server, login=login)
            if not existing and not can_add_trading_account(entitlements, count):
                conn.run("ROLLBACK")
                raise PermissionError("Votre formule a atteint sa limite de comptes connectés.")
            if existing:
                account_id = int(existing[0][0])
                conn.run("""UPDATE trading_accounts SET display_name=:display_name,status='PENDING_VERIFICATION',
                    sync_status='PENDING',last_error_code='',last_error_message='',updated_at=NOW()
                    WHERE id=:id AND user_id=:user_id""", id=account_id, user_id=user_id, display_name=display_name)
            else:
                inserted = conn.run("""INSERT INTO trading_accounts
                    (user_id,provider,platform,display_name,login,login_masked,server,status,sync_status)
                    VALUES (:user_id,'MT5','MT5',:display_name,:login,:masked,:server,'PENDING_VERIFICATION','PENDING')
                    RETURNING id""", user_id=user_id, display_name=display_name, login=login,
                    masked="••••" + login[-4:], server=server)
                account_id = int(inserted[0][0])
            encrypted = self._cipher().encrypt(password, self._credential_aad(account_id, user_id))
            key_version = max(1, int(os.environ.get("MT5_CREDENTIAL_KEY_VERSION", "1")))
            conn.run("""INSERT INTO trading_credentials
                (trading_account_id,encrypted_password,nonce,auth_tag,key_version)
                VALUES (:account_id,:ciphertext,:nonce,:tag,:key_version)
                ON CONFLICT (trading_account_id) DO UPDATE SET
                encrypted_password=EXCLUDED.encrypted_password,nonce=EXCLUDED.nonce,
                auth_tag=EXCLUDED.auth_tag,key_version=EXCLUDED.key_version,updated_at=NOW()""",
                account_id=account_id, ciphertext=encrypted.ciphertext, nonce=encrypted.nonce,
                tag=encrypted.tag, key_version=key_version)
            conn.run("""UPDATE trading_accounts SET status='PENDING_VERIFICATION',access_mode='PENDING',
                sync_status='PENDING',updated_at=NOW() WHERE id=:id""", id=account_id)
            job_id = self._enqueue(conn, account_id, "FULL_HISTORY_SYNC", priority=100)
            self._audit(conn, user_id, account_id, "ACCOUNT_CONNECTED", {"provider": "MT5", "server": server})
            self._audit(conn, user_id, account_id, "CREDENTIAL_UPDATED", {"key_version": key_version})
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
        account = self.get_account(user_id, account_id)
        account["sync_job_id"] = job_id
        account["entitlements"] = entitlements.as_dict()
        return account

    @staticmethod
    def _audit(conn, user_id: str, account_id: int | None, action: str, metadata: dict | None = None) -> None:
        import json
        conn.run("""INSERT INTO trading_audit_logs
            (user_id,trading_account_id,action,metadata) VALUES (:user_id,:account_id,:action,CAST(:metadata AS jsonb))""",
            user_id=str(user_id or "")[:100], account_id=account_id, action=action[:80],
            metadata=json.dumps(metadata or {}, separators=(",", ":")))

    def _enqueue(self, conn, account_id: int, job_type: str, priority: int = 10,
                 range_from=None, range_to=None) -> str:
        job_id = str(uuid.uuid4())
        inserted = conn.run("""INSERT INTO trading_sync_jobs
            (id,trading_account_id,job_type,status,priority,range_from,range_to)
            VALUES (:id,:account_id,:job_type,'PENDING',:priority,:range_from,:range_to)
            ON CONFLICT DO NOTHING RETURNING id""", id=job_id, account_id=account_id,
            job_type=job_type, priority=priority, range_from=range_from, range_to=range_to)
        return str(inserted[0][0]) if inserted else ""

    def request_sync(self, user_id: str, account_id: int) -> dict:
        self.require_access(user_id)
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run("""SELECT status,last_manual_sync_at FROM trading_accounts
                WHERE id=:id AND user_id=:user_id FOR UPDATE""", id=account_id, user_id=user_id)
            if not rows:
                conn.run("ROLLBACK")
                raise LookupError("Compte introuvable.")
            if rows[0][0] == "DISCONNECTED":
                conn.run("ROLLBACK")
                raise ValueError("Reconnectez le compte avant de le synchroniser.")
            last_manual = rows[0][1]
            if last_manual and datetime.now(timezone.utc) - as_utc(last_manual) < timedelta(seconds=self.manual_cooldown_seconds):
                remaining = self.manual_cooldown_seconds - int((datetime.now(timezone.utc) - as_utc(last_manual)).total_seconds())
                conn.run("ROLLBACK")
                return {"queued": False, "cooldown_seconds": max(1, remaining)}
            job_id = self._enqueue(conn, account_id, "MANUAL", priority=80)
            conn.run("UPDATE trading_accounts SET last_manual_sync_at=NOW(),sync_status='PENDING',updated_at=NOW() WHERE id=:id", id=account_id)
            self._audit(conn, user_id, account_id, "MANUAL_SYNC_REQUESTED", {"queued": bool(job_id)})
            conn.run("COMMIT")
            return {"queued": bool(job_id), "job_id": job_id, "cooldown_seconds": self.manual_cooldown_seconds}
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

    def disconnect_account(self, user_id: str, account_id: int, delete_data: bool) -> bool:
        conn = self.get_conn()
        try:
            if delete_data:
                self._audit(conn, user_id, account_id, "ACCOUNT_DATA_DELETED")
                rows = conn.run("DELETE FROM trading_accounts WHERE id=:id AND user_id=:user_id RETURNING id", id=account_id, user_id=user_id)
            else:
                rows = conn.run("""UPDATE trading_accounts SET status='DISCONNECTED',sync_status='DISCONNECTED',
                    updated_at=NOW() WHERE id=:id AND user_id=:user_id RETURNING id""",
                    id=account_id, user_id=user_id)
                if rows:
                    conn.run("DELETE FROM trading_credentials WHERE trading_account_id=:id", id=account_id)
                    self._audit(conn, user_id, account_id, "ACCOUNT_DISCONNECTED")
            return bool(rows)
        finally:
            conn.close()

    def enqueue_due_accounts(self) -> int:
        conn = self.get_conn()
        try:
            conn.run("""UPDATE trading_sync_jobs SET status='RETRY',worker_id='',instance_id='',
                locked_at=NULL,heartbeat_at=NULL,lease_expires_at=NULL,not_before=NOW(),
                last_error_code='WORKER_TIMEOUT',last_error_message='Lease expiré; relance automatique.'
                WHERE status IN ('LEASED','RUNNING') AND lease_expires_at < NOW()""")
            due = conn.run("""SELECT a.id,a.last_reconciliation_at,a.last_deep_reconciliation_at
                FROM trading_accounts a JOIN trading_credentials c ON c.trading_account_id=a.id
                WHERE a.status NOT IN ('DISCONNECTED','ACCESS_EXPIRED','AUTH_ERROR')
                AND (a.last_successful_sync_at IS NULL OR
                     a.last_successful_sync_at < NOW() - (:seconds * INTERVAL '1 second'))
                AND NOT EXISTS (SELECT 1 FROM trading_sync_jobs j
                    WHERE j.trading_account_id=a.id AND j.status IN ('PENDING','LEASED','RUNNING','RETRY'))""",
                seconds=self.sync_interval_seconds)
            created = 0
            now = datetime.now(timezone.utc)
            for account_id, last_reconciliation, last_deep in due:
                if not last_deep or now - as_utc(last_deep) >= timedelta(days=7):
                    job_type, priority = "WEEKLY_RECONCILIATION", 60
                elif not last_reconciliation or now - as_utc(last_reconciliation) >= timedelta(days=1):
                    job_type, priority = "DAILY_RECONCILIATION", 50
                else:
                    job_type, priority = "INCREMENTAL", 10
                if self._enqueue(conn, int(account_id), job_type, priority=priority):
                    created += 1
            return created
        finally:
            conn.close()

    def accept_worker_nonce(self, worker_id: str, nonce: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,120}", nonce or ""):
            return False
        conn = self.get_conn()
        try:
            conn.run("DELETE FROM trading_worker_nonces WHERE created_at<NOW()-INTERVAL '10 minutes'")
            rows = conn.run("""INSERT INTO trading_worker_nonces (nonce,worker_id)
                VALUES (:nonce,:worker_id) ON CONFLICT DO NOTHING RETURNING nonce""",
                nonce=nonce, worker_id=worker_id[:100])
            return bool(rows)
        finally:
            conn.close()

    def worker_heartbeat(self, worker_id: str, instance_id: str, payload: dict) -> dict:
        status = str(payload.get("status") or "ONLINE").upper()
        if status not in {"ONLINE", "BUSY", "STOPPING"}:
            raise ValueError("worker status invalid")
        terminal_fingerprint = str(payload.get("terminal_fingerprint") or "")[:128]
        version = str(payload.get("version") or "")[:40]
        current_job_id = str(payload.get("current_job_id") or "")[:100]
        conn = self.get_conn()
        try:
            conn.run("""INSERT INTO trading_workers
                (worker_id,instance_id,status,version,terminal_fingerprint,current_job_id)
                VALUES (:worker_id,:instance_id,:status,:version,:fingerprint,:job_id)
                ON CONFLICT (worker_id) DO UPDATE SET instance_id=EXCLUDED.instance_id,
                status=EXCLUDED.status,version=EXCLUDED.version,
                terminal_fingerprint=EXCLUDED.terminal_fingerprint,
                current_job_id=EXCLUDED.current_job_id,last_seen_at=NOW(),updated_at=NOW()""",
                worker_id=worker_id[:100], instance_id=instance_id[:100], status=status,
                version=version, fingerprint=terminal_fingerprint, job_id=current_job_id)
            return {"accepted": True}
        finally:
            conn.close()

    def worker_health(self) -> dict:
        conn = self.get_conn()
        try:
            workers = conn.run("""SELECT worker_id,instance_id,status,last_seen_at,current_job_id
                FROM trading_workers ORDER BY last_seen_at DESC""")
            queue = conn.run("""SELECT
                COUNT(*) FILTER (WHERE status IN ('PENDING','RETRY')),
                COUNT(*) FILTER (WHERE status IN ('LEASED','RUNNING')),
                COUNT(*) FILTER (WHERE status IN ('FAILED','DEAD') AND created_at>NOW()-INTERVAL '24 hours')
                FROM trading_sync_jobs""")[0]
        finally:
            conn.close()
        now = datetime.now(timezone.utc)
        serialized = []
        for worker_id, instance_id, status, last_seen_at, current_job_id in workers:
            seen = as_utc(last_seen_at)
            online = (now - seen).total_seconds() <= 90
            serialized.append({
                "worker_id": worker_id,
                "instance_id": instance_id,
                "status": status if online else "OFFLINE",
                "online": online,
                "last_seen_at": _iso(seen),
                "busy": online and status == "BUSY",
                "current_job_id": current_job_id or None,
            })
        return {
            "status": "ok" if any(row["online"] for row in serialized) else "degraded",
            "online": sum(1 for row in serialized if row["online"]),
            "busy": sum(1 for row in serialized if row["busy"]),
            "queue_depth": int(queue[0] or 0),
            "claimed_jobs": int(queue[1] or 0),
            "failed_jobs_24h": int(queue[2] or 0),
            "workers": serialized,
        }

    def claim_job(self, worker_id: str, instance_id: str) -> dict | None:
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run("""SELECT j.id,j.trading_account_id,j.job_type,j.attempts,
                a.user_id,a.login,a.server,c.encrypted_password,c.nonce,c.auth_tag,c.key_version,
                a.last_successful_sync_at,j.range_from,j.range_to
                FROM trading_sync_jobs j JOIN trading_accounts a ON a.id=j.trading_account_id
                JOIN trading_credentials c ON c.trading_account_id=a.id
                LEFT JOIN trading_server_circuits circuit ON circuit.server=a.server
                WHERE j.status IN ('PENDING','RETRY') AND j.not_before<=NOW()
                AND a.status NOT IN ('DISCONNECTED','AUTH_ERROR','ACCESS_EXPIRED')
                AND (circuit.opened_until IS NULL OR circuit.opened_until<=NOW())
                ORDER BY j.priority DESC,j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1""")
            if not rows:
                conn.run("COMMIT")
                return None
            (job_id, account_id, job_type, attempts, user_id, login, server,
             ciphertext, nonce, tag, key_version, last_successful, requested_from, requested_to) = rows[0]
            subscription_rows = conn.run("""SELECT plan,subscription_status,current_period_end
                FROM trading_subscriptions WHERE user_id=:user_id""", user_id=user_id)
            subscription = (
                {"plan": subscription_rows[0][0], "subscription_status": subscription_rows[0][1],
                 "current_period_end": subscription_rows[0][2]}
                if subscription_rows else None
            )
            grant_rows = conn.run("""SELECT feature_key,source,status,valid_from,valid_until
                FROM product_entitlements WHERE user_id=:user_id
                AND status='ACTIVE' AND valid_from<=NOW()
                AND (valid_until IS NULL OR valid_until>NOW())""", user_id=user_id)
            grants = [dict(zip(
                ("feature_key", "source", "status", "valid_from", "valid_until"), row
            )) for row in grant_rows]
            entitlements = resolve_entitlements(subscription, self.get_member(str(user_id)), grants)
            if not entitlements.allowed:
                conn.run("""UPDATE trading_sync_jobs SET status='DEAD',completed_at=NOW(),finished_at=NOW(),
                    last_error_code='ACCESS_EXPIRED',last_error_message='Accès Journal expiré.'
                    WHERE id=:id""", id=job_id)
                conn.run("""UPDATE trading_accounts SET status='ACCESS_EXPIRED',
                    sync_status='ACCESS_EXPIRED',updated_at=NOW() WHERE id=:id""", id=account_id)
                conn.run("COMMIT")
                return None
            password = self._cipher().decrypt(
                EncryptedCredential(bytes(ciphertext), bytes(nonce), bytes(tag)),
                self._credential_aad(int(account_id), str(user_id)),
            )
            now = datetime.now(timezone.utc)
            if requested_from:
                date_from = as_utc(requested_from)
            elif str(job_type) == "WEEKLY_RECONCILIATION":
                date_from = now - timedelta(days=self.weekly_reconciliation_days)
            elif str(job_type) == "DAILY_RECONCILIATION":
                date_from = now - timedelta(days=self.daily_reconciliation_days)
            elif last_successful:
                date_from = as_utc(last_successful) - timedelta(seconds=self.sync_overlap_seconds)
            elif entitlements.historical_days:
                date_from = now - timedelta(days=entitlements.historical_days)
            else:
                date_from = datetime(2000, 1, 1, tzinfo=timezone.utc)
            date_to = as_utc(requested_to) if requested_to else now
            run_id = str(uuid.uuid4())
            conn.run("""UPDATE trading_sync_jobs SET status='LEASED',attempts=attempts+1,
                worker_id=:worker_id,instance_id=:instance_id,locked_at=NOW(),heartbeat_at=NOW(),
                lease_expires_at=NOW()+(:lease_seconds*INTERVAL '1 second'),started_at=COALESCE(started_at,NOW()),
                range_from=:range_from,range_to=:range_to,imported_deals=0,received_deals=0
                WHERE id=:id""", id=job_id, worker_id=worker_id[:100], instance_id=instance_id[:100],
                lease_seconds=self.lease_seconds, range_from=date_from, range_to=date_to)
            conn.run("DELETE FROM trading_sync_batches WHERE sync_job_id=:id", id=job_id)
            conn.run("""INSERT INTO trading_sync_runs
                (id,sync_job_id,trading_account_id,worker_id,job_type,range_from,range_to)
                VALUES (:run_id,:job_id,:account_id,:worker_id,:job_type,:range_from,:range_to)""",
                run_id=run_id, job_id=job_id, account_id=account_id, worker_id=worker_id[:100],
                job_type=job_type, range_from=date_from, range_to=date_to)
            conn.run("UPDATE trading_accounts SET status='SYNCING',sync_status='CONNECTING',last_sync_at=NOW(),updated_at=NOW() WHERE id=:id", id=account_id)
            conn.run("""UPDATE trading_workers SET status='BUSY',current_job_id=:job_id,
                last_seen_at=NOW(),updated_at=NOW() WHERE worker_id=:worker_id""",
                job_id=job_id, worker_id=worker_id[:100])
            conn.run("""INSERT INTO trading_sync_events
                (trading_account_id,sync_job_id,event_name,worker_id)
                VALUES (:account_id,:job_id,'sync_started',:worker_id)""",
                account_id=account_id, job_id=job_id, worker_id=worker_id[:100])
            conn.run("COMMIT")
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

        return {
            "id": str(job_id), "job_type": str(job_type), "account_id": int(account_id),
            "platform": "MT5", "login": str(login), "server": str(server), "password": password,
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "attempt": int(attempts or 0) + 1, "run_id": run_id,
            "credential_key_version": int(key_version or 1),
        }

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        conn = self.get_conn()
        try:
            rows = conn.run("""UPDATE trading_sync_jobs SET heartbeat_at=NOW(),status='RUNNING',
                lease_expires_at=NOW()+(:lease_seconds*INTERVAL '1 second')
                WHERE id=:id AND worker_id=:worker_id AND status IN ('LEASED','RUNNING') RETURNING id""",
                id=job_id, worker_id=worker_id, lease_seconds=self.lease_seconds)
            return bool(rows)
        finally:
            conn.close()

    def import_batch(self, job_id: str, worker_id: str, deals: list[dict], batch_id: str = "") -> dict:
        if not isinstance(deals, list) or len(deals) > 5000:
            raise ValueError("Lot de synchronisation invalide.")
        conn = self.get_conn()
        inserted_count = 0
        try:
            conn.run("BEGIN")
            jobs = conn.run("""SELECT trading_account_id FROM trading_sync_jobs
                WHERE id=:id AND worker_id=:worker_id AND status IN ('LEASED','RUNNING') FOR UPDATE""",
                id=job_id, worker_id=worker_id)
            if not jobs:
                conn.run("ROLLBACK")
                raise LookupError("Tâche de synchronisation introuvable.")
            account_id = int(jobs[0][0])
            if not batch_id:
                tickets = ",".join(str(int(item.get("ticket") or 0)) for item in deals)
                batch_id = hashlib.sha256(tickets.encode("ascii")).hexdigest()[:32]
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,100}", str(batch_id)):
                raise ValueError("Identifiant de lot invalide.")
            receipt = conn.run("""INSERT INTO trading_sync_batches
                (sync_job_id,batch_id,received_count) VALUES (:job_id,:batch_id,:received)
                ON CONFLICT DO NOTHING RETURNING batch_id""", job_id=job_id,
                batch_id=batch_id, received=len(deals))
            if not receipt:
                existing = conn.run("""SELECT received_count,inserted_count FROM trading_sync_batches
                    WHERE sync_job_id=:job_id AND batch_id=:batch_id""", job_id=job_id, batch_id=batch_id)[0]
                conn.run("COMMIT")
                return {"accepted": int(existing[0]), "inserted": int(existing[1]), "duplicate_batch": True}
            for deal in deals:
                normalized = self._normalize_deal(deal)
                inserted = conn.run("""INSERT INTO trading_deals
                    (trading_account_id,mt5_deal_ticket,mt5_order_ticket,mt5_position_id,
                     symbol,deal_type,entry_type,reason,is_trading_deal,volume,price,profit,
                     commission,swap,fee,magic_number,comment,executed_at)
                    VALUES (:account_id,:ticket,:order_ticket,:position_id,:symbol,:deal_type,
                     :entry_type,:reason,:is_trading,:volume,:price,:profit,:commission,:swap,
                     :fee,:magic,:comment,:executed_at)
                    ON CONFLICT (trading_account_id,mt5_deal_ticket) DO UPDATE SET
                     mt5_order_ticket=EXCLUDED.mt5_order_ticket,mt5_position_id=EXCLUDED.mt5_position_id,
                     symbol=EXCLUDED.symbol,deal_type=EXCLUDED.deal_type,entry_type=EXCLUDED.entry_type,
                     reason=EXCLUDED.reason,is_trading_deal=EXCLUDED.is_trading_deal,
                     volume=EXCLUDED.volume,price=EXCLUDED.price,profit=EXCLUDED.profit,
                     commission=EXCLUDED.commission,swap=EXCLUDED.swap,fee=EXCLUDED.fee,
                     magic_number=EXCLUDED.magic_number,comment=EXCLUDED.comment,
                     executed_at=EXCLUDED.executed_at,updated_at=NOW()
                    RETURNING (xmax = 0)""", account_id=account_id, **normalized)
                if inserted and bool(inserted[0][0]):
                    inserted_count += 1
            conn.run("""UPDATE trading_sync_jobs SET imported_deals=imported_deals+:count,
                received_deals=received_deals+:received,heartbeat_at=NOW(),status='RUNNING',
                lease_expires_at=NOW()+(:lease_seconds*INTERVAL '1 second') WHERE id=:id""",
                count=inserted_count, received=len(deals), lease_seconds=self.lease_seconds, id=job_id)
            conn.run("""UPDATE trading_sync_runs SET deals_received=deals_received+:received,
                deals_inserted=deals_inserted+:inserted WHERE sync_job_id=:id AND status='RUNNING'""",
                received=len(deals), inserted=inserted_count, id=job_id)
            conn.run("""UPDATE trading_sync_batches SET inserted_count=:inserted
                WHERE sync_job_id=:job_id AND batch_id=:batch_id""",
                inserted=inserted_count, job_id=job_id, batch_id=batch_id)
            conn.run("""UPDATE trading_accounts SET sync_status='IMPORTING',updated_at=NOW()
                WHERE id=:account_id""", account_id=account_id)
            conn.run("COMMIT")
            return {"accepted": len(deals), "inserted": inserted_count}
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _normalize_deal(deal: dict) -> dict:
        if not isinstance(deal, dict):
            raise ValueError("Deal invalide")
        ticket = int(deal.get("ticket") or 0)
        if ticket <= 0:
            raise ValueError("Ticket MT5 invalide")
        deal_type = str(deal.get("deal_type") or "")[:30].upper()
        entry_type = str(deal.get("entry_type") or "")[:20].upper()
        executed_at = as_utc(str(deal.get("executed_at") or ""))
        if executed_at > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError("Horodatage deal invalide")
        return {
            "ticket": ticket,
            "order_ticket": int(deal.get("order_ticket") or 0) or None,
            "position_id": int(deal.get("position_id") or 0) or None,
            "symbol": str(deal.get("symbol") or "")[:40].upper(),
            "deal_type": deal_type,
            "entry_type": entry_type,
            "reason": str(deal.get("reason") or "")[:40].upper(),
            "is_trading": bool(deal.get("is_trading_deal")) and deal_type in {"BUY", "SELL"},
            "volume": _safe_decimal(deal.get("volume"), minimum=Decimal("0"), maximum=Decimal("1000000")),
            "price": _safe_decimal(deal.get("price"), minimum=Decimal("0"), maximum=Decimal("1e15")),
            "profit": _safe_decimal(deal.get("profit"), minimum=Decimal("-1e15"), maximum=Decimal("1e15")),
            "commission": _safe_decimal(deal.get("commission"), minimum=Decimal("-1e15"), maximum=Decimal("1e15")),
            "swap": _safe_decimal(deal.get("swap"), minimum=Decimal("-1e15"), maximum=Decimal("1e15")),
            "fee": _safe_decimal(deal.get("fee"), minimum=Decimal("-1e15"), maximum=Decimal("1e15")),
            "magic": int(deal.get("magic_number") or 0) or None,
            "comment": str(deal.get("comment") or "")[:500],
            "executed_at": executed_at,
        }

    def complete_job(self, job_id: str, worker_id: str, account: dict, duration_ms: int,
                     received_deals: int | None = None, source_pnl=None) -> dict:
        required = {"broker", "server", "currency", "balance", "equity", "margin", "free_margin", "leverage", "access_mode"}
        if not isinstance(account, dict) or not required.issubset(account):
            raise ValueError("Informations de compte incomplètes.")
        if account.get("access_mode") != "READ_ONLY" and os.environ.get("MT5_REQUIRE_READ_ONLY", "true").lower() not in {"0", "false", "no"}:
            raise PermissionError("Le compte n’est pas connecté en lecture seule.")
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run("""SELECT j.trading_account_id,j.imported_deals,j.received_deals,
                j.job_type,j.range_from,j.range_to,a.login,a.server
                FROM trading_sync_jobs j JOIN trading_accounts a ON a.id=j.trading_account_id
                WHERE j.id=:id AND j.worker_id=:worker_id AND j.status IN ('LEASED','RUNNING') FOR UPDATE""",
                id=job_id, worker_id=worker_id)
            if not rows:
                conn.run("ROLLBACK")
                raise LookupError("Tâche de synchronisation introuvable.")
            (raw_account_id, imported, stored_received, job_type, range_from, range_to,
             expected_login, expected_server) = rows[0]
            account_id = int(raw_account_id)
            imported = int(imported or 0)
            received = int(stored_received or 0)
            if received_deals is not None and int(received_deals) != received:
                raise ValueError("Le nombre de deals reçu ne correspond pas aux lots importés.")
            if str(account.get("login") or "") != str(expected_login):
                raise ValueError("Le compte MT5 connecté ne correspond pas au compte demandé.")
            if str(account.get("server") or "").lower() != str(expected_server or "").lower():
                raise ValueError("Le serveur MT5 connecté ne correspond pas au serveur demandé.")
            values = {
                "broker": str(account["broker"] or "")[:120],
                "server": str(account["server"] or "")[:120],
                "currency": str(account["currency"] or "")[:12].upper(),
                "account_type": str(account.get("account_type") or "")[:40],
                "balance": _safe_decimal(account["balance"]),
                "equity": _safe_decimal(account["equity"]),
                "margin": _safe_decimal(account["margin"]),
                "free_margin": _safe_decimal(account["free_margin"]),
                "leverage": max(0, int(account["leverage"] or 0)),
                "access_mode": str(account["access_mode"]),
            }
            conn.run("""UPDATE trading_accounts SET broker=:broker,server=:server,currency=:currency,account_type=:account_type,
                balance=:balance,equity=:equity,margin=:margin,free_margin=:free_margin,
                leverage=:leverage,access_mode=:access_mode,status='SYNCED',sync_status='SYNCED',
                last_successful_sync_at=NOW(),last_error_code='',last_error_message='',updated_at=NOW()
                WHERE id=:account_id""", account_id=account_id, **values)
            conn.run("""INSERT INTO trading_account_snapshots
                (trading_account_id,balance,equity,margin,free_margin)
                VALUES (:account_id,:balance,:equity,:margin,:free_margin)""", account_id=account_id, **values)
            db_rollup = conn.run("""SELECT COUNT(*),COALESCE(SUM(profit+commission+swap+fee),0)
                FROM trading_deals WHERE trading_account_id=:account_id
                AND executed_at>=:range_from AND executed_at<=:range_to""",
                account_id=account_id, range_from=range_from, range_to=range_to)[0]
            db_count, db_pnl = int(db_rollup[0] or 0), Decimal(str(db_rollup[1] or 0))
            pnl_delta = Decimal("0") if source_pnl is None else db_pnl - _safe_decimal(source_pnl)
            recon_status = "PASS" if source_pnl is None or abs(pnl_delta) <= Decimal("0.01") else "MISMATCH"
            if str(job_type) in {"DAILY_RECONCILIATION", "WEEKLY_RECONCILIATION"}:
                conn.run("""INSERT INTO trading_reconciliation_reports
                    (trading_account_id,sync_job_id,range_from,range_to,mt5_count,db_count,
                     missing_count,repaired_count,pnl_delta,status)
                    VALUES (:account_id,:job_id,:range_from,:range_to,:mt5_count,:db_count,
                     0,:repaired,:pnl_delta,:status)""", account_id=account_id, job_id=job_id,
                    range_from=range_from, range_to=range_to, mt5_count=received, db_count=db_count,
                    repaired=imported, pnl_delta=pnl_delta, status=recon_status)
            reconcile_columns = ""
            if str(job_type) == "DAILY_RECONCILIATION":
                reconcile_columns = ",last_reconciliation_at=NOW()"
            elif str(job_type) == "WEEKLY_RECONCILIATION":
                reconcile_columns = ",last_reconciliation_at=NOW(),last_deep_reconciliation_at=NOW()"
            if reconcile_columns:
                conn.run(f"UPDATE trading_accounts SET updated_at=NOW(){reconcile_columns} WHERE id=:id", id=account_id)
            conn.run("""UPDATE trading_sync_jobs SET status='SUCCESS',completed_at=NOW(),finished_at=NOW(),
                heartbeat_at=NOW(),lease_expires_at=NULL WHERE id=:id""", id=job_id)
            conn.run("""UPDATE trading_sync_runs SET status='SUCCESS',finished_at=NOW()
                WHERE sync_job_id=:id AND status='RUNNING'""", id=job_id)
            conn.run("""UPDATE trading_workers SET status='ONLINE',current_job_id='',
                last_seen_at=NOW(),updated_at=NOW() WHERE worker_id=:worker_id""",
                worker_id=worker_id[:100])
            conn.run("""INSERT INTO trading_sync_events
                (trading_account_id,sync_job_id,event_name,worker_id,duration_ms,deals_count)
                VALUES (:account_id,:job_id,'sync_success',:worker_id,:duration,:count)""",
                account_id=account_id, job_id=job_id, worker_id=worker_id[:100],
                duration=max(0, int(duration_ms or 0)), count=imported)
            conn.run("""INSERT INTO trading_server_circuits (server,failure_count,opened_until,last_error_code)
                VALUES (:server,0,NULL,'') ON CONFLICT (server) DO UPDATE SET
                failure_count=0,opened_until=NULL,last_error_code='',window_started_at=NOW(),updated_at=NOW()""",
                server=str(expected_server))
            conn.run("COMMIT")
            return {"account_id": account_id, "imported_deals": imported,
                    "received_deals": received, "status": "SYNCED", "integrity": recon_status}
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

    def fail_job(self, job_id: str, worker_id: str, code: str, retryable: bool) -> dict:
        safe_messages = {
            "AUTH_ERROR": "Les identifiants fournis sont incorrects.",
            "TRADING_PASSWORD_REJECTED": "Utilisez uniquement le mot de passe investisseur (lecture seule).",
            "SERVER_NOT_FOUND": "Le serveur MetaTrader indiqué n’a pas pu être trouvé.",
            "BROKER_UNAVAILABLE": "La connexion au broker est momentanément indisponible.",
            "TERMINAL_ERROR": "Le service MetaTrader est momentanément indisponible.",
            "NETWORK_ERROR": "La connexion réseau du worker est momentanément indisponible.",
            "DB_ERROR": "La base de données est momentanément indisponible.",
            "SYNC_ERROR": "La synchronisation n’a pas pu se terminer.",
        }
        normalized = code if code in safe_messages else "SYNC_ERROR"
        message = safe_messages[normalized]
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run("""SELECT j.trading_account_id,j.attempts,j.max_attempts,a.server
                FROM trading_sync_jobs j JOIN trading_accounts a ON a.id=j.trading_account_id
                WHERE j.id=:id AND j.worker_id=:worker_id AND j.status IN ('LEASED','RUNNING') FOR UPDATE""",
                id=job_id, worker_id=worker_id)
            if not rows:
                conn.run("ROLLBACK")
                raise LookupError("Tâche de synchronisation introuvable.")
            account_id, attempts, max_attempts, server = int(rows[0][0]), int(rows[0][1]), int(rows[0][2]), rows[0][3]
            should_retry = bool(retryable) and normalized != "AUTH_ERROR" and attempts < max_attempts
            next_status = "RETRY" if should_retry else "DEAD"
            delay = RETRY_DELAYS_SECONDS[min(max(0, attempts - 1), len(RETRY_DELAYS_SECONDS) - 1)]
            conn.run("""UPDATE trading_sync_jobs SET status=:status,not_before=NOW()+(:delay*INTERVAL '1 second'),
                last_error_code=:code,last_error_message=:message,heartbeat_at=NOW(),
                worker_id=CASE WHEN :status='RETRY' THEN '' ELSE worker_id END,
                instance_id=CASE WHEN :status='RETRY' THEN '' ELSE instance_id END,
                lease_expires_at=NULL,finished_at=CASE WHEN :status='DEAD' THEN NOW() ELSE NULL END,
                completed_at=CASE WHEN :status='DEAD' THEN NOW() ELSE NULL END
                WHERE id=:id""", status=next_status, delay=delay, code=normalized,
                message=message, id=job_id)
            conn.run("""UPDATE trading_sync_runs SET status=:status,error_code=:code,finished_at=NOW()
                WHERE sync_job_id=:id AND status='RUNNING'""",
                status="RETRY" if should_retry else "FAILED", code=normalized, id=job_id)
            account_status = {
                "TRADING_PASSWORD_REJECTED": "AUTH_ERROR",
                "SERVER_NOT_FOUND": "BROKER_UNAVAILABLE",
            }.get(normalized, normalized if normalized in ACCOUNT_STATUSES else "SYNC_ERROR")
            conn.run("""UPDATE trading_accounts SET status=:status,sync_status=:sync_status,
                last_error_code=:code,last_error_message=:message,updated_at=NOW()
                WHERE id=:account_id""", status=account_status,
                sync_status="RETRYING" if should_retry else account_status,
                code=normalized, message=message, account_id=account_id)
            conn.run("""INSERT INTO trading_sync_events
                (trading_account_id,sync_job_id,event_name,worker_id,error_code)
                VALUES (:account_id,:job_id,'sync_failed',:worker_id,:code)""",
                account_id=account_id, job_id=job_id, worker_id=worker_id[:100], code=normalized)
            conn.run("""UPDATE trading_workers SET status='ONLINE',current_job_id='',
                last_seen_at=NOW(),updated_at=NOW() WHERE worker_id=:worker_id""",
                worker_id=worker_id[:100])
            if normalized in {"BROKER_UNAVAILABLE", "NETWORK_ERROR", "TERMINAL_ERROR"}:
                threshold = max(3, int(os.environ.get("MT5_CIRCUIT_FAILURE_THRESHOLD", "50")))
                conn.run("""INSERT INTO trading_server_circuits
                    (server,failure_count,window_started_at,opened_until,last_error_code)
                    VALUES (:server,1,NOW(),NULL,:code)
                    ON CONFLICT (server) DO UPDATE SET
                    failure_count=CASE WHEN trading_server_circuits.window_started_at<NOW()-INTERVAL '5 minutes'
                        THEN 1 ELSE trading_server_circuits.failure_count+1 END,
                    window_started_at=CASE WHEN trading_server_circuits.window_started_at<NOW()-INTERVAL '5 minutes'
                        THEN NOW() ELSE trading_server_circuits.window_started_at END,
                    opened_until=CASE WHEN
                        (CASE WHEN trading_server_circuits.window_started_at<NOW()-INTERVAL '5 minutes'
                         THEN 1 ELSE trading_server_circuits.failure_count+1 END)>=:threshold
                        THEN NOW()+INTERVAL '5 minutes' ELSE trading_server_circuits.opened_until END,
                    last_error_code=:code,updated_at=NOW()""",
                    server=str(server), code=normalized, threshold=threshold)
            conn.run("COMMIT")
            return {"retrying": should_retry, "retry_in_seconds": delay if should_retry else None}
        except Exception:
            try: conn.run("ROLLBACK")
            except Exception: pass
            raise
        finally:
            conn.close()

    def verify_account_integrity(self, user_id: str, account_id: int) -> dict:
        """Run cheap, deterministic integrity checks without exposing credentials."""
        conn = self.get_conn()
        try:
            accounts = conn.run("""SELECT currency,server,last_successful_sync_at,status
                FROM trading_accounts WHERE id=:id AND user_id=:user_id""",
                id=account_id, user_id=user_id)
            if not accounts:
                raise LookupError("Compte de trading introuvable.")
            currency, server, last_successful, account_status = accounts[0]
            duplicate_count = int(conn.run("""SELECT COUNT(*) FROM (
                SELECT mt5_deal_ticket FROM trading_deals WHERE trading_account_id=:id
                GROUP BY mt5_deal_ticket HAVING COUNT(*)>1) duplicates""", id=account_id)[0][0] or 0)
            invalid_count = int(conn.run("""SELECT COUNT(*) FROM trading_deals
                WHERE trading_account_id=:id AND (
                    executed_at>NOW()+INTERVAL '1 day' OR executed_at<TIMESTAMPTZ '1990-01-01'
                    OR volume<0 OR ABS(profit)>1e15 OR ABS(commission)>1e15
                    OR ABS(swap)>1e15 OR ABS(fee)>1e15)""", id=account_id)[0][0] or 0)
            recon = conn.run("""SELECT status,missing_count,pnl_delta,created_at
                FROM trading_reconciliation_reports WHERE trading_account_id=:id
                ORDER BY created_at DESC LIMIT 1""", id=account_id)
        finally:
            conn.close()
        age_seconds = None
        if last_successful:
            age_seconds = max(0, int((datetime.now(timezone.utc) - as_utc(last_successful)).total_seconds()))
        stale_threshold = max(self.sync_interval_seconds * 3, 900)
        issues = []
        if duplicate_count:
            issues.append("DUPLICATE_DEALS")
        if invalid_count:
            issues.append("INVALID_DEALS")
        if not last_successful:
            issues.append("NEVER_SYNCED")
        elif age_seconds is not None and age_seconds > stale_threshold:
            issues.append("STALE")
        if recon and (recon[0][0] != "PASS" or int(recon[0][1] or 0) > 0 or abs(Decimal(str(recon[0][2] or 0))) > Decimal("0.01")):
            issues.append("RECONCILIATION_MISMATCH")
        health = "BROKEN" if any(issue in issues for issue in ("DUPLICATE_DEALS", "INVALID_DEALS", "RECONCILIATION_MISMATCH")) else (
            "WARNING" if issues else "HEALTHY"
        )
        return {
            "account_id": account_id,
            "health": health,
            "display_status": "SYNC_DELAYED" if "STALE" in issues else str(account_status),
            "last_verified_at": _iso(last_successful),
            "stale_seconds": age_seconds,
            "issues": issues,
            "checks": {"duplicates": duplicate_count, "invalid_deals": invalid_count,
                       "currency": currency or None, "server": server},
            "last_reconciliation": ({"status": recon[0][0], "missing": int(recon[0][1] or 0),
                                      "pnl_delta": float(recon[0][2] or 0), "at": _iso(recon[0][3])}
                                     if recon else None),
        }

    def queue_health(self) -> dict:
        conn = self.get_conn()
        try:
            row = conn.run("""SELECT
                COUNT(*) FILTER (WHERE status IN ('PENDING','RETRY')),
                COUNT(*) FILTER (WHERE status IN ('LEASED','RUNNING')),
                COUNT(*) FILTER (WHERE status='DEAD' AND created_at>NOW()-INTERVAL '24 hours'),
                MIN(created_at) FILTER (WHERE status IN ('PENDING','RETRY')),
                COUNT(*) FILTER (WHERE status='SUCCESS' AND completed_at>NOW()-INTERVAL '24 hours'),
                AVG(EXTRACT(EPOCH FROM (finished_at-started_at))*1000)
                    FILTER (WHERE status='SUCCESS' AND finished_at>NOW()-INTERVAL '24 hours')
                FROM trading_sync_jobs""")[0]
            stale = int(conn.run("""SELECT COUNT(*) FROM trading_accounts
                WHERE status NOT IN ('DISCONNECTED','ACCESS_EXPIRED') AND
                (last_successful_sync_at IS NULL OR last_successful_sync_at<NOW()-(:seconds*INTERVAL '1 second'))""",
                seconds=max(self.sync_interval_seconds * 3, 900))[0][0] or 0)
        finally:
            conn.close()
        oldest_age = None
        if row[3]:
            oldest_age = int((datetime.now(timezone.utc) - as_utc(row[3])).total_seconds())
        queue_blocked = bool(oldest_age is not None and oldest_age > max(900, self.sync_interval_seconds * 3))
        return {"status": "degraded" if queue_blocked or int(row[2] or 0) else "ok",
                "queue_depth": int(row[0] or 0), "running": int(row[1] or 0),
                "dead_24h": int(row[2] or 0), "oldest_pending_seconds": oldest_age,
                "success_24h": int(row[4] or 0), "average_duration_ms": round(float(row[5] or 0), 1),
                "stale_accounts": stale}

    def force_reconciliation(self, account_id: int, *, days: int | None = None) -> dict:
        conn = self.get_conn()
        try:
            rows = conn.run("SELECT id FROM trading_accounts WHERE id=:id", id=account_id)
            if not rows:
                raise LookupError("Compte de trading introuvable.")
            requested_days = max(1, min(int(days or self.weekly_reconciliation_days), 3650))
            job_id = self._enqueue(
                conn, account_id, "ADMIN_RECONCILIATION", priority=120,
                range_from=datetime.now(timezone.utc) - timedelta(days=requested_days),
                range_to=datetime.now(timezone.utc),
            )
            self._audit(conn, "admin", account_id, "ADMIN_FORCE_RECONCILIATION", {"days": requested_days})
            return {"queued": bool(job_id), "job_id": job_id}
        finally:
            conn.close()

    def retry_dead_job(self, job_id: str) -> dict:
        conn = self.get_conn()
        try:
            rows = conn.run("""UPDATE trading_sync_jobs SET status='RETRY',not_before=NOW(),
                worker_id='',instance_id='',locked_at=NULL,heartbeat_at=NULL,lease_expires_at=NULL,
                completed_at=NULL,finished_at=NULL,last_error_code='',last_error_message=''
                WHERE id=:id AND status IN ('DEAD','FAILED') RETURNING trading_account_id""", id=job_id)
            if not rows:
                raise LookupError("Tâche introuvable ou déjà active.")
            self._audit(conn, "admin", int(rows[0][0]), "ADMIN_RETRY_JOB", {"job_id": job_id})
            return {"queued": True, "job_id": job_id}
        finally:
            conn.close()

    def _scope_account_ids(self, conn, user_id: str, scope: str) -> tuple[list[int], str]:
        if scope == "all":
            rows = conn.run("""SELECT id,currency FROM trading_accounts
                WHERE user_id=:user_id AND status<>'DISCONNECTED' ORDER BY id""", user_id=user_id)
        else:
            try:
                account_id = int(scope)
            except (TypeError, ValueError) as exc:
                raise ValueError("Compte invalide.") from exc
            rows = conn.run("""SELECT id,currency FROM trading_accounts
                WHERE user_id=:user_id AND id=:id AND status<>'DISCONNECTED'""", user_id=user_id, id=account_id)
        if not rows:
            raise LookupError("Compte de trading introuvable.")
        currencies = {str(row[1] or "") for row in rows}
        if len(currencies) > 1:
            raise ValueError("Les comptes dans des devises différentes ne peuvent pas être agrégés.")
        return [int(row[0]) for row in rows], next(iter(currencies), "")

    @staticmethod
    def _account_where(account_ids: list[int]) -> tuple[str, dict]:
        params = {f"account_{index}": account_id for index, account_id in enumerate(account_ids)}
        return ",".join(f":account_{index}" for index in range(len(account_ids))), params

    def _fetch_deals(self, conn, account_ids: list[int], start=None, end=None) -> list[dict]:
        placeholders, params = self._account_where(account_ids)
        filters = [f"trading_account_id IN ({placeholders})"]
        if start is not None:
            filters.append("executed_at>=:start")
            params["start"] = start
        if end is not None:
            filters.append("executed_at<:end")
            params["end"] = end
        rows = conn.run(f"""SELECT trading_account_id,mt5_deal_ticket,mt5_order_ticket,
            mt5_position_id,symbol,deal_type,entry_type,is_trading_deal,volume,price,
            profit,commission,swap,fee,executed_at FROM trading_deals
            WHERE {' AND '.join(filters)} ORDER BY executed_at,mt5_deal_ticket""", **params)
        keys = ("trading_account_id", "mt5_deal_ticket", "mt5_order_ticket", "mt5_position_id",
                "symbol", "deal_type", "entry_type", "is_trading_deal", "volume", "price",
                "profit", "commission", "swap", "fee", "executed_at")
        return [dict(zip(keys, row)) for row in rows]

    def calendar(self, user_id: str, scope: str, month: str, timezone_name: str) -> dict:
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
            raise ValueError("Mois invalide.")
        timezone_name = validate_timezone(timezone_name)
        year, month_number = map(int, month.split("-"))
        local_start = datetime(year, month_number, 1, tzinfo=ZoneInfo(timezone_name))
        local_end = (local_start.replace(year=year + 1, month=1) if month_number == 12
                     else local_start.replace(month=month_number + 1))
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            # Pull one bounded month plus older entry legs for positions closed in it.
            month_deals = self._fetch_deals(conn, account_ids, local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc))
            position_ids = {int(row.get("mt5_position_id") or 0) for row in month_deals if row.get("entry_type") in {"OUT", "OUT_BY", "INOUT"}}
            all_deals = month_deals
            if position_ids:
                placeholders, params = self._account_where(account_ids)
                pos_params = {f"position_{i}": value for i, value in enumerate(sorted(position_ids))}
                pos_list = ",".join(f":position_{i}" for i in range(len(pos_params)))
                rows = conn.run(f"""SELECT trading_account_id,mt5_deal_ticket,mt5_order_ticket,
                    mt5_position_id,symbol,deal_type,entry_type,is_trading_deal,volume,price,
                    profit,commission,swap,fee,executed_at FROM trading_deals
                    WHERE trading_account_id IN ({placeholders}) AND mt5_position_id IN ({pos_list})
                    AND executed_at<:end ORDER BY executed_at,mt5_deal_ticket""",
                    **params, **pos_params, end=local_end.astimezone(timezone.utc))
                keys = ("trading_account_id", "mt5_deal_ticket", "mt5_order_ticket", "mt5_position_id",
                        "symbol", "deal_type", "entry_type", "is_trading_deal", "volume", "price",
                        "profit", "commission", "swap", "fee", "executed_at")
                prior = [dict(zip(keys, row)) for row in rows]
                by_ticket = {(row["trading_account_id"], row["mt5_deal_ticket"]): row for row in prior + month_deals}
                all_deals = list(by_ticket.values())
            result = calendar_summary(all_deals, timezone_name, month)
            result["currency"] = currency
            result["timezone"] = timezone_name
            account_ph, balance_params = self._account_where(account_ids)
            current_balance = Decimal(str(conn.run(f"""SELECT COALESCE(SUM(balance),0)
                FROM trading_accounts WHERE id IN ({account_ph})""", **balance_params)[0][0] or 0))
            month_change = Decimal(str(result["summary"]["netPnl"]))
            opening_balance = current_balance - month_change
            result["summary"]["returnPct"] = (
                round(float(Decimal(str(result["summary"]["netPnl"])) / abs(opening_balance) * 100), 2)
                if opening_balance else None
            )
            return result
        finally:
            conn.close()

    def day(self, user_id: str, scope: str, date_value: str, timezone_name: str) -> dict:
        try:
            local_date = datetime.strptime(date_value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Date invalide.") from exc
        timezone_name = validate_timezone(timezone_name)
        tz = ZoneInfo(timezone_name)
        start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
        end = (datetime.combine(local_date, datetime.min.time(), tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            day_deals = self._fetch_deals(conn, account_ids, start, end)
            position_ids = sorted({int(row.get("mt5_position_id") or 0) for row in day_deals if row.get("entry_type") in {"OUT", "OUT_BY", "INOUT"}} - {0})
            all_rows = day_deals
            if position_ids:
                account_ph, params = self._account_where(account_ids)
                pos_params = {f"position_{i}": value for i, value in enumerate(position_ids)}
                pos_ph = ",".join(f":position_{i}" for i in range(len(position_ids)))
                rows = conn.run(f"""SELECT trading_account_id,mt5_deal_ticket,mt5_order_ticket,
                    mt5_position_id,symbol,deal_type,entry_type,is_trading_deal,volume,price,
                    profit,commission,swap,fee,executed_at FROM trading_deals
                    WHERE trading_account_id IN ({account_ph}) AND mt5_position_id IN ({pos_ph})
                    AND executed_at<:end ORDER BY executed_at,mt5_deal_ticket""", **params, **pos_params, end=end)
                keys = ("trading_account_id", "mt5_deal_ticket", "mt5_order_ticket", "mt5_position_id",
                        "symbol", "deal_type", "entry_type", "is_trading_deal", "volume", "price",
                        "profit", "commission", "swap", "fee", "executed_at")
                all_rows = [dict(zip(keys, row)) for row in rows]
            positions = [position for position in reconstruct_positions(all_rows)
                         if position["closed_at"].astimezone(tz).date() == local_date]
            pnl = sum((item["net_pnl"] for item in positions), Decimal("0"))
            wins = sum(1 for item in positions if item["net_pnl"] > 0)
            losses = sum(1 for item in positions if item["net_pnl"] < 0)
            gross_profit = sum((item["net_pnl"] for item in positions if item["net_pnl"] > 0), Decimal("0"))
            gross_loss = sum((item["net_pnl"] for item in positions if item["net_pnl"] < 0), Decimal("0"))
            fees = sum((Decimal(str(row.get("commission") or 0)) + Decimal(str(row.get("swap") or 0)) + Decimal(str(row.get("fee") or 0)) for row in day_deals), Decimal("0"))
            return {
                "date": date_value, "currency": currency, "timezone": timezone_name,
                "summary": {
                    "netPnl": round(float(pnl), 2), "trades": len(positions), "deals": len(day_deals),
                    "wins": wins, "losses": losses,
                    "winRate": round(100 * wins / (wins + losses), 2) if wins + losses else 0,
                    "volume": round(float(sum((item["volume"] for item in positions), Decimal("0"))), 2),
                    "grossProfit": round(float(gross_profit), 2), "grossLoss": round(float(gross_loss), 2),
                    "fees": round(float(fees), 2),
                    "profitFactor": round(float(gross_profit / abs(gross_loss)), 2) if gross_loss else None,
                },
                "trades": [self._serialize_trade(item, tz) for item in reversed(positions)],
            }
        finally:
            conn.close()

    @staticmethod
    def _serialize_trade(item: dict, tz: ZoneInfo) -> dict:
        return {
            "positionId": item["position_id"], "symbol": item["symbol"], "direction": item["direction"],
            "volume": round(float(item["volume"]), 4),
            "entryPrice": _float(item["entry_price"]), "exitPrice": _float(item["exit_price"]),
            "openedAt": item["opened_at"].astimezone(tz).isoformat(),
            "closedAt": item["closed_at"].astimezone(tz).isoformat(),
            "durationSeconds": item["duration_seconds"], "netPnl": round(float(item["net_pnl"]), 2),
            "fees": round(float(item["fees"]), 2), "deals": item["deals"],
        }

    def stats(self, user_id: str, scope: str, timezone_name: str) -> dict:
        timezone_name = validate_timezone(timezone_name)
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            deals = self._fetch_deals(conn, account_ids)
            result = performance_stats(deals, timezone_name)
            result["currency"] = currency
            account_ph, params = self._account_where(account_ids)
            balances = conn.run(f"""SELECT COALESCE(SUM(balance),0),COALESCE(SUM(equity),0)
                FROM trading_accounts WHERE id IN ({account_ph})""", **params)[0]
            result["balance"] = round(float(balances[0] or 0), 2)
            result["equity"] = round(float(balances[1] or 0), 2)
            opening_balance = Decimal(str(balances[0] or 0)) - Decimal(str(result["netPnl"] or 0))
            if opening_balance > 0:
                result["maxDrawdownPct"] = round(
                    float(Decimal(str(result.get("maxDrawdown") or 0)) / opening_balance * 100), 2
                )
            else:
                result["maxDrawdownPct"] = None
            return result
        finally:
            conn.close()

    def trades(self, user_id: str, scope: str, timezone_name: str, limit: int = 100) -> dict:
        timezone_name = validate_timezone(timezone_name)
        tz = ZoneInfo(timezone_name)
        limit = max(1, min(int(limit), 250))
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            deals = self._fetch_deals(conn, account_ids)
        finally:
            conn.close()
        positions = reconstruct_positions(deals)
        return {
            "currency": currency,
            "timezone": timezone_name,
            "trades": [self._serialize_trade(item, tz) for item in reversed(positions[-limit:])],
            "total": len(positions),
        }

    def equity_curve(self, user_id: str, scope: str, timezone_name: str) -> dict:
        timezone_name = validate_timezone(timezone_name)
        tz = ZoneInfo(timezone_name)
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            deals = self._fetch_deals(conn, account_ids)
        finally:
            conn.close()
        daily = {}
        for deal in deals:
            if deal.get("is_trading_deal"):
                day = as_utc(deal["executed_at"]).astimezone(tz).date().isoformat()
                daily[day] = daily.get(day, Decimal("0")) + deal_net_pnl(deal)
        cumulative = Decimal("0")
        points = []
        for day in sorted(daily):
            cumulative += daily[day]
            points.append({"date": day, "cumulativePnl": round(float(cumulative), 2)})
        return {"currency": currency, "timezone": timezone_name, "points": points}

    def analytics(self, user_id: str, scope: str, timezone_name: str) -> dict:
        timezone_name = validate_timezone(timezone_name)
        conn = self.get_conn()
        try:
            account_ids, currency = self._scope_account_ids(conn, user_id, scope)
            deals = self._fetch_deals(conn, account_ids)
        finally:
            conn.close()
        result = analytics_breakdown(deals, timezone_name)
        result["currency"] = currency
        return result
