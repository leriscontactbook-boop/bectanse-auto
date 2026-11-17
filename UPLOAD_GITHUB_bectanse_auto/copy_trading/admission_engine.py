"""Co-located admission laboratory boundary. No distributed lease claims.

Registry must be the authoritative shared LOCAL database, not a replicated export.
Use with the preparation web register on the same host only. No live-account support.
"""
from contextlib import contextmanager
import hashlib
import hmac
import json
import time
from pathlib import Path
from .client_preparation import Actor
from .admission_evidence import canonical, deliver_alerts
from .atomic_io import write_json
from .demo_mt5 import NotSent
from .compatibility import FIELDS, metadata


class AdmissionGate:
    def __init__(self, factory, account, login, server):
        self.factory,self.account,self.login,self.server=factory,account,str(login),server

    @contextmanager
    def submission(self):
        """Serialize final read through submission; NEVER label post-send errors NotSent."""
        r=None
        entered=False
        lock=None
        try:
            r,reader=self.factory()
            lock=r.account_lock(self.account)
            lock.__enter__()
            entered=True
            actor=Actor('admission-executor',True)
            data,_=r._identity(actor,self.account)
            if data['login'] != self.login or data['server'] != self.server or data['environment'] != 'DEMO':
                raise ValueError('IDENTITY_CHANGED')
            state=r.readiness(actor,self.account)
            if not state['preparation_ready']:
                raise ValueError('ADMISSION_BLOCKED')
            row=r.db.execute('SELECT evidence FROM admissions WHERE account=?',(self.account,)).fetchone()
            reader(json.loads(row['evidence'])['sha256'],data)
            entered=True
        except Exception as exc:
            if entered:lock.__exit__(None,None,None)
            if r:r.close()
            raise NotSent('ADMISSION_DENIED') from exc
        try:
            yield
        finally:
            # Release only the account lock, even on an ambiguous send.
            # Cleanup errors after entering must never be classified NotSent.
            if entered:
                try:lock.__exit__(None,None,None)
                finally:r.close()


def collect_live(mt5, identity, symbols, settings, destination, key, *, session_reader,
                 reconciliation_reader, clock=time.time):
    """Read an already attached terminal. No initialize/login/select/check/send.

    Session/reconciliation readers must return real evidence, not optimistic defaults.
    Any failure leaves the previous file untouched; its <=30s expiry fails closed.
    This produces live evidence only, NEVER a qualification report.
    """
    if len(key)<32 or not symbols:raise ValueError('COLLECTOR_CONFIGURATION_REQUIRED')
    now=clock();a=mt5.account_info();t=mt5.terminal_info()
    expected=(identity['login'],identity['server'],identity['broker'])
    if (a is None or t is None or (str(a.login),a.server,a.company)!=expected
            or identity['environment']!='DEMO' or a.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO
            or not t.connected or not a.trade_allowed or not a.trade_expert
            or not t.trade_allowed or t.tradeapi_disabled):raise ValueError('TERMINAL_NOT_READY')
    catalog={}
    for symbol in sorted(set(symbols)):
        s=mt5.symbol_info(symbol)
        if s is None:raise ValueError('SYMBOL_ABSENT')
        sessions=session_reader(symbol)
        if not 0<=now-sessions['observed_at']<=30:raise ValueError('SESSION_STALE')
        observation={'spec':{k:getattr(s,k,None) for k in FIELDS},
            'account':{k:getattr(a,k) for k in ('currency','margin_mode','trade_mode','leverage')},
            'terminal_build':t.build,'sessions':sessions['metadata']}
        catalog[symbol]=metadata(observation)
        # Validate exact requested volume; never round or select unavailable symbols.
        from .demo_policy import decimal
        amount=decimal(settings[symbol]);low=decimal(s.volume_min);step=decimal(s.volume_step)
        if amount<low or amount>decimal(s.volume_max) or amount%step:
            raise ValueError('VOLUME_NOT_EXECUTABLE')
    rec=reconciliation_reader()
    if rec.get('reconciled') is not True or not 0<=now-rec['observed_at']<=30:
        raise ValueError('RECONCILIATION_REQUIRED')
    end=mt5.account_info();terminal=mt5.terminal_info()
    if (end is None or terminal is None or (str(end.login),end.server,end.company)!=expected
            or end.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO or not terminal.connected
            or not end.trade_allowed or not end.trade_expert or not terminal.trade_allowed
            or terminal.tradeapi_disabled or clock()-now>30):raise ValueError('COLLECTION_CHANGED')
    payload={'identity':identity,'observed_at':now,'connected':True,'trade_allowed':True,
        'reconciled':True,'terminal_build':t.build,
        'catalog_observed':hashlib.sha256(canonical(catalog)).hexdigest(),
        'settings_observed':hashlib.sha256(canonical(settings)).hexdigest()}
    document={'payload':payload,'signature':hmac.new(key,canonical(payload),hashlib.sha256).hexdigest()}
    path=Path(destination)/(hashlib.sha256(canonical(identity)).hexdigest()+'.live.json')
    write_json(path,document)
    return payload


def monitor_once(factory, accounts, transport=None):
    """Per-account failures isolated. Caller schedules this every <=5 seconds.
    No automatic resume, order, or external transport configured here.
    """
    outcomes={}
    for account in accounts:
        r=None
        try:
            r,reader=factory();actor=Actor('admission-monitor',True)
            data,_=r._identity(actor,account)
            state=r.readiness(actor,account)
            if state['state']=='DEMO_VALIDATED':
                reason=None
                if not state['preparation_ready']:reason='SUBSCRIPTION_EXPIRED' if 'SUBSCRIPTION_REQUIRED' in state['reasons'] else 'REVIEW_REQUIRED'
                try:
                    row=r.db.execute('SELECT evidence FROM admissions WHERE account=?',(account,)).fetchone()
                    reader(json.loads(row['evidence'])['sha256'],data)
                except Exception:reason=reason or 'REVIEW_REQUIRED'
                if reason:r.transition(actor,account,'SUSPENDED',reason=reason)
            outcomes[account]=r.readiness(actor,account)['state']
            if transport:deliver_alerts(r,transport)
        except Exception:outcomes[account]='MONITOR_UNAVAILABLE'
        finally:
            if r:r.close()
    return outcomes


def monitor_loop(factory, accounts, stop, transport=None, interval=5):
    if not 0<interval<=5:raise ValueError('INVALID_MONITOR_INTERVAL')
    while not stop.is_set():
        monitor_once(factory,accounts,transport)
        stop.wait(interval)


def runtime_gate(manifest, backend, environ):
    """Explicit local LAB wiring; absence of config denies new opens in DemoMT5.
    Never interpret a local replica of a central register as authoritative.
    """
    from .admission_evidence import SignedEvidence
    from .client_preparation import PreparationRegister
    path=environ.get('ADMISSION_LAB_CONFIG')
    if not path:return None
    config=json.loads(Path(path).read_text())
    if config.get('topology')!='COLOCATED_LAB':raise ValueError('ADMISSION_TOPOLOGY_UNSUPPORTED')
    identity=config['identity'];account=config['account_id']
    if type(account) is not int or account<=0 or (identity['login'],identity['server']) != (str(manifest['follower']['login']),manifest['follower']['server']):
        raise ValueError('ADMISSION_IDENTITY_MISMATCH')
    registry=Path(config['registry'])
    if not registry.is_file():raise ValueError('AUTHORITATIVE_REGISTRY_REQUIRED')
    reader=SignedEvidence(config['evidence_root'],environ.get('ADMISSION_EVIDENCE_KEY','').encode())
    def access(member):
        c=backend.configuration(manifest)  # no authorization cached between orders
        allowed=(member==identity['member'] and c.get('account_id')==account
                 and str(c.get('login'))==identity['login'] and c.get('server')==identity['server']
                 and c.get('enabled') is True)
        return allowed,c.get('valid_until',0)
    def factory():
        return PreparationRegister(registry,account_reader=lambda n:identity if n==account else None,
                                   access_reader=access,clock=time.time),reader
    return AdmissionGate(factory,account,identity['login'],identity['server'])


class HttpsAlertTransport:
    """Explicit HTTPS endpoint; expects {'ack_id': idempotency_key}. No auto wiring."""
    def __init__(self, url, bearer, namespace, post=None):
        from urllib.parse import urlsplit
        from .client_preparation import token
        address=urlsplit(url)
        if address.scheme!='https' or not address.hostname or address.username or address.password or address.fragment:
            raise ValueError('INVALID_ALERT_ENDPOINT')
        if not isinstance(bearer,str) or not bearer or '\n' in bearer or '\r' in bearer:
            raise ValueError('INVALID_ALERT_CREDENTIAL')
        token(namespace)
        if post is None:
            import requests
            post=requests.post
        self.url,self.bearer,self.namespace,self.post=url,bearer,namespace,post

    def __call__(self, event):
        key=self.namespace+':'+str(event['id'])
        response=self.post(self.url,json={k:event[k] for k in ('id','account_id','reason','at')},
            headers={'Authorization':'Bearer '+self.bearer,'Idempotency-Key':key},
            timeout=(3,5),allow_redirects=False)
        return 200<=response.status_code<300 and response.json().get('ack_id')==key
