"""Atomic state publication tolerant of bounded Windows reader sharing locks."""
import json
import os
from pathlib import Path
import time
import uuid


def write_json(path, value, *, replace=None, sleep=time.sleep, attempts=20):
    replace = replace or os.replace
    path = Path(path)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.' + uuid.uuid4().hex + '.tmp')
    try:
        # Persist the complete new file before publishing its name. Atomic rename
        # alone can leave a zero-filled destination after a host power loss.
        with temporary.open('w', encoding='utf-8') as output:
            output.write(json.dumps(value, allow_nan=False))
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(attempts):
            try:
                replace(temporary, path)
                return
            except PermissionError:
                # A Windows reader can momentarily deny FILE_SHARE_DELETE. Do not
                # restart a healthy trading process because its heartbeat was read.
                if attempt == attempts - 1:
                    raise
                sleep(0.05)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
