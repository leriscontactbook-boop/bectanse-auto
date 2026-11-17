"""Authenticated VPS provisioning and liveness. No trading or admin mutation."""
from .symbols import MAX_SYMBOLS
import json
import hashlib
import os
import re
import time
from flask import jsonify, request
from trading_journal.security import CredentialCipher, EncryptedCredential, verify_worker_request
from .demo_backend import bindings
from .service import digest

AGENT_SCHEMA = ("""CREATE TABLE IF NOT EXISTS copy_agent_nodes (
 worker_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
 lease_until TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 report JSONB NOT NULL DEFAULT '{}')""",
"""CREATE TABLE IF NOT EXISTS copy_dashboard_snapshots (account_id BIGINT PRIMARY KEY, payload JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",)


def node_secrets():
    values = json.loads(os.environ.get('COPY_AGENT_WORKER_SECRETS', '{}'))
    if not isinstance(values, dict) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', k)
            or not isinstance(v, str) or len(v) < 32 for k, v in values.items()):
        raise ValueError('INVALID_AGENT_CONFIGURATION')
    return values


def lease_valid(conn, worker, instance):
    return bool(conn.run('''SELECT 1 FROM copy_agent_nodes WHERE worker_id=:worker
        AND instance_id=:instance AND lease_until>NOW()''',worker=worker,instance=instance))


def plan_metadata(conn, worker, master_only=False):
    """Only explicit operator bindings select a follower; never adopt journal accounts."""
    rows = conn.run('SELECT revision,login,server,environment,password_cipher FROM copy_admin_master WHERE id=1')
    if not rows: return {'state':'WAITING_MASTER'}
    revision,login,server,environment,cipher=rows[0]
    if environment != 'DEMO': return {'state':'MASTER_NOT_DEMO'}
    binding=bindings().get(worker)
    if not binding: return {'state':'WAITING_BINDING'}
    if master_only:
        public={'master':{'login':str(login),'server':server},'master_revision':revision}
        return {'state':'READY_SOURCE','plan_id':digest(public),**public,'master_cipher':cipher}
    rows=conn.run('''SELECT a.login,a.server,c.encrypted_password,c.nonce,c.auth_tag
        FROM trading_accounts a JOIN trading_credentials c ON c.trading_account_id=a.id
        WHERE a.id=:account AND a.user_id=:code AND a.provider='MT5' AND a.platform='MT5'
        AND a.status NOT IN ('DISCONNECTED','AUTH_ERROR','ACCESS_EXPIRED')''',
        account=binding['account_id'],code=binding['member_code'])
    if not rows:return {'state':'WAITING_FOLLOWER_CREDENTIALS'}
    follower_login,follower_server,password,nonce,tag=rows[0]
    if (str(login),server)==(str(follower_login),follower_server):return {'state':'IDENTICAL_ACCOUNTS_FORBIDDEN'}
    symbols=json.loads(os.environ.get('COPY_DEMO_SYMBOLS','{}')).get(worker,{})
    if not isinstance(symbols,dict) or not symbols or len(symbols)>MAX_SYMBOLS or any(
            not isinstance(s,str) or not 1<=len(s)<=64 for pair in symbols.items() for s in pair):
        return {'state':'WAITING_SYMBOL_MAPPING'}
    public={'master':{'login':str(login),'server':server},
        'follower':{'login':str(follower_login),'server':follower_server},
        'account_id':binding['account_id'],'symbols':symbols,'master_revision':revision,
        'credential_revision':hashlib.sha256(bytes(password)+bytes(nonce)+bytes(tag)).hexdigest()}
    return {'state':'READY','plan_id':digest(public),**public,'master_cipher':cipher,
        'follower_cipher':(password,nonce,tag),'member_code':binding['member_code']}


def register_agent_routes(app, service, decrypt_master):
    @app.post('/internal/copy/dashboard')
    def copy_dashboard_report():
        if request.content_length is None or request.content_length>524288:
            return jsonify(ok=False,error='INVALID_REQUEST_SIZE'),413
        worker=request.headers.get('X-MT5-Worker-ID','');secret=node_secrets().get(worker,'')
        nonce=request.headers.get('X-MT5-Nonce','');data=request.get_json(silent=True)
        if (not secret or not isinstance(data,dict) or data.get('worker_id')!=worker
            or not re.fullmatch(r'[a-f0-9]{32}',nonce)
            or not verify_worker_request(secret,request.method,request.path,
                request.headers.get('X-MT5-Timestamp',''),request.get_data(cache=True),
                request.headers.get('X-MT5-Signature',''),nonce=nonce) or not service.accept_nonce(nonce)):
            return jsonify(ok=False),401
        conn=service.get_conn()
        try:
            # Only the existing source node may publish reporting for this fleet.
            if worker in json.loads(os.environ.get('COPY_FLEET_WORKER_IDS','[]')) or not conn.run(
                "SELECT 1 FROM copy_agent_nodes WHERE worker_id=:w AND lease_until>NOW() AND report->>'observer_alive'='true'",w=worker):
                return jsonify(ok=False),403
            from .dashboard_metrics import validate_snapshot
            raw=data.get('snapshots',[data.get('snapshot')])
            if not isinstance(raw,list) or not 1<=len(raw)<=100:raise ValueError('INVALID_BATCH')
            values=[validate_snapshot(v,time.time()) for v in raw]
            master=conn.run('SELECT login,server FROM copy_admin_master WHERE id=1')
            accounts=conn.run("""SELECT a.id,a.login,a.server FROM trading_accounts a WHERE EXISTS(
                SELECT 1 FROM copy_configurations c WHERE c.account_id=a.id AND c.member_code=a.user_id)""")
            identities={}
            for account,login,server in accounts:identities.setdefault((login,server),[]).append(account)
            batch=[];seen=set()
            for value in values:
                identity=(value['login'],value['server'])
                if master and tuple(master[0])==identity:account=0
                else:
                    found=identities.get(identity,[])
                    if len(found)!=1:return jsonify(ok=False,error='ACCOUNT_IDENTITY_UNCONFIRMED'),409
                    account=found[0]
                if account in seen:raise ValueError('DUPLICATE_ACCOUNT')
                seen.add(account);batch.append(dict(account_id=account,payload=value))
            conn.run('BEGIN')
            from .reporting_journal import persist, accumulated_statistics
            for item in batch:
                value=item['payload']
                deals=value.pop('journal_deals',None)
                if deals is not None and item['account_id']:
                    persist(conn,item['account_id'],deals,value['statistics']['currency'])
            batch=[item for item in batch if not item['payload'].pop('journal_only',False)]
            for item in batch:
                if item['account_id'] and item['payload'].get('journal_synced_at') is not None:
                    accumulated_statistics(conn,item['account_id'],item['payload'])
            conn.run("""INSERT INTO copy_dashboard_snapshots(account_id,payload)
                SELECT account_id,payload FROM jsonb_to_recordset(CAST(:batch AS JSONB)) AS x(account_id BIGINT,payload JSONB)
                ON CONFLICT(account_id) DO UPDATE SET payload=EXCLUDED.payload,received_at=NOW()
                WHERE (copy_dashboard_snapshots.payload->>'at')::double precision < (EXCLUDED.payload->>'at')::double precision""",batch=json.dumps(batch))
            for item in batch:
                synced=item['payload'].get('journal_synced_at')
                if item['account_id'] and synced is not None:
                    conn.run('UPDATE trading_accounts SET last_successful_sync_at=GREATEST(last_successful_sync_at,to_timestamp(:t)) WHERE id=:a',a=item['account_id'],t=synced)
            conn.run('COMMIT')
            return jsonify(ok=True)
        except (ValueError,KeyError,TypeError):
            conn.run('ROLLBACK')
            return jsonify(ok=False,error='INVALID_DASHBOARD_SNAPSHOT'),400
        finally:conn.close()

    @app.post('/internal/copy/agent/poll')
    def copy_agent_poll():
        try:
            if request.content_length is None or request.content_length>16384:
                return jsonify(ok=False,error='INVALID_REQUEST_SIZE'),413
            worker=request.headers.get('X-MT5-Worker-ID','')
            secret=node_secrets().get(worker,'')
            nonce=request.headers.get('X-MT5-Nonce','')
            data=request.get_json(silent=True)
            if (not secret or not isinstance(data,dict) or data.get('worker_id')!=worker
                    or not re.fullmatch(r'[a-f0-9]{32}',nonce)
                    or not verify_worker_request(secret,request.method,request.path,
                        request.headers.get('X-MT5-Timestamp',''),request.get_data(cache=True),
                        request.headers.get('X-MT5-Signature',''),nonce=nonce)
                    or not service.accept_nonce(nonce)):
                return jsonify(ok=False),401
            instance=data.get('instance_id',''); report=data.get('report',{})
            if (not isinstance(instance,str) or not re.fullmatch(r'[a-f0-9]{32}',instance)
                    or not isinstance(report,dict) or not isinstance(report.get('state'),str)
                    or not re.fullmatch(r'[A-Z0-9_]{1,80}',report['state'])):
                return jsonify(ok=False,error='INVALID_REPORT'),400
            clean={k:report[k] for k in ('state','observer_alive','executor_alive','execution_armed')
                   if k in report and (k=='state' or type(report[k]) is bool)}
            conn=service.get_conn()
            try:
                rows=conn.run('''INSERT INTO copy_agent_nodes(worker_id,instance_id,lease_until,report)
                    VALUES(:worker,:instance,NOW()+INTERVAL '30 seconds',CAST(:report AS JSONB))
                    ON CONFLICT(worker_id) DO UPDATE SET instance_id=EXCLUDED.instance_id,
                    lease_until=EXCLUDED.lease_until,received_at=NOW(),report=EXCLUDED.report
                    WHERE copy_agent_nodes.instance_id=EXCLUDED.instance_id OR copy_agent_nodes.lease_until<NOW()
                    RETURNING worker_id''',worker=worker,instance=instance,report=json.dumps(clean))
                if not rows:return jsonify(ok=False,error='AGENT_ALREADY_RUNNING'),409
                master_only=data.get('master_only') is True
                fleet_workers=json.loads(os.environ.get('COPY_FLEET_WORKER_IDS','[]'))
                if master_only and worker in fleet_workers:return jsonify(ok=False,error='MASTER_OBSERVER_NOT_AUTHORIZED'),403
                plan=plan_metadata(conn,worker,master_only=master_only)
                if master_only and plan['state']=='READY_SOURCE' and report.get('observer_alive') is True:
                    from .dashboard_telemetry import clean as clean_telemetry
                    try:
                        clean['master_telemetry']=clean_telemetry(report.get('master_telemetry'),
                            login=plan['master']['login'],server=plan['master']['server'])
                    except (ValueError,TypeError,KeyError):pass
                    else:
                        conn.run('UPDATE copy_agent_nodes SET report=CAST(:report AS JSONB) WHERE worker_id=:worker AND instance_id=:instance',
                            report=json.dumps(clean),worker=worker,instance=instance)
                if plan['state']=='READY_SOURCE':
                    plan['master']['password']=decrypt_master(plan.pop('master_cipher'))
                    if not plan['master']['password']:raise ValueError('CREDENTIAL_UNAVAILABLE')
                if plan['state']=='READY':
                    encrypted=plan.pop('follower_cipher');cipher=plan.pop('master_cipher')
                    member=plan['member_code']  # Explicit ownership for multi-follower enrollment.
                    # Credentials are delivered only over the authenticated private channel, never persisted by the agent.
                    fleet_workers=json.loads(os.environ.get('COPY_FLEET_WORKER_IDS','[]'))
                    if not isinstance(fleet_workers,list) or any(not isinstance(w,str) for w in fleet_workers):
                        raise ValueError('INVALID_FLEET_WORKERS')
                    follower_only=worker in fleet_workers or data.get('follower_only') is True
                    if not follower_only:
                        plan['master']['password']=decrypt_master(cipher)
                    plan['follower']['password']=CredentialCipher.from_environment().decrypt(
                        EncryptedCredential(*(bytes(v) for v in encrypted)),
                        f"trading-account:{plan['account_id']}:{member}")
                    if (not follower_only and not plan['master']['password']) or not plan['follower']['password']:
                        raise ValueError('CREDENTIAL_UNAVAILABLE')
                result=jsonify(ok=True,mode='DEMO_ONLY',issued_at=time.time(),lease_seconds=30,**plan)
                result.headers['Cache-Control']='no-store'
                return result
            finally:conn.close()
        except Exception:
            app.logger.error('copy_agent_poll_failed')
            return jsonify(ok=False,error='AGENT_SERVICE_UNAVAILABLE'),503
