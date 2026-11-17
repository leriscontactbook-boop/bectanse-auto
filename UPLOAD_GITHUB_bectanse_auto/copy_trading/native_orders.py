"""Native MT5 pending-order requests and authentic read-side reconciliation.

No terminal connection, activation or send occurs here. RealMT5 retains the
qualification and authoritative submission boundary around every request.
Reference: https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py
"""
from datetime import datetime, timezone
from .demo_policy import SafetyError, decimal

KINDS = frozenset({'PENDING_CREATE', 'PENDING_MODIFY', 'PENDING_CANCEL'})
TERMINAL_STATES = {2: 'CANCELLED', 4: 'FILLED', 5: 'REJECTED', 6: 'EXPIRED'}


def native_order(row):
    return dict(ticket=str(row.ticket), symbol=row.symbol, magic=row.magic,
                type=row.type, state=row.state, volume_initial=str(row.volume_initial),
                volume_current=str(row.volume_current), price=str(row.price_open),
                stoplimit=str(row.price_stoplimit), sl=str(row.sl), tp=str(row.tp),
                type_time=row.type_time, expiration=row.time_expiration,
                position_id=str(row.position_id), setup_msc=row.time_setup_msc)


def read_native_orders(broker):
    broker.guard()
    rows=broker.mt5.orders_get()
    if rows is None:raise SafetyError('ORDERS_UNAVAILABLE')
    # orders_get can briefly expose market/SL/TP executions too. Only MT5
    # pending types belong to this lifecycle; positions/deals track market exits.
    result={str(row.ticket):native_order(row) for row in rows if row.type in range(2,8)}
    broker.guard()
    return result


def reconcile_native(broker, *, magic, symbol, order_type, since, ticket=None):
    """Missing data never proves cancellation or permits resubmission.

    Filled volume is derived from authenticated deal history for the order.
    A partial fill retains both its broker order and its position identifiers.
    """
    broker.guard()
    current=broker.mt5.orders_get()
    history=broker.mt5.history_orders_get(
        datetime.fromtimestamp(since + broker.broker_time_offset - 300,timezone.utc),
        datetime.fromtimestamp(broker.clock() + broker.broker_time_offset + 1,timezone.utc))
    if current is None or history is None:raise SafetyError('ORDER_HISTORY_UNAVAILABLE')
    rows=[r for r in (*history,*current) if (str(r.ticket)==str(ticket) if ticket is not None else r.magic==magic)]
    if any(r.magic!=magic for r in rows):raise SafetyError('ORDER_IDENTITY_MISMATCH')
    if any(r.symbol!=symbol or r.type!=order_type for r in rows):
        raise SafetyError('ORDER_IDENTITY_MISMATCH')
    ids={str(r.ticket) for r in rows}
    if ticket is not None and ids and ids!={str(ticket)}:raise SafetyError('ORDER_IDENTITY_MISMATCH')
    if len(ids)>1:raise SafetyError('AMBIGUOUS_ORDER_EXECUTION')
    if not rows:return None
    identity=str(rows[0].ticket)
    working=[r for r in current if str(r.ticket)==identity]
    archived=[r for r in history if str(r.ticket)==identity]
    row=working[0] if working else max(archived,key=lambda r:r.time_done_msc)
    deals=[r for r in broker.deals(since-300) if str(r.order)==identity and r.type in (0,1)]
    if any(r.magic!=magic or r.symbol!=symbol or r.entry!=0 for r in deals):
        raise SafetyError('ORDER_DEAL_IDENTITY_MISMATCH')
    filled=sum((decimal(r.volume) for r in deals),decimal(0))
    if filled>decimal(row.volume_initial):raise SafetyError('UNEXPECTED_FILLED_VOLUME')
    state='PARTIALLY_FILLED' if working and filled else 'PLACED' if working else TERMINAL_STATES.get(row.state,'UNKNOWN')
    if state=='FILLED' and filled!=decimal(row.volume_initial):state='UNKNOWN'
    if working and decimal(row.volume_current)+filled!=decimal(row.volume_initial):state='UNKNOWN'
    broker.guard()
    return dict(order=native_order(row),state=state,filled_volume=str(filled),
                position_ids=sorted({str(r.position_id) for r in deals}),
                deal_ids=sorted({str(r.ticket) for r in deals}),observed_at=broker.clock())


def prepare_native(broker, logical):
    broker.guard()
    if not broker.writable:raise SafetyError('OBSERVER_CANNOT_TRADE')
    m=broker.mt5;kind=logical['kind'];symbol=logical['symbol']
    if kind not in KINDS:raise SafetyError('UNSUPPORTED_ACTION')
    order_type=logical.get('order_type')
    if type(order_type) is not int or order_type not in range(2,8):raise SafetyError('PENDING_TYPE_INVALID')
    existing=None
    if kind!='PENDING_CREATE':
        existing=read_native_orders(broker).get(str(logical.get('order_id')))
        if not existing or any(existing[k]!=v for k,v in {'symbol':symbol,'magic':logical['magic'],'type':order_type}.items()):
            raise SafetyError('ORDER_IDENTITY_MISMATCH')
    request=dict(symbol=symbol,magic=logical['magic'],comment=broker.order_comment)
    if existing:request['order']=int(existing['ticket'])
    if kind=='PENDING_CANCEL':
        request['action']=m.TRADE_ACTION_REMOVE
        return request
    info=m.symbol_info(symbol)
    if info is None:raise SafetyError('SYMBOL_UNAVAILABLE')
    volume=decimal(logical['volume']);step=decimal(info.volume_step)
    if step<=0 or volume%step or not decimal(info.volume_min)<=volume<=decimal(info.volume_max):
        raise SafetyError('INVALID_ORDER_VOLUME')
    if existing and volume!=decimal(existing['volume_current']):
        # MT5 MODIFY cannot change pending volume. Never replace implicitly.
        raise SafetyError('PENDING_VOLUME_CHANGE_UNSUPPORTED')
    if kind=='PENDING_CREATE' and decimal(logical.get('requested_volume',volume))!=volume:
        raise SafetyError('VOLUME_TRANSFORMATION_FORBIDDEN')
    tick=decimal(info.trade_tick_size)
    values={k:decimal(logical.get(k,0)) for k in ('price','stoplimit','sl','tp')}
    if tick<=0 or values['price']<=0 or any(v<0 or v%tick for v in values.values()):
        raise SafetyError('PENDING_PRICE_INVALID')
    if (order_type in (6,7)) != (values['stoplimit']>0):raise SafetyError('STOPLIMIT_REQUIRED_OR_UNEXPECTED')
    if broker.require_stop_loss and not values['sl']:raise SafetyError('STOP_LOSS_REQUIRED')
    validity=logical.get('type_time',0);expiration=logical.get('expiration',0)
    if type(validity) is not int or validity not in range(4) or not info.expiration_mode & (1<<validity):
        raise SafetyError('PENDING_VALIDITY_UNSUPPORTED')
    if type(expiration) is not int or (validity in (2,3) and expiration<=broker.clock()) or (validity in (0,1) and expiration!=0):
        raise SafetyError('PENDING_EXPIRATION_INVALID')
    required_flag=2 if order_type in (2,3) else 4 if order_type in (4,5) else 8
    if not info.order_mode & required_flag:raise SafetyError('PENDING_TYPE_UNSUPPORTED')
    if broker.clock()>=logical['expires_at']:raise SafetyError('ENTRY_EXPIRED')
    request.update(action=m.TRADE_ACTION_PENDING if kind=='PENDING_CREATE' else m.TRADE_ACTION_MODIFY,
                   type=order_type,type_time=validity,expiration=expiration,
                   **{k:float(v) for k,v in values.items()})
    if kind=='PENDING_CREATE':request.update(volume=float(volume),type_filling=m.ORDER_FILLING_RETURN)
    # Broker order_check remains mandatory: distance/freeze/session checks are
    # enforced against the actual request immediately before the send boundary.
    return request
