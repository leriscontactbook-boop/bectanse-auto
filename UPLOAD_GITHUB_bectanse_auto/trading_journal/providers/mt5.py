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
        self.timeout_ms = timeout_ms or int(os.environ.get("MT5_CONNECT_TIMEOUT_MS", "45000"))
        self.require_read_only = os.environ.get("MT5_REQUIRE_READ_ONLY", "true").lower() not in {
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
        if code in {-6, -10005} or "auth" in message_text or "password" in message_text:
            return ProviderError("AUTH_ERROR", "Les identifiants fournis sont incorrects.")
        if "server" in message_text or "network" in message_text:
            return ProviderError(
                "BROKER_UNAVAILABLE",
                "La connexion au broker est momentanément indisponible.",
                retryable=True,
            )
        return ProviderError(
            "TERMINAL_ERROR",
            "Le service MetaTrader est momentanément indisponible.",
            retryable=True,
        )

    def connect(self, login: str, server: str, password: str) -> AccountSnapshot:
        mt5 = self._module()
        initialized = (
            mt5.initialize(self.terminal_path, timeout=self.timeout_ms)
            if self.terminal_path
            else mt5.initialize(timeout=self.timeout_ms)
        )
        if not initialized:
            raise self._connection_error()
        self._connected = True
        if not mt5.login(int(login), password=password, server=server, timeout=self.timeout_ms):
            error = self._connection_error()
            self.disconnect()
            raise error
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
        return [{
            "ticket": int(getattr(row, "ticket", 0) or 0),
            "position_id": int(getattr(row, "identifier", 0) or getattr(row, "ticket", 0) or 0),
            "symbol": str(getattr(row, "symbol", "") or "")[:40],
            "type": "BUY" if int(getattr(row, "type", -1)) == 0 else "SELL",
            "volume": str(_decimal(getattr(row, "volume", 0))),
            "price_open": str(_decimal(getattr(row, "price_open", 0))),
            "profit": str(_decimal(getattr(row, "profit", 0))),
            "time": datetime.fromtimestamp(int(getattr(row, "time", 0)), timezone.utc).isoformat(),
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
        if self._mt5 is not None and self._connected:
            try:
                self._mt5.shutdown()
            finally:
                self._connected = False
