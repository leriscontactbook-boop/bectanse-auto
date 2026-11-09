"""Entrypoint for the dedicated Windows copy-trading worker host."""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import time

from .config import validate_worker_config
from .worker import run_follower_workers, run_master_monitor


log = logging.getLogger("copy_trading.host")


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(os.environ.get("COPY_TRADING_LOG_LEVEL", "INFO").upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)


def _component(role: str, target, config: dict) -> None:
    """Keep each role alive; the Windows task host also restarts this process."""
    _configure_logging()
    failures = 0
    while True:
        try:
            target(config)
            failures += 1
            log.error("component_returned role=%s", role)
        except Exception as error:
            failures += 1
            log.error("component_failed role=%s type=%s", role, type(error).__name__)
        time.sleep(min(60, 2 ** min(failures, 6)))


def main() -> None:
    _configure_logging()
    config = validate_worker_config()
    context = mp.get_context("spawn")
    components = [
        ("MASTER", run_master_monitor),
        ("FOLLOWER", run_follower_workers),
    ]
    processes = {}
    try:
        while True:
            for role, target in components:
                process = processes.get(role)
                if process is None or not process.is_alive():
                    if process is not None:
                        process.join(timeout=1)
                        log.error("component_restarting role=%s exit_code=%s", role, process.exitcode)
                    process = context.Process(
                        target=_component, args=(role, target, config),
                        name=f"copy-trading-{role.lower()}", daemon=False,
                    )
                    process.start()
                    processes[role] = process
                    log.info("component_started role=%s pid=%s", role, process.pid)
            time.sleep(2)
    except KeyboardInterrupt:
        log.info("worker_host_stopping")
    finally:
        for process in processes.values():
            if process.is_alive():
                process.terminate()
        for process in processes.values():
            process.join(timeout=5)


if __name__ == "__main__":
    mp.freeze_support()
    main()
