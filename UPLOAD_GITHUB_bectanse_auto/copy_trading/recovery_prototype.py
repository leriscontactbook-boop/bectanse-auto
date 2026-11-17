"""Offline recovery policy. No process, network, MT5 or production integration.

Inputs must come from a trusted independent probe. A returned restart request is
not an execution authorization. Production fencing and actuators are not supplied.
"""
from dataclasses import dataclass, asdict
import json
import sqlite3
import math
import os
from pathlib import Path
import random
import uuid


@dataclass(frozen=True)
class Policy:
    stale: float = 15
    grace: float = 30
    settle: float = 10
    confirmations: int = 2
    max_restarts: int = 3
    restart_window: float = 3600

    def __post_init__(self):
        values = tuple(asdict(self).values())
        if (any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values)
                or type(self.confirmations) is not int or type(self.max_restarts) is not int):
            raise ValueError('INVALID_POLICY')


@dataclass(frozen=True)
class Probe:
    at: float
    instance: str
    generation: int
    alive: bool
    heartbeat_at: float
    progress: int
    independent_at: float
    independent_ok: bool
    dependency_ok: bool
    reconciled: bool
    uncertain: bool = False
    identity_ok: bool = True


class Recovery:
    """Single serialized evaluator, durable decisions and restart budget.

    Instance/generation are pinned by construction, never adopted from heartbeat.
    A new owner must be registered explicitly with independent fencing evidence.
    Restart requests are persisted before any future actuator could consume them.
    Unconsumed requests time out to escalation; they are not issued twice.
    """
    def __init__(self, path, instance='test-instance', generation=1, policy=None,
                 *, initialize=False, random_fraction=None):
        target = Path(path).absolute()
        if target.is_symlink():
            raise ValueError('REGISTRY_SYMLINK_FORBIDDEN')
        if initialize:
            if not instance or type(generation) is not int or generation < 1:
                raise ValueError('INVALID_INITIAL_OWNER')
            selected = policy or Policy()
            # Installation is explicit and never overwrites an existing registry.
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(target.as_uri() + '?mode=rw', uri=True, timeout=3)
        self.random_fraction = random_fraction or random.SystemRandom().random
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            if initialize:
                with self.db:
                    self.db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
                    self.db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, at REAL, value TEXT NOT NULL)')
                    self.db.execute('CREATE TABLE policy(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
                    self.db.execute('INSERT INTO policy VALUES(1,?)', (json.dumps(asdict(selected)),))
                    self.db.execute('INSERT INTO state VALUES(1,?)', (json.dumps(dict(
                        schema=2, instance=instance, generation=generation, last_at=None, progress=None,
                        progressed_at=None, started=None, bad=0, incident=False, incident_id=None,
                        healthy_since=None, healthy_progress=None, restarts=[], request_at=None,
                        restart_not_before=0, mode='INITIALIZING')),))
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('REGISTRY_CORRUPTED')
            stored = Policy(**json.loads(self.db.execute('SELECT value FROM policy WHERE id=1').fetchone()[0]))
            if policy is not None and policy != stored:
                raise ValueError('POLICY_CHANGE_REQUIRES_REVIEW')
            self.policy = stored
            self.state()
        except Exception:
            self.db.close()
            raise

    def state(self):
        s = json.loads(self.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
        if (s.get('schema') != 2 or type(s.get('generation')) is not int or s['generation'] < 1
                or not s.get('instance') or type(s.get('restarts')) is not list
                or any(type(t) not in (float, int) or not math.isfinite(t) for t in s['restarts'])):
            raise ValueError('REGISTRY_INVALID')
        return s

    def register_replacement(self, instance, generation, *, fenced, reconciled, uncertain):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            s = self.state()
            if (fenced is not True or reconciled is not True or uncertain is not False or not instance
                    or type(generation) is not int or instance == s['instance'] or generation != s['generation'] + 1
                    or s['request_at'] is None):
                raise ValueError('REPLACEMENT_NOT_PROVEN')
            s.update(instance=instance, generation=generation, progress=None,
                     progressed_at=None, started=None, bad=0, healthy_since=None, healthy_progress=None,
                     request_at=None, mode='STABILIZING')
            self.db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(s),))

    def observe(self, p):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (p.at, p.heartbeat_at, p.independent_at, p.progress)):
            raise ValueError('INVALID_PROBE')
        if (type(p.progress) is not int or p.progress < 0 or type(p.generation) is not int
                or any(type(v) is not bool for v in (p.alive, p.independent_ok,
                       p.dependency_ok, p.reconciled, p.uncertain, p.identity_ok))):
            raise ValueError('INVALID_PROBE')
        q = self.policy
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            s = self.state()
            action, events, reason = 'NONE', [], 'OK'
            gap = s['last_at'] is not None and p.at - s['last_at'] > q.stale
            if gap:
                s['healthy_since'], s['healthy_progress'] = None, None
            if s['last_at'] is not None and p.at <= s['last_at']:
                reason = 'CLOCK_OR_REPLAY'
            elif not p.identity_ok or (p.instance, p.generation) != (s['instance'], s['generation']):
                reason = 'IDENTITY_OR_GENERATION'
            elif p.uncertain:
                reason = 'UNKNOWN_OPERATION'
            elif not 0 <= p.at - p.independent_at <= q.stale or not p.independent_ok:
                reason = 'INDEPENDENT_MONITOR_UNAVAILABLE'
            elif not p.dependency_ok:
                reason = 'DEPENDENCY_UNAVAILABLE'
            else:
                if s['started'] is None:
                    s['started'] = p.at
                if s['progress'] is not None and p.progress < s['progress']:
                    reason = 'PROGRESS_REGRESSION'
                else:
                    if s['progress'] is None or p.progress > s['progress']:
                        s['progressed_at'] = p.at
                        s['progress'] = p.progress
                    if not p.alive:
                        reason = 'PROCESS_STOPPED'
                    elif not 0 <= p.at - p.heartbeat_at <= q.stale:
                        reason = 'HEARTBEAT_STALE'
                    elif p.at - s['progressed_at'] > q.stale:
                        reason = 'PROGRESS_STALLED'
                    elif not p.reconciled:
                        reason = 'RECONCILIATION_MISSING'
            if reason == 'OK' and gap:
                reason = 'OBSERVATION_GAP'
            restartable = reason in {'PROCESS_STOPPED', 'HEARTBEAT_STALE', 'PROGRESS_STALLED'}
            if reason != 'OK':
                s['healthy_since'] = None
                s['healthy_progress'] = None
                s['bad'] += 1
                if not s['incident'] and s['bad'] >= q.confirmations:
                    s['incident'] = True
                    s['incident_id'] = uuid.uuid4().hex
                    events.append('INCIDENT_DETECTED')
                s['mode'] = 'SUSPECT' if not s['incident'] else 'ISOLATED'
                if s['incident']:
                    action = 'ESCALATE'
                    s['restarts'] = [t for t in s['restarts'] if p.at - t < q.restart_window]
                    if (restartable and p.reconciled and s['request_at'] is None
                            and s['started'] is not None and p.at - s['started'] >= q.grace
                            and p.at >= s['restart_not_before']
                            and len(s['restarts']) < q.max_restarts):
                        action = 'REQUEST_TARGETED_RESTART'
                        s['restarts'].append(p.at)
                        s['request_at'] = p.at
                        s['restart_not_before'] = p.at + reconnect_delay(len(s['restarts']), self.random_fraction())
                        s['mode'] = 'AWAITING_VERIFIED_REPLACEMENT'
            else:
                s['bad'] = 0
                if s['request_at'] is not None:
                    # A healthy old process does not consume/cancel an outstanding action.
                    s['mode'], action = 'AWAITING_VERIFIED_REPLACEMENT', 'ESCALATE'
                else:
                    if s['healthy_since'] is None:
                        s['healthy_since'] = p.at
                        s['healthy_progress'] = p.progress
                    s['mode'] = 'STABILIZING'
                    if p.at - s['healthy_since'] >= q.settle and p.progress > s['healthy_progress']:
                        s['mode'] = 'HEALTHY'
                        if s['incident']:
                            events.append('INCIDENT_RESOLVED')
                            s['incident'] = False
            s['last_at'] = max(p.at, s['last_at'] if s['last_at'] is not None else p.at)
            result = dict(at=p.at, reason=reason, action=action, state=s['mode'], events=events,
                          incident_id=s['incident_id'], generation=s['generation'])
            self.db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(s),))
            # Persist only normalized reasons, no raw broker/credential payloads.
            self.db.execute('INSERT INTO events(at,value) VALUES(?,?)', (p.at, json.dumps(result)))
            return result

    def close(self):
        self.db.close()


def reconnect_delay(failures, random_fraction, base=10, cap=60):
    """Equal jitter; caller supplies randomness to make failure tests deterministic."""
    if failures < 1 or not 0 <= random_fraction <= 1:
        raise ValueError('INVALID_BACKOFF')
    bound = min(cap, base * 2 ** min(failures - 1, 16))
    return bound * (0.5 + random_fraction / 2)


class Admission:
    """Offline normalized load model; defaults closed after every monitor restart.

    Each metric is divided by an explicitly verified node-specific ceiling.
    Samples must be independently timestamped. Missing evidence fails closed.
    No existing account is terminated or migrated by this model.
    """
    metrics = {'cpu', 'memory', 'disk_io', 'latency', 'terminals', 'queue', 'lag', 'connection_errors'}

    def __init__(self):
        self.closed, self.good, self.last_at = True, 0, None

    def observe(self, at, ratios, *, verified_capacity, now, max_age=15):
        finite = lambda x: type(x) in (int, float) and math.isfinite(x)
        dated = finite(at) and finite(now) and finite(max_age) and max_age > 0
        fresh = dated and 0 <= now - at <= max_age and (self.last_at is None or at > self.last_at)
        if not fresh or (self.last_at is not None and at - self.last_at > max_age):
            self.closed, self.good = True, 0
        valid = (set(ratios) == self.metrics and fresh and verified_capacity is True
                 and all(finite(x) and x >= 0 for x in ratios.values()))
        peak = max(ratios.values()) if valid else float('inf')
        if peak >= 1:
            self.closed, self.good = True, 0
        elif peak < 0.7:
            self.good += 1
            if self.good >= 3:
                self.closed = False
        else:
            self.good = 0
        if fresh:
            self.last_at = at
        return dict(admit=not self.closed, warning=peak >= 0.8,
                    existing_account_action='NONE')
