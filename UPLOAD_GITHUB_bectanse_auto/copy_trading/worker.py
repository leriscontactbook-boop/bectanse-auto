"""Windows master monitor and isolated MT5 follower-slot supervisor."""

from __future__ import annotations

import hashlib
import logging
import multiprocessing as mp
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

from trading_journal.providers.base import ProviderError

from .config import validate_worker_config
from .domain import execution_release_allowed, worker_claims_enabled
from .providers.mt5 import MetaTrader5ExecutionProvider
from .security import terminal_account_fingerprint
from .worker_client import CopyTradingClient, CopyWorkerAPIError


log = logging.getLogger("copy_trading.worker")


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _fingerprint(path: str, account: dict) -> str:
    material = "\n".join((path.casefold(), str(account.get("server") or ""),
                          str(account.get("terminal_build") or "")))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _client(config, worker_id, role):
    secret = config["master_secret"] if role == "MASTER" else config["follower_secret"]
    return CopyTradingClient(config["backend_url"], worker_id, role, secret)


def _windows_session_id():
    """Return the Windows logon session that owns this MT5 IPC process."""
    try:
        import ctypes

        if os.name != "nt":
            return None
        value = ctypes.c_uint32()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            return None
        return int(value.value)
    except (AttributeError, OSError, ImportError):
        return None


def _heartbeat(client, worker_id, role, *, status, fingerprint="", job_id="",
               terminal_probe=None):
    try:
        payload = {
            "role": role, "instance_id": worker_id, "terminal_fingerprint": fingerprint,
            "status": status, "current_job_id": str(job_id or ""),
        }
        if terminal_probe is not None:
            payload["terminal_probe"] = {
                **terminal_probe,
                "process_id": mp.current_process().pid,
                "session_id": _windows_session_id(),
            }
        client.post("/internal/copy-trading/workers/heartbeat", payload)
        log.info("worker_heartbeat role=%s status=%s", role, status)
    except CopyWorkerAPIError as error:
        log.warning("heartbeat_failed role=%s code=%s", role, error.code)


def run_master_monitor(config=None):
    """Poll the configured master terminal; this process never sends orders."""
    config = config or validate_worker_config()
    worker_id = f"{config['worker_node_id']}-copy-master"[:100]
    client = _client(config, worker_id, "MASTER")
    provider = MetaTrader5ExecutionProvider(
        config["master_terminal_path"], timeout_ms=config["connect_timeout_ms"],
    )
    credentials = None
    connected = False
    failures = 0
    last_credentials_refresh = 0.0
    while True:
        try:
            if not config["execution_enabled"] or config["mode"] == "simulation":
                _heartbeat(client, worker_id, "MASTER", status="BLOCKED_RELEASE")
                time.sleep(max(config["heartbeat_seconds"], 15))
                continue
            if credentials is None or time.monotonic() - last_credentials_refresh > 1800:
                data = client.post("/internal/copy-trading/master/credentials", {})
                credentials = {key: data[key] for key in ("login", "server", "password", "member_code")}
                last_credentials_refresh = time.monotonic()
                connected = False
            if not connected:
                account = provider.connect(credentials["login"], credentials["server"], credentials["password"])
                connected = True
            else:
                account = provider.account_info()
            terminal_probe = {
                "member_code": config["master_member_code"],
                "account_fingerprint": terminal_account_fingerprint(
                    client.secret, account["login"], account["server"]),
                "server": account["server"], "terminal_path_verified": account["terminal_path_verified"],
                "state": "CONNECTED", "diagnostic_code": None,
            }
            positions = provider.positions()
            close_candidates = client.post("/internal/copy-trading/master/close-candidates", {})
            close_deals = provider.closing_deals(close_candidates.get("position_ids") or [])
            symbols = {}
            for symbol in sorted({str(row["symbol"]) for row in positions}):
                symbols[symbol] = provider.symbol_evidence(symbol)
            observed = _iso(_utc_now())
            client.post("/internal/copy-trading/master/snapshot", {
                "account": account,
                "positions": positions,
                "close_deals": close_deals,
                "native_symbols": symbols,
                "observed_at": observed,
            })
            _heartbeat(client, worker_id, "MASTER", status="ONLINE",
                       fingerprint=_fingerprint(config["master_terminal_path"], account),
                       terminal_probe=terminal_probe)
            failures = 0
            time.sleep(config["poll_seconds"])
        except ProviderError as error:
            log.warning("master_terminal_error code=%s diagnostic=%s", error.code, error.diagnostic_code)
            terminal_probe = None
            if error.code in {"TERMINAL_CONNECTION_FAILED", "TERMINAL_IDENTITY_MISMATCH",
                              "TERMINAL_PROCESS_PATH_MISMATCH"} and credentials:
                terminal_probe = {
                    "member_code": config["master_member_code"],
                    "account_fingerprint": terminal_account_fingerprint(
                        client.secret, credentials["login"], credentials["server"]),
                    "server": credentials["server"], "terminal_path_verified": False,
                    "state": "FAILED", "failure_code": error.code,
                    "diagnostic_code": error.diagnostic_code,
                }
            connected = False
            credentials = None
            failures += 1
            provider.disconnect()
            _heartbeat(client, worker_id, "MASTER", status="DEGRADED",
                       terminal_probe=terminal_probe)
            time.sleep(min(30, 2 ** min(failures, 4)))
        except CopyWorkerAPIError as error:
            log.warning("master_backend_error code=%s", error.code)
            failures += 1
            if error.code == "BACKEND_REJECTED":
                credentials = None
                connected = False
                provider.disconnect()
            _heartbeat(client, worker_id, "MASTER", status="DEGRADED")
            time.sleep(min(30, 2 ** min(failures, 4)))
        except Exception as error:
            # Do not include exception text: provider/network exceptions can
            # accidentally contain account endpoints or secret-bearing data.
            log.error("master_unexpected_error type=%s", type(error).__name__)
            connected = False
            credentials = None
            provider.disconnect()
            failures += 1
            _heartbeat(client, worker_id, "MASTER", status="DEGRADED")
            time.sleep(min(30, 2 ** min(failures, 4)))


class _LeaseRenewer(threading.Thread):
    def __init__(self, client, job_id, token):
        super().__init__(daemon=True)
        self.client = client
        self.job_id = job_id
        self.token = token
        self.stop_event = threading.Event()
        self.lost = threading.Event()

    def run(self):
        while not self.stop_event.wait(7):
            try:
                self.client.post(f"/internal/copy-trading/jobs/{self.job_id}/heartbeat",
                                 {"lease_token": self.token})
            except CopyWorkerAPIError:
                self.lost.set()
                return


def _send_phase(pipe, name):
    try:
        pipe.send(name)
    except (BrokenPipeError, EOFError, OSError):
        pass


def _post_complete(client, job_id, result):
    path = f"/internal/copy-trading/jobs/{job_id}/complete"
    try:
        return client.post(path, {"result": result})
    except CopyWorkerAPIError as first:
        # Repeating a result submission is safe: it never repeats order_send.
        # The backend commits by job status before acknowledging.
        try:
            return client.post(path, {"result": result})
        except CopyWorkerAPIError as second:
            raise second from first


def _native_match(rows, ticket):
    ticket = str(ticket or "")
    return [row for row in rows if ticket and
            (str(row.get("ticket") or "") == ticket or str(row.get("position_id") or "") == ticket)]


def _process_claimed_job(config, worker_id, job, pipe):
    """Run one claimed job in a disposable process so hung IPC can be killed."""
    client = _client(config, worker_id, "FOLLOWER")
    provider = MetaTrader5ExecutionProvider(
        config["follower_terminal_path"], timeout_ms=config["connect_timeout_ms"],
    )
    job_id = str(job["job_id"])
    token = str(job["lease_token"])
    dispatch_started = False
    terminal_connected = False
    account = None
    try:
        account = provider.connect(job["follower_login"], job["follower_server"], job["follower_password"])
        terminal_connected = True
        _heartbeat(client, worker_id, "FOLLOWER", status="ONLINE",
                   fingerprint=_fingerprint(config["follower_terminal_path"], account),
                   job_id=job_id, terminal_probe={
                       "member_code": job["follower_member_code"],
                       "account_fingerprint": terminal_account_fingerprint(
                           client.secret, account["login"], account["server"]),
                       "server": account["server"],
                       "terminal_path_verified": account["terminal_path_verified"],
                       "state": "CONNECTED", "diagnostic_code": None,
                   })
        if not execution_release_allowed(
            mode=config["mode"], execution_enabled=config["execution_enabled"],
            live_release_approved=config["live_release_approved"], account_is_demo=account["is_demo"],
        ):
            client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                "lease_token": token, "code": "OPERATIONAL_RELEASE_REQUIRED", "retryable": False,
            })
            return

        symbol = str(job.get("mapped_symbol") or job["symbol"])
        positions = provider.positions()
        market = {**account, "positions": positions, "positions_complete": True}
        try:
            follower_evidence = provider.symbol_evidence(symbol)
        except ProviderError as error:
            if error.code != "SYMBOL_NOT_FOUND":
                raise
            candidates = provider.compatible_symbol_candidates(job.get("master_evidence") or {})
            if candidates:
                client.post(f"/internal/copy-trading/jobs/{job_id}/mapping-candidates", {
                    "lease_token": token, "candidates": candidates,
                })
            client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                "lease_token": token, "code": "MAPPING_REVIEW_REQUIRED", "retryable": True,
            })
            return

        academy = job.get("academy_settings") or {}
        effective_side = str(job["side"])
        if academy.get("inverser_trades") in (True, 1, "true", "True", "1", "on", "yes"):
            effective_side = "SELL" if effective_side == "BUY" else "BUY"
        risk_stop = Decimal(str(job.get("stop_loss") or 0))
        if effective_side != str(job["side"]):
            risk_stop = Decimal(str(job.get("take_profit") or 0))
        loss_per_lot = provider.risk_loss_per_lot(symbol, effective_side, risk_stop)
        if loss_per_lot is not None:
            market["loss_per_lot"] = str(loss_per_lot)
        request_comment = "BCT-" + job_id.replace("-", "")[:24]
        prepared_data = client.post(f"/internal/copy-trading/jobs/{job_id}/prepared", {
            "lease_token": token, "symbol": symbol,
            "follower_evidence": follower_evidence, "market": market,
            "request_comment": request_comment,
        })
        if prepared_data.get("status") != "PREPARED":
            return
        action = str(prepared_data.get("execution_action") or "")
        if action == "NOOP":
            return
        if action not in {"OPEN", "CLOSE", "RESIZE_CLOSE", "PROTECT"}:
            client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                "lease_token": token, "code": "UNSUPPORTED_EXECUTION_ACTION", "retryable": False,
            })
            return
        native_plan = (prepared_data.get("job") or {}).get("native_evidence") or {}
        plan = native_plan.get("plan") or {}
        volume = Decimal(str((prepared_data.get("job") or {}).get("normalized_volume") or 0))
        deviation = int(academy.get("slippage", 100) or 0)
        before_position = None

        if action == "OPEN":
            request = provider.open_request(
                symbol=symbol, side=str(plan.get("side") or effective_side), volume=volume,
                stop_loss=Decimal(str(plan.get("stop_loss") or 0)),
                take_profit=Decimal(str(plan.get("take_profit") or 0)),
                comment=request_comment, deviation=deviation,
            )
        else:
            ticket = str(plan.get("position_ticket") or "")
            matches = _native_match(positions, ticket)
            if len(matches) != 1:
                client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                    "lease_token": token, "code": "NATIVE_POSITION_NOT_UNIQUE", "retryable": True,
                })
                return
            before_position = matches[0]
            if action in {"CLOSE", "RESIZE_CLOSE"}:
                request = provider.close_request(
                    position=before_position, volume=volume,
                    comment=request_comment, deviation=deviation,
                )
            else:
                request = provider.protection_request(
                    position=before_position,
                    stop_loss=Decimal(str(plan.get("stop_loss") or 0)),
                    take_profit=Decimal(str(plan.get("take_profit") or 0)),
                )

        provider.check_request(request)
        expiry = datetime.fromisoformat(str(job["expires_at"]).replace("Z", "+00:00"))
        if (_utc_now() >= expiry or (expiry - _utc_now()).total_seconds() < 3):
            client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                "lease_token": token, "code": "ABSOLUTE_TTL_EXPIRED", "retryable": False,
            })
            return
        handoff_data = client.post(f"/internal/copy-trading/jobs/{job_id}/dispatch", {"lease_token": token})
        handoff = handoff_data.get("dispatch") or {}
        committed_evidence = handoff.get("native_evidence") or {}
        committed_plan = committed_evidence.get("plan") or {}
        if (str(handoff.get("execution_action") or "") != action
                or str(handoff.get("follower_symbol") or "") != symbol
                or Decimal(str(handoff.get("normalized_volume") or 0)) != volume
                or str(handoff.get("request_comment") or "") != request_comment
                or committed_plan != plan):
            dispatch_started = True
            _send_phase(pipe, "DISPATCHING")
            _post_complete(client, job_id, {
                "confirmed": False, "ambiguous": True,
                "failure_code": "ATOMIC_HANDOFF_PLAN_MISMATCH",
            })
            return
        dispatch_started = True
        _send_phase(pipe, "DISPATCHING")
        result = provider.submit_request(request)
        if result.get("confirmed") is True:
            after_account = provider.account_info()
            after_positions = provider.positions()
            result["verified"] = False
            result["volume"] = str(result.get("volume") or volume)
            target_id = str(result.get("position_id") or "") if action == "OPEN" else str(before_position["ticket"])
            post_matches = _native_match(after_positions, target_id)
            result["native_postcondition"] = {
                "terminal_connected": after_account.get("terminal_connected") is True,
                "terminal_path_verified": after_account.get("terminal_path_verified") is True,
                "login": str(after_account.get("login") or ""),
                "server": str(after_account.get("server") or ""),
                "positions_complete": True,
                "matches": [{
                    "ticket": str(row.get("ticket") or ""),
                    "position_id": str(row.get("position_id") or ""),
                    "symbol": str(row.get("symbol") or ""),
                    "side": str(row.get("side") or ""),
                    "volume": str(row.get("volume") or "0"),
                    "stop_loss": str(row.get("stop_loss") or "0"),
                    "take_profit": str(row.get("take_profit") or "0"),
                    "magic": int(row.get("magic") or 0),
                    "comment": str(row.get("comment") or "")[:31],
                } for row in post_matches],
            }
            if action == "OPEN":
                matches = _native_match(after_positions, result.get("position_id"))
                if len(matches) == 1:
                    native = matches[0]
                    result["verified"] = (
                        str(native["symbol"]) == symbol
                        and str(native["side"]) == str(plan.get("side") or effective_side)
                        and Decimal(str(native["volume"])) == volume
                    )
            elif action in {"CLOSE", "RESIZE_CLOSE"}:
                remaining = _native_match(after_positions, before_position["ticket"])
                expected_remaining = Decimal("0") if action == "CLOSE" else (
                    Decimal(str(before_position["volume"])) - volume
                )
                if not remaining:
                    result["verified"] = expected_remaining == 0
                elif len(remaining) == 1:
                    result["verified"] = Decimal(str(remaining[0]["volume"])) == expected_remaining
                if result["verified"]:
                    result["volume"] = str(volume)
            elif action == "PROTECT":
                matches = _native_match(after_positions, before_position["ticket"])
                if len(matches) == 1:
                    result["verified"] = (
                        Decimal(str(matches[0]["stop_loss"])) == Decimal(str(plan.get("stop_loss") or 0))
                        and Decimal(str(matches[0]["take_profit"])) == Decimal(str(plan.get("take_profit") or 0))
                    )
            if not result["verified"]:
                result.update({"confirmed": False, "ambiguous": True,
                               "failure_code": "NATIVE_POSTCONDITION_UNVERIFIED"})
        _post_complete(client, job_id, result)
        _send_phase(pipe, "RESULT_RECORDED")
    except ProviderError as error:
        log.warning("follower_terminal_error worker=%s code=%s diagnostic=%s",
                    worker_id, error.code, error.diagnostic_code)
        if error.code in {"TERMINAL_CONNECTION_FAILED", "TERMINAL_IDENTITY_MISMATCH",
                          "TERMINAL_PROCESS_PATH_MISMATCH"}:
            _heartbeat(client, worker_id, "FOLLOWER", status="DEGRADED", job_id=job_id,
                       terminal_probe={
                           "member_code": job["follower_member_code"],
                           "account_fingerprint": terminal_account_fingerprint(
                               client.secret, job["follower_login"], job["follower_server"]),
                           "server": job["follower_server"], "terminal_path_verified": False,
                           "state": "FAILED", "failure_code": error.code,
                           "diagnostic_code": error.diagnostic_code,
                       })
        elif terminal_connected:
            _heartbeat(client, worker_id, "FOLLOWER", status="DEGRADED", job_id=job_id,
                       terminal_probe={
                           "member_code": job["follower_member_code"],
                           "account_fingerprint": terminal_account_fingerprint(
                               client.secret, job["follower_login"], job["follower_server"]),
                           "server": job["follower_server"],
                           "terminal_path_verified": bool(account and account.get("terminal_path_verified")),
                           "state": "CONNECTED", "failure_code": "", "diagnostic_code": None,
                       })
        if not dispatch_started:
            try:
                client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                    "lease_token": token, "code": error.code,
                    "retryable": error.retryable,
                })
            except CopyWorkerAPIError:
                pass
        else:
            try:
                _post_complete(client, job_id, {
                    "confirmed": False, "ambiguous": True,
                    "failure_code": error.code, "diagnostic_code": error.diagnostic_code,
                })
            except CopyWorkerAPIError:
                pass
    except CopyWorkerAPIError as error:
        log.warning("follower_backend_error worker=%s code=%s", worker_id, error.code)
        if not dispatch_started:
            try:
                client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                    "lease_token": token, "code": error.code, "retryable": True,
                })
            except CopyWorkerAPIError:
                pass
    except Exception as error:
        log.error("follower_unexpected_error worker=%s type=%s", worker_id, type(error).__name__)
        if not dispatch_started:
            try:
                client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                    "lease_token": token, "code": "WORKER_UNEXPECTED_PREFLIGHT_ERROR", "retryable": True,
                })
            except CopyWorkerAPIError:
                pass
        else:
            try:
                _post_complete(client, job_id, {
                    "confirmed": False, "ambiguous": True,
                    "failure_code": "WORKER_UNEXPECTED_DURING_DISPATCH",
                })
            except CopyWorkerAPIError:
                pass
    finally:
        provider.disconnect()
        try:
            pipe.close()
        except OSError:
            pass


def _follower_slot(config, slot_index, terminal_path):
    worker_id = f"{config['worker_node_id']}-copy-follower-{slot_index + 1:02d}"[:100]
    client = _client(config, worker_id, "FOLLOWER")
    while True:
        if not worker_claims_enabled(
            mode=config["mode"], execution_enabled=config["execution_enabled"],
            live_release_approved=config["live_release_approved"],
        ):
            _heartbeat(client, worker_id, "FOLLOWER", status="BLOCKED_RELEASE")
            time.sleep(max(config["heartbeat_seconds"], 15))
            continue
        try:
            _heartbeat(client, worker_id, "FOLLOWER", status="ONLINE")
            claimed = client.post("/internal/copy-trading/jobs/claim", {})
            job = claimed.get("job")
            if not job:
                time.sleep(config["poll_seconds"])
                continue
            job_id = str(job["job_id"])
            token = str(job["lease_token"])
            job_config = dict(config, follower_terminal_path=terminal_path)
            recv_pipe, send_pipe = mp.get_context("spawn").Pipe(duplex=False)
            process = mp.get_context("spawn").Process(
                target=_process_claimed_job,
                args=(job_config, worker_id, job, send_pipe),
                daemon=True,
            )
            process.start()
            send_pipe.close()
            expiry = datetime.fromisoformat(str(job["expires_at"]).replace("Z", "+00:00"))
            hard_deadline = min(expiry.timestamp() - 2, time.time() + 118)
            dispatching = False
            renewer = _LeaseRenewer(client, job_id, token)
            renewer.start()
            while process.is_alive() and time.time() < hard_deadline:
                if recv_pipe.poll(0.2):
                    try:
                        phase = recv_pipe.recv()
                        dispatching = dispatching or phase == "DISPATCHING"
                    except (EOFError, OSError):
                        pass
                process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
                log.error("follower_job_deadline worker=%s job=%s dispatching=%s", worker_id, job_id, dispatching)
                if not dispatching:
                    try:
                        client.post(f"/internal/copy-trading/jobs/{job_id}/fail", {
                            "lease_token": token, "code": "WORKER_HARD_DEADLINE", "retryable": False,
                        })
                    except CopyWorkerAPIError:
                        pass
            renewer.stop_event.set()
            renewer.join(timeout=1)
            recv_pipe.close()
            if process.exitcode not in (0, None):
                log.warning("follower_job_process_exit worker=%s job=%s code=%s", worker_id, job_id, process.exitcode)
            time.sleep(0.1)
        except CopyWorkerAPIError as error:
            log.warning("follower_backend_error worker=%s code=%s", worker_id, error.code)
            time.sleep(min(30, config["poll_seconds"] * 2))
        except Exception as error:
            log.error("follower_slot_error worker=%s type=%s", worker_id, type(error).__name__)
            time.sleep(5)


def run_follower_workers(config=None):
    """Supervise independent follower slots, each with a unique terminal path."""
    config = config or validate_worker_config()
    context = mp.get_context("spawn")
    processes = []
    for index, terminal_path in enumerate(config["follower_terminal_paths"]):
        process = context.Process(target=_follower_slot, args=(config, index, terminal_path),
                                  name=f"copy-follower-slot-{index + 1}")
        process.start()
        processes.append(process)
    while True:
        for index, process in enumerate(processes):
            if not process.is_alive():
                log.error("follower_slot_restarting slot=%s exit_code=%s", index + 1, process.exitcode)
                process.join(timeout=1)
                replacement = context.Process(
                    target=_follower_slot,
                    args=(config, index, config["follower_terminal_paths"][index]),
                    name=f"copy-follower-slot-{index + 1}",
                )
                replacement.start()
                processes[index] = replacement
        time.sleep(2)
