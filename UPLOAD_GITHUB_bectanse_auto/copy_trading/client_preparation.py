"""Offline admission register. Never grants execution or contacts a terminal.

Adapters must supply authenticated actors and canonical account ownership from
server-side storage, never from request JSON. Not wired to production routes.
"""
import hashlib
from contextlib import contextmanager
import json
import math
import re
import sqlite3
from dataclasses import dataclass

STATES = {'PENDING', 'CHECKING', 'DEMO_VALIDATED', 'RESTRICTED', 'REJECTED', 'SUSPENDED'}
CHECKS = {'identity', 'permissions', 'catalog', 'mapping', 'volumes', 'execution_rules',
          'sessions', 'open', 'modify', 'partial_close', 'close', 'reconnection', 'reconciliation'}
REASONS = {'REQUESTED', 'SUBSCRIPTION_EXPIRED', 'DISCONNECTED', 'REPEATED_ERRORS',
           'POSITION_DIVERGENCE', 'METADATA_CHANGED', 'REVIEW_REQUIRED'}


@dataclass(frozen=True)
class Actor:
    member: str
    admin: bool = False


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('INVALID_IDENTIFIER')
    return value


class PreparationRegister:
    def __init__(self, path, *, account_reader, access_reader, clock):
        self.path = path
        self.accounts, self.access, self.clock = account_reader, access_reader, clock
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS admissions (
          account INTEGER PRIMARY KEY, member TEXT NOT NULL, identity_hash TEXT NOT NULL,
          state TEXT NOT NULL, evidence TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS admission_events (
          id INTEGER PRIMARY KEY, account INTEGER NOT NULL, actor TEXT NOT NULL,
          at REAL NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT);
        CREATE TABLE IF NOT EXISTS admission_receipts (
          request_id TEXT PRIMARY KEY, digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admission_alerts (
          id INTEGER PRIMARY KEY, account INTEGER NOT NULL, reason TEXT NOT NULL,
          at REAL NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
        ''')

    def account_lock(self, account):
        from .admission_lock import account_lock
        return account_lock(self.path, account)

    @contextmanager
    def transaction(self, account):
        with self.account_lock(account):
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        self.db.close()

    def _identity(self, actor, account):
        token(actor.member)
        if type(actor.admin) is not bool or type(account) is not int or account <= 0:
            raise ValueError('INVALID_IDENTITY')
        data = self.accounts(account)  # trusted DB reader, no MT5 login
        if not data or (data['member'] != actor.member and not actor.admin):
            raise PermissionError('ACCOUNT_ACCESS_DENIED')
        token(data['member'])
        if data['platform'] != 'MT5' or data['environment'] not in {'DEMO', 'REAL', 'UNKNOWN'}:
            raise ValueError('INVALID_PLATFORM')
        fields = {k: data[k] for k in ('member', 'platform', 'broker', 'server', 'login', 'environment')}
        if any(not isinstance(v, str) or not v for v in fields.values()):
            raise ValueError('INCOMPLETE_IDENTITY')
        return data, hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    def _event(self, account, actor, state, reason, evidence=None):
        self.db.execute('INSERT INTO admission_events(account,actor,at,state,reason,evidence) VALUES(?,?,?,?,?,?)',
                        (account, actor.member, self.clock(), state, reason, evidence))

    def register(self, actor, account):
        data, fingerprint = self._identity(actor, account)
        with self.transaction(account):
            old = self.db.execute('SELECT * FROM admissions WHERE account=?', (account,)).fetchone()
            if old:
                if old['identity_hash'] != fingerprint:
                    raise ValueError('ACCOUNT_IDENTITY_CHANGED')
                return old['state']
            self.db.execute('INSERT INTO admissions VALUES(?,?,?,?,NULL,NULL)',
                            (account, data['member'], fingerprint, 'PENDING'))
            self._event(account, actor, 'PENDING', 'REQUESTED')
        return 'PENDING'

    def transition(self, actor, account, state, *, reason='REVIEW_REQUIRED', evidence=None, request_id=None):
        if not actor.admin:
            raise PermissionError('ADMIN_REQUIRED')
        data, fingerprint = self._identity(actor, account)
        if state not in STATES or reason not in REASONS:
            raise ValueError('INVALID_TRANSITION')
        with self.transaction(account):
            request_digest = hashlib.sha256(json.dumps(
                [actor.member, account, state, reason, evidence], sort_keys=True,
                allow_nan=False).encode()).hexdigest() if request_id else None
            if request_id:
                token(request_id)
                receipt = self.db.execute('SELECT digest FROM admission_receipts WHERE request_id=?',
                                          (request_id,)).fetchone()
                if receipt:
                    if receipt['digest'] != request_digest:
                        raise ValueError('IDEMPOTENCY_CONFLICT')
                    return
            row = self.db.execute('SELECT * FROM admissions WHERE account=?', (account,)).fetchone()
            if not row or (row['identity_hash'] != fingerprint and state != 'SUSPENDED'):
                raise ValueError('ACCOUNT_REVIEW_REQUIRED')
            allowed = {'PENDING': {'CHECKING', 'REJECTED', 'SUSPENDED'},
                       'CHECKING': {'DEMO_VALIDATED', 'RESTRICTED', 'REJECTED', 'SUSPENDED'},
                       'DEMO_VALIDATED': {'SUSPENDED', 'CHECKING'},
                       'RESTRICTED': {'CHECKING', 'SUSPENDED', 'REJECTED'},
                       'REJECTED': {'CHECKING'}, 'SUSPENDED': {'CHECKING'}}
            if state not in allowed[row['state']]:
                raise ValueError('INVALID_TRANSITION')
            proof, expiry = None, None
            if state == 'DEMO_VALIDATED':
                e = evidence or {}
                if (set(e) != {'sha256', 'kind', 'checks', 'expires'} or data['environment'] != 'DEMO'
                        or e['kind'] != 'MT5_DEMO' or not re.fullmatch(r'[a-f0-9]{64}', str(e['sha256']))
                        or not isinstance(e['checks'], dict) or set(e['checks']) != CHECKS
                        or any(v is not True for v in e['checks'].values())
                        or type(e['expires']) not in (float, int) or not math.isfinite(e['expires'])
                        or e['expires'] <= self.clock()):
                    raise ValueError('DEMO_EVIDENCE_REQUIRED')
                # Operator attestation only; hash records the reviewed external report.
                # This registry does not establish that the report is true or grant trading.
                proof, expiry = json.dumps(e, sort_keys=True), e['expires']
            self.db.execute('UPDATE admissions SET state=?,evidence=?,expires=? WHERE account=?',
                            (state, proof, expiry, account))
            self._event(account, actor, state, reason, proof)
            if state == 'SUSPENDED':
                self.db.execute('INSERT INTO admission_alerts(account,reason,at) VALUES(?,?,?)',
                                (account, reason, self.clock()))
            if request_id:
                self.db.execute('INSERT INTO admission_receipts VALUES(?,?)', (request_id, request_digest))

    def readiness(self, actor, account):
        data, fingerprint = self._identity(actor, account)
        row = self.db.execute('SELECT * FROM admissions WHERE account=?', (account,)).fetchone()
        reasons = []
        try:
            granted, until = self.access(data['member'])  # fresh server-side entitlement
            if (granted is not True or type(until) not in (float, int)
                    or not math.isfinite(until) or until <= self.clock()):
                reasons.append('SUBSCRIPTION_REQUIRED')
        except Exception:
            reasons.append('ENTITLEMENT_UNAVAILABLE')
        if not row or row['identity_hash'] != fingerprint:
            reasons.append('ADMISSION_REQUIRED')
        elif row['state'] != 'DEMO_VALIDATED':
            reasons.append('ADMISSION_' + row['state'])
        elif row['expires'] <= self.clock():
            reasons.append('EVIDENCE_EXPIRED')
        if data['environment'] != 'DEMO':
            reasons.append('REAL_ACTIVATION_NOT_IMPLEMENTED')
        return {'account_id': account, 'state': row['state'] if row else 'PENDING',
                'preparation_ready': not reasons, 'reasons': reasons,
                'execution_enabled': False, 'close_existing_positions': False,
                'existing_positions_policy': 'RECONCILIATION_AND_EXPLICIT_OPERATOR_POLICY'}
