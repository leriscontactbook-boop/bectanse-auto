"""Native lab path using the existing observer, order lifecycle and ledger.

No production endpoints or member commands. No synthetic master events. The
source terminal must produce the scenarios; absence of them is NOT_EXECUTED.
"""
import hashlib,json,time
from contextlib import contextmanager
from pathlib import Path
from .demo_mt5 import DemoMT5,NotSent
from .real_engine import RealEngine
from .demo_engine import Observer
from .demo_policy import SafetyError,decimal
from .demo_runtime import DEFAULT_LIMITS,account_identity
from .settings import DEFAULTS
from .compatibility import QualificationGate
from .compatibility_runtime import ExecutionQualification,IncidentJournal,MT5MetadataReader
from .instrument_collection import NativeSessions
from .atomic_io import write_json

class LabBroker(DemoMT5):
    def _qualify(self,logical,request=None):
        from .native_orders import KINDS
        if logical.get('kind') in KINDS:
            if logical['kind']=='PENDING_CREATE':self.mapping.require_open(logical)
            if logical['kind']!='PENDING_CANCEL' and self.clock()>=logical['expires_at']:raise SafetyError('ENTRY_EXPIRED')
        return super()._qualify(logical,request)
    def _prepare(self,logical):
        from .native_orders import KINDS,prepare_native
        if logical.get('kind') in KINDS:return prepare_native(self,logical)
        return super()._prepare(logical)
    def unmanaged_orders(self,orders):
        allowed=getattr(self,'native_order_ids',lambda:set())()
        return [row for row in orders if str(row.ticket) not in allowed]
    @contextmanager
    def _submission_context(self,logical):
        with self.admission.submission():
            # Durable submission boundary, including management and pending orders.
            ledger=self.lab_ledger;command=ledger.command(logical['operation_id'])
            if command.get('submission_started_at') is not None:raise NotSent('LAB_OPERATION_ALREADY_SUBMITTED')
            command['submission_started_at']=self.clock()
            with ledger.db:
                ledger.db.execute('UPDATE intents SET value=? WHERE id=?',(json.dumps(command),command['id']))
                ledger._event(command['id'],'SUBMISSION_STARTED',{'source':'ISOLATED_DEMO'},self.clock())
            yield

class LabEngine(RealEngine):
    adapter_type=LabBroker
    mode='ISOLATED_DEMO'
    magic_namespace='BCT-LAB'

class Authorization:
    def __init__(self,root,identities,plan_digest,clock=time.time):
        self.root,self.identities,self.digest,self.clock=Path(root),identities,plan_digest,clock
    def read(self):
        try:data=json.loads((self.root/'execution-authorization.json').read_text())
        except (OSError,ValueError):raise NotSent('LAB_EXECUTION_AUTHORIZATION_REQUIRED') from None
        if (data.get('environment')!='ISOLATED_DEMO' or data.get('identities')!=self.identities
            or data.get('plan_sha256')!=self.digest or data.get('lot')!='0.01'
            or not data.get('approved_by') or not data.get('issued_at',self.clock()+1)<=self.clock()<data.get('expires_at',0)
            or data.get('state')!='ACTIVE'):
            raise NotSent('LAB_AUTHORIZATION_INVALID_OR_PAUSED')
        return data
    @contextmanager
    def submission(self):
        self.read();yield

class ExplicitMapping:
    """Exact reviewed pair; qualification still validates both native contracts."""
    def __init__(self,symbols,source,target):self.symbols,self.source,self.target=symbols,source,target
    def resolve(self,source,target,symbol,volume):
        if (source,target)!=(self.source,self.target) or decimal(volume)!=decimal('.01'):raise SafetyError('LAB_SCOPE_CHANGED')
        if symbol not in self.symbols:raise SafetyError('SYMBOL_NOT_MAPPED')
        return self.symbols[symbol]
    def require_open(self,logical):
        if self.symbols.get(logical.get('source_symbol'))!=logical.get('symbol'):raise SafetyError('MAPPING_CONFIG_MISMATCH')

class NativeFlow:
    def __init__(self,root,role,broker,ledger,plan,identities,*,clock=time.time):
        self.root,self.role,self.broker,self.ledger,self.clock=Path(root),role,broker,ledger,clock
        self.plan=plan;self.source=account_identity(identities['master']);self.owner=account_identity(identities['follower'])
        encoded=json.dumps(plan,sort_keys=True,separators=(',',':')).encode()
        self.authorization=Authorization(root,identities,hashlib.sha256(encoded).hexdigest(),clock)
        ledger.pin('lab_binding',{'identities':identities,'plan':hashlib.sha256(encoded).hexdigest()})
        self.settings={**DEFAULTS,**plan['settings']}
        if (self.settings['mode_risque']!='Lots fixes' or decimal(self.settings['lots'])!=decimal('.01')
            or decimal(self.settings['lots_max'])!=decimal('.01') or not self.settings['objectif_actif']
            or decimal(self.settings['objectif_perte_pct'])!=decimal('3')):raise SafetyError('LAB_RISK_POLICY_CHANGED')
        self.observer=Observer(ledger,broker,self.source,clock)
        limits={**DEFAULT_LIMITS,'max_lots_per_trade':.01,'max_total_lots':.01,'max_positions':1,'exact_member_volume':True}
        self.engine=None
        if role=='follower':
            self.engine=LabEngine(ledger,broker,source=self.source,owner=self.owner,symbols=plan['symbols'],limits=limits,clock=clock)
            broker.lab_ledger=ledger
            broker.mapping=ExplicitMapping(plan['symbols'],self.source,self.owner)
            broker.admission=self.authorization
    def metadata_reader(self):
        a=self.broker.guard();identity={'master':self.source,'follower':self.owner}[self.role]
        import ntpath
        def reconciliation():
            links=self.ledger.links(self.owner) if self.role=='follower' else {}
            pending=any(x.get('pending') for x in links.values())
            # Native state is inspected; mismatch cannot be asserted away.
            positions=self.broker.positions();orders=self.broker.native_orders()
            known={x.get('position_id') for x in links.values()}
            clean=(not pending and (not positions or set(positions)<=known)) if self.role=='follower' else True
            return dict(reconciled=clean,uncertain=not clean,observed_at=self.clock())
        return MT5MetadataReader(self.broker.mt5,identity=(str(a.login),a.server,a.company),
            account_type=self.plan['profile'][self.role.replace('master','source').replace('follower','target')+'_account_type'],
            terminal_path=ntpath.dirname(self.broker.path),session_reader=NativeSessions(self.broker.mt5),
            reconciliation_reader=reconciliation,clock=self.clock)
    def wire_qualification(self):
        matrix=self.root/self.plan['matrix']
        if matrix.resolve().parent!=self.root.resolve() or matrix.is_symlink():raise SafetyError('LAB_MATRIX_PATH_INVALID')
        gate=QualificationGate(matrix,approved_sha256=self.plan['matrix_sha256'],outbox=self.root/'State/lab-qualification.sqlite',clock=self.clock)
        def source(symbol):
            value=self.ledger.get('lab-source-metadata',{}).get(symbol)
            if not value:raise SafetyError('SOURCE_METADATA_MISSING')
            return value
        self.broker.qualification=ExecutionQualification(gate,profile=self.plan['profile'],source_reader=source,
            target_reader=self.metadata_reader(),incidents=IncidentJournal(self.root/'State/lab-incidents.sqlite'),
            executor_id=self.owner,position_reader=self.broker.positions,order_reader=self.broker.native_orders)
    def cycle(self,financial=None):
        self.broker.guard()
        if self.role=='master':
            snapshot=self.observer.cycle()
            reader=self.metadata_reader()
            self.ledger.put('lab-source-metadata',{s:reader(s) for s in self.plan['symbols']})
            return snapshot
        try:authorization=self.authorization.read();enabled=True
        except NotSent:authorization={'expires_at':self.clock(),'revision':0};enabled=False
        self.engine.objective_observation=financial or {}
        config={'enabled':enabled,'valid_until':min(self.clock()+1,authorization['expires_at']),
                'version':1,'revision':authorization.get('revision',0),'settings':self.settings}
        report=self.engine.cycle(config)
        # Append actual ledger states/acks; no automatic PASSED conclusion.
        operations=[json.loads(r[0]) for r in self.ledger.db.execute('SELECT value FROM intents ORDER BY created_at')]
        result=dict(at=self.clock(),source='MT5_NATIVE',role=self.role,report=report,operations=operations,
                    positions=self.broker.positions(),orders=self.broker.native_orders(),native_qualified=False,
                    authorization_enabled=enabled,authorization_revision=authorization.get('revision',0))
        write_json(self.root/'State/lab-execution-status.json',result)
        return result

def conclusion(root):
    """Verify collector journals and require observed coverage, never invented passes."""
    import sqlite3
    from .native_evidence_journal import Journal
    root=Path(root);streams={};integrity={};bindings={}
    for role in ('master','follower'):
        j=Journal.__new__(Journal);j.path=root/'Evidence'/('execution-'+role+'.sqlite')
        j.anchor=j.path.with_suffix('.head.json');j.key=(root/'Secrets/credential.key').read_bytes()
        j.db=sqlite3.connect('file:'+j.path.as_posix()+'?mode=ro',uri=True)
        try:
            integrity[role]=j.verify()
            bindings[role]=json.loads(j.db.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()[0])
            streams[role]=[json.loads(r[0]) for r in j.db.execute('SELECT body FROM records ORDER BY seq')]
        finally:j.db.close()
    with sqlite3.connect('file:'+(root/'State/lab-execution.sqlite').as_posix()+'?mode=ro',uri=True) as db:
        operations=[json.loads(r[0]) for r in db.execute('SELECT value FROM intents')]
        release=json.loads(db.execute("SELECT value FROM state WHERE key='lab_release'").fetchone()[0])
        executor=json.loads(db.execute("SELECT value FROM state WHERE key='lab_executor'").fetchone()[0])
        if any(b.get('release')!=release or b.get('executor')!=executor for b in bindings.values()):raise SafetyError('NATIVE_RELEASE_BINDING_CHANGED')
        events=list(db.execute('SELECT intent_id,phase,at FROM intent_events ORDER BY seq'))
        links=[json.loads(r[0]) for r in db.execute("SELECT value FROM state WHERE key LIKE 'links:%'")]
    uncertain=[o['id'] for o in operations if o['state'] in {'SENDING','UNKNOWN','ACCEPTED'}]
    confirmed=[o for o in operations if o['state']=='CONFIRMED' and o.get('broker_result')]
    kinds={o['kind'] for o in confirmed}
    observations=[e for e in streams['follower'] if e['kind']=='NATIVE_PIPELINE_OBSERVATION']
    pause_proven=False
    for before,after in zip(observations,observations[1:]):
        a,b=before['payload'],after['payload']
        if not a['authorization_enabled'] and not b['authorization_enabled'] and after['at']-before['at']>=.1:
            pause_proven=pause_proven or (len(a['operations'])==len(b['operations']) and not b['report']['pending_commands'])
    recovered={i for i,p,_ in events if p=='UNCERTAIN'} & {o['id'] for o in confirmed}
    starts=[e['at'] for e in streams['follower'] if e['kind']=='START']
    restarted=any(any(i==j and phase=='CONFIRMED' and end>=start for j,phase,end in events)
                  for start in starts[1:] for i,p,at in events if p=='UNCERTAIN' and at<start)
    expiry_observed=any(link.get('native_result',{}).get('state')=='EXPIRED' for group in links for link in group.values())
    checks={
        'master_event':any(e['kind']=='NATIVE_PIPELINE_OBSERVATION' and e['payload'].get('positions') for e in streams['master']),
        'native_ack':{'OPEN','MODIFY','CLOSE'}<=kinds,
        'compatibility':bool(confirmed), # Every send is gated by ExecutionQualification.
        'authorization':bool(confirmed),
        'reconciliation':bool(confirmed) and not uncertain and bool(observations) and not observations[-1]['payload']['positions'] and not observations[-1]['payload']['orders'] and not observations[-1]['payload']['report']['errors'],
        'expiry':expiry_observed,
        'uncertain_result':bool(recovered),
        'restart':restarted and bool(recovered),
        'pause':pause_proven,
    }
    complete=all(checks.values())
    return dict(source='MT5_NATIVE_LEDGER_AND_COLLECTOR_JOURNAL',native_qualified=complete,
                state='NATIVE_SCENARIOS_OBSERVED_REVIEW_REQUIRED' if complete else 'NATIVE_SCENARIOS_INCOMPLETE',
                checks={k:{'status':'PASSED' if v else 'NOT_EXECUTED','source':'MT5_NATIVE'} for k,v in checks.items()},
                uncertain_operations=uncertain,not_executed=[k for k,v in checks.items() if not v],
                operations=len(operations),integrity=integrity,executor_digest=executor,bindings=bindings,real_authorization_created=False)
