"""Durable submission boundary for a single, locked real-account ledger.

The authority is an injected, authoritative reservation service, NOT a cached
boolean. No production transport is selected by this module. Its submission
context must serialize member revocation with operation reservation and must
not acknowledge a pause until previously reserved operations are reconciled.
"""
from contextlib import contextmanager
import json
import time

from .demo_mt5 import NotSent


class RealExecutionBoundary:
    def __init__(self, ledger, authority, *, owner, source, clock=time.time, source_reader=None, source_max_age=3):
        self.ledger, self.authority = ledger, authority
        self.owner, self.source, self.clock = owner, source, clock
        self.source_reader,self.source_max_age=source_reader,source_max_age

    def require_source(self,command,logical):
        compensating=command['kind']=='CLOSE' and bool(logical.get('orphan_order') or logical.get('orphan_position'))
        if (command['kind'] not in {'OPEN','PENDING_CREATE'} and not compensating) or self.source_reader is None:return
        try:
            snapshot=self.source_reader(self.source)
            if (not snapshot or snapshot.get('source')!=self.source
                or not 0<=self.clock()-snapshot['observed_at']<=self.source_max_age
                or snapshot.get('unresolved')):raise ValueError()
            if compensating:
                if logical.get('orphan_order'):
                    ticket=logical['orphan_order']
                    result=snapshot.get('native_completed',{}).get(ticket)
                    if result is None:result=snapshot.get('native_orders',{}).get(ticket,{}).get('execution')
                    if not result or any(p in snapshot['positions'] for p in result.get('position_ids',[])):raise ValueError()
                elif logical['orphan_position'] in snapshot['positions']:raise ValueError()
                return
            row=self.ledger.db.execute('SELECT source_id FROM intents WHERE id=? AND owner=?',
                (command['id'],self.owner)).fetchone()
            if not row:raise ValueError()
            identity=row[0]
            if command['kind']=='OPEN' and logical.get('converted_pending'):
                if not identity.startswith('order:'):raise ValueError()
                position=snapshot.get('native_orders',{}).get(identity[6:])
                from .member_sizing import effective_side
                settings=logical.get('sizing_receipt',{}).get('body',{}).get('settings',{})
                if not settings.get('convertir_pending_invalide') or not position:raise ValueError()
                if position['type']!=logical.get('source_order_type'):raise ValueError()
                side='BUY' if position['type'] in (2,4,6) else 'SELL'
                if effective_side(settings,side)!=logical['side']:raise ValueError()
            elif command['kind']=='OPEN':
                if identity in snapshot.get('closed',[]):raise ValueError()
                position=snapshot['positions'].get(identity)
                from .member_sizing import effective_side
                settings=logical.get('sizing_receipt',{}).get('body',{}).get('settings',{})
                if not position or effective_side(settings,position.get('side'))!=logical['side']:raise ValueError()
            else:
                ticket=identity.removeprefix('order:')
                if ticket in snapshot.get('native_completed',{}):raise ValueError()
                position=snapshot.get('native_orders',{}).get(ticket)
                from .member_sizing import effective_order_type
                settings=logical.get('sizing_receipt',{}).get('body',{}).get('settings',{})
                if not position or effective_order_type(settings,position.get('type'))!=logical['order_type']:raise ValueError()
            if position.get('symbol')!=logical['source_symbol']:raise ValueError()
        except Exception:
            raise NotSent('SOURCE_ABSENCE_NO_LONGER_CONFIRMED' if compensating else 'MASTER_ENTRY_NO_LONGER_CONFIRMED') from None

    @contextmanager
    def submission(self, logical):
        operation = logical.get('operation_id')
        if (not isinstance(operation, str) or not operation
                or logical.get('owner') != self.owner
                or logical.get('source') != self.source):
            raise NotSent('OPERATION_IDENTITY_REQUIRED')
        try:
            command = self.ledger.command(operation)
        except RuntimeError:
            raise NotSent('DURABLE_INTENT_REQUIRED') from None
        request = {k: v for k, v in logical.items()
                   if k not in {'operation_id', 'owner', 'source'}}
        if command['request'] != request:
            raise NotSent('OPERATION_PAYLOAD_CHANGED')
        if self.authority is None:
            raise NotSent('OPERATIONAL_AUTHORIZATION_REQUIRED')
        self.require_source(command,logical)
        # The authority must independently verify member identity, entitlement,
        # consent, configuration, qualification and current executor identity.
        # Persist BEFORE requesting the remote reservation. On restart an absent
        # submission marker proves this process never reached native order_send.
        with self.ledger.db:
            current=self.ledger.command(operation)
            if current.get('state')!='SENDING' or 'submission_started_at' in current:
                raise NotSent('OPERATION_ALREADY_RESERVED_OR_SETTLED')
            if 'reservation_attempted_at' in current:
                raise NotSent('RESERVATION_RECOVERY_REQUIRED')
            current['reservation_attempted_at']=self.clock()
            self.ledger.db.execute('UPDATE intents SET value=? WHERE id=?',(json.dumps(current),operation))
        with self.authority.submission(dict(logical)):
            self.require_source(command,logical)
            db = self.ledger.db
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT owner,value FROM intents WHERE id=?',
                                 (operation,)).fetchone()
                current = json.loads(row[1]) if row else {}
                if (not row or row[0] != self.owner or current.get('request') != request
                        or current.get('state') != 'SENDING'
                        or 'submission_started_at' in current):
                    raise NotSent('OPERATION_ALREADY_RESERVED_OR_SETTLED')
                current['submission_started_at'] = self.clock()
                db.execute('UPDATE intents SET value=? WHERE id=?',
                           (json.dumps(current), operation))
                self.ledger._event(operation, 'SUBMISSION_STARTED', {'request_matches_intent': True}, self.clock())
                db.commit()
            except BaseException:
                db.rollback()
                raise
            # Never clear the marker on an error or a lost response. A crash
            # between this commit and send is uncertain, not permission to retry.
            yield
