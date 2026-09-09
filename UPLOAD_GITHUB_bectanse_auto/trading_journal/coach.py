"""Bectanse Coach: deterministic behavioral analytics over verified trades.

The engine produces observations from measured data only. Natural-language AI
may rephrase these records later, but never supplies calculations or facts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from math import sqrt
from statistics import median
from zoneinfo import ZoneInfo

from .calculations import decimal_value, reconstruct_positions, validate_timezone


MIN_TRADES = 20
MIN_SEQUENCE_SAMPLES = 8
MIN_BUCKET_SAMPLES = 5
MIN_TELEMETRY_EVENTS = 5


def _number(value) -> float:
    return round(float(decimal_value(value)), 2)


def _confidence(sample: int, minimum: int, strength: float = 1.0) -> float:
    coverage = min(1.0, sample / max(minimum * 3, 1))
    return round(min(0.99, 0.55 + 0.4 * coverage * min(1.0, max(0.2, strength))), 2)


def _detector(pattern: str, severity: str, sample: int, impact: Decimal | float | None,
              evidence: dict, minimum: int, strength: float = 1.0) -> dict:
    return {"pattern": pattern, "confidence": _confidence(sample, minimum, strength),
            "severity": severity, "sample_size": sample,
            "impact": _number(impact) if impact is not None else None, "evidence": evidence}


def _session(hour: int) -> str:
    return "ASIA" if hour < 8 else "LONDON" if hour < 14 else "NEW_YORK" if hour < 22 else "OFF_HOURS"


def _holding(seconds: int) -> str:
    return "UNDER_15M" if seconds < 900 else "15M_1H" if seconds < 3600 else "1H_4H" if seconds < 14400 else "OVER_4H"


def _group_stats(trades: list[dict], key_function) -> dict[str, dict]:
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for trade in trades:
        groups[str(key_function(trade))].append(decimal_value(trade["net_pnl"]))
    return {key: {"trades": len(values), "net_pnl": _number(sum(values, Decimal("0"))),
                  "average_pnl": _number(sum(values, Decimal("0")) / len(values)),
                  "win_rate": round(100 * sum(1 for value in values if value > 0) / len(values), 2)}
            for key, values in groups.items()}


def detect_behavior(trades: list[dict], timezone_name: str = "Europe/Paris") -> list[dict]:
    """Return only statistically eligible patterns with their source evidence."""
    tz = ZoneInfo(validate_timezone(timezone_name))
    ordered = sorted(trades, key=lambda trade: trade["closed_at"])
    if len(ordered) < MIN_TRADES:
        return []
    detectors: list[dict] = []
    by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in ordered:
        by_day[trade["closed_at"].astimezone(tz).date().isoformat()].append(trade)
    counts = [len(items) for items in by_day.values()]
    baseline = median(counts) if counts else 0
    over_limit = max(5, int(baseline * 1.75 + 0.5))
    over_days = [items for items in by_day.values() if len(items) >= over_limit]
    if len(over_days) >= 3:
        over_pnl = sum((decimal_value(t["net_pnl"]) for items in over_days for t in items), Decimal("0"))
        regular = [t for items in by_day.values() if len(items) < over_limit for t in items]
        over_average = over_pnl / sum(len(items) for items in over_days)
        regular_average = (sum((decimal_value(t["net_pnl"]) for t in regular), Decimal("0")) / len(regular)) if regular else Decimal("0")
        if over_average < regular_average:
            detectors.append(_detector("OVERTRADING", "HIGH" if over_pnl < 0 else "MEDIUM",
                sum(len(items) for items in over_days), over_pnl,
                {"days": len(over_days), "threshold_trades_per_day": over_limit,
                 "overtrading_average_pnl": _number(over_average), "baseline_average_pnl": _number(regular_average)},
                MIN_BUCKET_SAMPLES, min(2, float(abs(over_average - regular_average) / (abs(regular_average) + Decimal("1"))))))

    after_loss = []
    after_win = []
    for previous, current in zip(ordered, ordered[1:]):
        gap = (current["opened_at"] - previous["closed_at"]).total_seconds()
        if 0 <= gap <= 3600:
            (after_loss if decimal_value(previous["net_pnl"]) < 0 else after_win if decimal_value(previous["net_pnl"]) > 0 else []).append((previous, current, gap))
    baseline_avg = sum((decimal_value(t["net_pnl"]) for t in ordered), Decimal("0")) / len(ordered)
    if len(after_loss) >= MIN_SEQUENCE_SAMPLES:
        pnl = sum((decimal_value(pair[1]["net_pnl"]) for pair in after_loss), Decimal("0"))
        avg = pnl / len(after_loss)
        size_increases = sum(1 for prev, nxt, _ in after_loss if decimal_value(nxt["volume"]) > decimal_value(prev["volume"]) * Decimal("1.25"))
        if avg < baseline_avg and (pnl < 0 or size_increases / len(after_loss) >= .4):
            detectors.append(_detector("OVERTRADING_AFTER_LOSS", "HIGH" if pnl < 0 else "MEDIUM",
                len(after_loss), pnl, {"window_minutes": 60, "average_pnl": _number(avg),
                "baseline_average_pnl": _number(baseline_avg), "position_size_increase_rate": round(size_increases / len(after_loss), 3)},
                MIN_SEQUENCE_SAMPLES))
    if len(after_win) >= MIN_SEQUENCE_SAMPLES:
        pnl = sum((decimal_value(pair[1]["net_pnl"]) for pair in after_win), Decimal("0"))
        avg = pnl / len(after_win)
        if avg < baseline_avg:
            detectors.append(_detector("PERFORMANCE_AFTER_WIN", "MEDIUM", len(after_win), pnl,
                {"window_minutes": 60, "average_pnl": _number(avg), "baseline_average_pnl": _number(baseline_avg)},
                MIN_SEQUENCE_SAMPLES))

    volumes = [float(decimal_value(t["volume"])) for t in ordered if decimal_value(t["volume"]) > 0]
    if len(volumes) >= 15 and sum(volumes) > 0:
        mean = sum(volumes) / len(volumes)
        coefficient = sqrt(sum((value - mean) ** 2 for value in volumes) / len(volumes)) / mean
        if coefficient > .6:
            largest = max(ordered, key=lambda t: decimal_value(t["volume"]))
            detectors.append(_detector("POSITION_SIZE_INCONSISTENCY", "HIGH" if coefficient > 1 else "MEDIUM",
                len(volumes), largest["net_pnl"], {"coefficient_of_variation": round(coefficient, 3),
                "average_volume": round(mean, 4), "maximum_volume": round(max(volumes), 4)}, 15))

    for streak_kind, predicate, pattern in (
        ("loss", lambda value: value < 0, "BEHAVIOR_AFTER_LOSS_STREAK"),
        ("win", lambda value: value > 0, "BEHAVIOR_AFTER_WIN_STREAK"),
    ):
        samples = []
        streak = 0
        for index, trade in enumerate(ordered[:-1]):
            streak = streak + 1 if predicate(decimal_value(trade["net_pnl"])) else 0
            if streak >= 3:
                samples.append(ordered[index + 1])
        if len(samples) >= MIN_BUCKET_SAMPLES:
            pnl = sum((decimal_value(t["net_pnl"]) for t in samples), Decimal("0"))
            average = pnl / len(samples)
            adverse = average < baseline_avg
            if adverse:
                detectors.append(_detector(pattern, "HIGH" if pnl < 0 else "MEDIUM", len(samples), pnl,
                    {"minimum_streak": 3, "average_next_trade_pnl": _number(average),
                     "baseline_average_pnl": _number(baseline_avg), "streak_type": streak_kind}, MIN_BUCKET_SAMPLES))

    dimensions = {
        "SESSION_PERFORMANCE": _group_stats(ordered, lambda t: _session(t["closed_at"].astimezone(tz).hour)),
        "WEEKDAY_PERFORMANCE": _group_stats(ordered, lambda t: t["closed_at"].astimezone(tz).strftime("%A").upper()),
        "SYMBOL_PERFORMANCE": _group_stats(ordered, lambda t: t["symbol"] or "UNKNOWN"),
        "HOLDING_TIME_PERFORMANCE": _group_stats(ordered, lambda t: _holding(t["duration_seconds"])),
    }
    for pattern, groups in dimensions.items():
        eligible = {key: value for key, value in groups.items() if value["trades"] >= MIN_BUCKET_SAMPLES}
        if len(eligible) < 2:
            continue
        best = max(eligible, key=lambda key: eligible[key]["average_pnl"])
        worst = min(eligible, key=lambda key: eligible[key]["average_pnl"])
        delta = eligible[best]["average_pnl"] - eligible[worst]["average_pnl"]
        if delta > max(1, abs(float(baseline_avg)) * .5):
            detectors.append(_detector(pattern, "MEDIUM", sum(row["trades"] for row in eligible.values()),
                eligible[worst]["net_pnl"], {"best": best, "worst": worst, "groups": eligible,
                "average_pnl_gap": round(delta, 2)}, MIN_BUCKET_SAMPLES * 2))
    return sorted(detectors, key=lambda row: ({"HIGH": 3, "MEDIUM": 2, "LOW": 1}[row["severity"]], row["confidence"]), reverse=True)


def detect_mt5_behavior(events: list[dict], trades: list[dict]) -> list[dict]:
    """Detect behavior visible in sampled MT5 position/order state changes."""
    closed = {(int(row.get("trading_account_id") or 0), int(row.get("position_id") or 0)): row
              for row in trades}
    position_opened = [row for row in events if row.get("event_type") == "POSITION_OPENED"]
    position_changes = [row for row in events if row.get("event_type") == "POSITION_CHANGED"]
    detectors: list[dict] = []

    without_sl = [row for row in position_opened
                  if decimal_value((row.get("current_state") or {}).get("sl")) <= 0]
    if len(position_opened) >= MIN_TELEMETRY_EVENTS and len(without_sl) >= MIN_TELEMETRY_EVENTS:
        affected = {(int(row.get("trading_account_id") or 0), int(row.get("entity_id") or 0))
                    for row in without_sl}
        measured = [closed[key] for key in affected if key in closed]
        impact = sum((decimal_value(row["net_pnl"]) for row in measured), Decimal("0")) if measured else None
        rate = len(without_sl) / len(position_opened)
        detectors.append(_detector(
            "POSITIONS_WITHOUT_STOP_LOSS", "HIGH" if rate >= .6 else "MEDIUM",
            len(position_opened), impact,
            {"positions_observed": len(position_opened), "positions_without_sl": len(without_sl),
             "rate": round(rate, 3), "closed_positions_measured": len(measured)},
            MIN_TELEMETRY_EVENTS, rate,
        ))

    sl_changes = [row for row in position_changes if "sl" in (row.get("changed_fields") or [])]
    if len(sl_changes) >= MIN_TELEMETRY_EVENTS:
        positions = len({(row.get("trading_account_id"), row.get("entity_id")) for row in sl_changes})
        detectors.append(_detector(
            "FREQUENT_STOP_LOSS_CHANGES", "HIGH" if len(sl_changes) >= 12 else "MEDIUM",
            len(sl_changes), None,
            {"changes": len(sl_changes), "positions_affected": positions,
             "average_changes_per_position": round(len(sl_changes) / max(positions, 1), 2)},
            MIN_TELEMETRY_EVENTS,
        ))

    widened = []
    for row in sl_changes:
        previous, current = row.get("previous_state") or {}, row.get("current_state") or {}
        old_sl, new_sl = decimal_value(previous.get("sl")), decimal_value(current.get("sl"))
        side = str(current.get("type") or previous.get("type") or "").upper()
        if old_sl > 0 and new_sl > 0 and ((side == "BUY" and new_sl < old_sl) or
                                         (side == "SELL" and new_sl > old_sl)):
            widened.append(row)
    if len(widened) >= MIN_TELEMETRY_EVENTS:
        detectors.append(_detector(
            "STOP_LOSS_WIDENING", "HIGH", len(widened), None,
            {"widening_events": len(widened),
             "positions_affected": len({row.get("entity_id") for row in widened})},
            MIN_TELEMETRY_EVENTS,
        ))

    tp_changes = [row for row in position_changes if "tp" in (row.get("changed_fields") or [])]
    if len(tp_changes) >= MIN_TELEMETRY_EVENTS:
        detectors.append(_detector(
            "FREQUENT_TAKE_PROFIT_CHANGES", "MEDIUM", len(tp_changes), None,
            {"changes": len(tp_changes),
             "positions_affected": len({row.get("entity_id") for row in tp_changes})},
            MIN_TELEMETRY_EVENTS,
        ))

    volume_changes = [row for row in position_changes if "volume" in (row.get("changed_fields") or [])]
    if len(volume_changes) >= MIN_TELEMETRY_EVENTS:
        detectors.append(_detector(
            "POSITION_SIZE_CHANGES", "MEDIUM", len(volume_changes), None,
            {"changes": len(volume_changes),
             "positions_affected": len({row.get("entity_id") for row in volume_changes})},
            MIN_TELEMETRY_EVENTS,
        ))

    winners = [row for row in trades if decimal_value(row["net_pnl"]) > 0]
    losers = [row for row in trades if decimal_value(row["net_pnl"]) < 0]
    if len(winners) >= MIN_BUCKET_SAMPLES and len(losers) >= MIN_BUCKET_SAMPLES:
        winner_median = median(row["duration_seconds"] for row in winners)
        loser_median = median(row["duration_seconds"] for row in losers)
        if loser_median >= max(3600, winner_median * 1.5):
            impact = sum((decimal_value(row["net_pnl"]) for row in losers), Decimal("0"))
            detectors.append(_detector(
                "LOSSES_HELD_TOO_LONG", "HIGH" if loser_median >= winner_median * 2 else "MEDIUM",
                len(winners) + len(losers), impact,
                {"winning_trades": len(winners), "losing_trades": len(losers),
                 "median_winner_minutes": round(winner_median / 60, 1),
                 "median_loser_minutes": round(loser_median / 60, 1),
                 "duration_ratio": round(loser_median / max(winner_median, 1), 2)},
                MIN_BUCKET_SAMPLES * 2,
            ))
    return detectors


def trading_score(trades: list[dict], detectors: list[dict]) -> dict:
    """Compute five deterministic 0-100 dimensions and their equal-weight score."""
    if len(trades) < MIN_TRADES:
        return {"available": False, "minimum_trades": MIN_TRADES, "sample_size": len(trades), "score": None, "components": {}}
    penalties = defaultdict(float)
    severity = {"HIGH": 22, "MEDIUM": 12, "LOW": 6}
    mapping = {
        "OVERTRADING": ("discipline", "consistency"), "OVERTRADING_AFTER_LOSS": ("discipline", "risk"),
        "POSITION_SIZE_INCONSISTENCY": ("risk", "consistency"), "BEHAVIOR_AFTER_LOSS_STREAK": ("discipline", "risk"),
        "BEHAVIOR_AFTER_WIN_STREAK": ("discipline", "consistency"), "PERFORMANCE_AFTER_WIN": ("discipline", "consistency"),
        "SESSION_PERFORMANCE": ("timing",), "WEEKDAY_PERFORMANCE": ("timing",), "SYMBOL_PERFORMANCE": ("execution",),
        "HOLDING_TIME_PERFORMANCE": ("execution", "timing"),
        "POSITIONS_WITHOUT_STOP_LOSS": ("risk", "discipline"),
        "FREQUENT_STOP_LOSS_CHANGES": ("risk", "discipline"),
        "STOP_LOSS_WIDENING": ("risk", "discipline"),
        "FREQUENT_TAKE_PROFIT_CHANGES": ("execution", "discipline"),
        "POSITION_SIZE_CHANGES": ("risk", "consistency"),
        "LOSSES_HELD_TOO_LONG": ("execution", "discipline"),
    }
    for detector in detectors:
        for component in mapping.get(detector["pattern"], ("discipline",)):
            penalties[component] += severity[detector["severity"]] * detector["confidence"]
    components = {name: round(max(0, 100 - penalties[name]), 1)
                  for name in ("discipline", "risk", "consistency", "execution", "timing")}
    return {"available": True, "sample_size": len(trades),
            "score": round(sum(components.values()) / len(components), 1), "components": components,
            "method": "Equal-weight mean; deterministic evidence penalties capped at 100 per component."}


INSIGHT_COPY = {
    "OVERTRADING": ("Trop de trades dégrade vos résultats", "Réduisez votre nombre maximal de trades par jour."),
    "OVERTRADING_AFTER_LOSS": ("Vos décisions après une perte coûtent cher", "Imposez une pause de 60 minutes après une perte."),
    "POSITION_SIZE_INCONSISTENCY": ("Votre taille de position manque de régularité", "Définissez une règle de taille fixe liée au risque."),
    "BEHAVIOR_AFTER_LOSS_STREAK": ("Vos résultats baissent après une série de pertes", "Arrêtez la session après trois pertes consécutives."),
    "BEHAVIOR_AFTER_WIN_STREAK": ("Votre avantage baisse après une série gagnante", "Conservez les mêmes critères après une série positive."),
    "PERFORMANCE_AFTER_WIN": ("Vos décisions après un gain sont moins performantes", "Gardez les mêmes critères après un trade gagnant."),
    "SESSION_PERFORMANCE": ("Vos résultats varient fortement selon la session", "Concentrez-vous sur les sessions où votre historique est le plus solide."),
    "WEEKDAY_PERFORMANCE": ("Vos résultats varient selon le jour", "Adaptez votre exposition aux jours historiquement faibles."),
    "SYMBOL_PERFORMANCE": ("Tous vos actifs ne contribuent pas de la même façon", "Priorisez les actifs où votre exécution est mesurablement meilleure."),
    "HOLDING_TIME_PERFORMANCE": ("La durée de détention influence vos résultats", "Cadrez vos sorties autour de la durée la plus robuste."),
    "POSITIONS_WITHOUT_STOP_LOSS": ("Vous ouvrez régulièrement sans stop-loss", "Définissez votre invalidation avant chaque entrée."),
    "FREQUENT_STOP_LOSS_CHANGES": ("Vous modifiez souvent vos stop-loss", "Fixez une règle précise avant de déplacer un stop-loss."),
    "STOP_LOSS_WIDENING": ("Vous éloignez vos stop-loss", "N’augmentez pas le risque initial après l’entrée."),
    "FREQUENT_TAKE_PROFIT_CHANGES": ("Vous modifiez souvent vos objectifs", "Définissez votre objectif avant l’entrée et documentez toute exception."),
    "POSITION_SIZE_CHANGES": ("Votre exposition change en cours de position", "Respectez une règle stable de réduction ou de renforcement."),
    "LOSSES_HELD_TOO_LONG": ("Vous conservez les pertes plus longtemps que les gains", "Cadrez une durée maximale cohérente avec votre plan."),
}


def build_insights(detectors: list[dict], period: dict) -> list[dict]:
    insights = []
    for row in detectors:
        title, recommendation = INSIGHT_COPY[row["pattern"]]
        insights.append({"title": title, "observation": title + ".", "evidence": row["evidence"],
            "financial_impact_if_measurable": row["impact"], "recommendation": recommendation,
            "confidence": row["confidence"], "time_period": period, "pattern": row["pattern"],
            "severity": row["severity"], "sample_size": row["sample_size"]})
    return insights


def review(deals: list[dict], timezone_name: str, review_type: str, now: datetime | None = None,
           behavior_events: list[dict] | None = None) -> dict:
    tz = ZoneInfo(validate_timezone(timezone_name))
    local_now = (now or datetime.now(tz)).astimezone(tz)
    review_type = review_type.lower()
    if review_type == "daily":
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        days = 1
    elif review_type == "weekly":
        start = (local_now - timedelta(days=local_now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        days = 7
    elif review_type == "monthly":
        start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days = (local_now - start).days + 1
    else:
        raise ValueError("Type de revue Coach invalide.")
    all_trades = reconstruct_positions(deals)
    period_trades = [trade for trade in all_trades if start <= trade["closed_at"].astimezone(tz) <= local_now]
    previous_end = start
    if review_type == "daily":
        previous_start = start - timedelta(days=1)
    elif review_type == "weekly":
        previous_start = start - timedelta(days=7)
    else:
        previous_month_last_day = start - timedelta(days=1)
        previous_start = previous_month_last_day.replace(day=1)
    previous_trades = [trade for trade in all_trades if previous_start <= trade["closed_at"].astimezone(tz) < previous_end]
    # Detectors use the rolling history ending now so daily reviews remain useful
    # while every financial figure in the period summary stays period-bound.
    rolling_start = local_now - timedelta(days=max(90, days * 3))
    rolling = [trade for trade in all_trades if rolling_start <= trade["closed_at"].astimezone(tz) <= local_now]
    detectors = detect_behavior(rolling, timezone_name)
    detectors.extend(detect_mt5_behavior(behavior_events or [], rolling))
    detectors.sort(key=lambda row: ({"HIGH": 3, "MEDIUM": 2, "LOW": 1}[row["severity"]], row["confidence"]), reverse=True)
    period = {"type": review_type, "from": start.isoformat(), "to": local_now.isoformat()}
    insights = build_insights(detectors, period)
    pnl = sum((decimal_value(t["net_pnl"]) for t in period_trades), Decimal("0"))
    previous_pnl = sum((decimal_value(t["net_pnl"]) for t in previous_trades), Decimal("0"))
    positive = [row for row in insights if row["financial_impact_if_measurable"] is not None
                and row["financial_impact_if_measurable"] >= 0]
    negative = [row for row in insights if row["financial_impact_if_measurable"] is None
                or row["financial_impact_if_measurable"] < 0]
    main = negative[0] if negative else (insights[0] if insights else None)
    ranked_strengths = sorted((row for row in insights if (row["financial_impact_if_measurable"] or 0) >= 0),
                              key=lambda row: row["confidence"], reverse=True)[:3]
    ranked_weaknesses = sorted(negative, key=lambda row: ((row["financial_impact_if_measurable"] or 0), -row["confidence"]))[:3]
    return {"review_type": review_type, "period": period,
            "sample": {"period_trades": len(period_trades), "rolling_trades": len(rolling), "minimum_for_patterns": MIN_TRADES},
            "period_performance": {"net_pnl": _number(pnl), "trades": len(period_trades),
                "previous_net_pnl": _number(previous_pnl), "pnl_change": _number(pnl - previous_pnl),
                "previous_trades": len(previous_trades)},
            "score": trading_score(rolling, detectors), "detectors": detectors, "insights": insights,
            "summary": {"what_went_well": positive[:3], "what_went_wrong": negative[:3],
                        "main_improvement": main["recommendation"] if main else (
                            "Continuez à collecter des données vérifiées avant de conclure." if len(rolling) < MIN_TRADES
                            else "Conservez vos règles actuelles et surveillez leur stabilité."),
                        "strongest_behavior": ranked_strengths[0] if ranked_strengths else None,
                        "weakest_behavior": ranked_weaknesses[0] if ranked_weaknesses else None,
                        "biggest_leak": ranked_weaknesses[0] if ranked_weaknesses else None,
                        "top_strengths": ranked_strengths, "top_weaknesses": ranked_weaknesses},
            "telemetry": {"events_analyzed": len(behavior_events or []),
                          "sampling": "PERIODIC_MT5_STATE", "external_api_cost": 0},
            "data_sufficiency": "SUFFICIENT" if detectors or len(rolling) >= MIN_TRADES else "INSUFFICIENT"}


def answer_question(deals: list[dict], behavior_events: list[dict], timezone_name: str,
                    question: str, now: datetime | None = None) -> dict:
    """Answer common coaching questions locally from structured evidence only."""
    clean = " ".join(str(question or "").strip().split())[:500]
    if not clean:
        raise ValueError("Posez une question au Coach.")
    report = review(deals, timezone_name, "monthly", now=now, behavior_events=behavior_events)
    lowered = clean.casefold()
    intents = {
        "stop_loss": ("stop", "sl", "risque"),
        "overtrading": ("trop de trade", "overtrad", "fréquence", "frequence", "revenge", "vengeance"),
        "position_size": ("lot", "taille", "exposition"),
        "holding": ("temps", "durée", "duree", "longtemps", "position"),
        "after_loss": ("perte", "perds", "perdant"),
        "timing": ("session", "jour", "horaire", "moment"),
        "symbol": ("actif", "symbole", "instrument", "xau", "gold"),
    }
    patterns = {
        "stop_loss": {"POSITIONS_WITHOUT_STOP_LOSS", "FREQUENT_STOP_LOSS_CHANGES", "STOP_LOSS_WIDENING"},
        "overtrading": {"OVERTRADING", "OVERTRADING_AFTER_LOSS"},
        "position_size": {"POSITION_SIZE_INCONSISTENCY", "POSITION_SIZE_CHANGES"},
        "holding": {"HOLDING_TIME_PERFORMANCE", "LOSSES_HELD_TOO_LONG"},
        "after_loss": {"OVERTRADING_AFTER_LOSS", "BEHAVIOR_AFTER_LOSS_STREAK"},
        "timing": {"SESSION_PERFORMANCE", "WEEKDAY_PERFORMANCE"},
        "symbol": {"SYMBOL_PERFORMANCE"},
    }
    intent = next((name for name, words in intents.items() if any(word in lowered for word in words)), None)
    eligible = [row for row in report["insights"] if not intent or row["pattern"] in patterns[intent]]
    if not eligible:
        return {"question": clean, "answer": (
            "Je n’ai pas encore assez d’observations vérifiées pour répondre à cette question sans inventer. "
            "La collecte continue automatiquement."
        ), "evidence": {"trades": report["sample"]["rolling_trades"],
                         "telemetry_events": report["telemetry"]["events_analyzed"]},
            "confidence": None, "recommendation": None, "source": "BECTANSE_LOCAL_ENGINE",
            "external_api_cost": 0}
    top = eligible[0]
    return {"question": clean,
            "answer": f"{top['observation']} {top['recommendation']}",
            "evidence": top["evidence"], "confidence": top["confidence"],
            "recommendation": top["recommendation"], "pattern": top["pattern"],
            "financial_impact_if_measurable": top["financial_impact_if_measurable"],
            "source": "BECTANSE_LOCAL_ENGINE", "external_api_cost": 0}
