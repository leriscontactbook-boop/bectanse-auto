"""Windows demo runner: one observer and executor process, isolated MT5 terminals."""
import argparse
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import tempfile
import sys
import time
import random
import requests
from urllib.parse import urlparse

from .demo_engine import DemoEngine, Observer
from .demo_mt5 import DemoMT5
from .demo_policy import SafetyError, decimal
from .demo_store import Ledger
from .symbols import MAX_SYMBOLS
from .atomic_io import write_json
from .worker import SimulationWorker
from .runtime_io import RuntimeTelemetry, LatestStatusReporter, MeasuredMT5, observed_ledger

DEFAULT_LIMITS = {"max_lots_per_trade": 0.1, "max_total_lots": 0.5, "max_positions": 5,
                  "max_spread_points": 100, "entry_max_age": 5, "snapshot_max_age": 3,
                  "allow_broker_minimum": False, "exact_member_volume": False, "enforce_spread_limit": True,
                  "require_stop_loss": True, "enforce_exposure_limits": True, "enforce_master_price_limit": True}


def account_identity(account):
    return str(account["login"]) + "@" + account["server"]


def read_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("mode") != "DEMO_ONLY":
        raise ValueError("Manifest mode must be DEMO_ONLY")
    for role in ("master", "follower"):
        account = data[role]
        if (set(account) != {"login", "server", "terminal"}
                or not re.fullmatch(r"[0-9]{3,20}", str(account["login"]))
                or not isinstance(account["server"], str) or not account["server"]
                or not (ntpath.isabs(account["terminal"]) or
                        (os.name != 'nt' and Path(account["terminal"]).is_absolute()))):
            raise ValueError("Use exact demo login, server and absolute terminal path; no passwords")
    folders = [ntpath.normcase(ntpath.dirname(data[role]["terminal"])) for role in ("master", "follower")]
    journal = [ntpath.normcase(ntpath.dirname(p)) for p in
               (os.environ.get("MT5_TERMINAL_PATHS") or os.environ.get("MT5_TERMINAL_PATH") or "").split(";") if p]
    if folders[0] == folders[1] or set(folders) & set(journal) or account_identity(data["master"]) == account_identity(data["follower"]):
        raise ValueError("Separate master, follower and journal installations required")
    if type(data["account_id"]) is not int or data["account_id"] <= 0:
        raise ValueError("account_id must identify the bound journal account")
    if not isinstance(data["symbols"], dict) or not data["symbols"] or len(data["symbols"]) > MAX_SYMBOLS:
        raise ValueError("Explicit symbol mapping required")
    if any(not isinstance(s, str) or not s or len(s) > 64 for pair in data["symbols"].items() for s in pair):
        raise ValueError("Invalid symbol mapping")
    offsets = data.get("broker_time_offsets", {"master": 0, "follower": 0})
    if (not isinstance(offsets, dict) or set(offsets) != {"master", "follower"}
            or any(type(v) is not int or abs(v) > 14 * 3600 or v % 900 for v in offsets.values())):
        raise ValueError("Invalid explicit broker time offsets")
    data["broker_time_offsets"] = offsets
    limits = {**DEFAULT_LIMITS, **data.get("limits", {})}
    for key, ceiling in {"max_lots_per_trade": 1, "max_total_lots": 5, "max_positions": 20,
                         "max_spread_points": 1000, "entry_max_age": 10, "snapshot_max_age": 5}.items():
        if type(limits[key]) not in (int, float) or not 0 < decimal(limits[key]) <= ceiling:
            raise ValueError("Invalid pilot limit: " + key)
    if type(limits["max_positions"]) is not int:
        raise ValueError("max_positions must be an integer")
    for key in ("exact_member_volume", "allow_broker_minimum", "enforce_spread_limit", "require_stop_loss", "enforce_exposure_limits", "enforce_master_price_limit"):
        if type(limits[key]) is not bool:
            raise ValueError("Invalid pilot flag: " + key)
    data["limits"] = limits
    return data


class ProcessLocks:
    """OS locks released on process death; run all pilot processes under one OS user."""
    def __init__(self, account):
        root = Path(tempfile.gettempdir()) / "bectanse-copy-locks"
        root.mkdir(mode=0o700, exist_ok=True)
        self.files = []
        try:
            for identity in ("account:" + account_identity(account),
                             "terminal:" + ntpath.normcase(ntpath.dirname(account["terminal"]))):
                path = root / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
                handle = path.open("a+b")
                self.files.append(handle)
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    if path.stat().st_size == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.close()
            raise SafetyError("ACCOUNT_OR_TERMINAL_ALREADY_OWNED") from None

    def close(self):
        for handle in self.files:
            handle.close()
        self.files = []


class DemoBackend(SimulationWorker):
    def __init__(self, *, url=None, secret=None, identity=None, instance=None):
        self.timeout = 2
        self.url = (os.environ.get("COPY_CONFIG_BACKEND_URL", "") if url is None else url).rstrip("/")
        self.secret = os.environ.get("COPY_DEMO_WORKER_SECRET", "") if secret is None else secret
        self.identity = os.environ.get("COPY_DEMO_WORKER_ID", "") if identity is None else identity
        self.instance = os.environ.get("COPY_DEMO_INSTANCE_ID", "") if instance is None else instance
        parsed = urlparse(self.url)
        if (len(self.secret) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.identity)
                or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or parsed.scheme not in {"http", "https"}
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})):
            raise ValueError("Dedicated pilot credentials and HTTPS backend required")

        self.transport = requests.Session()

    def close(self):
        self.transport.close()

    def request(self, path, data):
        return super().request(path, {"instance_id": self.instance, **data})

    def configuration(self, manifest):
        data = self.request("/internal/copy/demo/configuration", {"plan_id": manifest.get("plan_id", "")})
        now = time.time()
        config = data["configuration"]
        if (data.get("mode") != "DEMO_ONLY" or not -1 <= now - data["issued_at"] <= 5
                or str(config["login"]) != str(manifest["follower"]["login"])
                or config["server"] != manifest["follower"]["server"]
                or config["account_id"] != manifest["account_id"]):
            raise SafetyError("BACKEND_ACCOUNT_MISMATCH")
        from .access import configuration_deadline
        return {**config, "valid_until": configuration_deadline(config, data["issued_at"], now)}


class ReconnectBackoff:
    def __init__(self, clock=time.monotonic, jitter=lambda: 1):
        self.clock, self.attempts, self.next_at = clock, 0, 0
        self.jitter = jitter

    def due(self):
        return self.clock() >= self.next_at

    def failed(self):
        self.attempts += 1
        self.next_at = self.clock() + min(60, 10 * 2 ** min(self.attempts - 1, 3) * self.jitter())

    def succeeded(self):
        self.attempts, self.next_at = 0, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("check", "observe", "execute"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--state", required=True, help="Same absolute local SQLite path for both processes")
    parser.add_argument("--credentials-stdin", action="store_true")
    parser.add_argument("--health-file")
    parser.add_argument("--parent-pid",type=int)
    parser.add_argument("--parent-start",type=float)
    parser.add_argument("--fleet-state", help="Optional isolated multi-follower dispatch journal")
    parser.add_argument("--follower-id")
    args = parser.parse_args()
    telemetry = reporter = None
    def health(state, error=""):
        if args.health_file:
            write_json(args.health_file, {"at":time.time(),"pid":os.getpid(),"state":state,"error":error,
                "telemetry_dropped": telemetry.dropped if telemetry else 0,
                "telemetry_write_errors": telemetry.write_errors if telemetry else 0,
                "status_failures": reporter.failures if reporter else 0,
                "status_replaced": reporter.replaced if reporter else 0})
    health("STARTING")
    password=None
    if args.credentials_stdin:
        credentials=json.loads(sys.stdin.read(4097))
        if set(credentials)!={"password"} or not isinstance(credentials["password"],str) or not 1<=len(credentials["password"])<=256:
            raise SystemExit("INVALID_CREDENTIAL_INPUT")
        password=credentials.pop("password")
    manifest = read_manifest(args.manifest)
    if args.role == "check":
        print("Manifest valid. MT5 accounts and order execution are not verified.")
        return
    if os.name != "nt" or os.environ.get("COPY_DEMO_EXECUTION", "") != "I_CONFIRM_DEMO_ONLY":
        raise SystemExit("Windows and COPY_DEMO_EXECUTION=I_CONFIRM_DEMO_ONLY required")
    state = Path(args.state)
    if not state.is_absolute() or str(state).startswith("\\\\") or not state.parent.is_dir():
        raise SystemExit("Use an existing absolute local state directory; network shares forbidden")
    account = manifest["master" if args.role == "observe" else "follower"]
    locks = ProcessLocks(account)
    ledger = broker = telemetry = reporter = backend = None
    try:
        import MetaTrader5 as mt5
        if args.fleet_state:
            telemetry = RuntimeTelemetry(state.parent / "latency.jsonl")
            mt5 = MeasuredMT5(mt5, telemetry)
        if args.fleet_state:
            from .fleet_orders import FleetOrderLedger
            ledger = observed_ledger(FleetOrderLedger, telemetry, state)
        else:
            ledger = Ledger(state)
        source, owner = account_identity(manifest["master"]), account_identity(manifest["follower"])
        ledger.pin("master_identity", manifest["master"])
        ledger.pin("broker_time_offsets", manifest["broker_time_offsets"])
        broker = DemoMT5(mt5, login=account["login"], server=account["server"], terminal_path=account["terminal"],
                         writable=args.role == "execute", require_stop_loss=manifest["limits"]["require_stop_loss"],
                         enforce_master_price_limit=manifest["limits"]["enforce_master_price_limit"], enforce_spread_limit=manifest["limits"]["enforce_spread_limit"], max_spread_points=manifest["limits"]["max_spread_points"],password=password,
                         broker_time_offset_seconds=manifest["broker_time_offsets"]["master" if args.role == "observe" else "follower"])
        broker.connect()
        observer = Observer(ledger, broker, source)
        engine = DemoEngine(ledger, broker, source=source, owner=owner,
                            symbols=manifest["symbols"], limits=manifest["limits"])
        if args.fleet_state:
            if args.role != 'execute' or not args.follower_id:
                raise ValueError('FLEET_EXECUTOR_REQUIRED')
            from .fleet_engine import FleetEngine, validate_policy
            validate_policy(manifest)
            engine = FleetEngine(engine, args.fleet_state, args.follower_id)
        backend = DemoBackend() if args.role == "execute" else None
        if backend is not None:
            from .admission_engine import runtime_gate
            broker.admission = runtime_gate(manifest, backend, os.environ)
        if args.fleet_state:
            reporter = LatestStatusReporter(DemoBackend, telemetry)
        last_report = 0
        reconnect = ReconnectBackoff(jitter=lambda: random.uniform(.8, 1.2))
        while True:
            if args.parent_pid:
                import psutil
                try:
                    parent_alive=psutil.Process(args.parent_pid).create_time()==args.parent_start
                except psutil.Error:parent_alive=False
                if not parent_alive:
                    health("STOPPED","SUPERVISOR_EXITED")
                    raise SystemExit("SUPERVISOR_EXITED")
            configuration = None
            if telemetry:
                telemetry.context.identity = None
            try:
                if args.role == "observe":
                    observer.cycle()
                else:
                    try:
                        configuration = (telemetry.measure("configuration_http", lambda: backend.configuration(manifest))
                                         if telemetry else backend.configuration(manifest))
                    except Exception:
                        pass  # No entries; existing copies retain their management settings.
                    report = (telemetry.measure("engine_cycle", lambda: engine.cycle(configuration))
                              if telemetry else engine.cycle(configuration))
                    report["version"] = int(configuration["version"]) if configuration else 0
                    ledger.put("runtime:" + owner, {**report, "updated_at": time.time()})
                    if time.time() - last_report >= 5:
                        try:
                            reporter.submit(report) if reporter else backend.request("/internal/copy/demo/status", {"report": report})
                        except Exception:
                            print("STATUS_DELIVERY_FAILED", flush=True)
                        last_report = time.time()
                if args.role == "execute" and (report["errors"] or engine.recovery_required):
                    health("BLOCKED", report["errors"][0] if report["errors"] else "RECOVERY_REQUIRED")
                else:
                    health("CONNECTED")
                    reconnect.succeeded()
            except SafetyError as error:
                health("BLOCKED", str(error))
                if args.role == "execute":
                    engine.suspend(str(error))
                    ledger.put("runtime:" + owner, {**engine._report([str(error)]),
                                                   "version": 0, "updated_at": time.time()})
                else:
                    observer.continuity_started_at = time.time()
                print(str(error), flush=True)
                if args.role == "execute":
                    try:
                        report = engine._report([str(error)])
                        report["version"] = 0
                        reporter.submit(report) if reporter else backend.request("/internal/copy/demo/status", {"report": report})
                    except Exception:
                        pass
                fatal = {"REAL_TRADING_FORBIDDEN", "ACCOUNT_IDENTITY_CHANGED", "HEDGING_REQUIRED",
                         "TERMINAL_IDENTITY_CHANGED", "FIFO_UNSUPPORTED"}
                if str(error) in fatal:
                    raise SystemExit(str(error))
                if str(error) in {"TERMINAL_DISCONNECTED", "TERMINAL_CONNECTION_FAILED", "TERMINAL_AUTH_FAILED"} and reconnect.due():
                    reconnect.failed()
                    try:
                        broker.connect()
                    except SafetyError as reconnect_error:
                        if str(reconnect_error) in fatal:
                            health("BLOCKED",str(reconnect_error))
                            raise SystemExit(str(reconnect_error))
                        print("RECONNECT_PENDING", flush=True)
            time.sleep(0.5)
    except SafetyError as error:
        health("BLOCKED",str(error))
        raise SystemExit(str(error)) from None
    except ValueError:
        health("BLOCKED","LOCAL_STATE_INVALID")
        raise SystemExit("LOCAL_STATE_INVALID") from None
    except Exception as error:
        import traceback
        # Never serialize exception messages, locals or credentials.
        diagnostic={"at":time.time(),"role":args.role,"type":type(error).__name__,
                    "frames":[{"file":Path(f.filename).name,"line":f.lineno}
                              for f in traceback.extract_tb(error.__traceback__)[-8:]]}
        with (state.parent/'runtime-failures.jsonl').open('a',encoding='utf-8') as output:
            output.write(json.dumps(diagnostic)+'\n')
        health("BLOCKED","LOCAL_RUNTIME_FAILURE")
        raise SystemExit("LOCAL_RUNTIME_FAILURE") from None
    except KeyboardInterrupt:
        print("STOPPED: broker stops remain; position management is now offline.")
    finally:
        if reporter:
            reporter.close()
        if backend:
            backend.close()
        if 'engine' in locals() and args.fleet_state and hasattr(engine, 'close'):
            engine.close()
        if broker:
            broker.disconnect()
        if ledger:
            ledger.close()
        locks.close()
        if telemetry:
            telemetry.close()


if __name__ == "__main__":
    main()
