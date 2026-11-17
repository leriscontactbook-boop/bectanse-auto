"""Native pending-order lifecycle sharing the existing durable intent ledger.

Market conversion requires the member option and a definitive read-only price
rejection before submission. No retry or conversion after an unknown result. OFF cancels remaining copied pending orders while preserving reconciliation.
Maintenance requires separate server authorization and an owned original intent.
"""
import hashlib
from .demo_policy import SafetyError,decimal,opening_volume,compatible


class NativeLifecycle:
    def __init__(self,engine):self.engine=engine
    @property
    def ledger(self):return self.engine.ledger
    def settle(self,identity,link):
        e=self.engine;command=self.ledger.command(link['pending'])
        result=e.broker.reconcile_native(magic=link['magic'],symbol=link['symbol'],
            order_type=link['order_type'],since=link['created_at'],ticket=link.get('order_id'))
        if result is None or result['state']=='UNKNOWN':return False
        row=result['order'];kind=command['kind']
        if kind=='PENDING_CREATE':
            if decimal(row['volume_initial'])!=decimal(command['expected']['volume']):
                raise SafetyError('PENDING_VOLUME_MISMATCH')
            if any(decimal(row[k])!=decimal(command['request'][k]) for k in ('price','sl','tp','stoplimit','type_time','expiration')):
                raise SafetyError('PENDING_REQUEST_MISMATCH')
        if kind=='PENDING_MODIFY':
            # Historical intents already retain validity in request, even when
            # their expected fields predate explicit expiry reconciliation.
            expected={**command['request'],**command['expected']}
            if any(decimal(row[k])!=decimal(expected[k]) for k in ('price','sl','tp','stoplimit','type_time','expiration')):
                return False
        if kind=='PENDING_CANCEL' and result['state'] not in {'CANCELLED','EXPIRED','FILLED','REJECTED'}:return False
        link.update(order_id=row['ticket'],native_result=result,state='NATIVE_'+result['state'])
        outcome='REJECTED' if result['state']=='REJECTED' or (kind=='PENDING_CANCEL' and result['state']=='FILLED') else 'CONFIRMED'
        link.pop('error',None)
        self.ledger.finish(e.owner,identity,link,command,outcome)
        return True

    def cycle(self,snapshot,configuration):
        e=self.engine;errors=[];active=configuration is not None and configuration.get('enabled') is True and e.clock()<configuration['valid_until']
        managing=active or e.management_allowed(configuration)
        settings=configuration['settings'] if configuration else {}
        native=snapshot.get('native_orders');completed=snapshot.get('native_completed',{})
        if native is None:
            return ['NATIVE_SOURCE_EVIDENCE_REQUIRED'] if settings.get('copier_ordres_en_attente') else []
        baseline='native_baseline:'+e.owner
        if not self.ledger.get(baseline):
            for ticket in native:e._save('order:'+ticket,dict(state='SKIPPED',error='INITIAL_NATIVE_BASELINE'))
            self.ledger.put(baseline,True)
        # A baseline/paused native order must not reappear as a new market
        # entry when the source order later fills. Preserve the original skip.
        for ticket,result in completed.items():
            prior=self.ledger.links(e.owner).get('order:'+ticket,{})
            if prior.get('state')=='SKIPPED' and prior.get('error')!='NATIVE_COPY_DISABLED':
                for position in result.get('position_ids',[]):
                    if position not in self.ledger.links(e.owner):
                        e._save(position,dict(state='SKIPPED',error='NATIVE_SOURCE_SKIPPED'))
        for identity,link in list(self.ledger.links(e.owner).items()):
            if not identity.startswith('order:') or 'order_type' not in link:continue
            if link.get('converted_pending'):
                self._transfer_converted(identity,link,snapshot)
                continue
            ticket=identity[6:];source=native.get(ticket);source_result=completed.get(ticket) or (source or {}).get('execution')
            try:
                if link.get('pending'):
                    if not self.settle(identity,link):errors.append('NATIVE_EXECUTION_UNCERTAIN');continue
                    link=self.ledger.links(e.owner)[identity]
                if not link.get('order_id'):continue
                observed=e.broker.reconcile_native(magic=link['magic'],symbol=link['symbol'],
                    order_type=link['order_type'],since=link['created_at'],ticket=link['order_id'])
                if not observed or observed['state']=='UNKNOWN':raise SafetyError('NATIVE_EXECUTION_UNCERTAIN')
                link.update(native_result=observed,state='NATIVE_'+observed['state']);e._save(identity,link)
                self._link_positions(link,source_result,observed,snapshot,ticket)
                from .orphan_recovery import native_orphans
                if managing:native_orphans(e,identity,link,source_result,observed,snapshot)
                if not managing or e.clock()<link.get('retry_after',0):continue
                if observed['state'] not in {'PLACED','PARTIALLY_FILLED'}:continue
                if (not active or any(v.get('orphan_order')==ticket for v in self.ledger.links(e.owner).values())
                    or (source_result and source_result['state'] in {'CANCELLED','EXPIRED','REJECTED'})):
                    e._send(identity,link,'PENDING_CANCEL',self._request(link,source or (source_result or {}).get('order') or observed['order'],configuration),{})
                elif source:
                    if decimal(source['volume_initial'])!=decimal(link['source_volume']):raise SafetyError('NATIVE_SOURCE_VOLUME_CHANGED')
                    request=self._request(link,source,configuration)
                    expected={k:request[k] for k in ('price','sl','tp','stoplimit','type_time','expiration')}
                    if any(decimal(observed['order'][k])!=decimal(v) for k,v in expected.items()):
                        request=self._request(link,source,configuration)
                        request['volume']=observed['order']['volume_current']
                        e._send(identity,link,'PENDING_MODIFY',request,expected)
            except SafetyError as error:
                link['error']=str(error);e._save(identity,link);errors.append(str(error))
        for ticket,source in native.items():
            identity='order:'+ticket
            if identity in self.ledger.links(e.owner):continue
            if not active or not settings.get('copier_ordres_en_attente'):
                e._save(identity,dict(state='SKIPPED',error='NATIVE_ENTRIES_PAUSED' if not active else 'NATIVE_COPY_DISABLED'));continue
            try:self._create(identity,source,settings,configuration)
            except SafetyError as error:
                # Reconciliation can fail AFTER native submission. Preserve the
                # durable intent and pending link; never replace it with SKIPPED.
                saved=self.ledger.links(e.owner).get(identity)
                if saved is None:saved=dict(state='SKIPPED')
                saved['error']=str(error)
                e._save(identity,saved);errors.append(str(error))
        return errors

    def _request(self,link,source,configuration):
        e=self.engine
        from .member_sizing import effective_prices
        prices=effective_prices(link['settings'],source)
        return dict(order_id=link.get('order_id'),order_type=link['order_type'],
            volume=link['volume'],requested_volume=link['volume'],price=source['price'],
            sl=prices['sl'],tp=prices['tp'],
            stoplimit=source['stoplimit'],type_time=source['type_time'],expiration=source['expiration'],
            expires_at=configuration['valid_until'])

    def _create(self,identity,source,settings,configuration):
        e=self.engine;now=e.clock()
        if any(l.get('pending') for l in self.ledger.links(e.owner).values()):raise SafetyError('UNRESOLVED_EXECUTION')
        if not -1<=now-source['setup_msc']/1000<=e.limits['entry_max_age']:raise SafetyError('ENTRY_EXPIRED')
        target=e.symbols.get(source['symbol'])
        if not target:raise SafetyError('SYMBOL_NOT_MAPPED')
        spec=e.broker.spec(target)
        lots,sizing=e._entry_volume(source,settings,target,spec)
        target=e.broker.resolve_symbol(e.source,e.owner,source['symbol'],lots)
        if e.symbols.get(source['symbol'])!=target:raise SafetyError('MAPPING_CONFIG_MISMATCH')
        spec=e.broker.spec(target);compatible(source['spec'],spec)
        from .member_sizing import effective_side,effective_order_type
        order_type=effective_order_type(settings,source['type'])
        positions=e.broker.positions();orders=e.broker.orders()
        known={link.get('position_id') for link in self.ledger.links(e.owner).values()}
        from .external_positions import isolated
        external=isolated(e.broker,positions,self.ledger.links(e.owner))
        if set(positions)-known-external or e.broker.unmanaged_orders(orders):
            raise SafetyError('DEDICATED_ACCOUNT_REQUIRED')
        exposure=sum((decimal(p['volume']) for p in positions.values()),decimal(0))+sum((decimal(o.volume_current) for o in orders),decimal(0))
        if e.limits.get('enforce_exposure_limits',True) and (len(positions)+len(orders)>=e.limits['max_positions'] or exposure+lots>decimal(e.limits['max_total_lots'])):
            raise SafetyError('EXPOSURE_LIMIT')
        magic=int(hashlib.sha256(f'{e.magic_namespace}:{e.source}:{e.owner}:{identity}'.encode()).hexdigest()[:15],16)
        if e.broker.reconcile_native(magic=magic,symbol=target,order_type=order_type,since=now):raise SafetyError('PREEXISTING_EXECUTION')
        link=dict(state='NATIVE_OPENING',symbol=target,source_symbol=source['symbol'],
            side=effective_side(settings,'BUY' if source['type'] in (2,4,6) else 'SELL'),magic=magic,order_type=order_type,sizing=sizing,
            settings=settings,source_volume=source['volume_initial'],volume=str(lots),created_at=now,config_version=configuration['version'])
        request=self._request(link,source,configuration)
        if settings.get('convertir_pending_invalide'):
            logical={**request,'kind':'PENDING_CREATE','symbol':target,'side':link['side'],'magic':magic,
                     'source_symbol':source['symbol'],'slippage':settings['slippage']}
            if e.broker.pending_conversion_required(logical):
                market={**source,'side':'BUY' if source['type'] in (2,4,6) else 'SELL',
                        'volume':source['volume_initial']}
                market.pop('type',None)
                lots,sizing=e._entry_volume(market,settings,target,spec)
                # Fresh quote will be rechecked by the market preparation path.
                tick=e.broker.mt5.symbol_info_tick(target)
                if tick is None:raise SafetyError('STALE_QUOTE')
                reference=str(tick.ask if link['side']=='BUY' else tick.bid)
                link.update(state='OPENING',converted_pending=True,sizing=sizing,volume=str(lots))
                from .member_sizing import effective_prices
                e._send(identity,link,'OPEN',dict(volume=str(lots),requested_volume=str(lots),
                    reference_price=reference,converted_pending=True,source_order_type=source['type'],
                    expires_at=min(configuration['valid_until'],now+e.limits['entry_max_age']),
                    **effective_prices(settings,source)),{'volume':str(lots)})
                return
        e._send(identity,link,'PENDING_CREATE',request,{'volume':str(lots)})

    def _transfer_converted(self,identity,link,snapshot):
        # Reserve the later source fill so it cannot create a duplicate market copy.
        result=snapshot.get('native_completed',{}).get(identity[6:]) or snapshot.get('native_orders',{}).get(identity[6:],{}).get('execution')
        if not result:return
        sources=result.get('position_ids',[])
        if len(sources)>1:raise SafetyError('NATIVE_POSITION_MAPPING_AMBIGUOUS')
        if not sources or link.get('pending') or link.get('state')!='OPEN':return
        e=self.engine;source_id=sources[0]
        existing=self.ledger.links(e.owner).get(source_id)
        if existing and existing.get('magic')!=link['magic']:raise SafetyError('NATIVE_POSITION_MAPPING_CONFLICT')
        if result.get('state') not in {'FILLED','CANCELLED','EXPIRED','REJECTED'}:
            e._save(source_id,dict(state='NATIVE_POSITION_WAITING',magic=link['magic']))
            return
        transferred={**link,'source_volume':result['filled_volume']}
        transferred.pop('converted_pending',None)
        e._save(source_id,transferred)
        e._save(identity,{**link,'state':'TRANSFERRED'})

    def _link_positions(self,link,source_result,target_result,snapshot,source_ticket=None):
        if not source_result:return
        sources=source_result['position_ids'];targets=target_result['position_ids']
        if len(sources)>1 or len(targets)>1:raise SafetyError('NATIVE_POSITION_MAPPING_AMBIGUOUS')
        if not sources:return
        e=self.engine;identity=sources[0];existing=self.ledger.links(e.owner).get(identity)
        orphans=[(k,v) for k,v in self.ledger.links(e.owner).items()
                 if source_ticket is not None and v.get('orphan_order')==source_ticket]
        if orphans:
            # If a source fill arrives before compensation was submitted, adopt
            # the owned position rather than close a now-matched exposure.
            k,owned=orphans[0]
            closing=e.ledger.db.execute("SELECT 1 FROM intents WHERE owner=? AND source_id=? AND json_extract(value,'$.kind')='CLOSE'",(e.owner,k)).fetchone()
            if (len(orphans)==1 and owned['state']=='OPEN' and not owned.get('pending') and not closing
                and identity in snapshot['positions'] and len(targets)==1 and targets[0]==owned['position_id']):
                transferred={**owned,'source_volume':source_result['filled_volume']}
                transferred.pop('orphan_order',None)
                e._save(identity,transferred);e._save(k,{**owned,'state':'TRANSFERRED'})
            elif existing is None or existing.get('state')=='NATIVE_POSITION_WAITING':
                e._save(identity,dict(state='SKIPPED',error='NATIVE_ORPHAN_COMPENSATED',magic=link['magic']))
            return
        if existing and existing.get('magic')!=link['magic']:raise SafetyError('NATIVE_POSITION_MAPPING_CONFLICT')
        if existing and existing.get('state') in {'OPEN','CLOSED','CLOSED_LOCAL'}:
            if targets and existing.get('position_id')!=targets[0]:raise SafetyError('NATIVE_POSITION_MAPPING_CONFLICT')
            return
        # Reserve the master position immediately. It must not cause a second
        # market entry while the follower's native order is still working.
        state={**link,'state':'NATIVE_POSITION_WAITING'}
        state.pop('pending',None)
        if targets:state['position_id']=targets[0]
        terminal={'FILLED','CANCELLED','EXPIRED','REJECTED'}
        if targets and source_result['state'] in terminal and target_result['state'] in terminal:
            if decimal(source_result['filled_volume'])<=0 or decimal(target_result['filled_volume'])<=0:
                raise SafetyError('NATIVE_FILL_VOLUME_INVALID')
            state.update(state='OPEN',position_id=targets[0],initial_volume=target_result['filled_volume'],
                         source_volume=source_result['filled_volume'])
        e._save(identity,state)
