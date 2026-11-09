"""PostgreSQL outbox and worker handoff for copy trading.

The database stores intent before any broker request. Once a job reaches
DISPATCHING it is never automatically sent a second time: recovery must first
reconcile native MT5 positions/deals to avoid duplicate exposure.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .domain import (
    AcademyCopySettings,
    CopyDecisionError,
    academy_protection_enabled,
    academy_settings,
    canonical_hash,
    ensure_event_fresh,
    evaluate_academy_protection,
    enabled_unsupported_academy_guards,
    enabled_unsupported_pending_order_settings,
    execution_release_allowed,
    event_idempotency_key,
    normalized_volume,
    protection_period_key,
    requested_volume,
    verify_native_symbol_mapping,
)
from .security import lease_token_hash


ACTIVE_JOB_STATES = ("PENDING", "LEASED", "PREPARED", "DISPATCHING", "VERIFY_REQUIRED")
ACADEMY_SETTINGS_KEYS = (
    "mode_risque", "lots", "lots_max", "multiplicateur", "risque_pct",
    "risque_balance_pct", "risque_equity_pct", "forcer_lot_minimum",
    "inverser_trades", "copier_sl", "filtre_news", "lot_symboles", "slippage",
    "drawdown_actif", "drawdown_pct", "drawdown_gain_actif", "drawdown_gain_pct",
    "objectif_actif", "objectif_gain_pct", "objectif_perte_pct", "objectif_periode",
    "copier_ordres_en_attente", "convertir_pending_invalide", "calendar_currencies",
    "calendar_min_importance", "calendar_minutes_before", "calendar_minutes_after",
    "calendar_timezone", "calendar_region",
)

CALENDAR_SOURCE = "tradingeconomics"


def _calendar_failure_policy() -> str:
    """Default to blocking; fail-open requires an explicit global operator choice."""
    value = (os.environ.get("COPY_TRADING_CALENDAR_FAILURE_POLICY") or "BLOCK").strip().upper()
    return value if value in {"BLOCK", "ALLOW"} else "BLOCK"


def _calendar_decision(conn, settings: AcademyCopySettings, *, region="", preferences=None):
    if not settings.copy_news_filter:
        return True, "FILTER_DISABLED"

    policy = _calendar_failure_policy()

    def unavailable(reason):
        if policy == "ALLOW":
            return True, "CALENDAR_UNAVAILABLE_FAIL_OPEN"
        return False, reason

    if (os.environ.get("COPY_TRADING_CALENDAR_PROVIDER") or "").strip().lower() != CALENDAR_SOURCE:
        return unavailable("CALENDAR_PROVIDER_NOT_CONFIGURED")

    state = conn.run(
        """SELECT configured,last_attempt_at,last_success_at,last_failure_code,
                  coverage_start,coverage_end
           FROM copy_trading_calendar_sources WHERE source=:source""",
        source=CALENDAR_SOURCE,
    )
    if not state:
        return unavailable("CALENDAR_STALE_OR_UNAVAILABLE")
    configured, attempted, succeeded, failure, coverage_start, coverage_end = state[0]
    if not configured:
        return unavailable("CALENDAR_PROVIDER_NOT_CONFIGURED")
    if (failure or not attempted or not succeeded or
            (attempted and succeeded and attempted > succeeded) or
            coverage_start is None or coverage_end is None):
        return unavailable("CALENDAR_STALE_OR_UNAVAILABLE")
    try:
        sync_ttl = int(os.environ.get("COPY_TRADING_CALENDAR_SYNC_TTL_SECONDS", "900"))
    except (TypeError, ValueError):
        sync_ttl = 900
    sync_ttl = min(3600, max(120, sync_ttl))
    fresh = conn.run(
        """SELECT 1 FROM copy_trading_calendar_sources WHERE source=:source
           AND last_success_at>NOW()-(:ttl * INTERVAL '1 second')
           AND coverage_start<=NOW() AND coverage_end>NOW()
           AND last_attempt_at<=last_success_at AND last_failure_code=''""",
        source=CALENDAR_SOURCE, ttl=sync_ttl,
    )
    if not fresh:
        return unavailable("CALENDAR_STALE_OR_UNAVAILABLE")

    profile = dict(preferences or {})
    currencies_value = profile.get("calendar_currencies") or ""
    if isinstance(currencies_value, (list, tuple, set)):
        currencies = sorted({str(value).strip().upper() for value in currencies_value if str(value).strip()})
    else:
        currencies = sorted({value.strip().upper() for value in str(currencies_value).split(",") if value.strip()})
    if any(not value.isalnum() or len(value) > 8 for value in currencies):
        return unavailable("CALENDAR_SETTINGS_INVALID")
    try:
        min_importance = int(profile.get("calendar_min_importance", 2))
        before = int(profile.get("calendar_minutes_before", 30))
        after = int(profile.get("calendar_minutes_after", 30))
    except (TypeError, ValueError, OverflowError):
        return unavailable("CALENDAR_SETTINGS_INVALID")
    if min_importance not in (1, 2, 3) or not 0 <= before <= 360 or not 0 <= after <= 360:
        return unavailable("CALENDAR_SETTINGS_INVALID")
    region_value = str(profile.get("calendar_region") or region or "ALL").strip()[:40]
    rows = conn.run(
        """SELECT 1 FROM copy_trading_calendar_events
           WHERE source=:source AND fetched_at>NOW()-INTERVAL '36 hours'
             AND (:region='ALL' OR region IN (:region,'ALL','GLOBAL'))
             AND importance>=:importance
             AND (:currencies='' OR currency=ANY(string_to_array(:currencies,',')))
             AND ((estimated_time AND starts_at<=NOW()+make_interval(mins=>:before)
                                   AND ends_at>NOW()-make_interval(mins=>:after))
                  OR (NOT estimated_time AND starts_at>=NOW()-make_interval(mins=>:after)
                                         AND starts_at<=NOW()+make_interval(mins=>:before)))
           LIMIT 1""",
        source=CALENDAR_SOURCE, region=region_value or "ALL", importance=min_importance,
        currencies=",".join(currencies), before=before, after=after,
    )
    if rows:
        return False, "NEWS_WINDOW_ACTIVE"
    return True, "NO_BLOCKING_WINDOW"


def _row_dicts(conn, rows):
    columns = [column["name"] for column in (conn.columns or [])]
    return [dict(zip(columns, row)) for row in (rows or [])]


def _json_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _number(value, field: str, *, positive=False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CopyDecisionError("INVALID_NATIVE_POSITION", f"Valeur MT5 invalide : {field}.")
    if not number.is_finite() or (positive and number <= 0):
        raise CopyDecisionError("INVALID_NATIVE_POSITION", f"Valeur MT5 invalide : {field}.")
    return number


def _native_position_matches(positions, linked_id):
    linked_id = str(linked_id or "")
    if not isinstance(positions, list) or not linked_id:
        return []
    return [row for row in positions if isinstance(row, dict) and (
        str(row.get("ticket") or "") == linked_id
        or str(row.get("position_id") or "") == linked_id
    )]


def _aware_datetime(value, field="observed_at"):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CopyDecisionError("INVALID_EVENT_TIME", f"{field} doit inclure un fuseau horaire.")
    return value.astimezone(timezone.utc)


def _native_update_msc(value):
    """Return a positive native MT5 millisecond timestamp, or None."""
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value > 0 else None


def _native_update_datetime(value):
    """Convert MT5's epoch-millisecond source time without substituting poll time."""
    stamp = _native_update_msc(value)
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _pending_event_source_time(raw, old, server_now):
    """Use native setup time for first-seen orders, prior snapshot for changes."""
    if old is not None and bool(old[12]):
        try:
            source_time = _aware_datetime(old[14], "last_seen_at")
            ensure_event_fresh(source_time, server_now)
            return source_time
        except (CopyDecisionError, TypeError, ValueError):
            return None
    source_time = _native_update_datetime(raw.get("time_setup_msc"))
    if source_time is None:
        return None
    try:
        ensure_event_fresh(source_time, server_now)
    except CopyDecisionError:
        return None
    return source_time


def _pending_terminal_event(terminal, server_now):
    """Return a fresh cancel/expiry, or quarantine when status/time is ambiguous."""
    state = str((terminal or {}).get("state") or "").upper()
    if state == "FILLED":
        return "QUARANTINE", None
    action = "PENDING_EXPIRE" if state == "EXPIRED" else (
        "PENDING_CANCEL" if state in {"CANCELED", "REJECTED"} else None
    )
    source_time = _native_update_datetime((terminal or {}).get("time_done_msc"))
    if action is None or source_time is None:
        return "QUARANTINE", None
    try:
        ensure_event_fresh(source_time, server_now)
    except CopyDecisionError:
        return "QUARANTINE", None
    return action, source_time


def _settings_payload(params) -> tuple[dict, AcademyCopySettings, str]:
    settings = academy_settings(params)
    raw = dict(params or {})
    payload = {key: raw[key] for key in ACADEMY_SETTINGS_KEYS if key in raw}
    # Resolve all known defaults in the event snapshot so later profile edits
    # cannot change an already accepted master event.
    payload.setdefault("mode_risque", settings.mode)
    payload.setdefault("lots", str(settings.lots))
    payload.setdefault("lots_max", str(settings.lots_max))
    payload.setdefault("multiplicateur", str(settings.multiplier))
    payload.setdefault("risque_pct", str(settings.risk_pct))
    payload.setdefault("risque_balance_pct", str(settings.risk_balance_pct))
    payload.setdefault("risque_equity_pct", str(settings.risk_equity_pct))
    payload.setdefault("forcer_lot_minimum", settings.force_minimum)
    payload.setdefault("inverser_trades", settings.invert_trades)
    payload.setdefault("copier_sl", settings.copy_stop_loss)
    payload.setdefault("filtre_news", settings.copy_news_filter)
    payload.setdefault("lot_symboles", {key: str(value) for key, value in settings.symbol_lots.items()})
    payload.setdefault("slippage", raw.get("slippage", 100))
    return payload, settings, canonical_hash(payload)


class CopyTradingService:
    def __init__(self, get_conn, get_member, can_copy_member, *, consent_version: str, logger=None):
        self.get_conn = get_conn
        self.get_member = get_member
        self.can_copy_member = can_copy_member
        self.consent_version = (consent_version or "").strip()
        self.logger = logger

    @staticmethod
    def _audit(conn, event_type, *, master="", follower="", job=None,
               idempotency_key="", outcome="", details=None):
        """Write bounded operational evidence in the same DB transaction."""
        conn.run(
            """INSERT INTO copy_trading_audit_events
               (event_type,master_member_code,follower_member_code,job_id,
                idempotency_key,outcome_code,details)
               VALUES (:event,:master,:follower,:job,:key,:outcome,:details::jsonb)""",
            event=str(event_type)[:64], master=str(master or "")[:100],
            follower=str(follower or "")[:100], job=job,
            key=str(idempotency_key or "")[:128], outcome=str(outcome or "")[:100],
            details=json.dumps(details or {}, separators=(",", ":"), default=str),
        )

    @staticmethod
    def _require_position_reconciliation(conn, master_code, position_id, reason):
        """Block followers with an open link when a source event is too old to execute."""
        rows = conn.run(
            """SELECT follower_member_code FROM copy_trading_position_links
               WHERE master_member_code=:master AND master_position_id=:position AND state='OPEN'
               FOR UPDATE""",
            master=master_code, position=position_id,
        )
        for row in rows or []:
            follower = str(row[0])
            conn.run(
                """UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                   WHERE master_member_code=:master AND follower_member_code=:follower
                     AND master_position_id=:position AND state='OPEN'""",
                master=master_code, follower=follower, position=position_id,
            )
            conn.run(
                """INSERT INTO copy_trading_follower_locks(member_code,blocked,blocked_reason)
                   VALUES (:follower,TRUE,:reason)
                   ON CONFLICT (member_code) DO UPDATE SET blocked=TRUE,
                     blocked_reason=EXCLUDED.blocked_reason,updated_at=NOW()""",
                follower=follower, reason=str(reason)[:100],
            )

    @staticmethod
    def _require_pending_order_reconciliation(conn, master_code, order_ticket, reason):
        """Quarantine linked follower orders when master-side evidence is ambiguous or stale."""
        link_rows = conn.run(
            """SELECT follower_member_code FROM copy_trading_pending_order_links
               WHERE master_member_code=:master AND master_order_ticket=:ticket AND state='OPEN'
               FOR UPDATE""",
            master=master_code, ticket=str(order_ticket),
        )
        job_rows = conn.run(
            """SELECT j.job_id::text,j.follower_member_code FROM copy_trading_jobs j
               JOIN copy_trading_events e ON e.event_id=j.event_id
               WHERE e.master_member_code=:master AND e.position_id=:ticket
                 AND e.action IN ('PENDING_CREATE','PENDING_MODIFY')
                 AND j.status IN ('PENDING','RETRY','LEASED','PREPARED','DISPATCHING')
               FOR UPDATE OF j""",
            master=master_code, ticket=str(order_ticket),
        )
        followers = {str(row[0]) for row in (link_rows or [])}
        for job, follower_code in job_rows or []:
            follower = str(follower_code)
            followers.add(follower)
            conn.run(
                """UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',
                   failure_code=:reason,updated_at=NOW()
                   WHERE job_id=:job AND status IN ('PENDING','RETRY','LEASED','PREPARED','DISPATCHING')""",
                reason=str(reason)[:100], job=str(job),
            )
        for follower in followers:
            conn.run(
                """UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                   WHERE master_member_code=:master AND follower_member_code=:follower
                     AND master_order_ticket=:ticket AND state='OPEN'""",
                master=master_code, follower=follower, ticket=str(order_ticket),
            )
            conn.run(
                """INSERT INTO copy_trading_follower_locks(member_code,blocked,blocked_reason)
                   VALUES (:follower,TRUE,:reason)
                   ON CONFLICT (member_code) DO UPDATE SET blocked=TRUE,
                     blocked_reason=CASE WHEN copy_trading_follower_locks.blocked
                       AND copy_trading_follower_locks.blocked_reason<>''
                       THEN copy_trading_follower_locks.blocked_reason ELSE EXCLUDED.blocked_reason END,
                     updated_at=NOW()""",
                follower=follower, reason=str(reason)[:100],
            )
            CopyTradingService._audit(
                conn, "PENDING_ORDER_RECONCILIATION_REQUIRED", master=master_code,
                follower=follower, outcome=str(reason)[:100],
                details={"master_order_ticket": str(order_ticket)},
            )

    @staticmethod
    def _verify_native_postcondition(*, execution_action, evidence, request_comment, result):
        """Re-evaluate the worker's signed terminal read-back before completion."""
        native = result.get("native_postcondition")
        try:
            if (not isinstance(native, dict) or native.get("terminal_connected") is not True
                    or native.get("terminal_path_verified") is not True):
                return False
            expected_market = _json_object(evidence.get("market"))
            if (str(native.get("login") or "") != str(expected_market.get("login") or "")
                    or str(native.get("server") or "").casefold() != str(expected_market.get("server") or "").casefold()):
                return False
            matches = native.get("matches")
            if not isinstance(matches, list) or len(matches) > 2 or any(not isinstance(row, dict) for row in matches):
                return False
            plan = _json_object(evidence.get("plan"))
            action = str(execution_action or "")
            if action.startswith("PENDING_"):
                if native.get("orders_complete") is not True:
                    return False
                ticket = str(plan.get("follower_order_ticket") or result.get("order_ticket") or "")
                if action in {"PENDING_CANCEL", "PENDING_EXPIRE"}:
                    return len(matches) == 0
                if len(matches) != 1:
                    return False
                order = matches[0]
                expected_comment = (request_comment if action == "PENDING_CREATE" else
                                    str(plan.get("follower_before", {}).get("comment") or ""))
                if (not ticket or str(order.get("order_ticket") or "") != ticket
                        or str(order.get("symbol") or "") != str(evidence.get("follower", {}).get("name") or "")
                        or str(order.get("pending_type") or "").upper() != str(plan.get("pending_type") or "").upper()
                        or str(order.get("side") or "").upper() != str(plan.get("side") or "").upper()
                        or str(order.get("comment") or "") != str(expected_comment or "")):
                    return False
                for key, plan_key in (("volume", "volume"), ("price_open", "price"),
                                      ("stop_loss", "stop_loss"), ("take_profit", "take_profit"),
                                      ("stop_limit", "stop_limit")):
                    positive = key in {"volume", "price_open"}
                    if _number(order.get(key), key, positive=positive) != \
                            _number(plan.get(plan_key), plan_key, positive=positive):
                        return False
                return (str(order.get("time_type") or "0") == str(plan.get("time_type") or 0)
                        and str(order.get("expiration_at") or "") == str(plan.get("expiration_at") or ""))
            if native.get("positions_complete") is not True:
                return False
            target_id = str(plan.get("position_ticket") or "")
            if action == "OPEN":
                if len(matches) != 1:
                    return False
                position = matches[0]
                expected_id = str(result.get("position_id") or "")
                expected_symbol = str(evidence.get("follower", {}).get("name") or "")
                expected_side = str(plan.get("side") or "")
                expected_volume = _number(result.get("volume"), "verified volume", positive=True)
                return bool(expected_id and
                            expected_id in {str(position.get("ticket") or ""), str(position.get("position_id") or "")} and
                            str(position.get("symbol") or "") == expected_symbol and
                            str(position.get("side") or "").upper() == expected_side and
                            _number(position.get("volume"), "native position volume", positive=True) == expected_volume and
                            str(position.get("comment") or "") == str(request_comment or ""))
            if not target_id:
                return False
            if action == "CLOSE":
                return len(matches) == 0
            if action == "RESIZE_CLOSE":
                before = _number(plan.get("follower_before_volume"), "pre-close follower volume", positive=True)
                closed = _number(result.get("volume"), "confirmed closed volume", positive=True)
                if closed > before:
                    return False
                expected_remaining = before - closed
                if expected_remaining == 0:
                    return len(matches) == 0
                return (len(matches) == 1 and
                        str(matches[0].get("symbol") or "") ==
                            str(evidence.get("follower", {}).get("name") or "") and
                        _number(matches[0].get("volume"), "remaining follower volume", positive=True) == expected_remaining)
            if action == "PROTECT":
                if len(matches) != 1:
                    return False
                position = matches[0]
                return (str(position.get("ticket") or "") == target_id or
                        str(position.get("position_id") or "") == target_id) and \
                    str(position.get("symbol") or "") == str(evidence.get("follower", {}).get("name") or "") and \
                    str(position.get("side") or "").upper() == str(plan.get("side") or "").upper() and (
                    _number(position.get("stop_loss", position.get("sl", 0)), "native stop") ==
                        _number(plan.get("stop_loss", 0), "planned stop") and
                    _number(position.get("take_profit", position.get("tp", 0)), "native take profit") ==
                        _number(plan.get("take_profit", 0), "planned take profit"))
            return False
        except (CopyDecisionError, TypeError, ValueError):
            return False

    def _eligible_followers(self, conn):
        rows = conn.run(
            """SELECT m.code,m.params,m.actif,m.copy_actif,m.date_fin,m.access_level,
                      m.billing_status,m.billing_current_period_end,m.admin_suspended
               FROM members m
               JOIN copy_trading_consents c ON c.member_code=m.code
               WHERE c.revoked_at IS NULL AND c.consented_at IS NOT NULL
                 AND c.consent_version=:version
                 AND m.actif=TRUE AND COALESCE(m.copy_actif,FALSE)=TRUE
                 AND COALESCE(m.access_level,'member') NOT IN ('explorer','demo')
               ORDER BY m.code""",
            version=self.consent_version,
        )
        result = []
        for row in _row_dicts(conn, rows):
            member = {
                "code": row["code"], "actif": row["actif"], "copy_actif": row["copy_actif"],
                "date_fin": row["date_fin"], "access_level": row["access_level"],
                "billing_status": row["billing_status"],
                "billing_current_period_end": row["billing_current_period_end"],
                "admin_suspended": row["admin_suspended"],
            }
            try:
                eligible = self.can_copy_member(member)
            except Exception as error:
                if self.logger:
                    self.logger.warning("Copy eligibility check failed for one member (%s)", type(error).__name__)
                continue
            if not eligible:
                continue
            try:
                params = _json_object(row["params"])
                settings, _parsed, settings_hash = _settings_payload(params)
            except CopyDecisionError as error:
                if self.logger:
                    self.logger.warning("Copy settings rejected for one member (%s)", error.code)
                continue
            result.append((str(row["code"]), settings, settings_hash))
        return result

    def master_positions_requiring_close_evidence(self, master_code: str) -> list[str]:
        """Return positions missing from one complete snapshot, for native deal lookup."""
        conn = self.get_conn()
        try:
            rows = conn.run(
                """SELECT position_id FROM copy_trading_master_positions
                   WHERE master_member_code=:master AND is_open=TRUE AND missing_snapshots>=1
                   ORDER BY updated_at LIMIT 1000""",
                master=str(master_code or "").strip().upper(),
            )
            return [str(row[0]) for row in (rows or [])]
        finally:
            conn.close()

    def master_orders_requiring_close_evidence(self, master_code: str) -> list[str]:
        """Return missing active orders requiring a native history state."""
        conn = self.get_conn()
        try:
            rows = conn.run(
                """SELECT order_ticket FROM copy_trading_master_orders
                   WHERE master_member_code=:master AND is_active=TRUE AND missing_snapshots>=1
                   ORDER BY updated_at LIMIT 1000""",
                master=str(master_code or "").strip().upper(),
            )
            return [str(row[0]) for row in (rows or [])]
        finally:
            conn.close()

    def _event(self, conn, *, master_code, master_login, master_server, position,
               action, observed_at, source_version, pending=None):
        now_row = conn.run("SELECT NOW()")
        now = now_row[0][0].astimezone(timezone.utc)
        observed = _aware_datetime(observed_at)
        expires_at, _remaining = ensure_event_fresh(observed, now)
        position_id = str(position["position_id"])
        key = event_idempotency_key(
            master_login=master_login, master_server=master_server,
            position_id=position_id, action=action, source_version=source_version,
        )
        payload = {
            "master_member_code": master_code,
            "position_id": position_id,
            "action": action,
            "symbol": position["symbol"],
            "side": position["side"],
            "volume": str(position["volume"]),
            "price_open": str(position["price_open"]),
            "stop_loss": str(position["stop_loss"]),
            "take_profit": str(position["take_profit"]),
            "source_version": str(source_version),
            "source_time": observed.isoformat(),
        }
        pending = dict(pending or {})
        if action.startswith("PENDING_"):
            payload.update({
                "pending_type": str(pending.get("pending_type") or ""),
                "stop_limit": str(pending.get("stop_limit") or 0),
                "expiration_at": pending.get("expiration_at"),
                "time_type": int(pending.get("time_type") or 0),
            })
        payload_hash = canonical_hash(payload)
        rows = conn.run(
            """INSERT INTO copy_trading_events
               (idempotency_key,master_member_code,position_id,action,symbol,side,volume,
                price_open,stop_loss,take_profit,source_version,source_time,expires_at,payload_hash,
                pending_type,stop_limit,expiration_at,time_type)
               VALUES (:key,:master,:position,:action,:symbol,:side,:volume,:price,:sl,:tp,
                       :version,:source_time,:expires,:payload_hash,:pending_type,:stop_limit,
                       :expiration_at,:time_type)
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING event_id::text""",
            key=key, master=master_code, position=position_id, action=action,
            symbol=position["symbol"], side=position["side"], volume=position["volume"],
            price=position["price_open"], sl=position["stop_loss"], tp=position["take_profit"],
            version=str(source_version), source_time=observed, expires=expires_at,
            payload_hash=payload_hash,
            pending_type=str(pending.get("pending_type") or ""),
            stop_limit=pending.get("stop_limit") or 0,
            expiration_at=pending.get("expiration_at"),
            time_type=int(pending.get("time_type") or 0),
        )
        if rows:
            event_id = str(rows[0][0])
            persisted_expiry = expires_at
            self._audit(conn, "MASTER_EVENT_ACCEPTED", master=master_code,
                        job=None, idempotency_key=key, outcome=action,
                        details={"position_id": position_id, "symbol": str(position["symbol"])[:64],
                                 "expires_at": expires_at.isoformat()})
        else:
            rows = conn.run(
                """SELECT event_id::text,expires_at,status,payload_hash
                   FROM copy_trading_events WHERE idempotency_key=:key""", key=key,
            )
            if not rows:
                raise RuntimeError("Idempotent event insert could not be resolved")
            if str(rows[0][3] or "") != payload_hash:
                raise CopyDecisionError("IDEMPOTENCY_COLLISION", "La clé d’idempotence existe avec un contenu différent.")
            if rows[0][2] != "READY" or rows[0][1] <= now:
                raise CopyDecisionError("EVENT_EXPIRED", "L’événement idempotent a dépassé son échéance absolue de 120 secondes.")
            event_id = str(rows[0][0])
            persisted_expiry = rows[0][1]
        return event_id, key, payload, persisted_expiry

    def _enqueue_for_followers(self, conn, event_id, idempotency_key, payload, expires_at):
        followers = self._eligible_followers(conn)
        queued = 0
        for follower_code, settings, settings_hash in followers:
            if str(payload.get("action") or "").startswith("PENDING_") and str(
                    settings.get("copier_ordres_en_attente", False)).strip().lower() not in {"1", "true", "yes", "on"}:
                continue
            rows = conn.run(
                """INSERT INTO copy_trading_jobs
                   (event_id,follower_member_code,expires_at,academy_settings,academy_settings_hash)
                   VALUES (:event,:follower,:expires,:settings::jsonb,:settings_hash)
                   ON CONFLICT (event_id,follower_member_code) DO NOTHING
                RETURNING job_id::text""",
                event=event_id, follower=follower_code, expires=expires_at,
                settings=json.dumps(settings, separators=(",", ":")), settings_hash=settings_hash,
            )
            if rows:
                queued += len(rows)
                for row in rows:
                    self._audit(conn, "FOLLOWER_JOB_QUEUED",
                                master=payload.get("master_member_code", ""),
                                follower=follower_code, job=str(row[0]),
                                idempotency_key=idempotency_key,
                                outcome=payload.get("action", ""),
                                details={"expires_at": expires_at.isoformat()})
        return queued

    def claim_protection_monitor(self, worker_id: str):
        """Lease one consented account whose native balance/equity needs sampling."""
        conn = self.get_conn()
        token = secrets.token_urlsafe(32)
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT m.code
                   FROM members m
                   JOIN copy_trading_consents c ON c.member_code=m.code
                   LEFT JOIN copy_trading_account_protections p ON p.member_code=m.code
                   WHERE c.revoked_at IS NULL AND c.consented_at IS NOT NULL
                     AND c.consent_version=:version AND m.actif=TRUE
                     AND COALESCE(m.access_level,'member') NOT IN ('explorer','demo')
                     AND NOT COALESCE(m.admin_suspended,FALSE)
                     AND (LOWER(COALESCE(m.params->>'drawdown_actif','false')) IN ('1','true','yes','on')
                       OR LOWER(COALESCE(m.params->>'drawdown_gain_actif','false')) IN ('1','true','yes','on')
                       OR LOWER(COALESCE(m.params->>'objectif_actif','false')) IN ('1','true','yes','on')
                       OR COALESCE(p.suspended,FALSE)=TRUE)
                     AND (p.member_code IS NULL OR
                       (COALESCE(p.monitor_lease_expires_at,'-infinity'::timestamptz)<=NOW()
                        AND (p.last_sample_at IS NULL OR p.last_sample_at<NOW()-INTERVAL '15 seconds')))
                   ORDER BY p.last_sample_at NULLS FIRST,m.code
                   LIMIT 1 FOR UPDATE OF m SKIP LOCKED""",
                version=self.consent_version,
            )
            if not rows:
                conn.run("COMMIT")
                return None
            member_code = str(rows[0][0])
            conn.run(
                """INSERT INTO copy_trading_account_protections
                   (member_code,monitor_worker_id,monitor_token_hash,monitor_lease_expires_at,updated_at)
                   VALUES (:member,:worker,:token_hash,NOW()+INTERVAL '120 seconds',NOW())
                   ON CONFLICT(member_code) DO UPDATE SET monitor_worker_id=EXCLUDED.monitor_worker_id,
                     monitor_token_hash=EXCLUDED.monitor_token_hash,
                     monitor_lease_expires_at=EXCLUDED.monitor_lease_expires_at,updated_at=NOW()""",
                member=member_code, worker=str(worker_id)[:100], token_hash=lease_token_hash(token),
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
        member = self.get_member(member_code) or {}
        params = _json_object(member.get("params"))
        if not all(str(params.get(key) or "").strip() for key in ("mt_login", "mt_password")) or not str(
                params.get("serveur") or params.get("mt_server") or "").strip():
            self._audit_standalone("PROTECTION_SAMPLE_SKIPPED", follower=member_code,
                                   outcome="FOLLOWER_CONNECTION_NOT_CONFIGURED")
            return None
        return {
            "member_code": member_code,
            "login": str(params["mt_login"]),
            "server": str(params.get("serveur") or params.get("mt_server")),
            "password": str(params["mt_password"]),
            "settings": {key: params.get(key) for key in ACADEMY_SETTINGS_KEYS},
            "monitor_token": token,
        }

    def _audit_standalone(self, event_type, *, follower="", outcome="", details=None):
        conn = self.get_conn()
        try:
            self._audit(conn, event_type, follower=follower, outcome=outcome, details=details)
        finally:
            conn.close()

    def record_protection_snapshot(self, worker_id: str, member_code: str, token: str,
                                   account: dict, positions: list[dict], *, positions_complete: bool,
                                   pending_orders: list[dict], pending_orders_complete: bool):
        """Persist a native monitor sample and queue confirmed position/order safety actions."""
        if (not isinstance(account, dict) or account.get("terminal_connected") is not True
                or account.get("terminal_path_verified") is not True
                or not isinstance(positions, list) or len(positions) > 5000
                or positions_complete is not True or not isinstance(pending_orders, list)
                or len(pending_orders) > 5000 or pending_orders_complete is not True):
            raise CopyDecisionError("NATIVE_PROTECTION_SAMPLE_INCOMPLETE",
                                    "La protection exige un compte et des instantanés complets de positions et d’ordres MT5.")
        member_code = str(member_code or "").strip().upper()
        member = self.get_member(member_code) or {}
        params = _json_object(member.get("params"))
        expected_login = str(params.get("mt_login") or "").strip()
        expected_server = str(params.get("serveur") or params.get("mt_server") or "").strip()
        if (not expected_login or str(account.get("login") or "").strip() != expected_login
                or not expected_server or str(account.get("server") or "").strip().casefold()
                != expected_server.casefold()):
            raise CopyDecisionError("TERMINAL_IDENTITY_MISMATCH",
                                    "Le relevé de protection ne correspond pas au compte MT5 du profil.")
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT guard_enabled,period_key,baseline_balance,baseline_equity,risk_anchor_equity,
                          peak_equity,last_balance,last_equity,loss_drawdown_pct,gain_pct,objective_pct,
                          suspended,suspension_reason,triggered_at,resume_requested,
                          monitor_worker_id,monitor_token_hash,monitor_lease_expires_at,
                          objective_resume_pending
                   FROM copy_trading_account_protections WHERE member_code=:member FOR UPDATE""",
                member=member_code,
            )
            now = _aware_datetime(conn.run("SELECT NOW()")[0][0], "server_now")
            if not rows:
                raise CopyDecisionError("PROTECTION_MONITOR_LEASE_INVALID", "Le bail du moniteur de protection est absent.")
            row = rows[0]
            if (str(row[15] or "") != str(worker_id or "") or not token
                    or lease_token_hash(token) != str(row[16] or "") or row[17] is None or row[17] <= now):
                raise CopyDecisionError("PROTECTION_MONITOR_LEASE_INVALID", "Le bail du moniteur de protection a expiré.")
            native_account = dict(account)
            evaluation = evaluate_academy_protection(params, native_account, {
                "guard_enabled": row[0], "period_key": row[1],
                "baseline_balance": row[2], "baseline_equity": row[3],
                "risk_anchor_equity": row[4], "peak_equity": row[5],
                "last_balance": row[6], "last_equity": row[7],
                "loss_drawdown_pct": row[8], "gain_pct": row[9],
                "objective_pct": row[10], "suspended": row[11],
                "suspension_reason": row[12], "triggered_at": row[13],
                "resume_requested": row[14], "objective_resume_pending": row[18],
            }, now)
            was_suspended = bool(row[11])
            objective_rollover = bool(
                was_suspended and str(row[12] or "").startswith("OBJECTIVE_")
                and (evaluation["period_changed"] or bool(row[18]))
            )
            if objective_rollover:
                unresolved_links = conn.run(
                    """SELECT
                         (SELECT COUNT(*) FROM copy_trading_position_links
                          WHERE follower_member_code=:member AND state IN ('OPEN','VERIFY_REQUIRED'))
                         +
                         (SELECT COUNT(*) FROM copy_trading_pending_order_links
                          WHERE follower_member_code=:member AND state IN ('OPEN','VERIFY_REQUIRED'))""",
                    member=member_code,
                )
                if int((unresolved_links[0] or [0])[0] if unresolved_links else 0) == 0:
                    state_for_resume = {"guard_enabled": row[0], "period_key": row[1],
                        "baseline_balance": row[2], "baseline_equity": row[3],
                        "risk_anchor_equity": row[4], "peak_equity": row[5],
                        "suspended": row[11], "suspension_reason": row[12],
                        "resume_requested": True}
                    evaluation = evaluate_academy_protection(params, native_account, state_for_resume, now)
                    objective_resume_pending = False
                else:
                    evaluation["suspended"] = True
                    evaluation["suspension_reason"] = str(row[12] or "OBJECTIVE_PERIOD_END")
                    evaluation["trigger"] = ""
                    objective_resume_pending = True
            else:
                objective_resume_pending = bool(row[18])
            new_trigger = evaluation["trigger"] if not was_suspended else ""
            reason = evaluation["suspension_reason"]
            triggered_at = (row[13] if was_suspended and evaluation["suspended"]
                            else now if new_trigger else None)
            conn.run(
                """UPDATE copy_trading_account_protections SET guard_enabled=:enabled,
                     period_key=:period,baseline_balance=:baseline_balance,baseline_equity=:baseline_equity,
                     risk_anchor_equity=:risk_anchor,peak_equity=:peak,last_balance=:balance,last_equity=:equity,
                     loss_drawdown_pct=:loss_pct,gain_pct=:gain_pct,objective_pct=:objective_pct,
                     suspended=:suspended,suspension_reason=:reason,triggered_at=:triggered_at,
                     resume_requested=FALSE,resume_requested_at=NULL,objective_resume_pending=:objective_resume_pending,
                     last_sample_at=NOW(),
                     monitor_worker_id='',monitor_token_hash='',monitor_lease_expires_at=NULL,updated_at=NOW()
                   WHERE member_code=:member""",
                enabled=evaluation["guard_enabled"], period=evaluation["period_key"],
                baseline_balance=evaluation["baseline_balance"], baseline_equity=evaluation["baseline_equity"],
                risk_anchor=evaluation["risk_anchor_equity"], peak=evaluation["peak_equity"],
                balance=evaluation["balance"], equity=evaluation["equity"],
                loss_pct=evaluation["loss_drawdown_pct"], gain_pct=evaluation["gain_pct"],
                objective_pct=evaluation["objective_pct"], suspended=evaluation["suspended"],
                reason=reason, triggered_at=triggered_at,
                objective_resume_pending=objective_resume_pending, member=member_code,
            )
            if (not evaluation["suspended"] and evaluation["period_changed"] and was_suspended
                    and str(row[12] or "").startswith("OBJECTIVE_")):
                conn.run(
                    """UPDATE copy_trading_follower_locks SET blocked=FALSE,blocked_reason='',
                         updated_at=NOW() WHERE member_code=:member
                         AND blocked_reason LIKE 'ACADEMY_PROTECTION:%'""", member=member_code,
                )
                self._audit(conn, "ACADEMY_PROTECTION_PERIOD_RESUMED", follower=member_code,
                            outcome="OBJECTIVE_PERIOD_RESET", details={"period_key": evaluation["period_key"]})
            elif evaluation["resume_requested"]:
                conn.run(
                    """UPDATE copy_trading_follower_locks SET blocked=FALSE,blocked_reason='',
                         updated_at=NOW() WHERE member_code=:member
                         AND blocked_reason LIKE 'ACADEMY_PROTECTION:%'""", member=member_code,
                )
                self._audit(conn, "ACADEMY_PROTECTION_RESUMED", follower=member_code,
                            outcome="USER_REQUESTED_AFTER_NATIVE_SAMPLE")
            if new_trigger:
                conn.run(
                    """INSERT INTO copy_trading_follower_locks(member_code,blocked,blocked_reason)
                       VALUES (:member,TRUE,:reason)
                       ON CONFLICT(member_code) DO UPDATE SET blocked=TRUE,
                         blocked_reason=CASE WHEN copy_trading_follower_locks.blocked
                           AND copy_trading_follower_locks.blocked_reason NOT LIKE 'ACADEMY_PROTECTION:%'
                           THEN copy_trading_follower_locks.blocked_reason ELSE EXCLUDED.blocked_reason END,
                         updated_at=NOW()""",
                    member=member_code, reason=f"ACADEMY_PROTECTION:{new_trigger}",
                )
                self._audit(conn, "ACADEMY_PROTECTION_TRIGGERED", follower=member_code,
                            outcome=new_trigger, details={
                                "balance_basis": "period-start native balance for objectives",
                                "equity_basis": "native equity including floating PnL for drawdown",
                                "balance": str(evaluation["balance"]), "equity": str(evaluation["equity"]),
                                "baseline_balance": str(evaluation["baseline_balance"]),
                                "peak_equity": str(evaluation["peak_equity"]),
                                "loss_drawdown_pct": str(evaluation["loss_drawdown_pct"]),
                                "gain_pct": str(evaluation["gain_pct"]),
                                "objective_pct": str(evaluation["objective_pct"]),
                            })
            if (evaluation["suspended"]
                    and str(reason or "").startswith(("DRAWDOWN_", "OBJECTIVE_"))):
                self._queue_protection_closes(conn, member_code, member, positions, pending_orders,
                                              trigger=reason, triggered_at=triggered_at or now)
            conn.run("COMMIT")
            return {"suspended": evaluation["suspended"], "reason": reason,
                    "trigger": new_trigger, "metrics": {
                        "loss_drawdown_pct": str(evaluation["loss_drawdown_pct"]),
                        "gain_pct": str(evaluation["gain_pct"]),
                        "objective_pct": str(evaluation["objective_pct"]),
                    }}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def request_protection_resume(self, member_code: str):
        """Queue an explicit, authenticated resume; a fresh native sample resets anchors."""
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                "SELECT suspended,suspension_reason FROM copy_trading_account_protections WHERE member_code=:member FOR UPDATE",
                member=str(member_code or "").strip().upper(),
            )
            if not rows or not rows[0][0]:
                conn.run("COMMIT")
                return {"status": "NOT_SUSPENDED"}
            links = conn.run(
                """SELECT
                     (SELECT COUNT(*) FROM copy_trading_position_links
                      WHERE follower_member_code=:member AND state IN ('OPEN','VERIFY_REQUIRED'))
                     +
                     (SELECT COUNT(*) FROM copy_trading_pending_order_links
                      WHERE follower_member_code=:member AND state IN ('OPEN','VERIFY_REQUIRED'))""",
                member=str(member_code or "").strip().upper(),
            )
            if links and int(links[0][0] or 0) > 0:
                raise CopyDecisionError("PROTECTION_CLOSES_PENDING",
                                        "La reprise attend la confirmation MT5 de toutes les clôtures et annulations liées.")
            active = conn.run(
                """SELECT 1 FROM copy_trading_jobs WHERE follower_member_code=:member
                   AND protection_close=TRUE AND status IN ('PENDING','LEASED','PREPARED','DISPATCHING','VERIFY_REQUIRED')
                   LIMIT 1""", member=str(member_code or "").strip().upper(),
            )
            if active:
                raise CopyDecisionError("PROTECTION_CLOSES_PENDING",
                                        "La reprise attend la résolution des jobs de protection en cours.")
            conn.run(
                """UPDATE copy_trading_account_protections SET resume_requested=TRUE,
                     resume_requested_at=NOW(),updated_at=NOW() WHERE member_code=:member""",
                member=str(member_code or "").strip().upper(),
            )
            self._audit(conn, "ACADEMY_PROTECTION_RESUME_REQUESTED",
                        follower=str(member_code or "").strip().upper(),
                        outcome=str(rows[0][1] or ""))
            conn.run("COMMIT")
            return {"status": "WAITING_FOR_NATIVE_SAMPLE"}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _queue_protection_closes(self, conn, member_code, member, positions, pending_orders,
                                 *, trigger, triggered_at):
        settings, _parsed, settings_hash = _settings_payload(_json_object(member.get("params")))
        links = conn.run(
            """SELECT l.master_member_code,l.master_position_id,l.follower_position_id,
                      l.master_symbol,l.follower_symbol,l.current_master_volume,l.current_follower_volume,
                      mp.side,mp.price_open,mp.stop_loss,mp.take_profit
               FROM copy_trading_position_links l
               LEFT JOIN copy_trading_master_positions mp
                 ON mp.master_member_code=l.master_member_code AND mp.position_id=l.master_position_id
               WHERE l.follower_member_code=:member AND l.state='OPEN'
               FOR UPDATE OF l""", member=member_code,
        )
        for row in links or []:
            master, master_position, follower_position, master_symbol, follower_symbol = map(str, row[:5])
            current_master_volume, current_follower_volume = row[5], row[6]
            master_side, master_price, master_sl, master_tp = row[7], row[8], row[9], row[10]
            in_flight = conn.run(
                """SELECT 1 FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.follower_member_code=:follower AND e.master_member_code=:master
                     AND e.position_id=:position AND j.status IN
                     ('PENDING','RETRY','LEASED','PREPARED','DISPATCHING','VERIFY_REQUIRED') LIMIT 1""",
                follower=member_code, master=master, position=master_position,
            )
            if in_flight:
                continue
            matches = _native_position_matches(positions, follower_position)
            if not matches:
                conn.run(
                    """UPDATE copy_trading_position_links SET state='CLOSED',current_master_volume=0,
                         current_follower_volume=0,closed_at=NOW(),updated_at=NOW()
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_position_id=:position AND state='OPEN'""",
                    master=master, follower=member_code, position=master_position,
                )
                self._audit(conn, "ACADEMY_PROTECTION_POSITION_ALREADY_CLOSED", master=master,
                            follower=member_code, outcome="NATIVE_POSITION_ABSENT",
                            details={"master_position_id": master_position})
                continue
            if (len(matches) != 1 or not master_side or not master_symbol
                    or str(matches[0].get("symbol") or "") != follower_symbol
                    or Decimal(str(matches[0].get("volume") or 0)) != Decimal(str(current_follower_volume or 0))):
                conn.run(
                    """UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_position_id=:position""",
                    master=master, follower=member_code, position=master_position,
                )
                conn.run(
                    """UPDATE copy_trading_follower_locks SET blocked=TRUE,
                         blocked_reason='ACADEMY_PROTECTION:POSITION_RECONCILIATION_REQUIRED',updated_at=NOW()
                       WHERE member_code=:member""", member=member_code,
                )
                self._audit(conn, "ACADEMY_PROTECTION_CLOSE_REQUIRES_RECONCILIATION", master=master,
                            follower=member_code, outcome="NATIVE_POSITION_MISMATCH",
                            details={"master_position_id": master_position, "matches": len(matches)})
                continue
            previous = conn.run(
                """SELECT j.status FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.follower_member_code=:follower AND j.protection_close=TRUE
                     AND e.master_member_code=:master AND e.position_id=:position
                   ORDER BY j.created_at DESC LIMIT 1""",
                follower=member_code, master=master, position=master_position,
            )
            if previous and str(previous[0][0]) in {
                    "PENDING", "RETRY", "LEASED", "PREPARED", "DISPATCHING", "VERIFY_REQUIRED", "COMPLETE"}:
                continue
            attempt = int((conn.run(
                """SELECT COUNT(*) FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.follower_member_code=:follower AND j.protection_close=TRUE
                     AND e.master_member_code=:master AND e.position_id=:position""",
                follower=member_code, master=master, position=master_position,
            )[0] or [0])[0]) + 1
            master_profile = self.get_member(master) or {}
            master_params = _json_object(master_profile.get("params"))
            master_login = str(master_params.get("mt_login") or "").strip()
            master_server = str(master_params.get("serveur") or master_params.get("mt_server") or "").strip()
            if not master_login or not master_server:
                self._audit(conn, "ACADEMY_PROTECTION_CLOSE_REQUIRES_RECONCILIATION", master=master,
                            follower=member_code, outcome="MASTER_PROFILE_UNAVAILABLE",
                            details={"master_position_id": master_position})
                continue
            position = {
                "position_id": master_position, "symbol": master_symbol,
                "side": str(master_side).upper(), "volume": Decimal(str(current_master_volume)),
                "price_open": Decimal(str(master_price or 0)), "stop_loss": Decimal(str(master_sl or 0)),
                "take_profit": Decimal(str(master_tp or 0)),
            }
            version = f"protection:{member_code}:{trigger}:{triggered_at.isoformat()}:{attempt}"
            event_id, key, _payload, expires = self._event(
                conn, master_code=master, master_login=master_login, master_server=master_server,
                position=position, action="CLOSE",
                observed_at=_aware_datetime(conn.run("SELECT NOW()")[0][0], "server_now"), source_version=version,
            )
            conn.run(
                """INSERT INTO copy_trading_jobs
                   (event_id,follower_member_code,expires_at,academy_settings,academy_settings_hash,protection_close)
                   VALUES (:event,:follower,:expires,:settings::jsonb,:settings_hash,TRUE)
                   ON CONFLICT(event_id,follower_member_code) DO NOTHING""",
                event=event_id, follower=member_code, expires=expires,
                settings=json.dumps(settings, separators=(",", ":")), settings_hash=settings_hash,
            )
            self._audit(conn, "ACADEMY_PROTECTION_CLOSE_QUEUED", master=master,
                        follower=member_code, idempotency_key=key, outcome=trigger,
                        details={"master_position_id": master_position, "attempt": attempt})

        self._queue_protection_pending_cancels(
            conn, member_code, member, pending_orders,
            trigger=trigger, triggered_at=triggered_at,
        )

    def _queue_protection_pending_cancels(self, conn, member_code, member, pending_orders,
                                          *, trigger, triggered_at):
        """Cancel linked pending orders after a capital protection is triggered.

        A resting order can create new exposure after the follower is suspended.
        Only an exact, complete native match is canceled. Any ambiguity quarantines
        that link so the system cannot issue a market action or resume prematurely.
        """
        if not isinstance(pending_orders, list):
            raise CopyDecisionError("NATIVE_ORDERS_INCOMPLETE",
                                    "L’instantané natif des ordres est requis pour appliquer la protection.")
        links = conn.run(
            """SELECT master_member_code,master_order_ticket,follower_order_ticket,
                      master_symbol,follower_symbol,master_type,follower_type,current_volume,
                      current_price,current_stop_loss,current_take_profit,current_stop_limit,
                      expiration_at,time_type
               FROM copy_trading_pending_order_links
               WHERE follower_member_code=:member AND state='OPEN' FOR UPDATE""",
            member=member_code,
        )
        settings, _parsed, settings_hash = _settings_payload(_json_object(member.get("params")))
        native_by_ticket = {}
        for order in pending_orders:
            if isinstance(order, dict):
                ticket = str(order.get("order_ticket") or "")
                native_by_ticket.setdefault(ticket, []).append(order)
        for row in links or []:
            (master, master_ticket, follower_ticket, master_symbol, follower_symbol,
             master_type, follower_type, volume, price, stop_loss, take_profit,
             stop_limit, expiration, time_type) = row
            master, master_ticket, follower_ticket = map(str, (master, master_ticket, follower_ticket))
            follower_type = str(follower_type or "").upper()
            matches = native_by_ticket.get(follower_ticket, [])
            native = matches[0] if len(matches) == 1 else None
            try:
                native_expiration = (_aware_datetime(native.get("expiration_at"), "native expiration")
                                     if native and native.get("expiration_at") else None)
                linked_expiration = (_aware_datetime(expiration, "linked expiration") if expiration else None)
                exact = bool(native) and all((
                    str(native.get("symbol") or "") == str(follower_symbol),
                    str(native.get("pending_type") or "").upper() == follower_type,
                    str(native.get("side") or "").upper()
                    == ("BUY" if follower_type.startswith("BUY_") else "SELL"),
                    _number(native.get("volume"), "native pending volume", positive=True) == Decimal(str(volume)),
                    _number(native.get("price_open"), "native pending price", positive=True) == Decimal(str(price)),
                    _number(native.get("stop_loss", 0), "native pending stop") == Decimal(str(stop_loss)),
                    _number(native.get("take_profit", 0), "native pending take") == Decimal(str(take_profit)),
                    _number(native.get("stop_limit", 0), "native pending stop-limit") == Decimal(str(stop_limit)),
                    int(native.get("time_type") or 0) == int(time_type or 0),
                    native_expiration == linked_expiration,
                ))
            except (CopyDecisionError, TypeError, ValueError, OverflowError):
                exact = False
            if not exact:
                reason = "ACADEMY_PROTECTION:PENDING_ORDER_RECONCILIATION_REQUIRED"
                conn.run(
                    """UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_order_ticket=:ticket AND state='OPEN'""",
                    master=master, follower=member_code, ticket=master_ticket,
                )
                conn.run(
                    """INSERT INTO copy_trading_follower_locks(member_code,blocked,blocked_reason)
                       VALUES (:member,TRUE,:reason)
                       ON CONFLICT(member_code) DO UPDATE SET blocked=TRUE,blocked_reason=:reason,updated_at=NOW()""",
                    member=member_code, reason=reason,
                )
                self._audit(conn, "ACADEMY_PROTECTION_PENDING_CANCEL_REQUIRES_RECONCILIATION",
                            master=master, follower=member_code, outcome="NATIVE_ORDER_MISMATCH",
                            details={"master_order_ticket": master_ticket,
                                     "follower_order_ticket": follower_ticket,
                                     "matches": len(matches)})
                continue

            previous = conn.run(
                """SELECT j.status FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.follower_member_code=:follower AND j.protection_close=TRUE
                     AND e.master_member_code=:master AND e.position_id=:position
                     AND e.action='PENDING_CANCEL' ORDER BY j.created_at DESC LIMIT 1""",
                follower=member_code, master=master, position=master_ticket,
            )
            if previous and str(previous[0][0]) in {
                    "PENDING", "RETRY", "LEASED", "PREPARED", "DISPATCHING", "VERIFY_REQUIRED", "COMPLETE"}:
                continue
            attempt = int((conn.run(
                """SELECT COUNT(*) FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.follower_member_code=:follower AND j.protection_close=TRUE
                     AND e.master_member_code=:master AND e.position_id=:position
                     AND e.action='PENDING_CANCEL'""",
                follower=member_code, master=master, position=master_ticket,
            )[0] or [0])[0]) + 1
            master_profile = self.get_member(master) or {}
            master_params = _json_object(master_profile.get("params"))
            master_login = str(master_params.get("mt_login") or "").strip()
            master_server = str(master_params.get("serveur") or master_params.get("mt_server") or "").strip()
            if not master_login or not master_server:
                self._audit(conn, "ACADEMY_PROTECTION_PENDING_CANCEL_REQUIRES_RECONCILIATION",
                            master=master, follower=member_code, outcome="MASTER_PROFILE_UNAVAILABLE",
                            details={"master_order_ticket": master_ticket})
                continue
            master_type = str(master_type or "").upper()
            position = {
                "position_id": master_ticket, "symbol": str(master_symbol),
                "side": "BUY" if master_type.startswith("BUY_") else "SELL",
                "volume": Decimal(str(volume)), "price_open": Decimal(str(price)),
                "stop_loss": Decimal(str(stop_loss)), "take_profit": Decimal(str(take_profit)),
            }
            version = f"protection-pending-cancel:{member_code}:{trigger}:{triggered_at.isoformat()}:{attempt}"
            event_id, key, _payload, expires = self._event(
                conn, master_code=master, master_login=master_login, master_server=master_server,
                position=position, action="PENDING_CANCEL",
                observed_at=_aware_datetime(conn.run("SELECT NOW()")[0][0], "server_now"),
                source_version=version,
                pending={"pending_type": master_type, "stop_limit": stop_limit,
                         "expiration_at": expiration, "time_type": time_type},
            )
            conn.run(
                """INSERT INTO copy_trading_jobs
                   (event_id,follower_member_code,expires_at,academy_settings,academy_settings_hash,protection_close)
                   VALUES (:event,:follower,:expires,:settings::jsonb,:settings_hash,TRUE)
                   ON CONFLICT(event_id,follower_member_code) DO NOTHING""",
                event=event_id, follower=member_code, expires=expires,
                settings=json.dumps(settings, separators=(",", ":")), settings_hash=settings_hash,
            )
            self._audit(conn, "ACADEMY_PROTECTION_PENDING_CANCEL_QUEUED", master=master,
                        follower=member_code, idempotency_key=key, outcome=trigger,
                        details={"master_order_ticket": master_ticket,
                                 "follower_order_ticket": follower_ticket})

    def _ingest_pending_order_snapshot(self, conn, *, master_code, master_login,
                                       master_server, rows, history_by_ticket,
                                       native_symbols, observed, server_now):
        existing_rows = conn.run(
            """SELECT order_ticket,symbol,side,pending_type,volume,price_open,stop_loss,
                      take_profit,stop_limit,expiration_at,time_type,source_version,is_active,
                      missing_snapshots,last_seen_at
               FROM copy_trading_master_orders WHERE master_member_code=:master FOR UPDATE""",
            master=master_code,
        )
        previous = {str(row[0]): row for row in (existing_rows or [])}
        current_ids = set()
        queued = 0
        accepted = 0
        valid_types = {"BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP",
                       "BUY_STOP_LIMIT", "SELL_STOP_LIMIT"}
        for raw in rows:
            if not isinstance(raw, dict):
                raise CopyDecisionError("INVALID_NATIVE_PENDING_ORDER", "Un ordre en attente MT5 est invalide.")
            ticket = str(raw.get("order_ticket") or "").strip()
            symbol = str(raw.get("symbol") or "").strip()
            pending_type = str(raw.get("pending_type") or "").upper()
            side = str(raw.get("side") or "").upper()
            if (not ticket.isdigit() or int(ticket) <= 0 or not symbol or pending_type not in valid_types
                    or side not in {"BUY", "SELL"} or symbol not in native_symbols):
                raise CopyDecisionError("INVALID_NATIVE_PENDING_ORDER", "Un ordre en attente ne contient pas une identité native complète.")
            volume = _number(raw.get("volume"), "pending volume", positive=True)
            price = _number(raw.get("price_open"), "pending price", positive=True)
            sl = _number(raw.get("stop_loss", 0), "pending stop_loss")
            tp = _number(raw.get("take_profit", 0), "pending take_profit")
            stop_limit = _number(raw.get("stop_limit", 0), "pending stop_limit")
            time_type = int(raw.get("time_type") or 0)
            expiration = raw.get("expiration_at")
            if expiration:
                expiration = _aware_datetime(expiration, "expiration_at")
                if expiration <= server_now:
                    raise CopyDecisionError("INVALID_NATIVE_PENDING_ORDER", "Un ordre MT5 actif annonce une expiration passée.")
            current = {"order_ticket": ticket, "symbol": symbol, "side": side,
                       "pending_type": pending_type, "volume": volume, "price_open": price,
                       "stop_loss": sl, "take_profit": tp, "stop_limit": stop_limit,
                       "expiration_at": expiration, "time_type": time_type}
            version = canonical_hash(current)
            if ticket in current_ids:
                raise CopyDecisionError("DUPLICATE_PENDING_ORDER_ID", "Le snapshot contient un ordre en attente en double.")
            current_ids.add(ticket)
            old = previous.get(ticket)
            if old is not None and bool(old[12]) and str(old[11]) == version:
                conn.run(
                    """UPDATE copy_trading_master_orders SET missing_snapshots=0,last_seen_at=:seen,
                       updated_at=NOW() WHERE master_member_code=:master AND order_ticket=:ticket""",
                    seen=observed, master=master_code, ticket=ticket,
                )
                continue

            # MT5 exposes the original setup time, not a trustworthy modification
            # timestamp. For a changed order, anchor the event at the prior complete
            # snapshot so the 120-second expiry is conservative. A first-seen order
            # must carry the native setup timestamp; poll time is never substituted.
            source_time = _pending_event_source_time(raw, old, server_now)
            if source_time is None:
                self._require_pending_order_reconciliation(
                    conn, master_code, ticket, "PENDING_SOURCE_TIMESTAMP_STALE_OR_MISSING",
                )
                self._audit(
                    conn, "PENDING_ORDER_EVENT_SKIPPED", master=master_code,
                    outcome="SOURCE_TIMESTAMP_STALE_OR_MISSING",
                    details={"master_order_ticket": ticket,
                             "first_seen": old is None or not bool(old[12])},
                )
                # Keep the native snapshot as a baseline. Any later detected change
                # is then anchored to this successful observation, never to a stale
                # order setup timestamp.
                conn.run(
                    """INSERT INTO copy_trading_master_orders
                       (master_member_code,order_ticket,symbol,side,pending_type,volume,price_open,
                        stop_loss,take_profit,stop_limit,expiration_at,time_type,source_version,
                        is_active,missing_snapshots,last_seen_at,updated_at)
                       VALUES (:master,:ticket,:symbol,:side,:type,:volume,:price,:sl,:tp,:stop_limit,
                               :expiration,:time_type,:version,TRUE,0,:seen,NOW())
                       ON CONFLICT(master_member_code,order_ticket) DO UPDATE SET
                         symbol=EXCLUDED.symbol,side=EXCLUDED.side,pending_type=EXCLUDED.pending_type,
                         volume=EXCLUDED.volume,price_open=EXCLUDED.price_open,stop_loss=EXCLUDED.stop_loss,
                         take_profit=EXCLUDED.take_profit,stop_limit=EXCLUDED.stop_limit,
                         expiration_at=EXCLUDED.expiration_at,time_type=EXCLUDED.time_type,
                         source_version=EXCLUDED.source_version,is_active=TRUE,missing_snapshots=0,
                         last_seen_at=EXCLUDED.last_seen_at,updated_at=NOW()""",
                    master=master_code, ticket=ticket, symbol=symbol, side=side, type=pending_type,
                    volume=volume, price=price, sl=sl, tp=tp, stop_limit=stop_limit,
                    expiration=expiration, time_type=time_type, version=version, seen=observed,
                )
                accepted += 1
                continue

            metadata = {"pending_type": pending_type, "stop_limit": stop_limit,
                        "expiration_at": expiration, "time_type": time_type}
            if old is not None and bool(old[12]) and (
                    str(old[1]) != symbol or str(old[2]) != side
                    or str(old[3]) != pending_type or Decimal(str(old[4])) != volume
                    or Decimal(str(old[8])) != stop_limit):
                old_order = {"position_id": ticket, "symbol": str(old[1]), "side": str(old[2]),
                             "volume": Decimal(str(old[4])), "price_open": Decimal(str(old[5])),
                             "stop_loss": Decimal(str(old[6])), "take_profit": Decimal(str(old[7]))}
                old_meta = {"pending_type": str(old[3]), "stop_limit": old[8],
                            "expiration_at": old[9], "time_type": old[10]}
                event_id, key, payload, expires = self._event(
                    conn, master_code=master_code, master_login=master_login,
                    master_server=master_server, position=old_order, action="PENDING_CANCEL",
                    observed_at=source_time, source_version=f"replace-cancel:{old[11]}:{version}",
                    pending=old_meta,
                )
                queued += self._enqueue_for_followers(conn, event_id, key, payload, expires)
                action = "PENDING_CREATE"
            else:
                action = "PENDING_CREATE" if old is None or not bool(old[12]) else "PENDING_MODIFY"
            event_id, key, payload, expires = self._event(
                conn, master_code=master_code, master_login=master_login,
                master_server=master_server, position={
                    "position_id": ticket, "symbol": symbol, "side": side, "volume": volume,
                    "price_open": price, "stop_loss": sl, "take_profit": tp,
                }, action=action, observed_at=source_time, source_version=version, pending=metadata,
            )
            queued += self._enqueue_for_followers(conn, event_id, key, payload, expires)
            conn.run(
                """INSERT INTO copy_trading_master_orders
                   (master_member_code,order_ticket,symbol,side,pending_type,volume,price_open,
                    stop_loss,take_profit,stop_limit,expiration_at,time_type,source_version,
                    is_active,missing_snapshots,last_seen_at,updated_at)
                   VALUES (:master,:ticket,:symbol,:side,:type,:volume,:price,:sl,:tp,:stop_limit,
                           :expiration,:time_type,:version,TRUE,0,:seen,NOW())
                   ON CONFLICT(master_member_code,order_ticket) DO UPDATE SET
                     symbol=EXCLUDED.symbol,side=EXCLUDED.side,pending_type=EXCLUDED.pending_type,
                     volume=EXCLUDED.volume,price_open=EXCLUDED.price_open,stop_loss=EXCLUDED.stop_loss,
                     take_profit=EXCLUDED.take_profit,stop_limit=EXCLUDED.stop_limit,
                     expiration_at=EXCLUDED.expiration_at,time_type=EXCLUDED.time_type,
                     source_version=EXCLUDED.source_version,is_active=TRUE,missing_snapshots=0,
                     last_seen_at=EXCLUDED.last_seen_at,updated_at=NOW()""",
                master=master_code, ticket=ticket, symbol=symbol, side=side, type=pending_type,
                volume=volume, price=price, sl=sl, tp=tp, stop_limit=stop_limit,
                expiration=expiration, time_type=time_type, version=version, seen=observed,
            )
            accepted += 1

        for ticket, old in previous.items():
            if ticket in current_ids or not bool(old[12]):
                continue
            missing = int(old[13] or 0) + 1
            terminal = history_by_ticket.get(ticket)
            if missing >= 2 and terminal:
                terminal_state = str(terminal["state"])
                terminal_action, native_done = _pending_terminal_event(terminal, server_now)
                if terminal_action == "QUARANTINE":
                    # A follower's linked pending order may already have filled.
                    # Never cancel it and also mirror the resulting master position:
                    # quarantine until native follower evidence resolves the state.
                    self._require_pending_order_reconciliation(
                        conn, master_code, ticket,
                        "MASTER_PENDING_ORDER_FILLED_RECONCILIATION_REQUIRED"
                        if terminal_state == "FILLED" else "PENDING_TERMINAL_EVENT_STALE_OR_UNTIMED",
                    )
                    self._audit(conn, "PENDING_ORDER_RECONCILIATION_REQUIRED", master=master_code,
                                outcome="NATIVE_MASTER_ORDER_FILLED" if terminal_state == "FILLED"
                                else "TERMINAL_TIMESTAMP_STALE_OR_MISSING",
                                details={"master_order_ticket": ticket,
                                         "terminal_state": terminal_state,
                                         "time_done_msc": terminal.get("time_done_msc")})
                    conn.run(
                        """UPDATE copy_trading_master_orders SET is_active=FALSE,missing_snapshots=:missing,
                           last_seen_at=:seen,updated_at=NOW() WHERE master_member_code=:master
                             AND order_ticket=:ticket""",
                        missing=missing, seen=observed, master=master_code, ticket=ticket,
                    )
                    continue
                action = terminal_action
                old_order = {"position_id": ticket, "symbol": str(old[1]), "side": str(old[2]),
                             "volume": Decimal(str(old[4])), "price_open": Decimal(str(old[5])),
                             "stop_loss": Decimal(str(old[6])), "take_profit": Decimal(str(old[7]))}
                old_meta = {"pending_type": str(old[3]), "stop_limit": old[8],
                            "expiration_at": old[9], "time_type": old[10]}
                event_id, key, payload, expires = self._event(
                    conn, master_code=master_code, master_login=master_login,
                    master_server=master_server, position=old_order, action=action,
                    observed_at=native_done,
                    source_version=f"terminal:{terminal_state}:{terminal.get('time_done_msc')}",
                    pending=old_meta,
                )
                queued += self._enqueue_for_followers(conn, event_id, key, payload, expires)
                conn.run(
                    """UPDATE copy_trading_master_orders SET is_active=FALSE,missing_snapshots=:missing,
                       last_seen_at=:seen,updated_at=NOW() WHERE master_member_code=:master
                         AND order_ticket=:ticket""",
                    missing=missing, seen=observed, master=master_code, ticket=ticket,
                )
            else:
                conn.run(
                    """UPDATE copy_trading_master_orders SET missing_snapshots=:missing,updated_at=NOW()
                       WHERE master_member_code=:master AND order_ticket=:ticket""",
                    missing=missing, master=master_code, ticket=ticket,
                )
        return accepted, queued

    def ingest_master_snapshot(self, master_code: str, account: dict, positions: list[dict],
                               native_symbols: dict, observed_at, close_deals=None,
                               pending_orders=None, pending_order_history=None) -> dict:
        """Persist one complete native snapshot and enqueue idempotent jobs.

        An empty positions list is considered authoritative only after a
        successful MT5 response. The worker rejects ``None`` responses before
        this method is called. Close detection requires two complete snapshots.
        """
        if not isinstance(positions, list) or len(positions) > 5000:
            raise CopyDecisionError("SNAPSHOT_TOO_LARGE", "Le snapshot MT5 dépasse la limite autorisée.")
        if not isinstance(native_symbols, dict):
            raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Les preuves natives MT5 sont invalides.")
        if close_deals is None:
            close_deals = []
        if not isinstance(close_deals, list) or len(close_deals) > 1000:
            raise CopyDecisionError("INVALID_CLOSE_EVIDENCE", "Les preuves natives de clôture MT5 sont invalides.")
        pending_orders = [] if pending_orders is None else pending_orders
        pending_order_history = [] if pending_order_history is None else pending_order_history
        if (not isinstance(pending_orders, list) or len(pending_orders) > 5000
                or not isinstance(pending_order_history, list) or len(pending_order_history) > 1000):
            raise CopyDecisionError("INVALID_PENDING_ORDER_EVIDENCE", "Les preuves natives des ordres en attente sont invalides.")
        history_by_ticket = {
            str(item["order_ticket"]): item for item in pending_order_history
            if isinstance(item, dict) and str(item.get("order_ticket") or "").isdigit()
            and item.get("state") in {"CANCELED", "FILLED", "REJECTED", "EXPIRED"}
        }
        deals_by_position = {}
        for deal in close_deals:
            if not isinstance(deal, dict) or deal.get("entry") not in {1, 3}:
                continue
            position_id = str(deal.get("position_id") or "").strip()
            native_msc = _native_update_msc(deal.get("time_msc"))
            try:
                deal_ticket = int(deal.get("ticket") or 0)
            except (TypeError, ValueError, OverflowError):
                deal_ticket = 0
            if position_id and native_msc and deal_ticket > 0:
                old_deal = deals_by_position.get(position_id)
                if old_deal is None or native_msc > old_deal[0]:
                    deals_by_position[position_id] = (native_msc, deal_ticket)
        observed = _aware_datetime(observed_at)
        master_code = str(master_code or "").strip().upper()
        conn = self.get_conn()
        queued = 0
        accepted = 0
        try:
            conn.run("BEGIN")
            master_rows = conn.run(
                """SELECT a.enabled,m.params FROM copy_trading_master_accounts a
                   JOIN members m ON m.code=a.member_code WHERE a.member_code=:code FOR UPDATE OF a""",
                code=master_code,
            )
            master = _row_dicts(conn, master_rows)
            if not master or not master[0]["enabled"]:
                raise CopyDecisionError("MASTER_NOT_RELEASED", "Le compte maître n’est pas autorisé à publier des événements.")
            profile = _json_object(master[0]["params"])
            expected_login = str(profile.get("mt_login") or "").strip()
            expected_server = str(profile.get("serveur") or profile.get("mt_server") or "").strip()
            if not expected_login or str(account.get("login") or "").strip() != expected_login:
                raise CopyDecisionError("MASTER_IDENTITY_MISMATCH", "Le login natif ne correspond pas au profil maître configuré.")
            if not expected_server or str(account.get("server") or "").strip() != expected_server:
                raise CopyDecisionError("MASTER_SERVER_MISMATCH", "Le serveur natif ne correspond pas au profil maître configuré.")
            if account.get("terminal_connected") is not True:
                raise CopyDecisionError("TERMINAL_CONNECTION_FAILED", "Le terminal maître n’est pas connecté selon son état natif.")

            for symbol, evidence in (native_symbols or {}).items():
                symbol = str(symbol or "").strip()
                if not symbol or not isinstance(evidence, dict) or str(evidence.get("name") or "").strip() != symbol:
                    raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Le symbole maître n’est pas appuyé par une preuve MT5 native.")
                evidence_hash = canonical_hash(evidence)
                conn.run(
                    """INSERT INTO copy_trading_native_symbols
                       (master_member_code,symbol,evidence,evidence_hash,observed_at)
                       VALUES (:master,:symbol,:evidence::jsonb,:hash,:observed)
                       ON CONFLICT (master_member_code,symbol) DO UPDATE SET
                         evidence=EXCLUDED.evidence,evidence_hash=EXCLUDED.evidence_hash,
                         observed_at=EXCLUDED.observed_at""",
                    master=master_code, symbol=symbol,
                    evidence=json.dumps(evidence, separators=(",", ":")),
                    hash=evidence_hash, observed=observed,
                )

            previous_rows = conn.run(
                """SELECT position_id,symbol,side,volume,price_open,stop_loss,take_profit,
                          time_update_msc,is_open,missing_snapshots
                   FROM copy_trading_master_positions WHERE master_member_code=:master FOR UPDATE""",
                master=master_code,
            )
            previous = {str(row[0]): row for row in (previous_rows or [])}
            server_now = _aware_datetime(conn.run("SELECT NOW()")[0][0], "server_now")
            pending_accepted, pending_queued = self._ingest_pending_order_snapshot(
                conn, master_code=master_code, master_login=expected_login,
                master_server=expected_server, rows=pending_orders,
                history_by_ticket=history_by_ticket, native_symbols=native_symbols,
                observed=observed, server_now=server_now,
            )
            accepted += pending_accepted
            queued += pending_queued
            current_ids = set()
            for raw in positions:
                position_id = str(raw.get("position_id") or "").strip()
                symbol = str(raw.get("symbol") or "").strip()
                side = str(raw.get("side") or "").strip().upper()
                if not position_id or not symbol or side not in {"BUY", "SELL"}:
                    raise CopyDecisionError("INVALID_NATIVE_POSITION", "Une position maître ne contient pas d’identité native complète.")
                if symbol not in native_symbols:
                    raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Une position n’a pas de preuve native de symbole.")
                volume = _number(raw.get("volume"), "volume", positive=True)
                price = _number(raw.get("price_open"), "price_open")
                sl = _number(raw.get("stop_loss", 0), "stop_loss")
                tp = _number(raw.get("take_profit", 0), "take_profit")
                if position_id in current_ids:
                    raise CopyDecisionError("DUPLICATE_POSITION_ID", "Le snapshot contient une position maître en double.")
                current_ids.add(position_id)
                old = previous.get(position_id)
                action = "OPEN" if old is None or not old[8] else "RECONCILE"
                position = {"position_id": position_id, "symbol": symbol, "side": side,
                            "volume": volume, "price_open": price, "stop_loss": sl, "take_profit": tp}
                unchanged = old is not None and old[8] and all((
                    str(old[1]) == symbol,
                    str(old[2]) == side,
                    Decimal(str(old[3])) == volume,
                    Decimal(str(old[4])) == price,
                    Decimal(str(old[5])) == sl,
                    Decimal(str(old[6])) == tp,
                ))
                if unchanged:
                    conn.run(
                        """UPDATE copy_trading_master_positions SET missing_snapshots=0,
                           time_update_msc=COALESCE(NULLIF(:version,0),time_update_msc),
                           last_seen_at=:seen,updated_at=NOW()
                           WHERE master_member_code=:master AND position_id=:position""",
                        version=_native_update_msc(raw.get("time_update_msc")) or 0,
                        seen=observed, master=master_code, position=position_id,
                    )
                    continue
                native_msc = _native_update_msc(raw.get("time_update_msc"))
                native_event_time = _native_update_datetime(native_msc)
                version = f"{native_msc or 'missing'}:{canonical_hash(position)}"
                if native_event_time is None:
                    self._audit(conn, "MASTER_EVENT_SKIPPED", master=master_code,
                                outcome="NATIVE_POSITION_TIME_MISSING",
                                details={"position_id": position_id})
                    if action == "RECONCILE":
                        self._require_position_reconciliation(
                            conn, master_code, position_id,
                            "MASTER_EVENT_NATIVE_POSITION_TIME_MISSING",
                        )
                else:
                    try:
                        ensure_event_fresh(native_event_time, server_now)
                    except CopyDecisionError as error:
                        self._audit(conn, "MASTER_EVENT_SKIPPED", master=master_code,
                                    outcome=error.code,
                                    details={"position_id": position_id,
                                             "source_time": native_event_time.isoformat()})
                        if action in {"RECONCILE", "CLOSE"}:
                            self._require_position_reconciliation(
                                conn, master_code, position_id,
                                f"MASTER_EVENT_{error.code}",
                            )
                    else:
                        event_id, key, payload, expires = self._event(
                            conn, master_code=master_code, master_login=expected_login,
                            master_server=expected_server, position=position, action=action,
                            observed_at=native_event_time, source_version=version,
                        )
                        queued += self._enqueue_for_followers(conn, event_id, key, payload, expires)
                conn.run(
                    """INSERT INTO copy_trading_master_positions
                       (master_member_code,position_id,symbol,side,volume,price_open,stop_loss,take_profit,
                        time_update_msc,is_open,missing_snapshots,last_seen_at,updated_at)
                       VALUES (:master,:position,:symbol,:side,:volume,:price,:sl,:tp,:version,TRUE,0,:seen,NOW())
                       ON CONFLICT (master_member_code,position_id) DO UPDATE SET
                         symbol=EXCLUDED.symbol,side=EXCLUDED.side,volume=EXCLUDED.volume,
                         price_open=EXCLUDED.price_open,stop_loss=EXCLUDED.stop_loss,
                         take_profit=EXCLUDED.take_profit,time_update_msc=EXCLUDED.time_update_msc,
                         is_open=TRUE,missing_snapshots=0,last_seen_at=EXCLUDED.last_seen_at,updated_at=NOW()""",
                    master=master_code, position=position_id, symbol=symbol, side=side,
                    volume=volume, price=price, sl=sl, tp=tp, version=native_msc or 0, seen=observed,
                )
                accepted += 1

            for position_id, old in previous.items():
                if position_id in current_ids or not old[8]:
                    continue
                missing_count = int(old[9] or 0) + 1
                if missing_count >= 2:
                    deal_evidence = deals_by_position.get(position_id)
                    if deal_evidence is None:
                        if missing_count == 2:
                            self._audit(conn, "MASTER_CLOSE_UNCONFIRMED", master=master_code,
                                        outcome="NATIVE_CLOSE_DEAL_MISSING",
                                        details={"position_id": position_id})
                        conn.run(
                            """UPDATE copy_trading_master_positions SET missing_snapshots=:missing,updated_at=NOW()
                               WHERE master_member_code=:master AND position_id=:position""",
                            missing=missing_count, master=master_code, position=position_id,
                        )
                        continue
                    position = {"position_id": position_id, "symbol": old[1], "side": old[2],
                                "volume": Decimal(str(old[3])), "price_open": Decimal(str(old[4])),
                                "stop_loss": Decimal(str(old[5])), "take_profit": Decimal(str(old[6]))}
                    native_event_time = _native_update_datetime(deal_evidence[0])
                    if native_event_time is None:
                        self._audit(conn, "MASTER_CLOSE_UNCONFIRMED", master=master_code,
                                    outcome="NATIVE_CLOSE_TIME_INVALID",
                                    details={"position_id": position_id,
                                             "deal_ticket": deal_evidence[1]})
                        conn.run(
                            """UPDATE copy_trading_master_positions SET missing_snapshots=:missing,updated_at=NOW()
                               WHERE master_member_code=:master AND position_id=:position""",
                            missing=missing_count, master=master_code, position=position_id,
                        )
                        continue
                    try:
                        ensure_event_fresh(native_event_time, server_now)
                    except CopyDecisionError as error:
                        self._audit(conn, "MASTER_EVENT_SKIPPED", master=master_code,
                                    outcome=error.code,
                                    details={"position_id": position_id,
                                             "deal_ticket": deal_evidence[1],
                                             "source_time": native_event_time.isoformat()})
                        self._require_position_reconciliation(
                            conn, master_code, position_id, f"MASTER_CLOSE_{error.code}",
                        )
                    else:
                        event_id, key, payload, expires = self._event(
                            conn, master_code=master_code, master_login=expected_login,
                            master_server=expected_server, position=position, action="CLOSE",
                            observed_at=native_event_time,
                            source_version=f"close:{deal_evidence[1]}:{deal_evidence[0]}",
                        )
                        queued += self._enqueue_for_followers(conn, event_id, key, payload, expires)
                    conn.run(
                        """UPDATE copy_trading_master_positions SET is_open=FALSE,
                           missing_snapshots=:missing,last_seen_at=:seen,updated_at=NOW()
                           WHERE master_member_code=:master AND position_id=:position""",
                        missing=missing_count, seen=observed, master=master_code, position=position_id,
                    )
                else:
                    conn.run(
                        """UPDATE copy_trading_master_positions SET missing_snapshots=:missing,updated_at=NOW()
                           WHERE master_member_code=:master AND position_id=:position""",
                        missing=missing_count, master=master_code, position=position_id,
                    )
            conn.run("COMMIT")
            return {"positions": accepted, "jobs_queued": queued, "observed_at": observed.isoformat()}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def claim_job(self, worker_id: str, *, require_demo_e2e: bool = False):
        """Lease one fresh job; the lease cannot outlive the source event."""
        conn = self.get_conn()
        token = secrets.token_urlsafe(32)
        try:
            conn.run("BEGIN")
            conn.run(
                """UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',
                   lease_token_hash='',lease_expires_at=NULL,updated_at=NOW()
                   WHERE status IN ('PENDING','RETRY','LEASED','PREPARED') AND expires_at<=NOW()"""
            )
            conn.run(
                """UPDATE copy_trading_events SET status='EXPIRED'
                   WHERE status='READY' AND expires_at<=NOW()"""
            )
            conn.run(
                """UPDATE copy_trading_follower_locks l SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW()
                   FROM copy_trading_jobs j WHERE l.job_id=j.job_id AND j.status='EXPIRED'
                     AND l.blocked=FALSE"""
            )
            rows = conn.run(
                """SELECT j.job_id::text,e.event_id::text,e.master_member_code,e.position_id,
                          e.action,e.symbol,e.side,e.volume,e.price_open,e.stop_loss,e.take_profit,
                          e.source_version,e.source_time,e.expires_at,j.academy_settings,
                          j.academy_settings_hash,j.protection_close,m.code,
                          CASE WHEN sm.state='VERIFIED' THEN sm.follower_symbol ELSE e.symbol END AS mapped_symbol,
                          COALESCE(sm.state,'') AS mapping_state,
                          ns.evidence AS master_evidence,e.pending_type,e.stop_limit,
                          e.expiration_at,e.time_type,pending_link.follower_order_ticket
                   FROM copy_trading_jobs j
                   JOIN copy_trading_events e ON e.event_id=j.event_id
                   JOIN members m ON m.code=j.follower_member_code
                   JOIN copy_trading_consents c ON c.member_code=m.code
                   LEFT JOIN copy_trading_follower_locks l ON l.member_code=m.code
                   LEFT JOIN copy_trading_symbol_mappings sm
                     ON sm.master_member_code=e.master_member_code
                    AND sm.follower_member_code=m.code AND sm.master_symbol=e.symbol
                   LEFT JOIN copy_trading_native_symbols ns
                     ON ns.master_member_code=e.master_member_code AND ns.symbol=e.symbol
                   LEFT JOIN copy_trading_pending_order_links pending_link
                     ON pending_link.master_member_code=e.master_member_code
                    AND pending_link.follower_member_code=m.code
                    AND pending_link.master_order_ticket=e.position_id
                    AND pending_link.state='OPEN'
                   WHERE e.status='READY' AND e.expires_at>NOW() AND j.expires_at>NOW()
                     AND ((j.status IN ('PENDING','RETRY')) OR
                          (j.status IN ('LEASED','PREPARED') AND j.lease_expires_at<=NOW()))
                     AND c.revoked_at IS NULL AND c.consented_at IS NOT NULL
                     AND c.consent_version=:consent_version
                     AND m.actif=TRUE AND (COALESCE(m.copy_actif,FALSE)=TRUE OR j.protection_close=TRUE)
                     AND COALESCE(m.access_level,'member') NOT IN ('explorer','demo')
                     AND NOT COALESCE(m.admin_suspended,FALSE)
                     AND (NOT :require_demo_e2e OR EXISTS (
                         SELECT 1 FROM copy_trading_jobs demo_job
                         WHERE demo_job.follower_member_code=j.follower_member_code
                           AND demo_job.status='COMPLETE'
                           AND demo_job.native_evidence #>> '{market,is_demo}'='true'
                           AND demo_job.result_payload #>> '{native_postcondition,terminal_connected}'='true'
                           AND demo_job.result_payload #>> '{native_postcondition,terminal_path_verified}'='true'
                           AND demo_job.updated_at>NOW()-INTERVAL '24 hours'
                     ))
                     AND (NOT COALESCE(l.blocked,FALSE) OR
                          (j.protection_close=TRUE AND l.blocked_reason LIKE 'ACADEMY_PROTECTION:%'))
                     AND (l.member_code IS NULL OR l.lease_expires_at IS NULL
                          OR l.lease_expires_at<=NOW() OR l.job_id=j.job_id)
                     AND NOT EXISTS (
                         SELECT 1 FROM copy_trading_jobs active_j
                         WHERE active_j.follower_member_code=j.follower_member_code
                           AND active_j.job_id<>j.job_id
                           AND active_j.status IN ('DISPATCHING','VERIFY_REQUIRED')
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM copy_trading_jobs same_position_j
                         JOIN copy_trading_events same_position_e ON same_position_e.event_id=same_position_j.event_id
                         WHERE same_position_j.follower_member_code=j.follower_member_code
                           AND same_position_e.master_member_code=e.master_member_code
                           AND same_position_e.position_id=e.position_id
                           AND same_position_j.job_id<>j.job_id
                           AND same_position_j.status IN ('PENDING','LEASED','PREPARED','DISPATCHING','VERIFY_REQUIRED')
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM copy_trading_pending_order_links pending_link
                         WHERE pending_link.follower_member_code=j.follower_member_code
                           AND pending_link.state='VERIFY_REQUIRED'
                     )
                   ORDER BY e.created_at,j.created_at
                   LIMIT 1 FOR UPDATE OF j SKIP LOCKED""",
                consent_version=self.consent_version,
                require_demo_e2e=bool(require_demo_e2e),
            )
            item = _row_dicts(conn, rows)
            if not item:
                # A broker dispatch with unknown outcome is deliberately not
                # reclaimed or replayed, even after the worker disappears.
                conn.run("UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='EVENT_EXPIRED',updated_at=NOW() WHERE status IN ('PENDING','RETRY','LEASED','PREPARED') AND expires_at<=NOW()")
                conn.run("UPDATE copy_trading_events SET status='EXPIRED' WHERE status='READY' AND expires_at<=NOW()")
                conn.run("COMMIT")
                return None
            job = item[0]
            lock_rows = conn.run(
                """INSERT INTO copy_trading_follower_locks(member_code,job_id,lease_expires_at,blocked,blocked_reason)
                   VALUES (:member,:job,LEAST(NOW()+INTERVAL '30 seconds',:expires),FALSE,'')
                   ON CONFLICT(member_code) DO UPDATE SET job_id=EXCLUDED.job_id,
                     lease_expires_at=EXCLUDED.lease_expires_at,updated_at=NOW()
                   WHERE (copy_trading_follower_locks.blocked=FALSE OR
                          (:protection_close AND copy_trading_follower_locks.blocked_reason LIKE 'ACADEMY_PROTECTION:%'))
                     AND (copy_trading_follower_locks.lease_expires_at IS NULL
                          OR copy_trading_follower_locks.lease_expires_at<=NOW()
                          OR copy_trading_follower_locks.job_id=EXCLUDED.job_id)
                   RETURNING member_code""",
                member=job["code"], job=job["job_id"], expires=job["expires_at"],
                protection_close=bool(job.get("protection_close")),
            )
            if not lock_rows:
                conn.run("COMMIT")
                return None
            conn.run(
                """UPDATE copy_trading_jobs SET status='LEASED',worker_id=:worker,
                   lease_token_hash=:token_hash,lease_expires_at=LEAST(NOW()+INTERVAL '30 seconds',expires_at),
                   attempts=attempts+1,updated_at=NOW() WHERE job_id=:job""",
                worker=worker_id, token_hash=lease_token_hash(token), job=job["job_id"],
            )
            self._audit(conn, "FOLLOWER_JOB_CLAIMED", master=job.get("master_member_code", ""),
                        follower=job["code"], job=job["job_id"],
                        outcome="LEASED", details={"worker_id": worker_id})
            conn.run("COMMIT")
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

        # Decrypt credentials only after committing the short claim transaction;
        # never persist or log them in this service.
        member = self.get_member(job["code"])
        params = dict((member or {}).get("params") or {})
        login = str(params.get("mt_login") or "").strip()
        server = str(params.get("serveur") or params.get("mt_server") or "").strip()
        password = str(params.get("mt_password") or "")
        if not login or not server or not password:
            self.fail_before_dispatch(job["job_id"], token, "FOLLOWER_CONNECTION_NOT_CONFIGURED", retryable=False)
            raise CopyDecisionError("FOLLOWER_CONNECTION_NOT_CONFIGURED", "Le profil MT5 suiveur est incomplet.")
        job.update({"lease_token": token, "follower_member_code": job["code"],
                    "follower_login": login, "follower_server": server,
                    "follower_password": password,
                    "protection_close": bool(job.get("protection_close")),
                    "source_time": _aware_datetime(job["source_time"]).isoformat(),
                    "expires_at": _aware_datetime(job["expires_at"]).isoformat(),
                    "academy_settings": _json_object(job.get("academy_settings")),
                    "mapped_symbol": str(job.get("mapped_symbol") or job.get("symbol") or ""),
                    "master_evidence": _json_object(job.get("master_evidence")),
                    "pending_type": str(job.get("pending_type") or ""),
                    "stop_limit": str(job.get("stop_limit") or "0"),
                    "expiration_at": _aware_datetime(job["expiration_at"]).isoformat()
                        if job.get("expiration_at") else None,
                    "time_type": int(job.get("time_type") or 0)})
        job.pop("code", None)
        return job

    def record_mapping_candidates(self, job_id, token, candidates):
        """Persist compatible native aliases for explicit admin review only."""
        if not isinstance(candidates, list) or len(candidates) > 200:
            raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Liste de preuves candidates invalide.")
        conn = self.get_conn()
        recorded = []
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT j.status,j.expires_at,j.lease_expires_at,j.lease_token_hash,
                          j.follower_member_code,e.master_member_code,e.symbol
                   FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.job_id=:job FOR UPDATE OF j""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            state, expires, lease_expires, expected_hash, follower, master, master_symbol = rows[0]
            now = conn.run("SELECT NOW()")[0][0]
            if (state != "LEASED" or not token or lease_token_hash(token) != expected_hash
                    or expires <= now or lease_expires is None or lease_expires <= now):
                raise CopyDecisionError("LEASE_INVALID", "Le bail de collecte des preuves n’est plus valide.")
            master_rows = conn.run(
                "SELECT evidence FROM copy_trading_native_symbols WHERE master_member_code=:master AND symbol=:symbol",
                master=master, symbol=master_symbol,
            )
            if not master_rows:
                raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "La preuve native maître n’est plus disponible.")
            master_evidence = _json_object(master_rows[0][0])
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                name = str(candidate.get("name") or "").strip()
                if not name:
                    continue
                mapping = verify_native_symbol_mapping(master_evidence, candidate)
                if mapping["state"] != "PENDING_REVIEW":
                    continue
                conn.run(
                    """INSERT INTO copy_trading_symbol_candidates
                       (master_member_code,follower_member_code,master_symbol,follower_symbol,
                        native_evidence,evidence_hash,state,updated_at)
                       VALUES (:master,:follower,:master_symbol,:follower_symbol,:evidence::jsonb,
                               :hash,'PENDING_REVIEW',NOW())
                       ON CONFLICT (master_member_code,follower_member_code,master_symbol,follower_symbol)
                       DO UPDATE SET native_evidence=EXCLUDED.native_evidence,
                         evidence_hash=EXCLUDED.evidence_hash,state='PENDING_REVIEW',updated_at=NOW()""",
                    master=master, follower=follower, master_symbol=master_symbol,
                    follower_symbol=name,
                    evidence=json.dumps(mapping["evidence"], separators=(",", ":")),
                    hash=mapping["evidence_hash"],
                )
                recorded.append(name)
            conn.run("COMMIT")
            return {"state": "PENDING_REVIEW", "candidates": recorded}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _update_leased(self, job_id, token, sql, **params):
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT status,expires_at,lease_expires_at,lease_token_hash
                   FROM copy_trading_jobs WHERE job_id=:job FOR UPDATE""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            state, expires, lease_expires, expected_hash = rows[0]
            now_rows = conn.run("SELECT NOW()")
            now = now_rows[0][0]
            if state not in {"LEASED", "PREPARED"} or not token or lease_token_hash(token) != expected_hash:
                raise CopyDecisionError("LEASE_INVALID", "Le bail de préparation n’est plus valide.")
            if expires <= now or lease_expires is None or lease_expires <= now:
                conn.run("UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("EVENT_EXPIRED", "L’événement maître a dépassé son délai absolu de 120 secondes.")
            result = conn.run(sql, job=job_id, **params)
            conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE job_id=:job", job=job_id)
            conn.run("COMMIT")
            return result
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _resolve_missing_pending_order(self, conn, *, job_id, follower_code, master_code,
                                       order_ticket, market):
        """Use native history before declaring an absent linked order terminal."""
        history = market.get("pending_order_history")
        master_ticket = str(order_ticket)
        follower_ticket = str(market.get("follower_pending_order_ticket") or "")
        matches = ([row for row in history if isinstance(row, dict)
                    and str(row.get("order_ticket") or "") == follower_ticket]
                   if isinstance(history, list) else [])
        states = {str(row.get("state") or "").upper() for row in matches}
        if (follower_ticket.isdigit() and market.get("pending_order_history_complete") is True and len(matches) == 1
                and len(states) == 1 and next(iter(states)) in {"CANCELED", "REJECTED", "EXPIRED"}):
            terminal_state = next(iter(states))
            conn.run(
                """UPDATE copy_trading_pending_order_links SET state='CLOSED',updated_at=NOW()
                   WHERE master_member_code=:master AND follower_member_code=:follower
                     AND master_order_ticket=:ticket AND state='OPEN'""",
                master=master_code, follower=follower_code, ticket=master_ticket,
            )
            conn.run(
                """UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',
                   failure_code='NATIVE_PENDING_ORDER_ALREADY_TERMINAL',updated_at=NOW()
                   WHERE job_id=:job""", job=job_id,
            )
            conn.run(
                """UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW()
                   WHERE member_code=:follower AND job_id=:job""",
                follower=follower_code, job=job_id,
            )
            self._audit(conn, "PENDING_ORDER_TERMINAL_HISTORY_CONFIRMED", master=master_code,
                        follower=follower_code, job=job_id, outcome=terminal_state,
                        details={"master_order_ticket": master_ticket,
                                 "follower_order_ticket": follower_ticket})
            conn.run("COMMIT")
            return {"status": "COMPLETE", "execution_action": "NOOP"}

        state = next(iter(states)) if len(states) == 1 else "UNKNOWN"
        reason = ("PENDING_ORDER_FILLED_REQUIRES_POSITION_RECONCILIATION"
                  if state == "FILLED" else "PENDING_ORDER_HISTORY_UNVERIFIED")
        conn.run(
            """UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW()
               WHERE master_member_code=:master AND follower_member_code=:follower
                 AND master_order_ticket=:ticket""",
            master=master_code, follower=follower_code, ticket=master_ticket,
        )
        conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code=:reason,updated_at=NOW() WHERE job_id=:job",
                 reason=reason, job=job_id)
        conn.run(
            """INSERT INTO copy_trading_follower_locks(member_code,blocked,blocked_reason)
               VALUES (:follower,TRUE,:reason)
               ON CONFLICT(member_code) DO UPDATE SET blocked=TRUE,blocked_reason=:reason,updated_at=NOW()""",
            follower=follower_code, reason=reason,
        )
        self._audit(conn, "PENDING_ORDER_RECONCILIATION_REQUIRED", master=master_code,
                    follower=follower_code, job=job_id, outcome=reason,
                 details={"master_order_ticket": master_ticket,
                             "follower_order_ticket": follower_ticket,
                             "native_history_states": sorted(states)})
        conn.run("COMMIT")
        return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}

    def _prepare_pending_job(self, conn, *, job_id, follower_code, master_code, order_ticket,
                             action, master_symbol, side, source_volume, source_price,
                             source_sl, source_tp, pending_type, stop_limit, expiration_at,
                             time_type, settings, raw_settings, market, symbol,
                             follower_evidence, mapping, request_comment, calendar_region,
                             protection_close=False):
        if (not protection_close and str(raw_settings.get("copier_ordres_en_attente", False)).strip().lower()
                not in {"1", "true", "yes", "on"}):
            raise CopyDecisionError("PENDING_ORDER_COPY_DISABLED", "La copie des ordres en attente est désactivée pour ce compte.")
        if not protection_close and enabled_unsupported_pending_order_settings(raw_settings):
            raise CopyDecisionError("PENDING_CONVERSION_UNSUPPORTED", "La conversion d’un ordre pending invalide en ordre marché reste désactivée.")
        orders = market.get("orders")
        if not isinstance(orders, list) or market.get("orders_complete") is not True:
            raise CopyDecisionError("NATIVE_ORDERS_INCOMPLETE", "La liste MT5 des ordres en attente n’est pas complète.")
        link_rows = conn.run(
            """SELECT follower_order_ticket,follower_symbol,follower_type,current_volume,current_price,
                      current_stop_loss,current_take_profit,current_stop_limit,expiration_at,time_type,state
               FROM copy_trading_pending_order_links WHERE master_member_code=:master
                 AND follower_member_code=:follower AND master_order_ticket=:ticket FOR UPDATE""",
            master=master_code, follower=follower_code, ticket=order_ticket,
        )
        link = link_rows[0] if link_rows else None
        existing = []
        if link and str(link[10]) == "OPEN":
            existing = [item for item in orders if isinstance(item, dict)
                        and str(item.get("order_ticket") or "") == str(link[0])]
            if len(existing) != 1:
                if not existing and action in {"PENDING_CANCEL", "PENDING_EXPIRE"}:
                    return self._resolve_missing_pending_order(
                        conn, job_id=job_id, follower_code=follower_code,
                        master_code=master_code, order_ticket=order_ticket,
                        market=market,
                    )
                reason = "PENDING_ORDER_LINK_MISSING" if not existing else "PENDING_ORDER_NOT_UNIQUE"
                conn.run("""UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                           WHERE master_member_code=:master AND follower_member_code=:follower
                             AND master_order_ticket=:ticket""",
                         master=master_code, follower=follower_code, ticket=order_ticket)
                conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code=:reason,updated_at=NOW() WHERE job_id=:job",
                         reason=reason, job=job_id)
                conn.run("""UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason=:reason,
                           updated_at=NOW() WHERE member_code=:follower""", reason=reason, follower=follower_code)
                self._audit(conn, "PENDING_ORDER_RECONCILIATION_REQUIRED", master=master_code,
                            follower=follower_code, job=job_id, outcome=reason,
                            details={"master_order_ticket": order_ticket})
                conn.run("COMMIT")
                return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
            native = existing[0]
            expected = {
                "symbol": str(link[1]), "pending_type": str(link[2]).upper(),
                "volume": Decimal(str(link[3])), "price_open": Decimal(str(link[4])),
                "stop_loss": Decimal(str(link[5])), "take_profit": Decimal(str(link[6])),
                "stop_limit": Decimal(str(link[7])), "time_type": int(link[9] or 0),
            }
            try:
                native_expiration = (_aware_datetime(native.get("expiration_at"), "native expiration")
                                     if native.get("expiration_at") else None)
                linked_expiration = (_aware_datetime(link[8], "linked expiration") if link[8] else None)
                matches_link = all((
                    str(native.get("symbol") or "") == expected["symbol"],
                    str(native.get("pending_type") or "").upper() == expected["pending_type"],
                    _number(native.get("volume"), "native pending volume", positive=True) == expected["volume"],
                    _number(native.get("price_open"), "native pending price", positive=True) == expected["price_open"],
                    _number(native.get("stop_loss", 0), "native pending stop") == expected["stop_loss"],
                    _number(native.get("take_profit", 0), "native pending take") == expected["take_profit"],
                    _number(native.get("stop_limit", 0), "native pending stop-limit") == expected["stop_limit"],
                    int(native.get("time_type") or 0) == expected["time_type"],
                    native_expiration == linked_expiration,
                ))
            except (CopyDecisionError, TypeError, ValueError, OverflowError):
                matches_link = False
            if not matches_link:
                conn.run("UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_order_ticket=:ticket",
                         master=master_code, follower=follower_code, ticket=order_ticket)
                conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='PENDING_ORDER_STATE_MISMATCH',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='PENDING_ORDER_STATE_MISMATCH',updated_at=NOW() WHERE member_code=:follower", follower=follower_code)
                conn.run("COMMIT")
                return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
        if action in {"PENDING_CANCEL", "PENDING_EXPIRE"}:
            if not link or str(link[10]) != "OPEN":
                conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='NO_LINKED_PENDING_ORDER',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:follower AND job_id=:job", follower=follower_code, job=job_id)
                conn.run("COMMIT")
                return {"status": "COMPLETE", "execution_action": "NOOP"}
            native_ticket = str(link[0])
            target_volume = _number(link[3], "linked pending volume", positive=True)
            plan = {
                "action": action, "master_order_ticket": str(order_ticket),
                "follower_order_ticket": str(link[0]),
                "master_pending_type": str(pending_type).upper(),
                "pending_type": str(link[2]).upper(),
                "side": str(existing[0].get("side") or ""),
                "price": str(link[4]), "volume": str(target_volume),
                "stop_loss": str(link[5]), "take_profit": str(link[6]),
                "stop_limit": str(link[7]), "time_type": int(link[9] or 0),
                "expiration_at": _aware_datetime(link[8]).isoformat() if link[8] else None,
                "follower_before": {"ticket": str(link[0])},
            }
            evidence = {"master": {}, "follower": dict(follower_evidence),
                        "mapping_hash": mapping["evidence_hash"],
                        "market": {"login": str(market.get("login") or ""),
                                   "server": str(market.get("server") or ""),
                                   "terminal_connected": market.get("terminal_connected") is True,
                                   "is_demo": market.get("is_demo") is True,
                                   "calendar_region": str(calendar_region or "ALL")[:40]},
                        "plan": plan}
            result = conn.run(
                """UPDATE copy_trading_jobs SET status='PREPARED',follower_symbol=:symbol,
                   normalized_volume=:volume,native_evidence=:evidence::jsonb,
                   execution_action=:action,request_comment=:comment,updated_at=NOW()
                   WHERE job_id=:job AND status='LEASED'
                   RETURNING job_id::text,native_evidence,normalized_volume,request_comment,execution_action,follower_symbol""",
                symbol=str(symbol)[:64], volume=target_volume,
                evidence=json.dumps(evidence, separators=(",", ":")), action=action,
                comment=str(request_comment)[:31], job=job_id,
            )
            if not result:
                raise CopyDecisionError("ATOMIC_HANDOFF_REJECTED", "La préparation de l’annulation pending a échoué.")
            self._audit(conn, "PENDING_ORDER_JOB_PREPARED", master=master_code,
                        follower=follower_code, job=job_id, outcome=action,
                        details={"master_order_ticket": str(order_ticket)})
            conn.run("COMMIT")
            return {"status": "PREPARED", "execution_action": action,
                    "job": _row_dicts(conn, result)[0]}
        else:
            native_ticket = str(link[0]) if link and str(link[10]) == "OPEN" else ""
            if action == "PENDING_MODIFY" and not native_ticket:
                raise CopyDecisionError("PENDING_ORDER_LINK_MISSING", "Aucun ordre suiveur lié n’est disponible pour une modification.")
            if action == "PENDING_CREATE" and link and str(link[10]) == "OPEN":
                conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='PENDING_ORDER_DUPLICATE_RISK',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='PENDING_ORDER_DUPLICATE_RISK',updated_at=NOW() WHERE member_code=:follower", follower=follower_code)
                conn.run("COMMIT")
                return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}

        invert = settings.invert_trades
        translated_types = {
            "BUY_LIMIT": "SELL_STOP", "SELL_LIMIT": "BUY_STOP",
            "BUY_STOP": "SELL_LIMIT", "SELL_STOP": "BUY_LIMIT",
            "BUY_STOP_LIMIT": "SELL_STOP_LIMIT", "SELL_STOP_LIMIT": "BUY_STOP_LIMIT",
        }
        follower_type = translated_types.get(str(pending_type).upper(), "") if invert else str(pending_type).upper()
        if not follower_type:
            raise CopyDecisionError("PENDING_ORDER_TYPE_UNSUPPORTED", "Le type MT5 demandé n’est pas pris en charge.")
        target_side = "SELL" if invert and side == "BUY" else "BUY" if invert and side == "SELL" else side
        stop_loss = Decimal(str(source_tp or 0)) if invert else Decimal(str(source_sl or 0))
        take_profit = Decimal(str(source_sl or 0)) if invert else Decimal(str(source_tp or 0))
        if not settings.copy_stop_loss:
            stop_loss = Decimal("0")
        min_volume = _number(follower_evidence.get("volume_min"), "volume_min", positive=True)
        step = _number(follower_evidence.get("volume_step"), "volume_step", positive=True)
        max_volume = _number(follower_evidence.get("volume_max"), "volume_max", positive=True)
        loss_per_lot = market.get("pending_loss_per_lot")
        if settings.mode in {"Risque en %", "Risque par solde (Balance)", "Risque par capitaux (Equity)"}:
            loss_per_lot = _number(loss_per_lot, "pending_loss_per_lot", positive=True) if loss_per_lot else None
        requested = requested_volume(
            settings, source_symbol=master_symbol, source_volume=Decimal(str(source_volume)),
            balance=_number(market.get("balance"), "balance", positive=True),
            equity=_number(market.get("equity"), "equity", positive=True),
            loss_per_lot=loss_per_lot,
        )
        target_volume = normalized_volume(
            requested, minimum=min_volume, step=step, maximum=max_volume,
            configured_max=settings.lots_max, force_minimum=settings.force_minimum,
        )
        before = existing[0] if existing else None
        if action == "PENDING_MODIFY" and before and Decimal(str(before["volume"])) != target_volume:
            raise CopyDecisionError("PENDING_VOLUME_REPLACE_REQUIRED", "Le volume pending a changé; une annulation suivie d’une nouvelle création est requise.")
        plan = {
            "action": action, "master_order_ticket": str(order_ticket),
            "follower_order_ticket": native_ticket, "master_pending_type": str(pending_type).upper(),
            "pending_type": follower_type,
            "side": target_side, "price": str(source_price), "volume": str(target_volume),
            "stop_loss": str(stop_loss), "take_profit": str(take_profit),
            "stop_limit": str(stop_limit), "time_type": int(time_type),
            "expiration_at": _aware_datetime(expiration_at).isoformat() if expiration_at else None,
            "follower_before": ({
                "ticket": str(before.get("order_ticket") or ""), "pending_type": str(before.get("pending_type") or ""),
                "volume": str(before.get("volume") or ""), "price_open": str(before.get("price_open") or ""),
                "stop_loss": str(before.get("stop_loss") or ""), "take_profit": str(before.get("take_profit") or ""),
                    "stop_limit": str(before.get("stop_limit") or ""),
                    "comment": str(before.get("comment") or "")[:31],
            } if before else {}),
        }
        evidence = {"master": {}, "follower": dict(follower_evidence),
                    "mapping_hash": mapping["evidence_hash"],
                    "market": {"login": str(market.get("login") or ""),
                               "server": str(market.get("server") or ""),
                               "terminal_connected": market.get("terminal_connected") is True,
                               "is_demo": market.get("is_demo") is True,
                               "calendar_region": str(calendar_region or "ALL")[:40]},
                    "plan": plan}
        result = conn.run(
            """UPDATE copy_trading_jobs SET status='PREPARED',follower_symbol=:symbol,
               normalized_volume=:volume,native_evidence=:evidence::jsonb,
               execution_action=:action,request_comment=:comment,updated_at=NOW()
               WHERE job_id=:job AND status='LEASED'
               RETURNING job_id::text,native_evidence,normalized_volume,request_comment,execution_action,follower_symbol""",
            symbol=str(symbol)[:64], volume=target_volume, evidence=json.dumps(evidence, separators=(",", ":")),
            action=action, comment=str(request_comment)[:31], job=job_id,
        )
        if not result:
            raise CopyDecisionError("ATOMIC_HANDOFF_REJECTED", "Le prévol pending n’a pas conservé son bail.")
        self._audit(conn, "PENDING_ORDER_JOB_PREPARED", master=master_code,
                    follower=follower_code, job=job_id, outcome=action,
                    details={"master_order_ticket": str(order_ticket), "volume": str(target_volume)})
        conn.run("COMMIT")
        return {"status": "PREPARED", "execution_action": action,
                "job": _row_dicts(conn, result)[0]}

    def mark_prepared(self, job_id, token, *, symbol, follower_evidence, market,
                      request_comment):
        """Validate a native terminal snapshot and persist a bounded execution plan."""
        if not str(symbol or "").strip() or not isinstance(follower_evidence, dict):
            raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Le prévol MT5 doit inclure les preuves natives du symbole suiveur.")
        if not isinstance(market, dict):
            raise CopyDecisionError("NATIVE_ACCOUNT_INFO_MISSING", "Les informations de compte natives sont requises pour le prévol.")
        conn = self.get_conn()
        try:
            rows = conn.run(
                """SELECT j.status,j.expires_at,j.lease_expires_at,j.lease_token_hash,
                          j.follower_member_code,j.academy_settings,j.protection_close,e.master_member_code,
                          e.position_id,e.action,e.symbol,e.side,e.volume,e.price_open,
                          e.stop_loss,e.take_profit,e.pending_type,e.stop_limit,
                          e.expiration_at,e.time_type
                   FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.job_id=:job FOR UPDATE OF j""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            (state, expires, lease_expires, expected_hash, follower_code, raw_settings, protection_close,
             master_code, master_position, action, master_symbol, side, source_volume,
             source_price, source_sl, source_tp, pending_type, pending_stop_limit,
             pending_expiration, pending_time_type) = rows[0]
            now = conn.run("SELECT NOW()")[0][0]
            if state != "LEASED" or not token or lease_token_hash(token) != expected_hash:
                raise CopyDecisionError("LEASE_INVALID", "Le bail de préparation n’est plus valide.")
            if expires <= now or lease_expires is None or lease_expires <= now:
                conn.run("UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("EVENT_EXPIRED", "L’événement maître a dépassé son délai absolu de 120 secondes.")
            protection_rows = conn.run(
                """SELECT suspended,suspension_reason,guard_enabled,
                          (last_sample_at>NOW()-INTERVAL '30 seconds')
                   FROM copy_trading_account_protections WHERE member_code=:member FOR UPDATE""",
                member=follower_code,
            )
            protection_state = protection_rows[0] if protection_rows else (False, "", False, False)
            protection_configured = academy_protection_enabled(_json_object(raw_settings))
            if (protection_configured and not protection_close
                    and (not protection_state[2] or not protection_state[3])):
                raise CopyDecisionError("ACADEMY_PROTECTION_SAMPLE_STALE",
                                        "La copie attend un relevé MT5 récent pour appliquer les protections configurées.")
            if ((bool(protection_state[0]) and not protection_close)
                    or (protection_close and (not bool(protection_state[0])
                         or not str(protection_state[1] or "").startswith("ACADEMY_PROTECTION:")) )):
                conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='ACADEMY_PROTECTION_STATE_CHANGED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                self._audit(conn, "ACADEMY_PROTECTION_JOB_BLOCKED", master=master_code,
                            follower=follower_code, job=job_id, outcome="PROTECTION_STATE_CHANGED")
                conn.run("COMMIT")
                raise CopyDecisionError("ACADEMY_PROTECTION_ACTIVE", "Le compte est suspendu par une protection ou la clôture n’est plus autorisée.")
            master_rows = conn.run(
                """SELECT evidence,evidence_hash FROM copy_trading_native_symbols
                   WHERE master_member_code=:master AND symbol=:symbol""",
                master=master_code, symbol=master_symbol,
            )
            if not master_rows:
                raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "La preuve native maître n’est plus disponible.")
            master_evidence = _json_object(master_rows[0][0])
            mapping = verify_native_symbol_mapping(master_evidence, follower_evidence)
            if mapping["follower_symbol"] != str(symbol):
                raise CopyDecisionError("MAPPING_SYMBOL_MISMATCH", "Le symbole préparé ne correspond pas à la preuve du terminal suiveur.")
            reviewed_by = ""
            reviewed_at = None
            if mapping["state"] == "PENDING_REVIEW":
                approved = conn.run(
                    """SELECT state,evidence_hash,reviewed_by,reviewed_at FROM copy_trading_symbol_mappings
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_symbol=:symbol AND follower_symbol=:follower_symbol""",
                    master=master_code, follower=follower_code, symbol=master_symbol,
                    follower_symbol=str(symbol),
                )
                if approved and approved[0][0] == "VERIFIED" and approved[0][1] == mapping["evidence_hash"] and approved[0][3]:
                    mapping = verify_native_symbol_mapping(master_evidence, follower_evidence, reviewed_alias=True)
                    reviewed_by, reviewed_at = str(approved[0][2] or ""), approved[0][3]
            if mapping["state"] != "VERIFIED":
                conn.run(
                    """INSERT INTO copy_trading_symbol_mappings
                       (master_member_code,follower_member_code,master_symbol,follower_symbol,state,
                        native_evidence,evidence_hash,updated_at)
                       VALUES (:master,:follower,:master_symbol,:follower_symbol,:state,:evidence::jsonb,:hash,NOW())
                       ON CONFLICT (master_member_code,follower_member_code,master_symbol) DO UPDATE SET
                         follower_symbol=EXCLUDED.follower_symbol,state=EXCLUDED.state,
                         native_evidence=EXCLUDED.native_evidence,evidence_hash=EXCLUDED.evidence_hash,updated_at=NOW()""",
                    master=master_code, follower=follower_code, master_symbol=master_symbol,
                    follower_symbol=symbol, state=mapping["state"],
                    evidence=json.dumps(mapping["evidence"], separators=(",", ":")),
                    hash=mapping["evidence_hash"],
                )
                conn.run("COMMIT")
                raise CopyDecisionError("MAPPING_REVIEW_REQUIRED", "La correspondance instrument reste en attente de preuves ou de revue native.")

            # Store even exact-name mappings so operators can audit the evidence
            # that justified allowing an instrument on this follower account.
            conn.run(
                """INSERT INTO copy_trading_symbol_mappings
                   (master_member_code,follower_member_code,master_symbol,follower_symbol,state,
                    native_evidence,evidence_hash,reviewed_by,reviewed_at,updated_at)
                   VALUES (:master,:follower,:master_symbol,:follower_symbol,'VERIFIED',:evidence::jsonb,
                           :hash,:reviewed_by,:reviewed_at,NOW())
                   ON CONFLICT (master_member_code,follower_member_code,master_symbol) DO UPDATE SET
                     follower_symbol=EXCLUDED.follower_symbol,state='VERIFIED',
                     native_evidence=EXCLUDED.native_evidence,evidence_hash=EXCLUDED.evidence_hash,
                     reviewed_by=CASE WHEN EXCLUDED.reviewed_at IS NOT NULL THEN EXCLUDED.reviewed_by ELSE '' END,
                     reviewed_at=EXCLUDED.reviewed_at,updated_at=NOW()""",
                master=master_code, follower=follower_code, master_symbol=master_symbol,
                follower_symbol=str(symbol), evidence=json.dumps(mapping["evidence"], separators=(",", ":")),
                hash=mapping["evidence_hash"], reviewed_by=reviewed_by, reviewed_at=reviewed_at,
            )

            member = self.get_member(follower_code) or {}
            member_params = _json_object(member.get("params"))
            expected_login = str(member_params.get("mt_login") or "").strip()
            expected_server = str(member_params.get("serveur") or member_params.get("mt_server") or "").strip()
            if (not expected_login or str(market.get("login") or "").strip() != expected_login
                    or str(market.get("server") or "").strip().casefold() != expected_server.casefold()
                    or market.get("terminal_connected") is not True):
                raise CopyDecisionError("TERMINAL_IDENTITY_MISMATCH", "Le snapshot natif ne correspond pas au compte suiveur configuré.")
            if market.get("trade_allowed") is not True:
                raise CopyDecisionError("TERMINAL_TRADING_DISABLED", "Le terminal MT5 indique que le trading est désactivé.")

            raw_settings_dict = _json_object(raw_settings)
            unsupported_pending_settings = enabled_unsupported_pending_order_settings(raw_settings_dict)
            if unsupported_pending_settings and not protection_close:
                conn.run(
                    """UPDATE copy_trading_jobs SET status='REJECTED',
                       failure_code='PENDING_ORDER_COPY_NOT_SUPPORTED',updated_at=NOW()
                       WHERE job_id=:job AND status='LEASED'""", job=job_id,
                )
                conn.run(
                    """UPDATE copy_trading_follower_locks SET job_id=NULL,
                       lease_expires_at=NULL,updated_at=NOW()
                       WHERE member_code=:member AND job_id=:job""",
                    member=follower_code, job=job_id,
                )
                self._audit(conn, "UNSUPPORTED_COPY_SETTING_BLOCKED", master=master_code,
                            follower=follower_code, job=job_id,
                            outcome="PENDING_ORDER_COPY_NOT_SUPPORTED",
                            details={"settings": list(unsupported_pending_settings)})
                conn.run("COMMIT")
                raise CopyDecisionError(
                    "PENDING_ORDER_COPY_NOT_SUPPORTED",
                    "La copie des ordres en attente n’est pas implémentée; le job a été bloqué.",
                )
            settings = academy_settings(raw_settings_dict)
            if action.startswith("PENDING_"):
                return self._prepare_pending_job(
                    conn, job_id=job_id, follower_code=follower_code, master_code=master_code,
                    order_ticket=master_position, action=action, master_symbol=master_symbol,
                    side=side, source_volume=source_volume, source_price=source_price,
                    source_sl=source_sl, source_tp=source_tp,
                    pending_type=str(pending_type or ""), stop_limit=pending_stop_limit or 0,
                    expiration_at=pending_expiration, time_type=pending_time_type or 0,
                    settings=settings, raw_settings=raw_settings_dict, market=market,
                    symbol=symbol, follower_evidence=follower_evidence, mapping=mapping,
                    request_comment=request_comment,
                    calendar_region=str(member_params.get("calendar_region") or "ALL"),
                    protection_close=bool(protection_close),
                )
            min_volume = _number(follower_evidence.get("volume_min"), "volume_min", positive=True)
            volume_step = _number(follower_evidence.get("volume_step"), "volume_step", positive=True)
            max_volume = _number(follower_evidence.get("volume_max"), "volume_max", positive=True)
            link_rows = conn.run(
                """SELECT follower_position_id,follower_symbol,follower_initial_volume,
                          master_initial_volume,current_master_volume,current_follower_volume,state
                   FROM copy_trading_position_links WHERE master_member_code=:master
                     AND follower_member_code=:follower AND master_position_id=:position FOR UPDATE""",
                master=master_code, follower=follower_code, position=master_position,
            )
            link = link_rows[0] if link_rows else None
            native_positions = market.get("positions")
            execution_action = "OPEN"
            native_volume = Decimal("0")
            target_side = "SELL" if settings.invert_trades and side == "BUY" else (
                "BUY" if settings.invert_trades and side == "SELL" else side
            )
            source_stop = Decimal(str(source_sl or 0))
            source_take = Decimal(str(source_tp or 0))
            if settings.invert_trades:
                source_stop, source_take = source_take, source_stop
            risk_stop = source_stop
            if not settings.copy_stop_loss:
                source_stop = Decimal("0")

            native_position = None
            if action == "CLOSE":
                if not link or link[6] != "OPEN":
                    conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='NO_LINKED_POSITION',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "COMPLETE", "execution_action": "NOOP"}
                symbol = str(link[1])
                if not isinstance(native_positions, list) or market.get("positions_complete") is not True:
                    raise CopyDecisionError("NATIVE_POSITIONS_INCOMPLETE", "La liste native des positions suiveur n’est pas complète.")
                native_matches = _native_position_matches(native_positions, link[0])
                if not native_matches:
                    conn.run("UPDATE copy_trading_position_links SET state='CLOSED',closed_at=NOW(),updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position AND state='OPEN'", master=master_code, follower=follower_code, position=master_position)
                    conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='NATIVE_POSITION_ALREADY_CLOSED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "COMPLETE", "execution_action": "NOOP"}
                if len(native_matches) != 1:
                    conn.run("UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position", master=master_code, follower=follower_code, position=master_position)
                    conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='NATIVE_POSITION_NOT_UNIQUE',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='NATIVE_POSITION_NOT_UNIQUE',updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
                native_position = native_matches[0]
                native_volume = _number(native_position.get("volume"), "current follower volume", positive=True)
                if (str(native_position.get("symbol") or "") != str(link[1])
                        or str(native_position.get("side") or "").upper() != target_side
                        or native_volume != Decimal(str(link[5]))):
                    conn.run("UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position", master=master_code, follower=follower_code, position=master_position)
                    conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='LINKED_POSITION_STATE_MISMATCH',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='LINKED_POSITION_STATE_MISMATCH',updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
                execution_action = "CLOSE"
            elif link and link[6] != "OPEN":
                conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='LINK_NOT_OPEN',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                conn.run("COMMIT")
                return {"status": "COMPLETE", "execution_action": "NOOP"}
            elif link and link[6] == "OPEN":
                if action == "OPEN":
                    conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='POSITION_ALREADY_LINKED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "COMPLETE", "execution_action": "NOOP"}
                if not isinstance(native_positions, list) or market.get("positions_complete") is not True:
                    raise CopyDecisionError("NATIVE_POSITIONS_INCOMPLETE", "La liste native des positions suiveur n’est pas complète.")
                native_matches = _native_position_matches(native_positions, link[0])
                if len(native_matches) != 1:
                    conn.run("UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position", master=master_code, follower=follower_code, position=master_position)
                    reason = "LINKED_POSITION_MISSING" if not native_matches else "NATIVE_POSITION_NOT_UNIQUE"
                    conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code=:reason,updated_at=NOW() WHERE job_id=:job", reason=reason, job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason=:reason,updated_at=NOW() WHERE member_code=:member AND job_id=:job", reason=reason, member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
                native_position = native_matches[0]
                native_volume = _number(native_position.get("volume"), "current follower volume", positive=True)
                if (str(native_position.get("symbol") or "") != str(link[1])
                        or str(native_position.get("side") or "").upper() != target_side
                        or native_volume != Decimal(str(link[5]))):
                    conn.run("UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position", master=master_code, follower=follower_code, position=master_position)
                    conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='LINKED_POSITION_STATE_MISMATCH',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='LINKED_POSITION_STATE_MISMATCH',updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                    conn.run("COMMIT")
                    return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
                stop_mismatch = (settings.copy_stop_loss and
                    Decimal(str(native_position.get("stop_loss", native_position.get("sl", 0)))) != source_stop)
                take_mismatch = Decimal(str(native_position.get("take_profit", native_position.get("tp", 0))) ) != source_take
                if (Decimal(str(source_volume)) != Decimal(str(link[4])) or stop_mismatch or take_mismatch):
                    if Decimal(str(source_volume)) != Decimal(str(link[4])):
                        # A volume increase may create an additional MT5 ticket.
                        # Until that ticket has a durable component link, stop
                        # instead of risking an untracked scale-in.
                        if Decimal(str(source_volume)) > Decimal(str(link[4])):
                            conn.run("UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position", master=master_code, follower=follower_code, position=master_position)
                            conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='POSITION_SCALE_IN_REVIEW_REQUIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                            conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='POSITION_SCALE_IN_REVIEW_REQUIRED',updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                            conn.run("COMMIT")
                            return {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
                        target = (_number(link[2], "initial follower volume", positive=True)
                                  * Decimal(str(source_volume)) / _number(link[3], "initial master volume", positive=True))
                        difference = native_volume - target
                        if difference < min_volume:
                            execution_action = "NOOP"
                        else:
                            native_volume = normalized_volume(
                                difference, minimum=min_volume, step=volume_step,
                                maximum=max_volume, configured_max=max_volume,
                            )
                            execution_action = "RESIZE_CLOSE"
                    elif stop_mismatch or take_mismatch:
                        execution_action = "PROTECT"
                    else:
                        execution_action = "NOOP"
                else:
                    execution_action = "NOOP"
            elif action == "CLOSE":
                execution_action = "NOOP"
            elif execution_action == "OPEN":
                loss_per_lot = market.get("loss_per_lot")
                if risk_stop > 0:
                    # Stop/take-profit direction is sourced from the master;
                    # a connector may supply its own native one-lot loss.
                    loss_per_lot = market.get("loss_per_lot")
                elif settings.mode in {"Risque en %", "Risque par solde (Balance)", "Risque par capitaux (Equity)"}:
                    loss_per_lot = None
                requested = requested_volume(
                    settings, source_symbol=str(master_symbol), source_volume=Decimal(str(source_volume)),
                    balance=_number(market.get("balance"), "balance", positive=True),
                    equity=_number(market.get("equity"), "equity", positive=True),
                    loss_per_lot=_number(loss_per_lot, "loss_per_lot", positive=True) if loss_per_lot is not None else None,
                )
                native_volume = normalized_volume(
                    requested, minimum=min_volume, step=volume_step, maximum=max_volume,
                    configured_max=settings.lots_max, force_minimum=settings.force_minimum,
                )
            elif execution_action == "NOOP":
                conn.run("UPDATE copy_trading_jobs SET status='COMPLETE',execution_action='NOOP',failure_code='NO_RECONCILIATION_ACTION',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_position_links SET current_master_volume=:volume,updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower AND master_position_id=:position AND state='OPEN'", volume=source_volume, master=master_code, follower=follower_code, position=master_position)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=follower_code, job=job_id)
                conn.run("COMMIT")
                return {"status": "COMPLETE", "execution_action": "NOOP"}
            if execution_action not in {"CLOSE", "RESIZE_CLOSE"} and native_volume > max_volume:
                raise CopyDecisionError("VOLUME_OUT_OF_RANGE", "Le volume prévalidé dépasse la limite native MT5.")
            evidence = {"master": master_evidence, "follower": dict(follower_evidence),
                        "mapping_hash": mapping["evidence_hash"], "market": {
                            "login": str(market.get("login") or ""),
                            "server": str(market.get("server") or ""),
                            "terminal_connected": market.get("terminal_connected") is True,
                            "is_demo": market.get("is_demo") is True,
                            "balance": str(market.get("balance", "")),
                            "equity": str(market.get("equity", "")),
                            "loss_per_lot": str(market.get("loss_per_lot", "")),
                            "calendar_region": str(member_params.get("calendar_region") or "ALL")[:40],
                        }, "plan": {
                            "action": execution_action,
                            "side": target_side,
                            "stop_loss": str(source_stop),
                            "take_profit": str(source_take),
                            "risk_stop_loss": str(risk_stop),
                            "position_ticket": str((native_position or {}).get("ticket", ""))
                                               if execution_action in {"CLOSE", "RESIZE_CLOSE", "PROTECT"} else "",
                            "follower_before_volume": str((native_position or {}).get("volume", ""))
                                                       if execution_action in {"CLOSE", "RESIZE_CLOSE", "PROTECT"} else "",
                            "source_position_id": str(master_position),
                        }}
            result = conn.run(
                """UPDATE copy_trading_jobs SET status='PREPARED',follower_symbol=:symbol,
                   normalized_volume=:volume,native_evidence=:evidence::jsonb,
                   execution_action=:execution_action,request_comment=:comment,updated_at=NOW()
                   WHERE job_id=:job AND status='LEASED'
                   RETURNING job_id::text,native_evidence,normalized_volume,request_comment,execution_action,follower_symbol""",
                job=job_id, symbol=str(symbol)[:64], volume=native_volume,
                evidence=json.dumps(evidence, separators=(",", ":")),
                comment=str(request_comment)[:31], execution_action=execution_action,
            )
            if not result:
                raise CopyDecisionError("ATOMIC_HANDOFF_REJECTED", "Le prévol n’a pas conservé son bail.")
            self._audit(conn, "JOB_PREPARED", master=master_code, follower=follower_code,
                        job=job_id, outcome=execution_action,
                        details={"symbol": str(symbol)[:64], "volume": str(native_volume)})
            conn.run("COMMIT")
            return {"status": "PREPARED", "execution_action": execution_action,
                    "job": _row_dicts(conn, result)[0]}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def begin_dispatch(self, job_id, token):
        """Atomic DB handoff. This transition is the last replay-safe boundary."""
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT j.status,j.expires_at,j.lease_expires_at,j.lease_token_hash,
                          j.follower_member_code,c.revoked_at,c.consent_version,m.copy_actif,
                          m.actif,m.date_fin,m.access_level,m.billing_status,
                          m.billing_current_period_end,m.admin_suspended,
                          j.execution_action,j.native_evidence,j.protection_close
                   FROM copy_trading_jobs j
                   JOIN members m ON m.code=j.follower_member_code
                   LEFT JOIN copy_trading_consents c ON c.member_code=m.code
                   WHERE j.job_id=:job FOR UPDATE OF j""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            row = rows[0]
            (state, expires, lease_expires, expected_hash, member_code, revoked_at,
             consent_version, copy_active, active, date_end, access_level,
             billing_status, billing_end, suspended, execution_action, raw_evidence, protection_close) = row
            now = conn.run("SELECT NOW()")[0][0]
            if state != "PREPARED" or lease_token_hash(token or "") != expected_hash:
                raise CopyDecisionError("ATOMIC_HANDOFF_REJECTED", "La tâche n’est pas préparée ou son bail ne correspond plus.")
            if expires <= now or lease_expires is None or lease_expires <= now:
                conn.run("UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("EVENT_EXPIRED", "L’événement maître a dépassé son délai absolu de 120 secondes.")
            member = {"code": member_code, "actif": active, "date_fin": date_end,
                      "access_level": access_level, "billing_status": billing_status,
                      "billing_current_period_end": billing_end, "admin_suspended": suspended}
            if revoked_at is not None or consent_version != self.consent_version or (not copy_active and not protection_close) or not self.can_copy_member(member):
                conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='FOLLOWER_NOT_AUTHORIZED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("FOLLOWER_NOT_AUTHORIZED", "Le suiveur n’est plus autorisé à recevoir cet événement.")
            evidence_payload = _json_object(raw_evidence)
            native_market = _json_object(evidence_payload.get("market"))
            mode = (os.environ.get("COPY_TRADING_MODE") or "simulation").strip().lower()
            enabled = (os.environ.get("COPY_TRADING_EXECUTION_ENABLED", "false").strip().lower()
                       in {"1", "true", "yes", "on"})
            live_approved = (os.environ.get("COPY_TRADING_LIVE_RELEASE_APPROVED", "false").strip().lower()
                             in {"1", "true", "yes", "on"})
            if not execution_release_allowed(
                mode=mode, execution_enabled=enabled,
                live_release_approved=live_approved,
                account_is_demo=native_market.get("is_demo") is True,
            ):
                conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='OPERATIONAL_RELEASE_REQUIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=member_code, job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("OPERATIONAL_RELEASE_REQUIRED", "Le mode d’exécution n’a pas de libération opérationnelle valide pour ce compte.")
            protection_rows = conn.run(
                """SELECT suspended,suspension_reason,guard_enabled,
                          (last_sample_at>NOW()-INTERVAL '30 seconds')
                   FROM copy_trading_account_protections WHERE member_code=:member FOR UPDATE""",
                member=member_code,
            )
            protection_state = protection_rows[0] if protection_rows else (False, "", False, False)
            if ((bool(protection_state[0]) and not protection_close)
                    or (protection_close and (not bool(protection_state[0])
                         or not str(protection_state[1] or "").startswith("ACADEMY_PROTECTION:")) )):
                conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='ACADEMY_PROTECTION_STATE_CHANGED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE member_code=:member AND job_id=:job", member=member_code, job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("ACADEMY_PROTECTION_ACTIVE", "L’état de protection a changé avant l’envoi MT5.")
            settings_rows = conn.run(
                "SELECT academy_settings FROM copy_trading_jobs WHERE job_id=:job", job=job_id,
            )
            raw_settings = _json_object(settings_rows[0][0]) if settings_rows else {}
            settings = academy_settings(raw_settings)
            if (academy_protection_enabled(_json_object(settings_rows[0][0]) if settings_rows else {})
                    and not protection_close and (not protection_state[2] or not protection_state[3])):
                raise CopyDecisionError("ACADEMY_PROTECTION_SAMPLE_STALE",
                                        "Le relevé de protection n’est plus assez récent pour autoriser le handoff.")
            safety_actions = {"CLOSE", "RESIZE_CLOSE", "PENDING_CANCEL", "PENDING_EXPIRE"}
            if settings.copy_news_filter and execution_action not in safety_actions and not protection_close:
                calendar_region = str(native_market.get("calendar_region") or "ALL")[:40]
                calendar_allowed, calendar_reason = _calendar_decision(
                    conn, settings, region=calendar_region, preferences=raw_settings,
                )
                if not calendar_allowed:
                    conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code=:reason,updated_at=NOW() WHERE job_id=:job", job=job_id, reason=calendar_reason)
                    self._audit(conn, "ACADEMY_CALENDAR_DISPATCH_BLOCKED", follower=member_code,
                                job=job_id, outcome=calendar_reason,
                                details={"region": calendar_region,
                                         "currencies": raw_settings.get("calendar_currencies", ""),
                                         "min_importance": raw_settings.get("calendar_min_importance", 2)})
                    conn.run("COMMIT")
                    raise CopyDecisionError(calendar_reason, "Le filtre calendrier Académie bloque le handoff selon son flux ou sa fenêtre configurée.")
            result = conn.run(
                """UPDATE copy_trading_jobs SET status='DISPATCHING',dispatch_started_at=NOW(),
                   updated_at=NOW() WHERE job_id=:job AND status='PREPARED'
                   AND expires_at>NOW() AND lease_expires_at>NOW()
                   RETURNING job_id::text,event_id::text,follower_member_code,follower_symbol,
                             normalized_volume,request_comment,native_evidence,execution_action""",
                job=job_id,
            )
            if not result:
                raise CopyDecisionError("ATOMIC_HANDOFF_REJECTED", "Le handoff atomique n’a pas pu être acquis.")
            output = _row_dicts(conn, result)[0]
            self._audit(conn, "BROKER_HANDOFF_STARTED", follower=member_code,
                        job=job_id, outcome=execution_action,
                        details={"symbol": str(output.get("follower_symbol") or "")[:64]})
            conn.run("COMMIT")
            return output
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def fail_before_dispatch(self, job_id, token, code, *, retryable: bool):
        next_status = "RETRY" if retryable else "REJECTED"
        return self._update_leased(
            job_id, token,
            """UPDATE copy_trading_jobs SET status=:status,failure_code=:code,
               lease_token_hash='',lease_expires_at=NULL,updated_at=NOW()
               WHERE job_id=:job AND status IN ('LEASED','PREPARED') RETURNING job_id::text""",
            status=next_status, code=str(code)[:100],
        )

    def renew_job_lease(self, job_id, token):
        """Renew only a still-valid lease, capped by the immutable source expiry."""
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """UPDATE copy_trading_jobs SET
                     lease_expires_at=LEAST(NOW()+INTERVAL '30 seconds',expires_at),updated_at=NOW()
                   WHERE job_id=:job AND status IN ('LEASED','PREPARED','DISPATCHING')
                     AND expires_at>NOW() AND lease_expires_at>NOW()
                     AND lease_token_hash=:token_hash
                   RETURNING follower_member_code,expires_at,lease_expires_at""",
                job=job_id, token_hash=lease_token_hash(token or ""),
            )
            if not rows:
                conn.run("ROLLBACK")
                return None
            member, expires_at, lease_until = rows[0]
            conn.run(
                """UPDATE copy_trading_follower_locks SET lease_expires_at=LEAST(:lease,:expires),updated_at=NOW()
                   WHERE member_code=:member AND job_id=:job AND blocked=FALSE""",
                lease=lease_until, expires=expires_at, member=member, job=job_id,
            )
            conn.run("COMMIT")
            return {"expires_at": expires_at.isoformat(), "lease_expires_at": lease_until.isoformat()}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def complete_dispatch(self, job_id, *, result):
        """Persist broker outcome; unknown and partial results are quarantined."""
        if not isinstance(result, dict):
            result = {"confirmed": False, "ambiguous": True, "failure_code": "INVALID_BROKER_RESPONSE"}
        conn = self.get_conn()
        try:
            conn.run("BEGIN")
            rows = conn.run(
                """SELECT j.status,e.master_member_code,e.position_id,e.action,e.symbol,e.volume,
                          j.follower_member_code,j.follower_symbol,j.normalized_volume,j.execution_action,
                          j.native_evidence,j.request_comment,e.event_id::text,e.idempotency_key,
                          j.result_payload
                   FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.job_id=:job FOR UPDATE OF j""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            (state, master, master_position, action, master_symbol, master_volume,
             follower, follower_symbol, follower_volume, execution_action,
             raw_native_evidence, request_comment, event_id, idempotency_key,
             raw_existing_result) = rows[0]
            if state != "DISPATCHING":
                safe_retry = {key: value for key, value in result.items()
                              if not any(secret_word in str(key).lower()
                                         for secret_word in ("password", "credential", "token"))}
                if state in {"COMPLETE", "REJECTED", "VERIFY_REQUIRED"} and \
                        canonical_hash(_json_object(raw_existing_result)) == canonical_hash(safe_retry):
                    conn.run("COMMIT")
                    return {"status": state, "job_id": str(job_id), "already_recorded": True}
                raise CopyDecisionError("DISPATCH_NOT_STARTED", "Aucun handoff broker n’est en cours pour cette tâche.")
            confirmed = result.get("confirmed") is True and not result.get("ambiguous")
            # A broker retcode alone is not proof that the requested state is
            # present in MT5. Mutating actions only complete after the worker
            # has read back and matched the native postcondition.
            verified = self._verify_native_postcondition(
                execution_action=execution_action,
                evidence=_json_object(raw_native_evidence),
                request_comment=str(request_comment or ""), result=result,
            )
            status = "COMPLETE" if confirmed and verified else (
                "VERIFY_REQUIRED" if result.get("ambiguous") or result.get("confirmed") is not True else "REJECTED"
            )
            if confirmed and not verified:
                status = "VERIFY_REQUIRED"
            order_ticket = str(result.get("order_ticket") or "")[:80]
            deal_ticket = str(result.get("deal_ticket") or "")[:80]
            failure_code = str(result.get("failure_code") or (
                "" if status == "COMPLETE" else
                "NATIVE_POSTCONDITION_UNVERIFIED" if status == "VERIFY_REQUIRED" and confirmed else
                "BROKER_RESULT_UNCONFIRMED"
            ))[:100]
            safe_result = {key: value for key, value in result.items()
                           if not any(secret_word in str(key).lower() for secret_word in ("password", "credential", "token"))}
            conn.run(
                """UPDATE copy_trading_jobs SET status=:status,broker_order_ticket=:order_ticket,
                   broker_deal_ticket=:deal_ticket,broker_retcode=:retcode,
                   failure_code=:failure,result_payload=:result::jsonb,updated_at=NOW()
                   WHERE job_id=:job""",
                status=status, order_ticket=order_ticket, deal_ticket=deal_ticket,
                retcode=result.get("retcode"), failure=failure_code,
                result=json.dumps(safe_result, default=str),
                job=job_id,
            )

            if status == "VERIFY_REQUIRED":
                if str(execution_action).startswith("PENDING_"):
                    conn.run(
                        """UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                           WHERE master_member_code=:master AND follower_member_code=:follower
                             AND master_order_ticket=:ticket AND state='OPEN'""",
                        master=master, follower=follower, ticket=master_position,
                    )
                else:
                    conn.run(
                        """UPDATE copy_trading_position_links SET state='VERIFY_REQUIRED',updated_at=NOW()
                           WHERE master_member_code=:master AND follower_member_code=:follower
                             AND master_position_id=:position AND state='OPEN'""",
                        master=master, follower=follower, position=master_position,
                    )
                conn.run(
                    """UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason=:reason,
                       updated_at=NOW() WHERE member_code=:follower""",
                    reason=failure_code or "BROKER_RESULT_UNKNOWN", follower=follower,
                )
            elif status == "COMPLETE" and execution_action.startswith("PENDING_"):
                plan = _json_object(_json_object(raw_native_evidence).get("plan"))
                if execution_action == "PENDING_CREATE":
                    follower_order_ticket = str(result.get("order_ticket") or "")
                    if not follower_order_ticket.isdigit():
                        status = "VERIFY_REQUIRED"
                        conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='PENDING_ORDER_TICKET_MISSING',updated_at=NOW() WHERE job_id=:job", job=job_id)
                        conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='PENDING_ORDER_TICKET_MISSING',updated_at=NOW() WHERE member_code=:follower", follower=follower)
                    else:
                        conn.run(
                            """INSERT INTO copy_trading_pending_order_links
                               (master_member_code,follower_member_code,master_order_ticket,follower_order_ticket,
                                master_symbol,follower_symbol,master_type,follower_type,current_volume,current_price,
                                current_stop_loss,current_take_profit,current_stop_limit,expiration_at,time_type,state)
                               VALUES (:master,:follower,:master_ticket,:follower_ticket,:master_symbol,:follower_symbol,
                                       :master_type,:follower_type,:volume,:price,:sl,:tp,:stop_limit,:expiration,:time_type,'OPEN')
                               ON CONFLICT(master_member_code,follower_member_code,master_order_ticket) DO UPDATE SET
                                 follower_order_ticket=EXCLUDED.follower_order_ticket,
                                 follower_symbol=EXCLUDED.follower_symbol,master_type=EXCLUDED.master_type,
                                 follower_type=EXCLUDED.follower_type,current_volume=EXCLUDED.current_volume,
                                 current_price=EXCLUDED.current_price,current_stop_loss=EXCLUDED.current_stop_loss,
                                 current_take_profit=EXCLUDED.current_take_profit,current_stop_limit=EXCLUDED.current_stop_limit,
                                 expiration_at=EXCLUDED.expiration_at,time_type=EXCLUDED.time_type,
                                 state='OPEN',updated_at=NOW()""",
                            master=master, follower=follower, master_ticket=master_position,
                            follower_ticket=follower_order_ticket, master_symbol=master_symbol,
                            follower_symbol=follower_symbol, master_type=plan.get("master_pending_type", ""),
                            follower_type=plan.get("pending_type", ""),
                            volume=plan.get("volume", follower_volume), price=plan.get("price", 0),
                            sl=plan.get("stop_loss", 0), tp=plan.get("take_profit", 0),
                            stop_limit=plan.get("stop_limit", 0), expiration=plan.get("expiration_at"),
                            time_type=plan.get("time_type", 0),
                        )
                elif execution_action == "PENDING_MODIFY":
                    conn.run(
                        """UPDATE copy_trading_pending_order_links SET master_type=:master_type,
                           follower_type=:follower_type,current_volume=:volume,current_price=:price,
                           current_stop_loss=:sl,current_take_profit=:tp,current_stop_limit=:stop_limit,
                           expiration_at=:expiration,time_type=:time_type,updated_at=NOW()
                           WHERE master_member_code=:master AND follower_member_code=:follower
                             AND master_order_ticket=:ticket AND state='OPEN'""",
                        master_type=plan.get("master_pending_type", ""),
                        follower_type=plan.get("pending_type", ""), volume=plan.get("volume", follower_volume),
                        price=plan.get("price", 0), sl=plan.get("stop_loss", 0),
                        tp=plan.get("take_profit", 0), stop_limit=plan.get("stop_limit", 0),
                        expiration=plan.get("expiration_at"), time_type=plan.get("time_type", 0),
                        master=master, follower=follower, ticket=master_position,
                    )
                else:
                    conn.run(
                        """UPDATE copy_trading_pending_order_links SET state='CLOSED',updated_at=NOW()
                           WHERE master_member_code=:master AND follower_member_code=:follower
                             AND master_order_ticket=:ticket AND state='OPEN'""",
                        master=master, follower=follower, ticket=master_position,
                    )
            elif status == "COMPLETE" and execution_action == "OPEN":
                follower_position = str(result.get("position_id") or "")
                try:
                    actual_volume = _number(result.get("volume"), "confirmed follower volume", positive=True)
                except CopyDecisionError:
                    status = "VERIFY_REQUIRED"
                    conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='BROKER_POSITION_EVIDENCE_MISSING',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='BROKER_POSITION_EVIDENCE_MISSING',updated_at=NOW() WHERE member_code=:follower", follower=follower)
                else:
                    if not follower_position or actual_volume != Decimal(str(follower_volume)):
                        status = "VERIFY_REQUIRED"
                        conn.run("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',failure_code='BROKER_POSITION_EVIDENCE_MISMATCH',updated_at=NOW() WHERE job_id=:job", job=job_id)
                        conn.run("UPDATE copy_trading_follower_locks SET blocked=TRUE,blocked_reason='BROKER_POSITION_EVIDENCE_MISMATCH',updated_at=NOW() WHERE member_code=:follower", follower=follower)
                    else:
                        conn.run(
                    """INSERT INTO copy_trading_position_links
                       (master_member_code,follower_member_code,master_position_id,follower_position_id,
                        master_symbol,follower_symbol,master_initial_volume,follower_initial_volume,
                        current_master_volume,current_follower_volume)
                       VALUES (:master,:follower,:master_position,:follower_position,:master_symbol,:follower_symbol,
                               :master_volume,:follower_volume,:master_volume,:follower_volume)
                       ON CONFLICT (master_member_code,follower_member_code,master_position_id) DO UPDATE SET
                         follower_position_id=EXCLUDED.follower_position_id,
                         follower_symbol=EXCLUDED.follower_symbol,state='OPEN',closed_at=NULL,updated_at=NOW()""",
                    master=master, follower=follower, master_position=master_position,
                    follower_position=follower_position, master_symbol=master_symbol,
                    follower_symbol=follower_symbol, master_volume=master_volume,
                    follower_volume=actual_volume,
                )
            elif status == "COMPLETE" and execution_action == "CLOSE":
                conn.run(
                    """UPDATE copy_trading_position_links SET state='CLOSED',current_master_volume=0,
                       current_follower_volume=0,closed_at=NOW(),updated_at=NOW()
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_position_id=:position AND state='OPEN'""",
                    master=master, follower=follower, position=master_position,
                )
            elif status == "COMPLETE" and execution_action == "RESIZE_CLOSE":
                closed_volume = _number(result.get("volume"), "confirmed closed volume", positive=True)
                conn.run(
                    """UPDATE copy_trading_position_links SET current_master_volume=:master_volume,
                       current_follower_volume=GREATEST(0,current_follower_volume-:closed_volume),
                       updated_at=NOW() WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_position_id=:position AND state='OPEN'""",
                    master_volume=master_volume, closed_volume=closed_volume, master=master,
                    follower=follower, position=master_position,
                )
            elif status == "COMPLETE" and execution_action == "PROTECT":
                conn.run(
                    """UPDATE copy_trading_position_links SET current_master_volume=:master_volume,updated_at=NOW()
                       WHERE master_member_code=:master AND follower_member_code=:follower
                         AND master_position_id=:position AND state='OPEN'""",
                    master_volume=master_volume, master=master, follower=follower, position=master_position,
                )

            if status in {"COMPLETE", "REJECTED"}:
                conn.run(
                    """UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,
                       updated_at=NOW() WHERE member_code=:follower AND job_id=:job""",
                    follower=follower, job=job_id,
                )
            conn.run("COMMIT")
            return {"status": status, "job_id": str(job_id)}
        except Exception:
            try:
                conn.run("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def recover_dispatching_jobs(self):
        """Quarantine interrupted external side effects; do not resend blindly."""
        conn = self.get_conn()
        try:
            conn.run(
                """UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',
                   lease_token_hash='',lease_expires_at=NULL,updated_at=NOW()
                   WHERE status IN ('PENDING','RETRY','LEASED','PREPARED') AND expires_at<=NOW()"""
            )
            conn.run(
                """UPDATE copy_trading_events SET status='EXPIRED'
                   WHERE status='READY' AND expires_at<=NOW()"""
            )
            conn.run(
                """UPDATE copy_trading_follower_locks l SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW()
                   FROM copy_trading_jobs j WHERE l.job_id=j.job_id AND j.status='EXPIRED'
                     AND l.blocked=FALSE"""
            )
            rows = conn.run(
                """WITH quarantined AS (
                       UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED',
                         failure_code='WORKER_RESTART_DURING_DISPATCH',updated_at=NOW()
                       WHERE status='DISPATCHING' AND dispatch_started_at<NOW()-INTERVAL '30 seconds'
                       RETURNING job_id,follower_member_code
                   )
                   UPDATE copy_trading_follower_locks l SET blocked=TRUE,
                     blocked_reason='WORKER_RESTART_DURING_DISPATCH',updated_at=NOW()
                   FROM quarantined q WHERE l.member_code=q.follower_member_code
                   RETURNING q.job_id::text"""
            )
            return len(rows or [])
        finally:
            conn.close()

    def calendar_allows(self, settings: AcademyCopySettings, *, region: str = "",
                        preferences=None) -> tuple[bool, str]:
        """Calendar state blocks only when the Academy news filter is enabled."""
        if not settings.copy_news_filter:
            return True, "FILTER_DISABLED"
        if (os.environ.get("COPY_TRADING_CALENDAR_PROVIDER") or "").strip().lower() != CALENDAR_SOURCE:
            if _calendar_failure_policy() == "ALLOW":
                return True, "CALENDAR_UNAVAILABLE_FAIL_OPEN"
            return False, "CALENDAR_PROVIDER_NOT_CONFIGURED"
        conn = self.get_conn()
        try:
            return _calendar_decision(conn, settings, region=region, preferences=preferences)
        finally:
            conn.close()

    def calendar_active_window(self, settings: AcademyCopySettings, *, region: str = "",
                               preferences=None):
        """Return the currently matching event with UTC and configured local times."""
        if not settings.copy_news_filter:
            return None
        allowed, reason = self.calendar_allows(settings, region=region, preferences=preferences)
        if allowed or reason != "NEWS_WINDOW_ACTIVE":
            return None
        profile = dict(preferences or {})
        currencies_value = profile.get("calendar_currencies") or ""
        if isinstance(currencies_value, (list, tuple, set)):
            currencies = sorted({str(value).strip().upper() for value in currencies_value if str(value).strip()})
        else:
            currencies = sorted({value.strip().upper() for value in str(currencies_value).split(",") if value.strip()})
        timezone_name = str(profile.get("calendar_timezone") or "UTC")
        try:
            display_timezone = ZoneInfo(timezone_name)
            minimum = int(profile.get("calendar_min_importance", 2))
            before = int(profile.get("calendar_minutes_before", 30))
            after = int(profile.get("calendar_minutes_after", 30))
        except Exception:
            return None
        if minimum not in (1, 2, 3) or not 0 <= before <= 360 or not 0 <= after <= 360:
            return None
        conn = self.get_conn()
        try:
            rows = conn.run(
                """SELECT event_title,currency,importance,starts_at,ends_at,estimated_time
                   FROM copy_trading_calendar_events
                   WHERE source=:source AND fetched_at>NOW()-INTERVAL '36 hours'
                     AND (:region='ALL' OR region IN (:region,'ALL','GLOBAL'))
                     AND importance>=:importance
                     AND (:currencies='' OR currency=ANY(string_to_array(:currencies,',')))
                     AND ((estimated_time AND starts_at<=NOW()+make_interval(mins=>:before)
                                           AND ends_at>NOW()-make_interval(mins=>:after))
                          OR (NOT estimated_time AND starts_at>=NOW()-make_interval(mins=>:after)
                                                 AND starts_at<=NOW()+make_interval(mins=>:before)))
                   ORDER BY starts_at LIMIT 1""",
                source=CALENDAR_SOURCE, region=str(profile.get("calendar_region") or region or "ALL"),
                importance=minimum, currencies=",".join(currencies), before=before, after=after,
            )
        finally:
            conn.close()
        if not rows:
            return None
        title, currency, importance, starts_at, ends_at, estimated = rows[0]
        starts_at = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc)
        ends_at = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
        return {
            "title": str(title or ""), "currency": str(currency or ""),
            "importance": int(importance), "estimated_time": bool(estimated),
            "starts_at_utc": starts_at.astimezone(timezone.utc).isoformat(),
            "starts_at_local": starts_at.astimezone(display_timezone).isoformat(),
            "ends_at_local": ends_at.astimezone(display_timezone).isoformat(),
            "timezone": timezone_name,
        }
