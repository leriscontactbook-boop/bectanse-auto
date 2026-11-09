"""Fail-closed settings for the dedicated Windows copy-trading worker."""

from __future__ import annotations

import os
import ntpath
from urllib.parse import urlparse


class CopyTradingConfigurationError(RuntimeError):
    pass


def _truthy(name: str, default: str = "false") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def validate_worker_config() -> dict:
    backend_url = (os.environ.get("COPY_TRADING_BACKEND_URL") or os.environ.get("BACKEND_URL") or "").strip().rstrip("/")
    role_secrets = {
        role: (os.environ.get(f"COPY_TRADING_{role}_WORKER_SECRET") or "").strip()
        for role in ("MASTER", "FOLLOWER")
    }
    master_code = (os.environ.get("COPY_TRADING_MASTER_MEMBER_CODE") or "").strip().upper()
    worker_node_id = (os.environ.get("COPY_TRADING_WORKER_ID") or os.environ.get("COMPUTERNAME") or "copy-node")
    worker_node_id = worker_node_id.strip()[:48]
    master_path = (os.environ.get("MT5_COPY_MASTER_TERMINAL_PATH") or "").strip()
    follower_paths = [
        value.strip() for value in (os.environ.get("MT5_COPY_TERMINAL_PATHS") or "").split(";")
        if value.strip()
    ]
    mode = (os.environ.get("COPY_TRADING_MODE") or "simulation").strip().lower()
    execution_enabled = _truthy("COPY_TRADING_EXECUTION_ENABLED")
    live_release_approved = _truthy("COPY_TRADING_LIVE_RELEASE_APPROVED")
    if mode not in {"simulation", "demo", "live"}:
        raise CopyTradingConfigurationError("COPY_TRADING_MODE must be simulation, demo, or live")
    if not backend_url:
        raise CopyTradingConfigurationError("COPY_TRADING_BACKEND_URL or BACKEND_URL is required")
    parsed = urlparse(backend_url)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise CopyTradingConfigurationError("Copy-trading worker requires HTTPS")
    if any(len(secret) < 32 for secret in role_secrets.values()):
        raise CopyTradingConfigurationError("Separate master and follower worker secrets of at least 32 characters are required")
    if role_secrets["MASTER"] == role_secrets["FOLLOWER"]:
        raise CopyTradingConfigurationError("Master and follower worker secrets must be distinct")
    if not master_code:
        raise CopyTradingConfigurationError("COPY_TRADING_MASTER_MEMBER_CODE is required")
    if not worker_node_id or any(char in worker_node_id for char in "\\/\r\n"):
        raise CopyTradingConfigurationError("COPY_TRADING_WORKER_ID is invalid")
    if not master_path:
        raise CopyTradingConfigurationError("MT5_COPY_MASTER_TERMINAL_PATH is required")
    if not follower_paths:
        raise CopyTradingConfigurationError("Configure at least one dedicated follower terminal")
    all_paths = [master_path, *follower_paths]
    normalized_paths = [ntpath.normcase(ntpath.normpath(os.path.realpath(path))) for path in all_paths]
    normalized_dirs = [ntpath.normcase(ntpath.normpath(ntpath.dirname(path))) for path in normalized_paths]
    if len(set(normalized_paths)) != len(all_paths) or len(set(normalized_dirs)) != len(all_paths):
        raise CopyTradingConfigurationError(
            "Master and follower processes must use distinct MT5 terminal installations and portable data paths"
        )
    if os.name == "nt":
        for path in all_paths:
            if (not ntpath.isabs(path) or ntpath.basename(path).casefold() != "terminal64.exe"
                    or not os.path.isfile(path)):
                raise CopyTradingConfigurationError(
                    "Each MT5 terminal path must be an existing absolute terminal64.exe path"
                )
    if execution_enabled and mode == "simulation":
        raise CopyTradingConfigurationError("Simulation mode cannot enable broker order execution")
    if mode == "live" and execution_enabled and not live_release_approved:
        raise CopyTradingConfigurationError("Live execution requires COPY_TRADING_LIVE_RELEASE_APPROVED=true")
    return {
        "backend_url": backend_url,
        "master_secret": role_secrets["MASTER"],
        "follower_secret": role_secrets["FOLLOWER"],
        "calendar_secret": (os.environ.get("COPY_TRADING_CALENDAR_WORKER_SECRET") or "").strip(),
        "master_member_code": master_code,
        "worker_node_id": worker_node_id,
        "master_terminal_path": master_path,
        "follower_terminal_paths": follower_paths,
        "mode": mode,
        "execution_enabled": execution_enabled,
        "live_release_approved": live_release_approved,
        "poll_seconds": max(1, int(os.environ.get("COPY_TRADING_POLL_SECONDS", "2"))),
        "heartbeat_seconds": max(5, int(os.environ.get("COPY_TRADING_HEARTBEAT_SECONDS", "15"))),
        "connect_timeout_ms": max(1000, int(os.environ.get("COPY_TRADING_CONNECT_TIMEOUT_MS", "60000"))),
    }
