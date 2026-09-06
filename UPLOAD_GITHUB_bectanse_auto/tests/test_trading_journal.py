import base64
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_journal.calculations import (
    analytics_breakdown,
    calendar_summary,
    deal_net_pnl,
    performance_stats,
    reconstruct_positions,
    validate_timezone,
)
from trading_journal.entitlements import (
    PLAN_RULES,
    can_add_trading_account,
    resolve_entitlements,
)
from trading_journal.providers.base import AccountSnapshot, DealSnapshot, ProviderError
from trading_journal.providers.mock import MockTradingProvider
from trading_journal.schema import STATEMENTS
from trading_journal.security import (
    CredentialCipher,
    EncryptedCredential,
    canonical_worker_signature,
    verify_worker_request,
)
from trading_journal.service import JournalService
from trading_journal.worker import month_ranges


def deal(ticket, executed_at, *, position=100, entry="OUT", deal_type="BUY",
         profit=0, commission=0, swap=0, fee=0, trading=True, volume="0.1", price="2000"):
    return {
        "trading_account_id": 1,
        "mt5_deal_ticket": ticket,
        "mt5_position_id": position,
        "symbol": "XAUUSD",
        "deal_type": deal_type,
        "entry_type": entry,
        "is_trading_deal": trading,
        "volume": Decimal(volume),
        "price": Decimal(price),
        "profit": Decimal(str(profit)),
        "commission": Decimal(str(commission)),
        "swap": Decimal(str(swap)),
        "fee": Decimal(str(fee)),
        "executed_at": executed_at,
    }


def test_aes_256_gcm_round_trip_and_tamper_detection():
    cipher = CredentialCipher(bytes(range(32)))
    encrypted = cipher.encrypt("investor-only", "trading-account:7:BCT-USER")
    assert encrypted.ciphertext != b"investor-only"
    assert len(encrypted.nonce) == 12
    assert len(encrypted.tag) == 16
    assert cipher.decrypt(encrypted, "trading-account:7:BCT-USER") == "investor-only"
    tampered = EncryptedCredential(encrypted.ciphertext[:-1] + b"x", encrypted.nonce, encrypted.tag)
    with pytest.raises(Exception):
        cipher.decrypt(tampered, "trading-account:7:BCT-USER")


def test_worker_signature_rejects_replay_and_modified_body():
    secret = "s" * 48
    body = b'{"job":"one"}'
    signature = canonical_worker_signature(secret, "POST", "/internal/mt5/jobs/claim", "1000", body)
    assert verify_worker_request(secret, "POST", "/internal/mt5/jobs/claim", "1000", body, signature, now=1040)
    assert not verify_worker_request(secret, "POST", "/internal/mt5/jobs/claim", "1000", b"{}", signature, now=1040)
    assert not verify_worker_request(secret, "POST", "/internal/mt5/jobs/claim", "1000", body, signature, now=1200)


def test_net_pnl_includes_commission_swap_and_fee():
    row = deal(1, datetime(2026, 9, 2, 12, tzinfo=timezone.utc), profit=100, commission=-4, swap=-2, fee=-1)
    assert deal_net_pnl(row) == Decimal("93")


def test_deposits_and_withdrawals_are_excluded_from_performance():
    rows = [
        deal(1, datetime(2026, 9, 2, 10, tzinfo=timezone.utc), entry="IN", profit=0, commission=-2),
        deal(2, datetime(2026, 9, 2, 11, tzinfo=timezone.utc), entry="OUT", profit=104, commission=-2),
        deal(3, datetime(2026, 9, 2, 12, tzinfo=timezone.utc), position=0, entry="", deal_type="BALANCE", profit=5000, trading=False),
    ]
    stats = performance_stats(rows)
    assert stats["netPnl"] == 100
    assert stats["trades"] == 1
    assert stats["wins"] == 1


def test_partial_closes_count_as_one_closed_position():
    rows = [
        deal(1, datetime(2026, 9, 2, 9, tzinfo=timezone.utc), entry="IN", deal_type="BUY", volume="0.2", commission=-2),
        deal(2, datetime(2026, 9, 2, 10, tzinfo=timezone.utc), entry="OUT", deal_type="SELL", volume="0.1", profit=45, commission=-1),
        deal(3, datetime(2026, 9, 2, 11, tzinfo=timezone.utc), entry="OUT", deal_type="SELL", volume="0.1", profit=60, commission=-1),
    ]
    positions = reconstruct_positions(rows)
    assert len(positions) == 1
    assert positions[0]["direction"] == "BUY"
    assert positions[0]["net_pnl"] == Decimal("101")
    assert positions[0]["volume"] == Decimal("0.2")


def test_calendar_groups_by_user_timezone_not_utc_date():
    rows = [
        deal(1, datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc), entry="IN", commission=-1),
        deal(2, datetime(2026, 8, 31, 23, 30, tzinfo=timezone.utc), entry="OUT", profit=102, commission=-1),
    ]
    result = calendar_summary(rows, "Europe/Paris", "2026-09")
    assert result["days"][0]["date"] == "2026-09-01"
    assert result["days"][0]["netPnl"] == 100
    assert result["summary"]["trades"] == 1


def test_invalid_timezone_is_rejected_as_user_input():
    with pytest.raises(ValueError, match="Fuseau horaire invalide"):
        validate_timezone("Mars/Olympus")


def test_monthly_stats_and_profit_factor_are_position_based():
    rows = [
        deal(1, datetime(2026, 9, 1, 8, tzinfo=timezone.utc), position=1, entry="IN"),
        deal(2, datetime(2026, 9, 1, 9, tzinfo=timezone.utc), position=1, entry="OUT", profit=200),
        deal(3, datetime(2026, 9, 2, 8, tzinfo=timezone.utc), position=2, entry="IN"),
        deal(4, datetime(2026, 9, 2, 9, tzinfo=timezone.utc), position=2, entry="OUT", profit=-100),
    ]
    stats = performance_stats(rows)
    assert stats["winRate"] == 50
    assert stats["profitFactor"] == 2
    assert stats["averageWin"] == 200
    assert stats["averageLoss"] == -100
    analytics = analytics_breakdown(rows, "UTC")
    assert analytics["symbol"][0]["netPnl"] == 100


def test_entitlements_are_centralized_and_existing_paid_members_get_pro():
    assert can_add_trading_account(PLAN_RULES["JOURNAL_PRO"], 0)
    assert not can_add_trading_account(PLAN_RULES["JOURNAL_PRO"], 1)
    entitlements = resolve_entitlements(None, {
        "actif": True, "access_level": "member", "billing_status": "active",
    })
    assert entitlements.plan == "ACADEMY_INCLUDED"
    assert entitlements.max_accounts == 1
    assert entitlements.coach_advanced_patterns


def test_mock_provider_supports_ci_without_metatrader():
    account = AccountSnapshot("1234", "Broker", "Broker-Live", "EUR", Decimal("1000"),
                              Decimal("1005"), Decimal("0"), Decimal("1005"), 100, "READ_ONLY")
    provider = MockTradingProvider(account)
    assert provider.connect("1234", "Broker-Live", "investor") == account
    provider.disconnect()
    rejecting = MockTradingProvider(account, reject=True)
    with pytest.raises(ProviderError) as error:
        rejecting.connect("1234", "Broker-Live", "bad")
    assert error.value.code == "AUTH_ERROR"


def test_sync_ranges_are_paginated_month_by_month():
    ranges = list(month_ranges(
        datetime(2026, 1, 15, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    ))
    assert len(ranges) == 4
    assert ranges[0][0].day == 15
    assert ranges[-1][1].month == 4


def test_schema_enforces_deal_idempotence_and_account_ownership_keys():
    sql = " ".join(STATEMENTS).lower()
    assert "unique (trading_account_id, mt5_deal_ticket)" in sql
    assert "user_id text not null references members(code)" in sql
    assert "where status in ('pending','leased','running','retry')" in sql


def test_worker_payload_normalization_never_treats_balance_as_trade():
    normalized = JournalService._normalize_deal({
        "ticket": 99,
        "deal_type": "BALANCE",
        "entry_type": "",
        "is_trading_deal": True,
        "executed_at": "2026-09-01T10:00:00+00:00",
        "profit": 5000,
    })
    assert normalized["is_trading"] is False
