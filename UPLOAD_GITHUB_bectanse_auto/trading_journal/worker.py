"""Windows MT5 worker pool.

Run with: python -m trading_journal.worker
Each worker must own a distinct MetaTrader terminal installation.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import socket
import threading
import time
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

from .providers.base import ProviderError
from .providers.mt5 import MetaTrader5Provider
from .security import canonical_worker_signature
from .config import validate_worker_config


LOGGER = logging.getLogger("bectanse.mt5_worker")
WORKER_VERSION = "1.6.0"


def _log(event: str, **values):
    LOGGER.info(json.dumps({"event": event, **values}, separators=(",", ":"), default=str))


def _terminate_terminal_process(terminal_path: str) -> int:
    """Terminate only the MT5 process owned by one isolated worker slot."""
    if not terminal_path:
        return 0
    try:
        import psutil
    except ImportError:
        _log("terminal_cleanup_unavailable", terminal_path=terminal_path)
        return 0
    target = os.path.normcase(os.path.abspath(terminal_path))
    stopped = 0
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            executable = process.info.get("exe") or ""
            if not executable or os.path.normcase(os.path.abspath(executable)) != target:
                continue
            process.kill()
            process.wait(timeout=10)
            stopped += 1
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired):
            continue
    if stopped:
        _log("terminal_process_recovered", terminal_path=terminal_path, stopped=stopped)
    return stopped


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def month_ranges(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        boundary = min(end, _next_month(cursor))
        yield cursor, boundary
        cursor = boundary


def history_ranges(start: datetime, end: datetime):
    """Read MT5 history in a few bounded chunks instead of one call per month."""
    chunk_days = max(31, min(int(os.environ.get("MT5_HISTORY_CHUNK_DAYS", "3650")), 3650))
    cursor = start
    while cursor < end:
        boundary = min(end, cursor + timedelta(days=chunk_days))
        yield cursor, boundary
        cursor = boundary


class WorkerBackendClient:
    def __init__(self, base_url: str, secret: str, worker_id: str, instance_id: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.worker_id = worker_id
        self.instance_id = instance_id
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("MT5_BACKEND_URL must use HTTPS")
        if len(secret) < 32:
            raise RuntimeError("INTERNAL_MT5_WORKER_SECRET must contain at least 32 characters")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"Bectanse-MT5-Worker/{WORKER_VERSION}"})

    def request(self, method: str, path: str, payload: dict | None = None, timeout=60):
        body = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        headers = {
            "Content-Type": "application/json",
            "X-MT5-Worker-ID": self.worker_id,
            "X-MT5-Instance-ID": self.instance_id,
            "X-MT5-Timestamp": timestamp,
            "X-MT5-Nonce": nonce,
            "X-MT5-Signature": canonical_worker_signature(
                self.secret, method, path, timestamp, body, nonce
            ),
        }
        response = self.session.request(
            method, self.base_url + path, data=body, headers=headers, timeout=timeout
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def claim(self):
        result = self.request("POST", "/internal/mt5/jobs/claim", {})
        return (result or {}).get("job")

    def heartbeat(self, job_id: str, timeout=60):
        return self.request(
            "POST", f"/internal/mt5/jobs/{job_id}/heartbeat", {}, timeout=timeout
        )

    def node_heartbeat(self, status="ONLINE", current_job_id="", terminal_fingerprint="", timeout=20):
        return self.request("POST", "/internal/mt5/workers/heartbeat", {
            "status": status, "current_job_id": current_job_id,
            "terminal_fingerprint": terminal_fingerprint, "version": WORKER_VERSION,
        }, timeout=timeout)

    def upload(self, job_id: str, deals: list[dict], batch_id: str):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/batch",
                            {"deals": deals, "batch_id": batch_id}, timeout=120)

    def complete(self, job_id: str, account: dict, duration_ms: int, received_deals: int,
                 source_pnl: str, positions: list[dict], orders: list[dict], terminal: dict):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/complete", {
            "account": account, "duration_ms": duration_ms,
            "received_deals": received_deals, "source_pnl": source_pnl,
            "positions": positions, "orders": orders, "terminal": terminal,
        })

    def fail(self, job_id: str, code: str, retryable: bool, **diagnostic):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/fail", {
            "code": code, "retryable": retryable, **diagnostic,
        })


def account_terminal_path(account_id, default_path: str) -> str:
    """Route an account to its isolated broker terminal, when configured."""
    mapping = json.loads(os.environ.get("MT5_ACCOUNT_TERMINAL_PATHS", "{}"))
    if not isinstance(mapping, dict):
        raise ValueError("MT5_ACCOUNT_TERMINAL_PATHS must be an object")
    paths = []
    for key, path in mapping.items():
        if not str(key).isdigit() or not isinstance(path, str) or not path.strip():
            raise ValueError("Invalid account terminal mapping")
        paths.append(os.path.normcase(os.path.abspath(path)).casefold())
    if len(paths) != len(set(paths)):
        raise ValueError("Each account override must own a distinct terminal")
    configured_pool = os.environ.get("MT5_TERMINAL_PATHS", "").split(";")
    pool = {os.path.normcase(os.path.abspath(p)).casefold() for p in configured_pool if p}
    if pool.intersection(paths):
        raise ValueError("Account terminals must be separate from the shared pool")
    return mapping.get(str(account_id), default_path)


class MT5Worker:
    def __init__(self, worker_id: str, terminal_path: str, client: WorkerBackendClient,
                 progress_callback=None):
        self.worker_id = worker_id
        self.terminal_path = terminal_path
        self.client = client
        self.progress_callback = progress_callback or (lambda _state, _job_id="": None)
        self.terminal_fingerprint = hashlib.sha256(terminal_path.lower().encode("utf-8")).hexdigest()[:24]

    def _start_job_heartbeat(self, job_id: str):
        stop = threading.Event()

        def maintain_lease():
            while not stop.wait(20):
                try:
                    self.client.heartbeat(job_id)
                    self.client.node_heartbeat("BUSY", job_id, self.terminal_fingerprint)
                except Exception:
                    LOGGER.warning(json.dumps({
                        "event": "job_heartbeat_failed",
                        "worker_id": self.worker_id,
                        "job_id": job_id,
                    }))

        thread = threading.Thread(
            target=maintain_lease,
            daemon=True,
            name=f"heartbeat-{self.worker_id}",
        )
        thread.start()
        return stop, thread

    def run_once(self) -> bool:
        job = self.client.claim()
        if not job:
            return False
        job_id = job["id"]
        account_id = job["account_id"]
        self.progress_callback("BUSY", job_id)
        started = time.monotonic()
        imported = 0
        received = 0
        source_pnl = 0
        recover_terminal = False
        phase = "connect"
        heartbeat_stop = None
        heartbeat_thread = None
        terminal_path = self.terminal_path
        provider = MetaTrader5Provider(terminal_path=terminal_path)

        def stop_job_heartbeat():
            nonlocal heartbeat_stop, heartbeat_thread
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=5)
            heartbeat_stop = None
            heartbeat_thread = None

        try:
            terminal_path = account_terminal_path(account_id, self.terminal_path)
            provider = MetaTrader5Provider(terminal_path=terminal_path)
            self.client.node_heartbeat("BUSY", job_id, self.terminal_fingerprint)
            heartbeat_stop, heartbeat_thread = self._start_job_heartbeat(job_id)
            _log("sync_started", worker_id=self.worker_id, job_id=job_id, account_id=account_id)
            account = provider.connect(job["login"], job["server"], job["password"])
            phase = "identity"
            from .brokers import server_identity
            if str(account.login) != str(job["login"]) or server_identity(account.server) != server_identity(job["server"]):
                raise ProviderError("SYNC_ERROR", "Le compte connecté ne correspond pas à la tâche.", retryable=False)
            phase = "history"
            if str(job.get("job_type") or "") != "TELEMETRY":
                batch = []
                start = datetime.fromisoformat(job["date_from"].replace("Z", "+00:00")).astimezone(timezone.utc)
                end = datetime.fromisoformat(job["date_to"].replace("Z", "+00:00")).astimezone(timezone.utc)
                for period_start, period_end in history_ranges(start, end):
                    for deal in provider.get_deals(period_start, period_end):
                        batch.append(deal.as_dict())
                        received += 1
                        source_pnl += deal.net_pnl
                        if len(batch) >= 1000:
                            batch_id = f"{batch[0]['ticket']}-{batch[-1]['ticket']}-{len(batch)}"
                            phase = "upload"
                            result = self.client.upload(job_id, batch, batch_id)
                            phase = "history"
                            imported += int((result or {}).get("inserted", 0))
                            batch = []
                            self.progress_callback("BUSY", job_id)
                    if batch:
                        batch_id = f"{batch[0]['ticket']}-{batch[-1]['ticket']}-{len(batch)}"
                        phase = "upload"
                        result = self.client.upload(job_id, batch, batch_id)
                        phase = "history"
                        imported += int((result or {}).get("inserted", 0))
                        batch = []
                    self.client.heartbeat(job_id)
                    self.progress_callback("BUSY", job_id)
            phase = "snapshot"
            latest_account = provider.get_account_info()
            terminal = provider.get_terminal_info()
            positions = provider.get_open_positions()
            orders = provider.get_open_orders()
            duration_ms = int((time.monotonic() - started) * 1000)
            phase = "complete"
            completion = self.client.complete(
                job_id, latest_account.as_dict(), duration_ms, received,
                str(source_pnl), positions, orders, terminal,
            )
            if (completion or {}).get("ignored"):
                _log("sync_result_discarded", worker_id=self.worker_id, job_id=job_id,
                     account_id=str(account_id)[-6:], reason=completion.get("reason", "lease_lost"))
                return True
            _log("integrity_failed" if (completion or {}).get("integrity") == "MISMATCH" else "sync_success",
                 worker_id=self.worker_id, job_id=job_id,
                 account_id=str(account_id)[-6:], deals_received=received,
                 deals_imported=imported, duration_ms=duration_ms)
            return True
        except ProviderError as error:
            recover_terminal = error.code == "TERMINAL_ERROR"
            _log("sync_failed", worker_id=self.worker_id, job_id=job_id,
                 account_id=account_id, error_code=error.code,
                 mt5_error_code=error.diagnostic_code)
            stop_job_heartbeat()
            self.client.fail(job_id, error.code, error.retryable, native_error_code=error.diagnostic_code,
                             phase=phase, duration_ms=int((time.monotonic()-started)*1000))
            return True
        except Exception:
            recover_terminal = True
            LOGGER.exception(json.dumps({"event": "sync_failed", "worker_id": self.worker_id,
                                         "job_id": job_id, "account_id": account_id,
                                         "error_code": "SYNC_ERROR"}))
            try:
                stop_job_heartbeat()
                self.client.fail(job_id, "SYNC_ERROR", True, phase=phase,
                                 duration_ms=int((time.monotonic()-started)*1000))
            except Exception:
                LOGGER.exception("Could not report MT5 worker failure")
            return True
        finally:
            stop_job_heartbeat()
            try:
                provider.disconnect()
            except Exception:
                recover_terminal = True
                LOGGER.exception(json.dumps({"event": "terminal_shutdown_failed",
                                             "worker_id": self.worker_id,
                                             "job_id": job_id}))
            if recover_terminal:
                _terminate_terminal_process(terminal_path)
            try:
                self.client.node_heartbeat("ONLINE", "", self.terminal_fingerprint)
            except Exception:
                LOGGER.warning(json.dumps({"event": "worker_heartbeat_failed", "worker_id": self.worker_id}))
            self.progress_callback("IDLE", "")

    def serve(self, stop_event):
        idle_seconds = max(2, int(os.environ.get("MT5_WORKER_POLL_SECONDS", "2")))
        last_heartbeat = 0.0
        while not stop_event.is_set():
            try:
                if time.monotonic() - last_heartbeat >= 30:
                    self.client.node_heartbeat("ONLINE", "", self.terminal_fingerprint)
                    last_heartbeat = time.monotonic()
                worked = self.run_once()
            except Exception:
                LOGGER.exception(json.dumps({"event": "worker_loop_failed", "worker_id": self.worker_id}))
                worked = False
            if not worked:
                stop_event.wait(idle_seconds)


def _serve_worker_process(backend_url: str, secret: str, machine_id: str,
                          worker_id: str, terminal_path: str, stop_event,
                          progress_queue) -> None:
    """Run one MT5 terminal in its own process.

    MetaQuotes exposes process-global IPC state, so terminal slots must never
    share a Python process.
    """
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    client = WorkerBackendClient(backend_url, secret, worker_id, machine_id)
    def report_progress(state: str, job_id: str = "") -> None:
        progress_queue.put((worker_id, os.getpid(), state, job_id, time.monotonic()))

    _log("worker_process_started", worker_id=worker_id)
    report_progress("IDLE")
    MT5Worker(worker_id, terminal_path, client, report_progress).serve(stop_event)


class MT5WorkerPool:
    def __init__(self):
        config = validate_worker_config()
        backend_url, secret = config["backend_url"], config["secret"]
        paths, configured_count = config["terminal_paths"], config["count"]
        maximum = max(1, int(os.environ.get("MAX_CONCURRENT_MT5_SESSIONS", str(configured_count))))
        if configured_count > maximum:
            raise RuntimeError("MT5_WORKER_COUNT exceeds MAX_CONCURRENT_MT5_SESSIONS")
        machine_id = (os.environ.get("WORKER_ID") or os.environ.get("MT5_INSTANCE_ID") or socket.gethostname())[:100]
        self.backend_url = backend_url
        self.secret = secret
        self.machine_id = machine_id
        self.worker_specs = []
        for index in range(configured_count):
            worker_id = f"{machine_id}-{index + 1}"
            self.worker_specs.append((worker_id, paths[index]))

    def serve_forever(self):
        context = multiprocessing.get_context("spawn")
        stop_event = context.Event()
        progress_queue = context.Queue()
        processes = {}
        progress = {}
        supervisor_clients = {}
        watchdog_seconds = max(
            120, int(os.environ.get("MT5_JOB_WATCHDOG_SECONDS", "420"))
        )

        def start_process(worker_id: str, terminal_path: str):
            process = context.Process(
                target=_serve_worker_process,
                args=(self.backend_url, self.secret, self.machine_id,
                      worker_id, terminal_path, stop_event, progress_queue),
                name=worker_id,
            )
            process.start()
            return process

        for worker_id, terminal_path in self.worker_specs:
            processes[worker_id] = start_process(worker_id, terminal_path)
            supervisor_clients[worker_id] = WorkerBackendClient(
                self.backend_url, self.secret, worker_id, self.machine_id
            )
        _log("worker_pool_started", worker_count=len(processes), isolation="process")
        last_supervisor_heartbeat = 0.0

        try:
            while not stop_event.wait(2):
                while True:
                    try:
                        worker_id, process_id, state, job_id, observed_at = progress_queue.get_nowait()
                    except queue.Empty:
                        break
                    process = processes.get(worker_id)
                    if process is not None and process.pid == process_id:
                        progress[worker_id] = (state, observed_at, job_id)
                now = time.monotonic()
                if now - last_supervisor_heartbeat >= 20:
                    for worker_id, terminal_path in self.worker_specs:
                        process = processes[worker_id]
                        state, _observed_at, job_id = progress.get(
                            worker_id, ("STARTING", now, "")
                        )
                        if not process.is_alive():
                            continue
                        fingerprint = hashlib.sha256(
                            terminal_path.lower().encode("utf-8")
                        ).hexdigest()[:24]
                        try:
                            if state == "BUSY" and job_id:
                                supervisor_clients[worker_id].heartbeat(job_id, timeout=5)
                                supervisor_clients[worker_id].node_heartbeat(
                                    "BUSY", job_id, fingerprint, timeout=5
                                )
                            else:
                                supervisor_clients[worker_id].node_heartbeat(
                                    "ONLINE", "", fingerprint, timeout=5
                                )
                        except requests.HTTPError as error:
                            if error.response is not None and error.response.status_code == 404:
                                _log("supervisor_lease_finished", worker_id=worker_id,
                                     job_id=job_id)
                            else:
                                LOGGER.warning(json.dumps({
                                    "event": "supervisor_heartbeat_failed",
                                    "worker_id": worker_id,
                                    "job_id": job_id,
                                }))
                        except Exception:
                            LOGGER.warning(json.dumps({
                                "event": "supervisor_heartbeat_failed",
                                "worker_id": worker_id,
                                "job_id": job_id,
                            }))
                    last_supervisor_heartbeat = now
                for worker_id, terminal_path in self.worker_specs:
                    process = processes[worker_id]
                    state, observed_at, job_id = progress.get(
                        worker_id, ("STARTING", time.monotonic(), "")
                    )
                    if (process.is_alive() and state == "BUSY"
                            and time.monotonic() - observed_at > watchdog_seconds):
                        _log("worker_process_watchdog_timeout", worker_id=worker_id,
                             process_id=process.pid, watchdog_seconds=watchdog_seconds)
                        process.terminate()
                        process.join(timeout=10)
                        if process.is_alive():
                            process.kill()
                            process.join(timeout=5)
                        _terminate_terminal_process(terminal_path)
                        if job_id:
                            try:
                                supervisor_clients[worker_id].fail(
                                    job_id, "WORKER_TIMEOUT", True
                                )
                            except Exception:
                                LOGGER.exception(json.dumps({
                                    "event": "watchdog_failure_report_failed",
                                    "worker_id": worker_id,
                                    "job_id": job_id,
                                }))
                        processes[worker_id] = start_process(worker_id, terminal_path)
                        progress[worker_id] = ("STARTING", time.monotonic(), "")
                        continue
                    if process.is_alive():
                        continue
                    exit_code = process.exitcode
                    process.join(timeout=1)
                    _log("worker_process_restarting", worker_id=worker_id,
                         exit_code=exit_code)
                    _terminate_terminal_process(terminal_path)
                    if job_id:
                        try:
                            supervisor_clients[worker_id].fail(
                                job_id, "WORKER_TIMEOUT", True
                            )
                        except Exception:
                            LOGGER.exception(json.dumps({
                                "event": "crash_failure_report_failed",
                                "worker_id": worker_id,
                                "job_id": job_id,
                            }))
                    processes[worker_id] = start_process(worker_id, terminal_path)
                    progress[worker_id] = ("STARTING", time.monotonic(), "")
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            for process in processes.values():
                process.join(timeout=20)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    MT5WorkerPool().serve_forever()


if __name__ == "__main__":
    main()
