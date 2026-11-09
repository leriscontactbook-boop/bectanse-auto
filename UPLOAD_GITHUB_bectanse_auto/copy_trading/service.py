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

from .domain import (
    AcademyCopySettings,
    CopyDecisionError,
    academy_settings,
    canonical_hash,
    ensure_event_fresh,
    enabled_unsupported_academy_guards,
    execution_release_allowed,
    event_idempotency_key,
    normalized_volume,
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
)


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
    def _verify_native_postcondition(*, execution_action, evidence, request_comment, result):
        """Re-evaluate the worker's signed terminal read-back before completion."""
        native = result.get("native_postcondition")
        try:
            if (not isinstance(native, dict) or native.get("terminal_connected") is not True
                    or native.get("terminal_path_verified") is not True):
                return False
            if native.get("positions_complete") is not True:
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

    def _event(self, conn, *, master_code, master_login, master_server, position,
               action, observed_at, source_version):
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
        payload_hash = canonical_hash(payload)
        rows = conn.run(
            """INSERT INTO copy_trading_events
               (idempotency_key,master_member_code,position_id,action,symbol,side,volume,
                price_open,stop_loss,take_profit,source_version,source_time,expires_at,payload_hash)
               VALUES (:key,:master,:position,:action,:symbol,:side,:volume,:price,:sl,:tp,
                       :version,:source_time,:expires,:payload_hash)
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING event_id::text""",
            key=key, master=master_code, position=position_id, action=action,
            symbol=position["symbol"], side=position["side"], volume=position["volume"],
            price=position["price_open"], sl=position["stop_loss"], tp=position["take_profit"],
            version=str(source_version), source_time=observed, expires=expires_at,
            payload_hash=payload_hash,
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

    def ingest_master_snapshot(self, master_code: str, account: dict, positions: list[dict],
                               native_symbols: dict, observed_at, close_deals=None) -> dict:
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
                          j.academy_settings_hash,m.code,
                          CASE WHEN sm.state='VERIFIED' THEN sm.follower_symbol ELSE e.symbol END AS mapped_symbol,
                          COALESCE(sm.state,'') AS mapping_state,
                          ns.evidence AS master_evidence
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
                   WHERE e.status='READY' AND e.expires_at>NOW() AND j.expires_at>NOW()
                     AND ((j.status IN ('PENDING','RETRY')) OR
                          (j.status IN ('LEASED','PREPARED') AND j.lease_expires_at<=NOW()))
                     AND c.revoked_at IS NULL AND c.consented_at IS NOT NULL
                     AND c.consent_version=:consent_version
                     AND m.actif=TRUE AND COALESCE(m.copy_actif,FALSE)=TRUE
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
                     AND NOT COALESCE(l.blocked,FALSE)
                     AND (l.member_code IS NULL OR l.lease_expires_at<=NOW() OR l.job_id=j.job_id)
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
                   WHERE copy_trading_follower_locks.blocked=FALSE
                     AND (copy_trading_follower_locks.lease_expires_at<=NOW()
                          OR copy_trading_follower_locks.job_id=EXCLUDED.job_id)
                   RETURNING member_code""",
                member=job["code"], job=job["job_id"], expires=job["expires_at"],
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
                    "source_time": _aware_datetime(job["source_time"]).isoformat(),
                    "expires_at": _aware_datetime(job["expires_at"]).isoformat(),
                    "academy_settings": _json_object(job.get("academy_settings")),
                    "mapped_symbol": str(job.get("mapped_symbol") or job.get("symbol") or ""),
                    "master_evidence": _json_object(job.get("master_evidence"))})
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
                          j.follower_member_code,j.academy_settings,e.master_member_code,
                          e.position_id,e.action,e.symbol,e.side,e.volume,e.price_open,
                          e.stop_loss,e.take_profit
                   FROM copy_trading_jobs j JOIN copy_trading_events e ON e.event_id=j.event_id
                   WHERE j.job_id=:job FOR UPDATE OF j""", job=job_id,
            )
            if not rows:
                raise CopyDecisionError("JOB_NOT_FOUND", "Tâche de copie introuvable.")
            (state, expires, lease_expires, expected_hash, follower_code, raw_settings,
             master_code, master_position, action, master_symbol, side, source_volume,
             source_price, source_sl, source_tp) = rows[0]
            now = conn.run("SELECT NOW()")[0][0]
            if state != "LEASED" or not token or lease_token_hash(token) != expected_hash:
                raise CopyDecisionError("LEASE_INVALID", "Le bail de préparation n’est plus valide.")
            if expires <= now or lease_expires is None or lease_expires <= now:
                conn.run("UPDATE copy_trading_jobs SET status='EXPIRED',failure_code='ABSOLUTE_TTL_EXPIRED',updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("UPDATE copy_trading_follower_locks SET job_id=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE job_id=:job", job=job_id)
                conn.run("COMMIT")
                raise CopyDecisionError("EVENT_EXPIRED", "L’événement maître a dépassé son délai absolu de 120 secondes.")
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
            unsupported_guards = enabled_unsupported_academy_guards(raw_settings_dict)
            if unsupported_guards:
                conn.run(
                    """UPDATE copy_trading_jobs SET status='REJECTED',
                       failure_code='ACADEMY_GUARD_NOT_SUPPORTED',updated_at=NOW()
                       WHERE job_id=:job AND status='LEASED'""", job=job_id,
                )
                conn.run(
                    """UPDATE copy_trading_follower_locks SET job_id=NULL,
                       lease_expires_at=NULL,updated_at=NOW()
                       WHERE member_code=:member AND job_id=:job""",
                    member=follower_code, job=job_id,
                )
                self._audit(conn, "ACADEMY_GUARD_BLOCKED", master=master_code,
                            follower=follower_code, job=job_id,
                            outcome="ACADEMY_GUARD_NOT_SUPPORTED",
                            details={"enabled_guards": list(unsupported_guards)})
                conn.run("COMMIT")
                raise CopyDecisionError(
                    "ACADEMY_GUARD_NOT_SUPPORTED",
                    "Un seuil drawdown/objectif Académie est activé mais n’est pas encore appliqué par le moteur de copie.",
                )
            settings = academy_settings(raw_settings_dict)
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
                   WHERE job_id=:job AND status='LEASED' RETURNING job_id::text""",
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
                          j.execution_action,j.native_evidence
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
             billing_status, billing_end, suspended, execution_action, raw_evidence) = row
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
            if revoked_at is not None or consent_version != self.consent_version or not copy_active or not self.can_copy_member(member):
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
            settings_rows = conn.run(
                "SELECT academy_settings FROM copy_trading_jobs WHERE job_id=:job", job=job_id,
            )
            settings = academy_settings(_json_object(settings_rows[0][0]) if settings_rows else {})
            if settings.copy_news_filter:
                calendar_region = str(native_market.get("calendar_region") or "ALL")[:40]
                calendar = conn.run(
                    """SELECT 1 FROM copy_trading_calendar_events
                       WHERE fetched_at>NOW()-INTERVAL '36 hours'
                         AND region IN (:region,'ALL','GLOBAL')
                         AND starts_at<=NOW() AND ends_at>NOW() LIMIT 1""",
                    region=calendar_region,
                )
                fresh_calendar = conn.run(
                    """SELECT 1 FROM copy_trading_calendar_events
                       WHERE fetched_at>NOW()-INTERVAL '36 hours'
                         AND region IN (:region,'ALL','GLOBAL') LIMIT 1""",
                    region=calendar_region,
                )
                if calendar:
                    conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='NEWS_WINDOW_ACTIVE',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("COMMIT")
                    raise CopyDecisionError("NEWS_WINDOW_ACTIVE", "Le filtre Académie bloque cet événement pendant une fenêtre d’actualité.")
                if not fresh_calendar:
                    conn.run("UPDATE copy_trading_jobs SET status='REJECTED',failure_code='CALENDAR_STALE_OR_UNAVAILABLE',updated_at=NOW() WHERE job_id=:job", job=job_id)
                    conn.run("COMMIT")
                    raise CopyDecisionError("CALENDAR_STALE_OR_UNAVAILABLE", "Le filtre Académie est activé mais son calendrier durable n’est pas à jour.")
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

    def calendar_allows(self, settings: AcademyCopySettings, *, region: str = "") -> tuple[bool, str]:
        """Calendar state blocks only when the Academy news filter is enabled."""
        if not settings.copy_news_filter:
            return True, "FILTER_DISABLED"
        conn = self.get_conn()
        try:
            rows = conn.run(
                """SELECT starts_at,ends_at FROM copy_trading_calendar_events
                   WHERE fetched_at>NOW()-INTERVAL '36 hours'
                     AND (region=:region OR region='ALL')
                     AND starts_at<=NOW() AND ends_at>NOW()
                   ORDER BY ends_at LIMIT 1""",
                region=str(region or "ALL"),
            )
            if rows:
                return False, "NEWS_WINDOW_ACTIVE"
            fresh = conn.run(
                """SELECT 1 FROM copy_trading_calendar_events
                   WHERE fetched_at>NOW()-INTERVAL '36 hours'
                   AND (region=:region OR region='ALL') LIMIT 1""",
                region=str(region or "ALL"),
            )
            if not fresh:
                return False, "CALENDAR_STALE_OR_UNAVAILABLE"
            return True, "NO_BLOCKING_WINDOW"
        finally:
            conn.close()
