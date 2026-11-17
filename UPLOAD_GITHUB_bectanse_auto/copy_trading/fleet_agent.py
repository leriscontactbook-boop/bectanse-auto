"""Sidecar multi-follower supervisor. Reuses the live master observer read-only.

No second observer, no edits to the legacy manifest/ledger, no new services.
One independently supervised child process per follower; one dedicated terminal
and local execution ledger per identity. Central authorization remains the
existing signed agent/configuration APIs and explicit operator bindings.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import psutil

from .agent import Supervisor, fresh_plan, child_health_matches
from .atomic_io import write_json
from .demo_runtime import DemoBackend, ProcessLocks, account_identity, read_manifest
from .fleet_engine import STRICT_LIMITS, validate_policy
from .fleet_store import FleetStore, SourceReader


def read_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(data) != {'mode', 'source_ledger', 'master', 'max_followers', 'followers'} or data['mode'] != 'DEMO_ONLY':
        raise ValueError('INVALID_FLEET_CONFIG')
    if type(data['max_followers']) is not int or not 1 <= data['max_followers'] <= 20:
        raise ValueError('INVALID_FLEET_CAPACITY')
    if not isinstance(data['followers'], list) or len(data['followers']) > data['max_followers']:
        raise ValueError('FLEET_CAPACITY_EXCEEDED')
    if (set(data['master']) != {'login', 'server', 'terminal'}
            or not re.fullmatch(r'[0-9]{3,20}', str(data['master']['login']))):
        raise ValueError('INVALID_MASTER_IDENTITY')
    workers, accounts, members = set(), set(), set()
    for follower in data['followers']:
        if (not {'worker_id', 'member_code', 'account_id', 'enabled'} <= set(follower)
                or set(follower) - {'worker_id', 'member_code', 'account_id', 'enabled', 'limits', 'broker_time_offsets', 'symbol_mapping'}
                or not isinstance(follower['worker_id'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', follower['worker_id'])
                or type(follower['account_id']) is not int or follower['account_id'] <= 0
                or not isinstance(follower['member_code'], str) or not follower['member_code']
                or type(follower['enabled']) is not bool):
            raise ValueError('INVALID_FOLLOWER_BINDING')
        if not isinstance(follower.get('limits', {}), dict) or set(follower.get('limits', {})) - set(STRICT_LIMITS):
            raise ValueError('INVALID_FOLLOWER_LIMITS')
        validate_policy({'mode': 'DEMO_ONLY', 'symbols': follower.get('symbol_mapping', {'__validation__': '__validation__'}),
                         'limits': {**STRICT_LIMITS, **follower.get('limits', {})}})
        for value, seen in ((follower['worker_id'], workers), (follower['account_id'], accounts), (follower['member_code'], members)):
            if value in seen:
                raise ValueError('DUPLICATE_FOLLOWER_BINDING')
            seen.add(value)
    return data


def make_follower_manifest(plan, config, root, follower):
    if (plan['state'] != 'READY' or plan['account_id'] != follower['account_id']
            or plan.get('member_code') != follower['member_code']):
        raise ValueError('FOLLOWER_BINDING_MISMATCH')
    if account_identity(plan['master']) != account_identity(config['master']):
        raise ValueError('SHARED_MASTER_MISMATCH')
    # Preserve account-specific mappings supplied by the authenticated plan.
    # Instrument names do not constitute a fleet-wide market restriction.
    symbols = dict(follower.get('symbol_mapping', plan['symbols']))
    manifest = {'mode': 'DEMO_ONLY', 'account_id': follower['account_id'], 'plan_id': plan['plan_id'],
        'master_revision': plan['master_revision'], 'master': config['master'],
        'follower': {k: plan['follower'][k] for k in ('login', 'server')},
        'symbols': symbols, 'limits': {**STRICT_LIMITS, **follower.get('limits', {})},
        'broker_time_offsets': follower.get('broker_time_offsets', json.loads(os.environ.get('COPY_DEMO_BROKER_TIME_OFFSETS', '{"master":0,"follower":0}')))}
    manifest['follower']['terminal'] = str(root / 'Followers' / follower['worker_id'] / 'Terminal' / 'terminal64.exe')
    validate_policy(manifest)
    return manifest


class FollowerSupervisor(Supervisor):
    """Reuse proven child health/reaping; never call the legacy observer spawn path."""
    def __init__(self, root, config, follower, credentials, url):
        self.fleet_root, self.config, self.follower = root, config, follower
        self.credentials, self.url = credentials, url
        work = root / 'Followers' / follower['worker_id']
        (work / 'State').mkdir(parents=True, exist_ok=True)
        backend = DemoBackend(url=url, secret=credentials['agent'], identity=follower['worker_id'])
        backend.timeout = (2, 5)
        super().__init__(work, backend)
        self.validator = None  # Existing central validation is authoritative.
        self.backend.instance = self.instance
        self.lock = ProcessLocks({'login': 'fleet-worker', 'server': follower['worker_id'],
                                  'terminal': str(work / 'supervisor.exe')})

    def step(self):
        self.reap_stalled()
        plan = fresh_plan(self.backend.request('/internal/copy/agent/poll',
            {'instance_id': self.instance, 'report': self.report(), 'follower_only': True}))
        if plan['state'] != 'READY':
            self.state = plan['state']
            return
        manifest = make_follower_manifest(plan, self.config, self.fleet_root, self.follower)
        if self.plan_id and self.plan_id != plan['plan_id']:
            self.state = 'PLAN_CHANGED_OPERATOR_REQUIRED'
            return
        path = self.root / 'State' / 'manifest.json'
        if path.exists():
            previous = json.loads(path.read_text())
            if any(previous.get(k) != manifest[k] for k in ('limits', 'broker_time_offsets')):
                fleet = FleetStore(self.fleet_root / 'State' / 'fleet.sqlite3')
                try:
                    with fleet.db:
                        fleet.db.execute("UPDATE fleet_followers SET blocked='POLICY_CHANGED_OPERATOR_REQUIRED' WHERE id=?",
                                         (self.follower['worker_id'],))
                finally:
                    fleet.close()
                self.state = 'POLICY_CHANGED_OPERATOR_REQUIRED'
                return
        write_json(path, manifest)
        read_manifest(path)
        fleet = FleetStore(self.fleet_root / 'State' / 'fleet.sqlite3')
        try:
            fleet.register(self.follower['worker_id'], self.follower['member_code'], self.follower['account_id'],
                           account_identity(manifest['master']), account_identity(manifest['follower']), self.follower['enabled'])
        finally:
            fleet.close()
        self.plan_id = plan['plan_id']
        if self.alive('execute'):
            try:
                h = json.loads((self.root / 'State' / 'execute-health.json').read_text())
                self.state = 'TERMINALS_CONNECTED' if child_health_matches(h, self.children['execute']) and h['state'] == 'CONNECTED' and 0 <= time.time() - h['at'] <= 15 else 'TERMINALS_NOT_READY'
            except (OSError, ValueError, KeyError):
                self.state = 'TERMINALS_NOT_READY'
            return
        if not self.armed:
            self.state = 'WAITING_EXECUTION_ARMING'
            return
        if self.clock() < self.next_start:
            return
        # Never clone a terminal that contains another account's saved credentials.
        terminal = Path(manifest['follower']['terminal'])
        if not terminal.is_file():
            from .fleet_terminal import provision
            try:
                provision(self.fleet_root / 'TerminalTemplate', terminal.parent)
            except (ValueError, OSError):
                self.state = 'WAITING_DEDICATED_TERMINAL'
                return
        try:
            old = json.loads((self.root / 'State' / 'execute-health.json').read_text())
        except (OSError, ValueError):
            old = {}
        if old.get('error') in {'REAL_TRADING_FORBIDDEN', 'ACCOUNT_IDENTITY_CHANGED', 'HEDGING_REQUIRED',
                                 'TERMINAL_IDENTITY_CHANGED', 'FIFO_UNSUPPORTED', 'LOCAL_STATE_INVALID'}:
            self.state = 'TERMINAL_SAFETY_BLOCK'
            return
        env = {k: v for k, v in os.environ.items() if not k.startswith('COPY_')}
        env.update(COPY_CONFIG_BACKEND_URL=self.url, COPY_DEMO_WORKER_ID=self.follower['worker_id'],
                   COPY_DEMO_INSTANCE_ID=self.instance, COPY_DEMO_WORKER_SECRET=self.credentials['demo'],
                   COPY_DEMO_EXECUTION='I_CONFIRM_DEMO_ONLY')
        child = self.spawn([sys.executable, '-m', 'copy_trading.demo_runtime', 'execute',
            '--manifest', str(path), '--state', str(self.root / 'State' / 'ledger.sqlite3'),
            '--credentials-stdin', '--health-file', str(self.root / 'State' / 'execute-health.json'),
            '--fleet-state', str(self.fleet_root / 'State' / 'fleet.sqlite3'), '--follower-id', self.follower['worker_id'],
            '--parent-pid', str(os.getpid()), '--parent-start', str(psutil.Process().create_time())],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, text=True)
        try:
            child.stdin.write(json.dumps({'password': plan['follower']['password']}))
            child.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        self.children['execute'] = child
        self.started_at['execute'] = self.clock()
        self.failures += 1
        self.next_start = self.clock() + min(60, 2 ** min(self.failures, 6))
        self.state = 'STARTING_TERMINALS'

    def poll(self):
        try:
            self.step()
        except Exception as error:
            # Record only type and source locations; exception text can contain secrets.
            import traceback
            frames = traceback.extract_tb(error.__traceback__)
            locations = [(Path(f.filename).name, f.lineno, f.name) for f in frames]
            self.log.warning('follower_cycle_failed worker=%s category=%s locations=%s',
                             self.follower['worker_id'], type(error).__name__, locations)
            self.state = 'FOLLOWER_SERVICE_UNAVAILABLE'
        write_json(self.root / 'State' / 'agent-health.json',
                   {'at': time.time(), 'pid': os.getpid(), **self.report()})

    def close(self):
        super().close()
        self.lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    root = Path(args.root)
    if os.name != 'nt' or not root.is_absolute():
        raise SystemExit('WINDOWS_AND_ABSOLUTE_ROOT_REQUIRED')
    config = read_config(args.config)
    for folder in ('State', 'logs', 'Followers'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(root / 'logs' / 'fleet.log', maxBytes=2000000,
                                  backupCount=3, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)sZ %(levelname)s %(name)s %(message)s',
                                  datefmt='%Y-%m-%dT%H:%M:%S')
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.info('fleet_started mode=DEMO_ONLY release=%s', Path(__file__).resolve().parent.parent.name)
    locks = ProcessLocks({'login': 'fleet', 'server': 'dispatcher', 'terminal': str(root / 'fleet.exe')})
    store = FleetStore(root / 'State' / 'fleet.sqlite3')
    reader = SourceReader(config['source_ledger'])
    # Only dedicated worker API secrets, never MT5 passwords, in the existing env mechanism.
    def worker_secrets():
        value = os.environ.get('COPY_FLEET_WORKER_SECRETS', '{}')
        private = root / '.agent.env'
        if private.is_file():
            for line in private.read_text(encoding='utf-8').splitlines():
                if line.startswith('COPY_FLEET_WORKER_SECRETS='):
                    value = line.split('=', 1)[1]
        return json.loads(value)
    supervisors = []
    futures = {}
    due = {}
    # One bounded network poll per admitted follower; an unresponsive account must
    # never consume another follower's lease-refresh slot.
    pool = ThreadPoolExecutor(max_workers=max(1, config['max_followers']))
    try:
        next_enrollment = 0
        while True:
            if time.monotonic() >= next_enrollment:
                try:
                    latest = read_config(args.config)
                    if any(latest[k] != config[k] for k in ('master', 'source_ledger', 'max_followers')):
                        raise ValueError('FLEET_TOPOLOGY_CHANGE_REQUIRES_REVIEW')
                    registered = {s.follower['worker_id']: s for s in supervisors}
                    requested = {f['worker_id']: f for f in latest['followers']}
                    for worker, supervisor in registered.items():
                        follower = requested.get(worker)
                        if follower and any(follower[k] != supervisor.follower[k] for k in ('account_id', 'member_code')):
                            raise ValueError('FOLLOWER_IDENTITY_CHANGED')
                        supervisor.follower = follower or {**supervisor.follower, 'enabled': False}
                    for worker, follower in requested.items():
                        if worker in registered:
                            continue
                        if len(supervisors) >= config['max_followers']:
                            raise ValueError('FLEET_CAPACITY_EXCEEDED')
                        secret = worker_secrets().get(worker, {})
                        if set(secret) != {'agent', 'demo'} or any(not isinstance(v, str) or len(v) < 32 for v in secret.values()):
                            logging.warning('fleet_enrollment worker=%s state=MISSING_API_SECRETS', worker)
                            continue
                        supervisors.append(FollowerSupervisor(root, config, follower, secret, os.environ['COPY_CONFIG_BACKEND_URL']))
                except Exception:
                    logging.warning('fleet_enrollment state=CONFIGURATION_REJECTED')
                next_enrollment = time.monotonic() + 5
            try:
                snapshot = reader.read(account_identity(config['master']))
                if snapshot:
                    store.publish(account_identity(config['master']), snapshot)
                state = 'SOURCE_CONNECTED' if snapshot and 0 <= time.time() - snapshot['observed_at'] <= 3 else 'SOURCE_STALE'
            except Exception:
                state = 'SOURCE_UNAVAILABLE'
            for supervisor in supervisors:
                worker = supervisor.follower['worker_id']
                if worker in futures and not futures[worker].done():
                    continue
                if time.monotonic() >= due.get(worker, 0):
                    futures[worker] = pool.submit(supervisor.poll)
                    due[worker] = time.monotonic() + 5
            write_json(root / 'State' / 'fleet-health.json', {'at': time.time(), 'pid': os.getpid(),
                'state': state, 'followers_configured': len(supervisors), 'max_followers': config['max_followers'],
                'mode': 'DEMO_ONLY'})
            if args.once:
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        pool.shutdown(wait=True)
        for supervisor in supervisors:
            supervisor.close()
        reader.close()
        store.close()
        locks.close()


if __name__ == '__main__':
    main()
