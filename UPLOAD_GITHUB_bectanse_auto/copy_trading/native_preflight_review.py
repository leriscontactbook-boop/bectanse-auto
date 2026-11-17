"""Explicit real-account preflight review, never an execution qualification.

Uses the existing collector's signed, account/configuration-bound observations.
An immutable review may be approved by an administrator; it never activates a
member. Live execution must still recheck metadata, consent and financial risk.
"""
import hashlib
import hmac
import json
import re
from pathlib import Path
from .account_observation import canonical
from .compatibility import metadata, digest, timestamp, number

SCHEMA = 'BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1'
POLICY = 'NATIVE_PREFLIGHT_ADMIN_AND_MEMBER_V1'


def artifact(folder, reference):
    name=reference.get('artifact','')
    path=Path(folder)/name
    if (not name or Path(name).name!=name or path.is_symlink() or not path.is_file()
        or path.stat().st_size>2_000_000):
        raise ValueError('PREFLIGHT_ARTIFACT_MISSING')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference.get('sha256'):
        raise ValueError('PREFLIGHT_ARTIFACT_CHANGED')
    return raw


def validate_review(data, folder, *, now, secrets=None):
    """Verify archived captures at capture time; current availability is separate.

    The authenticated collector key is obtained from existing node registration,
    never from the evidence document. No order ACK or tested reconnection is
    inferred from this read-only review.
    """
    if (data.get('schema')!=SCHEMA or data.get('policy')!=POLICY
        or data.get('native_qualified') is not False
        or data.get('execution_validation')!='NOT_OBSERVED'
        or data.get('environment')!='DEMO_TO_REAL'):
        raise ValueError('INVALID_PREFLIGHT_POLICY')
    captured=timestamp(data['observed_at']);expires=timestamp(data['expires_at'])
    if not captured<=now<expires or expires-captured>86400:
        raise ValueError('PREFLIGHT_REVIEW_EXPIRED')
    for k in ('configuration_digest','executor_digest','qualification_digest','manifest_digest'):
        if not re.fullmatch('[a-f0-9]{64}',str(data.get(k,''))):
            raise ValueError('EVIDENCE_BINDING_REQUIRED')
    target=data['target_review'];identity=target['identity']
    if (target.get('status')!='REVIEWED_NATIVE_READ_ONLY'
        or identity.get('member_code')!=data.get('member_code')
        or identity.get('account_id')!=data.get('account_id')
        or identity.get('trade_mode')!=2 or type(identity.get('version')) is not int
        or identity['version']<1 or not target.get('observed_at',now+1)<=now<target.get('expires_at',0)):
        raise ValueError('TARGET_REVIEW_STALE_OR_CHANGED')
    if secrets is None:
        from .agent_backend import node_secrets
        secrets=node_secrets()
        import os
        node=os.environ.get('COPY_REAL_PREPARATION_NODE','');secret=os.environ.get('COPY_REAL_PREPARATION_SECRET','')
        if node and len(secret)>=32:secrets={**secrets,node:secret}
    secret=secrets.get(data.get('worker_id'),'')
    if not isinstance(secret,str) or len(secret)<32:
        raise ValueError('REGISTERED_COLLECTOR_REQUIRED')
    key=hmac.new(secret.encode(),b'BECTANSE_RUNTIME_EVIDENCE_V1',hashlib.sha256).digest()
    binding={'manifest':data['manifest_digest'],'account_version':identity['version']}
    observations={}
    for side,mode,account in [('source',0,data['master']),('target',2,identity)]:
        envelope=json.loads(artifact(folder,data['captures'][side]));body=envelope['body']
        if not hmac.compare_digest(str(envelope.get('signature','')),hmac.new(key,canonical(body),hashlib.sha256).hexdigest()):
            raise ValueError('PREFLIGHT_SIGNATURE_INVALID')
        start=timestamp(body['observed_at']);end=timestamp(body['expires_at'])
        if (body.get('source')!='MT5_READ_ONLY' or body.get('binding')!=binding
            or body.get('identity')!={'account':str(account['login'])+'@'+account['server'],'trade_mode':mode}
            or body.get('error') or not start<=captured<end
            or not 0<end-start<=5):
            raise ValueError('PREFLIGHT_CAPTURE_INVALID')
        observations[side]=body['qualification']
        # Unrelated catalog failures do not invalidate a verified mapped pair.
        for symbol in body.get('errors',{}):observations[side].pop(symbol,None)
    matrix_raw=artifact(folder,data['matrix'])
    if hashlib.sha256(matrix_raw).hexdigest()!=data['qualification_digest']:
        raise ValueError('PREFLIGHT_MATRIX_CHANGED')
    matrix=json.loads(matrix_raw)
    if matrix.get('schema_version')!=1 or not matrix.get('records'):
        raise ValueError('PREFLIGHT_INSTRUMENTS_REQUIRED')
    seen=set()
    for row in matrix['records']:
        if (row.get('review_policy')!=POLICY or row.get('execution_validation')!='NOT_OBSERVED'
            or row.get('tests')!={} or row.get('captures')!=data['captures']
            or row.get('status')!='QUALIFIED_LIMITED' or row.get('checked_at')!=captured
            or not row.get('reviewer') or row.get('expires_at',0)<expires):
            raise ValueError('PREFLIGHT_MATRIX_POLICY_CHANGED')
        scope=row['scope'];pair=(scope['source_symbol'],scope['target_symbol'])
        if pair in seen:raise ValueError('AMBIGUOUS_QUALIFICATION')
        seen.add(pair)
        if scope.get('environment')!='DEMO_TO_REAL':raise ValueError('PREFLIGHT_SCOPE_CHANGED')
        specs={}
        for side,symbol in zip(('source','target'),pair):
            observation=observations[side][symbol]
            if (observation.get('connected') is not True or observation.get('reconciled') is not True
                or observation.get('uncertain') is not False or observation.get('session_open') is not True
                or observation['account']['trade_mode']!=(0 if side=='source' else 2)
                or any(observation[k]!=scope[side+'_'+k] for k in ('broker','server','account_type'))):
                raise ValueError('PREFLIGHT_NATIVE_CHECK_FAILED')
            for k in ('observed_at','quote_at','session_checked_at','reconciled_at'):
                if not 0<=captured-timestamp(observation[k])<=5:raise ValueError('PREFLIGHT_CAPTURE_STALE')
            m=metadata(observation)
            if (m['spec']['name']!=symbol or digest(m)!=row['metadata_sha256'][side]
                or m!=row['metadata'][side]):raise ValueError('PREFLIGHT_METADATA_CHANGED')
            specs[side]=m['spec']
        for k in ('trade_contract_size','trade_tick_size','currency_profit','currency_base','trade_calc_mode'):
            if specs['source'][k]!=specs['target'][k]:raise ValueError('CONTRACT_EQUIVALENCE_NOT_PROVEN')
        volume=number(data['volume'][scope['source_symbol']] if isinstance(data['volume'],dict) else data['volume'])
        if number(row['restrictions']['max_volume'])!=volume:raise ValueError('PREFLIGHT_VOLUME_INVALID')
        spec=specs['target']
        if volume<=0 or not number(spec['volume_min'])<=volume<=number(spec['volume_max']) or volume%number(spec['volume_step']):
            raise ValueError('PREFLIGHT_VOLUME_INVALID')
    if isinstance(data['volume'],dict) and set(data['volume'])!={pair[0] for pair in seen}:raise ValueError('PREFLIGHT_VOLUME_INVALID')
    return data


def build_review(destination, manifest, *, binding, identity, worker_id, secret,
                 volume, reviewer, restrictions, expires_at, now, proposed_symbols=None):
    """Package fresh native captures for the EXISTING authenticated admin route.

    No publication, approval, mapping inference, member activation or order.
    The explicit symbol mapping and operation restrictions are reviewed inputs.
    """
    folder=Path(destination)
    symbols = manifest.get('symbols') if proposed_symbols is None else proposed_symbols
    if not symbols or not reviewer:raise ValueError('REVIEWED_INSTRUMENT_MAPPING_REQUIRED')
    if any(k in manifest.get('symbols',{}) and manifest['symbols'][k]!=v for k,v in symbols.items()):
        raise ValueError('EXISTING_MAPPING_CHANGE_REQUIRES_REVIEW')
    if set(binding)!={'member_code','account_id','configuration_digest','executor_digest'}:
        raise ValueError('REVIEWED_BINDING_REQUIRED')
    if manifest['identity']!={k:identity[k] for k in ('member_code','account_id','login','server')}:
        raise ValueError('PREFLIGHT_ACCOUNT_CHANGED')
    if (set(restrictions)!={'operations','filling_modes','validity_modes','max_volume'}
        or (not isinstance(volume,dict) and number(restrictions['max_volume'])!=number(volume))):
        raise ValueError('EXPLICIT_PREFLIGHT_RESTRICTIONS_REQUIRED')
    folder.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        raw=canonical(value);(folder/name).write_bytes(raw)
        return {'artifact':name,'sha256':hashlib.sha256(raw).hexdigest()}
    captures={};bodies={}
    for side in ('source','target'):
        envelope=json.loads(Path(manifest[side+'_evidence']).read_text(encoding='utf-8-sig'))
        bodies[side]=envelope['body'];captures[side]=save(side+'-native-capture.json',envelope)
    rows=[]
    for source,target in symbols.items():
        row_restrictions={**restrictions,'max_volume':str(volume[source])} if isinstance(volume,dict) else restrictions
        observations={side:bodies[side]['qualification'][symbol] for side,symbol in [('source',source),('target',target)]}
        scope={**manifest['profile'],'source_symbol':source,'target_symbol':target}
        from .compatibility import SCOPE_KEYS
        scope={k:scope[k] for k in SCOPE_KEYS}
        rows.append(dict(scope=scope,status='QUALIFIED_LIMITED',checked_at=now,expires_at=expires_at,
            reviewer=reviewer,review_policy=POLICY,execution_validation='NOT_OBSERVED',tests={},captures=captures,
            metadata={side:metadata(obs) for side,obs in observations.items()},
            metadata_sha256={side:digest(metadata(obs)) for side,obs in observations.items()},restrictions=row_restrictions))
    matrix=save('reviewed-matrix.json',{'schema_version':1,'revision':1,'records':rows})
    data=dict(schema=SCHEMA,policy=POLICY,native_qualified=False,execution_validation='NOT_OBSERVED',
        environment='DEMO_TO_REAL',observed_at=now,expires_at=expires_at,**binding,
        manifest_digest=digest(manifest),worker_id=worker_id,master=manifest['master'],volume=({s:str(v) for s,v in volume.items()} if isinstance(volume,dict) else str(volume)),
        captures=captures,matrix=matrix,qualification_digest=matrix['sha256'],
        target_review=dict(status='REVIEWED_NATIVE_READ_ONLY',identity=identity,observed_at=now,expires_at=expires_at))
    validate_review(data,folder,now=now,secrets={worker_id:secret})
    raw=canonical(data);identifier=hashlib.sha256(raw).hexdigest()
    (folder/(identifier+'.json')).write_bytes(raw)
    return {'evidence_id':identifier,'authorization_granted':False,'member_activated':False,
            'native_execution_qualified':False,'matrix_sha256':matrix['sha256']}


import tempfile
from contextlib import contextmanager

@contextmanager
def materialize_bundle(bundle):
    if (not isinstance(bundle,dict) or set(bundle)!={'evidence_id','files'}
        or not re.fullmatch('[a-f0-9]{64}',str(bundle['evidence_id']))
        or not isinstance(bundle['files'],dict) or not 1<=len(bundle['files'])<=8
        or len(canonical(bundle))>2_000_000):raise ValueError('INVALID_PREFLIGHT_BUNDLE')
    with tempfile.TemporaryDirectory(prefix='copy-review-') as directory:
        folder=Path(directory)
        for name,content in bundle['files'].items():
            if (not re.fullmatch('[a-zA-Z0-9_.-]{1,100}',name) or name in {'.','..'}
                or not isinstance(content,str)):raise ValueError('INVALID_PREFLIGHT_FILENAME')
            (folder/name).write_text(content,encoding='utf-8')
        yield folder

