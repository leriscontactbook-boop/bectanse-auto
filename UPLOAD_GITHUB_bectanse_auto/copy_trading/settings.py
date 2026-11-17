"""Strict validation of the existing member settings, without changing lot modes."""

import math

MODES = {"Lots fixes", "Lot par symbole", "Copier les lots de l'envoyeur",
         "Risque par solde (Balance)", "Risque par capitaux (Equity)", "Risque en %"}
DEFAULTS = {
    "mode_risque": "Lots fixes", "lots": 0.01, "lots_max": 5, "slippage": 100,
    "forcer_lot_minimum": False, "inverser_trades": False,
    "copier_ordres_en_attente": True, "convertir_pending_invalide": False,
    "copier_sl": True, "drawdown_actif": False, "drawdown_pct": 5.0,
    "drawdown_gain_actif": False, "drawdown_gain_pct": 5.0,
    "objectif_actif": False, "objectif_gain_pct": 5.0, "objectif_perte_pct": 3.0,
    "objectif_periode": "Mensuel", "filtre_news": False, "risque_pct": 1.0,
    "multiplicateur": 1.0, "risque_balance_pct": 1.0, "risque_equity_pct": 1.0,
    "lot_symboles": {},
}


def number(value, key, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} : nombre requis.")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{key} : valeur entre {minimum} et {maximum} requise.")
    return value


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("Les paramètres doivent être un objet JSON.")
    values = {key: payload.get(key, default) for key, default in DEFAULTS.items()}
    if not isinstance(values["mode_risque"], str) or values["mode_risque"] not in MODES:
        raise ValueError("Mode de risque inconnu.")
    if values["objectif_periode"] not in ("Quotidien", "Hebdomadaire", "Mensuel"):
        raise ValueError("Période d’objectif inconnue.")
    for key, default in DEFAULTS.items():
        if isinstance(default, bool):
            if not isinstance(values[key], bool):
                raise ValueError(f"{key} : booléen requis.")
        elif isinstance(default, (int, float)):
            maximum = 10000 if key in {"drawdown_gain_pct", "objectif_gain_pct"} else 100
            if key in {"lots_max", "slippage"}:
                maximum = 1000
            values[key] = number(values[key], key, 0 if key == "slippage" else 0.01, maximum)
    if int(values["slippage"]) != values["slippage"]:
        raise ValueError("Le slippage doit être un entier en points.")
    symbols = values["lot_symboles"]
    if not isinstance(symbols, dict) or len(symbols) > 500:
        raise ValueError("Liste de lots par symbole invalide.")
    for symbol, lot in symbols.items():
        if not isinstance(symbol, str) or not symbol or len(symbol) > 64 or any(ord(c) < 33 for c in symbol):
            raise ValueError("Nom de symbole invalide.")
        number(lot, symbol, 0.01, 100)
    if values["mode_risque"] == "Lot par symbole" and not symbols:
        raise ValueError("Renseignez au moins un lot par symbole.")
    if values["mode_risque"] == "Lots fixes" and values["lots"] > values["lots_max"]:
        raise ValueError("Le lot fixe dépasse le lot maximal.")
    if values["convertir_pending_invalide"] and not values["copier_ordres_en_attente"]:
        raise ValueError("La conversion nécessite la copie des ordres en attente.")
    return values
