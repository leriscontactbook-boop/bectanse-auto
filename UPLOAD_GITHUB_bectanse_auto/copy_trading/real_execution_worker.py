"""Continuous real execution cycle using existing MT5 adapter and durable ledger.

The host must retain ProcessLocks and the prepared RealSession for the entire
loop. This module never provisions a terminal or enables its trading switch.
Server authorization is fetched again at each submission, not cached locally.
"""
from contextlib import contextmanager
import time
from .service import digest
from .demo_mt5 import NotSent
from .real_execution_boundary import RealExecutionBoundary


class ServerAuthority:
    def __init__(self,backend,binding,clock=time.time):
        self.backend,self.binding,self.clock=backend,dict(binding),clock
        self.revision=None
        self.last_poll=None
    def request(self,action,**payload):
        started=time.monotonic()
        try:result=self.backend.request('/internal/copy/real/execution/'+action,{**self.binding,**payload})
        finally:
            timings=getattr(self,'timings',None)
            if timings is not None:timings[action]=timings.get(action,0)+(time.monotonic()-started)*1000
        if result.get('ok') is not True:raise RuntimeError('EXECUTION_SERVER_UNAVAILABLE')
        return result['result']
    def poll(self):
        completed=getattr(self,'completed_cycle',None)
        self.completed_cycle=None
        state=self.request('poll',**({'completed_cycle':completed} if completed else {}))
        self.revision=state.get('revision')
        self.last_poll=state
        return state
    @contextmanager
    def submission(self,logical):
        if self.revision is None:raise NotSent('COMMAND_REVISION_REQUIRED')
        # A lost reservation response is uncertain. Never translate a transport
        # exception into a retryable send; the server may have committed it.
        try:ticket=self.request('reserve',revision=self.revision,operation=logical)
        except Exception as error:
            # Only a definitive, authenticated server refusal BEFORE reservation
            # can be classified NotSent. Duplicates/timeouts remain uncertain.
            response=getattr(error,'response',None)
            if response is not None and response.status_code==409:
                try:reason=response.json().get('error')
                except Exception:reason=None
                if reason in {'AUTHORIZATION_REVOKED_OR_CHANGED','MEMBERSHIP_INACTIVE','ADMIN_PERMISSION_REQUIRED',
                              'CONFIGURATION_CHANGED','MASTER_CHANGED','OPERATIONAL_RELEASE_REQUIRED',
                              'CONNECTION_EVIDENCE_STALE_OR_CHANGED','CONNECTION_EVIDENCE_REQUIRED',
                              'TERMINAL_EXECUTION_NOT_READY','QUALIFICATION_CHANGED',
                              'EXECUTOR_SESSION_EXPIRED_OR_REPLACED','REQUESTED_VOLUME_CHANGED',
                              'OPERATION_IDENTITY_CHANGED','COPY_OWNERSHIP_REQUIRED','NEWS_CALENDAR_UNAVAILABLE',
                              'NEWS_HIGH_IMPACT_WINDOW','SIZING_EVIDENCE_REQUIRED','SIZING_EVIDENCE_INVALID_OR_EXPIRED'}:
                    raise NotSent(reason) from None
            raise
        if (ticket.get('operation_id')!=logical.get('operation_id')
            or ticket.get('request_digest')!=digest(logical)
            or ticket.get('revision')!=self.revision
            or not self.clock()<ticket.get('expires_at',0)<=self.clock()+3):
            raise RuntimeError('RESERVATION_UNCONFIRMED')
        yield


class ExecutionCycle:
    def __init__(self,engine,authority,source_reader,*,clock=time.time,observer=None):
        self.engine,self.authority,self.read_source,self.clock=engine,authority,source_reader,clock
        self.observer=observer
        self.next_observation=0
        self.next_ack=0
        self.ack_identity=None
        boundary=RealExecutionBoundary(engine.ledger,authority,owner=engine.owner,source=engine.source,clock=clock,
            source_reader=source_reader,source_max_age=engine.limits['snapshot_max_age'])
        engine.broker.boundary=boundary
        engine.broker._submission.boundary=boundary
        engine.on_execution_finished=self.flush_results

    def recover_unsent_reservations(self):
        # No native marker => no send is possible in this code path. Cancel
        # remotely FIRST so late reservation requests are fenced by a tombstone.
        import json
        rows=self.engine.ledger.db.execute('SELECT source_id,value FROM intents WHERE owner=?',(self.engine.owner,)).fetchall()
        for identity,raw in rows:
            command=json.loads(raw)
            if 'reservation_attempted_at' not in command or 'submission_started_at' in command:continue
            if self.engine.ledger.get('abandoned:'+command['id']):continue
            logical={**command['request'],'operation_id':command['id'],'owner':self.engine.owner,'source':self.engine.source}
            result=self.authority.request('abandon',operation=logical,revision=self.authority.revision,native_submission_started=False)
            if result.get('state')!='NOT_SENT':raise RuntimeError('RESERVATION_RECOVERY_UNCONFIRMED')
            link=self.engine.ledger.links(self.engine.owner).get(identity)
            if not link:raise RuntimeError('DURABLE_LINK_MISSING')
            if command['kind'] in {'OPEN','PENDING_CREATE'}:link['state']='SKIPPED'
            link['error']='RESERVATION_ABANDONED_NO_NATIVE_SEND'
            self.engine.ledger.finish(self.engine.owner,identity,link,command,'NOT_SENT')
            self.engine.ledger.put('abandoned:'+command['id'],True)

    def results(self):
        rows=self.engine.ledger.db.execute('SELECT value FROM intents WHERE owner=?',(self.engine.owner,)).fetchall()
        import json
        results=[]
        for row in rows:
            command=json.loads(row[0])
            # A preflight NotSent before central reservation has no server row.
            if 'submission_started_at' not in command:continue
            if command['state'] in {'CONFIRMED','REJECTED','NOT_SENT','UNKNOWN'}:
                item=dict(id=command['id'],state=command['state'],evidence=dict(
                    intent=command,reconciled=command['state']=='CONFIRMED'))
                if command.get('native_reconciliation'):
                    item['evidence']['native_reconciliation']=command['native_reconciliation']
                if self.engine.ledger.get('reported:'+command['id'])!=digest(item):results.append(item)
        return results[:100]

    def flush_results(self):
        while True:
            operations=self.results()
            if not operations:return
            self.authority.request('report',operations=operations)
            for item in operations:self.engine.ledger.put('reported:'+item['id'],digest(item))

    def cycle(self):
        self.authority.timings={}
        timings={}
        observation_due=self.observer and self.clock()>=self.next_observation
        def refresh_observation():
            # Previous settings choose only the history window. The report
            # evaluates CURRENT settings and returns a fresh authorization poll.
            # An insufficient history window fails financial qualification.
            envelope=self.observer()
            self.authority.completed_cycle=None
            current=self.authority.request('report',observation=envelope,refresh_authorization=True)
            if 'authorized' not in current:current=self.authority.poll()
            self.authority.revision=current.get('revision')
            self.authority.last_poll=current
            self.engine.objective_observation=envelope['body']
            self.next_observation=self.clock()+5
            return current
        if observation_due and self.authority.last_poll is not None:
            state=refresh_observation()
            observation_due=False
        else:state=self.authority.poll()
        self.recover_unsent_reservations()
        self.flush_results()
        if observation_due:state=refresh_observation()
        snapshot=self.read_source(self.engine.source)
        if snapshot is not None:
            if snapshot.get('source')!=self.engine.source:raise RuntimeError('MASTER_IDENTITY_CHANGED')
            self.engine.ledger.publish(self.engine.source,snapshot,[])
        collector=getattr(self,'evidence_collector',None)
        if collector is not None:
            started=time.monotonic()
            self.evidence_diagnostics=collector(snapshot,self.engine)
            timings['collection']=round((time.monotonic()-started)*1000,2)
        publisher=getattr(collector,'publish',None)
        if publisher is not None:
            from .preflight_delivery import RuntimeReviewReload
            try:publisher(state)
            except RuntimeReviewReload:raise
            except Exception as error:
                # Evidence publication has no authority to change engine state.
                # Keep a visible *sanitised* diagnostic; execution still uses
                # its own guards.  In particular, never put a response body,
                # URL, account identifier or exception representation in this
                # health channel: it is readable by operations staff.
                code=str(error)
                response=getattr(error,'response',None)
                if response is not None:
                    try:code=response.json().get('error') or ('HTTP_'+str(response.status_code))
                    except Exception:code='HTTP_'+str(getattr(response,'status_code','UNKNOWN'))
                permitted={'SIGNED_PREFLIGHT_REQUIRED','PREFLIGHT_OWNER_CHANGED',
                           'PREFLIGHT_CONFIGURATION_CHANGED','PREFLIGHT_UPLOAD_STALE',
                           'PREFLIGHT_ID_REUSED','PREFLIGHT_DELIVERY_UNCONFIRMED',
                           'PREFLIGHT_APPROVAL_UNCONFIRMED','PREFLIGHT_INSTRUMENTS_REQUIRED',
                           'REVIEWED_INSTRUMENT_MAPPING_REQUIRED','PER_INSTRUMENT_REVIEW_REQUIRED',
                           'PREFLIGHT_MANIFEST_CHANGED','PREFLIGHT_INSTALL_BINDING_CHANGED'}
                # Domain failures are intentionally symbolic upper-case codes.
                # Exposing only that constrained alphabet keeps the health
                # channel useful while excluding free-form exception strings.
                import re
                safe_code=isinstance(code,str) and (
                    code in {'HTTP_401','HTTP_409','HTTP_413','HTTP_503'}
                    or re.fullmatch(r'[A-Z][A-Z0-9_]{1,96}',code) is not None)
                if code not in permitted and not safe_code:
                    # Exception class names contain no request/response payload
                    # and distinguish a network fault from a local evidence
                    # validation error without disclosing either.
                    code='PREFLIGHT_TRANSPORT_'+type(error).__name__.upper()[:48]
                self.evidence_diagnostics['review_transport']=code
        # Collection/publication may have taken long enough for a source entry
        # or close to arrive. Decide from the latest snapshot in this same cycle.
        # Qualification remains fresh and every submission rechecks the source
        # before and after its central reservation, including missed-close races.
        if snapshot is not None:
            latest=self.read_source(self.engine.source)
            if latest is None:raise RuntimeError('MASTER_SNAPSHOT_MISSING')
            if latest.get('source')!=self.engine.source:raise RuntimeError('MASTER_IDENTITY_CHANGED')
            snapshot=latest
            self.engine.ledger.publish(self.engine.source,snapshot,[])
        p=state['configuration']
        if state.get('confirmed')=='AUTHORIZATION_ACCEPTED':
            self.authority.request('report',phase='STARTING',revision=state['revision'])
        cfg=dict(settings=p['settings'],news_filter=state.get('news_filter'),version=self.authority.binding['version'],revision=state.get('revision',0),
                 enabled=state['authorized'] is True,management_enabled=state.get('management_authorized') is True,
                 valid_until=state['valid_until'])
        mapping_waiting=False
        if (snapshot and self.engine.ledger.get('baseline:'+self.engine.owner)
            and not self.engine.recovery_required and state['desired']=='ACTIVE'
            and (state['authorized'] or state.get('reason')=='QUALIFICATION_CHANGED')):
            # Give a newly discovered instrument a bounded qualification window.
            # Existing/previously skipped positions are never replayed. Existing
            # mapped positions continue to be managed during this window.
            filtered,mapping_waiting=hold_unmapped(self.engine,snapshot,self.clock)
            self.engine.ledger.publish(self.engine.source,filtered,[])
        writable=self.engine.broker.writable
        try:
            if not cfg['enabled'] and not cfg['management_enabled']:self.engine.broker.writable=False
            started=time.monotonic()
            report=self.engine.cycle(cfg)
            timings['engine']=round((time.monotonic()-started)*1000,2)
        finally:self.engine.broker.writable=writable
        if mapping_waiting:report['errors']=sorted(set(report['errors']+['SYMBOL_MAPPING_PENDING']))
        self.flush_results()
        evidence_errors=getattr(self,'evidence_diagnostics',{})
        unhealthy=bool(set(report['errors'])-{'BASELINE_ESTABLISHED'}) or bool(evidence_errors)
        payload=dict(revision=state.get('revision'))
        if unhealthy:payload['phase']='UNCONFIRMED'
        # Only report active after the actual engine completed a successful cycle.
        # Never acknowledge a pause while an unknown operation remains outstanding.
        # Recovery may have settled a reservation since the initial poll.
        # Only that exceptional case needs a separate state read.
        pending_state=self.authority.request('report') if state['pending_operations'] else state
        if not report['pending_commands'] and not pending_state['pending_operations']:
            if state['desired'] in {'PAUSED','STOPPED'}:payload['ack']=state['desired']
            elif state['authorized'] and not report['errors'] and not evidence_errors:payload['ack']='ACTIVE'
        # The server atomically checks revision, ownership, eligibility and
        # unsettled operations before accepting this acknowledgement. A separate
        # status-only round trip adds latency without granting any authority.
        identity=(state.get('revision'),state.get('desired'),payload.get('ack'))
        # Every cycle still polls current authorization, and every order reserves
        # remotely. Only unchanged healthy status acknowledgements are coalesced.
        # Failures, transitions, recovery and pending commands bypass this limit.
        repeat=(not unhealthy and not report['pending_commands']
                and not state['pending_operations'] and payload.get('ack')=='ACTIVE'
                and state.get('confirmed')=='ACTIVE' and self.ack_identity==identity
                and self.clock()<self.next_ack)
        piggyback=(state.get('completed_cycle_supported') is True and not unhealthy
                   and not report['pending_commands'] and not state['pending_operations']
                   and payload.get('ack')=='ACTIVE' and state.get('confirmed')=='ACTIVE')
        if piggyback:
            self.authority.completed_cycle={'revision':state['revision'],'at':self.clock()}
            confirmed=state
        elif repeat:confirmed=state
        else:
            confirmed=self.authority.request('report',**payload)
            self.ack_identity=identity
            self.next_ack=self.clock()+2
        return dict(engine=report,control=confirmed,instruments=evidence_errors,
                    timings_ms={**timings,**{k:round(v,2) for k,v in self.authority.timings.items()}})

    def run(self,stop,report_health,interval=1):
        if not 0<interval<=2:raise ValueError('INVALID_EXECUTION_INTERVAL')
        while not stop.is_set():
            try:report_health(self.cycle())
            except Exception:
                self.engine.suspend('EXECUTION_CYCLE_UNCONFIRMED')
                try:self.authority.request('report',phase='UNCONFIRMED')
                except Exception:pass  # Server expires the session independently.
                report_health({'execution_enabled':False,'state':'UNCONFIRMED'})
            stop.wait(interval)


def hold_unmapped(engine,snapshot,clock):
    """Bound discovery by the existing entry deadline, persisted across reloads."""
    filtered=dict(snapshot);waiting=False
    for collection in ('positions','native_orders'):
        filtered[collection]=dict(snapshot.get(collection,{}))
        for identifier,position in snapshot.get(collection,{}).items():
            if position['symbol'] in engine.symbols:continue
            key='mapping_wait:'+collection+':'+str(identifier)
            first=engine.ledger.get(key)
            if first is None:
                first=clock();engine.ledger.put(key,first)
            deadline=first+min(10,engine.limits['entry_max_age'])
            if 0<=clock()-first and clock()<deadline:
                filtered[collection].pop(identifier,None);waiting=True
    return filtered,waiting


class PreparedExecutor:
    """Attach to the existing locked session, never initialize/provision twice.

    Qualification and mapping must be approved providers, never synthesized from
    a matching symbol name. Missing providers prevent construction.
    The host owns session lifetime and reconnects through RealSession on failure.
    """
    def __init__(self,session,backend,*,master,source_path,state_path,symbols,limits,
                 qualification,mapping,qualification_digest,broker_time_offset_seconds=0):
        import uuid
        from .demo_store import Ledger
        from .real_mt5 import RealMT5
        from .real_engine import RealEngine
        from .fleet_store import SourceReader
        if session.lock is None or session.broker is None:raise ValueError('LOCKED_PREPARED_SESSION_REQUIRED')
        if qualification is None or mapping is None:raise ValueError('REAL_QUALIFICATION_REQUIRED')
        self.ledger=Ledger(state_path);self.source=None
        try:
            identity=session.identity
            self.ledger.pin('real_binding',{k:identity[k] for k in ('member_code','account_id','login','server')})
            instance=self.ledger.get('executor_instance')
            if not instance:instance=uuid.uuid4().hex;self.ledger.put('executor_instance',instance)
            binding={k:identity[k] for k in ('member_code','account_id','version')}
            binding.update(instance_id=instance,qualification_digest=qualification_digest)
            auth=ServerAuthority(backend,binding)
            self.source=SourceReader(source_path)
            broker=RealMT5(session.module,login=identity['login'],server=identity['server'],terminal_path=identity['terminal'],
                           writable=True,qualification=qualification,mapping=mapping,broker_time_offset_seconds=broker_time_offset_seconds,
                           require_stop_loss=limits['require_stop_loss'],enforce_spread_limit=limits['enforce_spread_limit'],
                           enforce_master_price_limit=limits['enforce_master_price_limit'],max_spread_points=limits['max_spread_points'])
            broker.sizing_key=backend.secret.encode()
            engine=RealEngine(self.ledger,broker,source=str(master['login'])+'@'+master['server'],
                              owner=identity['login']+'@'+identity['server'],symbols=symbols,limits=limits)
            def observe():
                from .financial_objective import period_start
                # The current cycle has just polled. Reuse its settings only for
                # choosing the history window; submission still reserves remotely.
                current=auth.last_poll['configuration']['settings']
                start=period_start(current['objectif_periode'],time.time()) if current.get('objectif_actif') else max(0,time.time()-86400)
                return session.observe({},history_from=start,key=backend.secret.encode())
            self.cycle=ExecutionCycle(engine,auth,self.source.read,observer=observe)
        except BaseException:
            self.close();raise
    def close(self):
        if self.source:self.source.close()
        self.ledger.close()
