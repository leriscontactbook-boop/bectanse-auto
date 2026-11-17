"""Bounded, read-only terminal telemetry. Never an execution permission."""
import math
import time
from datetime import datetime, timezone


def clean(value, *, login, server, now=None):
    now=time.time() if now is None else now
    if not isinstance(value,dict) or value.get('login')!=str(login) or value.get('server')!=server:
        raise ValueError('TELEMETRY_IDENTITY_CHANGED')
    stamp=value.get('observed_at')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=now-stamp<=15:
        raise ValueError('TELEMETRY_STALE')
    def number(v):
        if isinstance(v,bool):raise ValueError('INVALID_TELEMETRY_NUMBER')
        n=float(v)
        if not math.isfinite(n):raise ValueError('INVALID_TELEMETRY_NUMBER')
        return n
    currency=value.get('currency')
    if not isinstance(currency,str) or not 1<=len(currency)<=12:raise ValueError('INVALID_CURRENCY')
    positions=value.get('positions')
    if not isinstance(positions,list) or len(positions)>100:raise ValueError('INVALID_POSITIONS')
    rows=[];seen=set()
    for p in positions:
        ticket=p.get('ticket');symbol=p.get('symbol');side=p.get('type')
        if (type(ticket) is not int or ticket<=0 or ticket in seen or type(side) is not int or side not in (0,1)
            or not isinstance(symbol,str) or not 1<=len(symbol)<=100):raise ValueError('INVALID_POSITION')
        seen.add(ticket)
        volume=number(p['volume'])
        if volume<=0:raise ValueError('INVALID_VOLUME')
        rows.append(dict(ticket=ticket,symbol=symbol,type=side,volume=volume,profit=number(p['profit'])))
    return dict(login=str(login),server=server,observed_at=stamp,currency=currency,
                balance=number(value['balance']),equity=number(value['equity']),positions=rows)


def capture(module,login,server):
    start=time.time();a=module.account_info();t=module.terminal_info();ps=module.positions_get()
    b=module.account_info()
    if (a is None or b is None or t is None or not t.connected or ps is None
        or any(str(x.login)!=str(login) or x.server!=server for x in (a,b))):
        raise ValueError('TELEMETRY_UNAVAILABLE')
    return clean(dict(login=str(login),server=server,observed_at=start,currency=b.currency,
        balance=b.balance,equity=b.equity,positions=[{k:getattr(p,k) for k in
        ('ticket','symbol','type','volume','profit')} for p in ps]),login=login,server=server)


def project(row, observation):
    fresh=observation['connected']
    row.update(online=fresh,last_seen=observation['statistics']['observed_at'],
        open_positions=len(observation['positions']) if fresh else None,
        positions=[{**p,'side':{0:'BUY',1:'SELL'}.get(p.get('type'),'—')} for p in observation['positions']] if fresh else [],
        statistics=observation['statistics'])


def master_view(conn,master):
    if not master:return master
    rows=conn.run("SELECT report FROM copy_agent_nodes WHERE received_at>NOW()-INTERVAL '15 seconds' ORDER BY received_at DESC")
    import json
    for (report,) in rows:
        if isinstance(report,str):report=json.loads(report)
        try:data=clean(report.get('master_telemetry'),login=master['login'],server=master['server'])
        except (ValueError,TypeError,KeyError):continue
        stamp=datetime.fromtimestamp(data['observed_at'],timezone.utc).isoformat()
        project(master,dict(connected=True,positions=data['positions'],statistics=dict(
            balance=data['balance'],equity=data['equity'],currency=data['currency'],fresh=True,
            observed_at=stamp,source='Terminal MT5 — maître',daily=None,weekly=None,monthly=None,total=None)))
        break
    return master
