"""Deterministic provider used by CI; never selected by production routes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .base import AccountSnapshot, DealSnapshot, ProviderError, TradingProvider


class MockTradingProvider(TradingProvider):
    def __init__(self, account: AccountSnapshot, deals: Iterable[DealSnapshot] = (), *, reject=False):
        self.account = account
        self.deals = list(deals)
        self.reject = reject
        self.connected = False

    def connect(self, login: str, server: str, password: str) -> AccountSnapshot:
        if self.reject:
            raise ProviderError("AUTH_ERROR", "Les identifiants fournis sont incorrects.")
        self.connected = True
        return self.account

    def get_account_info(self) -> AccountSnapshot:
        if not self.connected:
            raise ProviderError("TERMINAL_ERROR", "Session fermée")
        return self.account

    def get_deals(self, date_from: datetime, date_to: datetime):
        return [deal for deal in self.deals if date_from <= deal.executed_at <= date_to]

    def get_open_positions(self) -> list[dict]:
        return []

    def get_terminal_info(self) -> dict:
        return {"connected": self.connected, "name": "CI Mock Terminal"}

    def disconnect(self) -> None:
        self.connected = False
