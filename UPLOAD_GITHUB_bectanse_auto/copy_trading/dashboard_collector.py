"""Independent reporting process. Attach to running terminals; never login/order.

One process per terminal: the MetaTrader5 binding is process-global. Heavy
history reads never run inside an observer or execution loop.
"""
import json,os,time,hashlib,hmac,uuid,sys,subprocess
from pathlib import Path
from datetime import datetime,timezone
import requests,psutil
from .dashboard_metrics import calculate

ROOT=Path(r'C:\Bectanse\CopyPilot')
RUNTIME=Path(r'C:\Bectanse\CopyRealExecutor20261015\runtime.json')
OUT=Path(r'C:\Bectanse\DashboardReporting')


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value),encoding='utf-8');os.replace(temp,path)


def running(path):
    for p in psutil.process_iter(['exe']):
        try:
            if p.info['exe'] and os.path.normcase(p.info['exe'])==os.path.normcase(str(path)):return True
        except psutil.Error:pass
    return False


def publish(value):
    path='/internal/copy/dashboard';worker=os.environ['COPY_DEMO_WORKER_ID']
    body=json.dumps({'worker_id':worker,'snapshots':value},separators=(',',':')).encode()
    nonce=uuid.uuid4().hex;stamp=str(int(time.time()))
    canonical='\n'.join(('POST',path,stamp,nonce,hashlib.sha256(body).hexdigest())).encode()
    signature=hmac.new(os.environ['COPY_AGENT_WORKER_SECRET'].encode(),canonical,hashlib.sha256).hexdigest()
    response=requests.post(os.environ['COPY_CONFIG_BACKEND_URL'].rstrip('/')+path,data=body,
        headers={'Content-Type':'application/json','X-MT5-Worker-ID':worker,'X-MT5-Nonce':nonce,'X-MT5-Timestamp':stamp,'X-MT5-Signature':signature},timeout=(2,3),allow_redirects=False)
    if response.status_code!=200:raise ValueError('REPORT_HTTP_'+str(response.status_code))


def worker(config_path):
    import MetaTrader5 as mt5
    cfg=json.loads(Path(config_path).read_text());terminal=cfg['terminal'];attached=False
    deals={};history_at=0;deep_at=0;offset=cfg['offset'];health=OUT/(cfg['key']+'-health.json')
    def identity():
        a=mt5.account_info();t=mt5.terminal_info()
        if not a or not t or not t.connected or str(a.login)!=cfg['login'] or a.server!=cfg['server']:
            raise ValueError('ACCOUNT_IDENTITY_OR_CONNECTION_CHANGED')
        return a
    while True:
        try:
            if psutil.Process(cfg['supervisor_pid']).create_time()!=cfg['supervisor_started_at']:break
        except psutil.Error:break
        start=time.monotonic()
        try:
            if not running(terminal):raise ValueError('EXISTING_TERMINAL_REQUIRED')
            if not attached:
                if not mt5.initialize(terminal,timeout=3000):raise ValueError('ATTACH_FAILED')
                attached=True
            identity();now=time.time()
            if now-history_at>=5:
                full=not deals or now-deep_at>=1800
                begin=0 if full else history_at-60
                rows=mt5.history_deals_get(datetime.fromtimestamp(max(0,begin+offset),timezone.utc),datetime.fromtimestamp(now+offset+60,timezone.utc))
                if rows is None:raise ValueError('HISTORY_UNAVAILABLE')
                identity()
                if full:deals={};deep_at=now
                for d in rows:deals[d.ticket]={k:getattr(d,k) for k in ('ticket','order','position_id','time_msc','type','entry','symbol','volume','price','profit','commission','swap','fee','magic','comment','reason')}
                history_at=now
            positions=mt5.positions_get()
            if positions is None:raise ValueError('POSITIONS_UNAVAILABLE')
            a=identity();now=time.time()
            ps=[{k:getattr(p,k) for k in ('ticket','symbol','type','volume','profit','swap')} for p in positions]
            stats=calculate({k:getattr(a,k) for k in ('balance','equity','credit','currency')},ps,list(deals.values()),now=now,offset=offset)
            # History refresh is independent from live prices; expose its true age.
            stats['history_to']=history_at
            value=dict(login=cfg['login'],server=cfg['server'],at=now,statistics=stats,positions=ps,broker_time_offset_seconds=offset,journal_deals=list(deals.values()))
            write(OUT/(cfg['key']+'-snapshot.json'),value)
            write(health,dict(at=time.time(),state='REPORTING',history_at=history_at,history_deals=len(deals),history_reconciled=stats['history_reconciled'],cycle_ms=round((time.monotonic()-start)*1000)))
        except Exception as e:
            reason=str(e) if isinstance(e,ValueError) and len(str(e))<80 else type(e).__name__
            write(health,dict(at=time.time(),state='UNAVAILABLE',reason=reason))
            mt5.shutdown();attached=False
        time.sleep(max(.2,2-(time.monotonic()-start)))
    mt5.shutdown()


def discover():
    runtime=json.loads(RUNTIME.read_text(encoding='utf-8-sig'));items=[]
    for path in Path(runtime['executor_manifest_directory']).glob('*.json'):
        m=json.loads(path.read_text(encoding='utf-8-sig'));i=m['identity'];offset=m.get('broker_time_offsets',{}).get('follower')
        if offset is None:continue # never infer an unknown broker clock
        terminal=Path(runtime['terminals'])/hashlib.sha256((i['login']+'@'+i['server']).encode()).hexdigest()/'Terminal'/'terminal64.exe'
        items.append(dict(login=i['login'],server=i['server'],terminal=str(terminal),offset=offset))
    source=json.loads((ROOT/'State'/'master-telemetry.json').read_text());offset=json.loads(os.environ['COPY_DEMO_BROKER_TIME_OFFSETS'])['master']
    items.append(dict(login=source['login'],server=source['server'],terminal=str(ROOT/'Terminals'/'Master'/'terminal64.exe'),offset=offset))
    return items


def main():
    OUT.mkdir(exist_ok=True)
    if len(sys.argv)>1:return worker(sys.argv[1])
    children={};published_history={}
    while True:
        try:
            wanted={}
            for cfg in discover():
                cfg.update(supervisor_pid=os.getpid(),supervisor_started_at=psutil.Process().create_time())
                cfg['key']=hashlib.sha256((cfg['login']+'@'+cfg['server']).encode()).hexdigest();key=cfg['key'];wanted[key]=cfg
                if not running(cfg['terminal']):continue
                if key in children and children[key][0].poll() is None:
                    if children[key][1]==cfg:continue
                    children[key][0].terminate();children[key][0].wait(timeout=5) # reporting process only
                path=OUT/(key+'.json');write(path,cfg)
                child=subprocess.Popen([sys.executable,'-m','copy_trading.dashboard_collector',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:psutil.Process(child.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                except psutil.Error:pass
                children[key]=(child,cfg)
            for key in list(children):
                if key not in wanted:
                    children[key][0].terminate();children[key][0].wait(timeout=5);del children[key]
            snapshots=[]
            for key in wanted:
                path=OUT/(key+'-snapshot.json')
                if not path.exists():continue
                value=json.loads(path.read_text())
                if 0<=time.time()-value['at']<10:
                    history=value.pop('journal_deals',None)
                    history_at=value['statistics']['history_to']
                    if history is not None and published_history.get(key)!=history_at:
                        # Idempotent batches: acknowledge every native deal before
                        # publishing the corresponding financial totals.
                        for start in range(0,len(history),500):
                            publish([{**value,'journal_deals':history[start:start+500],'journal_only':True}])
                        published_history[key]=history_at
                    if history is not None:
                        value['journal_synced_at']=history_at
                        value['journal_ticket_ids']=[int(d['ticket']) for d in history]
                    snapshots.append(value)
            if snapshots:publish(snapshots)
            write(OUT/'health.json',dict(at=time.time(),state='REPORTING',workers=len(children),published=len(snapshots)))
        except Exception:write(OUT/'health.json',dict(at=time.time(),state='DISCOVERY_UNAVAILABLE'))
        time.sleep(2)

if __name__=='__main__':main()
