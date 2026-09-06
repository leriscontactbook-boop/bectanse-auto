"""Authenticated encryption and machine-to-machine request signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialConfigurationError(RuntimeError):
    """Raised when credential encryption is not configured safely."""


def _decode_master_key(value: str) -> bytes:
    value = (value or "").strip()
    if not value:
        raise CredentialConfigurationError("MT5_CREDENTIAL_MASTER_KEY is missing")
    candidates = []
    try:
        candidates.append(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(value))
    except ValueError:
        pass
    for candidate in candidates:
        if len(candidate) == 32:
            return candidate
    raise CredentialConfigurationError(
        "MT5_CREDENTIAL_MASTER_KEY must encode exactly 32 random bytes"
    )


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    tag: bytes


class CredentialCipher:
    """AES-256-GCM with the account identity bound as authenticated data."""

    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise CredentialConfigurationError("AES-256-GCM requires a 32-byte key")
        self._cipher = AESGCM(master_key)

    @classmethod
    def from_environment(cls) -> "CredentialCipher":
        return cls(_decode_master_key(os.environ.get("MT5_CREDENTIAL_MASTER_KEY", "")))

    def encrypt(self, password: str, associated_data: str) -> EncryptedCredential:
        if not password:
            raise ValueError("The credential cannot be empty")
        nonce = os.urandom(12)
        sealed = self._cipher.encrypt(
            nonce, password.encode("utf-8"), associated_data.encode("utf-8")
        )
        return EncryptedCredential(sealed[:-16], nonce, sealed[-16:])

    def decrypt(self, credential: EncryptedCredential, associated_data: str) -> str:
        plaintext = self._cipher.decrypt(
            credential.nonce,
            credential.ciphertext + credential.tag,
            associated_data.encode("utf-8"),
        )
        return plaintext.decode("utf-8")


def canonical_worker_signature(
    secret: str, method: str, path: str, timestamp: str, body: bytes, nonce: str = ""
) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join((method.upper(), path, timestamp, nonce, body_hash)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def verify_worker_request(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: int | None = None,
    tolerance_seconds: int = 90,
    nonce: str = "",
) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((int(time.time()) if now is None else int(now)) - sent_at) > tolerance_seconds:
        return False
    expected = canonical_worker_signature(secret, method, path, timestamp, body, nonce)
    return hmac.compare_digest(expected, signature)
