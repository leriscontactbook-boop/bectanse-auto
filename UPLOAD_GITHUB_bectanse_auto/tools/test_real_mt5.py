#!/usr/bin/env python3
"""Interactive, read-only validation of one real MT5 investor account."""

from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timedelta, timezone

from trading_journal.providers.mt5 import MetaTrader5Provider


def line(label: str, passed: bool) -> None:
    print(f"{label:.<24} {'PASS' if passed else 'FAIL'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-path", default="")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    login = input("MT5 login: ").strip()
    server = input("MT5 server: ").strip()
    password = getpass.getpass("Investor password (hidden): ")
    provider = MetaTrader5Provider(args.terminal_path or None)
    try:
        account = provider.connect(login, server, password)
        line("MT5 TERMINAL ", True)
        line("LOGIN ", account.login == login)
        line("ACCOUNT INFO ", bool(account.server and account.currency))
        line("READ ACCESS ", account.access_mode == "READ_ONLY")
        provider.get_open_positions()
        deals = list(provider.get_deals(datetime.now(timezone.utc) - timedelta(days=max(1, args.days)), datetime.now(timezone.utc)))
        line("HISTORY ", deals is not None)
        print(f"Account: ••••{login[-4:]} | server={account.server} | deals={len(deals)}")
        return 0
    except Exception as error:
        line("VALIDATION ", False)
        print(f"Safe error: {getattr(error, 'code', type(error).__name__)}")
        return 1
    finally:
        provider.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
