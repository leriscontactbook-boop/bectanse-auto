"""VPS supervisor. Standby is real authenticated connectivity, never simulated trading."""
from .atomic_io import write_json
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import uuid
import psutil
import requests

from .demo_runtime import DemoBackend, ProcessLocks, read_manifest
from .worker import SimulationWorker


class AgentBackend(DemoBackend):
    def __init__(self):
        super().__init__()
        self.secret=os.environ.get('COPY_AGENT_WORKER_SECRET','')
        # The agent lease is distinct from the configuration validator lease.
        # Reusing the validator identity made the source observer contend with
        # another healthy worker and receive AGENT_ALREADY_RUNNING responses.
        self.identity=os.environ.get('COPY_DEMO_WORKER_ID','')
        if len(self.secret)<32:raise ValueError('DEDICATED_AGENT_SECRET_REQUIRED')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',self.identity):
            raise ValueError('DEDICATED_AGENT_ID_REQUIRED')
        self.timeout=(3,8)


def fresh_plan(plan,now=None):
    now=time.time() if now is None else now
    if (not isinstance(plan,dict) or plan.get('ok') is not True or plan.get('mode')!='DEMO_ONLY'
            or type(plan.get('issued_at')) not in (int,float) or not -2<=now-plan['issued_at']<=15
            or not isinstance(plan.get('state'),str)):
        raise ValueError('INVALID_AGENT_RESPONSE')
    return plan


def child_health_matches(item, child):
    """Windows venv launchers delegate to a Python descendant with a different PID."""
    # Never inspect the process tree of the kernel/init as a managed worker.
    if type(child.pid) is not int or child.pid <= 1 or type(item.get('pid')) is not int or item['pid'] <= 1:
        return False
    if item.get('pid') == child.pid:
        return True
    try:
        return any(p.pid == item.get('pid') and p.create_time() <= item['at']
                   for p in psutil.Process(child.pid).children(recursive=True))
    except (psutil.Error, KeyError, TypeError):
        return False


def make_manifest(plan,root):
    if plan.get('state')!='READY':raise ValueError('PLAN_NOT_READY')
    data={'mode':'DEMO_ONLY','account_id':plan['account_id'],'symbols':plan['symbols'],
          'plan_id':plan['plan_id'],'master_revision':plan['master_revision']}
    for role,folder in [('master','Master'),('follower','Follower')]:
        account=plan[role]
        if not isinstance(account.get('password'),str) or not account['password']:
            raise ValueError('CREDENTIAL_UNAVAILABLE')
        data[role]={'login':account['login'],'server':account['server'],
            'terminal':str(root/'Terminals'/folder/'terminal64.exe')}
    # An explicit, operator-verified server clock convention, never inferred from
    # a stale tick. read_manifest validates it and the ledger pins it across restarts.
    data['limits']=json.loads(os.environ.get('COPY_DEMO_LIMITS','{}'))
    data['broker_time_offsets']=json.loads(os.environ.get('COPY_DEMO_BROKER_TIME_OFFSETS',
        '{"master":0,"follower":0}'))
    return data


class Supervisor:
    def __init__(self,root,backend=None,spawn=subprocess.Popen,clock=time.monotonic):
        self.root=Path(root);self.backend=backend or AgentBackend();self.spawn=spawn;self.clock=clock
        self.instance=uuid.uuid4().hex;self.children={};self.plan_id=None;self.next_start=0;self.failures=0
        self.started_at={}
        self.last_heartbeat={}
        self.state='STARTING';self.log=logging.getLogger('copy_agent')
        self.armed=(os.environ.get('COPY_DEMO_EXECUTION')=='I_CONFIRM_DEMO_ONLY' and os.environ.get('COPY_AGENT_SOURCE_ONLY')!='1')
        self.validator=SimulationWorker() if len(os.environ.get('COPY_CONFIG_WORKER_SECRET',''))>=32 else None
        if self.validator:self.validator.timeout=(2,3)

    def report(self):
        report={'state':self.state,'execution_armed':self.armed,
            'observer_alive':self.alive('observe'),'executor_alive':self.alive('execute')}
        if os.environ.get('COPY_AGENT_SOURCE_ONLY')=='1' and report['observer_alive']:
            try:
                value=json.loads((self.root/'State'/'master-telemetry.json').read_text())
                from .dashboard_telemetry import clean
                report['master_telemetry']=clean(value,login=value['login'],server=value['server'])
            except (OSError,ValueError,KeyError,TypeError):pass
        return report

    def alive(self,role):return role in self.children and self.children[role].poll() is None

    def start(self,plan):
        if not self.armed: self.state='WAITING_EXECUTION_ARMING';return
        if self.plan_id and self.plan_id!=plan['plan_id']:
            self.state='PLAN_CHANGED_OPERATOR_REQUIRED';return
        for role in ('observe','execute'):
            try:
                health=json.loads((self.root/'State'/f'{role}-health.json').read_text())
                if health.get('error') in {'REAL_TRADING_FORBIDDEN','ACCOUNT_IDENTITY_CHANGED',
                        'HEDGING_REQUIRED','TERMINAL_IDENTITY_CHANGED','FIFO_UNSUPPORTED','LOCAL_STATE_INVALID'}:
                    self.state='TERMINAL_SAFETY_BLOCK';return
            except (OSError,ValueError):pass
        manifest=make_manifest(plan,self.root)
        # Stable ledger and immutable identity prevent a restart from forgetting prior orders.
        path=self.root/'State'/'manifest.json';temp=path.with_suffix('.validate.json')
        write_json(temp,manifest)
        try:read_manifest(temp)
        finally:temp.unlink(missing_ok=True)
        write_json(path,manifest)
        self.plan_id=plan['plan_id']
        if self.clock()<self.next_start:return
        for role,account in [('observe','master'),('execute','follower')]:
            if self.alive(role):continue
            if role in self.children:
                try:
                    previous=json.loads((self.root/'State'/f'{role}-health.json').read_text())
                except (OSError,ValueError):previous={}
                self.log.warning('child_exited role=%s code=%s last_state=%s last_error=%s',
                    role,self.children[role].poll(),previous.get('state','UNKNOWN'),previous.get('error',''))
            env=dict(os.environ,COPY_DEMO_INSTANCE_ID=self.instance)
            # Child roles receive only the secret required by their own transport.
            for key in ('COPY_AGENT_WORKER_SECRET','COPY_CONFIG_WORKER_SECRET'):
                env.pop(key,None)
            if role=='observe':env.pop('COPY_DEMO_WORKER_SECRET',None)
            child=self.spawn([sys.executable,'-m','copy_trading.demo_runtime',role,
                '--manifest',str(path),'--state',str(self.root/'State'/'ledger.sqlite3'),'--credentials-stdin',
                '--health-file',str(self.root/'State'/f'{role}-health.json'),
                '--parent-pid',str(os.getpid()),'--parent-start',str(psutil.Process().create_time())],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                env=env,text=True)
            try:
                child.stdin.write(json.dumps({'password':plan[account]['password']}));child.stdin.close()
            except (BrokenPipeError,OSError):pass
            self.children[role]=child
            self.started_at[role]=self.clock()
            self.log.info('child_started role=%s pid=%s',role,child.pid)
        self.next_start=self.clock()+min(60,2**min(self.failures+1,6))
        self.state='STARTING_TERMINALS'

    def start_source(self, plan):
        # Read-only master collection must not depend on a demo follower's credentials.
        if self.alive('observe') or self.clock()<self.next_start:return
        # The running agent's interpreter is the one that has already proven
        # it can load the local MT5 bridge.  A second launcher can resolve a
        # different site-packages set on Windows and make the observer exit
        # before it can write its health record.
        child=self.spawn([sys.executable,'-m','copy_trading.source_observer','--root',str(self.root),
                          '--parent-pid',str(os.getpid()),'--parent-start',str(psutil.Process().create_time())],
                         stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         env=dict(os.environ),text=True)
        try:child.stdin.write(json.dumps(plan)+'\n');child.stdin.close()
        except (BrokenPipeError,OSError):pass
        self.children['observe']=child;self.started_at['observe']=self.clock()
        self.next_start=self.clock()+10

    def reap_stalled(self):
        """Bound a hung native MT5 call; durable intents still forbid blind order retries."""
        for role, child in list(self.children.items()):
            if not self.alive(role):
                continue
            now=self.clock()
            try:
                item=json.loads((self.root/'State'/f'{role}-health.json').read_text())
                age=time.time()-item['at']
                if child_health_matches(item,child) and 0 <= age <= 90:
                    self.last_heartbeat[role]=(child.pid,now-age)
                    continue
            except (OSError,ValueError,KeyError,TypeError):pass
            # Windows atomic replacement or a transient process-inspection error
            # is not proof of a hung child. Keep the last verified heartbeat in
            # monotonic time, scoped to this launcher PID, before reaping.
            previous=self.last_heartbeat.get(role)
            last_seen=self.started_at.get(role,now)
            if previous and previous[0]==child.pid:
                last_seen=max(last_seen,previous[1])
            if now-last_seen < 90:
                continue
            try:
                process=psutil.Process(child.pid)
                # The venv launcher owns a base Python child. Preserve MT5 terminals,
                # including broker-side stops and any order whose response was lost.
                descendants=[p for p in process.children(recursive=True)
                             if p.name().lower() in {'python.exe','pythonw.exe'}]
                for p in descendants:p.terminate()
                _,alive=psutil.wait_procs(descendants,timeout=2)
                for p in alive:p.kill()
                child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)
                self.failures+=1
                self.next_start=self.clock()+min(60,2**min(self.failures+1,6))
                self.log.warning('child_stalled_reaped role=%s reason=HEARTBEAT_EXPIRED',role)
            except psutil.Error:
                self.log.warning('child_stalled_reap_pending role=%s',role)

    def step(self):
        self.reap_stalled()
        if os.environ.get('COPY_AGENT_SOURCE_ONLY')=='1':
            source=fresh_plan(self.backend.request('/internal/copy/agent/poll',
                {'instance_id':self.instance,'report':self.report(),'master_only':True}))
            self.state=source['state']
            if source['state']=='READY_SOURCE':self.start_source(source)
            return self.report()
        plan=fresh_plan(self.backend.request('/internal/copy/agent/poll',
            {'instance_id':self.instance,'report':self.report()}))
        if self.validator:
            try:self.validator.run_once()
            except Exception:self.log.warning('configuration_validation_unavailable')
        if plan['state']=='READY':
            if self.plan_id and not all(self.alive(role) for role in ('observe','execute')):
                self.failures+=1
            self.start(plan)
            if self.state in {'PLAN_CHANGED_OPERATOR_REQUIRED','TERMINAL_SAFETY_BLOCK'}:return self.report()
            if all(self.alive(role) for role in ('observe','execute')):
                # Alive process is not proof of a connected broker: inspect both fresh cycle heartbeats.
                health=[]
                for role in ('observe','execute'):
                    try:
                        item=json.loads((self.root/'State'/f'{role}-health.json').read_text())
                        health.append(child_health_matches(item,self.children[role]) and item.get('state')=='CONNECTED' and 0<=time.time()-item['at']<=15)
                    except (OSError,ValueError,KeyError,TypeError):health.append(False)
                self.state='TERMINALS_CONNECTED' if all(health) else 'TERMINALS_NOT_READY'
        else:
            # Do not kill management of existing positions when authorization/bindings disappear.
            self.state=plan['state']
            source=fresh_plan(self.backend.request('/internal/copy/agent/poll',
                {'instance_id':self.instance,'report':self.report(),'master_only':True}))
            if source['state']=='READY_SOURCE':self.start_source(source)
        return self.report()

    def run(self,once=False):
        failures=0;last_state=None
        while True:
            try:
                self.step();failures=0
            except Exception as error:
                failures+=1
                self.state=('BACKEND_UNAVAILABLE' if isinstance(error,requests.RequestException) else
                    'INVALID_BACKEND_PLAN' if isinstance(error,(ValueError,KeyError,TypeError)) else 'AGENT_LOCAL_ERROR')
                self.log.warning('agent_cycle_failed category=%s attempt=%s',self.state,failures)
            if self.state!=last_state:
                self.log.info('agent_state state=%s armed=%s',self.state,self.armed)
                last_state=self.state
            health=self.root/'State'/'agent-health.json'
            write_json(health, {'at':time.time(),'pid':os.getpid(),**self.report()})
            if once:return failures==0
            # No retry of orders here. Only authenticated read/configuration operations are retried.
            time.sleep((2 if os.environ.get('COPY_AGENT_SOURCE_ONLY')=='1' else 5) if not failures else min(60,2**min(failures,6))+random.uniform(0,1))

    def close(self):
        # Explicit shutdown stops local management; broker SL/TP remain in place.
        for child in self.children.values():
            if child.poll() is None:child.terminate()
        for child in self.children.values():
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--once',action='store_true')
    args=parser.parse_args();root=Path(args.root)
    if os.name!='nt' or not root.is_absolute():raise SystemExit('WINDOWS_AND_ABSOLUTE_ROOT_REQUIRED')
    for part in ('State','logs'):(root/part).mkdir(exist_ok=True,parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[RotatingFileHandler(root/'logs'/'agent.log',maxBytes=2_000_000,backupCount=5,encoding='utf-8')])
    locks=ProcessLocks({'login':'agent','server':os.environ.get('COPY_DEMO_WORKER_ID',''),
                        'terminal':str(root/'agent.exe')})
    supervisor=Supervisor(root)
    try:
        result=supervisor.run(once=args.once)
        if args.once and not result:raise SystemExit(1)
    except KeyboardInterrupt:pass
    finally:supervisor.close();locks.close()


if __name__=='__main__':main()
