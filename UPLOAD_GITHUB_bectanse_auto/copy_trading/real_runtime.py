"""Concrete Windows runtime composition; no implicit broker qualification.

Administrator-installed manifest references reviewed artifacts. Mutable evidence
must be signed by the collector, bound to an account and fresh. Missing evidence
is a denial, never a synthesized session schedule or reconciliation result.
"""
import hashlib
import hmac
import json
import ntpath
import time
from pathlib import Path
from .account_observation import canonical
from .compatibility import NativePreflightQualificationGate
from .compatibility_runtime import ExecutionQualification,IncidentJournal,MT5MetadataReader
from .demo_policy import SafetyError
from .demo_runtime import DEFAULT_LIMITS
from .real_execution_worker import PreparedExecutor
from .symbol_mapping import SymbolDiscovery,SymbolMappingRegistry,MappingAdmission,BrokerQualificationGate


class EvidenceFile:
    def __init__(self,path,key,identity,clock=time.time,binding=None):
        self.path,self.key,self.identity,self.clock=Path(path),key,identity,clock
        self.binding=binding
    def read(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8-sig'))
            body=data['body']
            if self.binding is not None and body.get('binding')!=self.binding:raise ValueError()
            if body.get('error'):raise ValueError()
            if not hmac.compare_digest(data['signature'],hmac.new(self.key,canonical(body),hashlib.sha256).hexdigest()):
                raise ValueError()
            now=self.clock()
            if (body['identity']!=self.identity or body['source']!='MT5_READ_ONLY'
                or not body['observed_at']<=now<body['expires_at']
                or body['expires_at']-body['observed_at']>5):raise ValueError()
            return body
        except Exception:raise SafetyError('RUNTIME_EVIDENCE_MISSING_OR_INVALID') from None


class RealSymbolDiscovery(SymbolDiscovery):
    """Explicit real-account variant; the existing demo discovery is unchanged."""
    def _identity(self):
        from .compatibility import digest
        a,t=self.mt5.account_info(),self.mt5.terminal_info()
        if a is None or t is None or not t.connected:raise SafetyError('DISCOVERY_TERMINAL_UNAVAILABLE')
        if ((str(a.login),a.server,a.company)!=self.expected
            or ntpath.normcase(t.path)!=ntpath.normcase(self.path)):
            raise SafetyError('DISCOVERY_IDENTITY_CHANGED')
        if a.trade_mode!=2:raise SafetyError('REAL_ACCOUNT_REQUIRED')
        return {'account_ref':self.ref,'identity_hash':digest(self.expected),'broker':a.company,'server':a.server},{
            'path_hash':digest(self.path),'build':t.build,'api_version':getattr(self.mt5,'__version__',None)}


class RuntimeBuilder:
    def __init__(self,manifest,*,clock=time.time):
        self.path=Path(manifest);self.clock=clock
    def load(self,plan):
        data=json.loads(self.path.read_text(encoding='utf-8-sig'))
        required={'schema','identity','master','source_ledger','matrix','matrix_sha256','mapping_registry',
                  'mapping_head','profile','source_evidence','target_evidence','assets','symbols','limits'}
        if set(data)-{'broker_time_offsets','external_positions'}!=required or data['schema']!=1:raise ValueError('INVALID_EXECUTOR_MANIFEST')
        from .broker_clock import offset_seconds
        offsets=data.get('broker_time_offsets',{'master':0,'follower':0})
        if not isinstance(offsets,dict) or set(offsets)!={'master','follower'}:raise ValueError('INVALID_BROKER_TIME_OFFSETS')
        for value in offsets.values():offset_seconds(value)
        if data['identity']!={k:plan['identity'][k] for k in ('member_code','account_id','login','server')}:
            raise ValueError('EXECUTOR_ACCOUNT_BINDING_CHANGED')
        if data['master']!={k:plan['master'][k] for k in ('login','server')}:
            raise ValueError('EXECUTOR_MASTER_BINDING_CHANGED')
        p=data['profile']
        if (p.get('environment')!='DEMO_TO_REAL' or p.get('source_server')!=data['master']['server']
            or p.get('target_server')!=data['identity']['server']):raise ValueError('EXECUTOR_SCOPE_CHANGED')
        for key in ('source_ledger','matrix','mapping_registry','source_evidence','target_evidence','assets'):
            if not (ntpath.isabs(data[key]) or Path(data[key]).is_absolute()):raise ValueError('ABSOLUTE_RUNTIME_PATH_REQUIRED')
        if set(data['limits'])!=set(DEFAULT_LIMITS):raise ValueError('EXPLICIT_LIMITS_REQUIRED')
        # Real execution never inherits the historical demo minimum-lot exception.
        if data['limits']['exact_member_volume'] is not True or data['limits']['allow_broker_minimum'] is not False:
            raise ValueError('EXACT_MEMBER_VOLUME_REQUIRED')
        if not isinstance(data['symbols'],dict):raise ValueError('EXPLICIT_SYMBOL_MAPPING_REQUIRED')
        # Empty means no instrument is admitted. It permits a STOPPED service
        # to collect/reconcile while qualification is still pending.
        if hashlib.sha256(Path(data['matrix']).read_bytes()).hexdigest()!=data['matrix_sha256']:
            raise ValueError('QUALIFICATION_ARTIFACT_CHANGED')
        return data
    def __call__(self,session,backend,plan,state):
        data=self.load(plan);state=Path(state);profile=data['profile']
        source=str(data['master']['login'])+'@'+data['master']['server']
        target=session.identity['login']+'@'+session.identity['server']
        key=hmac.new(backend.secret.encode(),b'BECTANSE_RUNTIME_EVIDENCE_V1',hashlib.sha256).digest()
        from .runtime_collection import RuntimeCollector, RecordedQualification
        from .compatibility import digest
        binding={'manifest':digest(data),'account_version':plan['identity']['version']}
        session.broker.external_position_inventory=data.get('external_positions')
        collector=RuntimeCollector(data,key,binding,clock=self.clock,observation_broker=session.broker)
        src=EvidenceFile(data['source_evidence'],key,{'account':source,'trade_mode':0},self.clock,binding)
        dst=EvidenceFile(data['target_evidence'],key,{'account':target,'trade_mode':2},self.clock,binding)
        def source_observer(symbol):return src.read()['qualification'][symbol]
        def target_session(symbol):return dst.read()['sessions'][symbol]
        def target_reconciliation():return dst.read()['reconciliation']
        def asset(symbol):return collector.asset(target,symbol)
        path=ntpath.dirname(session.identity['terminal'])
        observer=MT5MetadataReader(session.module,identity=(session.identity['login'],session.identity['server'],profile['target_broker']),
            account_type=profile['target_account_type'],terminal_path=path,session_reader=target_session,
            reconciliation_reader=target_reconciliation,expected_trade_mode=2,clock=self.clock,
            broker_time_offset_seconds=data.get('broker_time_offsets',{}).get('follower',0))
        discovery=RealSymbolDiscovery(session.module,expected_identity=(session.identity['login'],session.identity['server'],profile['target_broker']),
            terminal_path=path,account_ref=target,asset_reader=asset,clock=self.clock,
            broker_time_offset_seconds=data.get('broker_time_offsets',{}).get('follower',0))
        incidents=IncidentJournal(state/'incidents.sqlite',clock=self.clock)
        gate=NativePreflightQualificationGate(data['matrix'],approved_sha256=data['matrix_sha256'],outbox=state/'qualification.sqlite',clock=self.clock,
            evidence_key=key,expected_accounts={'source':source,'target':target})
        mapping_gate=BrokerQualificationGate(gate,profile,source_observer,observer)
        mapping=MappingAdmission(SymbolMappingRegistry(data['mapping_registry']),head_reader=lambda:data['mapping_head'],
            source_account=source,target_account=target,source_catalog=lambda:src.read()['catalog'],target_catalog=discovery.collect,
            qualification=mapping_gate,incidents=incidents,clock=self.clock)
        from .native_preflight_review import POLICY
        matrix_rows=json.loads(Path(data['matrix']).read_text(encoding='utf-8-sig')).get('records',[])
        if matrix_rows and all(row.get('review_policy')==POLICY for row in matrix_rows):
            from .initial_instruments import ReviewedMapping
            mapping=ReviewedMapping(data['symbols'],source,target,gate,profile,source_observer,observer)
        qualification=ExecutionQualification(gate,profile=profile,source_reader=source_observer,target_reader=observer,
            incidents=incidents,executor_id=target,position_reader=session.broker.positions,order_reader=session.broker.native_orders)
        from .qualification_version import executor_digest,authorization_digest
        worker=PreparedExecutor(session,backend,master=data['master'],source_path=data['source_ledger'],
            state_path=state/'operations.sqlite',symbols=data['symbols'],limits=data['limits'],
            broker_time_offset_seconds=data.get('broker_time_offsets',{}).get('follower',0),
            qualification=qualification,mapping=mapping,qualification_digest=authorization_digest(data['matrix_sha256'],executor_digest()))
        worker.cycle.engine.broker.external_position_inventory=data.get('external_positions')
        worker.cycle.engine.broker.qualification=RecordedQualification(
            qualification,worker.ledger,key,binding,clock=self.clock)
        from .preflight_delivery import PreflightDelivery,CollectorDelivery
        delivery=PreflightDelivery(backend,worker.cycle.authority.binding,state/'preflight-delivery',clock=self.clock)
        worker.cycle.evidence_collector=CollectorDelivery(collector,delivery,data,plan['identity'],executor_digest(),clock=self.clock,manifest_path=self.path)
        return worker


class RuntimeDirectory:
    """Select an operator-installed account manifest; never infer qualification.

    A stable account key preserves the journal across configuration revisions.
    The existing RuntimeBuilder still verifies the full member/master binding.
    """
    def __init__(self, directory, *, clock=time.time, bootstrap_policy=None):
        self.directory = Path(directory).resolve()
        self.clock = clock
        self.bootstrap_policy = bootstrap_policy

    @staticmethod
    def key(identity):
        member, account = identity['member_code'], identity['account_id']
        if not isinstance(member, str) or not member or type(account) is not int or account < 1:
            raise ValueError('EXECUTOR_ACCOUNT_BINDING_CHANGED')
        return hashlib.sha256((member + '|' + str(account)).encode()).hexdigest()

    def select(self, plan):
        path = self.directory / (self.key(plan['identity']) + '.json')
        if path.is_symlink() or not path.is_file():
            raise ValueError('REVIEWED_ACCOUNT_MANIFEST_REQUIRED')
        builder = RuntimeBuilder(path, clock=self.clock)
        builder.load(plan)
        return builder

    def __call__(self, session, backend, plan, state):
        if self.bootstrap_policy is not None:
            from .account_bootstrap import prepare
            prepare(self.directory / (self.key(plan['identity']) + '.json'),
                    self.bootstrap_policy, session, plan, state, clock=self.clock)
        return self.select(plan)(session, backend, plan, state)
