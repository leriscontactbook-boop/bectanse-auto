"""Persistent server preparation. Read-only checks; no execution/provisioning adapter.

The deployed MT5 copier is demo-only. This module must report that limitation,
never turn a journal login or an administrator assignment into real execution.
"""
import time
import secrets
import os
from .service import decoded
from .settings import validate
from .access import trading_access_state

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS copy_preparation_jobs (
      member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
      version BIGINT NOT NULL,account_id BIGINT NOT NULL REFERENCES trading_accounts(id),
      state TEXT NOT NULL CHECK(state IN ('PREPARING','BLOCKED','UNASSIGNED','ERROR')),
      reason TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 0,
      due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),lease_token TEXT,lease_until TIMESTAMPTZ,
      checked_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "CREATE INDEX IF NOT EXISTS copy_preparation_due_idx ON copy_preparation_jobs(due_at) WHERE state<>'UNASSIGNED'",
)
LABELS = {
    'ACCOUNT_DISCONNECTED':'Compte déconnecté à votre demande. Aucune nouvelle copie autorisée.',
    'REAL_EXECUTOR_UNAVAILABLE':'Le moteur installé ne prend pas encore en charge la copie réelle.',
    'ACCOUNT_NOT_VERIFIED_REAL':'Le terminal n’a pas confirmé que ce compte MT5 est réel.',
    'MEMBERSHIP_INACTIVE':'L’accès à l’Académie est inactif.',
    'ACCOUNT_IDENTITY_CHANGED':'Le compte MT5 ne correspond plus au rattachement.',
    'MASTER_CHANGED':'Le compte maître a changé : rattachement à vérifier.',
    'METADATA_STALE':'La connexion MT5 n’a pas été confirmée récemment.',
    'ACCOUNT_READ_ONLY':'Le compte ne dispose pas des autorisations de trading nécessaires.',
    'INVALID_SETTINGS':'Les paramètres de lots ou de risque sont invalides.',
    'CHECK_UNAVAILABLE':'La vérification serveur est momentanément indisponible.',
    'CONFIGURATION_CHANGED':'Les paramètres ont changé : nouvelle vérification nécessaire.',
    'REAL_ACCOUNT_REQUIRED':'Le compte suiveur doit être un compte MT5 réel.',
    'CREDENTIAL_UNAVAILABLE':'Les identifiants de connexion sont indisponibles.',
    'TERMINAL_AUTH_FAILED':'La connexion au courtier a été refusée : identifiants à vérifier.',
    'TERMINAL_CONNECTION_FAILED':'Le terminal n’a pas pu établir la connexion au courtier.',
    'TERMINAL_DISCONNECTED':'Le terminal MT5 est déconnecté.',
    'TERMINAL_IDENTITY_CHANGED':'Le terminal connecté ne correspond pas au compte préparé.',
    'HEDGING_REQUIRED':'Le compte doit utiliser le mode hedging pris en charge par le moteur.',
    'FIFO_UNSUPPORTED':'Les contraintes FIFO de ce compte ne sont pas prises en charge.',
    'COLLECTION_FAILED':'Les données du courtier n’ont pas pu être vérifiées.',
}


def enqueue(conn, code, version, account, assigned):
    # Carry only existing decisions across unchanged account/master identities.
    from .version_continuity import synchronize
    synchronize(conn, code)
    # Called inside the assignment transaction: no gap between assignment and job.
    conn.run("""INSERT INTO copy_preparation_jobs(member_code,version,account_id,state)
      VALUES(:code,:version,:account,:state) ON CONFLICT(member_code) DO UPDATE SET
      version=EXCLUDED.version,account_id=EXCLUDED.account_id,state=EXCLUDED.state,
      reason='',attempts=0,due_at=NOW(),lease_token=NULL,lease_until=NULL,checked_at=NULL,updated_at=NOW()
      WHERE copy_preparation_jobs.version<EXCLUDED.version
         OR (copy_preparation_jobs.version=EXCLUDED.version
             AND copy_preparation_jobs.state IN ('PREPARING','BLOCKED','ERROR')
             AND (copy_preparation_jobs.lease_until IS NULL
                  OR copy_preparation_jobs.lease_until<NOW()))""",
      code=code,version=version,account=account,state='PREPARING' if assigned else 'UNASSIGNED')


def status(conn, code):
    rows=conn.run('SELECT version,state,reason,checked_at,updated_at,attempts FROM copy_preparation_jobs WHERE member_code=:code',code=code)
    if not rows:return None
    version,state,reason,checked,updated,attempts=rows[0]
    return dict(version=version,state=state,reason=reason,message=LABELS.get(reason,''),
                checked_at=checked.isoformat() if checked else None,updated_at=updated.isoformat(),
                attempts=attempts,execution_enabled=False)


def claim(conn, member_code=None, *, role='preparation'):
    # Preparation and execution share one durable queue, but only the executor
    # may consume a verified preparation handoff.  The role is a transport
    # property, never a member setting: it prevents a second worker from
    # reopening the terminal between observation and executor attachment.
    if role not in {'preparation','executor'}:
        raise ValueError('INVALID_CLAIM_ROLE')
    token=secrets.token_hex(24)
    handoff = role == 'executor'
    rows=conn.run("""UPDATE copy_preparation_jobs SET lease_token=:token,
      lease_until=NOW()+INTERVAL '120 seconds',attempts=attempts+1
      WHERE member_code=(SELECT member_code FROM copy_preparation_jobs j
        WHERE (CAST(:member AS TEXT) IS NULL OR j.member_code=:member)
          AND j.state<>'UNASSIGNED' AND j.due_at<=NOW()
          AND ((:handoff AND COALESCE(j.lease_token,'') LIKE 'executor-handoff:%')
               OR (NOT :handoff AND (j.lease_until IS NULL OR j.lease_until<NOW())
                   AND COALESCE(j.lease_token,'') NOT LIKE 'executor-handoff:%')
               OR (:handoff AND (j.lease_until IS NULL OR j.lease_until<NOW())
                   AND COALESCE(j.lease_token,'') NOT LIKE 'executor-handoff:%'))
          AND NOT EXISTS (SELECT 1 FROM copy_real_sessions s WHERE s.member_code=j.member_code
                          AND s.account_id=j.account_id AND s.version=j.version AND s.expires_at>NOW())
        ORDER BY CASE WHEN COALESCE(j.lease_token,'') LIKE 'executor-handoff:%' THEN 0 ELSE 1 END,
                 j.due_at FOR UPDATE SKIP LOCKED LIMIT 1)
      RETURNING member_code,version,account_id""",token=token,member=member_code,handoff=handoff)
    return dict(zip(('code','version','account'),rows[0]),token=token,role=role) if rows else None


def inspect(conn, job):
    from .master_assignment import selection_allows
    rows=conn.run("""SELECT c.version,c.payload,to_jsonb(m),
      jsonb_build_object('login',a.login,'server',a.server,'account_type',to_jsonb(a)->'account_type',
      'access_mode',to_jsonb(a)->'access_mode','status',to_jsonb(a)->'status',
      'last_successful_sync_at',to_jsonb(a)->'last_successful_sync_at',
      'recent',CAST(to_jsonb(a)->>'last_successful_sync_at' AS TIMESTAMPTZ) BETWEEN NOW()-INTERVAL '30 seconds' AND NOW())
      FROM copy_configurations c JOIN members m ON m.code=c.member_code
      JOIN trading_accounts a ON a.id=c.account_id AND a.user_id=c.member_code
      WHERE c.member_code=:code AND c.account_id=:account
      ORDER BY c.version DESC LIMIT 1""",code=job['code'],account=job['account'])
    if not rows or rows[0][0]!=job['version']:return 'CONFIGURATION_CHANGED'
    _,payload,member,account=rows[0];payload=decoded(payload);account=decoded(account)
    if not trading_access_state(decoded(member))[0]:return 'MEMBERSHIP_INACTIVE'
    if (payload.get('login'),payload.get('server'))!=(account.get('login'),account.get('server')):return 'ACCOUNT_IDENTITY_CHANGED'
    if not selection_allows(conn,payload):return 'MASTER_CHANGED'
    try:validate(payload['settings'])
    except (ValueError,KeyError,TypeError):return 'INVALID_SETTINGS'
    # MT5 ACCOUNT_TRADE_MODE_REAL=2, or explicit REAL from an established collector.
    if str(account.get('account_type')).upper() not in ('2','REAL'):return 'ACCOUNT_NOT_VERIFIED_REAL'
    if account.get('access_mode')!='TRADING_ALLOWED':return 'ACCOUNT_READ_ONLY'
    if account.get('recent') is not True or account.get('status')!='SYNCED':return 'METADATA_STALE'
    # No real-capable execution adapter exists. There is deliberately no flag to
    # bypass this result, and no call to initialize, login, order_check or order_send.
    return 'REAL_EXECUTOR_UNAVAILABLE'


def complete(conn, job, reason):
    state='ERROR' if reason=='CHECK_UNAVAILABLE' else 'BLOCKED'
    return bool(conn.run("""UPDATE copy_preparation_jobs SET state=:state,reason=:reason,
      lease_token=NULL,lease_until=NULL,checked_at=NOW(),updated_at=NOW(),
      due_at=NOW()+CASE WHEN :reason='TERMINAL_AUTH_FAILED' THEN INTERVAL '15 minutes' ELSE INTERVAL '60 seconds' END
      WHERE member_code=:code AND version=:version AND lease_token=:token AND lease_until>NOW()
      RETURNING member_code""",state=state,reason=reason,code=job['code'],version=job['version'],token=job['token']))


def run_once(get_conn):
    # A registered Windows node owns the same persistent queue when configured.
    # Local checks must not consume its leases before it can connect the terminal.
    if os.environ.get('COPY_REAL_PREPARATION_NODE') and len(os.environ.get('COPY_REAL_PREPARATION_SECRET','')) >= 32:
        return False
    conn=get_conn()
    try:
        job=claim(conn)
        if not job:return False
        try:reason=inspect(conn,job)
        except Exception:reason='CHECK_UNAVAILABLE'
        complete(conn,job,reason)
        return True
    finally:conn.close()


def main():
    # Explicit separate process; no implicit background thread in web requests.
    from trading_journal.migrate import connection_from_environment
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    while True:
        try:worked=run_once(connection_from_environment)
        except Exception:
            worked=False
            print('Préparation : service indisponible, reprise au prochain cycle.',flush=True)
        if not args.watch:return
        time.sleep(1 if worked else 5)


if __name__=='__main__':main()
