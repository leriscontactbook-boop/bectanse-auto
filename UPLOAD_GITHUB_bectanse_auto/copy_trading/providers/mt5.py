"""Isolated MetaTrader 5 execution adapter.

Unlike ``trading_journal.providers.mt5``, this adapter owns broker mutations.
It is imported only by the dedicated Windows copy worker and all sending is
gated by the worker's demo/live release policy.
"""

from __future__ import annotations

import os
import ntpath
from datetime import datetime, timezone
from decimal import Decimal

from trading_journal.providers.base import ProviderError
from copy_trading.domain import verify_native_symbol_mapping


class MetaTrader5ExecutionProvider:
    def __init__(self, terminal_path: str, timeout_ms: int | None = None):
        self.terminal_path = terminal_path
        self.timeout_ms = timeout_ms or int(os.environ.get("COPY_TRADING_CONNECT_TIMEOUT_MS", "60000"))
        self._mt5 = None
        self._connected = False

    def _module(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as error:
                raise ProviderError("MT5_PACKAGE_MISSING", "Le module MT5 manque sur le worker Windows.", retryable=True) from error
            self._mt5 = mt5
        return self._mt5

    def connect(self, login: str, server: str, password: str) -> dict:
        mt5 = self._module()
        initialized = mt5.initialize(
            self.terminal_path,
            login=int(login), password=password, server=server,
            timeout=self.timeout_ms, portable=True,
        )
        if not initialized:
            code, _message = mt5.last_error() or (None, "")
            try:
                mt5.shutdown()
            except Exception:
                pass
            self._connected = False
            raise ProviderError("TERMINAL_CONNECTION_FAILED", "La session MT5 n’a pas pu se connecter.",
                                retryable=True, diagnostic_code=code)
        self._connected = True
        try:
            account = self.account_info()
            if (str(account["login"]) != str(login)
                    or account["server"].casefold() != server.casefold()):
                raise ProviderError("TERMINAL_IDENTITY_MISMATCH",
                                    "Le terminal est connecté à un autre compte ou serveur.")
            return account
        except Exception:
            self.disconnect()
            raise

    def account_info(self) -> dict:
        mt5 = self._module()
        info = mt5.account_info()
        terminal = mt5.terminal_info()
        if info is None or terminal is None or not getattr(terminal, "connected", False):
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("TERMINAL_CONNECTION_FAILED", "La session MT5 n’est pas connectée.",
                                retryable=True, diagnostic_code=code)
        actual_path = str(getattr(terminal, "path", "") or "").strip()
        expected_path = ntpath.dirname(ntpath.normpath(self.terminal_path))
        if (not actual_path or ntpath.normcase(ntpath.normpath(actual_path))
                != ntpath.normcase(expected_path)):
            raise ProviderError("TERMINAL_PROCESS_PATH_MISMATCH",
                                "La session IPC MT5 ne correspond pas au terminal Windows configuré.")
        demo_value = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        return {
            "login": str(getattr(info, "login", "")),
            "server": str(getattr(info, "server", "") or ""),
            "company": str(getattr(info, "company", "") or ""),
            "currency": str(getattr(info, "currency", "") or ""),
            "balance": str(Decimal(str(getattr(info, "balance", 0)))),
            "equity": str(Decimal(str(getattr(info, "equity", 0)))),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "trade_allowed": bool(getattr(info, "trade_allowed", False)),
            "is_demo": getattr(info, "trade_mode", None) == demo_value,
            "terminal_connected": bool(getattr(terminal, "connected", False)),
            "terminal_path_verified": True,
            "terminal_build": int(getattr(terminal, "build", 0) or 0),
        }

    def positions(self) -> list[dict]:
        mt5 = self._module()
        rows = mt5.positions_get()
        if rows is None:
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("TERMINAL_CONNECTION_FAILED", "La lecture des positions MT5 a échoué.",
                                retryable=True, diagnostic_code=code)
        result = []
        for row in rows:
            side = "BUY" if int(getattr(row, "type", -1)) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "SELL"
            result.append({
                "position_id": str(getattr(row, "identifier", 0) or getattr(row, "ticket", 0)),
                "ticket": str(getattr(row, "ticket", 0)),
                "type": int(getattr(row, "type", -1)),
                "symbol": str(getattr(row, "symbol", "") or ""),
                "side": side,
                "volume": str(Decimal(str(getattr(row, "volume", 0)))),
                "price_open": str(Decimal(str(getattr(row, "price_open", 0)))),
                "stop_loss": str(Decimal(str(getattr(row, "sl", 0)))),
                "take_profit": str(Decimal(str(getattr(row, "tp", 0)))),
                "magic": int(getattr(row, "magic", 0) or 0),
                "comment": str(getattr(row, "comment", "") or "")[:31],
                "time_update_msc": int(getattr(row, "time_update_msc", 0) or 0),
            })
        return result

    def pending_orders(self) -> list[dict]:
        """Return the complete native active pending-order snapshot."""
        mt5 = self._module()
        rows = mt5.orders_get()
        if rows is None:
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("TERMINAL_CONNECTION_FAILED", "La lecture native des ordres en attente a échoué.",
                                retryable=True, diagnostic_code=code)
        names = {
            getattr(mt5, "ORDER_TYPE_BUY_LIMIT", -1): "BUY_LIMIT",
            getattr(mt5, "ORDER_TYPE_SELL_LIMIT", -2): "SELL_LIMIT",
            getattr(mt5, "ORDER_TYPE_BUY_STOP", -3): "BUY_STOP",
            getattr(mt5, "ORDER_TYPE_SELL_STOP", -4): "SELL_STOP",
            getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", -5): "BUY_STOP_LIMIT",
            getattr(mt5, "ORDER_TYPE_SELL_STOP_LIMIT", -6): "SELL_STOP_LIMIT",
        }
        result = []
        for row in rows:
            pending_type = names.get(int(getattr(row, "type", -1)))
            if not pending_type:
                continue
            expiry = int(getattr(row, "time_expiration", 0) or 0)
            result.append({
                "order_ticket": str(getattr(row, "ticket", "") or ""),
                "symbol": str(getattr(row, "symbol", "") or ""),
                "pending_type": pending_type,
                "side": "BUY" if pending_type.startswith("BUY_") else "SELL",
                "volume": str(Decimal(str(getattr(row, "volume_current", 0)
                                           or getattr(row, "volume_initial", 0)))),
                "price_open": str(Decimal(str(getattr(row, "price_open", 0)))),
                "stop_loss": str(Decimal(str(getattr(row, "sl", 0)))),
                "take_profit": str(Decimal(str(getattr(row, "tp", 0)))),
                "stop_limit": str(Decimal(str(getattr(row, "price_stoplimit", 0)))),
                "time_type": int(getattr(row, "type_time", 0) or 0),
                "expiration_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat()
                    if expiry > 0 else None,
                "time_setup_msc": int(getattr(row, "time_setup_msc", 0) or 0),
                "state": int(getattr(row, "state", -1)),
                "comment": str(getattr(row, "comment", "") or "")[:31],
            })
        return result

    def pending_order_history(self, order_tickets: list[str]) -> list[dict]:
        """Read native history for missing active-order tickets.

        A missing active order is never treated as cancelled from an empty
        orders_get() result alone. The history lookup supplies the terminal
        state needed to distinguish expiry, cancellation, fill, and rejection.
        """
        mt5 = self._module()
        states = {
            getattr(mt5, "ORDER_STATE_CANCELED", 2): "CANCELED",
            getattr(mt5, "ORDER_STATE_FILLED", 4): "FILLED",
            getattr(mt5, "ORDER_STATE_REJECTED", 5): "REJECTED",
            getattr(mt5, "ORDER_STATE_EXPIRED", 6): "EXPIRED",
        }
        output = []
        for ticket in dict.fromkeys(str(value) for value in (order_tickets or []) if str(value).isdigit()):
            rows = mt5.history_orders_get(ticket=int(ticket))
            if rows is None:
                code, _message = mt5.last_error() or (None, "")
                raise ProviderError("TERMINAL_CONNECTION_FAILED", "La lecture native de l’historique des ordres a échoué.",
                                    retryable=True, diagnostic_code=code)
            for row in rows:
                if str(getattr(row, "ticket", "")) != ticket:
                    continue
                state = states.get(int(getattr(row, "state", -1)))
                if not state:
                    continue
                output.append({"order_ticket": ticket, "state": state,
                               "time_done_msc": int(getattr(row, "time_done_msc", 0) or 0)})
        return output

    def closing_deals(self, position_ids: list[str]) -> list[dict]:
        """Read native closing deals for positions missing from a full snapshot."""
        mt5 = self._module()
        closing_entries = {
            int(getattr(mt5, "DEAL_ENTRY_OUT", 1)),
            int(getattr(mt5, "DEAL_ENTRY_OUT_BY", 3)),
        }
        latest = {}
        for value in position_ids[:1000]:
            try:
                position_id = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if position_id <= 0:
                continue
            rows = mt5.history_deals_get(position=position_id)
            if rows is None:
                code, _message = mt5.last_error() or (None, "")
                raise ProviderError("TERMINAL_CONNECTION_FAILED", "La lecture de l’historique MT5 a échoué.",
                                    retryable=True, diagnostic_code=code)
            for row in rows:
                entry = int(getattr(row, "entry", -1))
                ticket = int(getattr(row, "ticket", 0) or 0)
                native_position = str(getattr(row, "position_id", position_id) or position_id)
                time_msc = int(getattr(row, "time_msc", 0) or 0)
                if entry not in closing_entries or ticket <= 0 or time_msc <= 0:
                    continue
                old = latest.get(native_position)
                if old is None or time_msc > old["time_msc"]:
                    latest[native_position] = {
                        "position_id": native_position,
                        "ticket": ticket,
                        "entry": entry,
                        "time_msc": time_msc,
                    }
        return list(latest.values())

    def symbol_evidence(self, symbol: str) -> dict:
        mt5 = self._module()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise ProviderError("SYMBOL_NOT_FOUND", "Le symbole n’existe pas dans le terminal MT5 connecté.")
        return {
            "name": str(getattr(info, "name", "") or ""),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "contract_size": str(Decimal(str(getattr(info, "trade_contract_size", 0)))),
            "tick_size": str(Decimal(str(getattr(info, "trade_tick_size", 0)))),
            "currency_base": str(getattr(info, "currency_base", "") or ""),
            "currency_profit": str(getattr(info, "currency_profit", "") or ""),
            "currency_margin": str(getattr(info, "currency_margin", "") or ""),
            "trade_calc_mode": int(getattr(info, "trade_calc_mode", -1)),
            "trade_exemode": int(getattr(info, "trade_exemode", -1)),
            "volume_min": str(Decimal(str(getattr(info, "volume_min", 0)))),
            "volume_step": str(Decimal(str(getattr(info, "volume_step", 0)))),
            "volume_max": str(Decimal(str(getattr(info, "volume_max", 0)))),
            "digits": int(getattr(info, "digits", 0) or 0),
            "point": str(Decimal(str(getattr(info, "point", 0)))),
            "filling_mode": int(getattr(info, "filling_mode", 0) or 0),
        }

    def compatible_symbol_candidates(self, master_evidence: dict, *, limit: int = 200) -> list[dict]:
        """Return native compatible aliases as review candidates, never approvals."""
        mt5 = self._module()
        symbols = mt5.symbols_get()
        if symbols is None:
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("SYMBOL_LIST_UNAVAILABLE", "La liste native des symboles MT5 est indisponible.",
                                retryable=True, diagnostic_code=code)
        result = []
        for item in symbols:
            name = str(getattr(item, "name", "") or "").strip()
            if not name or name.casefold() == str(master_evidence.get("name") or "").casefold():
                continue
            try:
                evidence = self.symbol_evidence(name)
                decision = verify_native_symbol_mapping(master_evidence, evidence)
            except (ProviderError, ValueError):
                continue
            if decision["state"] == "PENDING_REVIEW":
                result.append(evidence)
                if len(result) >= max(1, min(int(limit), 200)):
                    break
        return result

    def tick(self, symbol: str):
        mt5 = self._module()
        if not mt5.symbol_select(symbol, True):
            raise ProviderError("SYMBOL_NOT_SELECTABLE", "Le symbole MT5 n’a pas pu être activé dans Market Watch.")
        value = mt5.symbol_info_tick(symbol)
        if value is None:
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("QUOTE_UNAVAILABLE", "Le prix MT5 courant n’est pas disponible.", retryable=True,
                                diagnostic_code=code)
        return value

    def risk_loss_per_lot(self, symbol: str, side: str, stop_loss: Decimal | None) -> Decimal | None:
        if not stop_loss or stop_loss <= 0:
            return None
        mt5 = self._module()
        quote = self.tick(symbol)
        is_buy = side == "BUY"
        open_price = Decimal(str(getattr(quote, "ask" if is_buy else "bid", 0)))
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        result = mt5.order_calc_profit(order_type, symbol, 1.0, float(open_price), float(stop_loss))
        if result is None:
            return None
        signed = Decimal(str(result))
        return -signed if signed < 0 else None

    def risk_loss_per_lot_at_price(self, symbol: str, side: str, entry_price: Decimal,
                                   stop_loss: Decimal | None) -> Decimal | None:
        """Calculate native one-lot risk using the pending entry price."""
        if not stop_loss or stop_loss <= 0:
            return None
        mt5 = self._module()
        order_type = mt5.ORDER_TYPE_BUY if str(side).upper() == "BUY" else mt5.ORDER_TYPE_SELL
        result = mt5.order_calc_profit(order_type, symbol, 1.0, float(entry_price), float(stop_loss))
        if result is None:
            code, _message = mt5.last_error() or (None, "")
            raise ProviderError("PENDING_RISK_CALCULATION_UNAVAILABLE",
                                "Le risque d’un ordre en attente n’a pas pu être calculé par MT5.",
                                retryable=True, diagnostic_code=code)
        signed = Decimal(str(result))
        if signed >= 0:
            return None
        return -signed

    def check_request(self, request: dict) -> dict:
        result = self._module().order_check(request)
        if result is None:
            code, _message = self._module().last_error() or (None, "")
            raise ProviderError("ORDER_CHECK_UNAVAILABLE", "MT5 n’a pas confirmé le précontrôle.", retryable=True,
                                diagnostic_code=code)
        retcode = int(getattr(result, "retcode", -1))
        if retcode not in {0, getattr(self._module(), "TRADE_RETCODE_DONE", 10009)}:
            raise ProviderError("ORDER_CHECK_REJECTED", "Le précontrôle MT5 a refusé la requête.",
                                diagnostic_code=retcode)
        return {"retcode": retcode,
                "comment": str(getattr(result, "comment", "") or "")[:120]}

    def _safe_filling(self, evidence: dict) -> int:
        mt5 = self._module()
        mode = int(evidence.get("filling_mode", 0))
        execution_mode = int(evidence.get("trade_exemode", -1))
        if mode & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            return getattr(mt5, "ORDER_FILLING_IOC", 1)
        if mode & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            return getattr(mt5, "ORDER_FILLING_FOK", 0)
        if execution_mode != getattr(mt5, "SYMBOL_TRADE_EXECUTION_MARKET", 2):
            return getattr(mt5, "ORDER_FILLING_RETURN", 2)
        raise ProviderError("FILLING_MODE_UNSUPPORTED", "Le symbole ne permet pas un mode de remplissage sûr.")

    def submit_request(self, request: dict) -> dict:
        mt5 = self._module()
        result = mt5.order_send(request)
        if result is None:
            code, _message = mt5.last_error() or (None, "")
            return {"confirmed": False, "ambiguous": True, "failure_code": "ORDER_RESULT_UNKNOWN",
                    "retcode": code}
        retcode = int(getattr(result, "retcode", -1))
        confirmed_codes = {
            getattr(mt5, "TRADE_RETCODE_DONE", 10009),
            getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
            getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025),
        }
        confirmed = retcode in confirmed_codes
        ambiguous_codes = {
            getattr(mt5, "TRADE_RETCODE_TIMEOUT", -999),
            getattr(mt5, "TRADE_RETCODE_CONNECTION", -998),
            getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", -997),
        }
        return {
            "confirmed": confirmed,
            "ambiguous": not confirmed and retcode in ambiguous_codes,
            "failure_code": "" if confirmed else f"MT5_RETCODE_{retcode}",
            "retcode": retcode,
            "order_ticket": str(getattr(result, "order", "") or ""),
            "deal_ticket": str(getattr(result, "deal", "") or ""),
            "position_id": str(getattr(result, "position", "") or ""),
            "volume": str(Decimal(str(getattr(result, "volume", 0) or 0))),
            "comment": str(getattr(result, "comment", "") or "")[:120],
        }

    def open_request(self, *, symbol: str, side: str, volume: Decimal,
                     stop_loss: Decimal | None, take_profit: Decimal | None,
                     comment: str, deviation: int) -> dict:
        mt5 = self._module()
        quote = self.tick(symbol)
        is_buy = side == "BUY"
        evidence = self.symbol_evidence(symbol)
        filling = self._safe_filling(evidence)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": float(getattr(quote, "ask" if is_buy else "bid")),
            "deviation": max(0, min(int(deviation), 500)),
            "magic": int(os.environ.get("COPY_TRADING_MAGIC", "7623101")),
            "comment": str(comment)[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        if stop_loss and stop_loss > 0:
            request["sl"] = float(stop_loss)
        if take_profit and take_profit > 0:
            request["tp"] = float(take_profit)
        return request

    def protection_request(self, *, position: dict, stop_loss: Decimal | None,
                           take_profit: Decimal | None) -> dict:
        mt5 = self._module()
        return {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": str(position["symbol"]),
            "position": int(position["ticket"]),
            "sl": float(stop_loss or 0),
            "tp": float(take_profit or 0),
            "magic": int(os.environ.get("COPY_TRADING_MAGIC", "7623101")),
        }

    def close_request(self, *, position: dict, volume: Decimal, comment: str,
                      deviation: int) -> dict:
        mt5 = self._module()
        symbol = str(position["symbol"])
        quote = self.tick(symbol)
        is_buy = int(position["type"]) == getattr(mt5, "POSITION_TYPE_BUY", 0)
        return {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "position": int(position["ticket"]),
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": float(getattr(quote, "bid" if is_buy else "ask")),
            "deviation": max(0, min(int(deviation), 500)),
            "magic": int(os.environ.get("COPY_TRADING_MAGIC", "7623101")),
            "comment": str(comment)[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._safe_filling(self.symbol_evidence(symbol)),
        }

    def pending_request(self, *, action: str, symbol: str, pending_type: str,
                        volume: Decimal, price: Decimal, stop_loss: Decimal,
                        take_profit: Decimal, stop_limit: Decimal, time_type: int,
                        expiration_at: str | None, comment: str, order_ticket: str = "") -> dict:
        """Build a native pending-order create/modify/remove request."""
        mt5 = self._module()
        normalized_action = str(action or "").upper()
        types = {
            "BUY_LIMIT": getattr(mt5, "ORDER_TYPE_BUY_LIMIT", None),
            "SELL_LIMIT": getattr(mt5, "ORDER_TYPE_SELL_LIMIT", None),
            "BUY_STOP": getattr(mt5, "ORDER_TYPE_BUY_STOP", None),
            "SELL_STOP": getattr(mt5, "ORDER_TYPE_SELL_STOP", None),
            "BUY_STOP_LIMIT": getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", None),
            "SELL_STOP_LIMIT": getattr(mt5, "ORDER_TYPE_SELL_STOP_LIMIT", None),
        }
        mt5_type = types.get(str(pending_type or "").upper())
        if mt5_type is None:
            raise ProviderError("PENDING_ORDER_TYPE_UNSUPPORTED", "Le type d’ordre en attente MT5 n’est pas pris en charge.")
        if normalized_action not in {"PENDING_CREATE", "PENDING_MODIFY", "PENDING_CANCEL", "PENDING_EXPIRE"}:
            raise ProviderError("PENDING_ORDER_ACTION_UNSUPPORTED", "Action d’ordre en attente inconnue.")
        if normalized_action in {"PENDING_MODIFY", "PENDING_CANCEL", "PENDING_EXPIRE"}:
            if not str(order_ticket or "").isdigit() or int(order_ticket) <= 0:
                raise ProviderError("PENDING_ORDER_ID_INVALID", "Le ticket natif à modifier est invalide.")
        if normalized_action in {"PENDING_CANCEL", "PENDING_EXPIRE"}:
            return {"action": mt5.TRADE_ACTION_REMOVE, "order": int(order_ticket)}
        if normalized_action == "PENDING_MODIFY":
            request = {"action": mt5.TRADE_ACTION_MODIFY, "order": int(order_ticket),
                       "price": float(price), "sl": float(stop_loss or 0),
                       "tp": float(take_profit or 0), "type_time": int(time_type)}
        else:
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": str(symbol), "type": mt5_type, "volume": float(volume),
                "price": float(price), "sl": float(stop_loss or 0),
                "tp": float(take_profit or 0), "stoplimit": float(stop_limit or 0),
                "type_time": int(time_type),
                "type_filling": getattr(mt5, "ORDER_FILLING_RETURN", 2),
            }
        if normalized_action == "PENDING_CREATE":
            request["comment"] = str(comment)[:31]
        else:
            request["order"] = int(order_ticket)
        if expiration_at:
            try:
                expiry = datetime.fromisoformat(str(expiration_at).replace("Z", "+00:00"))
                if expiry.tzinfo is None or expiry.timestamp() <= datetime.now(timezone.utc).timestamp():
                    raise ValueError
                request["expiration"] = int(expiry.timestamp())
            except (TypeError, ValueError, OverflowError):
                raise ProviderError("PENDING_ORDER_EXPIRATION_INVALID", "L’expiration native de l’ordre est passée ou invalide.")
        return request

    def disconnect(self):
        if self._mt5 is not None and self._connected:
            try:
                self._mt5.shutdown()
            except Exception:
                pass
            finally:
                self._connected = False
