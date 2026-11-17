"""Isolated onboarding preparation. No terminal start, login or trading submission.

An operator-supplied read-only adapter supplies observations. READY_FOR_ACTIVATION
means checks passed, never that trading was enabled. Not wired to production.
"""
import hashlib
import json
import math
import sqlite3
import time
import uuid


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class PreparationQueue:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS preparation(
            member TEXT PRIMARY KEY, version INTEGER NOT NULL, request TEXT NOT NULL,
            digest TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
            token TEXT, lease REAL, due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS preparation_events(
            id INTEGER PRIMARY KEY, member TEXT NOT NULL, version INTEGER NOT NULL,
            state TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def _event(self, member, version, state, reason):
        self.db.execute('INSERT INTO preparation_events(member,version,state,reason,at) VALUES(?,?,?,?,?)',
                        (member, version, state, reason, self.clock()))

    def enqueue(self, *, member, version, account, login, server, master, settings_digest):
        if (not isinstance(member, str) or not member or type(version) is not int or version < 1
                or type(account) is not int or account < 1 or not isinstance(login, str) or not login
                or not isinstance(server, str) or not server or len(settings_digest) != 64
                or any(c not in '0123456789abcdef' for c in settings_digest)):
            raise ValueError('INVALID_REQUEST')
        if master is not None and (not isinstance(master, dict) or set(master) != {'login','server','environment'}
                or any(not isinstance(v, str) or not v for v in master.values())
                or master['environment'] not in ('DEMO','REAL')):
            raise ValueError('INVALID_MASTER')
        request = dict(member=member, version=version, account=account, login=login,
                       server=server, master=master, settings_digest=settings_digest)
        digest = fingerprint(request)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT * FROM preparation WHERE member=?', (member,)).fetchone()
            if old and old['version'] >= version:
                if old['version'] != version or old['digest'] != digest:
                    raise ValueError('STALE_OR_CONFLICTING_REQUEST')
                self.db.execute('COMMIT')
                return False
            state = 'QUEUED' if master else 'CANCELLED'
            self.db.execute('''INSERT INTO preparation VALUES(?,?,?,?,?,?,NULL,NULL,?,0)
                ON CONFLICT(member) DO UPDATE SET version=excluded.version,request=excluded.request,
                digest=excluded.digest,state=excluded.state,reason='',token=NULL,lease=NULL,
                due=excluded.due,attempts=0''',
                (member, version, json.dumps(request), digest, state, '', self.clock()))
            self._event(member, version, state, '')
            self.db.execute('COMMIT')
            return True
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def claim(self):
        now = self.clock()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('''SELECT * FROM preparation WHERE
                (state IN ('QUEUED','RETRY') AND due<=?) OR (state='CHECKING' AND lease<=?)
                ORDER BY due,member LIMIT 1''', (now, now)).fetchone()
            if not row:
                self.db.execute('COMMIT')
                return None
            token = uuid.uuid4().hex
            self.db.execute("UPDATE preparation SET state='CHECKING',token=?,lease=?,attempts=attempts+1 WHERE member=?",
                            (token, now+30, row['member']))
            self._event(row['member'], row['version'], 'CHECKING', '')
            self.db.execute('COMMIT')
            return dict(request=json.loads(row['request']), token=token)
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def finish(self, claim, observation=None, unavailable=False):
        request = claim['request']
        reason = self.validate(request, observation) if not unavailable else 'COLLECTOR_UNAVAILABLE'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM preparation WHERE member=?', (request['member'],)).fetchone()
            if (not row or row['version'] != request['version'] or row['token'] != claim['token']
                    or row['state'] != 'CHECKING' or row['lease'] <= self.clock()):
                self.db.execute('COMMIT')
                return False
            transient = reason in ('COLLECTOR_UNAVAILABLE','STALE_EVIDENCE','DISCONNECTED')
            state = 'RETRY' if transient and row['attempts'] < 5 else 'BLOCKED' if reason else 'READY_FOR_ACTIVATION'
            delay = min(300, 2 ** row['attempts'])
            self.db.execute('UPDATE preparation SET state=?,reason=?,token=NULL,lease=NULL,due=? WHERE member=?',
                            (state, reason, self.clock()+delay, request['member']))
            self._event(request['member'], request['version'], state, reason)
            self.db.execute('COMMIT')
            return True
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def validate(self, request, observation):
        if not isinstance(observation, dict):return 'COLLECTOR_UNAVAILABLE'
        stamp = observation.get('observed_at')
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0 <= self.clock()-stamp < 30:
            return 'STALE_EVIDENCE'
        for field in ('member','version','account','login','server','master','settings_digest'):
            if observation.get(field) != request[field]:return 'IDENTITY_OR_SETTINGS_CHANGED'
        # An adapter must verify evidence authenticity before supplying this observation.
        if observation.get('verified_evidence') is not True:return 'EVIDENCE_UNVERIFIED'
        checks = {'membership_active':'MEMBERSHIP_INACTIVE', 'connected':'DISCONNECTED',
                  'trade_permission':'ACCOUNT_PERMISSION_MISSING','metadata_valid':'METADATA_INVALID',
                  'volumes_valid':'VOLUME_INVALID','reconciled':'RECONCILIATION_REQUIRED',
                  'capacity_reserved':'CAPACITY_UNAVAILABLE'}
        for field, reason in checks.items():
            if observation.get(field) is not True:return reason
        return ''

    def tick(self, read_only_collector):
        claim = self.claim()
        if not claim:return False
        try:
            observation = read_only_collector(claim['request'])
        except Exception:
            # Never persist exception strings: a provider may include credentials.
            return self.finish(claim, unavailable=True)
        return self.finish(claim, observation)

    def status(self, member):
        row = self.db.execute('SELECT version,state,reason,attempts FROM preparation WHERE member=?', (member,)).fetchone()
        return dict(row, execution_enabled=False) if row else None
