"""Read-only, paginated broker journal for an administrator's selected account."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def journal(conn, member, account, month, page=0, day=None):
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', month or ''):
        raise ValueError('Mois invalide.')
    page=int(page)
    if page<0:raise ValueError('Page invalide.')
    if day and (not re.fullmatch(re.escape(month)+r'-\d{2}',day)):
        raise ValueError('Jour invalide.')
    if day:datetime.strptime(day,'%Y-%m-%d')
    rows=conn.run('SELECT currency,last_successful_sync_at FROM trading_accounts WHERE id=:a AND user_id=:m',a=int(account),m=member)
    if not rows:raise ValueError('Compte introuvable pour ce client.')
    currency,sync=rows[0]
    start=datetime.fromisoformat(month+'-01').replace(tzinfo=ZoneInfo('Europe/Paris'))
    end=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
    # A terminal can return a partial window. Never hide previously imported deals.
    scope=''
    coverage=conn.run('SELECT MIN(executed_at),MAX(executed_at),COUNT(*) FROM trading_deals WHERE trading_account_id=:a'+scope,a=int(account))[0]
    daily=conn.run("""SELECT (executed_at AT TIME ZONE 'Europe/Paris')::date,
      SUM(profit+commission+swap+fee),COUNT(*) FILTER (WHERE entry_type IN ('OUT','OUT_BY','INOUT'))
      FROM trading_deals WHERE trading_account_id=:a AND is_trading_deal=TRUE AND executed_at>=:s AND executed_at<:e
      """+scope+" GROUP BY 1 ORDER BY 1",a=int(account),s=start,e=end)
    params={'a':int(account)}
    clause='trading_account_id=:a'+scope
    if day:
        clause+=" AND (executed_at AT TIME ZONE 'Europe/Paris')::date=CAST(:day AS DATE)";params['day']=day
    count=conn.run('SELECT COUNT(*) FROM trading_deals WHERE '+clause,**params)[0][0]
    data=conn.run('''SELECT mt5_deal_ticket,mt5_position_id,symbol,deal_type,entry_type,volume,CASE WHEN reason='RECOVERED_NATIVE_NO_PRICE' THEN NULL ELSE price END,
      profit,commission,swap,fee,executed_at,is_trading_deal FROM trading_deals WHERE '''+clause+
      ' ORDER BY executed_at DESC,mt5_deal_ticket DESC LIMIT 100 OFFSET :offset',**params,offset=page*100)
    keys=('ticket','position','symbol','type','entry','volume','price','profit','commission','swap','fee','at','trading')
    def serial(v):return v.isoformat() if hasattr(v,'isoformat') else str(v) if v is not None else None
    return dict(native_inventory=bool(scope),currency=currency or '',synced_at=serial(sync),first_at=serial(coverage[0]),last_at=serial(coverage[1]),
      total_count=int(coverage[2]),filtered_count=int(count),page=page,has_more=(page+1)*100<count,
      days=[dict(day=str(d),net=float(net or 0),closed_deals=int(n)) for d,net,n in daily],
      deals=[{k:(v if isinstance(v,bool) else serial(v)) for k,v in zip(keys,row)} for row in data])


def native_fallback(conn,member,account,month,page,day,result):
    """Display received broker history when the separate journal has no import.

    Native observations have broker wall-clock seconds, not verified UTC dates.
    Keep their clock explicit instead of inventing a timezone conversion.
    """
    import json
    from datetime import timezone
    from collections import defaultdict
    if result['total_count'] or result.get('native_inventory'):return result
    if not conn.run("SELECT to_regclass('copy_real_observations')")[0][0]:return result
    rows=conn.run('SELECT payload FROM copy_real_observations WHERE member_code=:m AND account_id=:a ORDER BY version DESC',m=member,a=int(account))
    deals={};latest=0;start=None;currency=result['currency']
    for raw, in rows:
        obs=json.loads(raw) if isinstance(raw,str) else raw
        if not obs.get('deals'):continue
        latest=max(latest,obs.get('observed_at') or 0)
        start=min(start,obs['history_from']) if start is not None and obs.get('history_from') is not None else obs.get('history_from',start)
        currency=currency or obs.get('currency','')
        for x in obs['deals']:
            if str(x['ticket']) in deals:continue
            at=datetime.fromtimestamp(x['time'],timezone.utc).isoformat()
            deals[str(x['ticket'])]=dict(ticket=str(x['ticket']),position=str(x.get('position_id','')),symbol=x.get('symbol',''),
                type={0:'BUY',1:'SELL',2:'BALANCE',3:'CREDIT'}.get(x['type'],'OTHER'),
                entry={0:'IN',1:'OUT',2:'INOUT',3:'OUT_BY'}.get(x.get('entry'),'OTHER'),
                volume=x.get('volume'),price=x.get('price'),profit=x.get('profit',0),commission=x.get('commission',0),
                swap=x.get('swap',0),fee=x.get('fee',0),at=at,trading=x['type'] in (0,1))
    ordered=sorted(deals.values(),key=lambda x:(x['at'],int(x['ticket'])),reverse=True);daily=defaultdict(lambda:{'net':0,'closed_deals':0})
    for x in ordered:
        if x['trading'] and x['at'].startswith(month):
            a=daily[x['at'][:10]];a['net']+=sum(float(x[k] or 0) for k in ('profit','commission','swap','fee'));a['closed_deals']+=int(x['entry'] in ('OUT','OUT_BY','INOUT'))
    filtered=[x for x in ordered if not day or x['at'].startswith(day)]
    return {**result,'currency':currency,'clock':'BROKER','partial':True,'synced_at':datetime.fromtimestamp(latest,timezone.utc).isoformat() if latest else None,
        'first_at':ordered[-1]['at'] if ordered else None,'last_at':ordered[0]['at'] if ordered else None,
        'total_count':len(ordered),'filtered_count':len(filtered),'has_more':(int(page)+1)*100<len(filtered),
        'days':[dict(day=k,**v) for k,v in sorted(daily.items())],'deals':filtered[int(page)*100:(int(page)+1)*100]}
