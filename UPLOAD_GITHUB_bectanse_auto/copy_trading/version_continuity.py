"""Carry an existing decision and ON/OFF intent across settings-only versions.

Caller holds the member row lock. Technical proofs and ACTIVE acknowledgements
are never carried: the executor must qualify and acknowledge the new version.
"""
from .service import decoded
from .settings import validate


def synchronize(conn, code):
    rows=conn.run('SELECT version,account_id,payload FROM copy_configurations WHERE member_code=:m ORDER BY version',m=code)
    if not rows:return False
    latest,account,raw=rows[-1];payload=decoded(raw)
    try:
        validate(payload['settings'])
    except ValueError:
        # Do not carry authorizations to invalid settings. Enqueue must still
        # permit a legacy member pause, which deliberately preserves those
        # settings while recording the explicit OFF request.
        return False
    def continuous(start):
        chain=[(v,a,decoded(p)) for v,a,p in rows if v>=start]
        if not chain or chain[0][0]!=start:return False
        if [v for v,_,_ in chain]!=list(range(start,latest+1)):return False
        identity=lambda p:{k:v for k,v in p.items() if k not in {'settings','requested_enabled'}}
        return all(a==account and identity(p)==identity(payload) for _,a,p in chain)
    changed=False
    approvals=conn.run('SELECT version,decision FROM copy_admin_controls WHERE member_code=:m',m=code)
    if approvals and approvals[0][0]<latest and continuous(approvals[0][0]):
        conn.run('UPDATE copy_admin_controls SET version=:v,updated_at=NOW() WHERE member_code=:m',v=latest,m=code)
        changed=True
    controls=conn.run('SELECT version,account_id FROM copy_real_controls WHERE member_code=:m',m=code)
    if controls and controls[0][0]<latest and controls[0][1]==account and continuous(controls[0][0]):
        conn.run("""UPDATE copy_real_controls SET version=:v,revision=revision+1,
            confirmed='REQUEST_RECEIVED',confirmed_at=NOW() WHERE member_code=:m""",v=latest,m=code)
        changed=True
    if changed:
        conn.run("INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:m,'policy:SETTINGS_VERSION_CONTINUITY')",a='SETTINGS_VERSION_SYNCHRONIZED:'+str(latest),m=code)
    return changed
