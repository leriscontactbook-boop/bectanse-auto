"""Per-follower adapter around the proven demo engine, with delivery evidence."""
import json
import os
import time

from .demo_policy import SafetyError, decimal
from .demo_runtime import DEFAULT_LIMITS
from .fleet_store import FleetStore
from .symbols import MAX_SYMBOLS

STRICT_LIMITS = {**DEFAULT_LIMITS, 'max_lots_per_trade': 0.01,
                 'max_total_lots': 0.01, 'max_positions': 1, 'max_spread_points': 150}


def validate_policy(manifest):
    symbols = manifest.get('symbols')
    if (manifest.get('mode') != 'DEMO_ONLY' or not isinstance(symbols, dict)
            or not symbols or len(symbols) > MAX_SYMBOLS
            or any(not isinstance(name, str) or not name.strip() or len(name) > 64
                   for pair in symbols.items() for name in pair)):
        raise ValueError('FLEET_INSTRUMENT_OR_ENVIRONMENT_FORBIDDEN')
    limits = manifest['limits']
    for key in ('require_stop_loss', 'enforce_spread_limit', 'enforce_exposure_limits', 'enforce_master_price_limit', 'exact_member_volume'):
        if type(limits.get(key)) is not bool:
            raise ValueError('FLEET_PROTECTION_REQUIRED')
    if limits.get('allow_broker_minimum') is not False:
        raise ValueError('FLEET_MINIMUM_OVERRIDE_FORBIDDEN')
    for key, ceiling in STRICT_LIMITS.items():
        if type(ceiling) is bool:
            continue
        if not 0 < decimal(limits[key]) <= decimal(ceiling):
            raise ValueError('FLEET_LIMIT_EXCEEDED')


class FleetEngine:
    def __init__(self, engine, path, identity):
        self.engine, self.identity = engine, identity
        self.fleet = FleetStore(path, clock=engine.clock)
        row = self.fleet.follower(identity)
        if not row or row['source'] != engine.source or row['owner'] != engine.owner:
            self.fleet.close()
            raise ValueError('FLEET_ACCOUNT_MISMATCH')

    @property
    def recovery_required(self):
        return self.engine.recovery_required

    def suspend(self, reason):
        self.engine.suspend(reason)
        self.fleet.health(self.identity, {'state': 'BLOCKED', 'error': reason, 'pid': os.getpid()})

    def _report(self, errors):
        return self.engine._report(errors)

    def cycle(self, configuration=None):
        engine = self.engine
        snapshot = self.fleet.source(engine.source)
        if snapshot:
            # Preserve original observation time and continuity; no historical replay.
            engine.ledger.publish(engine.source, snapshot, [])
        pending_before = self.fleet.pending(self.identity)
        if hasattr(engine.ledger, 'events'):
            engine.ledger.events = {(e['source_id'], e['kind']): e['id'] for e in pending_before}
        follower = self.fleet.follower(self.identity)
        if not follower or follower['blocked'] or not follower['enabled']:
            configuration = None
        try:
            report = engine.cycle(configuration)
        except SafetyError as error:
            self.fleet.health(self.identity, {'state': 'BLOCKED', 'error': str(error), 'pid': os.getpid()})
            raise
        links = engine.ledger.links(engine.owner)
        last_copy = last_reconciled = None
        for event in self.fleet.pending(self.identity):
            link = links.get(event['source_id'])
            if not link:
                # A signal closed before this executor observed it is never backfilled.
                if snapshot and event['source_id'] in snapshot['closed']:
                    self.fleet.resolve(self.identity, event['id'], 'REJECTED', 'CLOSED_BEFORE_OBSERVATION')
                continue
            if link.get('pending'):
                self.fleet.resolve(self.identity, event['id'], 'UNCERTAIN', 'RECONCILIATION_REQUIRED')
                continue
            if link.get('state') == 'SKIPPED':
                self.fleet.resolve(self.identity, event['id'], 'REJECTED', link.get('error', 'POLICY_REJECTED'))
                continue
            # Provider confirmation is in the isolated intent ledger. Detection or
            # a live Python process is never evidence of a successful broker order.
            commands = engine.ledger.db.execute("""SELECT value FROM intents WHERE owner=?
                AND source_id=? AND created_at>=? ORDER BY created_at DESC""",
                (engine.owner, event['source_id'], event['observed_at'])).fetchall()
            command = next((json.loads(c[0]) for c in commands
                            if json.loads(c[0])['kind'] == event['kind']
                            and json.loads(c[0]).get('event_id') == event['id']), None)
            if command and command['state'] == 'CONFIRMED':
                self.fleet.resolve(self.identity, event['id'], 'EXECUTED')
                last_copy = event['id']
                last_reconciled = engine.clock()
            elif command and command['state'] in {'REJECTED', 'NOT_SENT'}:
                self.fleet.resolve(self.identity, event['id'], 'REJECTED', link.get('error', command['state']))
            elif snapshot and snapshot['observed_at'] > event['observed_at']:
                # Intermediate stop/volume updates can be superseded by a more recent
                # master state. Explicitly report coalescing, never claim execution.
                current = snapshot['positions'].get(event['source_id'])
                if current != json.loads(event['payload']):
                    self.fleet.resolve(self.identity, event['id'], 'SUPERSEDED', 'NEWER_MASTER_STATE')
                elif event['kind'] != 'OPEN' and not link.get('error'):
                    self.fleet.resolve(self.identity, event['id'], 'RECONCILED', 'NO_ORDER_NEEDED')
                    last_reconciled = engine.clock()
        old = json.loads((follower or {}).get('health', '{}'))
        pending = self.fleet.pending(self.identity)
        error = (follower or {}).get('blocked') or (report['errors'][0] if report['errors'] else '')
        self.fleet.health(self.identity, {'state': 'BLOCKED' if error else 'CONNECTED',
            'error': error, 'demo_confirmed': True, 'version': (configuration or {}).get('version', 0),
            'open_positions': report['open_positions'], 'pending_commands': report['pending_commands'],
            'last_signal': pending_before[-1]['id'] if pending_before else old.get('last_signal'),
            'last_copy': last_copy or old.get('last_copy'),
            'last_reconciliation': last_reconciled or old.get('last_reconciliation'),
            'lag_seconds': engine.clock() - snapshot['observed_at'] if snapshot else None,
            'pid': os.getpid()})
        return report

    def close(self):
        self.fleet.close()
