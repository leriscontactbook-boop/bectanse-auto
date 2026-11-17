"""Read native instrument evidence inside an already-owned terminal session.

No login, symbol selection, order check, submission or compatibility approval.
The MQL session file is terminal-local and validated against live SDK identity.
"""
import csv
import ntpath
import os
import time
import uuid
from pathlib import Path
from .compatibility import FIELDS, digest
from .symbol_mapping import REQUIRED
from .demo_policy import SafetyError


class NativeSessions:
    def __init__(self, module, clock=time.time):
        self.module,self.clock=module,clock

    def clock_offset(self):
        """Measure server/UTC offset from the identity-checked native reader."""
        from datetime import datetime, timezone
        from .broker_clock import offset_seconds
        a,t=self.module.account_info(),self.module.terminal_info()
        if a is None or t is None or not t.connected:raise SafetyError('NATIVE_SESSION_TERMINAL_UNAVAILABLE')
        try:
            path=Path(t.data_path)/'MQL5'/'Files'/'BectanseNativeSessions.csv'
            if path.stat().st_size>2_000_000:raise ValueError()
            with path.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
            row=rows[0]
            if (len(row)!=10 or row[0]!='IDENTITY' or row[1:5]!=[str(a.login),a.server,str(a.trade_mode),str(t.build)]
                or ntpath.normcase(row[5])!=ntpath.normcase(t.path) or rows[-1]!=['END',row[9]]):raise ValueError()
            observed=float(row[6]);day=int(row[7]);second=int(row[8])
            if not 0<=self.clock()-observed<=3 or day not in range(7) or not 0<=second<86400:raise ValueError()
            utc=datetime.fromtimestamp(observed,timezone.utc)
            seconds=((utc.weekday()+1)%7)*86400+utc.hour*3600+utc.minute*60+utc.second
            difference=(day*86400+second-seconds+302400)%604800-302400
            offset=round(difference/900)*900
            if abs(difference-offset)>2:raise ValueError()
            return offset_seconds(offset)
        except (OSError,ValueError,IndexError,TypeError):
            raise SafetyError('NATIVE_SERVER_CLOCK_UNAVAILABLE') from None

    def __call__(self, symbol):
        a,t=self.module.account_info(),self.module.terminal_info()
        if a is None or t is None or not t.connected:
            raise SafetyError('NATIVE_SESSION_TERMINAL_UNAVAILABLE')
        path=Path(t.data_path)/'MQL5'/'Files'/'BectanseNativeSessions.csv'
        try:
            if path.stat().st_size>2_000_000:raise ValueError()
            with path.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
            identity=rows[0]
            if (identity[:1]!=['IDENTITY'] or len(identity)!=10
                or identity[1:5]!=[str(a.login),a.server,str(a.trade_mode),str(t.build)]
                or ntpath.normcase(identity[5])!=ntpath.normcase(t.path)):
                raise ValueError()
            observed=float(identity[6]);day=int(identity[7]);second=int(identity[8]);generation=identity[9]
            if not 0<=self.clock()-observed<=3 or day not in range(7) or not 0<=second<86400:raise ValueError()
            if rows[-1]!=['END',generation]:raise ValueError()
            schedule={};errors=[]
            for row in rows[1:-1]:
                if len(row)<2 or row[1]!=symbol:continue
                if row[0]=='ERROR':errors.append(row)
                elif row[0]=='DAY' and len(row)==4:
                    d=int(row[2])
                    if d not in range(7) or d in schedule:raise ValueError()
                    intervals=[]
                    for interval in row[3].split(';') if row[3] else []:
                        start,end=map(int,interval.split(':'))
                        # A zero-length native placeholder grants no trading window.
                        # Keep any explicit nonzero sessions; never infer a full day.
                        if start==0 and end==0:continue
                        if not 0<=start<end<=86400:raise ValueError()
                        intervals.append([start,end])
                    schedule[d]=intervals
            if errors or set(schedule)!=set(range(7)):raise ValueError()
            # Native server day/seconds are supplied by MQL, never guessed from
            # the Mac clock or from a quote. Unsupported overnight forms deny.
            return {'metadata':{'timezone':'MT5_SERVER_TIME','schedule':{str(k):v for k,v in schedule.items()},
                                 'source':'MQL5.SymbolInfoSessionTrade'},
                    'open':any(start<=second<end for start,end in schedule[day]),
                    'observed_at':observed}
        except (OSError,ValueError,IndexError,TypeError):
            raise SafetyError('NATIVE_SESSION_EVIDENCE_UNAVAILABLE') from None


def collect(broker, symbols, *, reconciliation, clock=time.time, broker_time_offset_seconds=None):
    """Capture only exact requested symbols, with explicit missing evidence.

    This output has no QUALIFIED status. Caller persists it with the source
    snapshot, or signs it with the existing worker transport key.
    """
    from .broker_clock import quote_utc, offset_seconds
    offset=offset_seconds(getattr(broker,'broker_time_offset',0) if broker_time_offset_seconds is None else broker_time_offset_seconds)
    broker.guard();module=broker.mt5;account=module.account_info();terminal=module.terminal_info()
    identity=(str(account.login),account.server,account.company,account.trade_mode,terminal.path,terminal.build)
    requested=sorted(set(symbols))
    if len(requested)>256 or any(not isinstance(s,str) or not s or '\n' in s or '\r' in s for s in requested):
        raise SafetyError('INSTRUMENT_COLLECTION_LIMIT')
    directory=Path(terminal.data_path)/'MQL5'/'Files'
    try:directory.mkdir(parents=True,exist_ok=True)
    except OSError:raise SafetyError('NATIVE_SESSION_DIRECTORY_UNAVAILABLE') from None
    temporary=directory/('symbols-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text('\n'.join(requested)+'\n',encoding='utf-8')
        os.replace(temporary,directory/'BectanseNativeSymbols.txt')
    finally:temporary.unlink(missing_ok=True)
    rows=module.symbols_get()
    if rows is None:raise SafetyError('INSTRUMENT_CATALOG_UNAVAILABLE')
    counts={name:sum(getattr(row,'name',None)==name for row in rows) for name in requested}
    result={'source':'MT5_READ_ONLY','observed_at':clock(),
            'identity':{'account':str(account.login)+'@'+account.server,'trade_mode':account.trade_mode},
            'broker':account.company,'terminal':{'path_hash':digest(terminal.path),'build':terminal.build,
                                                'api_version':getattr(module,'__version__',None)},
            'account':{k:getattr(account,k,None) for k in ('currency','margin_mode','trade_mode','leverage')},
            'instruments':{},'errors':{}}
    sessions=NativeSessions(module,clock)
    for symbol in requested:
        if counts[symbol]!=1:
            result['errors'][symbol]='SYMBOL_ABSENT' if counts[symbol]==0 else 'SYMBOL_AMBIGUOUS';continue
        spec=module.symbol_info(symbol);tick=module.symbol_info_tick(symbol)
        if spec is None or getattr(spec,'name',None)!=symbol:
            result['errors'][symbol]='SYMBOL_CHANGED_DURING_COLLECTION';continue
        item={'spec':{k:getattr(spec,k,None) for k in REQUIRED},'observed_at':clock(),
              'quote_at':None,'quote_time_msc_raw':getattr(tick,'time_msc',None),
              'broker_time_offset_seconds':offset,'bid':getattr(tick,'bid',None),
              'ask':getattr(tick,'ask',None),'reconciliation':dict(reconciliation)}
        try:item['quote_at']=quote_utc(getattr(tick,'time_msc',None),offset)
        except ValueError:item['error']='INVALID_QUOTE_TIMESTAMP'
        try:item['session']=sessions(symbol)
        except SafetyError as error:item['error']=str(error)
        if any(item['spec'][k] is None for k in FIELDS):item['error']='INSTRUMENT_FIELDS_MISSING'
        result['instruments'][symbol]=item
    broker.guard();after=module.account_info();end=module.terminal_info()
    if identity!=(str(after.login),after.server,after.company,after.trade_mode,end.path,end.build):
        raise SafetyError('INSTRUMENT_IDENTITY_CHANGED')
    result['completed_at']=clock()
    if not 0<=result['completed_at']-result['observed_at']<=3:
        raise SafetyError('INSTRUMENT_COLLECTION_STALE')
    return result
