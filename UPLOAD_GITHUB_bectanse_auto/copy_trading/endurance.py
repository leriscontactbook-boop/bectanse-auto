"""Persistent passive endurance evidence; no trading dependencies or submission APIs."""
import json
import sqlite3
import time
from pathlib import Path


def classify(row, baseline):
    alerts=[]
    if row.get('probe_error'):alerts.append('PROBE_UNAVAILABLE')
    if not row.get('all_reconciled'):alerts.append('RECONCILIATION_UNVERIFIED_OR_DIVERGENT')
    if not row.get('all_health_fresh'):alerts.append('STALE_HEARTBEAT')
    expected={'agent':'TERMINALS_CONNECTED','observer':'CONNECTED','historical':'CONNECTED','fleet':'SOURCE_CONNECTED','second':'CONNECTED'}
    for role,state in expected.items():
        h=row.get('health',{}).get(role,{})
        if h.get('state')!=state or h.get('error'):alerts.append('CONNECTION_'+role.upper())
    if set(row.get('accounts',{}))!={'master','historical','second'}:alerts.append('ACCOUNTS_UNVERIFIED')
    for role,a in row.get('accounts',{}).items():
        if not a.get('demo') or not a.get('identity_matches') or not a.get('terminal_matches'):alerts.append('IDENTITY_'+role.upper())
        if a.get('build')!=baseline.get('accounts',{}).get(role,{}).get('build'):alerts.append('TERMINAL_BUILD_CHANGED_'+role.upper())
        if a.get('pending_orders')!=0:alerts.append('PENDING_ORDERS_'+role.upper())
        if a.get('history_error') or 'deals' not in a:alerts.append('HISTORY_UNVERIFIED_'+role.upper())
        if role!='master':
            by_magic={}
            for d in a.get('deals',[]):
                if d['entry']==0 and d['magic']:
                    by_magic.setdefault(d['magic'],set()).add(d['position_id'])
            if any(len(ids)>1 for ids in by_magic.values()):alerts.append('POSSIBLE_DUPLICATE_'+role.upper())
    for role,outcomes in row.get('new_trade_outcomes',{}).items():
        if any(v['state']=='SKIPPED' or (v['state']=='NO_LINK' and v['age_seconds']>15) for v in outcomes.values()):alerts.append('COPY_NOT_EXECUTED_'+role.upper())
    if row.get('settings_hashes')!=baseline.get('settings_hashes'):alerts.append('SETTINGS_CHANGED')
    if row.get('release_hashes')!=baseline.get('release_hashes'):alerts.append('VERSION_CHANGED')
    resources=row.get('resources',{})
    if resources.get('cpu_percent',0)>=85:alerts.append('CPU_HIGH')
    if resources.get('memory_percent',0)>=85:alerts.append('MEMORY_HIGH')
    if resources.get('disk_free',0)<5*1024**3:alerts.append('DISK_LOW_OR_UNKNOWN')
    q=row.get('queue',{})
    if q.get('error'):alerts.append('PERSISTENCE_UNVERIFIED')
    if q.get('uncertain',0):alerts.append('UNCERTAIN_EXECUTION')
    if q.get('pending_age_seconds',0)>15:alerts.append('QUEUE_DELAY')
    if q.get('pending',0)>=128:alerts.append('QUEUE_HIGH')
    for task in row.get('watchdogs',[]):
        if task.get('disabled') or task.get('result') not in (0,267009) or task.get('age',999)>150:alerts.append('WATCHDOG_UNHEALTHY')
    if len(row.get('watchdogs',[]))!=2:alerts.append('WATCHDOG_UNVERIFIED')
    if row.get('central_http',{}).get('status')!=200:alerts.append('CENTRAL_HTTP_UNAVAILABLE')
    return sorted(set(alerts))


class Journal:
    def __init__(self,path):
        self.db=sqlite3.connect(str(path),timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);CREATE TABLE IF NOT EXISTS samples(id INTEGER PRIMARY KEY,at REAL,payload TEXT,alerts TEXT);')
    def get(self,key):
        r=self.db.execute('SELECT v FROM meta WHERE k=?',(key,)).fetchone();return json.loads(r[0]) if r else None
    def start(self,baseline,now=None,duration=86400):
        if self.get('window') is not None:raise ValueError('WINDOW_ALREADY_EXISTS')
        now=time.time() if now is None else now
        with self.db:
            self.db.execute('INSERT INTO meta VALUES(?,?)',('window',json.dumps({'start':now,'end':now+duration})))
            self.db.execute('INSERT INTO meta VALUES(?,?)',('baseline',json.dumps(baseline)))
    def record(self,row,now=None):
        now=time.time() if now is None else now;alerts=classify(row,self.get('baseline'))
        last=self.db.execute('SELECT at FROM samples ORDER BY id DESC LIMIT 1').fetchone()
        gap=now-(last[0] if last else self.get('window')['start'])
        if gap>90:alerts.append('COLLECTION_GAP')
        if gap<0:alerts.append('CLOCK_ROLLBACK')
        row={**row,'collection_gap_seconds':gap}
        with self.db:self.db.execute('INSERT INTO samples(at,payload,alerts) VALUES(?,?,?)',(now,json.dumps(row,allow_nan=False),json.dumps(alerts)))
        return alerts
    def status(self,now=None):
        now=time.time() if now is None else now;w=self.get('window');last=self.db.execute('SELECT at,alerts FROM samples ORDER BY id DESC LIMIT 1').fetchone()
        count,bad=self.db.execute("SELECT count(*),sum(CASE WHEN alerts!='[]' THEN 1 ELSE 0 END) FROM samples").fetchone()
        return {'state':'COMPLETED_REVIEW_REQUIRED' if now>=w['end'] else 'RUNNING','window':w,'samples':count,'alert_samples':bad or 0,'last_sample_at':last[0] if last else None,'last_alerts':json.loads(last[1]) if last else [],'success_claimed':False}
    def close(self):self.db.close()


def atomic_json(path,value):
    p=Path(path);tmp=p.with_suffix(p.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(value,f,allow_nan=False);f.flush();__import__('os').fsync(f.fileno())
    tmp.replace(p)
