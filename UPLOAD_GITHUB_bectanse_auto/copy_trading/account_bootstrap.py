"""Create an unqualified account runtime from an explicit host policy.

No customer manifest is used as a template. Only the shared source location and
host limits come from the operator policy; account identity comes from the
authenticated assignment and the connected terminal.
"""
import hashlib
import json
import time
from pathlib import Path
from .atomic_io import write_json
from .fleet_store import SourceReader
from .broker_clock import offset_seconds


def prepare(path, policy_path, session, plan, state, *, clock=time.time):
    path, state = Path(path), Path(state)
    if path.exists() or path.is_symlink():
        return  # RuntimeBuilder validates an existing binding, never overwrites it.
    policy = json.loads(Path(policy_path).read_text(encoding='utf-8-sig'))
    if set(policy) != {'schema', 'masters', 'limits', 'server_offsets'} or policy['schema'] != 1:
        raise ValueError('INVALID_ACCOUNT_BOOTSTRAP_POLICY')
    identity = plan['identity']
    account, terminal = session.module.account_info(), session.module.terminal_info()
    if (account is None or terminal is None or not terminal.connected
        or (str(account.login), account.server, account.trade_mode) !=
           (identity['login'], identity['server'], 2)):
        raise ValueError('BOOTSTRAP_NATIVE_IDENTITY_MISMATCH')
    master = {k: plan['master'][k] for k in ('login', 'server')}
    source = str(master['login']) + '@' + master['server']
    entry = policy['masters'].get(source)
    if not entry or set(entry) != {'source_ledger', 'broker_time_offset_seconds'}:
        raise ValueError('BOOTSTRAP_MASTER_NOT_CONFIGURED')
    # Do not guess a broker clock from its latest quote or another customer's broker.
    if account.server in policy['server_offsets']:
        follower_offset=offset_seconds(policy['server_offsets'][account.server])
    else:
        from .instrument_collection import NativeSessions
        try: follower_offset=NativeSessions(session.module,clock).clock_offset()
        except Exception: raise ValueError('BOOTSTRAP_SERVER_CLOCK_REQUIRED') from None
    offsets = {'master': offset_seconds(entry['broker_time_offset_seconds']),
               'follower': follower_offset}
    reader = SourceReader(entry['source_ledger'])
    try:
        snapshot = reader.read(source)
    finally:
        reader.close()
    capture = (snapshot or {}).get('instrument_metadata', {})
    if (capture.get('identity') != {'account': source, 'trade_mode': 0}
        or not 0 <= clock() - capture.get('observed_at', 0) <= 5
        or not capture.get('broker')):
        raise ValueError('BOOTSTRAP_MASTER_NATIVE_EVIDENCE_REQUIRED')
    modes = {0: 'NETTING', 1: 'EXCHANGE', 2: 'HEDGING'}
    source_mode = modes.get(capture.get('account', {}).get('margin_mode'))
    target_mode = modes.get(account.margin_mode)
    if not source_mode or not target_mode:
        raise ValueError('BOOTSTRAP_POSITION_MODEL_UNKNOWN')
    root = state / 'instrument-preparation'
    root.mkdir(parents=True, exist_ok=True)
    matrix = root / 'unqualified.json'
    if not matrix.exists():
        write_json(matrix, {'schema_version': 1, 'revision': 1, 'records': []})
    data = dict(schema=1,
        identity={k: identity[k] for k in ('member_code', 'account_id', 'login', 'server')},
        master=master, source_ledger=entry['source_ledger'], matrix=str(matrix),
        matrix_sha256=hashlib.sha256(matrix.read_bytes()).hexdigest(),
        mapping_registry=str(root / 'mapping.sqlite'), mapping_head='GENESIS',
        profile=dict(environment='DEMO_TO_REAL', source_broker=capture['broker'],
            source_server=master['server'], source_account_type=source_mode,
            target_broker=account.company, target_server=account.server, target_account_type=target_mode),
        source_evidence=str(root / 'source.json'), target_evidence=str(root / 'target.json'),
        assets=str(root / 'assets.json'), symbols={}, limits=policy['limits'], broker_time_offsets=offsets)
    # Validate the complete manifest before publishing it. Staging is per-account;
    # the existing RealSession account lock spans this method and the executor.
    from .real_runtime import RuntimeBuilder
    candidate = root / 'candidate.json'
    write_json(candidate, data)
    RuntimeBuilder(candidate).load(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)
