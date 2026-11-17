"""Operator CLI for additive fleet enrollment and redacted health inspection.

Existing backend bindings/approval and encrypted account credentials are required.
This command cannot turn a journal account into an authorized trading account.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import time

from .atomic_io import write_json
from .demo_runtime import ProcessLocks
from .fleet_agent import read_config
from .fleet_store import FleetStore


def add(config_path, worker, member, account, *, enabled=False):
    path = Path(config_path)
    lock = ProcessLocks({'login': 'fleet-config', 'server': str(path.resolve()), 'terminal': str(path.parent / 'config-editor.exe')})
    try:
        config = read_config(path)
        entry = {'worker_id': worker, 'member_code': member, 'account_id': account, 'enabled': enabled}
        old = next((x for x in config['followers'] if x['worker_id'] == worker), None)
        if old == entry:
            return False
        if old:
            raise ValueError('EXISTING_FOLLOWER_REQUIRES_REVIEW')
        config['followers'].append(entry)
        # Validate a candidate without replacing the live configuration first.
        candidate = path.with_name(path.name + '.candidate')
        try:
            write_json(candidate, config)
            read_config(candidate)
            backup = path.with_name(path.name + '.' + str(time.time_ns()) + '.backup')
            shutil.copy2(path, backup)
            os.replace(candidate, path)
        finally:
            candidate.unlink(missing_ok=True)
        return True
    finally:
        lock.close()


def status(root):
    path = Path(root) / 'State' / 'fleet.sqlite3'
    if not path.is_file():
        return {'state': 'NOT_INSTALLED', 'followers': []}
    store = FleetStore(path)
    try:
        followers = []
        for row in store.db.execute('SELECT * FROM fleet_followers ORDER BY id'):
            age = time.time() - row['health_at'] if row['health_at'] is not None else None
            health = json.loads(row['health'])
            followers.append({'worker_id': row['id'], 'account_id': row['account_id'],
                'enabled': bool(row['enabled']), 'blocked': row['blocked'],
                'online': age is not None and 0 <= age <= 15, 'health_age': age,
                'health': health, 'pending_deliveries': len(store.pending(row['id']))})
        return {'mode': 'DEMO_ONLY', 'followers': followers}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('add')
    p.add_argument('--config', required=True);p.add_argument('--worker', required=True)
    p.add_argument('--member', required=True);p.add_argument('--account', required=True, type=int)
    p.add_argument('--enabled', action='store_true')
    p = commands.add_parser('status');p.add_argument('--root', required=True)
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(status(args.root), indent=2))
    else:
        changed = add(args.config, args.worker, args.member, args.account, enabled=args.enabled)
        print(json.dumps({'registered': True, 'changed': changed,
            'execution_authorized': False, 'next': 'BACKEND_BINDING_AND_DEDICATED_TERMINAL_REQUIRED'}))


if __name__ == '__main__':
    main()
