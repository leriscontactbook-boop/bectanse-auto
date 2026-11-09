"""Small, no-retry HTTP client for the dedicated MT5 copy workers."""

from __future__ import annotations

import json
import secrets
import time

import requests

from .security import canonical_copy_signature


class CopyWorkerAPIError(RuntimeError):
    def __init__(self, code: str, status: int = 0):
        super().__init__(code)
        self.code = code
        self.status = status


class CopyTradingClient:
    def __init__(self, base_url: str, worker_id: str, role: str, secret: str,
                 *, timeout=(4, 8), session=None):
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.role = role.upper()
        self.secret = secret
        self.timeout = timeout
        self.session = session or requests

    def post(self, path: str, payload: dict) -> dict:
        if not path.startswith("/internal/copy-trading/") or ".." in path:
            raise CopyWorkerAPIError("INVALID_INTERNAL_PATH")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        signature = canonical_copy_signature(
            self.secret, self.worker_id, self.role, "POST", path, timestamp, body, nonce,
        )
        headers = {
            "Content-Type": "application/json",
            "X-COPY-Worker-ID": self.worker_id,
            "X-COPY-Worker-Role": self.role,
            "X-COPY-Timestamp": timestamp,
            "X-COPY-Nonce": nonce,
            "X-COPY-Signature": signature,
        }
        try:
            response = self.session.post(
                self.base_url + path, data=body, headers=headers,
                timeout=self.timeout, verify=True,
            )
        except requests.RequestException as error:
            raise CopyWorkerAPIError("BACKEND_UNREACHABLE") from error
        try:
            data = response.json()
        except ValueError as error:
            raise CopyWorkerAPIError("BACKEND_INVALID_RESPONSE", response.status_code) from error
        if not response.ok or not isinstance(data, dict) or data.get("ok") is not True:
            raise CopyWorkerAPIError(str((data or {}).get("code") or "BACKEND_REJECTED"), response.status_code)
        return data
