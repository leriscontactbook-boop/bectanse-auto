"""Additive, bounded fan-out journal. No credentials and no order submission.

One dispatcher reads the existing observer's SQLite snapshot in read-only mode.
Executors own separate durable order ledgers. This database contains delivery
evidence, never authority to retry an uncertain broker operation.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FleetStore:
    def __init__(self, path, *, queue_limit=256, clock=time.time):
        if type(queue_limit) is not int or not 1 <= queue_limit <= 10000:
            raise ValueError("INVALID_QUEUE_LIMIT")
        self.clock, self.queue_limit = clock, queue_limit
        self.db = sqlite3.connect(str(path), timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS fleet_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS fleet_followers(
            id TEXT PRIMARY KEY, member_code TEXT NOT NULL, account_id INTEGER NOT NULL UNIQUE,
            source TEXT NOT NULL, owner TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL,
            blocked TEXT NOT NULL DEFAULT '', health TEXT NOT NULL DEFAULT '{}',
            health_at REAL, registered_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS fleet_events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL, source_id TEXT NOT NULL, kind TEXT NOT NULL,
            detected_at REAL NOT NULL, observed_at REAL NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS fleet_deliveries(
            event_id TEXT NOT NULL REFERENCES fleet_events(id),
            follower_id TEXT NOT NULL REFERENCES fleet_followers(id),
            state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
            PRIMARY KEY(event_id,follower_id));
          CREATE INDEX IF NOT EXISTS fleet_delivery_pending
            ON fleet_deliveries(follower_id,state,updated_at);
          CREATE TABLE IF NOT EXISTS fleet_sources(source TEXT PRIMARY KEY,snapshot TEXT NOT NULL);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO fleet_meta VALUES('schema_version','1')")
            if self.db.execute("SELECT value FROM fleet_meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise ValueError("FLEET_SCHEMA_UNSUPPORTED")

    def register(self, identity, member_code, account_id, source, owner, enabled=False):
        if (not all(isinstance(v, str) and 0 < len(v) <= 160 for v in (identity, member_code, source, owner))
                or type(account_id) is not int or account_id <= 0 or source == owner or type(enabled) is not bool):
            raise ValueError("INVALID_FOLLOWER_IDENTITY")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT * FROM fleet_followers WHERE id=?", (identity,)).fetchone()
            if old and tuple(old[k] for k in ('member_code', 'account_id', 'source', 'owner')) != (member_code, account_id, source, owner):
                raise ValueError("FOLLOWER_IDENTITY_CHANGED")
            self.db.execute("""INSERT INTO fleet_followers
              (id,member_code,account_id,source,owner,enabled,registered_at) VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled""",
              (identity, member_code, account_id, source, owner, int(enabled), self.clock()))

    def publish(self, source, snapshot):
        if snapshot.get('source', source) != source:
            raise ValueError("SOURCE_IDENTITY_MISMATCH")
        now = self.clock()
        if not 0 <= now - snapshot['observed_at'] <= 5:
            return 0  # Never make a stale snapshot fresh by relaying it.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT snapshot FROM fleet_sources WHERE source=?", (source,)).fetchone()
            previous = json.loads(row[0]) if row else {'positions': {}, 'closed': []}
            if snapshot['observed_at'] < previous.get('observed_at', -1):
                raise ValueError("SOURCE_CLOCK_REVERSED")
            events = []
            for position_id, position in snapshot['positions'].items():
                before = previous['positions'].get(position_id)
                if before == position:
                    continue
                kind = 'OPEN' if before is None else ('CLOSE' if before['volume'] != position['volume'] else 'MODIFY')
                events.append((position_id, kind, position))
            for position_id in set(snapshot['closed']) - set(previous['closed']):
                events.append((position_id, 'CLOSE', None))
            followers = self.db.execute("SELECT * FROM fleet_followers WHERE source=?", (source,)).fetchall()
            for position_id, kind, payload in events:
                key = hashlib.sha256(encoded([source, position_id, kind, snapshot['observed_at'], payload]).encode()).hexdigest()
                inserted = self.db.execute("""INSERT OR IGNORE INTO fleet_events
                  (id,source,source_id,kind,detected_at,observed_at,payload) VALUES(?,?,?,?,?,?,?)""",
                  (key, source, position_id, kind, now, snapshot['observed_at'], encoded(payload))).rowcount
                if not inserted:
                    continue
                for follower in followers:
                    blocked = self.db.execute("SELECT blocked FROM fleet_followers WHERE id=?", (follower['id'],)).fetchone()[0]
                    count = self.db.execute("SELECT count(*) FROM fleet_deliveries WHERE follower_id=? AND state IN ('PENDING','UNCERTAIN')", (follower['id'],)).fetchone()[0]
                    if count >= self.queue_limit:
                        blocked = 'QUEUE_OVERFLOW'
                        self.db.execute("UPDATE fleet_followers SET blocked=? WHERE id=?", (blocked, follower['id']))
                    # Closures remain visible even while new entries are disabled.
                    state, reason = ('PENDING', '') if not blocked else ('REJECTED', blocked)
                    if kind == 'OPEN' and not follower['enabled']:
                        state, reason = 'REJECTED', 'FOLLOWER_DISABLED'
                    self.db.execute("INSERT OR IGNORE INTO fleet_deliveries VALUES(?,?,?,?,?)",
                                    (key, follower['id'], state, reason, now))
            self.db.execute("INSERT INTO fleet_sources VALUES(?,?) ON CONFLICT(source) DO UPDATE SET snapshot=excluded.snapshot",
                            (source, encoded(snapshot)))
            return len(events)

    def source(self, source):
        row = self.db.execute("SELECT snapshot FROM fleet_sources WHERE source=?", (source,)).fetchone()
        return json.loads(row[0]) if row else None

    def follower(self, identity):
        row = self.db.execute("SELECT * FROM fleet_followers WHERE id=?", (identity,)).fetchone()
        return dict(row) if row else None

    def pending(self, identity):
        return [dict(r) for r in self.db.execute("""SELECT e.*,d.state FROM fleet_deliveries d
          JOIN fleet_events e ON e.id=d.event_id WHERE d.follower_id=?
          AND d.state IN ('PENDING','UNCERTAIN') ORDER BY e.seq LIMIT ?""", (identity, self.queue_limit))]

    def resolve(self, identity, event_id, state, reason=''):
        if state not in {'EXECUTED', 'REJECTED', 'UNCERTAIN', 'SUPERSEDED', 'RECONCILED'}:
            raise ValueError('INVALID_DELIVERY_STATE')
        with self.db:
            self.db.execute("""UPDATE fleet_deliveries SET state=?,reason=?,updated_at=?
              WHERE follower_id=? AND event_id=? AND state IN ('PENDING','UNCERTAIN')""",
              (state, reason, self.clock(), identity, event_id))

    def health(self, identity, report):
        # Explicit allowlist: never serialize broker credentials or arbitrary exceptions.
        allowed = {'state', 'error', 'demo_confirmed', 'last_signal', 'last_copy', 'last_reconciliation',
                   'version', 'open_positions', 'pending_commands', 'lag_seconds', 'pid', 'reconnections'}
        clean = {k: v for k, v in report.items() if k in allowed and isinstance(v, (str, int, float, bool, type(None)))}
        with self.db:
            self.db.execute("UPDATE fleet_followers SET health=?,health_at=? WHERE id=?", (encoded(clean), self.clock(), identity))

    def close(self):
        self.db.close()


class SourceReader:
    """Read-only connection; never migrate, checkpoint or change the pilot ledger."""
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        self.db.execute('PRAGMA query_only=ON')

    def read(self, source):
        row = self.db.execute('SELECT value FROM state WHERE key=?', ('source:' + source,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
