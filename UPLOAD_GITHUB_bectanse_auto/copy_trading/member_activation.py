"""Explicit member commands. Real activation remains unavailable without executor."""
import json
import re
from .service import decoded,digest
from .onboarding_server import status,enqueue

SCHEMA=("""CREATE TABLE IF NOT EXISTS copy_member_commands(
 member_code TEXT NOT NULL REFERENCES members(code) ON DELETE CASCADE,request_id TEXT NOT NULL,
 request_digest TEXT NOT NULL,response JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(member_code,request_id))""",)


def command(conn, code, data):
    if isinstance(data,dict) and data.get('enabled') is True and set(data)=={'enabled','version','request_id'}:
        from .real_control import command as real_command
        return real_command(conn,code,dict(action='activate',version=data['version'],request_id=data['request_id']))
    if isinstance(data,dict) and 'action' in data:
        from .real_control import command as real_command
        return real_command(conn,code,data)
    if (not isinstance(data,dict) or type(data.get('enabled')) is not bool
        or type(data.get('version')) is not int or data['version']<1
        or not isinstance(data.get('request_id'),str)
        or not re.fullmatch('[A-Za-z0-9_-]{16,100}',data['request_id'])
        or set(data)!={'enabled','version','request_id'}):raise ValueError('Commande invalide.')
    # Old browser tabs still send enabled=false. Route their pause through the
    # same authoritative control once a real execution record exists; writing
    # only copy_admin_controls would leave a real session authorized.
    if not data['enabled'] and conn.run('SELECT 1 FROM copy_real_controls WHERE member_code=:m',m=code):
        from .real_control import command as real_command
        return real_command(conn,code,dict(action='pause',version=data['version'],request_id=data['request_id']))
    conn.run('BEGIN')
    try:
        if not conn.run('SELECT code FROM members WHERE code=:code FOR UPDATE',code=code):raise ValueError('Membre introuvable.')
        existing=conn.run('SELECT request_digest,response FROM copy_member_commands WHERE member_code=:code AND request_id=:request',code=code,request=data['request_id'])
        if existing:
            if existing[0][0]!=digest(data):raise ValueError('Cette commande a déjà été utilisée pour une autre action.')
            conn.run('COMMIT');return decoded(existing[0][1])
        rows=conn.run('''SELECT c.version,c.account_id,c.payload FROM copy_configurations c
          JOIN trading_accounts a ON a.id=c.account_id AND a.user_id=c.member_code
          WHERE c.member_code=:code ORDER BY version DESC LIMIT 1''',code=code)
        if not rows or rows[0][0]!=data['version']:raise ValueError('Les paramètres ont changé. Actualisez la page.')
        version,account,payload=rows[0];payload=dict(decoded(payload))
        if 'master_assignment' not in payload:raise ValueError('Ce compte doit d’abord être rattaché au nouveau parcours.')
        if data['enabled']:
            preparation=status(conn,code)
            result=dict(ok=False,execution_enabled=False,reason='REAL_EXECUTOR_UNAVAILABLE',
                        error=(preparation or {}).get('message') or 'Le moteur de copie réelle n’est pas disponible.')
            action='MEMBER_ACTIVATION_REFUSED'
        else:
            # Stopping must remain possible even if risk settings are invalid or access expired.
            if payload.get('requested_enabled'):
                payload['requested_enabled']=False;version+=1
                conn.run("UPDATE copy_configurations SET status='SUPERSEDED',lease_token=NULL,lease_until=NULL WHERE member_code=:code AND status='PENDING'",code=code)
                conn.run("INSERT INTO copy_configurations(member_code,version,account_id,payload,digest,status) VALUES(:code,:version,:account,CAST(:payload AS JSONB),:digest,'PENDING')",code=code,version=version,account=account,payload=json.dumps(payload),digest=digest(payload))
                enqueue(conn,code,version,account,payload['master_assignment'] is not None)
            conn.run("INSERT INTO copy_admin_controls(member_code,version,decision) VALUES(:code,:version,'PAUSED') ON CONFLICT(member_code) DO UPDATE SET version=EXCLUDED.version,decision='PAUSED',updated_at=NOW()",code=code,version=version)
            result=dict(ok=True,version=version,requested_enabled=False,execution_enabled=False,
                        message='Demande de pause enregistrée. Les positions existantes ne sont pas clôturées. La réception par le moteur reste à confirmer.')
            action='MEMBER_PAUSE_REQUESTED'
        conn.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:action,:code,:actor)',action=action,code=code,actor='member:'+code)
        conn.run('INSERT INTO copy_member_commands(member_code,request_id,request_digest,response) VALUES(:code,:request,:digest,CAST(:response AS JSONB))',code=code,request=data['request_id'],digest=digest(data),response=json.dumps(result))
        conn.run('COMMIT');return result
    except Exception:conn.run('ROLLBACK');raise
