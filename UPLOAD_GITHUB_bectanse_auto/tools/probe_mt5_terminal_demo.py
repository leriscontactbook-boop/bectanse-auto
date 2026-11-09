#!/usr/bin/env python3
"""Non-interactive smoke test for one MetaTrader 5 terminal installation."""

from __future__ import annotations

import argparse
import json
import getpass
import ntpath


def main() -> int:
    import MetaTrader5 as mt5

    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--shared-terminal-data", action="store_true",
                        help="Use shared MT5 data paths; isolated portable mode is the default.")
    parser.add_argument("--prompt-demo-credentials", action="store_true",
                        help="Prompt interactively; credentials are never accepted in command arguments or env vars.")
    args = parser.parse_args()
    login = input("Demo MT5 login (leave empty to use the account already open in this dedicated terminal): ").strip() \
        if args.prompt_demo_credentials else ""
    server = input("Demo MT5 server: ").strip() if login else ""
    password = getpass.getpass("Demo MT5 password (hidden): ") if login and args.prompt_demo_credentials else ""
    if args.prompt_demo_credentials and login and not server:
        parser.error("A broker server is required when a login is supplied.")
    initialized = False
    try:
        options = {"timeout": args.timeout_ms, "portable": not args.shared_terminal_data}
        if login:
            options.update({
                "login": int(login),
                "server": server,
                "password": password,
            })
        initialized = bool(mt5.initialize(args.terminal_path, **options))
        info = mt5.terminal_info() if initialized else None
        account = mt5.account_info() if initialized else None
        actual_path = str(getattr(info, "path", "") or "") if info else ""
        expected_path = ntpath.dirname(ntpath.normpath(args.terminal_path))
        terminal_path_verified = bool(actual_path) and (
            ntpath.normcase(ntpath.normpath(actual_path)) == ntpath.normcase(expected_path)
        )
        is_demo = bool(account is not None and getattr(account, "trade_mode", None) ==
                       getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", object()))
        login_value = str(getattr(account, "login", "") or "") if account else ""
        diagnostic = (mt5.last_error() or (None,))[0]
        print(json.dumps({
            "initialized": initialized,
            "terminal_info": info is not None,
            "connected": bool(getattr(info, "connected", False)) if info else False,
            "terminal_path_verified": terminal_path_verified,
            "account_info": account is not None,
            "demo_account_verified": is_demo,
            "login_masked": "••••" + login_value[-4:] if login_value else "",
            "build": int(getattr(info, "build", 0) or 0) if info else 0,
            "diagnostic_code": diagnostic,
        }, separators=(",", ":")))
        return 0 if (initialized and info is not None and account is not None
                     and bool(getattr(info, "connected", False)) and terminal_path_verified and is_demo) else 1
    finally:
        if password:
            password = ""
        if initialized:
            mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
