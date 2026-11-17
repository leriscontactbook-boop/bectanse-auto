"""Real-account state machine; reuse durable intents and broker reconciliation.

Requires a distinct ledger. Does not migrate demo links or authorize operations.
The runtime must supply a real-capable broker and the authoritative per-operation
boundary before selecting this engine.
"""
from .demo_engine import DemoEngine
from .real_mt5 import RealMT5
from .demo_policy import SafetyError,decimal


class RealEngine(DemoEngine):
    adapter_type = RealMT5
    mode = 'REAL'
    magic_namespace = 'BCT-REAL'
    # OFF blocks entry. Management needs separate current server authority.
    manage_when_paused = False

    def __init__(self, ledger, broker, **kwargs):
        if not isinstance(broker, self.adapter_type):
            raise ValueError('REAL_ADAPTER_REQUIRED')
        if not ledger.get('execution_environment'):
            if ledger.db.execute('SELECT COUNT(*) FROM intents').fetchone()[0] or ledger.links(kwargs['owner']):
                raise ValueError('EXISTING_LEDGER_REQUIRES_REVIEW')
        ledger.pin('execution_environment', self.mode)
        kwargs['limits']={**kwargs['limits'],'require_stop_loss':False}
        broker.require_stop_loss=False
        super().__init__(ledger, broker, **kwargs)
        self.recovery_cutoff=self.clock()
        from .native_lifecycle import NativeLifecycle
        self.native=NativeLifecycle(self)
        broker.native_order_ids=lambda:{link['order_id'] for link in self.ledger.links(self.owner).values() if link.get('order_id')}

    def suspend(self, reason):
        if not self.recovery_required:
            self.recovery_cutoff=self.clock()
        super().suspend(reason)

    def _recover(self, positions, errors):
        # Reconcile every owned position, but baseline only entries predating
        # this interruption. Fresh later entries still pass the normal entry
        # age, source-continuity, configuration and reservation checks.
        before={k:v for k,v in positions.items() if v.get('opened_at',0)<=self.recovery_cutoff}
        super()._recover(before,errors)

    def management_allowed(self, configuration):
        return bool(configuration and configuration.get('management_enabled') is True
                    and self.clock()<configuration['valid_until'])

    def _manage(self, identity, link, source, closed):
        from .orphan_recovery import absence_confirmed
        latch=self.ledger.get('objective_latch:'+self.owner,{}) or {}
        if latch.get('evidence',{}).get('close_copies'):
            return super()._manage(identity,link,None,True)
        snapshot=self.ledger.get('source:'+self.source,{})
        if link.get('converted_pending'):
            ticket=identity.removeprefix('order:')
            pending=snapshot.get('native_orders',{}).get(ticket)
            result=snapshot.get('native_completed',{}).get(ticket)
            if pending:
                source={**pending,'side':'BUY' if pending['type'] in (2,4,6) else 'SELL',
                        'volume':pending['volume_initial']}
                source=self._entry_source(source,link['settings'])
                return super()._manage(identity,link,source,False)
            if result and result.get('state') in {'CANCELLED','EXPIRED','REJECTED'}:
                return super()._manage(identity,link,None,True)
            raise SafetyError('SOURCE_CLOSURE_UNCONFIRMED')
        if link.get('orphan_order'):
            # Native fills are linked only through authenticated broker history.
            order=link['orphan_order'];result=snapshot.get('native_completed',{}).get(order)
            if result is None:result=snapshot.get('native_orders',{}).get(order,{}).get('execution')
            if not result:raise SafetyError('SOURCE_CLOSURE_UNCONFIRMED')
            absent=not any(p in snapshot.get('positions',{}) for p in result.get('position_ids',[]))
            closed=absence_confirmed(self,'native:'+order,snapshot,absent)
        elif not source and not closed:
            closed=absence_confirmed(self,'position:'+identity,snapshot,True)
            if closed:link['orphan_position']=identity
        else:
            absence_confirmed(self,'position:'+identity,snapshot,False)
            link.pop('orphan_position',None)
        if source:source=self._entry_source(source,link['settings'])
        return super()._manage(identity,link,source,closed)

    def _requested_volume(self, settings, source, lots):
        return lots

    def _entry_source(self, source, settings):
        from .member_sizing import effective_side, effective_prices
        return {**source,'side':effective_side(settings,source['side']),**effective_prices(settings,source)}

    def _entry_volume(self, source, settings, target, spec):
        from .member_sizing import volume,RISK
        evidence = (self.broker.sizing_evidence(settings,source,target) if settings['mode_risque'] in RISK
                    else {'source_volume':str(source.get('volume',source.get('volume_initial')))})
        return volume(settings,source['symbol'],spec,evidence), {'inputs':evidence,'spec':spec}

    def validate_settings(self, settings):
        from .demo_policy import executable_settings
        return executable_settings(settings, require_stop_loss=self.limits.get('require_stop_loss', True),
                                   supported_options={'objectif_actif','copier_ordres_en_attente','forcer_lot_minimum','inverser_trades','drawdown_actif','drawdown_gain_actif','filtre_news','convertir_pending_invalide'}, all_lot_modes=True)

    def cycle(self, configuration=None):
        from .financial_objective import evaluate
        from .demo_policy import SafetyError,decimal
        try:self.broker.guard()
        except SafetyError as exc:
            self.suspend(str(exc));raise
        config=dict(configuration) if configuration else None
        error=None
        if config and (any(config['settings'].get(k) for k in ('objectif_actif','drawdown_actif','drawdown_gain_actif')) or self.ledger.get('objective_latch:'+self.owner)):
            key='objective_latch:'+self.owner
            try:
                result=evaluate(config['settings'],getattr(self,'objective_observation',{}),self.clock())
                latch=self.ledger.get(key)
                if result['blocked']:
                    if not latch:
                        latch={'revision':config.get('revision',0),'evidence':result}
                        self.ledger.put(key,latch)
                    error='OBJECTIVE_'+result['reason']
                elif latch:
                    # A new financial period resets the objective only while
                    # the server still authorizes ON. Explicit OFF never resumes.
                    period_changed=(result.get('period_start') is not None
                                    and latch['evidence'].get('period_start') is not None
                                    and result['period_start']>latch['evidence']['period_start'])
                    if config.get('enabled') is True and (period_changed or config.get('revision',0)>latch['revision']):
                        self.ledger.put(key,None)
                    else:error='OBJECTIVE_MANUAL_RESUME_REQUIRED'
            except (SafetyError,KeyError,TypeError,ValueError):
                error='OBJECTIVE_FINANCIAL_EVIDENCE_REQUIRED'
            if error:config['enabled']=False
        if config and config['settings'].get('filtre_news'):
            news=config.get('news_filter') or {}
            if not self.clock()<news.get('expires_at',0) or news.get('blocked',True):
                error=news.get('reason') or 'NEWS_CALENDAR_UNAVAILABLE'
                config['enabled']=False
        snapshot=self.ledger.get('source:'+self.source)
        native_errors=[]
        if snapshot and 0<=self.clock()-snapshot['observed_at']<=self.limits['snapshot_max_age']:
            native_config=config
            try:
                if config:self.validate_settings(config['settings'])
            except (SafetyError,ValueError,KeyError):
                native_config={**config,'enabled':False}
            if native_config and (self.recovery_required or snapshot.get('unresolved')):
                native_config={**native_config,'enabled':False}
            native_errors=self.native.cycle(snapshot,native_config)
            if native_errors and config:config={**config,'enabled':False}
        report=super().cycle(config)
        report['errors']=sorted(set(report['errors']+native_errors))
        if error:report['errors']=sorted(set(report['errors']+[error]))
        return report

    def _settle(self, identity, link):
        command=self.ledger.command(link['pending'])
        if command['kind'].startswith('PENDING_'):return self.native.settle(identity,link)
        return super()._settle(identity,link)

    def _execute_command(self, command):
        # Intent is committed before this hook. Retries/restarts retain the
        # same identity; broker requests are constructed separately by MT5.
        return self.broker.execute({**command['request'],
                                   'operation_id': command['id'],
                                   'owner': self.owner, 'source': self.source})

    def _send(self, *args, **kwargs):
        identity,link,kind,values,expected=args
        if kind in {'CLOSE','MODIFY','PENDING_CANCEL','PENDING_MODIFY'}:
            from .orphan_recovery import origin_for
            values={**values,'copy_origin':origin_for(self,link)}
            if link.get('orphan_order'):values['orphan_order']=link['orphan_order']
            elif link.get('orphan_position'):values['orphan_position']=link['orphan_position']
        from .member_sizing import FIXED,raw_volume
        if (kind in {'OPEN','PENDING_CREATE'} and link.get('sizing')
            and (link['settings']['mode_risque'] not in FIXED or link['settings'].get('inverser_trades') or link.get('converted_pending')
                 or raw_volume(link['settings'],link['source_symbol'],link['sizing']['inputs']) != decimal(values['volume']))):
            from .sizing_receipt import sign
            payload={**values,'kind':kind,'symbol':link['symbol'],'source_symbol':link['source_symbol'],
                     'side':link['side'],'magic':link['magic']}
            values={**values,'sizing_receipt':sign(payload,link['settings'],link['sizing'],self.owner,
                self.source,self.clock(),getattr(self.broker,'sizing_key',None))}
        super()._send(identity,link,kind,values,expected,**kwargs)
        # A second master event in the same cycle must not be blocked by a
        # reservation whose broker result was already reconciled locally.
        callback=getattr(self,'on_execution_finished',None)
        if callback:callback()
