"""Versioned administrative master selection. Never provisions or sends orders."""
import json
from .service import decoded, digest
from .settings import validate
from .access import trading_access_state


def member_record(conn, code, lock=False):
    rows = conn.run('SELECT to_jsonb(m) FROM members m WHERE code=:code' + (' FOR UPDATE' if lock else ''), code=code)
    if not rows:
        raise ValueError('Membre introuvable.')
    return decoded(rows[0][0])


def selection_allows(conn, payload):
    # Existing operator bindings keep their established behavior until explicitly edited.
    if 'master_assignment' not in payload:
        return True
    chosen = payload['master_assignment']
    if not isinstance(chosen, dict):
        return False
    rows = conn.run('SELECT login,server,environment FROM copy_admin_master WHERE id=1')
    return bool(rows) and chosen == dict(zip(('login','server','environment'), rows[0]))


def options(service, code):
    conn = service.get_conn()
    try:
        member = member_record(conn, code)
        accounts = conn.run("SELECT id,display_name,login_masked,server FROM trading_accounts WHERE user_id=:code AND provider='MT5' AND platform='MT5' ORDER BY id", code=code)
        rows = conn.run('SELECT version,account_id,payload FROM copy_configurations WHERE member_code=:code ORDER BY version DESC LIMIT 1', code=code)
        master = conn.run('SELECT revision,alias,login,server,environment FROM copy_admin_master WHERE id=1')
        return dict(code=code, name=member.get('nom') or code, active=trading_access_state(member)[0],
            accounts=[dict(zip(('id','name','login_masked','server'), r)) for r in accounts],
            version=rows[0][0] if rows else 0, account_id=rows[0][1] if rows else None,
            assignment=decoded(rows[0][2]).get('master_assignment') if rows else None,
            master=dict(zip(('revision','alias','login','server','environment'), master[0])) if master else None)
    finally:
        conn.close()


def assign(service, data, actor):
    if (not isinstance(data, dict) or not isinstance(data.get('code'), str) or not 1 <= len(data['code']) <= 100
            or type(data.get('version')) is not int or data['version'] < 0
            or 'master_revision' not in data or type(data.get('account_id')) is not int
            or (data.get('master_revision') is not None and type(data['master_revision']) is not int)):
        raise ValueError('Membre, compte et version requis.')
    code = data['code']; conn = service.get_conn()
    try:
        conn.run('BEGIN')
        member = member_record(conn, code, True)
        rows = conn.run('SELECT version,account_id,payload FROM copy_configurations WHERE member_code=:code ORDER BY version DESC LIMIT 1', code=code)
        version = rows[0][0] if rows else 0
        old = dict(decoded(rows[0][2])) if rows else None
        if version != data['version']:
            raise ValueError('Le dossier a changé. Recharge la sélection avant de confirmer.')
        if rows and rows[0][1] != data['account_id']:
            raise ValueError('Ce membre possède déjà un compte de copie. Son remplacement nécessite une vérification séparée des positions.')
        accounts = conn.run("SELECT login,server FROM trading_accounts WHERE id=:account AND user_id=:code AND provider='MT5' AND platform='MT5' FOR SHARE", account=data['account_id'], code=code)
        if not accounts:
            raise ValueError('Ce compte MT5 n’appartient pas à ce membre.')
        chosen = None
        if data.get('master_revision') is not None:
            if not trading_access_state(member)[0]:
                raise ValueError('L’accès à l’Académie est inactif. Le rattachement est refusé.')
            masters = conn.run('SELECT revision,login,server,environment FROM copy_admin_master WHERE id=1 FOR SHARE')
            if not masters or masters[0][0] != data['master_revision']:
                raise ValueError('Le maître a changé. Recharge la sélection.')
            if tuple(accounts[0]) == tuple(masters[0][1:3]):
                raise ValueError('Le maître ne peut pas suivre son propre compte.')
            chosen = dict(zip(('login','server','environment'), masters[0][1:]))
        if old and 'master_assignment' in old and old['master_assignment'] == chosen:
            from .onboarding_server import enqueue
            enqueue(conn,code,version,data['account_id'],chosen is not None)
            conn.run('COMMIT')
            return dict(version=version, unchanged=True, execution_enabled=False)
        if old is None and chosen is None:
            raise ValueError('Aucun rattachement à retirer.')
        # Keep existing lot and risk values. A legacy request is not permission to start.
        payload = dict(old) if old else dict(account_id=data['account_id'],login=accounts[0][0],server=accounts[0][1],
            settings=validate(decoded(member.get('params')) or {}),requested_enabled=False,mode='SIMULATION')
        if (payload['login'],payload['server']) != tuple(accounts[0]):
            raise ValueError('L’identité du compte a changé. Vérifie sa connexion.')
        payload['master_assignment'] = chosen
        conn.run("UPDATE copy_configurations SET status='SUPERSEDED',lease_token=NULL,lease_until=NULL WHERE member_code=:code AND status='PENDING'", code=code)
        conn.run("""INSERT INTO copy_configurations(member_code,version,account_id,payload,digest,status)
            VALUES(:code,:version,:account,CAST(:payload AS JSONB),:digest,'PENDING')""", code=code,version=version+1,account=data['account_id'],payload=json.dumps(payload,allow_nan=False),digest=digest(payload))
        # The authenticated master assignment is the administrative permission.
        # It is not member consent: the real control remains stopped until ON.
        conn.run("""INSERT INTO copy_admin_controls(member_code,version,decision) VALUES(:code,:version,:decision)
            ON CONFLICT(member_code) DO UPDATE SET version=EXCLUDED.version,decision=EXCLUDED.decision,updated_at=NOW()""",
            code=code,version=version+1,decision='APPROVED' if chosen is not None else 'PAUSED')
        from .onboarding_server import enqueue
        enqueue(conn,code,version+1,data['account_id'],chosen is not None)
        service.event(conn,'MASTER_ASSIGNED' if chosen else 'MASTER_REMOVED',code,actor)
        conn.run('COMMIT')
        return dict(version=version+1,unchanged=False,execution_enabled=False)
    except Exception:
        conn.run('ROLLBACK'); raise
    finally:
        conn.close()
