#!/usr/bin/env python3
"""Compare one real MT5 range to its persisted PostgreSQL deals."""

from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_journal.migrate import connection_from_environment
from trading_journal.providers.mt5 import MetaTrader5Provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("account_id", type=int)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--terminal-path", default="")
    args = parser.parse_args()
    conn = connection_from_environment()
    try:
        account_rows = conn.run("SELECT login,server FROM trading_accounts WHERE id=:id", id=args.account_id)
        if not account_rows:
            raise SystemExit("Unknown account_id")
        login, server = str(account_rows[0][0]), str(account_rows[0][1])
    finally:
        conn.close()
    password = getpass.getpass("Investor password (hidden): ")
    end, start = datetime.now(timezone.utc), datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    provider = MetaTrader5Provider(args.terminal_path or None)
    try:
        provider.connect(login, server, password)
        source = list(provider.get_deals(start, end))
    finally:
        provider.disconnect()
    source_by_ticket = {row.ticket: row for row in source}
    conn = connection_from_environment()
    try:
        rows = conn.run("""SELECT mt5_deal_ticket,profit,commission,swap,fee FROM trading_deals
            WHERE trading_account_id=:id AND executed_at>=:start AND executed_at<=:end""",
            id=args.account_id, start=start, end=end)
    finally:
        conn.close()
    db_by_ticket = {int(row[0]): row for row in rows}
    source_pnl = sum((row.net_pnl for row in source), Decimal("0"))
    db_pnl = sum((sum((Decimal(str(value or 0)) for value in row[1:]), Decimal("0")) for row in rows), Decimal("0"))
    missing = sorted(set(source_by_ticket) - set(db_by_ticket))
    extra = sorted(set(db_by_ticket) - set(source_by_ticket))
    print(f"Account ............... ••••{login[-4:]}")
    print(f"MT5 deals ............. {len(source_by_ticket)}")
    print(f"DB deals .............. {len(db_by_ticket)}")
    print(f"Missing ............... {len(missing)}")
    print(f"Extra ................. {len(extra)}")
    print("Duplicates ............ 0 (database uniqueness constraint)")
    print(f"P&L delta ............. {db_pnl - source_pnl:.2f}")
    return 0 if not missing and not extra and abs(db_pnl - source_pnl) <= Decimal("0.01") else 2


if __name__ == "__main__":
    raise SystemExit(main())
