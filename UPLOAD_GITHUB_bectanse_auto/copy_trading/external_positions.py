"""Isolate explicitly reviewed pre-existing hedging positions, never adopt them.

The inventory is bound to one native account and immutable position identifiers.
Unknown positions/orders and every Bectanse-owned position still reconcile normally.
External exposure remains included in account risk limits.
"""
from .demo_policy import SafetyError, decimal


def isolated(broker, positions, links):
    policy=getattr(broker,'external_position_inventory',None)
    personal=getattr(broker,'personal_position_ids',None)
    if not policy and not personal:return set()
    account=broker.guard()
    if ((policy and str(account.login)+'@'+account.server!=policy.get('account'))
        or account.margin_mode!=2):
        raise SafetyError('EXTERNAL_POSITION_ACCOUNT_MISMATCH')
    owned_ids={str(v.get('position_id')) for v in links.values() if v.get('position_id')}
    owned_magics={v.get('magic') for v in links.values() if v.get('magic') is not None}
    result=set(personal(positions) if personal else ())
    # Ownership always wins, including closed links and in-flight copies.
    result={pid for pid in result if pid not in owned_ids
            and positions[pid].get('magic') not in owned_magics}
    for pid,expected in (policy or {}).get('positions',{}).items():
        if pid in owned_ids or expected.get('magic') in owned_magics:
            raise SafetyError('EXTERNAL_POSITION_OWNERSHIP_COLLISION')
        actual=positions.get(pid)
        if actual is None:continue  # A closed external position is never reopened.
        if (str(actual.get('ticket'))!=str(expected.get('ticket'))
            or any(actual.get(k)!=expected.get(k) for k in ('symbol','side','magic'))
            or round((actual.get('opened_at',0)+getattr(broker,'broker_time_offset',0))*1000)!=round((expected.get('opened_at',0)+policy.get('broker_time_offset_seconds',0))*1000)
            or not decimal(0)<decimal(actual['volume'])<=decimal(expected['volume'])):
            raise SafetyError('EXTERNAL_POSITION_IDENTITY_CHANGED')
        result.add(pid)
    return result
