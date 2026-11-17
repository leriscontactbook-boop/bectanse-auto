"""Read-only observations from a dedicated, already attached MT5 terminal.

No login, symbol selection, order check or order submission. The caller owns the
terminal connection and its process lock. An observation is not an admission or
an execution authorization, including when every requested field is available.
"""
import hashlib
import hmac
import json
import ntpath
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


class ObservationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def number(value):
    if value is None or isinstance(value, bool):
        raise ObservationError('FINANCIAL_DATA_MISSING')
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation()
        return str(result)
    except (ValueError, InvalidOperation):
        raise ObservationError('FINANCIAL_DATA_INVALID') from None


def _identity(mt5, expected):
    a, t = mt5.account_info(), mt5.terminal_info()
    if a is None or t is None or not t.connected:
        raise ObservationError('TERMINAL_DISCONNECTED')
    if (str(a.login), a.server) != (expected['login'], expected['server']):
        raise ObservationError('ACCOUNT_IDENTITY_CHANGED')
    if ntpath.normcase(ntpath.normpath(t.path)) != ntpath.normcase(ntpath.dirname(expected['terminal'])):
        raise ObservationError('TERMINAL_IDENTITY_CHANGED')
    if a.trade_mode != expected['trade_mode']:
        raise ObservationError('ACCOUNT_MODE_CHANGED')
    return a, t


def collect(mt5, expected, volumes, *, history_from, key, clock=time.time):
    """Sign a bounded financial/metadata observation, not a compatibility claim.

    REAL and DEMO observations are supported explicitly; no trading API is used.
    Realized figures cover only the supplied history interval. Deposits and
    withdrawals are excluded; all raw deal types are retained for reconciliation.
    Missing history is an error, while an empty successful response is valid.
    """
    if (set(expected) != {'member_code', 'account_id', 'version', 'login', 'server', 'terminal', 'trade_mode'}
            or type(expected['trade_mode']) is not int or expected['trade_mode'] not in (0, 2)
            or any(type(expected[k]) is not int or expected[k] < 1 for k in ('account_id', 'version'))
            or any(not isinstance(expected[k], str) or not expected[k] for k in ('member_code', 'login', 'server', 'terminal'))
            or not isinstance(key, bytes) or len(key) < 32):
        raise ObservationError('INVALID_OBSERVATION_CONFIGURATION')
    start = clock()
    if not 0 <= history_from <= start:
        raise ObservationError('INVALID_HISTORY_WINDOW')
    try:
        a, t = _identity(mt5, expected)
        positions = mt5.positions_get()
        deals = mt5.history_deals_get(datetime.fromtimestamp(history_from, timezone.utc),
                                     datetime.fromtimestamp(start, timezone.utc))
        if positions is None or deals is None:
            raise ObservationError('COLLECTION_UNAVAILABLE')
        catalog = {}
        for symbol, requested in volumes.items():
            s = mt5.symbol_info(symbol)
            if s is None:
                raise ObservationError('SYMBOL_UNAVAILABLE')
            spec = {k: number(getattr(s, k, None)) for k in
                    ('volume_min', 'volume_max', 'volume_step', 'trade_contract_size', 'trade_tick_size', 'point')}
            v, low, high, step = (Decimal(number(x)) for x in
                                  (requested, s.volume_min, s.volume_max, s.volume_step))
            if step <= 0 or low <= 0 or high < low or v < low or v > high or v % step:
                raise ObservationError('VOLUME_NOT_EXECUTABLE')
            spec.update({k: getattr(s, k) for k in ('currency_profit', 'currency_margin', 'digits',
                        'trade_calc_mode', 'trade_exemode', 'filling_mode', 'expiration_mode',
                        'trade_stops_level', 'trade_freeze_level', 'trade_mode')})
            catalog[symbol] = spec
        position_data = []
        for p in positions:
            position_data.append({**{k: getattr(p, k) for k in ('ticket', 'identifier', 'symbol', 'type', 'magic')},
                                  **{k: number(getattr(p, k, None)) for k in ('volume', 'price_open', 'sl', 'tp', 'profit', 'swap')}})
        history = []
        seen = set()
        for d in deals:
            if d.ticket in seen:
                raise ObservationError('DUPLICATE_DEAL')
            seen.add(d.ticket)
            if not history_from <= d.time <= start:
                raise ObservationError('HISTORY_OUTSIDE_WINDOW')
            history.append({**{k: getattr(d, k) for k in ('ticket', 'order', 'position_id', 'symbol', 'type', 'entry', 'time', 'magic')},
                            **{k: number(getattr(d, k, None)) for k in ('volume', 'profit', 'commission', 'swap', 'fee')}})
        ending, terminal = _identity(mt5, expected)
        if terminal.build != t.build:
            raise ObservationError('TERMINAL_VERSION_CHANGED')
        # Collection spans calls, so it is not an atomic broker reconciliation.
        end = clock()
        if not 0 <= end - start <= 10:
            raise ObservationError('COLLECTION_TOO_SLOW')
        body = {'schema': 1, 'identity': dict(expected), 'source': 'MT5_TERMINAL',
                'started_at': start, 'observed_at': end, 'expires_at': start + 90,
                'terminal_build': t.build, 'currency': ending.currency,
                'margin_mode': getattr(ending, 'margin_mode', None),
                'fifo_close': getattr(ending, 'fifo_close', None),
                'permissions': {'account_trade_allowed': ending.trade_allowed,
                                'account_trade_expert': ending.trade_expert,
                                'terminal_trade_allowed': terminal.trade_allowed,
                                'python_api_disabled': terminal.tradeapi_disabled},
                'financial_consistent': number(a.balance) == number(ending.balance),
                'balance': number(ending.balance), 'equity': number(ending.equity),
                'unrealized_profit': number(ending.profit),
                'trade_allowed': bool(ending.trade_allowed and ending.trade_expert and
                                      terminal.trade_allowed and not terminal.tradeapi_disabled),
                'positions': position_data, 'deals': history, 'symbols': catalog,
                'history_from': history_from, 'history_to': start,
                'realized_net_in_window': str(sum((sum((Decimal(d[k]) for k in
                    ('profit', 'commission', 'swap', 'fee')), Decimal(0)) for d in history if d['type'] in (0, 1)), Decimal(0))),
                'reconciled': False, 'execution_authorized': False}
        return {'body': body, 'signature': hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}
    except ObservationError:
        raise
    except Exception:
        # Broker exceptions can contain credentials; do not propagate their text.
        raise ObservationError('COLLECTION_FAILED') from None


def verify(envelope, expected, key, *, now=None):
    """Verify origin, exact configuration binding and bounded age before use."""
    now = time.time() if now is None else now
    try:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError()
        body = envelope['body']
        signature = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, envelope['signature']):
            raise ValueError()
        if (body['identity'] != expected or body['schema'] != 1
                or not body['started_at'] <= body['observed_at'] <= now < body['expires_at']
                or body['expires_at'] - body['started_at'] > 90
                or body['observed_at'] - body['started_at'] > 10
                or body['execution_authorized'] is not False or body['reconciled'] is not False):
            raise ValueError()
        return body
    except Exception:
        raise ObservationError('OBSERVATION_INVALID_OR_EXPIRED') from None
