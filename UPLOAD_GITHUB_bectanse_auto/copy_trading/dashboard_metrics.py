"""Pure reporting calculations. No execution authority or terminal calls."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import math


def dec(value):
    n=Decimal(str(value))
    if not n.is_finite():raise ValueError('INVALID_NUMBER')
    return n


def calculate(account, positions, deals, *, now, offset, history_from=0):
    """Native deal timestamps use the explicitly configured broker offset.

    Evolution = net booked trading P&L / net balance deposits, since available
    history began; credits excluded. Only published if the balance reconciles.
    Equity % = floating profit including position swap / balance (not credit).
    """
    balance=dec(account['balance']);credit=dec(account['credit'])
    floating=sum((dec(p['profit'])+dec(p.get('swap',0)) for p in positions),Decimal(0))
    local=datetime.fromtimestamp(now,timezone.utc).astimezone(ZoneInfo('Europe/Paris'))
    day=local.replace(hour=0,minute=0,second=0,microsecond=0)
    boundaries={'daily':day.timestamp(),'weekly':(day-timedelta(days=day.weekday())).timestamp(),
                'monthly':day.replace(day=1).timestamp(),'total':history_from}
    totals={k:Decimal(0) for k in boundaries};deposits=withdrawals=credits=adjustments=Decimal(0)
    seen=set();max_stamp=0;known=True
    for d in deals:
        ticket=str(d['ticket'])
        if ticket in seen:continue
        seen.add(ticket)
        stamp=float(d['time_msc'])/1000-offset
        if stamp>now+2:raise ValueError('BROKER_CLOCK_MISMATCH')
        if stamp<history_from:continue
        max_stamp=max(max_stamp,stamp)
        kind=int(d['type']);profit=dec(d['profit'])
        net=profit+sum((dec(d.get(k,0)) for k in ('commission','swap','fee')),Decimal(0))
        if kind in (0,1,7,8,9,10,11,12): # executions and explicit commission/interest charges
            for key,start in boundaries.items():
                if stamp>=start:totals[key]+=net
        elif kind==2:
            if profit>=0:deposits+=profit
            else:withdrawals-=profit
            adjustments+=net-profit
        elif kind==3:credits+=net
        else:
            adjustments+=net
            # Corrections/cancelled trades/dividends need a broker-specific policy.
            known=False
    net_deposits=deposits-withdrawals
    reconciled=abs(net_deposits+adjustments+totals['total']-balance)<=Decimal('.02')
    complete=history_from==0 and reconciled and known
    out={k:float(v) if history_from<=boundaries[k] else None for k,v in totals.items()}
    out.update(balance=float(balance),equity=float(dec(account['equity'])),credit=float(credit),
        currency=account['currency'],platform='MT5',open_pnl=float(floating),
        equity_percent=float(floating/balance*100) if balance>0 else None,
        evolution_percent=float(totals['total']/net_deposits*100) if complete and net_deposits>0 else None,
        deposits=float(deposits),withdrawals=float(withdrawals),adjustments=float(adjustments),
        history_credit_net=float(credits),history_complete=complete,history_reconciled=reconciled,
        history_from=history_from,history_to=now,history_deals=len(seen),history_last_deal_at=max_stamp,
        observed_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),fresh=True,
        source='Courtier MT5',total_scope='Historique disponible',
        evolution_basis='P&L net réalisé / dépôts nets, hors crédits',
        equity_basis='P&L ouvert, swaps inclus / solde, hors crédits')
    return out


def validate_snapshot(value,now):
    if not isinstance(value,dict):raise ValueError('INVALID_SNAPSHOT')
    if not isinstance(value.get('login'),str) or not value['login'].isdigit():raise ValueError('INVALID_LOGIN')
    if not isinstance(value.get('server'),str) or not 1<=len(value['server'])<=120:raise ValueError('INVALID_SERVER')
    stamp=value['at']
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=now-stamp<=30:raise ValueError('STALE_SNAPSHOT')
    stats=value['statistics'];ps=value['positions']
    if not isinstance(ps,list) or len(ps)>1000:raise ValueError('INVALID_POSITIONS')
    allowed={'balance','equity','credit','open_pnl','equity_percent','evolution_percent','daily','weekly','monthly','total','deposits','withdrawals','adjustments','history_credit_net','history_from','history_to','history_deals','history_last_deal_at'}
    clean={k:(float(dec(stats[k])) if stats.get(k) is not None else None) for k in allowed}
    currency=stats.get('currency','')
    if not isinstance(currency,str) or not 1<=len(currency)<=12:raise ValueError('INVALID_CURRENCY')
    clean.update(currency=currency,platform='MT5',observed_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat(),source='Courtier MT5',fresh=True,
                 history_complete=stats.get('history_complete') is True,history_reconciled=stats.get('history_reconciled') is True)
    positions=[];seen=set()
    for p in ps:
        ticket=int(p['ticket'])
        if ticket<=0 or ticket in seen or p['type'] not in (0,1):raise ValueError('INVALID_POSITION')
        seen.add(ticket)
        symbol=p['symbol']
        if not isinstance(symbol,str) or not 1<=len(symbol)<=100:raise ValueError('INVALID_SYMBOL')
        positions.append(dict(ticket=ticket,symbol=symbol,type=p['type'],side='BUY' if p['type']==0 else 'SELL',volume=float(dec(p['volume'])),profit=float(dec(p['profit'])),swap=float(dec(p.get('swap',0)))))
    if abs(sum(p['profit']+p['swap'] for p in positions)-clean['open_pnl'])>.02:raise ValueError('PNL_MISMATCH')
    out=dict(login=value['login'],server=value['server'],at=stamp,statistics=clean,positions=positions)
    if 'journal_synced_at' in value:
        synced=float(dec(value['journal_synced_at']))
        if not 0<=synced<=stamp or synced!=clean['history_to']:raise ValueError('INVALID_HISTORY_TIME')
        out['journal_synced_at']=synced
        ids=value.get('journal_ticket_ids')
        if not isinstance(ids,list) or len(ids)>20000 or any(type(t) is not int or t<=0 for t in ids) or len(ids)!=len(set(ids)):raise ValueError('INVALID_HISTORY_INVENTORY')
        out['journal_ticket_ids']=ids
    if 'journal_deals' in value:
        from .reporting_journal import normalize
        out['journal_deals']=normalize(value['journal_deals'],value['broker_time_offset_seconds'],stamp)
        out['journal_only']=value.get('journal_only') is True
    return out


def apply_snapshots(conn,master,followers,now=None):
    import time,json
    now=time.time() if now is None else now
    if not conn.run("SELECT to_regclass('copy_dashboard_snapshots')")[0][0]:return
    rows=conn.run("""SELECT s.account_id,s.payload FROM copy_dashboard_snapshots s WHERE s.account_id=0 OR EXISTS(
        SELECT 1 FROM trading_accounts a WHERE a.id=s.account_id AND a.login=s.payload->>'login' AND a.server=s.payload->>'server')""")
    lookup={int(k):(json.loads(v) if isinstance(v,str) else v) for k,v in rows}
    for row in ([master] if master else [])+followers:
        item=lookup.get(0 if row is master else row.get('account_id'))
        if not item or item['server']!=row.get('server'):continue
        if row is master and item['login']!=str(master['login']):continue
        fresh=0<=now-item['at']<=15
        stats={**item['statistics'],'fresh':fresh}
        # Never overwrite newer terminal values with a reporting sample.
        if not fresh:
            for k in ('open_pnl','equity_percent','evolution_percent'):stats[k]=None
        row['statistics']=stats
        row['positions']=item['positions'] if fresh else []
        row['open_positions']=len(item['positions']) if fresh else None
