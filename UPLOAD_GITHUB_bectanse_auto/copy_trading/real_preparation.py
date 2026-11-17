"""Persistent handoff of preparation to a registered Windows node.

The master may be demo or real. Only the follower must be real. Connection
observations never grant member consent or operational trading permission.
"""
import json
from .service import decoded
from .onboarding_server import claim, complete
from .master_assignment import selection_allows
from .access import trading_access_state

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS copy_real_observations (
      member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
      account_id BIGINT NOT NULL REFERENCES trading_accounts(id),
      version BIGINT NOT NULL, worker_id TEXT NOT NULL,
      payload JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
)


def plan(conn, job):
    rows = conn.run("""SELECT c.payload,to_jsonb(m) AS member_data,to_jsonb(a) AS account_data
      FROM copy_configurations c JOIN members m ON m.code=c.member_code
      JOIN trading_accounts a ON a.id=c.account_id AND a.user_id=c.member_code
      WHERE c.member_code=:code AND c.version=:version AND c.account_id=:account
      AND c.version=(SELECT MAX(version) FROM copy_configurations WHERE member_code=:code)
      AND a.provider='MT5' AND a.platform='MT5'""",
      code=job['code'], version=job['version'], account=job['account'])
    if not rows:
        raise ValueError('CONFIGURATION_CHANGED')
    payload, member, account = map(decoded, rows[0])
    if not trading_access_state(member)[0]:
        raise ValueError('MEMBERSHIP_INACTIVE')
    if not payload.get('master_assignment') or not selection_allows(conn, payload):
        raise ValueError('MASTER_CHANGED')
    if (str(account['login']), account['server']) != (str(payload['login']), payload['server']):
        raise ValueError('ACCOUNT_IDENTITY_CHANGED')
    if account.get('status')=='DISCONNECTED':
        raise ValueError('ACCOUNT_DISCONNECTED')
    if str(account.get('account_type', '')).upper() in ('0', '1', 'DEMO', 'CONTEST'):
        raise ValueError('REAL_ACCOUNT_REQUIRED')
    # Unknown mode can be verified by the dedicated real-only connector; never
    # infer REAL from a server name or from an old journal connection alone.
    return {'job': dict(job), 'identity': dict(member_code=job['code'], account_id=job['account'],
             version=job['version'], login=str(account['login']), server=account['server'], trade_mode=2),
            'master': payload['master_assignment'], 'settings': payload['settings'],
            'execution_authorized': False}


def claim_plan(conn, decrypt, member_code=None, *, role='preparation'):
    if member_code is not None and (not isinstance(member_code,str) or not 1<=len(member_code)<=100):
        raise ValueError('INVALID_MEMBER_FILTER')
    job = claim(conn,member_code,role=role)
    if not job:
        return None
    try:
        result = plan(conn, job)
        credentials = conn.run("""SELECT c.encrypted_password,c.nonce,c.auth_tag
          FROM trading_credentials c JOIN trading_accounts a ON a.id=c.trading_account_id
          WHERE a.id=:account AND a.user_id=:code""", account=job['account'], code=job['code'])
        if not credentials:
            raise ValueError('CREDENTIAL_UNAVAILABLE')
        result['password'] = decrypt(credentials[0], job['account'], job['code'])
        if not result['password']:
            raise ValueError('CREDENTIAL_UNAVAILABLE')
        conn.run("""UPDATE copy_preparation_jobs SET lease_until=NOW()+INTERVAL '120 seconds'
          WHERE member_code=:code AND account_id=:account AND version=:version AND lease_token=:token""", **job)
        return result
    except ValueError as error:
        reason = str(error)
        if reason not in {'CONFIGURATION_CHANGED', 'MEMBERSHIP_INACTIVE', 'MASTER_CHANGED',
                          'ACCOUNT_IDENTITY_CHANGED', 'REAL_ACCOUNT_REQUIRED', 'CREDENTIAL_UNAVAILABLE'}:
            reason = 'CHECK_UNAVAILABLE'
        complete(conn, job, reason)
        return None
    except Exception:
        complete(conn, job, 'CHECK_UNAVAILABLE')
        return None


def accept_observation(conn, worker, job, envelope, key, *, now=None):
    from .account_observation import verify
    conn.run('BEGIN')
    try:
        rows = conn.run("""SELECT 1 FROM copy_preparation_jobs WHERE member_code=:code
          AND account_id=:account AND version=:version AND lease_token=:token
          AND lease_until>NOW() FOR UPDATE""", **job)
        if not rows:
            raise ValueError('PREPARATION_LEASE_EXPIRED')
        expected = plan(conn, job)['identity']
        # The authenticated node chooses its dedicated terminal path. It cannot
        # choose another owner/account/version or mark a demo account as real.
        expected['terminal'] = envelope['body']['identity']['terminal']
        if not isinstance(expected['terminal'], str) or not expected['terminal']:
            raise ValueError('TERMINAL_IDENTITY_REQUIRED')
        observation = verify(envelope, expected, key, now=now)
        conn.run("""INSERT INTO copy_real_observations(member_code,account_id,version,worker_id,payload)
          VALUES(:code,:account,:version,:worker,CAST(:payload AS JSONB))
          ON CONFLICT(member_code) DO UPDATE SET account_id=EXCLUDED.account_id,
          version=EXCLUDED.version,worker_id=EXCLUDED.worker_id,payload=EXCLUDED.payload,received_at=NOW()""",
          code=job['code'], account=job['account'], version=job['version'], worker=worker,
          payload=json.dumps(observation, allow_nan=False))
        # A preparation-only worker hands this exact binding to an executor.
        # An executor which collected the observation itself keeps its lease:
        # changing its token here used to make a later failure acknowledgement
        # fail silently and leave the account waiting for the old lease.
        role=job.get('role','preparation')
        if role == 'preparation':
            conn.run("""UPDATE copy_preparation_jobs SET state='PREPARING',reason='',
              lease_token=:handoff,lease_until=NOW()+INTERVAL '120 seconds',
              checked_at=NOW(),updated_at=NOW(),due_at=NOW()
              WHERE member_code=:code AND account_id=:account AND version=:version
                AND lease_token=:token""",
              handoff='executor-handoff:'+job['token'], **job)
        else:
            conn.run("""UPDATE copy_preparation_jobs SET state='PREPARING',reason='',
              lease_until=NOW()+INTERVAL '120 seconds',checked_at=NOW(),updated_at=NOW(),due_at=NOW()
              WHERE member_code=:code AND account_id=:account AND version=:version
                AND lease_token=:token""", **job)
        conn.run('COMMIT')
        return {'connected': True, 'execution_authorized': False, 'handoff': role == 'preparation'}
    except Exception:
        conn.run('ROLLBACK')
        raise


def public_observation(conn, code, account, version, *, now=None, cached=None):
    import time
    from datetime import datetime, timezone
    from .admin_statistics import financials
    now=time.time() if now is None else now
    rows=([[cached[(code,account,version)]]] if (code,account,version) in cached else []) if cached is not None else conn.run("""SELECT payload FROM copy_real_observations WHERE member_code=:code
      AND account_id=:account AND version=:version""",code=code,account=account,version=version)
    if not rows:return None
    body=decoded(rows[0][0])
    fresh=body['observed_at']<=now<body['expires_at']
    stats=financials({'balance':body['balance'],'equity':body['equity'],'currency':body['currency'],
                      'last_successful_sync_at':datetime.fromtimestamp(body['observed_at'],timezone.utc).isoformat()},
                     now=datetime.fromtimestamp(now,timezone.utc))
    stats.update(source='Terminal MT5 — copie',fresh=fresh,
                 unrealized_profit=body['unrealized_profit'],
                 realized_net_in_window=body['realized_net_in_window'],
                 history_from=body['history_from'],history_to=body['history_to'])
    return {'connected':fresh,'state':'CONNECTED' if fresh else 'STALE',
            'execution_authorized':False,'reconciled':False,'statistics':stats,
            'positions':body['positions'] if fresh else [],'observed_at':body['observed_at']}
