"""Resumable lab checks. Recorded passes are evidence, never blanket qualification."""
import hashlib,json,sqlite3,time
from pathlib import Path
from .atomic_io import write_json

class Campaign:
    def __init__(self,root,binding):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'campaign.sqlite',timeout=10)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS binding(value TEXT); CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,stage TEXT,started REAL,ended REAL,state TEXT,reason TEXT,artifact TEXT,digest TEXT);')
        encoded=json.dumps(binding,sort_keys=True)
        previous=self.db.execute('SELECT value FROM binding').fetchone()
        if previous and previous[0]!=encoded:raise ValueError('CAMPAIGN_VERSION_OR_ACCOUNT_CHANGED')
        with self.db:
            if not previous:self.db.execute('INSERT INTO binding VALUES(?)',(encoded,))
            self.db.execute("UPDATE attempts SET state='INTERRUPTED',reason='PROCESS_STOPPED_BEFORE_CONFIRMATION',ended=? WHERE state='RUNNING'",(time.time(),))
    def run(self,name,action,*,max_age=None):
        row=self.db.execute("SELECT ended,artifact,digest FROM attempts WHERE stage=? AND state='PASSED' ORDER BY id DESC LIMIT 1",(name,)).fetchone()
        if row and (max_age is None or 0<=time.time()-row[0]<max_age):
            p=self.root/row[1]
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row[2]:raise ValueError('CAMPAIGN_EVIDENCE_CHANGED')
            return json.loads(p.read_text())
        with self.db:
            cursor=self.db.execute("INSERT INTO attempts(stage,started,state) VALUES(?,?,'RUNNING')",(name,time.time()))
            attempt=cursor.lastrowid
        try:
            data=action();payload=json.dumps(data,sort_keys=True,allow_nan=False).encode()
            namefile=f'{attempt:06d}-{name}.json'
            with (self.root/namefile).open('xb') as stream:stream.write(payload)
            with self.db:self.db.execute("UPDATE attempts SET ended=?,state='PASSED',artifact=?,digest=? WHERE id=?",(time.time(),namefile,hashlib.sha256(payload).hexdigest(),attempt))
            return data
        except Exception as error:
            # Never persist SDK exception text or secrets.
            reason=error.args[0] if isinstance(error,CheckFailure) else 'CHECK_EXCEPTION'
            with self.db:self.db.execute("UPDATE attempts SET ended=?,state='FAILED',reason=? WHERE id=?",(time.time(),reason,attempt))
            raise CheckFailure(reason) from None
    def close(self):self.db.close()

class CheckFailure(RuntimeError):pass
