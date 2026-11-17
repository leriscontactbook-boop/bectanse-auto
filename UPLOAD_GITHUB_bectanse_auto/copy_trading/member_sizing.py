"""Shared member volume policy, recomputed at the executor and reservation server.

Native profit calculations are denominated in the follower account currency.
Configured fixed lots remain exact except for explicitly selected minimum uplift.
Calculated modes round down to the broker step and never exceed lots_max.
"""
from .demo_policy import SafetyError, decimal, floor_volume

FIXED = {'Lots fixes', 'Lot par symbole'}
RISK = {'Risque par solde (Balance)': ('balance', 'risque_balance_pct'),
        'Risque par capitaux (Equity)': ('equity', 'risque_equity_pct'),
        'Risque en %': ('balance', 'risque_pct')}


def raw_volume(settings, symbol, evidence):
    mode = settings['mode_risque']
    if mode == 'Lots fixes':return decimal(settings['lots'])
    if mode == 'Lot par symbole':return decimal(settings['lot_symboles'].get(symbol, 0))
    if mode == "Copier les lots de l'envoyeur":
        return decimal(evidence['source_volume']) * decimal(settings['multiplicateur'])
    if mode not in RISK:raise SafetyError('UNSUPPORTED_LOT_MODE')
    if not settings['copier_sl']:raise SafetyError('RISK_STOP_LOSS_REQUIRED')
    capital, percent = RISK[mode]
    amount = decimal(evidence[capital]); loss = decimal(evidence['loss_per_lot'])
    if amount <= 0 or loss <= 0:raise SafetyError('RISK_SIZING_EVIDENCE_REQUIRED')
    return amount * decimal(settings[percent]) / 100 / loss


def volume(settings, symbol, spec, evidence):
    raw = raw_volume(settings, symbol, evidence)
    minimum, maximum, step = (decimal(spec[k]) for k in ('volume_min','volume_max','volume_step'))
    cap = min(decimal(settings['lots_max']), maximum)
    if minimum <= 0 or step <= 0 or maximum < minimum:raise SafetyError('INVALID_VOLUME_SPEC')
    if raw <= 0:raise SafetyError('REQUESTED_VOLUME_NOT_EXECUTABLE')
    if raw < minimum and settings.get('forcer_lot_minimum'):
        raw = minimum
    if settings['mode_risque'] in FIXED:
        if raw < minimum or raw > cap or raw % step:raise SafetyError('REQUESTED_VOLUME_NOT_EXECUTABLE')
        return raw
    lots = floor_volume(min(raw, cap), spec)
    if lots <= 0:raise SafetyError('VOLUME_BELOW_MINIMUM')
    return lots


def effective_prices(settings, source):
    """Invert direction and exchange SL/TP roles, retaining actual price levels."""
    sl, tp = source['sl'], source['tp']
    if settings.get('inverser_trades'):sl, tp = tp, sl
    return {'sl':sl if settings['copier_sl'] else '0', 'tp':tp}


def effective_side(settings, side):
    if side not in {'BUY','SELL'}:raise SafetyError('SOURCE_SIDE_INVALID')
    return ('SELL' if side == 'BUY' else 'BUY') if settings.get('inverser_trades') else side


def effective_order_type(settings, order_type):
    if order_type not in range(2,8):raise SafetyError('PENDING_TYPE_INVALID')
    # At the same trigger price buy-limit becomes sell-stop, and vice versa.
    # Stop-limit requires two different directional triggers; retain a denial
    # until an explicit equivalent can be constructed without changing prices.
    if not settings.get('inverser_trades'):return order_type
    if order_type in (6,7):raise SafetyError('INVERTED_STOPLIMIT_NOT_EXECUTABLE')
    return {2:5,3:4,4:3,5:2}[order_type]
