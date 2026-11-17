"""Read-only native evidence capture. Never grants broker qualification.

Snapshots retain missing fields/errors rather than inventing session schedules.
The proxy makes an accidental send/login/selection call impossible here.
"""
import hashlib,json,platform,time
from pathlib import Path

class ReadOnlyMT5:
    METHODS=frozenset({'account_info','terminal_info','version','last_error','symbol_info','symbol_info_tick',
                      'symbols_get','positions_get','orders_get','history_orders_get','history_deals_get'})
    def __init__(self,module):self._module=module
    def __getattr__(self,name):
        if name in self.METHODS or name.isupper() or name=='__version__':return getattr(self._module,name)
        raise RuntimeError('READ_ONLY_MT5_METHOD_FORBIDDEN')

def plain(value):
    if hasattr(value,'_asdict'):return {k:plain(v) for k,v in value._asdict().items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    return value

def capture(module,*,login,server,terminal,mode,symbols,ledger_path,clock=time.time):
    from .demo_mt5 import DemoMT5
    from .real_mt5 import RealMT5
    from .demo_engine import Observer
    from .demo_store import Ledger
    if mode not in {'DEMO','REAL'}:raise ValueError('EXPLICIT_ACCOUNT_MODE_REQUIRED')
    readonly=ReadOnlyMT5(module)
    adapter=(DemoMT5 if mode=='DEMO' else RealMT5)(readonly,login=login,server=server,terminal_path=terminal,writable=False,clock=clock)
    account=adapter.guard();source=str(login)+'@'+server
    # Separate capture ledger only; caller must never pass an active engine DB.
    ledger=Ledger(ledger_path)
    try:
        ledger.pin('qualification_capture',dict(source=source,mode=mode))
        snapshot=Observer(ledger,adapter,source,clock).cycle()
        specs={s:plain(readonly.symbol_info(s)) for s in symbols}
        after=adapter.guard()
        if after.login!=account.login:raise RuntimeError('IDENTITY_CHANGED')
        t=readonly.terminal_info()
        result=dict(schema=1,source='MT5_NATIVE_READ_ONLY',observed_at=clock(),environment=mode,
                    identity=dict(login=str(account.login),server=account.server,company=account.company,trade_mode=account.trade_mode),
                    terminal=dict(build=t.build,path=t.path,api_version=getattr(readonly,'__version__',None)),
                    snapshot=snapshot,symbols=specs,
                    qualification='NOT_QUALIFIED',missing=['TRADING_SESSION_SOURCE','ASSET_EQUIVALENCE_REVIEW','NATIVE_EXECUTION_TESTS'],
                    native_orders_sent=0)
        return result
    finally:ledger.close()

def save(path,result):
    # Exclusive evidence output: historical captures cannot be overwritten.
    payload=json.dumps(result,sort_keys=True,allow_nan=False,default=str).encode()
    with Path(path).open('xb') as stream:stream.write(payload)
    return hashlib.sha256(payload).hexdigest()
