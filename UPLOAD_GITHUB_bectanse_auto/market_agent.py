"""Agent XAUUSD M15 piloté par les données exactes du Pine Script.

Le module garde les prix et le verdict sous contrôle déterministe. L'IA ne sert
qu'à résumer le contexte : elle ne peut ni créer ni modifier une zone, une
entrée, un stop ou un objectif.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


PARIS_TZ = ZoneInfo("Europe/Paris")
MARKET_AGENT_TIMEFRAMES = ("M15", "H1", "H4", "D", "W")
MARKET_AGENT_TIMEFRAME_LABELS = {
    "M15": "M15",
    "H1": "H1",
    "H4": "H4",
    "D": "Daily",
    "W": "Weekly",
}


def ensure_market_agent_schema(conn):
    conn.run(
        """
        CREATE TABLE IF NOT EXISTS market_agent_events (
            id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL UNIQUE,
            ticker TEXT NOT NULL DEFAULT '',
            tickerid TEXT NOT NULL DEFAULT '',
            timeframe TEXT NOT NULL DEFAULT '',
            time_close BIGINT NOT NULL DEFAULT 0,
            event_type TEXT NOT NULL DEFAULT 'bar_close',
            agent_state TEXT NOT NULL DEFAULT 'WAITING',
            direction TEXT NOT NULL DEFAULT 'NEUTRE',
            rejection_status TEXT NOT NULL DEFAULT 'NON_CONFIRMÉ',
            meaningful BOOLEAN NOT NULL DEFAULT FALSE,
            published BOOLEAN NOT NULL DEFAULT FALSE,
            payload TEXT NOT NULL DEFAULT '{}',
            analysis TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            received_at TIMESTAMP NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMP
        )
        """
    )
    conn.run(
        """CREATE INDEX IF NOT EXISTS market_agent_events_ticker_time_idx
           ON market_agent_events (tickerid, timeframe, time_close DESC)"""
    )
    conn.run(
        """
        CREATE TABLE IF NOT EXISTS market_agent_reports (
            timeframe TEXT PRIMARY KEY,
            time_close BIGINT NOT NULL DEFAULT 0,
            price DOUBLE PRECISION,
            verdict TEXT NOT NULL DEFAULT 'ATTENTE',
            report TEXT NOT NULL DEFAULT '',
            photo_file_id TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "oui", "on"}


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _text(value, limit=240):
    return str(value or "").strip()[:limit]


def _clean_markdown(value):
    text = _text(value, 500)
    for char in ("*", "_", "`", "["):
        text = text.replace(char, "")
    return text


def normalize_market_agent_timeframe(value):
    """Retourne le code canonique utilisé par le menu Telegram."""
    timeframe = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "15": "M15", "15M": "M15", "M15": "M15",
        "60": "H1", "1H": "H1", "H1": "H1",
        "240": "H4", "4H": "H4", "H4": "H4",
        "1D": "D", "D1": "D", "DAILY": "D", "JOURNALIER": "D", "D": "D",
        "1W": "W", "W1": "W", "WEEKLY": "W", "HEBDO": "W", "W": "W",
    }
    return aliases.get(timeframe, "")


def market_agent_keyboard(selected="M15"):
    """Menu inline réservé au bot GOLD, indépendant des bots de l'Académie."""
    selected = normalize_market_agent_timeframe(selected) or "M15"

    def button(timeframe):
        label = MARKET_AGENT_TIMEFRAME_LABELS[timeframe]
        if timeframe == selected:
            label = f"• {label} •"
        return {"text": label, "callback_data": f"gold_tf:{timeframe}"}

    return {
        "inline_keyboard": [
            [button("M15"), button("H1"), button("H4")],
            [button("D"), button("W")],
            [
                {"text": "🔄 Actualiser", "callback_data": f"gold_refresh:{selected}"},
                {"text": "📋 Menu", "callback_data": "gold_menu"},
            ],
        ]
    }


def normalize_payload(raw):
    """Normalise le contrat Pine sans recalculer les niveaux envoyés."""
    raw = raw if isinstance(raw, dict) else {}
    ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), dict) else {}
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    mtf = raw.get("mtf") if isinstance(raw.get("mtf"), dict) else {}
    structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    rejection = raw.get("rejection") if isinstance(raw.get("rejection"), dict) else {}
    zones = raw.get("zones") if isinstance(raw.get("zones"), dict) else {}
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    flow = raw.get("flow") if isinstance(raw.get("flow"), dict) else {}
    trends = raw.get("trends") if isinstance(raw.get("trends"), dict) else {}

    def zone(name):
        item = zones.get(name) if isinstance(zones.get(name), dict) else {}
        return {
            "low": _num(item.get("low")),
            "high": _num(item.get("high")),
            "active": _bool(item.get("active")),
            "bullish": _bool(item.get("bullish")),
        }

    return {
        "schema_version": _text(raw.get("schema_version") or "1.0", 12),
        "source": _text(raw.get("source") or "tradingview", 30),
        "event_type": _text(raw.get("event_type") or "bar_close", 40).lower(),
        "ticker": _text(raw.get("ticker") or raw.get("symbol"), 40).upper(),
        "tickerid": _text(raw.get("tickerid") or raw.get("ticker") or raw.get("symbol"), 80).upper(),
        "timeframe": _text(raw.get("timeframe"), 12).upper(),
        "time_close": _int(raw.get("time_close")),
        "state": _text(raw.get("state"), 80),
        "profile": _text(raw.get("profile"), 40),
        "verdict": _text(raw.get("verdict"), 80),
        "bias": _text(raw.get("bias"), 80),
        "setup": _text(raw.get("setup"), 100),
        "trade_quality": _text(raw.get("trade_quality"), 80),
        "market_regime": _text(raw.get("market_regime"), 80),
        "wyckoff_phase": _text(raw.get("wyckoff_phase"), 80),
        "volatility_regime": _text(raw.get("volatility_regime"), 80),
        "signal_age": _int(raw.get("signal_age")),
        "signal_age_text": _text(raw.get("signal_age_text"), 80),
        "no_trade": _bool(raw.get("no_trade")),
        "no_trade_reason": _text(raw.get("no_trade_reason"), 180),
        "ohlc": {
            "open": _num(ohlc.get("open")), "high": _num(ohlc.get("high")),
            "low": _num(ohlc.get("low")), "close": _num(ohlc.get("close")),
            "atr": _num(ohlc.get("atr")),
        },
        "scores": {
            "long": _int(scores.get("long")), "short": _int(scores.get("short")),
            "required_long": _int(scores.get("required_long")),
            "required_short": _int(scores.get("required_short")),
            "checks": _int(scores.get("checks")),
            "confluence": _int(scores.get("confluence")),
            "context_long": _int(scores.get("context_long")),
            "context_short": _int(scores.get("context_short")),
            "structure_long": _int(scores.get("structure_long")),
            "structure_short": _int(scores.get("structure_short")),
            "location_long": _int(scores.get("location_long")),
            "location_short": _int(scores.get("location_short")),
            "flow_long": _int(scores.get("flow_long")),
            "flow_short": _int(scores.get("flow_short")),
            "trigger_long": _int(scores.get("trigger_long")),
            "trigger_short": _int(scores.get("trigger_short")),
        },
        "signals": {
            "long": _bool(raw.get("long_signal")), "short": _bool(raw.get("short_signal")),
            "pre_long": _bool(raw.get("pre_long")), "pre_short": _bool(raw.get("pre_short")),
            "trade_active": _bool(raw.get("trade_active")),
            "trade_side": _text(raw.get("trade_side"), 12).upper(),
        },
        "mtf": {
            "high_bull": _bool(mtf.get("high_bull")), "mid_bull": _bool(mtf.get("mid_bull")),
            "trend_bull": _bool(mtf.get("trend_bull")), "long": _bool(mtf.get("long")),
            "short": _bool(mtf.get("short")),
        },
        "trends": {
            "D": _text(trends.get("D") or "NEUTRE", 20).upper(),
            "H4": _text(trends.get("H4") or "NEUTRE", 20).upper(),
            "H1": _text(trends.get("H1") or "NEUTRE", 20).upper(),
            "M15": _text(trends.get("M15") or "NEUTRE", 20).upper(),
        },
        "structure": {
            "bullish": _bool(structure.get("bullish")), "bos": _bool(structure.get("bos")),
            "bos_up": _bool(structure.get("bos_up")), "choch": _bool(structure.get("choch")),
            "bos_price": _num(structure.get("bos_price")),
            "last_high": _num(structure.get("last_high")), "last_low": _num(structure.get("last_low")),
            "hh": _bool(structure.get("hh")), "hl": _bool(structure.get("hl")),
            "lh": _bool(structure.get("lh")), "ll": _bool(structure.get("ll")),
        },
        "rejection": {
            "bullish_candle": _bool(rejection.get("bullish_candle")),
            "bearish_candle": _bool(rejection.get("bearish_candle")),
            "sweep_low": _bool(rejection.get("sweep_low")),
            "sweep_high": _bool(rejection.get("sweep_high")),
            "institutional_long": _bool(rejection.get("institutional_long")),
            "institutional_short": _bool(rejection.get("institutional_short")),
        },
        "zones": {
            "ote": zone("ote"), "order_block": zone("order_block"), "fvg": zone("fvg"),
            "liquidity": zone("liquidity"), "no_trade": zone("no_trade"),
            "buy_side_liquidity": zone("buy_side_liquidity"),
            "sell_side_liquidity": zone("sell_side_liquidity"),
            "support": zone("support"), "resistance": zone("resistance"),
            "supply": zone("supply"), "demand": zone("demand"),
            "previous_day": zone("previous_day"), "previous_week": zone("previous_week"),
            "asia": zone("asia"), "london_or": zone("london_or"), "comex_or": zone("comex_or"),
        },
        "location": {
            "discount": _bool(raw.get("discount")), "premium": _bool(raw.get("premium")),
            "in_ote": _bool(raw.get("in_ote")), "in_ob_bull": _bool(raw.get("in_ob_bull")),
            "in_ob_bear": _bool(raw.get("in_ob_bear")), "in_fvg_bull": _bool(raw.get("in_fvg_bull")),
            "in_fvg_bear": _bool(raw.get("in_fvg_bear")),
        },
        "risk": {
            "entry": _num(risk.get("entry")), "stop": _num(risk.get("stop")),
            "tp1": _num(risk.get("tp1")), "tp2": _num(risk.get("tp2")),
            "tp3": _num(risk.get("tp3")), "dynamic_stop": _num(risk.get("dynamic_stop")),
            "preventive": _bool(risk.get("preventive")),
            "optimal_entry": _num(risk.get("optimal_entry")),
            "lot_size": _num(risk.get("lot_size")),
            "risk_dollars": _num(risk.get("risk_dollars")),
            "rr": _num(risk.get("rr")),
            "reward_tp1": _num(risk.get("reward_tp1")),
            "reward_tp2": _num(risk.get("reward_tp2")),
            "reward_tp3": _num(risk.get("reward_tp3")),
            "sl_status": _text(risk.get("sl_status"), 60),
            "effective_risk_pct": _num(risk.get("effective_risk_pct")),
            "daily_drawdown_pct": _num(risk.get("daily_drawdown_pct")),
        },
        "flow": {
            "volume_source": _text(flow.get("volume_source"), 60),
            "relative_volume": _num(flow.get("relative_volume")),
            "delta_ratio": _num(flow.get("delta_ratio")),
            "delta_positive": _bool(flow.get("delta_positive")),
            "delta_negative": _bool(flow.get("delta_negative")),
            "macro_bull": _bool(flow.get("macro_bull")), "macro_bear": _bool(flow.get("macro_bear")),
            "macro_data_count": _int(flow.get("macro_data_count")),
        },
        "session": _text(raw.get("session"), 40),
        "in_session": _bool(raw.get("in_session")),
        "in_kill_zone": _bool(raw.get("in_kill_zone")),
        "chart_image_url": _text(raw.get("chart_image_url"), 600),
    }


def classify_snapshot(snapshot):
    sig = snapshot["signals"]
    rej = snapshot["rejection"]
    structure = snapshot["structure"]
    event_type = snapshot["event_type"]

    if event_type in {"trade_closed", "closed"}:
        state = "CLOSED"
    elif event_type == "tp2":
        state = "TP2"
    elif event_type == "tp1":
        state = "TP1"
    elif snapshot["no_trade"]:
        state = "NO_TRADE"
    elif event_type in {"long_signal", "short_signal"} or sig["long"] or sig["short"]:
        state = "CONFIRMED"
    elif sig["trade_active"]:
        state = "ACTIVE"
    elif sig["pre_long"] or sig["pre_short"]:
        state = "ARMED"
    else:
        state = "WATCHING"

    if sig["long"] or sig["pre_long"] or sig["trade_side"] == "LONG":
        direction = "BUY"
    elif sig["short"] or sig["pre_short"] or sig["trade_side"] == "SHORT":
        direction = "SELL"
    elif snapshot["mtf"]["long"]:
        direction = "BUY"
    elif snapshot["mtf"]["short"]:
        direction = "SELL"
    else:
        direction = "NEUTRE"

    bull_candle = rej["bullish_candle"]
    bear_candle = rej["bearish_candle"]
    bull_sweep = rej["sweep_low"] or rej["institutional_long"]
    bear_sweep = rej["sweep_high"] or rej["institutional_short"]
    bull_follow = sig["long"] or (structure["bos"] and structure["bos_up"])
    bear_follow = sig["short"] or (structure["bos"] and not structure["bos_up"])
    if bull_candle and bull_sweep and bull_follow:
        rejection = "REJET HAUSSIER CONFIRMÉ"
    elif bear_candle and bear_sweep and bear_follow:
        rejection = "REJET BAISSIER CONFIRMÉ"
    elif bull_candle and bull_sweep:
        rejection = "REJET HAUSSIER POTENTIEL"
    elif bear_candle and bear_sweep:
        rejection = "REJET BAISSIER POTENTIEL"
    elif bull_candle or bear_candle:
        rejection = "BOUGIE DE REJET SEULE — NON CONFIRMÉE"
    elif bull_sweep or bear_sweep:
        rejection = "SWEEP SEUL — NON CONFIRMÉ"
    else:
        rejection = "AUCUN REJET CONFIRMÉ"

    return {"state": state, "direction": direction, "rejection": rejection}


def is_meaningful_change(current, previous):
    if not previous:
        return True
    current_view = classify_snapshot(current)
    previous_view = classify_snapshot(previous)
    if current_view != previous_view:
        return True
    if current["event_type"] != "bar_close":
        return True
    for side in ("long", "short"):
        if current["scores"][side] != previous["scores"][side]:
            return True
    for flag in ("bos", "choch"):
        if current["structure"][flag] != previous["structure"][flag]:
            return True
    return False


def _fmt_price(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ")


def _fmt_zone(zone):
    low, high = zone.get("low"), zone.get("high")
    if low is None and high is None:
        return "—"
    if low is None:
        return _fmt_price(high)
    if high is None:
        return _fmt_price(low)
    low, high = min(low, high), max(low, high)
    return _fmt_price(low) if abs(high - low) < 0.005 else f"{_fmt_price(low)}–{_fmt_price(high)}"


def _zone_lines(snapshot):
    labels = [
        ("OTE", "ote"), ("Order Block", "order_block"), ("FVG", "fvg"),
        ("Buy-Side Liquidity", "buy_side_liquidity"),
        ("Sell-Side Liquidity", "sell_side_liquidity"),
        ("Support", "support"), ("Résistance", "resistance"),
        ("Supply", "supply"), ("Demand", "demand"),
        ("Liquidité basse/haute", "liquidity"), ("Range veille", "previous_day"),
        ("Range semaine précédente", "previous_week"), ("Range Asie", "asia"),
        ("OR Londres", "london_or"), ("OR COMEX", "comex_or"),
        ("No Trade Zone", "no_trade"),
    ]
    lines = []
    for label, key in labels:
        item = snapshot["zones"][key]
        if item["low"] is not None or item["high"] is not None:
            marker = " ← prix dans la zone" if item.get("active") else ""
            orientation = ""
            if key in {"ote", "order_block", "fvg"}:
                orientation = " · haussière" if item.get("bullish") else " · baissière"
            lines.append(f"• {label} : {_fmt_zone(item)}{orientation}{marker}")
    return lines


def _confirmation_text(snapshot, view):
    structure = snapshot["structure"]
    if view["direction"] == "BUY":
        parts = ["clôture M15 défendue au-dessus de la zone"]
        parts.append("BOS haussier" if structure["bos"] and structure["bos_up"] else "BOS/CHOCH haussier encore attendu")
    elif view["direction"] == "SELL":
        parts = ["clôture M15 rejetée sous la zone"]
        parts.append("BOS baissier" if structure["bos"] and not structure["bos_up"] else "BOS/CHOCH baissier encore attendu")
    else:
        parts = ["sortie du range puis retest défendu", "BOS/CHOCH dans le sens de la cassure"]
    return " + ".join(parts)


def _ai_commentary(snapshot, view, api_key, model):
    if not api_key:
        return ""
    prompt = (
        "Tu es le rédacteur de marché XAUUSD de Bectanse Académie. Résume en français "
        "le contexte en 2 phrases courtes, factuelles et conditionnelles. Interdiction de "
        "créer un niveau, une probabilité de gain ou de contredire le verdict déterministe. "
        "Le score /15 est une confluence, pas une probabilité. Données exactes :\n"
        + json.dumps({"verdict": view, "snapshot": snapshot}, ensure_ascii=False)
    )
    content = [{"type": "input_text", "text": prompt}]
    image_url = snapshot.get("chart_image_url")
    if image_url and image_url.startswith(("https://", "data:image/")):
        content.append({"type": "input_image", "image_url": image_url, "detail": "high"})
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 220,
            "input": [{"role": "user", "content": content}],
        },
        timeout=45,
    )
    response.raise_for_status()
    body = response.json()
    result = body.get("output_text", "")
    if not result:
        for item in body.get("output", []):
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    result += part.get("text", "")
    return _clean_markdown(result)[:700]


def format_telegram_message(snapshot, view=None, ai_comment=""):
    view = view or classify_snapshot(snapshot)
    score = snapshot["scores"]
    structure = snapshot["structure"]
    risk = snapshot["risk"]
    flow = snapshot["flow"]
    close = snapshot["ohlc"]["close"]
    try:
        closed_at = datetime.fromtimestamp(snapshot["time_close"] / 1000, tz=timezone.utc).astimezone(PARIS_TZ)
        time_label = closed_at.strftime("%d/%m/%Y %H:%M")
    except (ValueError, OSError, OverflowError):
        time_label = "clôture M15"

    direction_icon = {"BUY": "📈", "SELL": "📉", "NEUTRE": "⏳"}[view["direction"]]
    m15_trend = snapshot["trends"]["M15"]
    if m15_trend == "RANGE":
        structure_label = "range / neutre"
    else:
        structure_label = "haussière" if structure["bullish"] else "baissière"
    if structure["choch"]:
        structure_label += " · CHOCH détecté"
    elif structure["bos"]:
        structure_label += " · BOS " + ("haussier" if structure["bos_up"] else "baissier")
    swing_labels = [
        label for label, detected in (
            ("HH", structure["hh"]), ("HL", structure["hl"]),
            ("LH", structure["lh"]), ("LL", structure["ll"]),
        ) if detected
    ]
    swing_text = "/".join(swing_labels) if swing_labels else "aucun nouveau swing confirmé"

    mtf = snapshot["mtf"]
    mtf_label = (
        f"HTF {'↑' if mtf['high_bull'] else '↓'} · "
        f"MID {'↑' if mtf['mid_bull'] else '↓'} · "
        f"Tendance {'↑' if mtf['trend_bull'] else '↓'}"
    )
    age_label = _clean_markdown(snapshot["signal_age_text"])
    if not age_label and snapshot["signal_age"] > 0:
        age_label = f"{snapshot['signal_age']} bougie(s)"

    display_timeframe = MARKET_AGENT_TIMEFRAME_LABELS.get(
        normalize_market_agent_timeframe(snapshot.get("timeframe")),
        snapshot.get("timeframe") or "M15",
    )
    lines = [
        f"*🥇 BECTANSE AUTO — {snapshot['ticker'] or 'XAUUSD'} {display_timeframe}*",
        f"🕒 Clôture analysée : {time_label} · Prix : {_fmt_price(close)}",
        "",
        "*📊 ÉTAT DU MARCHÉ*",
        f"État : *{view['state']}*",
        f"{direction_icon} Direction surveillée : *{view['direction']}*",
        f"⚙️ Profil : {_clean_markdown(snapshot['profile']) or '—'} · Signal : {age_label or 'aucun'}",
        f"🎯 Verdict moteur : {_clean_markdown(snapshot['verdict']) or 'ATTENTE'}",
        f"🧭 Biais : {_clean_markdown(snapshot['bias']) or 'NEUTRE'} · Régime : {_clean_markdown(snapshot['market_regime']) or '—'}",
        f"🗺 MTF : {mtf_label}",
        f"📐 Tendances : D {snapshot['trends']['D']} · H4 {snapshot['trends']['H4']} · H1 {snapshot['trends']['H1']} · M15 {snapshot['trends']['M15']}",
        f"🧩 Setup : {_clean_markdown(snapshot['setup']) or '—'} · Qualité : {_clean_markdown(snapshot['trade_quality']) or '—'}",
        f"🌊 Phase : {_clean_markdown(snapshot['wyckoff_phase']) or '—'} · Volatilité : {_clean_markdown(snapshot['volatility_regime']) or '—'}",
        f"🌍 Session : {_clean_markdown(snapshot['session']) or 'hors session'} · Kill Zone : {'oui' if snapshot['in_kill_zone'] else 'non'}",
        f"📊 Confluence : BUY {score['long']}/15 (seuil {score['required_long']}) · SELL {score['short']}/15 (seuil {score['required_short']})",
        f"🧮 Piliers BUY C/S/Z/F/T : {score['context_long']}/{score['structure_long']}/{score['location_long']}/{score['flow_long']}/{score['trigger_long']} · SELL : {score['context_short']}/{score['structure_short']}/{score['location_short']}/{score['flow_short']}/{score['trigger_short']}",
        "",
        "*🏗 STRUCTURE & REJET*",
        f"🏗 Structure : {structure_label}",
        f"📌 Swings : {swing_text} · dernier haut {_fmt_price(structure['last_high'])} · dernier bas {_fmt_price(structure['last_low'])}",
        f"🕯 Rejet : {view['rejection']}",
    ]
    if ai_comment:
        lines.extend(["", f"🧠 {ai_comment}"])

    zone_title = "calculées sur bougies clôturées" if snapshot.get("source", "").startswith("standalone_") else "exactes du moteur"
    lines.extend(["", f"*📍 ZONES CLASSÉES — {zone_title}*"])
    zone_lines = _zone_lines(snapshot)
    lines.extend(zone_lines or ["• Aucune zone exploitable transmise sur cette clôture."])

    lines.extend(["", "*🎯 SCÉNARIO PRINCIPAL*"])
    if snapshot["no_trade"]:
        reason = _clean_markdown(snapshot["no_trade_reason"]) or "conditions internes insuffisantes"
        lines.append(f"🚫 Pas d’entrée : {reason}.")
    elif view["state"] in {"CONFIRMED", "ACTIVE", "TP1", "TP2"} and risk["entry"] is not None:
        lines.extend([
            f"➡️ Entrée calculée : {_fmt_price(risk['entry'])}",
            f"🛑 Invalidation / SL : {_fmt_price(risk['dynamic_stop'] or risk['stop'])}",
            f"✅ TP1 : {_fmt_price(risk['tp1'])} · TP2 : {_fmt_price(risk['tp2'])} · TP3 : {_fmt_price(risk['tp3'])}",
        ])
    else:
        lines.append(f"⏱ Confirmation requise : {_confirmation_text(snapshot, view)}.")
        if risk["entry"] is not None:
            lines.extend([
                f"➡️ Niveau préventif : {_fmt_price(risk['entry'])}",
                f"🛑 Invalidité : {_fmt_price(risk['stop'])}",
                f"🎯 Cibles : {_fmt_price(risk['tp1'])} / {_fmt_price(risk['tp2'])} / {_fmt_price(risk['tp3'])}",
            ])

    lines.extend([
        "",
        "*🔁 SCÉNARIO ALTERNATIF*",
        "Il ne devient valide qu'après l'invalidation du scénario principal et un BOS/CHOCH confirmé dans le sens opposé.",
    ])

    location_flags = []
    for label, active in (
        ("discount", snapshot["location"]["discount"]),
        ("premium", snapshot["location"]["premium"]),
        ("OTE", snapshot["location"]["in_ote"]),
        ("OB bull", snapshot["location"]["in_ob_bull"]),
        ("OB bear", snapshot["location"]["in_ob_bear"]),
        ("FVG bull", snapshot["location"]["in_fvg_bull"]),
        ("FVG bear", snapshot["location"]["in_fvg_bear"]),
    ):
        if active:
            location_flags.append(label)
    lines.extend(["", "*🧭 LOCALISATION & RISQUE*"])
    lines.append(f"📍 Localisation : {', '.join(location_flags) if location_flags else 'hors zone institutionnelle active'}")
    if risk["optimal_entry"] is not None:
        lines.append(f"💎 Entrée optimale moteur : {_fmt_price(risk['optimal_entry'])}")
    if risk["lot_size"] is not None or risk["risk_dollars"] is not None or risk["rr"] is not None:
        rr_label = "—" if risk["rr"] is None else f"1:{risk['rr']:.1f}"
        lot_label = "—" if risk["lot_size"] is None else f"{risk['lot_size']:.2f} lot"
        risk_label = "—" if risk["risk_dollars"] is None else f"{risk['risk_dollars']:.2f} $"
        lines.append(f"🛡 Risque moteur : {lot_label} · {risk_label} · RR {rr_label} · SL {_clean_markdown(risk['sl_status']) or '—'}")
    if risk["effective_risk_pct"] is not None or risk["daily_drawdown_pct"] is not None:
        effective = "—" if risk["effective_risk_pct"] is None else f"{risk['effective_risk_pct']:.2f}%"
        drawdown = "—" if risk["daily_drawdown_pct"] is None else f"{risk['daily_drawdown_pct']:.2f}%"
        lines.append(f"📐 Risque effectif : {effective} · Drawdown jour : {drawdown}")
    if any(risk[key] is not None for key in ("reward_tp1", "reward_tp2", "reward_tp3")):
        rewards = ["—" if risk[key] is None else f"{risk[key]:.0f}$" for key in ("reward_tp1", "reward_tp2", "reward_tp3")]
        lines.append(f"💰 Gains bruts TP1/2/3 : {' / '.join(rewards)}")

    delta = flow["delta_ratio"]
    if delta is not None or flow["volume_source"] or flow["macro_bull"] or flow["macro_bear"]:
        flow_side = "acheteurs" if delta is not None and delta > 0 else "vendeurs" if delta is not None and delta < 0 else "neutre"
        volume_source = _clean_markdown(flow["volume_source"]) or "volume"
        macro = "achat" if flow["macro_bull"] else "vente" if flow["macro_bear"] else "mixte"
        delta_label = "—" if delta is None else f"{delta * 100:.0f}%"
        rv_label = "—" if flow["relative_volume"] is None else f"{flow['relative_volume']:.2f}"
        lines.extend(["", "*📦 FLUX*", f"{volume_source} · RV {rv_label} · delta {delta_label} ({flow_side}) · macro {macro}"])
    lines.extend(["", "ℹ️ Le score /15 mesure la confluence technique, pas une probabilité de gain."])
    return "\n".join(lines)[:4050]


def _event_key(snapshot):
    raw = f"{snapshot['tickerid']}|{snapshot['timeframe']}|{snapshot['time_close']}|{snapshot['event_type']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def register_market_agent(app, get_conn, send_telegram, openai_api_key="", default_model="gpt-5.6-luna"):
    from flask import jsonify, request

    webhook_enabled = _bool(os.environ.get("MARKET_AGENT_ENABLED", "false"))
    mt5_bridge_enabled = _bool(os.environ.get("MARKET_AGENT_MT5_ENABLED", "false"))
    standalone_enabled = _bool(os.environ.get("MARKET_AGENT_STANDALONE_ENABLED", "false"))
    enabled = webhook_enabled or mt5_bridge_enabled or standalone_enabled
    webhook_secret = os.environ.get("MARKET_AGENT_WEBHOOK_SECRET", "").strip()
    chat_id = os.environ.get("MARKET_AGENT_TELEGRAM_CHAT_ID", "").strip()
    publish_every_close = _bool(os.environ.get("MARKET_AGENT_PUBLISH_EVERY_CLOSE", "true"))
    ai_enabled = _bool(os.environ.get("MARKET_AGENT_AI_ENABLED", "true"))
    model = os.environ.get("MARKET_AGENT_MODEL", default_model).strip() or default_model
    report_secret = os.environ.get("MARKET_AGENT_REPORT_SECRET", "").strip()
    bot_token = os.environ.get("MARKET_AGENT_BOT_TOKEN", "").strip()
    bot_webhook_secret = os.environ.get("MARKET_AGENT_TELEGRAM_WEBHOOK_SECRET", "").strip()
    bot_public_base_url = (
        os.environ.get("MARKET_AGENT_PUBLIC_BASE_URL", "").strip()
        or "https://acces.bectanse-academie.com"
    ).rstrip("/")
    bot_webhook_path = "/api/market-agent/telegram"
    standalone_lock = threading.Lock()
    standalone_error_alerted_at = 0.0

    def telegram_api(method, payload):
        if not bot_token:
            return {"ok": False, "description": "bot dédié non configuré"}
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/{method}",
            json=payload,
            timeout=20,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"ok": False, "description": "réponse Telegram illisible"}
        if not response.ok or not body.get("ok"):
            app.logger.warning(
                "Telegram agent GOLD %s: %s",
                method,
                body.get("description", f"HTTP {response.status_code}"),
            )
        return body

    def upsert_report(conn, timeframe, report_data):
        timeframe = normalize_market_agent_timeframe(timeframe)
        if not timeframe:
            raise ValueError("Timeframe non pris en charge")
        report_data = report_data if isinstance(report_data, dict) else {"text": report_data}
        report = str(report_data.get("text") or report_data.get("report") or "").strip()
        if not report:
            raise ValueError(f"Rapport {timeframe} vide")
        if len(report) > 4000:
            raise ValueError(f"Rapport {timeframe} trop long")
        time_close = _int(report_data.get("time_close"))
        price = _num(report_data.get("price"))
        verdict = _text(report_data.get("verdict") or "ATTENTE", 30).upper()
        photo_file_id = _text(report_data.get("photo_file_id"), 300)
        conn.run(
            """
            INSERT INTO market_agent_reports
                (timeframe,time_close,price,verdict,report,photo_file_id,updated_at)
            VALUES
                (:timeframe,:time_close,:price,:verdict,:report,:photo_file_id,NOW())
            ON CONFLICT (timeframe) DO UPDATE SET
                time_close=EXCLUDED.time_close,
                price=EXCLUDED.price,
                verdict=EXCLUDED.verdict,
                report=EXCLUDED.report,
                photo_file_id=CASE
                    WHEN EXCLUDED.photo_file_id <> '' THEN EXCLUDED.photo_file_id
                    ELSE market_agent_reports.photo_file_id
                END,
                updated_at=NOW()
            WHERE EXCLUDED.time_close >= market_agent_reports.time_close
            """,
            timeframe=timeframe,
            time_close=time_close,
            price=price,
            verdict=verdict,
            report=report,
            photo_file_id=photo_file_id,
        )
        return timeframe

    def latest_report(timeframe):
        timeframe = normalize_market_agent_timeframe(timeframe)
        if not timeframe:
            return None
        conn = get_conn()
        try:
            rows = conn.run(
                """SELECT timeframe,time_close,price,verdict,report,photo_file_id,updated_at
                   FROM market_agent_reports WHERE timeframe=:timeframe LIMIT 1""",
                timeframe=timeframe,
            )
        finally:
            conn.close()
        if not rows:
            return None
        keys = ("timeframe", "time_close", "price", "verdict", "report", "photo_file_id", "updated_at")
        return dict(zip(keys, rows[0]))

    def send_report(timeframe, target_chat):
        timeframe = normalize_market_agent_timeframe(timeframe) or "M15"
        cached = latest_report(timeframe)
        keyboard = market_agent_keyboard(timeframe)
        if not cached:
            label = MARKET_AGENT_TIMEFRAME_LABELS[timeframe]
            return send_telegram(
                f"*🥇 XAUUSD — {label}*\n\n"
                "⏳ L’analyse de cette unité est en préparation. "
                "Elle sera disponible après le prochain relevé sur bougies clôturées.",
                reply_markup=keyboard,
                chat_id=target_chat,
            )
        return send_telegram(
            cached["report"],
            reply_markup=keyboard,
            chat_id=target_chat,
        )

    def send_menu(target_chat):
        return send_telegram(
            "*🥇 AGENT GOLD — MENU*\n\n"
            "Choisis l’unité de temps à consulter. Le robot affiche uniquement "
            "la dernière bougie entièrement clôturée, jamais la bougie en formation.\n\n"
            "• M15 : exécution et rejet\n"
            "• H1 : structure intraday\n"
            "• H4 : direction de session\n"
            "• Daily : biais journalier\n"
            "• Weekly : contexte de fond",
            reply_markup=market_agent_keyboard("M15"),
            chat_id=target_chat,
        )

    def register_telegram_webhook():
        if not bot_token or not bot_webhook_secret:
            app.logger.warning("Webhook Telegram agent GOLD non configuré")
            return False
        result = telegram_api(
            "setWebhook",
            {
                "url": f"{bot_public_base_url}{bot_webhook_path}",
                "secret_token": bot_webhook_secret,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
            },
        )
        return bool(result.get("ok"))

    def process_event(event_id, snapshot):
        conn = None
        try:
            conn = get_conn()
            rows = conn.run(
                """SELECT payload FROM market_agent_events
                   WHERE tickerid=:tickerid AND timeframe=:timeframe AND id<>:id
                   ORDER BY time_close DESC, received_at DESC LIMIT 1""",
                tickerid=snapshot["tickerid"], timeframe=snapshot["timeframe"], id=event_id,
            )
            previous = normalize_payload(json.loads(rows[0][0])) if rows else None
            view = classify_snapshot(snapshot)
            meaningful = is_meaningful_change(snapshot, previous)
            ai_comment = ""
            if ai_enabled and openai_api_key:
                try:
                    ai_comment = _ai_commentary(snapshot, view, openai_api_key, model)
                except Exception as error:
                    app.logger.warning("Market agent IA indisponible: %s", error)
            message = format_telegram_message(snapshot, view, ai_comment)
            upsert_report(
                conn,
                snapshot["timeframe"],
                {
                    "text": message,
                    "time_close": snapshot["time_close"],
                    "price": snapshot["ohlc"]["close"],
                    "verdict": view["direction"],
                },
            )
            should_publish = publish_every_close or meaningful or snapshot["event_type"] != "bar_close"
            published = bool(
                should_publish
                and send_telegram(
                    message,
                    reply_markup=market_agent_keyboard(snapshot["timeframe"]),
                    chat_id=chat_id or None,
                )
            )
            conn.run(
                """UPDATE market_agent_events SET agent_state=:state, direction=:direction,
                   rejection_status=:rejection, meaningful=:meaningful, published=:published,
                   analysis=:analysis, processed_at=NOW() WHERE id=:id""",
                state=view["state"], direction=view["direction"], rejection=view["rejection"],
                meaningful=meaningful, published=published, analysis=message, id=event_id,
            )
        except Exception as error:
            app.logger.exception("Traitement market agent: %s", error)
            try:
                if conn is None:
                    conn = get_conn()
                conn.run(
                    "UPDATE market_agent_events SET error=:error, processed_at=NOW() WHERE id=:id",
                    error=str(error)[:1000], id=event_id,
                )
            except Exception:
                pass
        finally:
            if conn is not None:
                conn.close()

    def submit_snapshot(raw, asynchronous=True):
        snapshot = normalize_payload(raw)
        if not any(token in snapshot["tickerid"] for token in ("XAU", "GOLD", "GC1!", "MGC1!")):
            raise ValueError("Seul GOLD/XAUUSD est accepté")
        if snapshot["timeframe"] not in {"15", "15M", "M15"}:
            raise ValueError("Le timeframe doit être M15")
        if snapshot["time_close"] <= 0 or snapshot["ohlc"]["close"] is None:
            raise ValueError("Clôture M15 incomplète")

        event_id = str(uuid.uuid4())
        event_key = _event_key(snapshot)
        conn = get_conn()
        try:
            inserted = conn.run(
                """INSERT INTO market_agent_events
                   (id,event_key,ticker,tickerid,timeframe,time_close,event_type,payload)
                   VALUES (:id,:event_key,:ticker,:tickerid,:timeframe,:time_close,:event_type,:payload)
                   ON CONFLICT (event_key) DO NOTHING RETURNING id""",
                id=event_id, event_key=event_key, ticker=snapshot["ticker"], tickerid=snapshot["tickerid"],
                timeframe=snapshot["timeframe"], time_close=snapshot["time_close"],
                event_type=snapshot["event_type"], payload=json.dumps(snapshot, ensure_ascii=False),
            )
        finally:
            conn.close()
        if not inserted:
            return {"ok": True, "duplicate": True}
        if asynchronous:
            threading.Thread(target=process_event, args=(event_id, snapshot), daemon=True).start()
        else:
            process_event(event_id, snapshot)
        return {"ok": True, "accepted": True, "event_id": event_id}

    def run_standalone_once():
        nonlocal standalone_error_alerted_at
        if not standalone_enabled:
            return {"ok": False, "disabled": True}
        if not standalone_lock.acquire(blocking=False):
            return {"ok": False, "busy": True}
        try:
            from standalone_market_agent import build_standalone_snapshot_from_env

            result = submit_snapshot(build_standalone_snapshot_from_env(), asynchronous=False)
            app.logger.info("Market agent autonome: %s", result)
            return result
        except Exception as error:
            import time

            app.logger.exception("Market agent autonome: %s", error)
            if time.time() - standalone_error_alerted_at >= 6 * 60 * 60:
                standalone_error_alerted_at = time.time()
                send_telegram(
                    "🚨 *AGENT GOLD AUTONOME*\n\n"
                    "Le dernier relevé M15 n'a pas pu être analysé. "
                    f"Erreur : `{str(error)[:500]}`\n\n"
                    "Une nouvelle tentative aura lieu à la prochaine clôture.",
                    chat_id=chat_id or None,
                )
            return {"ok": False, "error": str(error)}
        finally:
            standalone_lock.release()

    @app.post("/api/market-agent/tradingview")
    def market_agent_tradingview_webhook():
        if not webhook_enabled:
            return jsonify({"ok": False, "error": "Agent M15 désactivé"}), 503
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify({"ok": False, "error": "JSON TradingView invalide"}), 400
        supplied = str(raw.get("secret") or "")
        if not webhook_secret or not hmac.compare_digest(supplied, webhook_secret):
            return jsonify({"ok": False, "error": "Signature invalide"}), 401
        try:
            result = submit_snapshot(raw, asynchronous=True)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 422
        return jsonify(result), 202 if result.get("accepted") else 200

    @app.post("/api/market-agent/mt5")
    def market_agent_mt5_webhook():
        if not mt5_bridge_enabled:
            return jsonify({"ok": False, "error": "Pont MT5 désactivé"}), 503
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify({"ok": False, "error": "Export MT5 invalide"}), 400
        supplied = str(raw.get("secret") or "")
        if not webhook_secret or not hmac.compare_digest(supplied, webhook_secret):
            return jsonify({"ok": False, "error": "Signature invalide"}), 401
        try:
            from standalone_market_agent import build_mt5_snapshot_from_payload

            snapshot = build_mt5_snapshot_from_payload(raw)
            result = submit_snapshot(snapshot, asynchronous=True)
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 422
        return jsonify(result), 202 if result.get("accepted") else 200

    @app.post("/api/market-agent/reports")
    def market_agent_reports_webhook():
        supplied = request.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:].strip()
        if not report_secret or not hmac.compare_digest(supplied, report_secret):
            return jsonify({"ok": False, "error": "Signature invalide"}), 401
        raw = request.get_json(silent=True)
        reports = raw.get("reports") if isinstance(raw, dict) else None
        if not isinstance(reports, dict) or not reports:
            return jsonify({"ok": False, "error": "Rapports absents"}), 400
        conn = get_conn()
        stored = []
        try:
            for timeframe, report_data in reports.items():
                stored.append(upsert_report(conn, timeframe, report_data))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 422
        finally:
            conn.close()
        return jsonify({"ok": True, "stored": stored})

    @app.post(bot_webhook_path)
    def market_agent_telegram_webhook():
        if not bot_token or not chat_id or not bot_webhook_secret:
            return jsonify({"ok": False, "error": "Bot dédié désactivé"}), 503
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied, bot_webhook_secret):
            return jsonify({"ok": False, "error": "Signature Telegram invalide"}), 401
        update = request.get_json(silent=True)
        if not isinstance(update, dict):
            return jsonify({"ok": False}), 400

        callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
        message = update.get("message") if isinstance(update.get("message"), dict) else None
        if callback:
            callback_id = str(callback.get("id") or "")
            callback_message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
            callback_chat = callback_message.get("chat") if isinstance(callback_message.get("chat"), dict) else {}
            target_chat = str(callback_chat.get("id") or "")
            if target_chat != str(chat_id):
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Accès réservé."})
                return jsonify({"ok": True})
            data = str(callback.get("data") or "")
            telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})
            if data == "gold_menu":
                send_menu(target_chat)
            elif data.startswith("gold_tf:") or data.startswith("gold_refresh:"):
                send_report(data.split(":", 1)[1], target_chat)
            return jsonify({"ok": True})

        if message:
            message_chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            target_chat = str(message_chat.get("id") or "")
            if target_chat != str(chat_id):
                return jsonify({"ok": True})
            command = str(message.get("text") or "").strip().split("@", 1)[0].lower()
            commands = {
                "/m15": "M15", "/h1": "H1", "/h4": "H4",
                "/daily": "D", "/d1": "D", "/weekly": "W", "/w1": "W",
            }
            if command in {"/start", "/menu", "/help"}:
                send_menu(target_chat)
            elif command in commands:
                send_report(commands[command], target_chat)
        return jsonify({"ok": True})

    return {
        "enabled": enabled,
        "webhook_enabled": webhook_enabled,
        "mt5_bridge_enabled": mt5_bridge_enabled,
        "standalone_enabled": standalone_enabled,
        "route": "/api/market-agent/tradingview",
        "mt5_route": "/api/market-agent/mt5",
        "reports_route": "/api/market-agent/reports",
        "telegram_route": bot_webhook_path,
        "telegram_bot_enabled": bool(bot_token and chat_id and bot_webhook_secret),
        "register_telegram_webhook": register_telegram_webhook,
        "run_standalone_once": run_standalone_once,
    }
