import ast
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_journal.calculations import (
    calculate_daily_pnl,
    calculate_monthly_pnl,
    performance_stats,
    reconstruct_positions,
)
from trading_journal.coach import MIN_TRADES, build_insights, detect_behavior, review, trading_score
from trading_journal.config import JournalConfigurationError, validate_backend_config, validate_worker_config
from trading_journal.entitlements import FEATURE_KEYS, resolve_entitlements
from trading_journal.providers.base import AccountSnapshot
from trading_journal.providers.mock import MockTradingProvider
from trading_journal.security import CredentialCipher, canonical_worker_signature, verify_worker_request
from trading_journal.service import RETRY_DELAYS_SECONDS


ROOT = Path(__file__).resolve().parents[1]


def row(ticket, hour, *, day=1, position=None, entry="IN", kind="BUY", volume="1", profit="0", symbol="XAUUSD"):
    return {"trading_account_id": 1, "mt5_deal_ticket": ticket,
            "mt5_position_id": position or ticket, "symbol": symbol, "deal_type": kind,
            "entry_type": entry, "is_trading_deal": kind in {"BUY", "SELL"},
            "volume": Decimal(volume), "price": Decimal("2000"), "profit": Decimal(profit),
            "commission": Decimal("0"), "swap": Decimal("0"), "fee": Decimal("0"),
            "executed_at": datetime(2026, 8, day, hour, tzinfo=timezone.utc)}


def trade_rows(count=30, *, after_loss_bad=False, variable_sizes=False):
    rows = []
    ticket = 1
    for index in range(count):
        day = 1 + index // 4
        hour = 8 + (index % 4) * 2
        volume = str(1 if not variable_sizes else (1 if index % 3 else 5))
        pnl = Decimal("100") if index % 2 == 0 else Decimal("-70")
        if after_loss_bad and index and index % 2 == 0:
            pnl = Decimal("-140")
        rows.extend([
            row(ticket, hour, day=day, position=1000 + index, entry="IN", volume=volume),
            row(ticket + 1, hour + 1, day=day, position=1000 + index, entry="OUT", kind="SELL",
                volume=volume, profit=str(pnl)),
        ])
        ticket += 2
    return rows


def test_production_configuration_blocks_mock(monkeypatch):
    monkeypatch.setenv("TRADING_PROVIDER", "mock")
    with pytest.raises(JournalConfigurationError, match="forbidden"):
        validate_backend_config(production=True)


def test_production_configuration_requires_postgres(monkeypatch):
    monkeypatch.setenv("TRADING_PROVIDER", "mt5")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///unsafe")
    with pytest.raises(JournalConfigurationError, match="PostgreSQL"):
        validate_backend_config(production=True)


def test_production_configuration_accepts_valid_secrets(monkeypatch):
    monkeypatch.setenv("TRADING_PROVIDER", "mt5")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/test")
    monkeypatch.setenv("INTERNAL_WORKER_SECRET", "x" * 48)
    monkeypatch.setenv("MT5_CREDENTIAL_MASTER_KEY", base64.urlsafe_b64encode(bytes(range(32))).decode())
    validate_backend_config(production=True)


def test_worker_capacity_rejects_duplicate_terminal_slots(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "https://example.test")
    monkeypatch.setenv("INTERNAL_WORKER_SECRET", "x" * 48)
    monkeypatch.setenv("MT5_TERMINAL_PATHS", "C:\\MT5\\one.exe;C:\\MT5\\one.exe")
    monkeypatch.setenv("MT5_WORKER_COUNT", "2")
    with pytest.raises(JournalConfigurationError, match="distinct"):
        validate_worker_config()


def test_hmac_nonce_is_bound_to_signature():
    secret, body = "x" * 48, b"{}"
    signature = canonical_worker_signature(secret, "POST", "/internal", "100", body, "nonce-123")
    assert verify_worker_request(secret, "POST", "/internal", "100", body, signature, now=100, nonce="nonce-123")
    assert not verify_worker_request(secret, "POST", "/internal", "100", body, signature, now=100, nonce="nonce-other")


def test_key_rotation_aad_prevents_cross_account_decryption():
    cipher = CredentialCipher(bytes(range(32)))
    encrypted = cipher.encrypt("investor", "trading-account:1:A")
    with pytest.raises(Exception):
        cipher.decrypt(encrypted, "trading-account:2:A")


def test_multi_entry_multi_exit_is_one_trade():
    rows = [row(1, 8, position=77, volume="0.4"), row(2, 9, position=77, volume="0.6"),
            row(3, 10, position=77, entry="OUT", kind="SELL", volume="0.5", profit="40"),
            row(4, 11, position=77, entry="OUT", kind="SELL", volume="0.5", profit="60")]
    trades = reconstruct_positions(rows)
    assert len(trades) == 1
    assert trades[0]["volume"] == Decimal("1.0")
    assert trades[0]["net_pnl"] == Decimal("100")


def test_inout_closes_netting_position():
    rows = [row(1, 8, position=88, volume="1"),
            row(2, 9, position=88, entry="INOUT", kind="SELL", volume="1", profit="25")]
    trades = reconstruct_positions(rows)
    assert len(trades) == 1
    assert trades[0]["net_pnl"] == Decimal("25")


def test_incomplete_partial_close_is_not_counted_as_closed_trade():
    rows = [row(1, 8, position=99, volume="1"),
            row(2, 9, position=99, entry="OUT", kind="SELL", volume="0.4", profit="10")]
    assert reconstruct_positions(rows) == []


def test_daily_and_monthly_pnl_are_centralized():
    trades = reconstruct_positions(trade_rows(4))
    daily = calculate_daily_pnl(trades, "UTC")
    monthly = calculate_monthly_pnl(trades, "UTC")
    assert sum(daily.values()) == Decimal("60")
    assert monthly["2026-08"] == Decimal("60")


def test_drawdown_and_breakeven_are_deterministic():
    rows = trade_rows(4)
    rows[-1]["profit"] = Decimal("0")
    stats = performance_stats(rows, "UTC")
    assert stats["breakeven"] == 1
    assert stats["maxDrawdown"] == 70


def test_coach_suppresses_conclusions_under_minimum_sample():
    trades = reconstruct_positions(trade_rows(MIN_TRADES - 1))
    assert detect_behavior(trades, "UTC") == []
    assert trading_score(trades, [])["available"] is False


def test_coach_detects_position_size_inconsistency_with_evidence():
    trades = reconstruct_positions(trade_rows(36, variable_sizes=True))
    results = detect_behavior(trades, "UTC")
    detector = next(row for row in results if row["pattern"] == "POSITION_SIZE_INCONSISTENCY")
    assert detector["sample_size"] == 36
    assert 0 <= detector["confidence"] <= 1
    assert detector["evidence"]["maximum_volume"] == 5


def test_coach_insight_never_changes_financial_evidence():
    detector = {"pattern": "OVERTRADING", "confidence": .91, "severity": "HIGH",
                "sample_size": 42, "impact": -1280.42, "evidence": {"days": 8}}
    insight = build_insights([detector], {"type": "monthly"})[0]
    assert insight["financial_impact_if_measurable"] == -1280.42
    assert insight["evidence"] == {"days": 8}


def test_coach_review_has_required_contract_and_insufficient_copy():
    result = review(trade_rows(5), "UTC", "daily", now=datetime(2026, 8, 2, 23, tzinfo=timezone.utc))
    assert result["data_sufficiency"] == "INSUFFICIENT"
    assert set(result) >= {"score", "detectors", "insights", "summary", "period"}


def test_academy_gets_all_coach_flags_and_external_pro_does_not_get_monthly():
    academy = resolve_entitlements(None, {"actif": True, "access_level": "member", "billing_status": "active"})
    external = resolve_entitlements({"plan": "JOURNAL_PRO", "subscription_status": "active"})
    assert academy.coach_monthly and academy.coach_advanced_patterns
    assert external.coach_daily and not external.coach_monthly
    assert "coach.ai_explanations" in FEATURE_KEYS


@pytest.mark.parametrize("member,subscription,allowed,source", [
    ({"actif": True, "access_level": "member", "billing_status": "active"}, None, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "active"}, True, "JOURNAL_SUBSCRIPTION"),
    ({"actif": False, "access_level": "explorer"}, None, False, "NONE"),
    ({"actif": True, "access_level": "member", "billing_status": "active"}, {"plan": "JOURNAL_ELITE", "subscription_status": "active"}, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "member", "billing_status": "canceled", "date_fin": datetime(2026, 1, 1)}, {"plan": "JOURNAL_PRO", "subscription_status": "active"}, True, "JOURNAL_SUBSCRIPTION"),
    ({"actif": True, "access_level": "member", "billing_status": "active"}, {"plan": "JOURNAL_PRO", "subscription_status": "canceled"}, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "member", "billing_status": "canceled", "date_fin": datetime(2026, 1, 1)}, None, False, "NONE"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "past_due"}, False, "NONE"),
])
def test_critical_entitlement_cases(member, subscription, allowed, source):
    result = resolve_entitlements(subscription, member, now=datetime(2026, 9, 6, tzinfo=timezone.utc))
    assert result.allowed is allowed
    assert result.source == source


def test_retry_policy_matches_documented_backoff():
    assert RETRY_DELAYS_SECONDS == (30, 120, 300, 900, 3600)


def test_mock_provider_has_full_read_contract():
    account = AccountSnapshot("123", "B", "S", "EUR", Decimal("1"), Decimal("1"), Decimal("0"), Decimal("1"), 10, "READ_ONLY")
    provider = MockTradingProvider(account)
    provider.connect("123", "S", "p")
    assert provider.get_open_positions() == []
    assert provider.health_check()["healthy"]


def test_production_mt5_code_contains_no_execution_api_calls():
    forbidden = {"order_send", "order_modify", "order_close", "position_close"}
    violations = []
    for path in (ROOT / "trading_journal").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden:
                violations.append(f"{path.name}:{node.lineno}:{node.func.attr}")
    assert violations == []


def test_worker_logs_do_not_include_password_fields():
    source = (ROOT / "trading_journal" / "worker.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "_log(" in line or "LOGGER." in line:
            assert "password=" not in line
