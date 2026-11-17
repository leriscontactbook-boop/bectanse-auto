"""Publish actual terminal observations to existing qualification consumers.

Signing authenticates the worker/configuration, not broker compatibility.
Missing native sessions or reviewed asset identities remain explicit denials.
"""
import hashlib
import hmac
import json
import time
from pathlib import Path
from .account_observation import canonical
from .atomic_io import write_json
from .compatibility import digest, number, metadata
from .demo_policy import SafetyError
from .instrument_collection import collect


def reconcile(broker, ledger, owner, clock=time.time):
    positions=broker.positions();orders=broker.native_orders();links=ledger.links(owner)
    expected_positions=set();expected_orders=set();errors=[]
    for row in ledger.db.execute('SELECT value FROM intents WHERE owner=?',(owner,)):
        if json.loads(row[0]).get('state') not in {'CONFIRMED','REJECTED','NOT_SENT'}:
            errors.append('EXECUTION_UNCERTAIN')
    for source_id,link in links.items():
        if link.get('pending'):
            errors.append('EXECUTION_UNCERTAIN');continue
        if link.get('state')=='OPEN':
            pid=str(link.get('position_id'));expected_positions.add(pid);actual=positions.get(pid)
            if not actual or any(actual.get(k)!=link.get(k) for k in ('symbol','magic','side')):
                errors.append('POSITION_IDENTITY_DIVERGENCE');continue
            commands=[json.loads(r[0]) for r in ledger.db.execute(
                'SELECT value FROM intents WHERE owner=? AND source_id=? ORDER BY created_at,id',(owner,source_id))]
            commands=[c for c in commands if c.get('state')=='CONFIRMED']
            native=link.get('native_result',{}).get('order',{})
            volume=link.get('initial_volume');sl=native.get('sl');tp=native.get('tp')
            for command in commands:
                if command['kind']=='OPEN':
                    sl=command['request'].get('sl');tp=command['request'].get('tp')
                elif command['kind']=='CLOSE':volume=command['expected'].get('remaining')
                elif command['kind']=='MODIFY':
                    sl=command['expected'].get('sl');tp=command['expected'].get('tp')
            if any(v is None for v in (volume,sl,tp)):
                errors.append('POSITION_TARGET_UNVERIFIED');continue
            if any(number(actual[k])!=number(v) for k,v in (('volume',volume),('sl',sl),('tp',tp))):
                errors.append('POSITION_VALUES_DIVERGE')
        if link.get('state') in {'NATIVE_PLACED','NATIVE_PARTIALLY_FILLED'}:
            oid=str(link.get('order_id'));expected_orders.add(oid)
            expected=link.get('native_result',{}).get('order');actual=orders.get(oid)
            if not expected or actual!=expected:errors.append('NATIVE_ORDER_DIVERGENCE')
    compensated={str(v.get('position_id')) for v in links.values() if v.get('orphan_order') and v.get('state') in {'CLOSED','CLOSED_LOCAL'}}
    for link in links.values():
        native=link.get('native_result',{})
        if native.get('state')!='PARTIALLY_FILLED':continue
        ids=native.get('position_ids',[]);order=native.get('order',{})
        if len(ids)!=1:errors.append('NATIVE_FILL_IDENTITY_UNVERIFIED');continue
        pid=str(ids[0])
        if pid in expected_positions or (pid in compensated and pid not in positions):continue
        expected_positions.add(pid);actual=positions.get(pid)
        side='BUY' if order.get('type') in (2,4,6) else 'SELL'
        if (not actual or actual.get('symbol')!=order.get('symbol') or actual.get('magic')!=order.get('magic')
            or actual.get('side')!=side or number(actual['volume'])!=number(native['filled_volume'])
            or any(number(actual[k])!=number(order[k]) for k in ('sl','tp'))):
            errors.append('NATIVE_FILL_VALUES_DIVERGE')
    from .external_positions import isolated
    try:external=isolated(broker,positions,links)
    except SafetyError as error:external=set();errors.append(str(error))
    if set(positions)-external!=expected_positions:errors.append('UNTRACKED_OR_MISSING_POSITION')
    if set(orders)!=expected_orders:errors.append('UNTRACKED_OR_MISSING_ORDER')
    if positions!=broker.positions() or orders!=broker.native_orders():errors.append('BROKER_CHANGED_DURING_RECONCILIATION')
    return {'reconciled':not errors,'uncertain':bool(errors),'observed_at':clock(),
            'issues':sorted(set(errors)),'ledger_digest':digest(links),'external_position_ids':sorted(external)}


class RuntimeCollector:
    def __init__(self,data,key,binding,*,clock=time.time,observation_broker=None):
        self.data,self.key,self.binding,self.clock=data,key,binding,clock
        self.observation_broker=observation_broker

    def asset(self,account,symbol):
        envelope=json.loads(Path(self.data['assets']).read_text(encoding='utf-8-sig'))
        if not self.clock()<envelope['review_at']:raise SafetyError('ASSET_REVIEW_REQUIRED')
        if 'accounts' in envelope:return envelope['accounts'][account][symbol]
        if envelope['account']!=account:raise SafetyError('ASSET_ACCOUNT_MISMATCH')
        return envelope['symbols'][symbol]

    def envelope(self,capture,identity,side):
        now=self.clock();start=capture['observed_at']
        # The authenticated source observer refreshes on the same five-second
        # native-evidence window enforced by the execution gate.  Requiring a
        # shorter three-second hand-off made a valid ON activation fail simply
        # when a pool slot read the latest source capture between refreshes.
        if (capture.get('error') or capture.get('source')!='MT5_READ_ONLY'
            or capture.get('identity')!=identity or not 0<=now-start<=5):
            raise SafetyError('INSTRUMENT_CAPTURE_UNAVAILABLE_OR_CHANGED')
        profile=self.data['profile'];account_type=profile[side+'_account_type']
        if capture['broker']!=profile[side+'_broker']:raise SafetyError('BROKER_PROFILE_REVIEW_REQUIRED')
        body={'source':'MT5_READ_ONLY','identity':identity,'binding':self.binding,
              'observed_at':start,'expires_at':start+5,'qualification':{},'catalog':[],
              'sessions':{},'errors':dict(capture.get('errors',{}))}
        for symbol,item in capture['instruments'].items():
            try:
                if item.get('error'):raise SafetyError(item['error'])
                if not account_type:raise SafetyError('ACCOUNT_TYPE_REVIEW_REQUIRED')
                session=item['session'];rec=item['reconciliation']
                observed=dict(broker=capture['broker'],server=identity['account'].split('@',1)[1],
                    account_type=account_type,environment='REAL' if identity['trade_mode']==2 else 'DEMO',
                    observed_at=item['observed_at'],connected=True,terminal_build=capture['terminal']['build'],
                    account=capture['account'],spec=item['spec'],sessions=session['metadata'],
                    session_open=session['open'],session_checked_at=session['observed_at'],
                    quote_at=item['quote_at'],quote_time_msc_raw=item.get('quote_time_msc_raw'),
                    broker_time_offset_seconds=item.get('broker_time_offset_seconds',0),reconciled=rec['reconciled'],uncertain=rec['uncertain'],
                    reconciled_at=rec['observed_at'])
                metadata(observed)
                if number(item['bid'])<=0 or number(item['ask'])<number(item['bid']):raise SafetyError('INVALID_QUOTE')
                body['qualification'][symbol]=observed;body['sessions'][symbol]=session
                login,server=identity['account'].split('@',1)
                try:asset=self.asset(identity['account'],symbol)
                except (OSError,ValueError,KeyError,SafetyError):asset=None
                body['catalog'].append({'spec':item['spec'],
                    'identity':{'account_ref':identity['account'],'identity_hash':digest((login,server,capture['broker'])),
                                'broker':capture['broker'],'server':server},
                    'terminal':capture['terminal'],'asset':asset,'observed_at':item['observed_at'],
                    'quote_at':item['quote_at'],'missing_fields':[k for k,v in item['spec'].items() if v is None],
                    'provenance':'MT5.symbols_get/symbol_info/symbol_info_tick','errors':[]})
            except (KeyError,ValueError,TypeError,SafetyError) as error:
                body['errors'][symbol]=str(error) if isinstance(error,SafetyError) else 'INVALID_INSTRUMENT_EVIDENCE'
        return body

    def write(self,path,body):
        write_json(path,{'body':body,'signature':hmac.new(self.key,canonical(body),hashlib.sha256).hexdigest()})

    def __call__(self,snapshot,engine):
        reasons={};captures={}
        try:
            if not snapshot:raise SafetyError('MASTER_SNAPSHOT_MISSING')
            captures['source']=snapshot['instrument_metadata']
        except (KeyError,SafetyError):reasons['source']='SOURCE_INSTRUMENT_EVIDENCE_MISSING'
        try:
            broker=self.observation_broker or engine.broker
            rec=reconcile(broker,engine.ledger,engine.owner,self.clock)
            # Observe exact source names as candidates only. This does not add mappings
            # or authorize instruments; absent target names remain explicit errors.
            candidates=set(self.data['symbols'].values())
            source_capture=captures.get('source',{})
            # Candidate discovery validates the two native account currencies as
            # well as their contract specifications.  The previous compact rows
            # accidentally dropped the account profile, causing
            # ``valuation_identity`` to reject every otherwise identical pair.
            # These are still read-only observations; this only lets an explicit
            # ON request advance to the normal reviewed-preflight workflow.
            source_rows={name:dict(broker=source_capture.get('broker'),
                                   account=source_capture.get('account',{}),spec=row['spec'])
                         for name,row in source_capture.get('instruments',{}).items() if 'spec' in row}
            candidates.update(set(source_capture.get('instruments',{}))-set(source_rows))
            native_catalog=broker.mt5.symbols_get()
            if native_catalog is None:raise SafetyError('INSTRUMENT_CATALOG_UNAVAILABLE')
            from .symbol_mapping import REQUIRED
            from .initial_instruments import candidates as propose
            target_account=broker.mt5.account_info()
            if target_account is None:raise SafetyError('TARGET_ACCOUNT_UNAVAILABLE')
            target_broker=target_account.company
            target_profile={key:getattr(target_account,key,None) for key in ('currency','margin_mode','trade_mode','leverage')}
            target_rows={row.name:dict(broker=target_broker,account=target_profile,
                spec={k:getattr(row,k,None) for k in REQUIRED}) for row in native_catalog}
            if len(target_rows)!=len(native_catalog):raise SafetyError('INSTRUMENT_CATALOG_AMBIGUOUS')
            for name,row in source_rows.items():
                try:candidates.update(propose({name:row},target_rows,{}).values())
                except ValueError:
                    # Keep a visible absence/error for unresolved instruments;
                    # never silently turn an ambiguous alias into a mapping.
                    candidates.add(name)
            # A newly provisioned terminal may not subscribe to these quotes
            # yet. Market Watch selection is not a trading operation. Restrict
            # it to the observed native catalog and retain any explicit failure.
            for row in native_catalog:
                if row.name in candidates and getattr(row,'select',True) is False:
                    if not broker.mt5.symbol_select(row.name,True):
                        raise SafetyError('INSTRUMENT_SUBSCRIPTION_FAILED')
            captures['target']=collect(broker,candidates,reconciliation=rec,clock=self.clock,
                broker_time_offset_seconds=self.data.get('broker_time_offsets',{}).get('follower',0))
        except Exception:reasons['target']='TARGET_INSTRUMENT_COLLECTION_FAILED'
        for side,account,mode in [('source',engine.source,0),('target',engine.owner,2)]:
            try:
                body=self.envelope(captures[side],{'account':account,'trade_mode':mode},side)
                if side=='target':body['reconciliation']=rec
                # Optional catalog discoveries must not veto healthy installed
                # mappings. Their errors remain signed and each entry is gated.
                required=set(self.data['symbols'] if side=='source' else self.data['symbols'].values())
                if self.data.get('matrix'):
                    matrix=json.loads(Path(self.data['matrix']).read_text(encoding='utf-8-sig'))
                    # Historical mappings remain pinned, but only reviewed pairs
                    # are admitted. Missing pairs still fail the per-order gate.
                    required={row['scope'][side+'_symbol'] for row in matrix.get('records',[])}
                if any(name in body['errors'] or name not in body['qualification'] for name in required):
                    reasons[side]='INSTRUMENT_EVIDENCE_INCOMPLETE'
                if not body['qualification']:reasons[side]='NO_VERIFIED_INSTRUMENT'
                if side=='target' and not rec['reconciled']:reasons[side]='POSITION_RECONCILIATION_REQUIRED'
            except (KeyError,ValueError,SafetyError) as error:
                reasons[side]=str(error) if isinstance(error,SafetyError) else reasons.get(side,'CAPTURE_MISSING')
                body={'source':'MT5_READ_ONLY','identity':{'account':account,'trade_mode':mode},
                      'binding':self.binding,'observed_at':self.clock(),'expires_at':self.clock(),
                      'error':reasons[side],'observed_capture':captures.get(side)}
            self.write(self.data[side+'_evidence'],body)
        return reasons

class RecordedQualification:
    """Retain the exact observations consumed by a successful qualification.

    The resulting signed proof travels in the existing durable intent/result
    report. It grants no admission or operational release on its own.
    """
    def __init__(self, delegate, ledger, key, binding, clock=time.time):
        self.delegate,self.ledger,self.key,self.binding,self.clock=delegate,ledger,key,binding,clock
        self.observations={}
        for side in ('source','target'):
            reader=getattr(delegate,side+'_reader')
            def capture(symbol,reader=reader,side=side):
                value=reader(symbol)
                self.observations[side]=json.loads(json.dumps(value,allow_nan=False))
                return value
            setattr(delegate,side+'_reader',capture)

    def __getattr__(self,name):return getattr(self.delegate,name)

    def require(self,logical,request=None):
        self.observations={}
        self.delegate.require(logical,request)
        if set(self.observations)!={'source','target'}:
            raise SafetyError('QUALIFICATION_OBSERVATIONS_MISSING')
        command=self.ledger.command(logical['operation_id'])
        expected={k:v for k,v in logical.items() if k not in {'operation_id','owner','source'}}
        if command['request']!=expected:raise SafetyError('QUALIFICATION_OPERATION_CHANGED')
        body={'source':'EXECUTION_QUALIFICATION','binding':self.binding,'observed_at':self.clock(),
              'operation_id':command['id'],'request_digest':digest(expected),
              'observations':self.observations,'native_request_digest':digest(request),
              'result':'ALLOWED_BY_REVIEWED_MATRIX'}
        evidence={'body':body,'signature':hmac.new(self.key,canonical(body),hashlib.sha256).hexdigest()}
        with self.ledger.db:
            current=self.ledger.command(command['id'])
            if current['request']!=expected or 'submission_started_at' in current:
                raise SafetyError('QUALIFICATION_OPERATION_ALREADY_SUBMITTED')
            current['qualification_evidence']=evidence
            self.ledger.db.execute('UPDATE intents SET value=? WHERE id=?',(json.dumps(current,allow_nan=False),command['id']))
