"""Compensate only journal-owned copies after fresh, repeated source absence."""
import json
from .demo_policy import SafetyError, decimal


def origin_for(engine,link):
    for raw, in engine.ledger.db.execute('SELECT value FROM intents WHERE owner=? ORDER BY created_at,id',(engine.owner,)):
        command=json.loads(raw);request=command['request']
        if (command['state']=='CONFIRMED' and command['kind'] in {'OPEN','PENDING_CREATE'}
            and all(request.get(k)==link.get(k) for k in ('magic','symbol','side','source_symbol'))):
            return command['id']
    raise SafetyError('COPY_OWNERSHIP_REQUIRED')


def absence_confirmed(engine,key,snapshot,absent):
    name='source_absence:'+engine.owner+':'+key
    now=engine.clock();at=snapshot.get('observed_at',0)
    valid=(snapshot.get('source')==engine.source and 0<=now-at<=engine.limits['snapshot_max_age']
           and not snapshot.get('unresolved') and isinstance(snapshot.get('positions'),dict))
    if not absent or not valid:
        engine.ledger.put(name,None);return False
    previous=engine.ledger.get(name)
    continuity=snapshot.get('continuity_started_at')
    if (not previous or previous.get('continuity')!=continuity or at<previous['last']
        or at-previous['last']>engine.limits['snapshot_max_age']):
        previous={'first':at,'last':at,'continuity':continuity}
    else:previous['last']=at
    engine.ledger.put(name,previous)
    return at-previous['first']>=1


def native_orphans(engine,identity,link,source_result,target_result,snapshot):
    """History supplies exact owned position IDs; magic alone is insufficient."""
    if not source_result:return
    sources=source_result.get('position_ids',[])
    absent=not any(pid in snapshot['positions'] for pid in sources)
    if not absence_confirmed(engine,'native:'+identity[6:],snapshot,absent):return
    targets=target_result.get('position_ids',[])
    if not targets:return
    if not target_result.get('deal_ids') or not target_result.get('order'):
        raise SafetyError('NATIVE_FILL_IDENTITY_UNVERIFIED')
    origin_for(engine,link)
    positions=engine.broker.positions()
    known={v.get('position_id') for v in engine.ledger.links(engine.owner).values()
           if v.get('state') in {'OPEN','CLOSED','CLOSED_LOCAL'}}
    total=sum((decimal(positions[t]['volume']) for t in targets if t in positions),decimal(0))
    if total>decimal(target_result['filled_volume']):raise SafetyError('NATIVE_FILL_VOLUME_INVALID')
    for pid in targets:
        if pid in known:continue
        actual=positions.get(pid)
        if actual is None:continue
        if any(actual.get(k)!=link[k] for k in ('symbol','side','magic')):
            raise SafetyError('POSITION_IDENTITY_MISMATCH')
        key='orphan:'+identity+':'+pid
        if key in engine.ledger.links(engine.owner):continue
        owned={**link,'state':'OPEN','orphan_order':identity[6:],'position_id':pid,
               'initial_volume':actual['volume'],'native_result':target_result}
        owned.pop('pending',None);owned.pop('error',None)
        engine._save(key,owned)
