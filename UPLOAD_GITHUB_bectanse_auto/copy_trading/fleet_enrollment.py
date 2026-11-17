"""Prepare a non-destructive backend enrollment patch; never auto-approve trades.

Input/output secrets belong in the existing private deployment mechanism. Never
log this return value. MT5 credentials remain encrypted in trading_credentials.
The caller must deploy the patch atomically, then admit the dedicated VPS worker.
"""
import json
import re
import secrets

from .symbols import MAX_SYMBOLS


def prepare(conn, environment, *, worker, member, account, symbol_mapping=None):
    if (not isinstance(worker, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', worker)
            or type(account) is not int or account <= 0 or not isinstance(member, str) or not member):
        raise ValueError('INVALID_ENROLLMENT')
    rows = conn.run("""SELECT login,server FROM trading_accounts WHERE id=:account
        AND user_id=:member AND provider='MT5' AND platform='MT5'""", account=account, member=member)
    if not rows:
        raise ValueError('ACCOUNT_NOT_OWNED_BY_MEMBER')
    masters = conn.run("SELECT login,server,environment FROM copy_admin_master WHERE id=1")
    if not masters or masters[0][2] != 'DEMO' or tuple(rows[0]) == tuple(masters[0][:2]):
        raise ValueError('DISTINCT_DEMO_MASTER_REQUIRED')
    # Broker demo status is still independently required by DemoMT5.guard().
    names = ('COPY_DEMO_BINDINGS', 'COPY_AGENT_WORKER_SECRETS', 'COPY_DEMO_WORKER_SECRETS', 'COPY_DEMO_SYMBOLS')
    values = {name: json.loads(environment.get(name, '{}')) for name in names}
    if any(not isinstance(v, dict) for v in values.values()):
        raise ValueError('INVALID_EXISTING_BINDINGS')
    bindings = values['COPY_DEMO_BINDINGS']
    if worker in bindings or any(b.get('account_id') == account or b.get('member_code') == member for b in bindings.values()):
        raise ValueError('ACCOUNT_OR_WORKER_ALREADY_BOUND')
    # Critical compatibility: adding a second worker disables the single-worker
    # fallback in the existing API. Carry the historical secret into its map first.
    demo_keys = values['COPY_DEMO_WORKER_SECRETS']
    for existing in bindings:
        if len(demo_keys.get(existing, '')) < 32:
            fallback = environment.get('COPY_DEMO_WORKER_SECRET', '')
            if len(bindings) != 1 or len(fallback) < 32:
                raise ValueError('EXISTING_WORKER_SECRET_MISSING')
            demo_keys[existing] = fallback
    node_keys = values['COPY_AGENT_WORKER_SECRETS']
    if worker in node_keys or worker in demo_keys:
        raise ValueError('WORKER_SECRET_ALREADY_EXISTS')
    node_keys[worker] = secrets.token_urlsafe(48)
    demo_keys[worker] = secrets.token_urlsafe(48)
    bindings[worker] = {'member_code': member, 'account_id': account}
    # The caller supplies mappings verified for this account, never a fixed market list.
    if (not isinstance(symbol_mapping, dict) or not symbol_mapping
            or len(symbol_mapping) > MAX_SYMBOLS
            or any(not isinstance(name, str) or not name.strip() or len(name) > 64
                   for pair in symbol_mapping.items() for name in pair)):
        raise ValueError('ACCOUNT_SYMBOL_MAPPING_REQUIRED')
    values['COPY_DEMO_SYMBOLS'][worker] = dict(symbol_mapping)
    fleet_workers = json.loads(environment.get('COPY_FLEET_WORKER_IDS', '[]'))
    if not isinstance(fleet_workers, list) or any(not isinstance(w, str) for w in fleet_workers):
        raise ValueError('INVALID_EXISTING_FLEET_WORKERS')
    values['COPY_FLEET_WORKER_IDS'] = sorted(set(fleet_workers + [worker]))
    return {'variables': {k: json.dumps(v, separators=(',', ':')) for k, v in values.items()},
            'vps_worker_secrets': {'agent': node_keys[worker], 'demo': demo_keys[worker]},
            'follower': {'worker_id': worker, 'member_code': member, 'account_id': account, 'enabled': False},
            'requires_demo_identity_check': True, 'requires_dedicated_terminal': True}
