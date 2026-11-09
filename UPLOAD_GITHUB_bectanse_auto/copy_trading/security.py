"""Signed worker requests with a replay-proof nonce namespace of their own."""

from __future__ import annotations

import hashlib
import hmac
import time


def canonical_copy_signature(secret: str, worker_id: str, role: str, method: str,
                             path: str, timestamp: str, body: bytes, nonce: str) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join((worker_id, role.upper(), method.upper(), path,
                           timestamp, nonce, body_hash)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def verify_copy_worker_request(secret: str, method: str, path: str, timestamp: str,
                               body: bytes, signature: str, nonce: str,
                               worker_id: str, role: str,
                               *, now: int | None = None) -> bool:
    if not secret or not nonce or len(nonce) > 128 or not worker_id or role.upper() not in {
        "MASTER", "FOLLOWER", "CALENDAR"
    }:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((int(time.time()) if now is None else int(now)) - sent_at) > 30:
        return False
    expected = canonical_copy_signature(secret, worker_id, role, method, path,
                                         timestamp, body, nonce)
    return hmac.compare_digest(expected, signature)


def lease_token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()
