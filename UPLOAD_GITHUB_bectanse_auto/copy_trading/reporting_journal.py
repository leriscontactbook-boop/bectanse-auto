"""Persist authenticated reporting history. No trading or terminal operations."""
import json
from datetime import datetime, timezone
from .broker_clock import offset_seconds
from .dashboard_metrics import dec

PNL_TYPES = {0, 1, 7, 8, 9, 10, 11, 12}
TYPES = {0:'BUY',1:'SELL',2:'BALANCE',3:'CREDIT',7:'COMMISSION',8:'COMMISSION_DAILY',
         9:'COMMISSION_MONTHLY',10:'COMMISSION_AGENT_DAILY',11:'COMMISSION_AGENT_MONTHLY',12:'INTEREST'}


def normalize(rows, offset, now):
    offset=offset_seconds(offset)
    if not isinstance(rows,list) or len(rows)>500:raise ValueError('INVALID_HISTORY_BATCH')
    out=[];seen=set()
    for d in rows:
        ticket=int(d['ticket']);kind=int(d['type']);stamp=int(d['time_msc'])/1000-offset
        if ticket<=0 or ticket in seen or not 0<=stamp<=now+2:raise ValueError('INVALID_NATIVE_DEAL')
        seen.add(ticket)
        row=dict(ticket=ticket,order_ticket=int(d['order']),position_id=int(d['position_id']),
                 symbol=str(d['symbol'])[:100],deal_type=TYPES.get(kind,'OTHER'),
                 entry_type={0:'IN',1:'OUT',2:'INOUT',3:'OUT_BY'}.get(d['entry'],'OTHER'),
                 reason=str(d['reason'])[:40],is_trading=kind in PNL_TYPES,
                 magic=int(d['magic']),comment=str(d['comment'])[:500],
                 executed_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat())
        for k in ('volume','price','profit','commission','swap','fee'):row[k]=str(dec(d[k]))
        if dec(row['volume'])<0 or dec(row['price'])<0:raise ValueError('INVALID_NATIVE_DEAL')
        out.append(row)
    return out


def persist(conn, account, rows, currency):
    # Account identity is resolved by the authenticated endpoint, never supplied
    # by the reporting process. The existing unique key prevents double imports.
    conn.run('''INSERT INTO trading_deals
        (trading_account_id,mt5_deal_ticket,mt5_order_ticket,mt5_position_id,symbol,
         deal_type,entry_type,reason,is_trading_deal,volume,price,profit,commission,swap,fee,
         magic_number,comment,executed_at)
        SELECT :a,ticket,order_ticket,position_id,symbol,deal_type,entry_type,reason,
         is_trading,volume,price,profit,commission,swap,fee,magic,comment,executed_at
        FROM jsonb_to_recordset(CAST(:payload AS JSONB)) AS x(ticket BIGINT,order_ticket BIGINT,
         position_id BIGINT,symbol TEXT,deal_type TEXT,entry_type TEXT,reason TEXT,is_trading BOOLEAN,
         volume NUMERIC,price NUMERIC,profit NUMERIC,commission NUMERIC,swap NUMERIC,fee NUMERIC,
         magic BIGINT,comment TEXT,executed_at TIMESTAMPTZ)
        ON CONFLICT(trading_account_id,mt5_deal_ticket) DO UPDATE SET
         mt5_order_ticket=EXCLUDED.mt5_order_ticket,mt5_position_id=EXCLUDED.mt5_position_id,
         symbol=EXCLUDED.symbol,deal_type=EXCLUDED.deal_type,entry_type=EXCLUDED.entry_type,
         reason=EXCLUDED.reason,is_trading_deal=EXCLUDED.is_trading_deal,volume=EXCLUDED.volume,
         price=EXCLUDED.price,profit=EXCLUDED.profit,commission=EXCLUDED.commission,
         swap=EXCLUDED.swap,fee=EXCLUDED.fee,magic_number=EXCLUDED.magic_number,
         comment=EXCLUDED.comment,executed_at=EXCLUDED.executed_at,updated_at=NOW()''',
         a=account,payload=json.dumps(rows))
    conn.run('UPDATE trading_accounts SET currency=:c WHERE id=:a',a=account,c=currency)


def accumulated_statistics(conn, account, payload):
    """Use the durable account history, never a shrinking terminal window."""
    from .dashboard_metrics import calculate
    rows=conn.run("""SELECT mt5_deal_ticket,deal_type,EXTRACT(EPOCH FROM executed_at),
        profit,commission,swap,fee FROM trading_deals WHERE trading_account_id=:a""",a=account)
    kinds={v:k for k,v in TYPES.items()}
    deals=[dict(ticket=t,type=kinds.get(kind,-1),time_msc=float(stamp)*1000,
                profit=profit,commission=commission,swap=swap,fee=fee)
           for t,kind,stamp,profit,commission,swap,fee in rows]
    stats=calculate(payload['statistics'],payload['positions'],deals,now=payload['at'],offset=0)
    stats['history_to']=payload['statistics']['history_to']
    payload['statistics']=stats
    payload['journal_ticket_ids']=[int(r[0]) for r in rows]
    return payload
