"""Read-only MetaTrader 5 provider. This module contains no order API."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

from .base import AccountSnapshot, DealSnapshot, ProviderError, TradingProvider


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


class MetaTrader5Provider(TradingProvider):
    def __init__(self, terminal_path: str | None = None, timeout_ms: int | None = None):
        self.terminal_path = terminal_path or os.environ.get("MT5_TERMINAL_PATH", "")
        # A freshly installed terminal performs a one-time MQL5 compilation that
        # can exceed the MetaQuotes 60-second default before IPC is ready.
        self.timeout_ms = timeout_ms or int(os.environ.get("MT5_CONNECT_TIMEOUT_MS", "180000"))
        self.require_read_only = os.environ.get("MT5_REQUIRE_READ_ONLY", "false").lower() not in {
            "0", "false", "no"
        }
        self.portable_mode = os.environ.get("MT5_PORTABLE_MODE", "true").lower() not in {
            "0", "false", "no"
        }
        self._mt5 = None
        self._connected = False

    def _module(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as exc:
                raise ProviderError(
                    "TERMINAL_ERROR",
                    "Le service MetaTrader est momentanément indisponible.",
                    retryable=True,
                ) from exc
            self._mt5 = mt5
        return self._mt5

    def _last_error(self) -> tuple:
        try:
            return tuple(self._module().last_error() or ())
        except Exception:
            return ()

    def _connection_error(self) -> ProviderError:
        code, message = (self._last_error() + (None, None))[:2]
        message_text = str(message or "").lower()
        if code == -6 or "auth" in message_text or "password" in message_text:
            return ProviderError(
                "AUTH_ERROR", "Les identifiants fournis sont incorrects.",
                diagnostic_code=code,
            )
        if "server" in message_text or "network" in message_text:
            return ProviderError(
                "BROKER_UNAVAILABLE",
                "La connexion au broker est momentanément indisponible.",
                retryable=True,
                diagnostic_code=code,
            )
        return ProviderError(
            "TERMINAL_ERROR",
            "Le service MetaTrader est momentanément indisponible.",
            retryable=True,
            diagnostic_code=code,
        )

    def connect(self, login: str, server: str, password: str) -> AccountSnapshot:
        mt5 = self._module()
        connection = {
            "login": int(login),
            "password": password,
            "server": server,
            "timeout": self.timeout_ms,
            "portable": self.portable_mode,
        }
        initialized = (
            mt5.initialize(self.terminal_path, **connection)
            if self.terminal_path
            else mt5.initialize(**connection)
        )
        if not initialized:
            raise self._connection_error()
        self._connected = True
        info = mt5.account_info()
        if info is None:
            raise self._connection_error()

        trade_allowed = getattr(info, "trade_allowed", None)
        access_mode = "READ_ONLY" if trade_allowed is False else "TRADING_ALLOWED"
        if self.require_read_only and trade_allowed is True:
            raise ProviderError(
                "TRADING_PASSWORD_REJECTED",
                "Utilisez uniquement le mot de passe investisseur (lecture seule).",
            )
        return self._snapshot(info, access_mode)

    def _snapshot(self, info, access_mode: str | None = None) -> AccountSnapshot:
        trade_allowed = getattr(info, "trade_allowed", None)
        resolved_access = access_mode or (
            "READ_ONLY" if trade_allowed is False else "TRADING_ALLOWED"
        )
        return AccountSnapshot(
            login=str(getattr(info, "login", "")),
            broker=str(getattr(info, "company", "") or ""),
            server=str(getattr(info, "server", "") or ""),
            currency=str(getattr(info, "currency", "") or ""),
            balance=_decimal(getattr(info, "balance", 0)),
            equity=_decimal(getattr(info, "equity", 0)),
            margin=_decimal(getattr(info, "margin", 0)),
            free_margin=_decimal(getattr(info, "margin_free", 0)),
            leverage=int(getattr(info, "leverage", 0) or 0),
            access_mode=resolved_access,
            account_type=str(getattr(info, "trade_mode", "") or ""),
        )

    def get_account_info(self) -> AccountSnapshot:
        if not self._connected:
            raise ProviderError("TERMINAL_ERROR", "La session MetaTrader n’est pas ouverte.")
        info = self._module().account_info()
        if info is None:
            raise self._connection_error()
        return self._snapshot(info)

    def get_deals(self, date_from: datetime, date_to: datetime):
        if not self._connected:
            raise ProviderError("TERMINAL_ERROR", "La session MetaTrader n’est pas ouverte.")
        mt5 = self._module()
        rows = mt5.history_deals_get(date_from, date_to)
        if rows is None:
            raise self._connection_error()

        type_names = {
            getattr(mt5, "DEAL_TYPE_BUY", 0): "BUY",
            getattr(mt5, "DEAL_TYPE_SELL", 1): "SELL",
            getattr(mt5, "DEAL_TYPE_BALANCE", 2): "BALANCE",
            getattr(mt5, "DEAL_TYPE_CREDIT", 3): "CREDIT",
            getattr(mt5, "DEAL_TYPE_CHARGE", 4): "CHARGE",
            getattr(mt5, "DEAL_TYPE_CORRECTION", 5): "CORRECTION",
            getattr(mt5, "DEAL_TYPE_BONUS", 6): "BONUS",
            getattr(mt5, "DEAL_TYPE_COMMISSION", 7): "COMMISSION",
            getattr(mt5, "DEAL_TYPE_INTEREST", 10): "INTEREST",
        }
        entry_names = {
            getattr(mt5, "DEAL_ENTRY_IN", 0): "IN",
            getattr(mt5, "DEAL_ENTRY_OUT", 1): "OUT",
            getattr(mt5, "DEAL_ENTRY_INOUT", 2): "INOUT",
            getattr(mt5, "DEAL_ENTRY_OUT_BY", 3): "OUT_BY",
        }
        reason_names = {
            getattr(mt5, name): name.removeprefix("DEAL_REASON_")
            for name in dir(mt5)
            if name.startswith("DEAL_REASON_") and isinstance(getattr(mt5, name), int)
        }
        trading_types = {
            getattr(mt5, "DEAL_TYPE_BUY", 0),
            getattr(mt5, "DEAL_TYPE_SELL", 1),
        }
        for row in rows:
            raw_type = int(getattr(row, "type", -1))
            milliseconds = int(getattr(row, "time_msc", 0) or 0)
            executed_at = (
                datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
                if milliseconds
                else datetime.fromtimestamp(int(getattr(row, "time", 0)), timezone.utc)
            )
            yield DealSnapshot(
                ticket=int(getattr(row, "ticket")),
                order_ticket=int(getattr(row, "order", 0) or 0) or None,
                position_id=int(getattr(row, "position_id", 0) or 0) or None,
                symbol=str(getattr(row, "symbol", "") or "")[:40],
                deal_type=type_names.get(raw_type, f"OTHER_{raw_type}"),
                entry_type=entry_names.get(int(getattr(row, "entry", -1)), ""),
                reason=reason_names.get(int(getattr(row, "reason", -1)), "")[:40],
                is_trading_deal=raw_type in trading_types,
                volume=_decimal(getattr(row, "volume", 0)),
                price=_decimal(getattr(row, "price", 0)),
                profit=_decimal(getattr(row, "profit", 0)),
                commission=_decimal(getattr(row, "commission", 0)),
                swap=_decimal(getattr(row, "swap", 0)),
                fee=_decimal(getattr(row, "fee", 0)),
                magic_number=int(getattr(row, "magic", 0) or 0) or None,
                comment=str(getattr(row, "comment", "") or "")[:500],
                executed_at=executed_at,
            )

    def get_open_positions(self) -> list[dict]:
        if not self._connected:
            raise ProviderError("TERMINAL_ERROR", "La session MetaTrader n’est pas ouverte.")
        rows = self._module().positions_get()
        if rows is None:
            raise self._connection_error()
        mt5 = self._module()
        type_names = {
            getattr(mt5, "POSITION_TYPE_BUY", 0): "BUY",
            getattr(mt5, "POSITION_TYPE_SELL", 1): "SELL",
        }
        reason_names = {
            getattr(mt5, name): name.removeprefix("POSITION_REASON_")
            for name in dir(mt5)
            if name.startswith("POSITION_REASON_") and isinstance(getattr(mt5, name), int)
        }
        return [{
            "ticket": int(getattr(row, "ticket", 0) or 0),
            "position_id": int(getattr(row, "identifier", 0) or getattr(row, "ticket", 0) or 0),
            "symbol": str(getattr(row, "symbol", "") or "")[:40],
            "type": type_names.get(int(getattr(row, "type", -1)), "UNKNOWN"),
            "volume": str(_decimal(getattr(row, "volume", 0))),
            "price_open": str(_decimal(getattr(row, "price_open", 0))),
            "price_current": str(_decimal(getattr(row, "price_current", 0))),
            "sl": str(_decimal(getattr(row, "sl", 0))),
            "tp": str(_decimal(getattr(row, "tp", 0))),
            "profit": str(_decimal(getattr(row, "profit", 0))),
            "swap": str(_decimal(getattr(row, "swap", 0))),
            "magic": int(getattr(row, "magic", 0) or 0),
            "reason": reason_names.get(int(getattr(row, "reason", -1)), "")[:40],
            "comment": str(getattr(row, "comment", "") or "")[:500],
            "opened_at": self._row_time(row, "time_msc", "time"),
            "updated_at": self._row_time(row, "time_update_msc", "time_update"),
        } for row in rows]

    @staticmethod
    def _row_time(row, milliseconds_field: str, seconds_field: str) -> str:
        milliseconds = int(getattr(row, milliseconds_field, 0) or 0)
        seconds = int(getattr(row, seconds_field, 0) or 0)
        value = milliseconds / 1000 if milliseconds else seconds
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else ""

    def get_open_orders(self) -> list[dict]:
        if not self._connected:
            raise ProviderError("TERMINAL_ERROR", "La session MetaTrader n’est pas ouverte.")
        mt5 = self._module()
        rows = mt5.orders_get()
        if rows is None:
            raise self._connection_error()
        type_names = {
            getattr(mt5, name): name.removeprefix("ORDER_TYPE_")
            for name in dir(mt5)
            if name.startswith("ORDER_TYPE_") and isinstance(getattr(mt5, name), int)
        }
        reason_names = {
            getattr(mt5, name): name.removeprefix("ORDER_REASON_")
            for name in dir(mt5)
            if name.startswith("ORDER_REASON_") and isinstance(getattr(mt5, name), int)
        }
        return [{
            "ticket": int(getattr(row, "ticket", 0) or 0),
            "order_id": int(getattr(row, "ticket", 0) or 0),
            "position_id": int(getattr(row, "position_id", 0) or 0),
            "symbol": str(getattr(row, "symbol", "") or "")[:40],
            "type": type_names.get(int(getattr(row, "type", -1)), "UNKNOWN")[:40],
            "volume_initial": str(_decimal(getattr(row, "volume_initial", 0))),
            "volume_current": str(_decimal(getattr(row, "volume_current", 0))),
            "price_open": str(_decimal(getattr(row, "price_open", 0))),
            "price_current": str(_decimal(getattr(row, "price_current", 0))),
            "price_stoplimit": str(_decimal(getattr(row, "price_stoplimit", 0))),
            "sl": str(_decimal(getattr(row, "sl", 0))),
            "tp": str(_decimal(getattr(row, "tp", 0))),
            "magic": int(getattr(row, "magic", 0) or 0),
            "reason": reason_names.get(int(getattr(row, "reason", -1)), "")[:40],
            "comment": str(getattr(row, "comment", "") or "")[:500],
            "created_at": self._row_time(row, "time_setup_msc", "time_setup"),
            "expires_at": self._row_time(row, "time_expiration_msc", "time_expiration"),
        } for row in rows]

    def get_terminal_info(self) -> dict:
        if not self._connected:
            raise ProviderError("TERMINAL_ERROR", "La session MetaTrader n’est pas ouverte.")
        info = self._module().terminal_info()
        if info is None:
            raise self._connection_error()
        return {
            "connected": bool(getattr(info, "connected", False)),
            "trade_allowed": bool(getattr(info, "trade_allowed", False)),
            "name": str(getattr(info, "name", "MetaTrader 5") or "MetaTrader 5")[:80],
            "build": int(getattr(info, "build", 0) or 0),
        }

    def disconnect(self) -> None:
        if self._mt5 is not None:
            try:
                self._mt5.shutdown()
            finally:
                self._connected = False
