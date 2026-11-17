"""Per-account OS advisory lock. All registry writers must use this boundary.
Local filesystem only; not a distributed lock or broker-side fencing proof.
"""
from contextlib import contextmanager
from pathlib import Path
import os,time

@contextmanager
def account_lock(database,account,timeout=5):
    path=Path(str(Path(database).resolve())+'.account-'+str(account)+'.lock')
    handle=path.open('a+b');locked=False
    try:
        if path.stat().st_size==0:handle.write(b'0');handle.flush()
        deadline=time.monotonic()+timeout
        while True:
            try:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                locked=True;break
            except (BlockingIOError,OSError):
                if time.monotonic()>=deadline:raise TimeoutError('ADMISSION_ACCOUNT_BUSY')
                time.sleep(.01)
        yield
    finally:
        try:
            if locked:
                handle.seek(0)
                if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()
