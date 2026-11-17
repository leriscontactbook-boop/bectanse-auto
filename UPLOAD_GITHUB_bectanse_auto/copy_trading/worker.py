"""Simulation validator: no MetaTrader import, credentials, or order execution.

Run once with python -m copy_trading.worker, or poll with --watch.
"""

import argparse
import json
import os
import time
import uuid
from urllib.parse import urlparse

import requests

from trading_journal.security import canonical_worker_signature
from .service import digest
from .settings import validate


def validation_result(job):
    if digest(job["payload"]) != job["digest"]:
        return "FAILED", "DIGEST_MISMATCH"
    try:
        payload = job["payload"]
        if payload["mode"] != "SIMULATION" or not isinstance(payload["requested_enabled"], bool):
            raise ValueError("Invalid simulation envelope")
        validate(payload["settings"])
    except (KeyError, ValueError, TypeError):
        return "FAILED", "INVALID_CONFIG"
    return "VALIDATED_SIMULATION", ""


class SimulationWorker:
    def __init__(self):
        self.url = os.environ.get("COPY_CONFIG_BACKEND_URL", "").rstrip("/")
        self.secret = os.environ.get("COPY_CONFIG_WORKER_SECRET", "")
        self.identity = os.environ.get("COPY_CONFIG_WORKER_ID", "configuration-validator")
        parsed = urlparse(self.url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in {"http", "https"}
                or (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"})
                or len(self.secret) < 32 or not 1 <= len(self.identity) <= 100):
            raise ValueError("Configurez une URL HTTPS et un secret dédié d’au moins 32 caractères.")

    def request(self, path, data):
        body = json.dumps({**data, "worker_id": self.identity}, separators=(",", ":")).encode()
        timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
        response = getattr(self, "transport", requests).post(self.url + path, data=body, headers={
            "Content-Type": "application/json", "X-MT5-Worker-ID": self.identity,
            "X-MT5-Timestamp": timestamp, "X-MT5-Nonce": nonce,
            "X-MT5-Signature": canonical_worker_signature(self.secret, "POST", path, timestamp, body, nonce),
        }, timeout=getattr(self, "timeout", 20), allow_redirects=False)
        response.raise_for_status()
        return response.json()

    def run_once(self):
        job = self.request("/internal/copy/configurations/claim", {}).get("job")
        if not job:
            return False
        status, error = validation_result(job)
        self.request("/internal/copy/configurations/ack", {
            key: job[key] for key in ("member_code", "version", "digest", "lease_token")
        } | {"status": status, "error_code": error})
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    worker = SimulationWorker()
    while True:
        try:
            worker.run_once()
        except requests.RequestException:
            # No response bodies or configuration payloads in logs.
            print("Service de configuration indisponible ; nouvelle tentative au prochain cycle.")
            if not args.watch:
                raise SystemExit(1)
        if not args.watch:
            break
        time.sleep(5)
