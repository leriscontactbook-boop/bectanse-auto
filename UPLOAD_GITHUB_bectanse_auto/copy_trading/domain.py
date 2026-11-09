"""Pure safety and sizing rules for Bectanse Copy Trading.

No database, Flask, or MetaTrader import is needed to use these functions.
They fail closed when the source event, native symbol evidence, or Academy
settings cannot support an unambiguous execution decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Mapping


EVENT_TTL_SECONDS = 120
SUPPORTED_ACTIONS = {"OPEN", "RECONCILE", "CLOSE"}
SUPPORTED_SIDES = {"BUY", "SELL"}
SUPPORTED_RISK_MODES = {
    "Lots fixes",
    "Copier les lots de l'envoyeur",
    "Risque en %",
    "Risque par solde (Balance)",
    "Risque par capitaux (Equity)",
    "Lot par symbole",
}


class CopyDecisionError(ValueError):
    """A safe, stable reason that a copy action must not proceed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decimal(value: Any, field: str, *, minimum: Decimal | None = None) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CopyDecisionError("INVALID_SETTINGS", f"{field} doit être numérique.")
    if not result.is_finite() or (minimum is not None and result < minimum):
        raise CopyDecisionError("INVALID_SETTINGS", f"{field} est hors limites.")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CopyDecisionError("INVALID_EVENT_TIME", f"{field} doit inclure un fuseau horaire.")
    return value.astimezone(timezone.utc)


def event_expiry(source_time: datetime) -> datetime:
    """Absolute expiry anchored to the MT5 source timestamp; never lease-extended."""
    return _utc(source_time, "source_time") + timedelta(seconds=EVENT_TTL_SECONDS)


def ensure_event_fresh(source_time: datetime, now: datetime) -> tuple[datetime, int]:
    source = _utc(source_time, "source_time")
    current = _utc(now, "now")
    expires = source + timedelta(seconds=EVENT_TTL_SECONDS)
    if current >= expires:
        raise CopyDecisionError("EVENT_EXPIRED", "L’événement maître a dépassé son délai absolu de 120 secondes.")
    if source > current + timedelta(seconds=5):
        raise CopyDecisionError("SOURCE_CLOCK_AHEAD", "L’horloge source est trop en avance pour valider l’événement.")
    return expires, max(0, int((expires - current).total_seconds()))


def event_idempotency_key(
    *, master_login: str, master_server: str, position_id: str | int,
    action: str, source_version: str | int,
) -> str:
    """Stable bounded key derived from native identity and source version."""
    normalized_action = str(action or "").upper()
    if normalized_action not in SUPPORTED_ACTIONS:
        raise CopyDecisionError("UNSUPPORTED_ACTION", "Action de copie non prise en charge.")
    payload = "\n".join((
        str(master_login).strip(), str(master_server).strip(), str(position_id).strip(),
        normalized_action, str(source_version).strip(),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AcademyCopySettings:
    mode: str
    lots: Decimal
    lots_max: Decimal
    multiplier: Decimal
    risk_pct: Decimal
    risk_balance_pct: Decimal
    risk_equity_pct: Decimal
    force_minimum: bool
    invert_trades: bool
    copy_stop_loss: bool
    copy_news_filter: bool
    symbol_lots: Mapping[str, Decimal]


def academy_settings(params: Mapping[str, Any] | None) -> AcademyCopySettings:
    """Read the existing Academy form contract without mutating member data."""
    p = dict(params or {})
    mode = str(p.get("mode_risque") or "Lots fixes").strip()
    if mode not in SUPPORTED_RISK_MODES:
        raise CopyDecisionError("UNSUPPORTED_RISK_MODE", "Le mode de risque Académie n’est pas pris en charge.")
    raw_lots = p.get("lot_symboles") or {}
    if not isinstance(raw_lots, Mapping):
        raise CopyDecisionError("INVALID_SETTINGS", "La configuration des lots par symbole est invalide.")
    symbol_lots = {
        str(symbol).strip(): _decimal(value, "lot_symboles", minimum=Decimal("0"))
        for symbol, value in raw_lots.items()
        if str(symbol).strip()
    }
    pct_values = {
        "risque_pct": _decimal(p.get("risque_pct", "1"), "risque_pct", minimum=Decimal("0")),
        "risque_balance_pct": _decimal(p.get("risque_balance_pct", "1"), "risque_balance_pct", minimum=Decimal("0")),
        "risque_equity_pct": _decimal(p.get("risque_equity_pct", "1"), "risque_equity_pct", minimum=Decimal("0")),
    }
    if any(value > 100 for value in pct_values.values()):
        raise CopyDecisionError("INVALID_SETTINGS", "Le risque configuré ne peut pas dépasser 100 %." )

    def boolean(value, default=False):
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    return AcademyCopySettings(
        mode=mode,
        lots=_decimal(p.get("lots", "0.01"), "lots", minimum=Decimal("0")),
        lots_max=_decimal(p.get("lots_max", "5"), "lots_max", minimum=Decimal("0.01")),
        multiplier=_decimal(p.get("multiplicateur", "1"), "multiplicateur", minimum=Decimal("0")),
        risk_pct=_decimal(p.get("risque_pct", "1"), "risque_pct", minimum=Decimal("0")),
        risk_balance_pct=_decimal(p.get("risque_balance_pct", "1"), "risque_balance_pct", minimum=Decimal("0")),
        risk_equity_pct=_decimal(p.get("risque_equity_pct", "1"), "risque_equity_pct", minimum=Decimal("0")),
        force_minimum=boolean(p.get("forcer_lot_minimum")),
        invert_trades=boolean(p.get("inverser_trades")),
        copy_stop_loss=boolean(p.get("copier_sl"), True),
        copy_news_filter=boolean(p.get("filtre_news")),
        symbol_lots=symbol_lots,
    )


def enabled_unsupported_academy_guards(params: Mapping[str, Any] | None) -> tuple[str, ...]:
    """List Academy loss/goal guards that this copy engine cannot enforce yet.

    The existing profile stores these toggles, but this V1 engine has no
    durable period baseline or close-all reconciliation. Silently ignoring
    an enabled guard would be unsafe, so the caller must block the job.
    """
    p = dict(params or {})

    def enabled(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    keys = (
        "drawdown_actif", "drawdown_gain_actif", "objectif_actif",
    )
    return tuple(key for key in keys if enabled(p.get(key)))


def verify_native_symbol_mapping(
    master: Mapping[str, Any], follower: Mapping[str, Any], *,
    reviewed_alias: bool = False,
) -> dict[str, Any]:
    """Return evidence-backed mapping state; never promote an unproven alias.

    Native evidence must include symbol, trade mode, contract size, tick size,
    currencies, and calculation mode obtained from each connected terminal.
    Exact names are auto-verifiable only when the economic contract is aligned.
    Aliases remain pending until an explicit reviewer approves the captured
    native evidence.
    """
    required = {
        "name", "trade_mode", "contract_size", "tick_size", "currency_base",
        "currency_profit", "currency_margin", "trade_calc_mode",
    }
    if not required.issubset(master) or not required.issubset(follower):
        raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Les preuves natives du symbole sont incomplètes.")
    master_name = str(master["name"] or "").strip()
    follower_name = str(follower["name"] or "").strip()
    if not master_name or not follower_name:
        raise CopyDecisionError("MAPPING_EVIDENCE_INCOMPLETE", "Le symbole natif est vide.")
    tradable = lambda mode: str(mode).upper() not in {
        "0", "1", "2", "3", "DISABLED", "LONGONLY", "LONG_ONLY",
        "SHORTONLY", "SHORT_ONLY", "CLOSEONLY", "CLOSE_ONLY",
    }
    compatible = all(
        str(master[key]).strip().upper() == str(follower[key]).strip().upper()
        for key in ("currency_base", "currency_profit", "currency_margin", "trade_calc_mode")
    ) and _decimal(master["contract_size"], "contract_size", minimum=Decimal("0")) == _decimal(
        follower["contract_size"], "contract_size", minimum=Decimal("0")
    ) and _decimal(master["tick_size"], "tick_size", minimum=Decimal("0")) == _decimal(
        follower["tick_size"], "tick_size", minimum=Decimal("0")
    )
    exact = master_name.casefold() == follower_name.casefold()
    accepted = compatible and tradable(master["trade_mode"]) and tradable(follower["trade_mode"])
    if exact:
        state = "VERIFIED" if accepted else "REJECTED"
    elif reviewed_alias:
        state = "VERIFIED" if accepted else "REJECTED"
    else:
        state = "PENDING_REVIEW" if compatible else "REJECTED"
    evidence = {"master": dict(master), "follower": dict(follower), "compatible": compatible, "exact_name": exact}
    return {
        "state": state,
        "master_symbol": master_name,
        "follower_symbol": follower_name,
        "evidence": evidence,
        "evidence_hash": canonical_hash(evidence),
    }


def normalized_volume(
    requested: Decimal, *, minimum: Decimal, step: Decimal, maximum: Decimal,
    configured_max: Decimal, force_minimum: bool = False,
) -> Decimal:
    """Round down to native step; never increase risk to satisfy a minimum."""
    for name, value in (("minimum", minimum), ("step", step), ("maximum", maximum), ("configured_max", configured_max)):
        if not value.is_finite() or value <= 0:
            raise CopyDecisionError("INVALID_SYMBOL_VOLUME", f"La valeur MT5 {name} est invalide.")
    upper = min(maximum, configured_max)
    target = min(requested, upper)
    if target < minimum:
        if not force_minimum or minimum > upper:
            raise CopyDecisionError("VOLUME_BELOW_MINIMUM", "Le volume calculé est inférieur au minimum MT5 ; aucun lot minimal ne sera forcé.")
        target = minimum
    steps = ((target - minimum) / step).to_integral_value(rounding=ROUND_DOWN)
    resolved = minimum + steps * step
    if resolved < minimum or resolved > upper:
        raise CopyDecisionError("VOLUME_OUT_OF_RANGE", "Le volume calculé est hors limites MT5.")
    return resolved.normalize()


def requested_volume(
    settings: AcademyCopySettings, *, source_symbol: str, source_volume: Decimal,
    balance: Decimal, equity: Decimal, loss_per_lot: Decimal | None = None,
) -> Decimal:
    if settings.mode == "Lots fixes":
        requested = settings.lots
    elif settings.mode == "Copier les lots de l'envoyeur":
        requested = source_volume * settings.multiplier
    elif settings.mode == "Lot par symbole":
        if source_symbol not in settings.symbol_lots:
            raise CopyDecisionError("SYMBOL_LOT_NOT_CONFIGURED", "Aucun lot Académie n’est défini pour ce symbole.")
        requested = settings.symbol_lots[source_symbol]
    else:
        base = balance if settings.mode == "Risque par solde (Balance)" else equity
        percent = settings.risk_pct if settings.mode == "Risque en %" else (
            settings.risk_balance_pct if settings.mode == "Risque par solde (Balance)" else settings.risk_equity_pct
        )
        if loss_per_lot is None or loss_per_lot <= 0:
            raise CopyDecisionError("STOP_LOSS_REQUIRED_FOR_RISK", "Un stop et un calcul natif de perte sont requis pour dimensionner le risque.")
        requested = base * percent / Decimal("100") / loss_per_lot
    if requested <= 0:
        raise CopyDecisionError("ZERO_VOLUME", "Le volume calculé est nul.")
    return requested


def execution_release_allowed(*, mode: str, execution_enabled: bool,
                              live_release_approved: bool, account_is_demo: bool) -> bool:
    """Fail-closed runtime release check; callers must also check consent/status."""
    normalized_mode = str(mode or "simulation").strip().lower()
    if normalized_mode == "simulation":
        return False
    if not execution_enabled:
        return False
    if normalized_mode == "demo":
        return account_is_demo
    if normalized_mode == "live":
        return live_release_approved and not account_is_demo
    return False


def worker_claims_enabled(*, mode: str, execution_enabled: bool,
                          live_release_approved: bool) -> bool:
    """Allow a slot to claim; the child still validates the connected account type."""
    normalized_mode = str(mode or "simulation").strip().lower()
    return bool(
        execution_enabled
        and (normalized_mode == "demo" or
             (normalized_mode == "live" and live_release_approved))
    )
