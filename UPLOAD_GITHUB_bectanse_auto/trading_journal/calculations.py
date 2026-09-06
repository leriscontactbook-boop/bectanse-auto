"""Deterministic, provider-neutral P&L and trade reconstruction."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TRADE_TYPES = {"BUY", "SELL"}
POSITION_COST_TYPES = {"COMMISSION", "COMMISSION_DAILY", "COMMISSION_MONTHLY", "FEE"}
EPSILON = Decimal("0.00000001")


def decimal_value(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def deal_net_pnl(deal: dict) -> Decimal:
    return sum((decimal_value(deal.get(field)) for field in ("profit", "commission", "swap", "fee")), Decimal("0"))


calculate_deal_net_pnl = deal_net_pnl


def calculate_trade_net_pnl(rows: list[dict]) -> Decimal:
    return sum((deal_net_pnl(row) for row in rows), Decimal("0"))


def as_utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_timezone(name: str) -> str:
    normalized = str(name or "Europe/Paris")[:80]
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Fuseau horaire invalide.") from exc
    return normalized


def _weighted_price(items: list[dict]) -> Decimal | None:
    volume = sum((decimal_value(item.get("volume")) for item in items), Decimal("0"))
    if volume <= 0:
        return None
    return sum((decimal_value(item.get("price")) * decimal_value(item.get("volume")) for item in items), Decimal("0")) / volume


def _is_position_row(row: dict) -> bool:
    deal_type = str(row.get("deal_type") or "").upper()
    return bool(row.get("is_trading_deal", deal_type in TRADE_TYPES)) or (
        int(row.get("mt5_position_id") or 0) > 0 and deal_type in POSITION_COST_TYPES
    )


def _finalize_trade(account_id: int, position_id: int, rows: list[dict], entries: list[dict], exits: list[dict],
                    opened_at: datetime, closed_at: datetime, direction: str) -> dict:
    net = calculate_trade_net_pnl(rows)
    return {
        "trading_account_id": account_id, "position_id": position_id,
        "symbol": str((entries[0] if entries else exits[-1]).get("symbol") or ""),
        "direction": direction,
        "volume": sum((decimal_value(row.get("volume")) for row in exits), Decimal("0")),
        "entry_price": _weighted_price(entries), "exit_price": _weighted_price(exits),
        "opened_at": opened_at, "closed_at": closed_at,
        "duration_seconds": max(0, int((closed_at - opened_at).total_seconds())),
        "net_pnl": net,
        "profit": sum((decimal_value(row.get("profit")) for row in rows), Decimal("0")),
        "fees": sum((decimal_value(row.get("commission")) + decimal_value(row.get("swap")) + decimal_value(row.get("fee")) for row in rows), Decimal("0")),
        "deals": len(rows),
    }


def reconstruct_positions(deals: list[dict]) -> list[dict]:
    """Build closed trades for hedge/netting accounts without counting partial exits."""
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in deals:
        if _is_position_row(row):
            groups[(int(row.get("trading_account_id") or 0),
                    int(row.get("mt5_position_id") or row.get("mt5_deal_ticket") or 0))].append(row)
    trades: list[dict] = []
    for (account_id, position_id), grouped in groups.items():
        grouped.sort(key=lambda row: (as_utc(row["executed_at"]), int(row.get("mt5_deal_ticket") or 0)))
        rows: list[dict] = []
        entries: list[dict] = []
        exits: list[dict] = []
        pending_cost_rows: list[dict] = []
        open_volume = Decimal("0")
        opened_at = None
        direction = ""
        for row in grouped:
            deal_type = str(row.get("deal_type") or "").upper()
            entry_type = str(row.get("entry_type") or "").upper()
            when, volume = as_utc(row["executed_at"]), decimal_value(row.get("volume"))
            if deal_type not in TRADE_TYPES:
                (rows if rows else pending_cost_rows).append(row)
            elif entry_type == "IN":
                if open_volume <= EPSILON:
                    rows, entries, exits = list(pending_cost_rows), [], []
                    pending_cost_rows = []
                    opened_at, direction = when, deal_type
                rows.append(row)
                entries.append(row)
                open_volume += volume
            elif entry_type in {"OUT", "OUT_BY"} and open_volume > EPSILON:
                rows.append(row)
                exits.append(row)
                open_volume -= volume
                if open_volume <= EPSILON:
                    trades.append(_finalize_trade(account_id, position_id, rows, entries, exits,
                                                  opened_at or when, when, direction or ("BUY" if deal_type == "SELL" else "SELL")))
                    rows, entries, exits, pending_cost_rows = [], [], [], []
                    open_volume, opened_at, direction = Decimal("0"), None, ""
            elif entry_type == "INOUT":
                previous_volume = open_volume
                if previous_volume > EPSILON:
                    rows.append(row)
                    exits.append(row)
                    trades.append(_finalize_trade(account_id, position_id, rows, entries, exits,
                                                  opened_at or when, when, direction or ("BUY" if deal_type == "SELL" else "SELL")))
                reverse_volume = max(Decimal("0"), volume - previous_volume)
                if reverse_volume > EPSILON:
                    synthetic = dict(row)
                    synthetic.update(volume=reverse_volume, profit=Decimal("0"), commission=Decimal("0"), swap=Decimal("0"), fee=Decimal("0"))
                    rows, entries, exits = [synthetic], [synthetic], []
                    open_volume, opened_at, direction = reverse_volume, when, deal_type
                else:
                    rows, entries, exits = [], [], []
                    open_volume, opened_at, direction = Decimal("0"), None, ""
    return sorted(trades, key=lambda row: (row["closed_at"], row["position_id"]))


def calculate_daily_pnl(trades: list[dict], timezone_name: str) -> dict[str, Decimal]:
    tz = ZoneInfo(validate_timezone(timezone_name))
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for trade in trades:
        totals[trade["closed_at"].astimezone(tz).date().isoformat()] += decimal_value(trade["net_pnl"])
    return dict(totals)


def calculate_monthly_pnl(trades: list[dict], timezone_name: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for day, value in calculate_daily_pnl(trades, timezone_name).items():
        totals[day[:7]] += value
    return dict(totals)


def calendar_summary(deals: list[dict], timezone_name: str, month: str) -> dict:
    tz = ZoneInfo(validate_timezone(timezone_name))
    daily: dict[str, dict] = defaultdict(lambda: {"netPnl": Decimal("0"), "deals": 0, "trades": 0, "wins": 0, "losses": 0})
    for row in deals:
        if bool(row.get("is_trading_deal", str(row.get("deal_type") or "").upper() in TRADE_TYPES)):
            day = as_utc(row["executed_at"]).astimezone(tz).date().isoformat()
            if day.startswith(month):
                daily[day]["deals"] += 1
    for trade in reconstruct_positions(deals):
        day = trade["closed_at"].astimezone(tz).date().isoformat()
        if day.startswith(month):
            daily[day]["netPnl"] += decimal_value(trade["net_pnl"])
            daily[day]["trades"] += 1
            if trade["net_pnl"] > 0:
                daily[day]["wins"] += 1
            elif trade["net_pnl"] < 0:
                daily[day]["losses"] += 1
    days = [{"date": day, "netPnl": round(float(values["netPnl"]), 2), **{k: values[k] for k in ("deals", "trades", "wins", "losses")}}
            for day, values in sorted(daily.items())]
    net = sum((decimal_value(day["netPnl"]) for day in days), Decimal("0"))
    trades = sum(day["trades"] for day in days)
    wins, losses = sum(day["wins"] for day in days), sum(day["losses"] for day in days)
    return {"month": month, "summary": {"netPnl": round(float(net), 2), "trades": trades,
            "deals": sum(day["deals"] for day in days), "wins": wins, "losses": losses,
            "winRate": round(100 * wins / trades, 2) if trades else 0,
            "tradingDays": sum(1 for day in days if day["trades"] > 0)}, "days": days}


def performance_stats(deals: list[dict], timezone_name: str = "Europe/Paris") -> dict:
    trades = reconstruct_positions(deals)
    pnls = [decimal_value(trade["net_pnl"]) for trade in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]
    gross_profit, gross_loss = sum(wins, Decimal("0")), sum(losses, Decimal("0"))
    daily = calculate_daily_pnl(trades, timezone_name)
    sorted_days = sorted(daily.items(), key=lambda item: item[1])
    cumulative = peak = max_drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {"netPnl": round(float(sum(pnls, Decimal("0"))), 2), "trades": len(trades),
            "deals": sum(1 for row in deals if bool(row.get("is_trading_deal", False))),
            "wins": len(wins), "losses": len(losses), "breakeven": len(breakeven),
            "winRate": round(100 * len(wins) / len(pnls), 2) if pnls else 0,
            "lossRate": round(100 * len(losses) / len(pnls), 2) if pnls else 0,
            "grossProfit": round(float(gross_profit), 2), "grossLoss": round(float(gross_loss), 2),
            "averageWin": round(float(gross_profit / len(wins)), 2) if wins else 0,
            "averageLoss": round(float(gross_loss / len(losses)), 2) if losses else 0,
            "largestWin": round(float(max(wins)), 2) if wins else 0,
            "largestLoss": round(float(min(losses)), 2) if losses else 0,
            "profitFactor": round(float(gross_profit / abs(gross_loss)), 2) if gross_loss else None,
            "maxDrawdown": round(float(max_drawdown), 2), "tradingDays": len(daily),
            "volume": round(float(sum((decimal_value(t["volume"]) for t in trades), Decimal("0"))), 4),
            "bestDay": {"date": sorted_days[-1][0], "netPnl": round(float(sorted_days[-1][1]), 2)} if sorted_days else None,
            "worstDay": {"date": sorted_days[0][0], "netPnl": round(float(sorted_days[0][1]), 2)} if sorted_days else None}


def analytics_breakdown(deals: list[dict], timezone_name: str) -> dict:
    tz = ZoneInfo(validate_timezone(timezone_name))
    dimensions = {name: defaultdict(list) for name in ("weekday", "symbol", "direction", "session", "holdingTime", "week", "month")}
    weekdays = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
    for trade in reconstruct_positions(deals):
        local, pnl = trade["closed_at"].astimezone(tz), decimal_value(trade["net_pnl"])
        session = "Asie" if local.hour < 8 else "Londres" if local.hour < 14 else "New York" if local.hour < 22 else "Hors session"
        duration = trade["duration_seconds"]
        holding = "< 15 min" if duration < 900 else "15–60 min" if duration < 3600 else "1–4 h" if duration < 14400 else "> 4 h"
        dimensions["weekday"][weekdays[local.weekday()]].append(pnl)
        dimensions["symbol"][trade["symbol"] or "—"].append(pnl)
        dimensions["direction"][trade["direction"] or "—"].append(pnl)
        dimensions["session"][session].append(pnl)
        dimensions["holdingTime"][holding].append(pnl)
        iso = local.isocalendar()
        dimensions["week"][f"{iso.year}-W{iso.week:02d}"].append(pnl)
        dimensions["month"][local.strftime("%Y-%m")].append(pnl)
    def serialize(groups):
        result = []
        for label, values in groups.items():
            wins = sum(1 for value in values if value > 0)
            result.append({"label": label, "netPnl": round(float(sum(values, Decimal("0"))), 2),
                           "trades": len(values), "winRate": round(100 * wins / len(values), 2) if values else 0})
        return sorted(result, key=lambda row: row["netPnl"], reverse=True)
    return {key: serialize(values) for key, values in dimensions.items()}
