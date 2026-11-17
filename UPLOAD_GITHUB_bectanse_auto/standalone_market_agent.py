"""Moteur autonome XAUUSD sans Pine Script ni capture d'écran.

Les décisions sont calculées uniquement à partir de bougies clôturées. La
source principale est Twelve Data, un fournisseur de données en lecture seule.
Le module ne place aucun ordre : il produit un instantané déterministe consommé
par ``market_agent.py`` puis envoyé sur Telegram.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


GRANULARITY_SECONDS = {"M15": 900, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}
TWELVE_DATA_INTERVALS = {"M15": "15min", "H1": "1h", "H4": "4h", "D": "1day", "W": "1week"}
TWELVE_DATA_COUNTS = {"M15": 700, "H1": 650, "H4": 450, "D": 260, "W": 120}


def _flag(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "oui", "on"}


def _parse_oanda_time(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Les réponses OANDA peuvent contenir neuf chiffres de sous-seconde.
        if "." in text:
            head, tail = text.split(".", 1)
            fraction, offset = (tail.split("+", 1) + [""])[:2]
            text = f"{head}.{fraction[:6]}" + (f"+{offset}" if offset else "+00:00")
        return datetime.fromisoformat(text)


def _parse_twelve_data_time(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("Horodatage Twelve Data manquant")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class TwelveDataCandleProvider:
    """Client REST lecture seule pour les chandeliers XAU/USD de Twelve Data."""

    base_url = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key, symbol="XAU/USD", session=None, now_fn=None):
        if not api_key:
            raise RuntimeError("TWELVE_DATA_API_KEY manquante")
        self.api_key = api_key.strip()
        self.symbol = str(symbol or "XAU/USD").strip().upper()
        self.session = session or requests.Session()
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def candles(self, granularity, count=500):
        canonical = str(granularity or "").upper()
        interval = TWELVE_DATA_INTERVALS.get(canonical)
        if not interval:
            raise ValueError(f"Intervalle Twelve Data non pris en charge : {granularity}")
        response = self.session.get(
            self.base_url,
            params={
                "symbol": self.symbol,
                "interval": interval,
                "outputsize": min(max(int(count), 60), 5000),
                "timezone": "UTC",
                "order": "ASC",
                "apikey": self.api_key,
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("status") or "").lower() == "error":
            message = payload.get("message") or payload.get("code") or "réponse invalide"
            raise RuntimeError(f"Twelve Data : {message}")
        rows = payload.get("values")
        if not isinstance(rows, list):
            raise RuntimeError(f"Historique {canonical} absent chez Twelve Data")

        seconds = GRANULARITY_SECONDS[canonical]
        now_utc = self.now_fn().astimezone(timezone.utc)
        parsed = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            start = _parse_twelve_data_time(item.get("datetime"))
            close_time = start + timedelta(seconds=seconds)
            if close_time > now_utc:
                continue
            try:
                candle = {
                    "time": start,
                    "time_close": close_time,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item.get("volume") or 0),
                }
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(f"Bougie {canonical} invalide chez Twelve Data") from error
            if candle["low"] > min(candle["open"], candle["close"], candle["high"]):
                raise RuntimeError(f"OHLC {canonical} incohérent chez Twelve Data")
            if candle["high"] < max(candle["open"], candle["close"], candle["low"]):
                raise RuntimeError(f"OHLC {canonical} incohérent chez Twelve Data")
            parsed.append(candle)

        parsed.sort(key=lambda item: item["time"])
        parsed = list({item["time"]: item for item in parsed}.values())
        if len(parsed) < 60:
            raise RuntimeError(f"Historique {canonical} insuffisant ({len(parsed)} bougies)")
        return parsed

    def complete_market(self):
        return {
            key: self.candles(key, TWELVE_DATA_COUNTS[key])
            for key in ("M15", "H1", "H4", "D", "W")
        }


class OandaCandleProvider:
    """Client REST minimal, lecture seule, pour les chandeliers OANDA v20."""

    def __init__(self, token, account_id="", environment="practice", instrument="XAU_USD"):
        if not token:
            raise RuntimeError("OANDA_API_TOKEN manquant")
        self.token = token.strip()
        self.account_id = account_id.strip()
        self.instrument = instrument.strip().upper() or "XAU_USD"
        self.base_url = (
            "https://api-fxtrade.oanda.com"
            if str(environment).strip().lower() == "live"
            else "https://api-fxpractice.oanda.com"
        )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        })

    def resolve_account_id(self):
        if self.account_id:
            return self.account_id
        response = self.session.get(f"{self.base_url}/v3/accounts", timeout=20)
        response.raise_for_status()
        accounts = response.json().get("accounts") or []
        if not accounts:
            raise RuntimeError("Aucun compte OANDA accessible avec ce jeton")
        self.account_id = str(accounts[0].get("id") or "").strip()
        if not self.account_id:
            raise RuntimeError("Identifiant de compte OANDA introuvable")
        return self.account_id

    def candles(self, granularity, count=500):
        account_id = self.resolve_account_id()
        response = self.session.get(
            f"{self.base_url}/v3/accounts/{account_id}/instruments/{self.instrument}/candles",
            params={
                "price": "M",
                "granularity": granularity,
                "count": min(max(int(count), 10), 5000),
                "smooth": "false",
                "dailyAlignment": 17,
                "alignmentTimezone": "America/New_York",
                "weeklyAlignment": "Friday",
            },
            timeout=25,
        )
        response.raise_for_status()
        parsed = []
        seconds = GRANULARITY_SECONDS[granularity]
        for item in response.json().get("candles") or []:
            if not item.get("complete") or not isinstance(item.get("mid"), dict):
                continue
            start = _parse_oanda_time(item.get("time")).astimezone(timezone.utc)
            mid = item["mid"]
            parsed.append({
                "time": start,
                "time_close": start + timedelta(seconds=seconds),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": float(item.get("volume") or 0),
            })
        if len(parsed) < 60:
            raise RuntimeError(f"Historique {granularity} insuffisant ({len(parsed)} bougies)")
        return parsed

    def complete_market(self):
        return {
            "M15": self.candles("M15", 700),
            "H1": self.candles("H1", 650),
            "H4": self.candles("H4", 450),
            "D": self.candles("D", 260),
            "W": self.candles("W", 120),
        }


def _ema(values, length):
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def _atr(candles, length=14):
    true_ranges = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1]["close"] if index else candle["close"]
        true_ranges.append(max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        ))
    if not true_ranges:
        return 0.0
    seed = sum(true_ranges[:length]) / min(length, len(true_ranges))
    value = seed
    for item in true_ranges[length:]:
        value = ((length - 1) * value + item) / length
    return value


def _pivots(candles, left=2, right=2):
    highs, lows = [], []
    for index in range(left, len(candles) - right):
        candle = candles[index]
        window = candles[index - left:index + right + 1]
        if candle["high"] == max(item["high"] for item in window):
            highs.append((index, candle["high"]))
        if candle["low"] == min(item["low"] for item in window):
            lows.append((index, candle["low"]))
    return highs, lows


def _trend(candles):
    closes = [item["close"] for item in candles]
    ema20, ema50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    highs, lows = _pivots(candles)
    hh = len(highs) >= 2 and highs[-1][1] > highs[-2][1]
    lh = len(highs) >= 2 and highs[-1][1] < highs[-2][1]
    hl = len(lows) >= 2 and lows[-1][1] > lows[-2][1]
    ll = len(lows) >= 2 and lows[-1][1] < lows[-2][1]
    if hh and hl and closes[-1] >= ema20:
        label = "HAUSSIÈRE"
    elif lh and ll and closes[-1] <= ema20:
        label = "BAISSIÈRE"
    elif ema20 > ema50 and closes[-1] > ema20:
        label = "HAUSSIÈRE"
    elif ema20 < ema50 and closes[-1] < ema20:
        label = "BAISSIÈRE"
    else:
        label = "RANGE"
    return {
        "label": label,
        "bullish": label == "HAUSSIÈRE",
        "bearish": label == "BAISSIÈRE",
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "ema20": ema20,
        "ema50": ema50,
        "highs": highs,
        "lows": lows,
    }


def _structure(candles, prior_trend):
    highs, lows = _pivots(candles)
    last_high = highs[-1][1] if highs else max(item["high"] for item in candles[-20:])
    last_low = lows[-1][1] if lows else min(item["low"] for item in candles[-20:])
    previous_close, close = candles[-2]["close"], candles[-1]["close"]
    bos_up = previous_close <= last_high < close
    bos_down = previous_close >= last_low > close
    return {
        "bullish": prior_trend["bullish"],
        "bos": bos_up or bos_down,
        "bos_up": bos_up,
        "choch": (bos_up and prior_trend["bearish"]) or (bos_down and prior_trend["bullish"]),
        "bos_price": last_high if bos_up else last_low if bos_down else None,
        "last_high": last_high,
        "last_low": last_low,
        "hh": prior_trend["hh"],
        "hl": prior_trend["hl"],
        "lh": prior_trend["lh"],
        "ll": prior_trend["ll"],
    }


def _zone(low=None, high=None, active=False, bullish=False):
    if low is not None and high is not None:
        low, high = min(float(low), float(high)), max(float(low), float(high))
    return {"low": low, "high": high, "active": bool(active), "bullish": bool(bullish)}


def _price_in(zone, price):
    return zone.get("low") is not None and zone.get("high") is not None and zone["low"] <= price <= zone["high"]


def _latest_fvg(candles, price):
    for index in range(len(candles) - 1, max(2, len(candles) - 140), -1):
        current, two_back = candles[index], candles[index - 2]
        if current["low"] > two_back["high"]:
            low, high = two_back["high"], current["low"]
            fully_filled = any(item["low"] <= low for item in candles[index + 1:])
            if not fully_filled:
                return _zone(low, high, low <= price <= high, True)
        if current["high"] < two_back["low"]:
            low, high = current["high"], two_back["low"]
            fully_filled = any(item["high"] >= high for item in candles[index + 1:])
            if not fully_filled:
                return _zone(low, high, low <= price <= high, False)
    return _zone()


def _latest_order_block(candles, atr, price):
    for index in range(len(candles) - 1, max(2, len(candles) - 120), -1):
        candle = candles[index]
        body = abs(candle["close"] - candle["open"])
        if body < atr * 1.2:
            continue
        bullish_displacement = candle["close"] > candle["open"]
        for origin in range(index - 1, max(-1, index - 5), -1):
            source = candles[origin]
            source_bull = source["close"] > source["open"]
            if source_bull != bullish_displacement:
                zone = _zone(source["low"], source["high"], False, bullish_displacement)
                zone["active"] = _price_in(zone, price)
                return zone
    return _zone()


def _session_zone(candles, local_tz, start_time, end_time, price):
    latest_local = candles[-1]["time"].astimezone(local_tz)
    session_date = latest_local.date()
    selected = []
    for candle in candles:
        local = candle["time"].astimezone(local_tz)
        if local.date() == session_date and start_time <= local.time() < end_time:
            selected.append(candle)
    if not selected:
        previous_dates = sorted({item["time"].astimezone(local_tz).date() for item in candles if item["time"].astimezone(local_tz).date() < session_date})
        if previous_dates:
            previous = previous_dates[-1]
            selected = [
                item for item in candles
                if item["time"].astimezone(local_tz).date() == previous
                and start_time <= item["time"].astimezone(local_tz).time() < end_time
            ]
    if not selected:
        return _zone()
    low, high = min(item["low"] for item in selected), max(item["high"] for item in selected)
    return _zone(low, high, low <= price <= high, False)


def _previous_range(candles, price):
    if len(candles) < 2:
        return _zone()
    candle = candles[-2]
    return _zone(candle["low"], candle["high"], candle["low"] <= price <= candle["high"], False)


def _liquidity_zones(candles, atr, price):
    highs, lows = _pivots(candles)
    recent_highs = [value for _, value in highs[-12:]] or [max(item["high"] for item in candles[-30:])]
    recent_lows = [value for _, value in lows[-12:]] or [min(item["low"] for item in candles[-30:])]

    def closest_cluster(values, side):
        if not values:
            return None
        candidates = []
        tolerance = atr * 0.18
        for index, value in enumerate(values):
            cluster = [other for other in values[index + 1:] if abs(other - value) <= tolerance]
            if cluster:
                candidates.append(sum([value] + cluster) / (len(cluster) + 1))
        pool = candidates or values
        above = [value for value in pool if value >= price]
        below = [value for value in pool if value <= price]
        if side == "high":
            return min(above, default=max(pool))
        return max(below, default=min(pool))

    buy = closest_cluster(recent_highs, "high")
    sell = closest_cluster(recent_lows, "low")
    width = max(atr * 0.08, price * 0.00015)
    buy_zone = _zone(buy - width, buy + width, buy is not None and abs(price - buy) <= width, False) if buy is not None else _zone()
    sell_zone = _zone(sell - width, sell + width, sell is not None and abs(price - sell) <= width, True) if sell is not None else _zone()
    combined = _zone(sell, buy, sell is not None and buy is not None and sell <= price <= buy, False)
    return buy_zone, sell_zone, combined


def _ote_zone(candles, trend, price):
    highs, lows = trend["highs"], trend["lows"]
    if not highs or not lows:
        return _zone()
    if trend["bullish"]:
        high_index, high = highs[-1]
        prior_lows = [(index, value) for index, value in lows if index < high_index]
        if not prior_lows:
            return _zone()
        low = prior_lows[-1][1]
        zone_low, zone_high = high - (high - low) * 0.786, high - (high - low) * 0.618
        return _zone(zone_low, zone_high, zone_low <= price <= zone_high, True)
    low_index, low = lows[-1]
    prior_highs = [(index, value) for index, value in highs if index < low_index]
    if not prior_highs:
        return _zone()
    high = prior_highs[-1][1]
    zone_low, zone_high = low + (high - low) * 0.618, low + (high - low) * 0.786
    return _zone(zone_low, zone_high, zone_low <= price <= zone_high, False)


def _rejection(candles, atr):
    candle = candles[-1]
    prior = candles[-9:-1]
    body = max(abs(candle["close"] - candle["open"]), atr * 0.04)
    upper = candle["high"] - max(candle["open"], candle["close"])
    lower = min(candle["open"], candle["close"]) - candle["low"]
    prior_high, prior_low = max(item["high"] for item in prior), min(item["low"] for item in prior)
    bullish_candle = lower >= body * 1.5 and candle["close"] >= candle["low"] + (candle["high"] - candle["low"]) * 0.55
    bearish_candle = upper >= body * 1.5 and candle["close"] <= candle["low"] + (candle["high"] - candle["low"]) * 0.45
    sweep_low = candle["low"] < prior_low and candle["close"] > prior_low
    sweep_high = candle["high"] > prior_high and candle["close"] < prior_high
    return {
        "bullish_candle": bullish_candle,
        "bearish_candle": bearish_candle,
        "sweep_low": sweep_low,
        "sweep_high": sweep_high,
        "institutional_long": bullish_candle and sweep_low and candle["close"] > candle["open"],
        "institutional_short": bearish_candle and sweep_high and candle["close"] < candle["open"],
    }


def _flow(candles):
    recent = candles[-20:]
    average = sum(item["volume"] for item in recent[:-1]) / max(1, len(recent) - 1)
    relative = recent[-1]["volume"] / average if average else 0.0
    signed = sum((1 if item["close"] >= item["open"] else -1) * item["volume"] for item in candles[-12:])
    total = sum(item["volume"] for item in candles[-12:]) or 1.0
    delta = max(-1.0, min(1.0, signed / total))
    return relative, delta


def _session_name(now_local):
    current = now_local.time()
    if dt_time(0, 0) <= current < dt_time(8, 0):
        return "ASIE", dt_time(1, 0) <= current < dt_time(5, 0)
    if dt_time(8, 0) <= current < dt_time(14, 30):
        return "LONDRES", dt_time(8, 0) <= current < dt_time(11, 0)
    if dt_time(14, 30) <= current < dt_time(22, 0):
        return "NEW YORK", dt_time(14, 30) <= current < dt_time(17, 30)
    return "HORS SESSION", False


def _score(trends, structure, zones, rejection, relative_volume, delta, price, atr, candles):
    trend_labels = [trends[key]["label"] for key in ("D", "H4", "H1")]
    context_long = sum(label == "HAUSSIÈRE" for label in trend_labels)
    context_short = sum(label == "BAISSIÈRE" for label in trend_labels)
    structure_long = min(3, (2 if trends["M15"]["bullish"] else 0) + (1 if structure["bos"] and structure["bos_up"] else 0))
    structure_short = min(3, (2 if trends["M15"]["bearish"] else 0) + (1 if structure["bos"] and not structure["bos_up"] else 0))

    recent_high = max(item["high"] for item in candles[-80:])
    recent_low = min(item["low"] for item in candles[-80:])
    midpoint = (recent_high + recent_low) / 2.0
    discount, premium = price <= midpoint, price >= midpoint
    bullish_active = sum(_price_in(zones[key], price) and zones[key].get("bullish") for key in ("ote", "order_block", "fvg", "demand", "sell_side_liquidity"))
    bearish_active = sum(_price_in(zones[key], price) and not zones[key].get("bullish") for key in ("ote", "order_block", "fvg", "supply", "buy_side_liquidity"))
    location_long = min(3, int(discount) + bullish_active)
    location_short = min(3, int(premium) + bearish_active)

    flow_long = min(3, int(delta > 0) + int(relative_volume >= 1.0) + int(candles[-1]["close"] > candles[-1]["open"]))
    flow_short = min(3, int(delta < 0) + int(relative_volume >= 1.0) + int(candles[-1]["close"] < candles[-1]["open"]))
    trigger_long = min(3, int(rejection["bullish_candle"]) + int(rejection["sweep_low"]) + int(structure["bos"] and structure["bos_up"]))
    trigger_short = min(3, int(rejection["bearish_candle"]) + int(rejection["sweep_high"]) + int(structure["bos"] and not structure["bos_up"]))
    long_score = context_long + structure_long + location_long + flow_long + trigger_long
    short_score = context_short + structure_short + location_short + flow_short + trigger_short
    return {
        "long": long_score, "short": short_score,
        "required_long": 9, "required_short": 9,
        "checks": 5, "confluence": max(long_score, short_score),
        "context_long": context_long, "context_short": context_short,
        "structure_long": structure_long, "structure_short": structure_short,
        "location_long": location_long, "location_short": location_short,
        "flow_long": flow_long, "flow_short": flow_short,
        "trigger_long": trigger_long, "trigger_short": trigger_short,
        "discount": discount, "premium": premium,
    }


def _risk_plan(direction, candles, atr, price, zones):
    if direction not in {"BUY", "SELL"}:
        return {}
    recent_low = min(item["low"] for item in candles[-10:])
    recent_high = max(item["high"] for item in candles[-10:])
    if direction == "BUY":
        relevant_lows = [recent_low] + [
            zones[key]["low"] for key in ("ote", "order_block", "fvg", "demand", "sell_side_liquidity")
            if zones[key].get("low") is not None
            and zones[key].get("bullish")
            and (zones[key].get("active") or abs(price - zones[key]["high"]) <= atr * 2.0)
        ]
        stop = min(relevant_lows) - atr * 0.18
        risk = max(price - stop, atr * 0.65)
        tp1, tp2, tp3 = price + risk, price + risk * 2, price + risk * 3
    else:
        relevant_highs = [recent_high] + [
            zones[key]["high"] for key in ("ote", "order_block", "fvg", "supply", "buy_side_liquidity")
            if zones[key].get("high") is not None
            and not zones[key].get("bullish")
            and (zones[key].get("active") or abs(zones[key]["low"] - price) <= atr * 2.0)
        ]
        stop = max(relevant_highs) + atr * 0.18
        risk = max(stop - price, atr * 0.65)
        tp1, tp2, tp3 = price - risk, price - risk * 2, price - risk * 3
    optimal = None
    for key in ("ote", "order_block", "fvg"):
        zone = zones[key]
        if zone.get("low") is not None and zone.get("high") is not None and zone.get("bullish") == (direction == "BUY"):
            optimal = (zone["low"] + zone["high"]) / 2.0
            break
    return {
        "entry": price,
        "stop": stop,
        "dynamic_stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "preventive": True,
        "optimal_entry": optimal,
        "rr": 3.0,
        "sl_status": "STRUCTURE + BUFFER ATR",
    }


def build_snapshot(
    market,
    instrument="XAU_USD",
    local_timezone="Europe/Paris",
    source="standalone_oanda",
    tickerid_prefix="OANDA",
    volume_source="OANDA tick count",
):
    """Construit le contrat complet compris par l'agent Telegram."""
    required = ("M15", "H1", "H4", "D", "W")
    if any(len(market.get(key) or []) < 60 for key in required):
        raise RuntimeError("Données multi-timeframes incomplètes")
    candles = market["M15"]
    last = candles[-1]
    price = last["close"]
    atr = _atr(candles)
    trends = {key: _trend(market[key]) for key in ("D", "H4", "H1", "M15")}
    structure = _structure(candles, trends["M15"])
    rejection = _rejection(candles, atr)
    local_tz = ZoneInfo(local_timezone)
    now_local = last["time_close"].astimezone(local_tz)
    session, in_kill_zone = _session_name(now_local)

    buy_liquidity, sell_liquidity, combined_liquidity = _liquidity_zones(candles, atr, price)
    recent_high = max(item["high"] for item in candles[-80:])
    recent_low = min(item["low"] for item in candles[-80:])
    support = _zone(recent_low, recent_low + atr * 0.35, False, True)
    resistance = _zone(recent_high - atr * 0.35, recent_high, False, False)
    zones = {
        "ote": _ote_zone(candles, trends["M15"], price),
        "order_block": _latest_order_block(candles, atr, price),
        "fvg": _latest_fvg(candles, price),
        "liquidity": combined_liquidity,
        "buy_side_liquidity": buy_liquidity,
        "sell_side_liquidity": sell_liquidity,
        "support": support,
        "resistance": resistance,
        "supply": resistance,
        "demand": support,
        "previous_day": _previous_range(market["D"], price),
        "previous_week": _previous_range(market["W"], price),
        "asia": _session_zone(candles, local_tz, dt_time(0, 0), dt_time(8, 0), price),
        "london_or": _session_zone(candles, local_tz, dt_time(8, 0), dt_time(9, 0), price),
        "comex_or": _session_zone(candles, local_tz, dt_time(14, 30), dt_time(15, 0), price),
    }
    for zone in zones.values():
        if zone.get("low") is not None and zone.get("high") is not None:
            zone["active"] = _price_in(zone, price)

    relative_volume, delta = _flow(candles)
    scores = _score(trends, structure, zones, rejection, relative_volume, delta, price, atr, candles)
    long_ready = scores["long"] >= scores["required_long"] and scores["long"] >= scores["short"] + 2
    short_ready = scores["short"] >= scores["required_short"] and scores["short"] >= scores["long"] + 2
    pre_long = not long_ready and scores["long"] >= 7 and scores["long"] > scores["short"]
    pre_short = not short_ready and scores["short"] >= 7 and scores["short"] > scores["long"]
    direction = "BUY" if long_ready or pre_long else "SELL" if short_ready or pre_short else "NEUTRE"

    recent_range = max(item["high"] for item in candles[-10:]) - min(item["low"] for item in candles[-10:])
    compression = recent_range <= atr * 2.15
    conflicting_mtf = scores["context_long"] > 0 and scores["context_short"] > 0
    no_trade = not (long_ready or short_ready) and (compression or max(scores["long"], scores["short"]) < 7)
    if compression:
        no_trade_reason = "compression M15 : attendre la sortie du range et son retest"
    elif conflicting_mtf:
        no_trade_reason = "timeframes en conflit : aucune direction institutionnelle alignée"
    else:
        no_trade_reason = "confluence ou déclencheur insuffisant à cette clôture"

    zones["no_trade"] = _zone(
        min(item["low"] for item in candles[-10:]),
        max(item["high"] for item in candles[-10:]),
        no_trade,
        False,
    )
    risk = _risk_plan(direction, candles, atr, price, zones)
    setup = (
        "SWEEP + REJET + BOS" if (rejection["sweep_low"] or rejection["sweep_high"]) and structure["bos"]
        else "RETEST DE ZONE + CONFIRMATION" if any(zone.get("active") for zone in zones.values())
        else "CASSURE + RETEST ATTENDU"
    )
    market_regime = "COMPRESSION" if compression else "TENDANCE" if trends["M15"]["label"] != "RANGE" else "RANGE"
    verdict = "ACHAT CONFIRMÉ" if long_ready else "VENTE CONFIRMÉE" if short_ready else "ACHAT ARMÉ" if pre_long else "VENTE ARMÉE" if pre_short else "ATTENTE"

    return {
        "schema_version": "2.0",
        "source": source,
        "event_type": "bar_close",
        "ticker": "XAUUSD",
        "tickerid": f"{tickerid_prefix}:{instrument}",
        "timeframe": "M15",
        "time_close": int(last["time_close"].timestamp() * 1000),
        "profile": "AGENT AUTONOME",
        "verdict": verdict,
        "bias": direction,
        "setup": setup,
        "trade_quality": "EXCELLENTE" if max(scores["long"], scores["short"]) >= 12 else "BONNE" if max(scores["long"], scores["short"]) >= 9 else "FAIBLE",
        "market_regime": market_regime,
        "wyckoff_phase": "ACCUMULATION POTENTIELLE" if trends["H1"]["bearish"] and trends["M15"]["bullish"] else "DISTRIBUTION POTENTIELLE" if trends["H1"]["bullish"] and trends["M15"]["bearish"] else "NEUTRE",
        "volatility_regime": "ÉLEVÉE" if atr / price >= 0.004 else "NORMALE",
        "signal_age": 0,
        "signal_age_text": "nouvelle clôture M15",
        "no_trade": no_trade,
        "no_trade_reason": no_trade_reason,
        "ohlc": {**{key: last[key] for key in ("open", "high", "low", "close")}, "atr": atr},
        "scores": {key: value for key, value in scores.items() if key not in {"discount", "premium"}},
        "long_signal": long_ready,
        "short_signal": short_ready,
        "pre_long": pre_long,
        "pre_short": pre_short,
        "trade_active": False,
        "trade_side": direction if direction != "NEUTRE" else "",
        "mtf": {
            "high_bull": trends["D"]["bullish"],
            "mid_bull": trends["H4"]["bullish"],
            "trend_bull": trends["H1"]["bullish"],
            "long": scores["context_long"] >= 2,
            "short": scores["context_short"] >= 2,
        },
        "trends": {key: value["label"] for key, value in trends.items()},
        "structure": structure,
        "rejection": rejection,
        "zones": zones,
        "discount": scores["discount"],
        "premium": scores["premium"],
        "in_ote": zones["ote"]["active"],
        "in_ob_bull": zones["order_block"]["active"] and zones["order_block"]["bullish"],
        "in_ob_bear": zones["order_block"]["active"] and not zones["order_block"]["bullish"],
        "in_fvg_bull": zones["fvg"]["active"] and zones["fvg"]["bullish"],
        "in_fvg_bear": zones["fvg"]["active"] and not zones["fvg"]["bullish"],
        "risk": risk,
        "flow": {
            "volume_source": volume_source,
            "relative_volume": relative_volume,
            "delta_ratio": delta,
            "delta_positive": delta > 0,
            "delta_negative": delta < 0,
            "macro_bull": False,
            "macro_bear": False,
            "macro_data_count": 0,
        },
        "session": session,
        "in_session": session != "HORS SESSION",
        "in_kill_zone": in_kill_zone,
    }


def build_oanda_snapshot_from_env():
    provider = OandaCandleProvider(
        token=os.environ.get("OANDA_API_TOKEN", ""),
        account_id=os.environ.get("OANDA_ACCOUNT_ID", ""),
        environment=os.environ.get("OANDA_ENVIRONMENT", "practice"),
        instrument=os.environ.get("OANDA_INSTRUMENT", "XAU_USD"),
    )
    return build_snapshot(
        provider.complete_market(),
        instrument=provider.instrument,
        local_timezone=os.environ.get("MARKET_AGENT_TIMEZONE", "Europe/Paris"),
    )


def build_twelve_data_snapshot_from_env():
    provider = TwelveDataCandleProvider(
        api_key=os.environ.get("TWELVE_DATA_API_KEY", ""),
        symbol=os.environ.get("TWELVE_DATA_SYMBOL", "XAU/USD"),
    )
    return build_snapshot(
        provider.complete_market(),
        instrument=provider.symbol,
        local_timezone=os.environ.get("MARKET_AGENT_TIMEZONE", "Europe/Paris"),
        source="standalone_twelvedata",
        tickerid_prefix="TWELVEDATA",
        volume_source="Twelve Data (volume indisponible sur le spot si égal à zéro)",
    )


def build_standalone_snapshot_from_env():
    """Sélectionne explicitement la source sans jamais ouvrir de position."""
    provider = os.environ.get("MARKET_AGENT_DATA_PROVIDER", "twelvedata").strip().lower()
    if provider in {"twelvedata", "twelve_data", "twelve-data"}:
        return build_twelve_data_snapshot_from_env()
    if provider == "oanda":
        return build_oanda_snapshot_from_env()
    raise RuntimeError(f"MARKET_AGENT_DATA_PROVIDER inconnu : {provider}")


def _wire_time(value):
    """Convertit un timestamp MT5 (secondes ou millisecondes) en UTC."""
    try:
        stamp = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Timestamp MT5 invalide") from error
    if not math.isfinite(stamp):
        raise ValueError("Timestamp MT5 invalide")
    if abs(stamp) >= 10_000_000_000:
        stamp /= 1000.0
    return datetime.fromtimestamp(stamp, timezone.utc)


def market_from_mt5_payload(raw_market):
    """Valide les bougies clôturées envoyées par le pont MT5."""
    if not isinstance(raw_market, dict):
        raise ValueError("Marché MT5 manquant")
    aliases = {"M15": "M15", "H1": "H1", "H4": "H4", "D": "D", "D1": "D", "W": "W", "W1": "W"}
    parsed_market = {}
    for raw_key, canonical in aliases.items():
        if canonical in parsed_market:
            continue
        rows = raw_market.get(raw_key)
        if rows is None:
            continue
        if not isinstance(rows, list) or len(rows) > 1200:
            raise ValueError(f"Historique MT5 {canonical} invalide")
        seconds = GRANULARITY_SECONDS[canonical]
        parsed = []
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError(f"Bougie MT5 {canonical} invalide")
            opened_at = _wire_time(item.get("time"))
            values = {}
            for field in ("open", "high", "low", "close", "volume"):
                try:
                    value = float(item.get(field) or 0)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Valeur MT5 {canonical}/{field} invalide") from error
                if not math.isfinite(value):
                    raise ValueError(f"Valeur MT5 {canonical}/{field} invalide")
                values[field] = value
            if values["low"] > min(values["open"], values["close"], values["high"]):
                raise ValueError(f"OHLC MT5 {canonical} incohérent")
            if values["high"] < max(values["open"], values["close"], values["low"]):
                raise ValueError(f"OHLC MT5 {canonical} incohérent")
            parsed.append({
                "time": opened_at,
                "time_close": opened_at + timedelta(seconds=seconds),
                **values,
            })
        parsed.sort(key=lambda item: item["time"])
        deduplicated = {item["time"]: item for item in parsed}
        parsed_market[canonical] = list(deduplicated.values())
    if any(len(parsed_market.get(key) or []) < 60 for key in ("M15", "H1", "H4", "D", "W")):
        raise ValueError("Données MT5 multi-timeframes incomplètes")
    return parsed_market


def build_mt5_snapshot_from_payload(raw):
    """Construit l'analyse depuis un export de bougies OANDA MT5."""
    raw = raw if isinstance(raw, dict) else {}
    instrument = str(raw.get("instrument") or raw.get("symbol") or "GOLD.pro").strip()[:40]
    normalized = "".join(char for char in instrument.upper() if char.isalnum())
    if "XAU" not in normalized and "GOLD" not in normalized:
        raise ValueError("Le pont MT5 doit être attaché au graphique GOLD/XAUUSD")
    return build_snapshot(
        market_from_mt5_payload(raw.get("market")),
        instrument=instrument,
        local_timezone=str(raw.get("timezone") or os.environ.get("MARKET_AGENT_TIMEZONE", "Europe/Paris")),
        source="standalone_mt5",
        tickerid_prefix="MT5",
        volume_source="OANDA MT5 tick count",
    )
