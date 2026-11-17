"""Explicit administrative approval of immutable evidence.

Signed worker captures are persisted by preflight_transport; historical operator
artifacts remain supported. Publication alone never authorizes execution.
"""
import hashlib,json,os,re,time
from pathlib import Path
from datetime import datetime,timezone
from . import real_control as rc
from .qualification_version import authorization_digest

REQUIRED={'master_event','compatibility','authorization','native_ack','reconciliation',
          'expiry','uncertain_result','restart','pause'}

def evidence(identifier,root=None,now=None):
    now=time.time() if now is None else now
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('EVIDENCE_ID_REQUIRED')
    folder=root or os.environ.get('COPY_OPERATIONAL_EVIDENCE_DIR')
    if not folder:raise ValueError('OPERATIONAL_EVIDENCE_STORE_NOT_CONFIGURED')
    folder=Path(folder).resolve();path=folder/(identifier+'.json')
    if path.is_symlink() or not path.is_file():raise ValueError('EVIDENCE_NOT_FOUND')
    raw=path.read_bytes()
    if len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=identifier:raise ValueError('EVIDENCE_CHANGED')
    data=json.loads(raw)
    if data.get('schema')=='BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1':
        from .native_preflight_review import validate_review
        try:validate_review(data,folder,now=now)
        except (KeyError,TypeError,OSError):raise ValueError('INVALID_NATIVE_PREFLIGHT_REVIEW') from None
        return data
    if (data.get('schema')!='BECTANSE_OPERATIONAL_REVIEW_V1'
        or data.get('native_qualified') is not True
        or data.get('environment')!='ISOLATED_DEMO'
        or not data.get('observed_at',now+1)<=now<data.get('expires_at',0)):
        raise ValueError('NATIVE_QUALIFICATION_REQUIRED')
    checks=data.get('checks',{})
    if not REQUIRED<=checks.keys():raise ValueError('QUALIFICATION_INCOMPLETE')
    for name in REQUIRED:
        check=checks[name]
        if check.get('status')!='PASSED' or check.get('source')!='MT5_NATIVE':raise ValueError('NATIVE_PROOF_REQUIRED')
        filename=check.get('artifact','')
        p=folder/filename
        if (not filename or Path(filename).name!=filename or p.is_symlink() or not p.is_file()
            or hashlib.sha256(p.read_bytes()).hexdigest()!=check.get('sha256')):
            raise ValueError('NATIVE_ARTIFACT_CHANGED')
    native_files={checks[name]['artifact'] for name in REQUIRED}
    for filename in native_files:
        try:native=json.loads((folder/filename).read_text())
        except (OSError,ValueError):raise ValueError('NATIVE_CONCLUSION_INVALID') from None
        if (native.get('source')!='MT5_NATIVE_LEDGER_AND_COLLECTOR_JOURNAL'
            or native.get('native_qualified') is not True or native.get('uncertain_operations')
            or native.get('not_executed') or not REQUIRED<=native.get('checks',{}).keys()
            or any(native['checks'][k].get('status')!='PASSED' or native['checks'][k].get('source')!='MT5_NATIVE' for k in REQUIRED)):
            raise ValueError('NATIVE_CONCLUSION_INCOMPLETE')
    for artifact in data.get('artifacts',[]):
        name=artifact.get('artifact','');p=folder/name
        if not name or Path(name).name!=name or p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=artifact.get('sha256'):
            raise ValueError('NATIVE_ARTIFACT_CHANGED')
    for key in ('qualification_digest','configuration_digest','executor_digest'):
        if not re.fullmatch('[a-f0-9]{64}',str(data.get(key,''))):raise ValueError('EVIDENCE_BINDING_REQUIRED')
    # Demo execution evidence alone never qualifies a different real broker/account.
    if data.get('target_review',{}).get('status')!='REVIEWED_NATIVE_READ_ONLY':raise ValueError('TARGET_COMPATIBILITY_REVIEW_REQUIRED')
    return data

def status(c,member):
    rows=c.run('SELECT configuration_digest,qualification_digest,approved_by,expires_at,expires_at>NOW() FROM copy_real_releases WHERE member_code=:m',m=member)
    if not rows:return {'state':'NOT_AUTHORIZED'}
    d,q,actor,expiry,fresh=rows[0]
    _,_,_,current=rc.current(c,member)
    return dict(state='AUTHORIZED' if fresh and d==current else 'EXPIRED' if not fresh else 'CONFIGURATION_CHANGED',
                qualification_digest=q,approved_by=actor,expires_at=expiry.isoformat(),execution_enabled=False)

def decide(c,data,actor,*,root=None,now=None):
    now=time.time() if now is None else now
    if (not isinstance(data,dict) or set(data)!={'code','version','action','evidence_id','expires_at','request_id'}
        or data['action'] not in {'approve','revoke'} or type(data['version']) is not int
        or not re.fullmatch('[a-zA-Z0-9_-]{16,100}',str(data['request_id'])) or not actor):
        raise ValueError('INVALID_OPERATIONAL_DECISION')
    member=data['code'];request_hash=rc.digest(data)
    with rc.transaction(c,member):
        # Use the existing durable idempotency journal, with a disjoint ID namespace.
        key='operational:'+data['request_id']
        prior=c.run('SELECT request_digest,response FROM copy_member_commands WHERE member_code=:m AND request_id=:i',m=member,i=key)
        if prior:
            if prior[0][0]!=request_hash:raise ValueError('COMMAND_ID_REUSED')
            return {**rc.decoded(prior[0][1]),**status(c,member)}
        a,v,p,d=rc.current(c,member)
        if v!=data['version']:raise ValueError('CONFIGURATION_CHANGED')
        if data['action']=='approve':
            from .preflight_transport import load
            proof=load(c,member,data['evidence_id'],now) or evidence(data['evidence_id'],root,now)
            if (proof.get('member_code')!=member or proof.get('account_id')!=a
                or proof['configuration_digest']!=d):raise ValueError('EVIDENCE_ACCOUNT_OR_CONFIGURATION_CHANGED')
            if proof.get('schema')=='BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1':
                from .compatibility import number
                settings=p['settings']
                from .preflight_volume import matches
                if not matches(proof,settings):
                    raise ValueError('PREFLIGHT_VOLUME_CONFIGURATION_CHANGED')
            target=proof['target_review']
            prepared=rc.plan(c,dict(code=member,account=a,version=v))
            expected=prepared['identity']
            if (proof.get('schema')=='BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1'
                and proof['master']!={k:prepared['master'][k] for k in ('login','server')}):
                raise ValueError('PREFLIGHT_MASTER_CHANGED')
            if target.get('identity')!=expected or not target.get('observed_at',now+1)<=now<target.get('expires_at',0):
                raise ValueError('TARGET_REVIEW_STALE_OR_CHANGED')
            observed=c.run('SELECT payload FROM copy_real_observations WHERE member_code=:m AND account_id=:a AND version=:v',m=member,a=a,v=v)
            if not observed:raise ValueError('CONNECTION_EVIDENCE_REQUIRED')
            observed=rc.decoded(observed[0][0])
            if observed.get('identity')!=expected or not observed.get('observed_at',now+1)<=now<observed.get('expires_at',0):
                raise ValueError('CONNECTION_EVIDENCE_STALE_OR_CHANGED')
            expiry=data['expires_at']
            if type(expiry) not in (int,float) or not now<expiry<=proof['expires_at']:raise ValueError('INVALID_RELEASE_EXPIRY')
            approval=c.run('SELECT version,decision FROM copy_admin_controls WHERE member_code=:m',m=member)
            if not approval or tuple(approval[0])!=(v,'APPROVED'):raise ValueError('ADMIN_PERMISSION_REQUIRED')
            if rc.pending(c,member):raise ValueError('RECONCILIATION_REQUIRED')
            active=c.run("SELECT 1 FROM copy_real_controls WHERE member_code=:m AND desired='ACTIVE'",m=member)
            if active:raise ValueError('PAUSE_BEFORE_OPERATIONAL_REVIEW')
            c.run('''INSERT INTO copy_real_releases VALUES(:m,:d,:q,:actor,:expiry)
                ON CONFLICT(member_code) DO UPDATE SET configuration_digest=EXCLUDED.configuration_digest,
                qualification_digest=EXCLUDED.qualification_digest,approved_by=EXCLUDED.approved_by,expires_at=EXCLUDED.expires_at''',
                m=member,d=d,q=authorization_digest(proof['qualification_digest'],proof['executor_digest']),actor=actor,expiry=datetime.fromtimestamp(expiry,timezone.utc))
        else:
            c.run('DELETE FROM copy_real_releases WHERE member_code=:m',m=member)
            c.run("UPDATE copy_real_controls SET desired='STOPPED',revision=revision+1,confirmed='REQUEST_RECEIVED',confirmed_at=NULL WHERE member_code=:m AND desired<>'STOPPED'",m=member)
        result=dict(ok=True,**status(c,member))
        c.run('INSERT INTO copy_member_commands(member_code,request_id,request_digest,response) VALUES(:m,:i,:h,CAST(:r AS JSONB))',m=member,i=key,h=request_hash,r=json.dumps(result))
        c.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:m,:actor)',a='OPERATIONAL_'+data['action'].upper()+':'+data['evidence_id'],m=member,actor=actor)
        return result
