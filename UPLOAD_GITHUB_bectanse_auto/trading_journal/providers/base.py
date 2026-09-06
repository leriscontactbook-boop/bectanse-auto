"""Provider contract shared by MT5 and deterministic CI mocks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class AccountSnapshot:
    login: str
    broker: str
    server: str
    currency: str
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    leverage: int
    access_mode: str
    account_type: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        for key in ("balance", "equity", "margin", "free_margin"):
            data[key] = str(data[key])
        return data


@dataclass(frozen=True)
class DealSnapshot:
    ticket: int
    order_ticket: int | None
    position_id: int | None
    symbol: str
    deal_type: str
    entry_type: str
    reason: str
    is_trading_deal: bool
    volume: Decimal
    price: Decimal
    profit: Decimal
    commission: Decimal
    swap: Decimal
    fee: Decimal
    magic_number: int | None
    comment: str
    executed_at: datetime

    @property
    def net_pnl(self) -> Decimal:
        return self.profit + self.commission + self.swap + self.fee

    def as_dict(self) -> dict:
        data = asdict(self)
        for key in ("volume", "price", "profit", "commission", "swap", "fee"):
            data[key] = str(data[key])
        data["executed_at"] = self.executed_at.isoformat()
        return data


class ProviderError(RuntimeError):
    def __init__(self, code: str, user_message: str, *, retryable: bool = False):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable


class TradingProvider(ABC):
    @abstractmethod
    def connect(self, login: str, server: str, password: str) -> AccountSnapshot:
        raise NotImplementedError

    def validate_credentials(self, login: str, server: str, password: str) -> AccountSnapshot:
        return self.connect(login, server, password)

    @abstractmethod
    def get_account_info(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_deals(self, date_from: datetime, date_to: datetime) -> Iterable[DealSnapshot]:
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_terminal_info(self) -> dict:
        raise NotImplementedError

    def health_check(self) -> dict:
        account = self.get_account_info()
        terminal = self.get_terminal_info()
        return {"healthy": True, "login_masked": "••••" + account.login[-4:], "terminal": terminal}

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.disconnect()
        return False
