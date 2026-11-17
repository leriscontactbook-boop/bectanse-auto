"""Conservative pilot policy. No path in this module enables real-money trading."""

from decimal import Decimal, ROUND_DOWN

from .settings import validate


class SafetyError(RuntimeError):
    """Stable, non-sensitive error code suitable for status reporting."""


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except Exception as exc:
        raise SafetyError("INVALID_NUMBER") from exc


def executable_settings(settings, require_stop_loss=True, *, supported_options=frozenset(), all_lot_modes=False):
    result = validate(settings)
    if not all_lot_modes and result["mode_risque"] not in {"Lots fixes", "Lot par symbole"}:
        raise SafetyError("UNSUPPORTED_LOT_MODE")
    for key in ("forcer_lot_minimum", "inverser_trades", "copier_ordres_en_attente",
                "convertir_pending_invalide", "drawdown_actif", "drawdown_gain_actif",
                "objectif_actif", "filtre_news"):
        if result[key] and key not in supported_options:
            raise SafetyError("UNSUPPORTED_OPTION_" + key.upper())
    if require_stop_loss and not result["copier_sl"]:
        raise SafetyError("STOP_LOSS_REQUIRED")
    return result


def floor_volume(value, spec):
    step = decimal(spec["volume_step"])
    minimum, maximum = decimal(spec["volume_min"]), decimal(spec["volume_max"])
    if step <= 0 or minimum <= 0 or maximum < minimum:
        raise SafetyError("INVALID_VOLUME_SPEC")
    lots = (min(decimal(value), maximum) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return lots if lots >= minimum else Decimal(0)


def opening_volume(settings, source_symbol, target_spec, hard_cap, allow_broker_minimum=False, exact_member_volume=False):
    raw = (settings["lots"] if settings["mode_risque"] == "Lots fixes"
           else settings["lot_symboles"].get(source_symbol, 0))
    if exact_member_volume:
        lots = decimal(raw)
        step = decimal(target_spec['volume_step'])
        if (step <= 0 or lots <= 0 or lots < decimal(target_spec['volume_min'])
                or lots > decimal(target_spec['volume_max']) or lots % step
                or lots > decimal(settings['lots_max'])):
            raise SafetyError('REQUESTED_VOLUME_NOT_EXECUTABLE')
        return lots
    capped = min(decimal(raw), decimal(settings["lots_max"]), decimal(hard_cap))
    if allow_broker_minimum and capped > 0:
        capped = max(capped, decimal(target_spec["volume_min"]))
    lots = floor_volume(capped, target_spec)
    if lots <= 0:
        raise SafetyError("VOLUME_BELOW_MINIMUM")
    return lots


def compatible(source, target):
    for key in ("currency_base", "currency_profit", "trade_calc_mode"):
        if source.get(key) != target.get(key):
            raise SafetyError("INCOMPATIBLE_SYMBOL")
    for key in ("trade_contract_size", "trade_tick_size", "point"):
        if decimal(source.get(key)) <= 0 or decimal(source[key]) != decimal(target.get(key)):
            raise SafetyError("INCOMPATIBLE_SYMBOL")


def same_price(first, second):
    return decimal(first) == decimal(second)
