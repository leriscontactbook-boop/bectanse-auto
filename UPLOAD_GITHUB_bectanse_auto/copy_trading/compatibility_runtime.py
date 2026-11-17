"""Local execution wiring. Providers are explicit, never discovered from credentials."""
import copy
import sqlite3
import time
import uuid
from .compatibility import digest, metadata, number, observe_mt5
from .demo_policy import SafetyError


class IncidentJournal:
    """Persistent deduplication; transport must deduplicate event_id on retries.

    No exactly-once network claim: a lost acknowledgement retries the same ID.
    Resolution requires a fresh successful qualification of the same operation.
    """
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS incidents(
              scope TEXT PRIMARY KEY, id TEXT, reason TEXT, active INTEGER);
              CREATE TABLE IF NOT EXISTS notifications(
              id TEXT PRIMARY KEY, incident TEXT, scope TEXT, kind TEXT, reason TEXT,
              at REAL, delivered INTEGER DEFAULT 0);''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def record(self, scope, reason):
        key = digest(scope)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT id,reason,active FROM incidents WHERE scope=?', (key,)).fetchone()
            if reason is None:
                if not previous or not previous[2]:
                    return
                incident, kind = previous[0], 'RESOLVED_BY_FRESH_QUALIFICATION'
                db.execute('UPDATE incidents SET active=0 WHERE scope=?', (key,))
            else:
                if previous and previous[2] and previous[1] == reason:
                    return
                incident, kind = (previous[0] if previous and previous[2] else uuid.uuid4().hex), 'BLOCKED'
                db.execute('INSERT OR REPLACE INTO incidents VALUES(?,?,?,1)', (key, incident, reason))
            db.execute('INSERT INTO notifications VALUES(?,?,?,?,?,?,0)',
                       (uuid.uuid4().hex, incident, key, kind, reason or 'VERIFIED', self.clock()))

    def dispatch(self, transport):
        # Caller supplies an idempotent transport; no endpoint or secret in this module.
        with self.connect() as db:
            rows = db.execute('SELECT id,incident,scope,kind,reason,at FROM notifications WHERE delivered=0 ORDER BY rowid').fetchall()
            for row in rows:
                payload = dict(zip(('event_id','correlation_id','scope_hash','kind','reason','at'), row))
                if transport.send(payload) is not True:
                    break
                db.execute('UPDATE notifications SET delivered=1 WHERE id=?', (row[0],))
                db.commit()


class MT5MetadataReader:
    """Only reads an already attached MT5 module; session/reconciliation suppliers mandatory."""
    def __init__(self, module, *, identity, account_type, terminal_path,
                 session_reader, reconciliation_reader, clock=time.time, expected_trade_mode=0, broker_time_offset_seconds=0):
        from .broker_clock import offset_seconds
        self.broker_time_offset=offset_seconds(broker_time_offset_seconds)
        self.expected_trade_mode = expected_trade_mode
        self.module, self.identity, self.account_type = module, identity, account_type
        self.path, self.sessions, self.reconciliation, self.clock = terminal_path, session_reader, reconciliation_reader, clock

    def __call__(self, symbol):
        import ntpath
        before = self.module.terminal_info()
        if before is None or not before.connected:
            raise SafetyError('TERMINAL_DISCONNECTED')
        if ntpath.normcase(ntpath.normpath(before.path)) != ntpath.normcase(ntpath.normpath(self.path)):
            raise SafetyError('TERMINAL_IDENTITY_CHANGED')
        result = observe_mt5(self.module, symbol, expected_identity=self.identity,
            account_type=self.account_type, session_reader=self.sessions,
            reconciliation_reader=self.reconciliation, clock=self.clock, expected_trade_mode=self.expected_trade_mode, broker_time_offset_seconds=self.broker_time_offset)
        after = self.module.terminal_info()
        if after is None or not after.connected or after.path != before.path or after.build != before.build:
            raise SafetyError('TERMINAL_IDENTITY_CHANGED')
        metadata(result)  # Reject incomplete/contradictory required fields now.
        result['provenance'] = {'interface':'MT5_READ_ONLY', 'terminal_identity':digest([self.path,self.identity]),
                                'collected_at':result['observed_at'], 'clock':'UTC_REQUIRED'}
        result['fresh'] = 0 <= self.clock()-result['observed_at'] <= 5
        if not result['fresh']:
            raise SafetyError('METADATA_OR_EVIDENCE_STALE')
        return result


class ExecutionQualification:
    """One trusted profile, explicit mapping and real gate, consulted for every execution."""
    def __init__(self, gate, *, profile, source_reader, target_reader, incidents, executor_id, position_reader=None, order_reader=None):
        self.order_reader = order_reader
        self.position_reader = position_reader
        self.executor_hash = digest(executor_id)
        self.gate, self.profile = gate, copy.deepcopy(profile)
        self.source_reader, self.target_reader, self.incidents = source_reader, target_reader, incidents

    def require(self, logical, request=None):
        scope = dict(self.profile)
        scope.update(source_symbol=logical.get('source_symbol',''), target_symbol=logical.get('symbol',''))
        incident_scope = self._incident_scope(scope, logical)
        reason = None
        try:
            # Management requires separate semantics/evidence. No implicit OPEN qualification reuse.
            kind = logical.get('kind')
            if kind in {'PENDING_CREATE','PENDING_MODIFY','PENDING_CANCEL'}:
                reason=self._native(scope,logical,request)
            elif kind != 'OPEN' and (kind not in {'MODIFY','CLOSE'} or self.position_reader is None):
                reason = 'MANAGEMENT_QUALIFICATION_REQUIRED'
            elif kind != 'OPEN':
                position = self.position_reader().get(str(logical.get('position_id')))
                if (not position or any(position.get(k) != logical.get(k) for k in ('symbol','magic','side'))):
                    reason = 'MANAGEMENT_POSITION_IDENTITY_CHANGED'
                else:
                    current_volume = number(position['volume'])
                    volume = current_volume if kind == 'MODIFY' else number(logical.get('volume'))
                    if volume <= 0 or volume > current_volume:
                        reason = 'MANAGEMENT_VOLUME_INVALID'
                    else:
                        # Evaluate the explicit management scope, not an OPEN record.
                        observed = self.target_reader(scope['target_symbol'])
                        flags = observed['spec']['filling_mode']
                        filling = 'FOK' if flags & 1 else 'IOC' if flags & 2 else 'UNSUPPORTED'
                        if request is not None and (request.get('symbol') != position['symbol']
                            or request.get('position') != position['ticket']
                            or (kind == 'CLOSE' and (number(request.get('volume')) != volume
                                or request.get('type_filling') != {'FOK':0,'IOC':1}.get(filling)))):
                            reason = 'EXECUTION_RULE_MISMATCH'
                        else:
                            result = self.gate.evaluate(scope,
                                source_reader=lambda:self.source_reader(scope['source_symbol']),
                                target_reader=lambda:observed, volume=volume, filling=filling,
                                validity='GTC', operation=kind)
                            if result.get('allowed') is not True:reason = result['reason']
            elif number(logical.get('requested_volume', logical['volume'])) != number(logical['volume']):
                reason = 'VOLUME_TRANSFORMATION_FORBIDDEN'
            else:
                observed = self.target_reader(scope['target_symbol'])
                flags = observed['spec']['filling_mode']
                filling = 'FOK' if flags & 1 else 'IOC' if flags & 2 else 'UNSUPPORTED'
                if request is not None and (request.get('type_filling') != {'FOK':0,'IOC':1}.get(filling)
                        or request.get('type_time') != 0
                        or number(request.get('volume')) != number(logical['volume'])
                        or request.get('symbol') != scope['target_symbol']):
                    reason = 'EXECUTION_RULE_MISMATCH'
                else:
                    result = self.gate.evaluate(scope,
                        source_reader=lambda:self.source_reader(scope['source_symbol']),
                        target_reader=lambda:observed, volume=logical['volume'], filling=filling, validity='GTC')
                    if result.get('allowed') is not True:
                        reason = result['reason']
        except Exception as exc:
            # Whitelist only stable categories; never relay arbitrary provider text.
            known = {'TERMINAL_DISCONNECTED', 'TERMINAL_IDENTITY_CHANGED',
                     'ACCOUNT_IDENTITY_CHANGED', 'SYMBOL_ABSENT', 'METADATA_OR_EVIDENCE_STALE'}
            reason = str(exc) if isinstance(exc, (SafetyError, ValueError)) and str(exc) in known else 'COLLECTION_FAILED'
        self.incidents.record(incident_scope, reason)
        if reason:
            raise SafetyError('COMPATIBILITY_' + reason)

    def _native(self, scope, logical, request):
        kind=logical['kind'];volume=number(logical['volume'])
        if kind=='PENDING_CREATE':
            if number(logical.get('requested_volume',volume))!=volume:return 'VOLUME_TRANSFORMATION_FORBIDDEN'
        else:
            if self.order_reader is None:return 'NATIVE_ORDER_EVIDENCE_REQUIRED'
            row=self.order_reader().get(str(logical.get('order_id')))
            if not row or any(row[k]!=logical.get(v) for k,v in [('symbol','symbol'),('magic','magic'),('type','order_type')]):
                return 'NATIVE_ORDER_IDENTITY_CHANGED'
            if request is not None and request.get('order')!=int(row['ticket']):return 'EXECUTION_RULE_MISMATCH'
        validity={0:'GTC',1:'DAY',2:'SPECIFIED',3:'SPECIFIED_DAY'}.get(logical.get('type_time',0),'INVALID')
        if request is not None:
            expected={'PENDING_CREATE':5,'PENDING_MODIFY':7,'PENDING_CANCEL':8}[kind]
            if request.get('action')!=expected or request.get('symbol')!=scope['target_symbol']:return 'EXECUTION_RULE_MISMATCH'
            if kind=='PENDING_CREATE' and (number(request.get('volume'))!=volume or request.get('type_filling')!=2):return 'EXECUTION_RULE_MISMATCH'
        result=self.gate.evaluate(scope,source_reader=lambda:self.source_reader(scope['source_symbol']),
            target_reader=lambda:self.target_reader(scope['target_symbol']),volume=volume,
            filling='RETURN',validity=validity,operation=kind,order_type=logical.get('order_type'))
        return None if result.get('allowed') is True else result['reason']

    def uncertain(self, logical):
        scope = dict(self.profile)
        scope.update(source_symbol=logical.get('source_symbol',''), target_symbol=logical.get('symbol',''), operation=logical.get('kind',''))
        self.incidents.record(self._incident_scope(scope, logical), 'EXECUTION_UNCERTAIN')

    def _incident_scope(self, scope, logical):
        return {**scope, 'executor':self.executor_hash, 'operation':logical.get('kind',''),
                'operation_identity':logical.get('magic'),
                'requested_volume':str(logical.get('requested_volume', logical.get('volume','')))}
