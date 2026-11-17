"""Authoritative PostgreSQL member commands and single-executor reservations.

No release is created by this module. An operational release must be reviewed
separately, bound to the exact configuration and qualification. Reservations
never expire into permission to retry. Pauses wait for explicit reconciliation.
"""
from contextlib import contextmanager
import json
import re
import time
from .service import decoded,digest
from .real_preparation import plan
from .demo_policy import executable_settings, SafetyError, decimal
from .access import copy_access

SCHEMA=(
"""CREATE TABLE IF NOT EXISTS copy_real_objective_events(id TEXT PRIMARY KEY,
 member_code TEXT NOT NULL REFERENCES members(code),kind TEXT NOT NULL CHECK(kind IN ('TRIPPED','MANUAL_RESUME')),
 evidence JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS copy_real_objective_latches(member_code TEXT PRIMARY KEY REFERENCES members(code),
 evidence JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS copy_real_controls(member_code TEXT PRIMARY KEY REFERENCES members(code),
 account_id BIGINT NOT NULL,version BIGINT NOT NULL,revision BIGINT NOT NULL,
 desired TEXT NOT NULL CHECK(desired IN ('ACTIVE','PAUSED','STOPPED')),
 confirmed TEXT NOT NULL DEFAULT 'REQUEST_RECEIVED',confirmed_at TIMESTAMPTZ,
 activation_started_at TIMESTAMPTZ)""",
"ALTER TABLE copy_real_controls ADD COLUMN IF NOT EXISTS activation_started_at TIMESTAMPTZ",
"""CREATE TABLE IF NOT EXISTS copy_real_releases(member_code TEXT PRIMARY KEY REFERENCES members(code),
 configuration_digest TEXT NOT NULL,qualification_digest TEXT NOT NULL,
 approved_by TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS copy_real_sessions(member_code TEXT PRIMARY KEY REFERENCES members(code),
 account_id BIGINT NOT NULL,version BIGINT NOT NULL,worker_id TEXT NOT NULL,
 instance_id TEXT NOT NULL,qualification_digest TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS copy_real_operations(id TEXT PRIMARY KEY,member_code TEXT NOT NULL REFERENCES members(code),
 account_id BIGINT NOT NULL,version BIGINT NOT NULL,revision BIGINT NOT NULL,instance_id TEXT NOT NULL,
 request_digest TEXT NOT NULL,request JSONB NOT NULL,state TEXT NOT NULL DEFAULT 'RESERVED'
 CHECK(state IN ('RESERVED','UNKNOWN','CONFIRMED','REJECTED','NOT_SENT')),
 evidence JSONB,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"CREATE INDEX IF NOT EXISTS copy_real_unresolved_idx ON copy_real_operations(member_code) WHERE state IN ('RESERVED','UNKNOWN')",
)

@contextmanager
def transaction(c,member):
    c.run('BEGIN')
    try:
        if not c.run('SELECT code FROM members WHERE code=:m FOR UPDATE',m=member):raise ValueError('MEMBER_NOT_FOUND')
        yield
        c.run('COMMIT')
    except BaseException:c.run('ROLLBACK');raise


def current(c,member):
    rows=c.run('SELECT account_id,version,payload,digest FROM copy_configurations WHERE member_code=:m ORDER BY version DESC LIMIT 1',m=member)
    if not rows:raise ValueError('CONFIGURATION_REQUIRED')
    a,v,p,d=rows[0]
    return a,v,decoded(p),d


def pending(c,member):
    return c.run("SELECT COUNT(*) FROM copy_real_operations WHERE member_code=:m AND state IN ('RESERVED','UNKNOWN')",m=member)[0][0]


def eligibility(c,member,now=None,*,require_release=True,management=False):
    now=time.time() if now is None else now
    a,v,p,d=current(c,member)
    prepared=plan(c,dict(code=member,account=a,version=v))
    member_data=decoded(c.run('SELECT to_jsonb(m) FROM members m WHERE code=:m',m=member)[0][0])
    if not copy_access(member_data)[0]:raise ValueError('MEMBERSHIP_INACTIVE')
    approval=c.run('SELECT version,decision FROM copy_admin_controls WHERE member_code=:m',m=member)
    if not approval or approval[0][0]!=v or approval[0][1] not in ({'APPROVED','PAUSED'} if management else {'APPROVED'}):
        raise ValueError('ADMIN_PERMISSION_REQUIRED')
    try:executable_settings(p['settings'],require_stop_loss=False,supported_options={'objectif_actif','copier_ordres_en_attente','forcer_lot_minimum','inverser_trades','drawdown_actif','drawdown_gain_actif','filtre_news','convertir_pending_invalide'},all_lot_modes=True)
    except SafetyError as e:raise ValueError(str(e)) from None
    rows=c.run('SELECT payload FROM copy_real_observations WHERE member_code=:m AND account_id=:a AND version=:v',m=member,a=a,v=v)
    if not rows:raise ValueError('CONNECTION_EVIDENCE_REQUIRED')
    from .financial_objective import evaluate
    objective=({'blocked':False} if management else evaluate(p['settings'],decoded(rows[0][0]),now))
    latches=c.run('SELECT evidence FROM copy_real_objective_latches WHERE member_code=:m',m=member)
    if latches:
        previous=decoded(latches[0][0])
        if (previous.get('automatic_period_reset') and objective.get('period_start') is not None
            and objective['period_start']>previous.get('period_start',float('inf'))):
            c.run('DELETE FROM copy_real_objective_latches WHERE member_code=:m',m=member)
            latches=[]
    if not management and (objective['blocked'] or latches):
        raise ValueError('OBJECTIVE_MANUAL_RESUME_REQUIRED')
    o=decoded(rows[0][0]);identity=o.get('identity',{})
    if (any(identity.get(k)!=val for k,val in prepared['identity'].items())
        or not o.get('observed_at',now+1)<=now<o.get('expires_at',0)):
        raise ValueError('CONNECTION_EVIDENCE_STALE_OR_CHANGED')
    if (o.get('trade_allowed') is not True or o.get('margin_mode')!=2
        or o.get('fifo_close') is not False):raise ValueError('TERMINAL_EXECUTION_NOT_READY')
    if not require_release:return a,v,p,None
    release=c.run('SELECT qualification_digest FROM copy_real_releases WHERE member_code=:m AND configuration_digest=:d AND expires_at>NOW()',m=member,d=d)
    if not release:raise ValueError('OPERATIONAL_RELEASE_REQUIRED')
    return a,v,p,release[0][0]


def state(c,member,*,check_eligibility=True):
    rows=c.run('SELECT account_id,version,revision,desired,confirmed,confirmed_at,activation_started_at FROM copy_real_controls WHERE member_code=:m',m=member)
    if not rows:return {'desired':'STOPPED','confirmed':'UNCONFIRMED','execution_enabled':False}
    a,v,r,d,s,at,activation_started_at=rows[0]
    live=c.run('SELECT qualification_digest FROM copy_real_sessions WHERE member_code=:m AND account_id=:a AND version=:v AND expires_at>NOW()',m=member,a=a,v=v)
    current_a,current_v,_,_=current(c,member)
    acknowledged=s in {'REQUEST_RECEIVED','UNCONFIRMED'} or (at is not None and 0 <= time.time()-at.timestamp() <= 10)
    fresh=bool(live) and (a,v)==(current_a,current_v) and acknowledged
    active=fresh and d=='ACTIVE' and s=='ACTIVE'
    reason=None
    if d=='ACTIVE' and check_eligibility:
        try:
            *_,qualified=eligibility(c,member)
            if live and live[0][0]!=qualified:active=False;reason='QUALIFICATION_CHANGED'
        except (ValueError,RuntimeError) as error:active=False;reason=str(error)
    return dict(account_id=a,version=v,revision=r,desired=d,confirmed=s if fresh else 'UNCONFIRMED',
                activation_started_at=(activation_started_at.isoformat()
                                       if activation_started_at is not None else None),
                execution_enabled=active,reason=reason,pending_operations=pending(c,member))


def command(c,member,data,*,admin_actor=None):
    if (not isinstance(data,dict) or set(data)!={'action','version','request_id'}
        or data['action'] not in {'activate','resume','pause','disable'}
        or type(data['version']) is not int or data['version']<1
        or not isinstance(data['request_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',data['request_id'])):
        raise ValueError('INVALID_MEMBER_COMMAND')
    if admin_actor and data['action'] not in {'activate','pause','disable'}:
        raise ValueError('ADMIN_RESUME_NOT_ALLOWED')
    command_digest=digest({'command':data,'admin_actor':admin_actor}) if admin_actor else digest(data)
    with transaction(c,member):
        prior=c.run('SELECT request_digest,response FROM copy_member_commands WHERE member_code=:m AND request_id=:i',m=member,i=data['request_id'])
        if prior:
            if prior[0][0]!=command_digest:raise ValueError('COMMAND_ID_REUSED')
            return decoded(prior[0][1])
        a,v,_,_=current(c,member)
        if v!=data['version']:raise ValueError('CONFIGURATION_CHANGED')
        desired={'activate':'ACTIVE','resume':'ACTIVE','pause':'PAUSED','disable':'STOPPED'}[data['action']]
        if desired=='ACTIVE':
            if admin_actor:
                # The explicit admin ON approves this exact configuration and
                # records start intent atomically; all execution gates still apply.
                c.run("""INSERT INTO copy_admin_controls(member_code,version,decision)
                    VALUES(:m,:v,'APPROVED') ON CONFLICT(member_code) DO UPDATE SET
                    version=EXCLUDED.version,decision='APPROVED',updated_at=NOW()""",m=member,v=v)
            if data['action']=='resume' or (admin_actor and data['action']=='activate' and c.run("SELECT 1 FROM copy_real_objective_latches WHERE member_code=:m AND evidence->>'rule'='ACCOUNT_FLOATING_OVER_CURRENT_BALANCE_V1'",m=member)):
                from .financial_objective import evaluate
                _,_,payload,_=current(c,member)
                observations=c.run('SELECT payload FROM copy_real_observations WHERE member_code=:m',m=member)
                if not observations:raise ValueError('CONNECTION_EVIDENCE_REQUIRED')
                if evaluate(payload['settings'],decoded(observations[0][0]),time.time())['blocked']:
                    raise ValueError('OBJECTIVE_STILL_REACHED')
                previous=c.run('SELECT evidence FROM copy_real_objective_latches WHERE member_code=:m',m=member)
                if previous:
                    c.run("INSERT INTO copy_real_objective_events VALUES(:id,:m,'MANUAL_RESUME',CAST(:e AS JSONB),NOW()) ON CONFLICT(id) DO NOTHING",
                          id=digest([member,data['request_id'],'MANUAL_RESUME']),m=member,
                          e=json.dumps({'request_id':data['request_id'],'prior_latch':decoded(previous[0][0])}))
                c.run('DELETE FROM copy_real_objective_latches WHERE member_code=:m',m=member)
            try:eligibility(c,member)
            except ValueError as error:
                # ON persists intent while native preparation finishes. It is
                # never an execution permission; poll/reserve still check all gates.
                if (data['action'] not in {'activate','resume'} or str(error) not in {
                    'CONNECTION_EVIDENCE_REQUIRED','CONNECTION_EVIDENCE_STALE_OR_CHANGED',
                    'OPERATIONAL_RELEASE_REQUIRED'}):raise
                if data['action']=='resume' and str(error)!='OPERATIONAL_RELEASE_REQUIRED':raise
                if c.run('SELECT 1 FROM copy_real_objective_latches WHERE member_code=:m',m=member):
                    raise ValueError('OBJECTIVE_MANUAL_RESUME_REQUIRED')
        # A different request id represents a new explicit human attempt only
        # after the prior ON has reached its fixed two-minute terminal window.
        # Double-clicks and transport retries inside that window do not move it.
        restart_expired=False
        if desired=='ACTIVE':
            prior_control=c.run('SELECT desired,confirmed,activation_started_at FROM copy_real_controls WHERE member_code=:m',m=member)
            if prior_control:
                old_desired,old_confirmed,old_started=prior_control[0]
                restart_expired=(old_desired=='ACTIVE' and old_confirmed!='ACTIVE'
                    and old_started is not None and time.time()-old_started.timestamp()>=120)
        c.run("""INSERT INTO copy_real_controls(member_code,account_id,version,revision,desired,activation_started_at)
          VALUES(:m,:a,:v,1,:d,CASE WHEN :d='ACTIVE' THEN NOW() ELSE NULL END) ON CONFLICT(member_code) DO UPDATE SET account_id=EXCLUDED.account_id,
          version=EXCLUDED.version,revision=copy_real_controls.revision+1,desired=EXCLUDED.desired,
          confirmed='REQUEST_RECEIVED',confirmed_at=NULL,
          activation_started_at=CASE WHEN EXCLUDED.desired='ACTIVE' THEN NOW() ELSE NULL END
          WHERE copy_real_controls.account_id IS DISTINCT FROM EXCLUDED.account_id
             OR copy_real_controls.version IS DISTINCT FROM EXCLUDED.version
             OR copy_real_controls.desired IS DISTINCT FROM EXCLUDED.desired
             OR (:restart AND copy_real_controls.confirmed<>'ACTIVE')""",m=member,a=a,v=v,d=desired,restart=restart_expired)
        if desired=='ACTIVE':
            # A click also wakes the connection queue. Preserve an in-flight
            # lease and existing sessions; never create a competing connector.
            from .onboarding_server import enqueue
            enqueue(c,member,v,a,True)
            c.run("""UPDATE copy_preparation_jobs SET due_at=NOW(),updated_at=NOW()
              WHERE member_code=:m AND account_id=:a AND version=:v
              AND (lease_until IS NULL OR lease_until<NOW())""",m=member,a=a,v=v)
        response=dict(ok=True,**state(c,member))
        c.run('INSERT INTO copy_member_commands(member_code,request_id,request_digest,response) VALUES(:m,:i,:h,CAST(:r AS JSONB))',m=member,i=data['request_id'],h=command_digest,r=json.dumps(response))
        c.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:m,:actor)',a=('REAL_ADMIN_' if admin_actor else 'REAL_MEMBER_')+data['action'].upper(),m=member,actor=admin_actor or 'member:'+member)
        return response


def poll(c,worker,data):
    member=data['member_code'];instance=data['instance_id']
    if not isinstance(instance,str) or not re.fullmatch('[a-f0-9]{32}',instance):raise ValueError('INSTANCE_REQUIRED')
    with transaction(c,member):
        from .version_continuity import synchronize
        synchronize(c,member)
        a,v,p,_=current(c,member)
        if (a,v)!=(data['account_id'],data['version']):raise ValueError('CONFIGURATION_CHANGED')
        # First attachment registers a stopped control, never member consent.
        c.run("INSERT INTO copy_real_controls(member_code,account_id,version,revision,desired) VALUES(:m,:a,:v,1,'STOPPED') ON CONFLICT(member_code) DO NOTHING",m=member,a=a,v=v)
        old=c.run('SELECT instance_id,worker_id,expires_at>NOW() FROM copy_real_sessions WHERE member_code=:m',m=member)
        if old and (old[0][0],old[0][1])!=(instance,worker) and (old[0][2] or pending(c,member)):
            raise ValueError('EXECUTOR_ALREADY_OWNED_OR_UNCERTAIN')
        # A renewed session is not a completed recovery. Neither an expired
        # process nor a new owner may inherit the previous ACTIVE confirmation.
        if old and (not old[0][2] or (old[0][0],old[0][1])!=(instance,worker)):
            c.run("UPDATE copy_real_controls SET confirmed='UNCONFIRMED',confirmed_at=NOW() WHERE member_code=:m",m=member)
        q=data.get('qualification_digest','')
        if not isinstance(q,str) or not re.fullmatch('[a-f0-9]{64}',q):raise ValueError('QUALIFICATION_REQUIRED')
        c.run("""INSERT INTO copy_real_sessions VALUES(:m,:a,:v,:w,:i,:q,NOW()+INTERVAL '10 seconds')
          ON CONFLICT(member_code) DO UPDATE SET account_id=EXCLUDED.account_id,version=EXCLUDED.version,
          worker_id=EXCLUDED.worker_id,instance_id=EXCLUDED.instance_id,
          qualification_digest=EXCLUDED.qualification_digest,expires_at=EXCLUDED.expires_at""",m=member,a=a,v=v,w=worker,i=instance,q=q)
        # Entry is checked below under the same member lock; do not repeat it.
        result=state(c,member,check_eligibility=False);reason=None
        if result['desired']=='ACTIVE':
            try:
                *_,qualified=eligibility(c,member)
                if q!=qualified:raise ValueError('QUALIFICATION_CHANGED')
            except (ValueError,RuntimeError) as error:reason=str(error)
        management_reason=None
        # Successful entry includes all management checks. OFF and entry
        # refusals still evaluate management independently to manage copies.
        if result['desired']!='ACTIVE' or reason is not None:
            try:
                *_,management_q=eligibility(c,member,management=True)
                if q!=management_q:raise ValueError('QUALIFICATION_CHANGED')
            except (ValueError,RuntimeError) as error:management_reason=str(error)
        if reason is not None:result['execution_enabled']=False
        bound=(result.get('account_id'),result.get('version'))==(a,v)
        result.update(configuration=p,authorized=result['desired']=='ACTIVE' and reason is None and bound,
                      reason=reason,management_authorized=management_reason is None and bound,
                      management_reason=management_reason,valid_until=time.time()+5)
        # A completed healthy cycle may renew only an already ACTIVE session.
        # This is never activation, a reservation, or a replacement for recovery.
        previous=data.get('completed_cycle')
        if (isinstance(previous,dict) and previous.get('revision')==result['revision']
            and isinstance(previous.get('at'),(int,float))
            and 0<=time.time()-previous['at']<=3
            and old and (old[0][0],old[0][1])==(instance,worker) and old[0][2]
            and result['authorized'] and result['confirmed']=='ACTIVE' and not result['pending_operations']):
            c.run("UPDATE copy_real_controls SET confirmed_at=NOW() WHERE member_code=:m",m=member)
        if p['settings'].get('filtre_news'):
            from .economic_news import current as news_current
            result['news_filter']=news_current()
        result['completed_cycle_supported']=True
        if result['authorized'] and result['confirmed']=='REQUEST_RECEIVED':
            c.run("UPDATE copy_real_controls SET confirmed='AUTHORIZATION_ACCEPTED',confirmed_at=NOW() WHERE member_code=:m",m=member)
            result['confirmed']='AUTHORIZATION_ACCEPTED'
        return result


def session(c,worker,data):
    rows=c.run('''SELECT qualification_digest FROM copy_real_sessions WHERE member_code=:m AND account_id=:a
       AND version=:v AND worker_id=:w AND instance_id=:i AND expires_at>NOW()''',m=data['member_code'],a=data['account_id'],v=data['version'],w=worker,i=data['instance_id'])
    if not rows:raise ValueError('EXECUTOR_SESSION_EXPIRED_OR_REPLACED')
    return rows[0][0]


def reserve(c,worker,data,observation_key=None):
    m=data['member_code'];op=data['operation']
    if (not isinstance(op,dict) or not isinstance(op.get('operation_id'),str)
        or not re.fullmatch('[a-f0-9]{32}',op['operation_id']) or op.get('kind') not in {'OPEN','MODIFY','CLOSE','PENDING_CREATE','PENDING_MODIFY','PENDING_CANCEL'}):raise ValueError('OPERATION_REQUIRED')
    with transaction(c,m):
        q=session(c,worker,data)
        managing=op['kind'] in {'CLOSE','MODIFY','PENDING_CANCEL','PENDING_MODIFY'}
        a,v,p,qualified=eligibility(c,m,management=managing)
        # eligibility above is fresh and checked under this same member lock.
        s=state(c,m,check_eligibility=False)
        if ((a,v)!=(data['account_id'],data['version']) or (s.get('account_id'),s.get('version'))!=(a,v)
            or (not managing and s['desired']!='ACTIVE') or s['revision']!=data['revision']):
            raise ValueError('AUTHORIZATION_REVOKED_OR_CHANGED')
        if q!=qualified:raise ValueError('QUALIFICATION_CHANGED')
        if op.get('owner')!=str(p['login'])+'@'+p['server'] or op.get('source')!=str(p['master_assignment']['login'])+'@'+p['master_assignment']['server']:
            raise ValueError('OPERATION_IDENTITY_CHANGED')
        if managing:
            origins=c.run('''SELECT request FROM copy_real_operations WHERE id=:id AND member_code=:m
                AND account_id=:a AND state='CONFIRMED' ''',id=op.get('copy_origin',''),m=m,a=a)
            origin=decoded(origins[0][0]) if origins else {}
            if (origin.get('kind') not in {'OPEN','PENDING_CREATE'}
                or any(origin.get(k)!=op.get(k) for k in ('owner','source','symbol','source_symbol','side','magic'))):
                raise ValueError('COPY_OWNERSHIP_REQUIRED')
            if op['kind'] in {'PENDING_CANCEL','PENDING_MODIFY'} and origin['kind']!='PENDING_CREATE':
                raise ValueError('COPY_OWNERSHIP_REQUIRED')
        if op['kind'] in {'OPEN','PENDING_CREATE'}:
            settings=p['settings']
            if settings.get('filtre_news'):
                from .economic_news import current as news_current
                news=news_current()
                if news['blocked']:raise ValueError(news['reason'])
            if 'sizing_receipt' in op:
                from .sizing_receipt import verify
                verify(op,settings,observation_key,time.time())
            else:
                # Compatibility for the previous executor: exact fixed lots only.
                if settings['mode_risque'] not in {'Lots fixes','Lot par symbole'} or settings.get('inverser_trades') or op.get('converted_pending'):
                    raise ValueError('SIZING_EVIDENCE_REQUIRED')
                requested=settings['lots'] if settings['mode_risque']=='Lots fixes' else settings['lot_symboles'].get(op.get('source_symbol'),0)
                if decimal(requested)<=0 or decimal(op.get('volume'))!=decimal(requested):
                    raise ValueError('REQUESTED_VOLUME_CHANGED')
        if c.run('SELECT 1 FROM copy_real_operations WHERE id=:i',i=op['operation_id']):raise ValueError('OPERATION_ALREADY_RESERVED')
        if pending(c,m):raise ValueError('RECONCILIATION_REQUIRED')
        access_until=copy_access(decoded(c.run('SELECT to_jsonb(m) FROM members m WHERE code=:m',m=m)[0][0]))[1]
        observation=decoded(c.run('SELECT payload FROM copy_real_observations WHERE member_code=:m',m=m)[0][0])
        release_until=c.run('SELECT expires_at FROM copy_real_releases WHERE member_code=:m',m=m)[0][0].timestamp()
        session_until=c.run('SELECT expires_at FROM copy_real_sessions WHERE member_code=:m',m=m)[0][0].timestamp()
        deadline=min(time.time()+2,access_until,observation['expires_at'],release_until,session_until)
        if deadline<=time.time():raise ValueError('AUTHORIZATION_REVOKED_OR_CHANGED')
        c.run('''INSERT INTO copy_real_operations(id,member_code,account_id,version,revision,instance_id,request_digest,request)
            VALUES(:i,:m,:a,:v,:r,:inst,:h,CAST(:p AS JSONB))''',i=op['operation_id'],m=m,a=a,v=v,r=s['revision'],inst=data['instance_id'],h=digest(op),p=json.dumps(op))
        return dict(operation_id=op['operation_id'],request_digest=digest(op),revision=s['revision'],expires_at=deadline)


def abandon(c,worker,data):
    """Tombstone a durably recorded attempt that never reached native submission.

    Serializes with reserve on the member row: a delayed reserve cannot appear
    after an absent-status response. Never grants permission to retransmit.
    Only the owning authenticated executor can make this assertion.
    """
    m=data['member_code'];op=data['operation']
    if (not isinstance(op,dict) or not re.fullmatch('[a-f0-9]{32}',str(op.get('operation_id','')))
        or data.get('native_submission_started') is not False):raise ValueError('UNSENT_EVIDENCE_REQUIRED')
    with transaction(c,m):
        session(c,worker,data)
        a,v,p,_=current(c,m)
        if (a,v)!=(data['account_id'],data['version']):raise ValueError('CONFIGURATION_CHANGED')
        if op.get('owner')!=str(p['login'])+'@'+p['server'] or op.get('source')!=str(p['master_assignment']['login'])+'@'+p['master_assignment']['server']:
            raise ValueError('OPERATION_IDENTITY_CHANGED')
        evidence={'reason':'DURABLE_NO_NATIVE_SUBMISSION','request_digest':digest(op)}
        rows=c.run('SELECT member_code,instance_id,request_digest,state,evidence FROM copy_real_operations WHERE id=:i',i=op['operation_id'])
        if rows:
            member,instance,h,status,prior=rows[0]
            if (member,instance,h)!=(m,data['instance_id'],digest(op)):raise ValueError('OPERATION_NOT_OWNED')
            if status=='NOT_SENT' and decoded(prior)==evidence:return {'state':'NOT_SENT','evidence':evidence}
            if status!='RESERVED':raise ValueError('RECONCILIATION_REQUIRED')
            c.run("UPDATE copy_real_operations SET state='NOT_SENT',evidence=CAST(:e AS JSONB),updated_at=NOW() WHERE id=:i",e=json.dumps(evidence),i=op['operation_id'])
        else:
            c.run("""INSERT INTO copy_real_operations(id,member_code,account_id,version,revision,instance_id,request_digest,request,state,evidence)
              VALUES(:i,:m,:a,:v,:r,:inst,:h,CAST(:p AS JSONB),'NOT_SENT',CAST(:e AS JSONB))""",
              i=op['operation_id'],m=m,a=a,v=v,r=data['revision'],inst=data['instance_id'],h=digest(op),p=json.dumps(op),e=json.dumps(evidence))
        return {'state':'NOT_SENT','evidence':evidence}


def report(c,worker,data,observation_key=None):
    m=data['member_code']
    with transaction(c,m):
        session(c,worker,data)
        if 'observation' in data:
            from .account_observation import verify
            rows=c.run('SELECT payload FROM copy_real_observations WHERE member_code=:m AND account_id=:a AND version=:v',m=m,a=data['account_id'],v=data['version'])
            if not rows:raise ValueError('PREPARED_IDENTITY_REQUIRED')
            expected=decoded(rows[0][0])['identity']
            observed=verify(data['observation'],expected,observation_key)
            c.run('UPDATE copy_real_observations SET payload=CAST(:p AS JSONB),worker_id=:w,received_at=NOW() WHERE member_code=:m',p=json.dumps(observed),w=worker,m=m)
            from .financial_objective import evaluate
            _,_,configuration,_=current(c,m)
            try:objective=evaluate(configuration['settings'],observed,time.time())
            except (SafetyError,KeyError,TypeError,ValueError):objective=None
            if objective and objective['blocked']:
                evidence={**objective,'automatic_period_reset':not objective.get('close_copies',False),'proof':{'observation':data['observation'],'settings':configuration['settings']}}
                inserted=c.run('INSERT INTO copy_real_objective_latches(member_code,evidence) VALUES(:m,CAST(:e AS JSONB)) ON CONFLICT(member_code) DO NOTHING RETURNING member_code',m=m,e=json.dumps(evidence))
                if inserted:
                    c.run("INSERT INTO copy_real_objective_events VALUES(:id,:m,'TRIPPED',CAST(:e AS JSONB),NOW())",
                          id=digest([m,evidence]),m=m,e=json.dumps(evidence))
                # Preserve ON intent for the documented next-period reset.
                # Eligibility blocks entries while latched; OFF remains authoritative.
                if objective.get('close_copies'):
                    c.run("UPDATE copy_real_controls SET desired='PAUSED',revision=revision+1,confirmed='REQUEST_RECEIVED',confirmed_at=NULL WHERE member_code=:m AND desired='ACTIVE'",m=m)

        for item in data.get('operations',[]):
            if item.get('state') not in {'UNKNOWN','CONFIRMED','REJECTED','NOT_SENT'}:raise ValueError('INVALID_OPERATION_RESULT')
            evidence=item.get('evidence')
            if not isinstance(evidence,dict) or not evidence:raise ValueError('RESULT_EVIDENCE_REQUIRED')
            if item['state']=='CONFIRMED' and evidence.get('reconciled') is not True:raise ValueError('RECONCILIATION_EVIDENCE_REQUIRED')
            rows=c.run('SELECT state,evidence FROM copy_real_operations WHERE id=:i AND member_code=:m AND instance_id=:inst',i=item['id'],m=m,inst=data['instance_id'])
            if not rows:raise ValueError('OPERATION_NOT_OWNED')
            if rows[0][0] not in {'RESERVED','UNKNOWN'}:
                if rows[0][0]!=item['state'] or decoded(rows[0][1])!=evidence:raise ValueError('SETTLED_RESULT_CHANGED')
                continue
            c.run('UPDATE copy_real_operations SET state=:s,evidence=CAST(:e AS JSONB),updated_at=NOW() WHERE id=:i',s=item['state'],e=json.dumps(evidence),i=item['id'])
        s=state(c,m);ack=data.get('ack')
        if (ack or data.get('phase')=='STARTING') and (s.get('account_id'),s.get('version'))!=(data['account_id'],data['version']):
            raise ValueError('COMMAND_BINDING_CHANGED')
        if data.get('phase')=='UNCONFIRMED':
            c.run("UPDATE copy_real_controls SET confirmed='UNCONFIRMED',confirmed_at=NOW() WHERE member_code=:m",m=m)
        if data.get('phase')=='STARTING':
            if data.get('revision')!=s.get('revision') or s['desired']!='ACTIVE':raise ValueError('COMMAND_REVISION_CHANGED')
            eligibility(c,m)
            c.run("UPDATE copy_real_controls SET confirmed='STARTING',confirmed_at=NOW() WHERE member_code=:m",m=m)
        if ack:
            if data.get('revision')!=s.get('revision') or ack!=s['desired']:raise ValueError('COMMAND_REVISION_CHANGED')
            if pending(c,m):raise ValueError('RECONCILIATION_REQUIRED')
            if ack=='ACTIVE':
                *_,q=eligibility(c,m)
                if q!=session(c,worker,data):raise ValueError('QUALIFICATION_CHANGED')
            c.run('UPDATE copy_real_controls SET confirmed=:s,confirmed_at=NOW() WHERE member_code=:m',s=ack,m=m)
        result=state(c,m)
    if data.get('refresh_authorization') is True:return poll(c,worker,data)
    return result
