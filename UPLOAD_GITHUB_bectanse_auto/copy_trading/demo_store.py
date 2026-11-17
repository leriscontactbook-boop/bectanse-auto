"""Local durable ledger shared by the master observer and demo executors on one VPS.

Use a local NTFS disk, not a network share. Every order intent is committed with
synchronous=FULL BEFORE contacting MT5. The OS locks in the runner enforce one writer
per terminal/account; SQLite transactions make intents survive process failure.
"""

import json
import hashlib
import sqlite3
import time
import uuid


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, source_id TEXT NOT NULL,
                value TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS intent_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL REFERENCES intents(id),
                phase TEXT NOT NULL, at REAL NOT NULL, evidence TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE);
            CREATE INDEX IF NOT EXISTS intent_events_operation ON intent_events(intent_id,seq);
            CREATE TABLE IF NOT EXISTS source_deals (
                source TEXT NOT NULL, ticket TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(source,ticket));
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self._put(key, value)

    def _put(self, key, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, allow_nan=False)))

    def pin(self, key, value):
        # Atomically install or verify identity across observer/executor startup.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state VALUES (?,?)",(key,json.dumps(value,allow_nan=False)))
            existing=self.get(key)
            if existing != value:
                raise ValueError("Ledger identity mismatch; do not reuse another account's state")

    def pin_copy_identity(self, owner, identity):
        """Allow only additive instrument extensions, never account or existing-map changes."""
        key = "identity:" + owner
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", (key, json.dumps(identity)))
            previous = self.get(key)
            if (previous.get("source") != identity["source"] or previous.get("owner") != identity["owner"]
                    or any(identity["symbols"].get(s) != target
                           for s, target in previous.get("symbols", {}).items())):
                raise ValueError("Ledger identity or existing symbol mapping changed")
            if previous != identity:
                self._put(key, identity)
                self._put("symbol_extension:" + owner, {"at": time.time(),
                    "before": len(previous["symbols"]), "after": len(identity["symbols"])})

    def publish(self, source, snapshot, deals):
        with self.db:
            for deal in deals:
                self.db.execute("INSERT OR IGNORE INTO source_deals VALUES (?,?,?)",
                                (source, str(deal["ticket"]), json.dumps(deal)))
            self._put("source:" + source, snapshot)

    def links(self, owner):
        return self.get("links:" + owner, {})

    def save_link(self, owner, source_id, link):
        links = self.links(owner)
        links[str(source_id)] = link
        self.put("links:" + owner, links)

    def intent(self, owner, source_id, link, kind, request, expected, now=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            links = self.links(owner)
            persisted = links.get(str(source_id), {})
            pending = persisted.get('pending') or link.get('pending')
            if pending:
                prior = self.command(pending)
                stored = self.db.execute('SELECT owner,source_id FROM intents WHERE id=?',(prior['id'],)).fetchone()
                if stored != (owner,str(source_id)) or prior['kind'] != kind or prior['request'] != request or prior['expected'] != expected:
                    raise ValueError('PENDING_OPERATION_CHANGED')
                link['pending'] = pending
                return prior
            command = {"id": uuid.uuid4().hex, "kind": kind, "request": request,
                       "expected": expected, "state": "SENDING", "created_at": time.time() if now is None else now}
            link["pending"] = command["id"]
            links[str(source_id)] = link
            self.db.execute("INSERT INTO intents VALUES (?,?,?,?,?)",
                            (command["id"], owner, str(source_id), json.dumps(command), command["created_at"]))
            self._put("links:" + owner, links)
            self._event(command['id'], 'QUEUED', {'kind':kind}, command['created_at'])
        return command

    def command(self, identity):
        row = self.db.execute("SELECT value FROM intents WHERE id=?", (identity,)).fetchone()
        if not row:
            raise RuntimeError("Missing durable intent")
        return json.loads(row[0])

    def record_result(self, command, result):
        # A submission boundary may have durably annotated this intent after
        # its creation. Preserve those fields when recording a broker response.
        command.update(self.command(command['id']))
        command["broker_result"] = result
        command["state"] = "ACCEPTED" if result and result.get("retcode") in (10008,10009,10010) else "UNKNOWN"
        with self.db:
            self.db.execute("UPDATE intents SET value=? WHERE id=?",(json.dumps(command),command["id"]))
            self._event(command['id'], 'BROKER_RESPONSE' if result is not None else 'UNCERTAIN',
                        {'result':result})

    def finish(self, owner, source_id, link, command, outcome):
        command.update(self.command(command['id']))
        command["state"] = outcome
        if command['kind'].startswith('PENDING_') and outcome in {'CONFIRMED','REJECTED'} and link.get('native_result'):
            # Snapshot the authoritative read-side evidence in the same commit
            # as the terminal state. Later changes to the link cannot rewrite it.
            command['native_reconciliation'] = json.loads(json.dumps(link['native_result']))
        link.pop("pending", None)
        links = self.links(owner)
        links[str(source_id)] = link
        with self.db:
            self.db.execute("UPDATE intents SET value=? WHERE id=?", (json.dumps(command), command["id"]))
            self._put("links:" + owner, links)
            self._event(command['id'], outcome, {'state':outcome, **(
                {'native_reconciliation':command['native_reconciliation']}
                if command.get('native_reconciliation') else {})})

    def _event(self, identity, phase, evidence, at=None):
        # Caller owns the transaction: state, mapping and history commit together.
        if phase not in {'QUEUED','SUBMISSION_STARTED','BROKER_RESPONSE','UNCERTAIN','CONFIRMED','REJECTED','NOT_SENT'}:
            raise ValueError('INVALID_OPERATION_PHASE')
        payload=json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False)
        fingerprint=hashlib.sha256(json.dumps([identity,phase,payload]).encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO intent_events(intent_id,phase,at,evidence,fingerprint) VALUES(?,?,?,?,?)',
                        (identity,phase,time.time() if at is None else at,payload,fingerprint))

    def history(self, identity):
        self.command(identity) # Never manufacture history for an unknown operation.
        return [dict(sequence=r[0],phase=r[1],at=r[2],evidence=json.loads(r[3])) for r in
                self.db.execute('SELECT seq,phase,at,evidence FROM intent_events WHERE intent_id=? ORDER BY seq',(identity,))]

    def operation_status(self, identity):
        command=self.command(identity)
        # ACCEPTED is an acknowledgement, never a confirmation of execution.
        if command['state']=='ACCEPTED':return 'AWAITING_RECONCILIATION'
        if command['state']=='SENDING':
            return 'UNCERTAIN' if 'submission_started_at' in command else 'QUEUED'
        return command['state']

    def close(self):
        self.db.close()
