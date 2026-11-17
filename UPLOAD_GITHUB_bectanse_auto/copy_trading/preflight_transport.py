"""Durable native evidence transport over the existing authenticated worker API.

Evidence publication alone grants no permission. Explicit member ON may release
a matching bundle after all server controls pass; administrative suspension vetoes it.
"""
import json,time
from datetime import datetime,timezone
from . import real_control as rc
from .account_observation import canonical
from .qualification_version import authorization_digest

SCHEMA=("""CREATE TABLE IF NOT EXISTS copy_native_reviews(
 id TEXT PRIMARY KEY,member_code TEXT NOT NULL REFERENCES members(code),
 account_id BIGINT NOT NULL,version BIGINT NOT NULL,configuration_digest TEXT NOT NULL,
 worker_id TEXT NOT NULL,qualification_digest TEXT NOT NULL,bundle JSONB NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,received_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
 "CREATE INDEX IF NOT EXISTS copy_native_reviews_member ON copy_native_reviews(member_code,received_at DESC)")

from .native_preflight_review import materialize_bundle as materialize


def unpack(bundle,now=None):
    from .operational_release import evidence
    with materialize(bundle) as root:return evidence(bundle['evidence_id'],root,now)


def load(c,member,identifier,now=None):
    rows=c.run('SELECT bundle FROM copy_native_reviews WHERE member_code=:m AND id=:i',m=member,i=identifier)
    return unpack(rc.decoded(rows[0][0]),now) if rows else None


def publish(c,worker,data):
    member=data['member_code'];bundle=data['bundle'];now=time.time()
    with rc.transaction(c,member):
        rc.session(c,worker,data)
        proof=unpack(bundle,now)
        if proof.get('schema')!='BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1':raise ValueError('SIGNED_PREFLIGHT_REQUIRED')
        if proof.get('worker_id')!=worker or proof.get('member_code')!=member:raise ValueError('PREFLIGHT_OWNER_CHANGED')
        a,v,p,d=rc.current(c,member)
        expected=rc.plan(c,dict(code=member,account=a,version=v))
        if (proof['target_review']['identity']!=expected['identity'] or proof['configuration_digest']!=d
            or proof['master']!={k:expected['master'][k] for k in ('login','server')}
            or (data['account_id'],data['version'])!=(a,v)):raise ValueError('PREFLIGHT_CONFIGURATION_CHANGED')
        existing=c.run('SELECT bundle FROM copy_native_reviews WHERE id=:i',i=bundle['evidence_id'])
        if existing:
            if canonical(rc.decoded(existing[0][0]))!=canonical(bundle):raise ValueError('PREFLIGHT_ID_REUSED')
            return {'stored':True,'evidence_id':bundle['evidence_id'],'execution_enabled':False}
        if not 0<=now-proof['observed_at']<=15:raise ValueError('PREFLIGHT_UPLOAD_STALE')
        q=authorization_digest(proof['qualification_digest'],proof['executor_digest'])
        c.run('''INSERT INTO copy_native_reviews(id,member_code,account_id,version,configuration_digest,
          worker_id,qualification_digest,bundle,expires_at) VALUES(:i,:m,:a,:v,:d,:w,:q,CAST(:b AS JSONB),:e)''',
          i=bundle['evidence_id'],m=member,a=a,v=v,d=d,w=worker,q=q,b=json.dumps(bundle),
          e=datetime.fromtimestamp(proof['expires_at'],timezone.utc))
        return {'stored':True,'evidence_id':bundle['evidence_id'],'execution_enabled':False}


def candidate(c,member):
    a,v,_,d=rc.current(c,member)
    rows=c.run('''SELECT id,bundle FROM copy_native_reviews WHERE member_code=:m AND account_id=:a
        AND version=:v AND configuration_digest=:d AND expires_at>NOW() ORDER BY received_at DESC LIMIT 1''',m=member,a=a,v=v,d=d)
    if not rows:return None
    proof=unpack(rc.decoded(rows[0][1]))
    bundle=rc.decoded(rows[0][1]);matrix=json.loads(bundle['files'][proof['matrix']['artifact']])
    return dict(evidence_id=rows[0][0],version=v,expires_at=proof['expires_at'],
        instruments=[{'source':r['scope']['source_symbol'],'target':r['scope']['target_symbol']} for r in matrix['records']],
        execution_validation=proof['execution_validation'])


def approved(c,worker,data):
    with rc.transaction(c,data['member_code']):
        rc.session(c,worker,data)
        a,v,_,d=rc.current(c,data['member_code'])
        if (a,v)!=(data['account_id'],data['version']):raise ValueError('CONFIGURATION_CHANGED')
        authorize_member_request(c,worker,data)
        rows=c.run('''SELECT n.bundle FROM copy_native_reviews n JOIN copy_real_releases r
            ON r.member_code=n.member_code AND r.configuration_digest=n.configuration_digest
            AND r.qualification_digest=n.qualification_digest
            WHERE n.member_code=:m AND n.account_id=:a AND n.version=:v AND n.configuration_digest=:d
            AND n.worker_id=:w AND n.expires_at>NOW() AND r.expires_at>NOW() ORDER BY n.received_at DESC LIMIT 1''',
            m=data['member_code'],a=a,v=v,d=d,w=worker)
        return {'bundle':rc.decoded(rows[0][0]) if rows else None,'execution_enabled':False}


def authorize_member_request(c,worker,data):
    """Inside the member transaction: technical release after ON, or renewal for existing copies.

    This is an automatic policy decision, never a fabricated admin approval.
    OFF renews an existing release only; uncertain operations always veto it.
    """
    member=data['member_code']
    a,v,p,d=rc.current(c,member)
    controls=c.run('SELECT desired FROM copy_real_controls WHERE member_code=:m AND account_id=:a AND version=:v',m=member,a=a,v=v)
    if not controls:return
    maintaining=controls[0][0]!='ACTIVE'
    if maintaining and not c.run('SELECT 1 FROM copy_real_releases WHERE member_code=:m AND configuration_digest=:d',m=member,d=d):return
    if rc.pending(c,member):return
    try:rc.eligibility(c,member,require_release=False,management=maintaining)
    except (ValueError,RuntimeError):return
    existing=c.run('SELECT qualification_digest FROM copy_real_releases WHERE member_code=:m AND configuration_digest=:d',m=member,d=d)
    rows=c.run('''SELECT id,bundle,qualification_digest,expires_at FROM copy_native_reviews
        WHERE member_code=:m AND account_id=:a AND version=:v AND configuration_digest=:d
        AND worker_id=:w AND expires_at>NOW() ORDER BY received_at DESC LIMIT 1''',m=member,a=a,v=v,d=d,w=worker)
    if not rows:return
    identifier,bundle,q,expiry=rows[0]
    if existing and existing[0][0]==q and c.run('SELECT 1 FROM copy_real_releases WHERE member_code=:m AND expires_at>NOW()',m=member):return
    proof=unpack(rc.decoded(bundle))
    if existing:
        previous=c.run('SELECT bundle FROM copy_native_reviews WHERE member_code=:m AND qualification_digest=:q ORDER BY received_at DESC LIMIT 1',m=member,q=existing[0][0])
        if not previous:return
        old=rc.decoded(previous[0][0]);new=rc.decoded(bundle)
        old_proof=json.loads(old['files'][old['evidence_id']+'.json'])
        old_rows=json.loads(old['files'][old_proof['matrix']['artifact']])['records']
        new_rows=json.loads(new['files'][proof['matrix']['artifact']])['records']
        pairs=lambda rows:{r['scope']['source_symbol']:(r['scope']['target_symbol'],r['restrictions']) for r in rows}
        before,after=pairs(old_rows),pairs(new_rows)
        if any(after.get(k)!=v for k,v in before.items()):return
    expected=rc.plan(c,dict(code=member,account=a,version=v))
    from .compatibility import number
    from .preflight_volume import matches
    if (proof.get('schema')!='BECTANSE_NATIVE_PREFLIGHT_REVIEW_V1'
        or proof.get('worker_id')!=worker or proof.get('member_code')!=member
        or proof['configuration_digest']!=d or proof['target_review']['identity']!=expected['identity']
        or proof['master']!={k:expected['master'][k] for k in ('login','server')}
        or not matches(proof,p['settings'])
        or q!=authorization_digest(proof['qualification_digest'],proof['executor_digest'])):
        raise ValueError('PREFLIGHT_CONFIGURATION_CHANGED')
    actor='policy:MEMBER_ON_NATIVE_CHECKS_V1'
    c.run('''INSERT INTO copy_real_releases VALUES(:m,:d,:q,:actor,:expiry)
        ON CONFLICT(member_code) DO UPDATE SET configuration_digest=EXCLUDED.configuration_digest,
        qualification_digest=EXCLUDED.qualification_digest,approved_by=EXCLUDED.approved_by,expires_at=EXCLUDED.expires_at''',
        m=member,d=d,q=q,actor=actor,expiry=expiry)
    c.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:action,:m,:actor)',
          action='MEMBER_ON_TECHNICAL_RELEASE:'+identifier,m=member,actor=actor)
