"""Read-only evidence classification. Never infer broker state from a heartbeat."""
from decimal import Decimal


def reconcile(account, ledger, source=False):
    issues = []
    if account.get('error') or not all(account.get(k) is True for k in
            ('demo', 'connected', 'identity_matches', 'terminal_matches')):
        return {'status': 'UNVERIFIED', 'issues': ['BROKER_IDENTITY_OR_CONNECTION_UNVERIFIED']}
    positions = account.get('positions')
    if positions is None or account.get('pending_orders') is None:
        return {'status': 'UNVERIFIED', 'issues': ['BROKER_READ_FAILED']}
    if ledger.get('error') or ledger.get('integrity') != 'ok':
        return {'status': 'UNVERIFIED', 'issues': ['LEDGER_READ_FAILED']}
    if ledger.get('changed_during_probe'):
        return {'status': 'UNVERIFIED', 'issues': ['CONCURRENT_CHANGE_REQUIRES_NEW_SAMPLE']}
    actual = {str(p['identifier']): p for p in positions}
    if account['pending_orders']:
        issues.append('BROKER_PENDING_ORDERS')
    if source:
        expected = ledger.get('source_positions')
        if expected is None or ledger.get('source_age', float('inf')) > 3:
            return {'status': 'UNVERIFIED', 'issues': ['SOURCE_SNAPSHOT_STALE_OR_MISSING']}
        expected = {str(k): v for k, v in expected.items()}
        if ledger.get('source_unresolved'):
            issues.append('SOURCE_UNRESOLVED')
    else:
        links = ledger.get('links', {})
        expected = {str(v['position_id']): v for v in links.values()
                    if v.get('state') == 'OPEN' and v.get('position_id')}
        if any(v.get('pending') or v.get('state') == 'OPENING' for v in links.values()):
            issues.append('EXECUTION_UNCERTAIN')
        if any(v.get('required') for v in ledger.get('recovery', {}).values()):
            issues.append('RECOVERY_REQUIRED')
        if any(v.get('error') for v in expected.values()):
            issues.append('ACTIVE_LINK_ERROR')
    for identity in sorted(set(actual) - set(expected)):
        issues.append('UNTRACKED_BROKER_POSITION:' + identity)
    for identity in sorted(set(expected) - set(actual)):
        issues.append('MISSING_BROKER_POSITION:' + identity)
    for identity in set(actual) & set(expected):
        p, link = actual[identity], expected[identity]
        for key, value in [('symbol', p['symbol']), ('side', 'BUY' if p['type'] == 0 else 'SELL')]:
            if link.get(key) != value:
                issues.append('POSITION_IDENTITY_MISMATCH:' + identity)
        if not source and link.get('magic') != p.get('magic'):
            issues.append('POSITION_MAGIC_MISMATCH:' + identity)
        targets = link if source else ledger.get('targets', {}).get(identity)
        if targets is None:
            issues.append('POSITION_TARGET_UNVERIFIED:' + identity)
        else:
            for field in ('volume', 'sl', 'tp'):
                if field not in targets or abs(Decimal(str(p[field])) - Decimal(str(targets[field]))) > Decimal('0.00000001'):
                    issues.append('POSITION_' + field.upper() + '_MISMATCH:' + identity)
    return {'status': 'DIVERGENT' if issues else 'RECONCILED', 'issues': sorted(set(issues)),
            'broker_positions': len(actual), 'ledger_positions': len(expected)}
