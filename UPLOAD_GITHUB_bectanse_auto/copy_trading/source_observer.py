"""Read-only master observer; no follower credentials or execution engine."""
import argparse,hashlib,json,os,re,sys,time
from pathlib import Path
from types import SimpleNamespace
import psutil
from .atomic_io import write_json
from .demo_runtime import ProcessLocks
from .demo_mt5 import DemoMT5
from .demo_store import Ledger
from .demo_engine import Observer
from .native_reader_install import ensure


def run(root,plan,module,parent_alive,*,sleep=time.sleep):
    phase='PLAN'
    terminal=root/'Terminals'/'Master'/'terminal64.exe'
    lock=ledger=broker=None
    try:
        if plan.get('state')!='READY_SOURCE':raise ValueError('MASTER_SOURCE_PLAN_REQUIRED')
        master=plan['master']
        identity=dict(login=str(master['login']),server=master['server'],terminal=str(terminal))
        lock=ProcessLocks(identity)
        offsets=json.loads(os.environ.get('COPY_DEMO_BROKER_TIME_OFFSETS','{"master":0,"follower":0}'))
        broker=DemoMT5(module,login=identity['login'],server=identity['server'],terminal_path=str(terminal),
                       password=master.pop('password'),writable=False,broker_time_offset_seconds=offsets['master'])
        phase='MASTER_CONNECTION'
        broker.connect()
        reader_path=os.environ.get('COPY_MASTER_NATIVE_RUNTIME')
        if not reader_path:raise ValueError('MASTER_READER_RUNTIME_REQUIRED')
        phase='READER_RUNTIME'
        reader=json.loads(Path(reader_path).read_text(encoding='utf-8-sig'))['native_reader']
        def reader_heartbeat():
            # This is liveness only.  It does not assert a broker connection,
            # authorize trading, or alter the copy state.
            write_json(root/'State'/'observe-health.json',dict(
                at=time.time(),pid=os.getpid(),state='STARTING_READER',
                execution_enabled=False))
        phase='READER_START'
        ensure(SimpleNamespace(module=module,broker=broker),terminal.parent,
               dict(login=identity['login'],server=identity['server'],trade_mode=0),reader['artifact'],reader['sha256'],
               heartbeat=reader_heartbeat)
        broker.password=None
        phase='LEDGER'
        # An operator can replace the configured master.  Preserve the old
        # identity-bound ledger as an archive and start an isolated observer
        # ledger for the newly authenticated master; never rewrite history.
        ledger=Ledger(root/'State'/'ledger.sqlite3')
        try:
            ledger.pin('master_identity',identity)
            ledger.pin('broker_time_offsets',offsets)
        except ValueError:
            ledger.close()
            key=hashlib.sha256((identity['login']+'@'+identity['server']).encode()).hexdigest()[:16]
            ledger=Ledger(root/'State'/('master-ledger-'+key+'.sqlite3'))
            ledger.pin('master_identity',identity)
            ledger.pin('broker_time_offsets',offsets)
        phase='OBSERVING'
        observer=Observer(ledger,broker,identity['login']+'@'+identity['server'])
        while parent_alive():
            started=time.monotonic()
            observer.cycle()
            try:
                from .dashboard_telemetry import capture
                write_json(root/'State'/'master-telemetry.json',capture(module,identity['login'],identity['server']))
            except (ValueError,TypeError,AttributeError,OSError):
                pass  # Missing/stale telemetry is never reported as an empty account.
            write_json(root/'State'/'observe-health.json',dict(at=time.time(),pid=os.getpid(),state='CONNECTED',execution_enabled=False,cycle_ms=round((time.monotonic()-started)*1000,2)))
            sleep(max(.01,.1-(time.monotonic()-started)))
    except Exception as error:
        # Keep an actionable, non-sensitive failure record for the supervisor.
        # This must live in the scope that owns ``phase``; otherwise an early
        # startup failure masks the original diagnostic and leaves stale health.
        code=str(error)
        reason=code if re.fullmatch(r'[A-Z][A-Z0-9_]{1,96}',code or '') else 'MASTER_OBSERVATION_UNCONFIRMED'
        write_json(root/'State'/'observe-health.json',dict(
            at=time.time(),pid=os.getpid(),state='BLOCKED',error=reason,
            error_type=type(error).__name__,phase=phase,execution_enabled=False))
        raise
    finally:
        if broker:broker.password=None;broker.disconnect()
        if ledger:ledger.close()
        if lock:lock.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--parent-pid',type=int,required=True);p.add_argument('--parent-start',type=float,required=True);a=p.parse_args()
    if os.name!='nt':raise SystemExit('WINDOWS_REQUIRED')
    # A venv launcher can retain an inherited stdin handle.  Read one bounded
    # plan line rather than waiting for a process-wide EOF before the observer
    # can begin its read-only master session.
    root=Path(a.root);plan=json.loads(sys.stdin.readline(16385))
    def alive():
        try:return psutil.Process(a.parent_pid).create_time()==a.parent_start
        except psutil.Error:return False
    import MetaTrader5 as mt5
    try:run(root,plan,mt5,alive)
    except Exception:
        raise SystemExit(1) from None
if __name__=='__main__':main()
