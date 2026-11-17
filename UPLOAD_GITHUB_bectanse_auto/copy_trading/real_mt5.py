"""Real-account adapter reusing the existing order and reconciliation mechanics.

No runtime selects this adapter yet. Deployment authorization is an injected
per-operation boundary, not an environment flag. Tests must inject a fake MT5
module and a fake boundary; never run them against a real terminal.
"""
from contextlib import contextmanager
from .demo_mt5 import DemoMT5, NotSent
from .demo_policy import SafetyError


class _SubmissionBoundary:
    """Delegate the LAST check, after order_check and immediately before send.

    The boundary must atomically reserve the operation against authoritative
    member state, and reconcile ambiguous results before permitting any retry.
    A cached authorization or a simple renewal must not provide this contract.
    """
    def __init__(self, boundary):
        self.boundary = boundary
        self.logical = None

    @contextmanager
    def submission(self):
        if self.logical is None:
            raise NotSent('OPERATION_CONTEXT_REQUIRED')
        with self.boundary.submission(self.logical):
            yield


class RealMT5(DemoMT5):
    order_comment = 'BCT-REAL'

    def __init__(self, module, *, execution_boundary=None, **kwargs):
        # Replacing admission via kwargs would allow bypassing the boundary.
        if 'admission' in kwargs:
            raise ValueError('USE_EXECUTION_BOUNDARY')
        self.boundary = execution_boundary
        self._submission = _SubmissionBoundary(execution_boundary)
        super().__init__(module, admission=self._submission, **kwargs)

    def check_account_mode(self, account):
        if account.trade_mode != getattr(self.mt5, 'ACCOUNT_TRADE_MODE_REAL', 2):
            raise SafetyError('REAL_ACCOUNT_REQUIRED')

    def connect(self):
        try:
            return super().connect()
        except SafetyError as error:
            # Preserve the numeric SDK diagnostic before session cleanup; never
            # persist the SDK message, which can contain credentials or identity.
            if str(error) in {'TERMINAL_AUTH_FAILED', 'TERMINAL_CONNECTION_FAILED'}:
                diagnostic = self.mt5.last_error()
                if diagnostic and type(diagnostic[0]) is int:
                    error.diagnostic_code = diagnostic[0]
            raise

    def execute(self, logical):
        if self.boundary is None:
            raise NotSent('OPERATIONAL_AUTHORIZATION_REQUIRED')
        if self._submission.logical is not None:
            raise NotSent('CONCURRENT_TERMINAL_OPERATION')
        self._submission.logical = dict(logical)
        try:
            return super().execute(logical)
        finally:
            self._submission.logical = None

    def _qualify(self, logical, request=None):
        from .native_orders import KINDS
        if logical.get('kind') in KINDS:
            if logical['kind']=='PENDING_CREATE':
                if self.mapping is None:raise SafetyError('MAPPING_NOT_CONFIGURED')
                self.mapping.require_open(logical)
            if logical['kind']!='PENDING_CANCEL' and self.clock()>=logical['expires_at']:
                raise SafetyError('ENTRY_EXPIRED')
        return super()._qualify(logical,request)

    def pending_conversion_required(self, logical):
        # Read-only preflight before creating any durable submission intent.
        # Never convert after order_send, a timeout, or a missing check response.
        request=self._prepare(logical)
        checked=self.mt5.order_check(request)
        self.guard()
        if checked is None:raise SafetyError('ORDER_CHECK_UNAVAILABLE')
        return checked.retcode in {10015,10016}

    def sizing_evidence(self, settings, source, target):
        from .member_sizing import RISK, effective_side, effective_prices
        from .demo_policy import decimal
        account = self.guard()
        evidence = {'source_volume':str(source.get('volume',source.get('volume_initial'))),
                    'source_side':source.get('side'), 'source_order_type':source.get('type'),
                    'balance':str(getattr(account,'balance',0)), 'equity':str(getattr(account,'equity',0))}
        if settings['mode_risque'] in RISK:
            side = source.get('side') or ('BUY' if source['type'] in (2,4,6) else 'SELL')
            side = effective_side(settings,side)
            stop = decimal(effective_prices(settings,source)['sl'])
            if 'type' in source:
                price = decimal(source['price'])
            else:
                tick = self.mt5.symbol_info_tick(target)
                if tick is None or not -1 <= self.clock() - (tick.time_msc/1000-self.broker_time_offset) <= self.max_tick_age:
                    raise SafetyError('RISK_QUOTE_UNAVAILABLE')
                price = decimal(tick.ask if side == 'BUY' else tick.bid)
            if stop <= 0 or (stop >= price if side == 'BUY' else stop <= price):
                raise SafetyError('RISK_STOP_LOSS_REQUIRED')
            # Use an executable reference lot even when the broker disallows 1 lot.
            spec = self.spec(target); reference = decimal(spec['volume_min'])
            loss = self.mt5.order_calc_profit(0 if side=='BUY' else 1,target,float(reference),float(price),float(stop))
            if loss is None or decimal(loss) >= 0:raise SafetyError('RISK_SIZING_EVIDENCE_REQUIRED')
            evidence.update(loss_per_lot=str(-decimal(loss)/reference), price=str(price), stop=str(stop))
        after = self.guard()
        if settings['mode_risque'] in RISK and str(after.balance)!=str(account.balance):
            raise SafetyError('RISK_ACCOUNT_CHANGED_DURING_SIZING')
        return evidence

    def unmanaged_orders(self, orders):
        allowed=getattr(self,'native_order_ids',lambda:set())()
        return [row for row in orders if str(row.ticket) not in allowed]

    def personal_position_ids(self, positions):
        """Read native manual-origin hedging positions without adopting them.

        Magic zero alone is insufficient: require the original native position
        reason (desktop/mobile/web), and match the complete current observation.
        Missing provenance and expert-origin positions remain reconciliation errors.
        """
        self.guard()
        rows=self.mt5.positions_get()
        if rows is None:raise SafetyError('POSITIONS_UNAVAILABLE')
        result=set()
        for row in rows:
            pid=str(row.identifier)
            if (row.magic==0 and getattr(row,'reason',None) in (0,1,2)
                and pid in positions and self.position(row)==positions[pid]):
                result.add(pid)
        self.guard()
        return result

    def _prepare(self, logical):
        from .native_orders import KINDS, prepare_native
        if logical.get('kind') in KINDS:
            return prepare_native(self, logical)
        return super()._prepare(logical)

    def native_orders(self):
        from .native_orders import read_native_orders
        return read_native_orders(self)

    def reconcile_native(self, **identity):
        from .native_orders import reconcile_native
        return reconcile_native(self, **identity)

    def _submission_context(self, logical):
        # Applies to CLOSE and MODIFY too, AFTER preflight. A pause received
        # during order_check must prevent the subsequent submission.
        return self._submission.submission()
