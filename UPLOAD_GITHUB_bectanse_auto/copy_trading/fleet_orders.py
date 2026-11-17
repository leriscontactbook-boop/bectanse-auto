"""Atomic intent reservation for new fleet ledgers; legacy ledgers stay unchanged."""
import hashlib
import json
import time
import uuid

from .demo_policy import SafetyError
from .demo_store import Ledger
from .fleet_store import encoded


class FleetOrderLedger(Ledger):
    def __init__(self, path):
        super().__init__(path)
        self.events = {}
        self.db.execute("""CREATE TABLE IF NOT EXISTS fleet_operation_keys(
            operation_key TEXT PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE)""")
        self.db.commit()

    def intent(self, owner, source_id, link, kind, request, expected, now=None):
        source = self.get('identity:' + owner)['source']
        event = self.events.get((str(source_id), kind))
        if not event:
            # Management must continue during dispatcher recovery. The actual target
            # and source revision make repeated reconciliation reservations stable.
            event = encoded([self.get('source:' + source, {}).get('positions', {}).get(str(source_id)), expected])
        key = hashlib.sha256(encoded([source, owner, str(source_id), event, kind, request]).encode()).hexdigest()
        command = {'id': uuid.uuid4().hex, 'operation_key': key, 'event_id': event,
                   'kind': kind, 'request': request, 'expected': expected,
                   'state': 'SENDING', 'created_at': time.time() if now is None else now}
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            links = self.links(owner)
            previous = links.get(str(source_id), {})
            if previous.get('pending') or (kind == 'OPEN' and previous):
                raise SafetyError('CONCURRENT_OPERATION_RECONCILE')
            reserved = self.db.execute('SELECT intent_id FROM fleet_operation_keys WHERE operation_key=?', (key,)).fetchone()
            if reserved:
                prior = self.command(reserved[0])
                if kind == 'OPEN' or prior['state'] not in {'NOT_SENT', 'REJECTED'}:
                    raise SafetyError('DUPLICATE_OPERATION_RECONCILE')
                command['retry_of'] = prior['id']  # Only proven non-execution can be retried.
                self.db.execute('UPDATE fleet_operation_keys SET intent_id=? WHERE operation_key=?', (command['id'], key))
            else:
                self.db.execute('INSERT INTO fleet_operation_keys VALUES(?,?)', (key, command['id']))
            link['pending'] = command['id']
            links[str(source_id)] = link
            self.db.execute('INSERT INTO intents VALUES(?,?,?,?,?)',
                            (command['id'], owner, str(source_id), encoded(command), command['created_at']))
            self._put('links:' + owner, links)
        return command
