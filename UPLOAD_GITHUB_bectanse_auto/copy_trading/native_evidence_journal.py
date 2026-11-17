"""Collector-signed, hash-chained observation journal, never a broker signature.

Polling + overlapping broker history, not an exhaustive transaction stream.
Gaps/unclean restarts are explicit; no gap is declared recovered from an empty
history response. SQLite FULL durability and an external head detect mismatch.
"""
import hashlib,hmac,json,sqlite3,time,uuid
from pathlib import Path
from .atomic_io import write_json

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

class Journal:
    def __init__(self,path,key,binding,clock=time.time):
        if len(key)<32:raise ValueError('SIGNING_KEY_REQUIRED')
        self.path=Path(path);self.anchor=self.path.with_suffix('.head.json');self.key=key;self.clock=clock
        self.db=sqlite3.connect(self.path,timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL, signature TEXT NOT NULL); CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
        stored=self.db.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()
        if stored and json.loads(stored[0])!=binding:raise ValueError('JOURNAL_BINDING_CHANGED')
        with self.db:self.db.execute("INSERT OR IGNORE INTO metadata VALUES('binding',?)",(encoded(binding).decode(),))
        self.verify()
        self.run=uuid.uuid4().hex
        last=self.last()
        if last and last['kind']!='STOP':self.append('UNCLEAN_RESTART',{'previous_run':last['run'],'previous_at':last['at']})
        self.append('START',{'binding':binding,'signature_origin':'LOCAL_COLLECTOR_HMAC_NOT_BROKER','coverage':'POLLING_PLUS_OVERLAPPING_HISTORY_NOT_EXHAUSTIVE'})
    def last(self):
        row=self.db.execute('SELECT body FROM records ORDER BY seq DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None
    def verify(self):
        previous='0'*64;count=0
        for seq,body,digest,signature in self.db.execute('SELECT seq,body,hash,signature FROM records ORDER BY seq'):
            event=json.loads(body);count+=1
            expected=hashlib.sha256(encoded(event)).hexdigest()
            if seq!=count or event['seq']!=seq or event['previous']!=previous or digest!=expected or not hmac.compare_digest(signature,hmac.new(self.key,digest.encode(),hashlib.sha256).hexdigest()):
                raise ValueError('JOURNAL_INTEGRITY_FAILURE')
            previous=digest
        if self.anchor.exists():
            anchor=json.loads(self.anchor.read_text())
            # Commit-before-anchor crash is detected, not silently trusted.
            if anchor!={'seq':count,'hash':previous}:raise ValueError('JOURNAL_ANCHOR_MISMATCH_REVIEW_REQUIRED')
        elif count:raise ValueError('JOURNAL_ANCHOR_MISSING')
        return {'records':count,'head':previous}
    def append(self,kind,payload):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT seq,hash FROM records ORDER BY seq DESC LIMIT 1').fetchone()
            seq=row[0]+1 if row else 1
            event=dict(seq=seq,previous=row[1] if row else '0'*64,run=self.run,at=self.clock(),kind=kind,payload=payload)
            digest=hashlib.sha256(encoded(event)).hexdigest();signature=hmac.new(self.key,digest.encode(),hashlib.sha256).hexdigest()
            self.db.execute('INSERT INTO records VALUES(?,?,?,?)',(seq,encoded(event).decode(),digest,signature))
        write_json(self.anchor,{'seq':seq,'hash':digest})
        return seq
    def last_observation(self):
        for row in self.db.execute('SELECT body FROM records ORDER BY seq DESC'):
            body=json.loads(row[0])
            if body['kind']=='NATIVE_OBSERVATION':return body['payload']['history_to']
        return None
    def close(self,graceful=True):
        if graceful:self.append('STOP',{'native_orders_sent':0})
        self.db.close()
