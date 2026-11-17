"""Windows preparation worker, no order execution or member activation.

Uses the existing authenticated backend transport and dedicated terminal
provisioner. One terminal is attached at a time in this process. An execution
worker must take the same OS account lock before taking over a prepared terminal.
"""
import argparse
import os
import time
from .demo_runtime import DemoBackend
from .real_session import RealSession


def run_once(backend, module, root, template, *, session_factory=RealSession, clock=time.time):
    plan = backend.request('/internal/copy/real/preparation/claim', {'role':'preparation'}).get('plan')
    if not plan:
        return False
    password = plan.pop('password')
    try:
        with session_factory(module, root, template, plan['identity'], password=password) as session:
            # No instrument equivalence is inferred during connection. Actual
            # execution qualification must inspect source/target metadata later.
            observation = session.observe({}, history_from=max(0, clock()-86400),
                                          key=backend.secret.encode(), clock=clock)
            backend.request('/internal/copy/real/preparation/report',
                            {'job': plan['job'], 'observation': observation})
        return True
    except Exception as error:
        known={'TERMINAL_AUTH_FAILED','TERMINAL_CONNECTION_FAILED','TERMINAL_DISCONNECTED',
               'ACCOUNT_IDENTITY_CHANGED','TERMINAL_IDENTITY_CHANGED','REAL_ACCOUNT_REQUIRED',
               'HEDGING_REQUIRED','FIFO_UNSUPPORTED','COLLECTION_FAILED'}
        reason=str(error) if str(error) in known else 'CHECK_UNAVAILABLE'
        diagnostic = getattr(error, 'diagnostic_code', None)
        if type(diagnostic) is int:
            print('REAL_PREPARATION_DIAGNOSTIC code=' + str(diagnostic), flush=True)
        backend.request('/internal/copy/real/preparation/failed',{'job':plan['job'],'reason':reason})
        return True
    finally:
        password = None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--health-file')
    args=parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('WINDOWS_REQUIRED')
    import MetaTrader5 as mt5
    # Reuse only the authenticated HTTPS transport, never its demo configuration API.
    backend=DemoBackend(url=os.environ['COPY_CONFIG_BACKEND_URL'],
                        secret=os.environ['COPY_REAL_PREPARATION_SECRET'],
                        identity=os.environ['COPY_REAL_PREPARATION_NODE'])
    while True:
        try:
            worked=run_once(backend,mt5,args.root,args.template)
            state='PREPARATION_PROCESSED' if worked else 'WAITING_FOR_ASSIGNMENT'
        except Exception:
            # No exception text: terminal errors can include credential material.
            print('REAL_PREPARATION_UNCONFIRMED',flush=True)
            worked=False
            state='BACKEND_OR_TERMINAL_UNAVAILABLE'
        if args.health_file:
            from .atomic_io import write_json
            write_json(args.health_file,{'at':time.time(),'pid':os.getpid(),'state':state,
                                        'execution_enabled':False})
        if not args.watch:return
        time.sleep(1 if worked else 5)


if __name__=='__main__':main()
