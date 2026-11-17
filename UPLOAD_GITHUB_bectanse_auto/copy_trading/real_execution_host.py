"""Session lifecycle for the existing preparation queue and real executor.

The runtime supplies a reviewed executor builder. This host never manufactures
qualification, changes terminal permissions or enables member consent. The same
RealSession lock spans collection and execution, including every broker call.
"""
from pathlib import Path
import hashlib
import time
from .real_session import RealSession


class ExecutionHost:
    def __init__(self, backend, module, *, root, template, state_root, build_executor,
                 stop, health, session_factory=RealSession, clock=time.time, member_code=None):
        if not callable(build_executor):raise ValueError('REVIEWED_EXECUTOR_BUILDER_REQUIRED')
        self.member_code=member_code
        self.backend,self.module=backend,module
        self.root,self.template,self.state_root=root,template,Path(state_root)
        self.build_executor,self.stop,self.health=build_executor,stop,health
        self.session_factory,self.clock=session_factory,clock
        self.state_root.mkdir(parents=True,exist_ok=True)

    def emit(self,state,**data):
        # Only structured codes/booleans leave this process. Never exception text,
        # configuration payloads, passwords or broker history in health logs.
        self.health({'at':self.clock(),'state':state,**data})

    def run_assignment(self):
        self.emit('CLAIMING_ASSIGNMENT',execution_enabled=False)
        claim={'role':'executor'}
        if self.member_code:
            claim['member_code']=self.member_code
        plan=self.backend.request('/internal/copy/real/preparation/claim',claim).get('plan')
        if plan is None:
            self.emit('WAITING_FOR_ASSIGNMENT',execution_enabled=False)
            return False
        job=dict(plan['job'])
        self.emit('PREPARING_SESSION',execution_enabled=False)
        password=plan.pop('password')
        executor=None
        try:
            with self.session_factory(self.module,self.root,self.template,plan['identity'],password=password) as session:
                password=None
                self.emit('COLLECTING_PREPARATION',execution_enabled=False)
                observation=session.observe({},history_from=max(0,self.clock()-86400),
                                            key=self.backend.secret.encode(),clock=self.clock)
                self.emit('REPORTING_PREPARATION',execution_enabled=False)
                self.backend.request('/internal/copy/real/preparation/report',{'job':plan['job'],'observation':observation})
                slot=hashlib.sha256((session.identity['member_code']+'|'+str(session.identity['account_id'])).encode()).hexdigest()
                # Neither a restart nor a configuration version creates a new
                # operation journal. Account rebinding uses a different slot.
                state=self.state_root/slot;state.mkdir(exist_ok=True)
                self.emit('BUILDING_EXECUTOR',execution_enabled=False)
                executor=self.build_executor(session,self.backend,plan,state)
                try:
                    self.emit('EXECUTOR_ATTACHED',execution_enabled=False)
                    while not self.stop.is_set():
                        from .preflight_delivery import RuntimeReviewReload
                        started=time.monotonic()
                        try:result=executor.cycle.cycle()
                        except RuntimeReviewReload:
                            # Rebuild qualification inside the same exclusively held
                            # terminal session; durable operations and consent survive.
                            recovering=executor.cycle.engine.recovery_required
                            executor.close();executor=None
                            executor=self.build_executor(session,self.backend,plan,state)
                            executor.cycle.engine.recovery_required=recovering
                            continue
                        control=result['control']
                        errors=list(result.get('engine',{}).get('errors',[]))
                        if result.get('instruments'):
                            errors.append('INSTRUMENT_EVIDENCE_UNCONFIRMED')
                        known={'INSTRUMENT_EVIDENCE_UNCONFIRMED','RECOVERY_QUOTES_UNAVAILABLE','SOURCE_STALE','CONFIGURATION_UNAVAILABLE',
                               'OBJECTIVE_FINANCIAL_EVIDENCE_REQUIRED','OBJECTIVE_MANUAL_RESUME_REQUIRED',
                               'OBJECTIVE_LOSS_LIMIT_REACHED','OBJECTIVE_GAIN_TARGET_REACHED',
                               'NATIVE_SOURCE_EVIDENCE_REQUIRED','DEDICATED_ACCOUNT_REQUIRED','EXECUTION_UNCERTAIN',
                               'BASELINE_ESTABLISHED','SOURCE_RECONCILIATION_REQUIRED'}
                        self.emit(control.get('confirmed','UNCONFIRMED'),
                                  execution_enabled=control.get('execution_enabled') is True,
                                  engine_ready=not errors,timings_ms=result.get('timings_ms',{}),cycle_ms=round((time.monotonic()-started)*1000,2),
                                  reasons=[e if e in known else 'EXECUTION_CHECK_FAILED' for e in errors],
                                  # A health file must make a failed evidence
                                  # gate actionable without publishing account
                                  # data, credentials, positions or prices.
                                  evidence_diagnostics=sorted(
                                      str(key)+':'+str(value)
                                      if isinstance(value,str) and __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{1,96}',value)
                                      else str(key)+':UNCONFIRMED'
                                      for key,value in result.get('instruments', {}).items())[:20])
                        if self.stop.wait(max(.01,.1-(time.monotonic()-started))):break
                finally:
                    # Close journals before releasing the MT5/account lock,
                    # also when a cycle or health publication raises.
                    try:executor.close()
                    finally:executor=None
            self.emit('HOST_STOPPED',execution_enabled=False)
            return True
        except Exception as error:
            known={'BOOTSTRAP_SERVER_CLOCK_REQUIRED','BOOTSTRAP_MASTER_NATIVE_EVIDENCE_REQUIRED',
                   'NATIVE_READER_START_UNCONFIRMED','NATIVE_READER_SYMBOL_REQUIRED',
                   'DEDICATED_READER_TERMINAL_REQUIRED','TERMINAL_STOP_UNCONFIRMED',
                   'TERMINAL_AUTH_FAILED','TERMINAL_CONNECTION_FAILED'}
            reason=str(error) if str(error) in known else 'SESSION_PREPARATION_FAILED'
            # MT5 errors are deliberately reduced to a numeric SDK code: the
            # accompanying text can include broker/account material.  Keeping
            # this code in the local health file lets the supervisor distinguish
            # an unreachable terminal from credentials rejected by the broker,
            # and therefore retry only the safe automatic recovery path.
            diagnostic=getattr(error, 'diagnostic_code', None)
            if type(diagnostic) is not int:
                diagnostic=None
            # Release a failed preparation lease immediately.  Without this
            # acknowledgement, a terminal failure held the account in
            # "synchronisation" until the lease expired, even though the next
            # automatic attempt could safely begin right away.
            try:
                self.backend.request('/internal/copy/real/preparation/failed',
                                     {'job':job,
                                      'reason':reason if reason in known else 'CHECK_UNAVAILABLE'})
            except Exception:
                pass
            print('PREPARATION_FAILURE reason='+reason+' type='+type(error).__name__,flush=True)
            self.emit('EXECUTION_SESSION_UNCONFIRMED',execution_enabled=False,
                      reason=reason,diagnostic_code=diagnostic)
            raise
        finally:
            password=None
            if executor is not None:executor.close()

    def run(self):
        failures=0
        while not self.stop.is_set():
            try:
                worked=self.run_assignment()
                failures=0
                wait=1 if worked else 5
            except Exception:
                # Fail closed, release the old session and fetch a fresh binding
                # and credentials on reconnect. Durable reservations stay intact.
                failures+=1
                wait=min(60,2**min(failures,6))
                self.emit('RECONNECT_PENDING',execution_enabled=False,retry_in=wait)
            # Keep supervisor liveness separate from execution readiness during
            # long reconnect backoff; no replacement process may overlap this one.
            while wait>0 and not self.stop.is_set():
                step=min(5,wait)
                if self.stop.wait(step):break
                wait-=step
                self.emit('RECONNECT_PENDING' if failures else 'WAITING_FOR_ASSIGNMENT',execution_enabled=False,retry_in=wait)
