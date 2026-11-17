#!/usr/bin/env python3
"""Non-interactive smoke test for one MetaTrader 5 terminal installation."""

from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    import MetaTrader5 as mt5

    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--login", default=os.environ.get("MT5_TEST_LOGIN", ""))
    parser.add_argument("--server", default=os.environ.get("MT5_TEST_SERVER", ""))
    parser.add_argument("--password", default=os.environ.get("MT5_TEST_PASSWORD", ""))
    args = parser.parse_args()
    credential_values = (args.login, args.server, args.password)
    if any(credential_values) and not all(credential_values):
        parser.error("login, server and password must be provided together")
    initialized = False
    try:
        options = {"timeout": args.timeout_ms, "portable": args.portable}
        if all(credential_values):
            options.update({
                "login": int(args.login),
                "server": args.server,
                "password": args.password,
            })
        initialized = bool(mt5.initialize(args.terminal_path, **options))
        info = mt5.terminal_info() if initialized else None
        print(json.dumps({
            "initialized": initialized,
            "terminal_info": info is not None,
            "connected": bool(getattr(info, "connected", False)) if info else False,
            "build": int(getattr(info, "build", 0) or 0) if info else 0,
            "last_error": list(mt5.last_error() or ()),
        }, separators=(",", ":")))
        return 0 if initialized and info is not None else 1
    finally:
        if initialized:
            mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
