"""Local qualification gate. No login, order, process, network or risk modification.

A reviewed matrix hash and read-only observation providers are mandatory.
This module is not wired into deployed admission/execution paths.
"""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from decimal import Decimal, InvalidOperation

STATUSES = {'QUALIFIED_DEMO', 'QUALIFIED_LIMITED', 'REJECTED', 'NOT_TESTED', 'EXPIRED', 'REQUIRES_REVIEW'}
CONTROLS = ('connection', 'symbols', 'contract_tick', 'precision', 'volume', 'margin_currency',
            'execution_filling', 'validity', 'sessions', 'stops', 'errors_late_response', 'reconnection')
SCOPE_KEYS = ('source_broker', 'source_server', 'source_account_type', 'target_broker',
              'target_server', 'target_account_type', 'source_symbol', 'target_symbol', 'environment')
FIELDS = ('name', 'currency_base', 'currency_profit', 'currency_margin', 'trade_calc_mode',
          'trade_contract_size', 'trade_tick_value', 'trade_tick_value_profit', 'trade_tick_value_loss',
          'trade_tick_size', 'point', 'digits', 'volume_min', 'volume_max', 'volume_step',
          'trade_exemode', 'filling_mode', 'expiration_mode', 'order_mode', 'order_gtc_mode',
          'trade_mode', 'trade_stops_level', 'trade_freeze_level')
NUMBERS = ('trade_contract_size', 'trade_tick_value', 'trade_tick_value_profit',
           'trade_tick_value_loss', 'trade_tick_size', 'point', 'volume_min', 'volume_max', 'volume_step')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def number(value):
    if isinstance(value, bool):
        raise ValueError('INVALID_NUMBER')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('INVALID_NUMBER') from None
    if not result.is_finite():
        raise ValueError('INVALID_NUMBER')
    return result


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('INVALID_TIMESTAMP')
    return value


VALUATION_FIELDS=('trade_tick_value','trade_tick_value_profit','trade_tick_value_loss')


def valuation_identity(observation):
    """Separate live account-currency conversion from stable contract identity.

    MT5 tick values are current monetary valuations, not contract dimensions.
    For a converted account retain the currency pair in the fingerprint and
    still validate every fresh native value. Fixed-volume contract/lot checks
    remain exact. Unconverted tick values retain their previous exact checks.
    """
    spec=observation['spec'];currency=observation.get('account',{}).get('currency')
    converted=isinstance(currency,str) and currency and isinstance(spec.get('currency_profit'),str) and currency!=spec['currency_profit']
    result={}
    for key in VALUATION_FIELDS:
        value=number(spec[key])
        if value<=0:raise ValueError('INVALID_METADATA')
        result[key]=('CONVERTED:'+currency+':'+spec['currency_profit']) if converted else str(value.normalize())
    return result


def metadata(observation):
    """Stable characterization only; quote, readiness and collection times are separate."""
    spec = observation['spec']
    result = {key: spec[key] for key in FIELDS}
    for key in NUMBERS:
        x = number(result[key])
        if x <= 0:
            raise ValueError('INVALID_METADATA')
        result[key] = str(x.normalize())
    for key in ('digits', 'trade_calc_mode', 'trade_exemode', 'filling_mode', 'expiration_mode',
                'order_mode', 'order_gtc_mode', 'trade_mode', 'trade_stops_level', 'trade_freeze_level'):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError('INVALID_METADATA')
    if number(result['volume_min']) > number(result['volume_max']):
        raise ValueError('INVALID_METADATA')
    for key in ('name', 'currency_base', 'currency_profit', 'currency_margin'):
        if not isinstance(result[key], str) or not result[key]:
            raise ValueError('INVALID_METADATA')
    account = observation['account']
    if set(account) != {'currency', 'margin_mode', 'trade_mode', 'leverage'}:
        raise ValueError('INVALID_ACCOUNT_PROFILE')
    if type(observation['terminal_build']) is not int or observation['terminal_build'] <= 0:
        raise ValueError('INVALID_TERMINAL_BUILD')
    sessions = observation['sessions']
    if not isinstance(sessions, dict) or not sessions.get('timezone') or not sessions.get('schedule') or not sessions.get('source'):
        raise ValueError('SESSION_EVIDENCE_MISSING')
    result.update(valuation_identity(observation))
    return {'spec': result, 'account': account, 'terminal_build': observation['terminal_build'], 'sessions': sessions}


def observe_mt5(module, symbol, *, expected_identity, account_type, session_reader, reconciliation_reader, clock=time.time, expected_trade_mode=0, broker_time_offset_seconds=0):
    """Read an ALREADY connected terminal. Never initialize/login/select/check/send.

    Identity expected_identity contains login/server/company, used in memory only.
    Session and reconciliation providers must supply actual read-only evidence;
    there is no assumption that a quote alone proves an open trading session.
    """
    from .broker_clock import quote_utc
    start = clock()
    a, t = module.account_info(), module.terminal_info()
    if a is None or t is None or not t.connected:
        raise ValueError('TERMINAL_DISCONNECTED')
    if (str(a.login), a.server, a.company) != expected_identity:
        raise ValueError('ACCOUNT_IDENTITY_CHANGED')
    s, tick = module.symbol_info(symbol), module.symbol_info_tick(symbol)
    if s is None:
        raise ValueError('SYMBOL_ABSENT')
    sessions = session_reader(symbol)
    reconciliation = reconciliation_reader()
    if tick is None or number(tick.bid) <= 0 or number(tick.ask) < number(tick.bid):
        raise ValueError('INVALID_QUOTE')
    after = module.account_info()
    if after is None or (str(after.login), after.server, after.company) != expected_identity:
        raise ValueError('ACCOUNT_IDENTITY_CHANGED')
    if expected_trade_mode not in (0, 2) or type(expected_trade_mode) is not int:
        raise ValueError('INVALID_ACCOUNT_MODE')
    if a.trade_mode != expected_trade_mode:
        raise ValueError('REAL_SCOPE_FORBIDDEN' if expected_trade_mode == 0 else 'REAL_ACCOUNT_REQUIRED')
    if after.trade_mode != a.trade_mode:
        raise ValueError('ACCOUNT_IDENTITY_CHANGED')
    return {'broker': a.company, 'server': a.server, 'account_type': account_type, 'environment': 'REAL' if expected_trade_mode == 2 else 'DEMO',
            'observed_at': start, 'connected': True, 'terminal_build': t.build,
            'account': {key: getattr(a, key) for key in ('currency', 'margin_mode', 'trade_mode', 'leverage')},
            'spec': {key: getattr(s, key, None) for key in FIELDS},
            'sessions': sessions['metadata'], 'session_open': sessions['open'],
            'session_checked_at': sessions['observed_at'],
            'quote_at': quote_utc(tick.time_msc,broker_time_offset_seconds),
            'quote_time_msc_raw':tick.time_msc,'broker_time_offset_seconds':broker_time_offset_seconds,
            'reconciled': reconciliation['reconciled'], 'uncertain': reconciliation['uncertain'],
            'reconciled_at': reconciliation['observed_at']}


class QualificationGate:
    environment = 'DEMO'
    evidence_environment = 'MT5_DEMO'
    account_modes = {'source': 0, 'target': 0}

    def __init__(self, matrix, *, approved_sha256, outbox, clock=time.time, max_age=5):
        self.path = Path(matrix).resolve()
        self.approved_sha256 = approved_sha256
        self.outbox = Path(outbox)
        self.clock = clock
        if not 0 < timestamp(max_age) <= 5:
            raise ValueError('INVALID_FRESHNESS_POLICY')
        self.max_age = max_age

    def _deny(self, scope, reason, status='REQUIRES_REVIEW'):
        """Durable notification intent, no external message sent by this local module."""
        scope_hash = digest(scope)
        with sqlite3.connect(self.outbox, timeout=2) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('''CREATE TABLE IF NOT EXISTS compatibility_alerts(
                key TEXT PRIMARY KEY, scope_hash TEXT, reason TEXT, first_at REAL,
                last_at REAL, occurrences INTEGER, delivery TEXT DEFAULT 'PENDING')''')
            key = digest([scope_hash, reason])
            now = self.clock()
            db.execute('''INSERT INTO compatibility_alerts(key,scope_hash,reason,first_at,last_at,occurrences)
                VALUES(?,?,?,?,?,1) ON CONFLICT(key) DO UPDATE SET last_at=excluded.last_at,occurrences=occurrences+1''',
                (key, scope_hash, reason, now, now))
        return {'allowed': False, 'status': status, 'reason': reason, 'alert': 'PENDING', 'scope_hash': scope_hash}

    def validate_evidence(self, row):
        if set(row['tests']) != set(CONTROLS):
            raise ValueError('INCOMPLETE_CONTROLS')
        for test in row['tests'].values():
            if test['result'] != 'PASS' or test['environment'] != self.evidence_environment:
                return 'DEMO_EVIDENCE_MISSING' if self.environment == 'DEMO' else 'REAL_EVIDENCE_MISSING'
            proof = (self.path.parent / test['evidence']).resolve()
            if self.path.parent not in proof.parents or hashlib.sha256(proof.read_bytes()).hexdigest() != test['sha256']:
                return 'EVIDENCE_INTEGRITY'

    def evaluate(self, scope, *, source_reader, target_reader, volume, filling, validity, operation='OPEN', order_type=None):
        """For new admission/new operation only; never abandons existing position management.

        Readers are invoked on every check. An allowed result is not an order permit
        and must not be cached. Existing risk/SL/order preflights still apply.
        """
        try:
            if set(scope) != set(SCOPE_KEYS) or any(not isinstance(x, str) or not x for x in scope.values()):
                raise ValueError('INVALID_SCOPE')
            if scope['environment'] != self.environment:
                return self._deny(scope, 'REAL_SCOPE_FORBIDDEN', 'REJECTED')
            raw = self.path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.approved_sha256:
                return self._deny(scope, 'MATRIX_INTEGRITY')
            matrix = json.loads(raw)
            if matrix['schema_version'] != 1 or type(matrix['revision']) is not int or matrix['revision'] < 1:
                raise ValueError('INVALID_MATRIX')
            rows = [r for r in matrix['records'] if r['scope'] == scope]
            if not rows:
                return self._deny(scope, 'QUALIFICATION_MISSING', 'NOT_TESTED')
            if len(rows) != 1:
                return self._deny(scope, 'AMBIGUOUS_QUALIFICATION')
            row = rows[0]
            if row['status'] not in STATUSES:
                raise ValueError('INVALID_STATUS')
            if row['status'] not in {'QUALIFIED_DEMO', 'QUALIFIED_LIMITED'}:
                return self._deny(scope, 'SCOPE_NOT_QUALIFIED', row['status'])
            if self.environment != 'DEMO' and row['status'] != 'QUALIFIED_LIMITED':
                return self._deny(scope, 'REAL_LIMITED_QUALIFICATION_REQUIRED', 'REJECTED')
            now = timestamp(self.clock())
            if not timestamp(row['checked_at']) <= now < timestamp(row['expires_at']):
                return self._deny(scope, 'QUALIFICATION_EXPIRED', 'EXPIRED')
            if not row.get('reviewer') or not row.get('restrictions'):
                raise ValueError('REVIEW_REQUIRED')
            for side in ('source', 'target'):
                if digest(row['metadata'][side]) != row['metadata_sha256'][side]:
                    raise ValueError('INVALID_METADATA_DIGEST')
            evidence_error=self.validate_evidence(row)
            if evidence_error:return self._deny(scope,evidence_error)
            restrictions = row['restrictions']
            if set(restrictions) != {'operations', 'filling_modes', 'validity_modes', 'max_volume'}:
                raise ValueError('UNSUPPORTED_RESTRICTION')
            if any(not isinstance(restrictions[k], list) or not restrictions[k] for k in ('operations', 'filling_modes', 'validity_modes')):
                raise ValueError('INVALID_RESTRICTIONS')
            if operation not in restrictions['operations'] or filling not in restrictions['filling_modes'] or validity not in restrictions['validity_modes']:
                return self._deny(scope, 'UNSUPPORTED_OPERATION_MODE', 'REJECTED')
            source, target = source_reader(), target_reader()
            now = timestamp(self.clock())
            if not row['checked_at'] <= now < row['expires_at']:
                return self._deny(scope, 'QUALIFICATION_EXPIRED', 'EXPIRED')
            for side, observation in [('source', source), ('target', target)]:
                if (observation['environment'] != ('REAL' if self.account_modes[side] == 2 else 'DEMO')
                    or observation['account']['trade_mode'] != self.account_modes[side]):
                    return self._deny(scope, 'REAL_SCOPE_FORBIDDEN', 'REJECTED')
                if any(observation[k] != scope[side+'_'+k] for k in ('broker', 'server', 'account_type')):
                    return self._deny(scope, 'OBSERVATION_SCOPE_MISMATCH')
                if observation['connected'] is not True:
                    return self._deny(scope, 'TERMINAL_DISCONNECTED')
                for field in ('observed_at', 'quote_at', 'session_checked_at', 'reconciled_at'):
                    if not 0 <= now - timestamp(observation[field]) <= self.max_age:
                        return self._deny(scope, 'METADATA_OR_EVIDENCE_STALE', 'EXPIRED')
                if observation['uncertain'] is not False or observation['reconciled'] is not True:
                    return self._deny(scope, 'EXECUTION_UNCERTAIN')
                if observation['session_open'] is not True:
                    return self._deny(scope, 'MARKET_SESSION_CLOSED', 'REJECTED')
                observed = metadata(observation)
                if observed['spec']['name'] != scope[side+'_symbol']:
                    return self._deny(scope, 'SYMBOL_RENAMED_OR_ABSENT', 'REJECTED')
                if digest(observed) != row['metadata_sha256'][side]:
                    return self._deny(scope, 'CHARACTERISTICS_CHANGED')
            # Conservative direct copy policy: no conversion of unlike contracts.
            a, b = metadata(source)['spec'], metadata(target)['spec']
            for key in ('trade_contract_size', 'trade_tick_size', 'trade_tick_value', 'currency_profit', 'currency_base', 'trade_calc_mode'):
                if a[key] != b[key]:
                    return self._deny(scope, 'CONTRACT_EQUIVALENCE_NOT_PROVEN', 'REJECTED')
            native = operation in {'PENDING_CREATE','PENDING_MODIFY','PENDING_CANCEL'}
            if native:
                if type(order_type) is not int or order_type not in range(2,8):
                    return self._deny(scope, 'PENDING_TYPE_INVALID', 'REJECTED')
                flag=2 if order_type in (2,3) else 4 if order_type in (4,5) else 8
                if filling!='RETURN' or not b['order_mode'] & flag or b['trade_mode']!=4:
                    return self._deny(scope, 'PENDING_MODE_UNSUPPORTED', 'REJECTED')
                validity_flag={'GTC':1,'DAY':2,'SPECIFIED':4,'SPECIFIED_DAY':8}.get(validity,0)
                if not validity_flag or not b['expiration_mode'] & validity_flag:
                    return self._deny(scope, 'VALIDITY_NOT_SUPPORTED', 'REJECTED')
            else:
                if filling not in ('FOK', 'IOC') or not b['filling_mode'] & (1 if filling == 'FOK' else 2):
                    return self._deny(scope, 'FILLING_NOT_SUPPORTED', 'REJECTED')
                if validity != 'GTC' or not b['expiration_mode'] & 1:
                    return self._deny(scope, 'VALIDITY_NOT_SUPPORTED', 'REJECTED')
                if b['trade_mode'] != 4 or not b['order_mode'] & 1:
                    return self._deny(scope, 'MARKET_ORDER_NOT_SUPPORTED', 'REJECTED')
            lots = number(volume)
            if lots < number(b['volume_min']) or lots > number(b['volume_max']):
                return self._deny(scope, 'VOLUME_OUT_OF_RANGE', 'REJECTED')
            if lots % number(b['volume_step']):
                return self._deny(scope, 'VOLUME_STEP_MISMATCH', 'REJECTED')
            if lots > number(restrictions['max_volume']):
                return self._deny(scope, 'QUALIFICATION_VOLUME_LIMIT', 'REJECTED')
            return {'allowed': True, 'status': row['status'], 'reason': 'QUALIFICATION_MATCH',
                    'matrix_revision': matrix['revision'], 'volume': str(lots), 'valid_until': min(now+self.max_age, row['expires_at'])}
        except (OSError, KeyError, TypeError, ValueError, InvalidOperation):
            return self._deny(scope, 'MISSING_OR_INVALID_EVIDENCE')
        except Exception:
            return self._deny(scope, 'OBSERVATION_FAILED')

    def require_qualified(self, *args, **kwargs):
        result = self.evaluate(*args, **kwargs)
        if not result['allowed']:
            raise PermissionError('COMPATIBILITY_DENIED:' + result['reason'])
        return result


class DemoToRealQualificationGate(QualificationGate):
    """Explicit, separately evidenced scope. Never promotes a demo matrix.

    A matching reviewed QUALIFIED_LIMITED record with MT5_DEMO_TO_REAL proofs
    is required. This class neither creates that record nor authorizes orders.
    """
    environment = 'DEMO_TO_REAL'
    evidence_environment = 'MT5_DEMO_TO_REAL'
    account_modes = {'source': 0, 'target': 2}


class NativePreflightQualificationGate(DemoToRealQualificationGate):
    """Admin-reviewed native metadata policy; never claims order qualification.

    Old execution-qualified matrices keep their existing validation. New records
    require signed captures from the registered collector and per-operation live
    checks inherited above. The server independently requires admin and member
    authorization before submission.
    """
    def __init__(self, *args, evidence_key, expected_accounts, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence_key=evidence_key
        self.expected_accounts=expected_accounts

    def validate_evidence(self,row):
        from .native_preflight_review import POLICY,artifact
        if row.get('review_policy')!=POLICY:return super().validate_evidence(row)
        import hmac
        from .account_observation import canonical
        if row.get('execution_validation')!='NOT_OBSERVED' or row.get('tests')!={}:
            raise ValueError('INVALID_PREFLIGHT_EXECUTION_CLAIM')
        if not isinstance(self.evidence_key,bytes) or len(self.evidence_key)<32:
            raise ValueError('REGISTERED_COLLECTOR_REQUIRED')
        for side in ('source','target'):
            envelope=json.loads(artifact(self.path.parent,row['captures'][side]));body=envelope['body']
            if not hmac.compare_digest(str(envelope.get('signature','')),hmac.new(self.evidence_key,canonical(body),hashlib.sha256).hexdigest()):
                raise ValueError('PREFLIGHT_SIGNATURE_INVALID')
            stamp=timestamp(row['checked_at'])
            # A catalog capture may contain failures for unrelated instruments.
            # Only the selected pair participates in this admission decision.
            symbol=row['scope'][side+'_symbol']
            errors=body.get('errors',{})
            if (body.get('source')!='MT5_READ_ONLY' or body.get('error')
                or not isinstance(errors,dict) or symbol in errors
                or body.get('identity')!={'account':self.expected_accounts[side],'trade_mode':self.account_modes[side]}
                or not timestamp(body['observed_at'])<=stamp<timestamp(body['expires_at'])
                or not 0<body['expires_at']-body['observed_at']<=5):
                raise ValueError('PREFLIGHT_CAPTURE_INVALID')
            observed=body['qualification'][row['scope'][side+'_symbol']]
            if digest(metadata(observed))!=row['metadata_sha256'][side]:
                raise ValueError('PREFLIGHT_METADATA_CHANGED')
            if (observed.get('connected') is not True or observed.get('reconciled') is not True
                or observed.get('uncertain') is not False or observed.get('session_open') is not True):
                raise ValueError('PREFLIGHT_NATIVE_CHECK_FAILED')
            for field in ('observed_at','quote_at','session_checked_at','reconciled_at'):
                if not 0<=stamp-timestamp(observed[field])<=5:raise ValueError('PREFLIGHT_CAPTURE_STALE')
