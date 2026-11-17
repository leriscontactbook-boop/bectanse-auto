"""Bounded process dispatch for the existing real executor, never an order API."""
import json
import math
import subprocess
import time
from pathlib import Path


def slots(config):
    count = config.get('executor_slots', 1)
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError('EXPLICIT_EXECUTOR_CAPACITY_REQUIRED')
    if config.get('executor_manifest_directory') and config.get('executor_manifest'):
        raise ValueError('AMBIGUOUS_EXECUTOR_CONFIGURATION')
    if count > 1 and (not config.get('executor_manifest_directory') or config.get('executor_manifest')):
        raise ValueError('ACCOUNT_MANIFEST_DIRECTORY_REQUIRED')
    return count


def slot_health(path, slot):
    path = Path(path)
    return path.with_name(path.stem + '-slot-' + str(slot) + path.suffix)


def heartbeat_fresh(path, started, now):
    """An old file, corrupt heartbeat or future timestamp proves no liveness."""
    try:
        item=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        stamp=item['at']
        return (type(stamp) in (int,float) and math.isfinite(stamp)
                and started <= stamp <= now+5 and now-stamp <= 45)
    except (OSError,ValueError,KeyError,TypeError):
        return False


def run_pool(config, command, stop, health, *, spawn=subprocess.Popen, clock=time.monotonic, wall_clock=time.time):
    """Every MT5 Python connection belongs to a separate OS process.

    A stalled child requests fenced recovery by the existing Windows host. The
    pool stays alive until that host stops its entire owned process tree and
    waits for exit; it never spawns a replacement beside a living child.
    """
    count = slots(config)
    children, failures, retry_at, started = {}, {}, {}, {}
    try:
        while not stop.is_set():
            now = clock()
            for index in range(count):
                child = children.get(index)
                if child is not None and child.poll() is not None:
                    del children[index]
                    failures[index] = failures.get(index, 0) + 1
                    retry_at[index] = now + min(60, 2 ** min(failures[index], 6))
                if index not in children and now >= retry_at.get(index, 0):
                    try:
                        children[index] = spawn([*command, '--slot', str(index)],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        started[index] = (clock(),wall_clock())
                    except OSError:
                        failures[index] = failures.get(index, 0) + 1
                        retry_at[index] = now + min(60, 2 ** min(failures[index], 6))
            stalled = [index for index in children
                       if config.get('health') and now-started[index][0] >= 60
                       and not heartbeat_fresh(slot_health(config['health'],index),started[index][1],wall_clock())]
            if stalled:
                health({'state':'POOL_RECOVERY_REQUIRED','stalled_slots':stalled,
                        'slots':count,'running_processes':len(children),'execution_enabled':False})
                # Keep ancestry intact for Stop-OwnedRunner. Exiting here could
                # orphan a blocked interpreter and defeat tree exclusion.
                while not stop.is_set():stop.wait(1)
                break
            health({'state': 'EXECUTOR_POOL_RUNNING', 'slots': count,
                    'running_processes': len(children), 'execution_enabled': False,
                    'execution_status': 'PER_ACCOUNT_AUTHORITY_REQUIRED'})
            stop.wait(1)
    finally:
        # The common stop file also stops every ExecutionHost. Do not kill a
        # venv wrapper and then start a replacement while its interpreter lives.
        # If shutdown exceeds the bound, the existing Windows host owns cleanup.
        deadline = clock() + 15
        for child in children.values():
            if child.poll() is None:
                child.wait(timeout=max(.1, deadline - clock()))
