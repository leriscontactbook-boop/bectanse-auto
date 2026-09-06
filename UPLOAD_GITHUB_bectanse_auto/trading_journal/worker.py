"""Windows MT5 worker pool.

Run with: python -m trading_journal.worker
Each worker must own a distinct MetaTrader terminal installation.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .providers.base import ProviderError
from .providers.mt5 import MetaTrader5Provider
from .security import canonical_worker_signature
from .config import validate_worker_config


LOGGER = logging.getLogger("bectanse.mt5_worker")


def _log(event: str, **values):
    LOGGER.info(json.dumps({"event": event, **values}, separators=(",", ":"), default=str))


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
        response = requests.request(
            method, self.base_url + path, data=body, headers=headers, timeout=timeout
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def claim(self):
        result = self.request("POST", "/internal/mt5/jobs/claim", {})
        return (result or {}).get("job")

    def heartbeat(self, job_id: str):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/heartbeat", {})

    def node_heartbeat(self, status="ONLINE", current_job_id="", terminal_fingerprint=""):
        return self.request("POST", "/internal/mt5/workers/heartbeat", {
            "status": status, "current_job_id": current_job_id,
            "terminal_fingerprint": terminal_fingerprint, "version": "1.0.0",
        }, timeout=20)

    def upload(self, job_id: str, deals: list[dict], batch_id: str):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/batch",
                            {"deals": deals, "batch_id": batch_id}, timeout=120)

    def complete(self, job_id: str, account: dict, duration_ms: int, received_deals: int, source_pnl: str):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/complete", {
            "account": account, "duration_ms": duration_ms,
            "received_deals": received_deals, "source_pnl": source_pnl,
        })

    def fail(self, job_id: str, code: str, retryable: bool):
        return self.request("POST", f"/internal/mt5/jobs/{job_id}/fail", {
            "code": code, "retryable": retryable,
        })


class MT5Worker:
    def __init__(self, worker_id: str, terminal_path: str, client: WorkerBackendClient):
        self.worker_id = worker_id
        self.terminal_path = terminal_path
        self.client = client
        self.terminal_fingerprint = hashlib.sha256(terminal_path.lower().encode("utf-8")).hexdigest()[:24]

    def run_once(self) -> bool:
        job = self.client.claim()
        if not job:
            return False
        job_id = job["id"]
        account_id = job["account_id"]
        started = time.monotonic()
        imported = 0
        received = 0
        source_pnl = 0
        provider = MetaTrader5Provider(terminal_path=self.terminal_path)
        try:
            self.client.node_heartbeat("BUSY", job_id, self.terminal_fingerprint)
            _log("sync_started", worker_id=self.worker_id, job_id=job_id, account_id=account_id)
            account = provider.connect(job["login"], job["server"], job["password"])
            batch = []
            start = datetime.fromisoformat(job["date_from"].replace("Z", "+00:00")).astimezone(timezone.utc)
            end = datetime.fromisoformat(job["date_to"].replace("Z", "+00:00")).astimezone(timezone.utc)
            for period_start, period_end in month_ranges(start, end):
                for deal in provider.get_deals(period_start, period_end):
                    batch.append(deal.as_dict())
                    received += 1
                    source_pnl += deal.net_pnl
                    if len(batch) >= 1000:
                        batch_id = f"{batch[0]['ticket']}-{batch[-1]['ticket']}-{len(batch)}"
                        result = self.client.upload(job_id, batch, batch_id)
                        imported += int((result or {}).get("inserted", 0))
                        batch = []
                if batch:
                    batch_id = f"{batch[0]['ticket']}-{batch[-1]['ticket']}-{len(batch)}"
                    result = self.client.upload(job_id, batch, batch_id)
                    imported += int((result or {}).get("inserted", 0))
                    batch = []
                self.client.heartbeat(job_id)
            latest_account = provider.get_account_info()
            provider.get_terminal_info()
            provider.get_open_positions()
            duration_ms = int((time.monotonic() - started) * 1000)
            self.client.complete(job_id, latest_account.as_dict(), duration_ms, received, str(source_pnl))
            _log("sync_success", worker_id=self.worker_id, job_id=job_id,
                 account_id=str(account_id)[-6:], deals_received=received,
                 deals_imported=imported, duration_ms=duration_ms)
            return True
        except ProviderError as error:
            _log("sync_failed", worker_id=self.worker_id, job_id=job_id,
                 account_id=account_id, error_code=error.code)
            self.client.fail(job_id, error.code, error.retryable)
            return True
        except Exception:
            LOGGER.exception(json.dumps({"event": "sync_failed", "worker_id": self.worker_id,
                                         "job_id": job_id, "account_id": account_id,
                                         "error_code": "SYNC_ERROR"}))
            try:
                self.client.fail(job_id, "SYNC_ERROR", True)
            except Exception:
                LOGGER.exception("Could not report MT5 worker failure")
            return True
        finally:
            provider.disconnect()
            try:
                self.client.node_heartbeat("ONLINE", "", self.terminal_fingerprint)
            except Exception:
                LOGGER.warning(json.dumps({"event": "worker_heartbeat_failed", "worker_id": self.worker_id}))

    def serve(self, stop_event: threading.Event):
        idle_seconds = max(2, int(os.environ.get("MT5_WORKER_POLL_SECONDS", "5")))
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


class MT5WorkerPool:
    def __init__(self):
        config = validate_worker_config()
        backend_url, secret = config["backend_url"], config["secret"]
        paths, configured_count = config["terminal_paths"], config["count"]
        maximum = max(1, int(os.environ.get("MAX_CONCURRENT_MT5_SESSIONS", str(configured_count))))
        if configured_count > maximum:
            raise RuntimeError("MT5_WORKER_COUNT exceeds MAX_CONCURRENT_MT5_SESSIONS")
        machine_id = (os.environ.get("WORKER_ID") or os.environ.get("MT5_INSTANCE_ID") or socket.gethostname())[:100]
        self.stop_event = threading.Event()
        self.workers = []
        for index in range(configured_count):
            worker_id = f"{machine_id}-{index + 1}"
            client = WorkerBackendClient(backend_url, secret, worker_id, machine_id)
            self.workers.append(MT5Worker(worker_id, paths[index], client))

    def serve_forever(self):
        _log("worker_pool_started", worker_count=len(self.workers))
        with ThreadPoolExecutor(max_workers=len(self.workers)) as executor:
            futures = [executor.submit(worker.serve, self.stop_event) for worker in self.workers]
            try:
                for future in futures:
                    future.result()
            except KeyboardInterrupt:
                self.stop_event.set()


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    MT5WorkerPool().serve_forever()


if __name__ == "__main__":
    main()
