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
from trading_journal.coach import (
    MIN_TRADES,
    answer_question,
    build_insights,
    detect_behavior,
    detect_mt5_behavior,
    review,
    trading_score,
)
from trading_journal.billing import (
    create_checkout,
    create_portal,
    process_webhook,
    reconcile_trading_access,
    schedule_standalone_cancellation,
)
from trading_journal.brokers import build_broker_catalog, broker_name_from_server
from trading_journal.config import JournalConfigurationError, validate_backend_config, validate_worker_config
from trading_journal.entitlements import FEATURE_KEYS, PLAN_RULES, resolve_entitlements
from trading_journal.providers.base import AccountSnapshot, ProviderError
from trading_journal.providers.mt5 import MetaTrader5Provider
from trading_journal.providers.mock import MockTradingProvider
from trading_journal.security import CredentialCipher, canonical_worker_signature, verify_worker_request
from trading_journal.service import JournalService, RETRY_DELAYS_SECONDS
from trading_journal.routes import _checkout_failure_code


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


def test_coach_detects_verified_positions_without_stop_loss():
    events = [{"trading_account_id": 1, "entity_id": index,
               "event_type": "POSITION_OPENED", "current_state": {"sl": "0", "type": "BUY"}}
              for index in range(1, 6)]
    results = detect_mt5_behavior(events, [])
    detector = next(row for row in results if row["pattern"] == "POSITIONS_WITHOUT_STOP_LOSS")
    assert detector["sample_size"] == 5
    assert detector["evidence"]["rate"] == 1
    assert detector["impact"] is None


def test_coach_detects_stop_loss_widening_from_sampled_mt5_states():
    events = [{"trading_account_id": 1, "entity_id": index,
               "event_type": "POSITION_CHANGED", "changed_fields": ["sl"],
               "previous_state": {"sl": "1990", "type": "BUY"},
               "current_state": {"sl": "1980", "type": "BUY"}}
              for index in range(1, 6)]
    patterns = {row["pattern"] for row in detect_mt5_behavior(events, [])}
    assert {"FREQUENT_STOP_LOSS_CHANGES", "STOP_LOSS_WIDENING"} <= patterns


def test_local_coach_answer_never_calls_an_external_ai_or_invents_a_fact():
    result = answer_question([], [], "UTC", "Est-ce que je déplace trop mon stop-loss ?",
                             now=datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert result["external_api_cost"] == 0
    assert result["source"] == "BECTANSE_LOCAL_ENGINE"
    assert result["confidence"] is None
    assert "pas encore assez" in result["answer"]


def test_academy_gets_all_coach_flags_and_external_pro_does_not_get_monthly():
    period_end = datetime.now(timezone.utc) + timedelta(days=30)
    academy = resolve_entitlements(None, {"actif": True, "access_level": "member",
        "billing_status": "active", "billing_current_period_end": period_end})
    external = resolve_entitlements({"plan": "JOURNAL_PRO", "subscription_status": "active",
        "current_period_end": period_end})
    assert academy.coach_monthly and academy.coach_advanced_patterns
    assert external.coach_daily and not external.coach_monthly
    assert "coach.ai_explanations" in FEATURE_KEYS


def test_academy_members_receive_the_complete_elite_journal_by_default(monkeypatch):
    monkeypatch.delenv("ACADEMY_JOURNAL_PLAN", raising=False)
    academy = resolve_entitlements(None, {
        "actif": True, "access_level": "member", "billing_status": "active",
        "billing_current_period_end": datetime.now(timezone.utc) + timedelta(days=30),
    })
    assert academy.plan == "ACADEMY_INCLUDED"
    assert academy.max_accounts == 10
    assert academy.advanced_analytics
    assert academy.multi_account
    assert academy.export
    assert academy.priority_sync


def test_academy_member_checkout_does_not_depend_on_standalone_price_configuration(monkeypatch):
    monkeypatch.delenv("STRIPE_JOURNAL_PRO_PRICE_ID", raising=False)

    def database_must_not_be_opened():
        raise AssertionError("Academy inclusion must be resolved before standalone billing")

    with pytest.raises(PermissionError, match="déjà inclus"):
        create_checkout(
            database_must_not_be_opened,
            {"actif": True, "access_level": "member", "billing_status": "active",
             "billing_current_period_end": datetime.now(timezone.utc) + timedelta(days=30),
             "email": "member@example.com"},
            "BCT-MEMBER",
            "JOURNAL_PRO",
            "https://example.test/",
        )


class _BillingConnection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def run(self, query, **params):
        self.queries.append((query, params))
        return self.rows if query.lstrip().startswith("SELECT") else []

    def close(self):
        pass


class _WebhookConnection(_BillingConnection):
    def run(self, query, **params):
        self.queries.append((query, params))
        if "RETURNING event_id" in query:
            return [(params["event_id"],)]
        return []


class _AccessConnection(_BillingConnection):
    def __init__(self, account_rows):
        super().__init__([])
        self.account_rows = account_rows

    def run(self, query, **params):
        self.queries.append((query, params))
        compact = " ".join(query.split())
        if "SELECT id,status FROM trading_accounts" in compact:
            return self.account_rows
        if "SET status='PENDING_VERIFICATION'" in compact:
            return [(params["account_id"],)]
        if "INSERT INTO trading_sync_jobs" in compact:
            return [(params["id"],)]
        if "SET status='ACCESS_EXPIRED'" in compact and "UPDATE trading_accounts" in compact:
            return [(params["account_id"],)]
        return []


class _StripeEndpoint:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, params):
        self.calls.append((params, None))
        return self.response

    def update(self, object_id, params, options=None):
        self.calls.append((object_id, params, options))
        return {"id": object_id, **params}


class _StripeClient:
    def __init__(self):
        self.checkout_sessions = _StripeEndpoint({"url": "https://checkout.stripe.com/c/pay/test"})
        self.portal_sessions = _StripeEndpoint({"url": "https://billing.stripe.com/p/session/test"})
        self.subscriptions = _StripeEndpoint({})
        self.v1 = type("V1", (), {})()
        self.v1.checkout = type("Checkout", (), {"sessions": self.checkout_sessions})()
        self.v1.billing_portal = type("Portal", (), {"sessions": self.portal_sessions})()
        self.v1.subscriptions = self.subscriptions


def test_standalone_checkout_reuses_customer_and_sends_structured_metadata(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_bectanse")
    monkeypatch.setenv("STRIPE_JOURNAL_PRO_PRICE_ID", "price_journal_pro")
    connection = _BillingConnection([("cus_existing", "", "inactive", False, None)])
    client = _StripeClient()
    url = create_checkout(
        lambda: connection,
        {"actif": False, "access_level": "explorer", "email": "client@example.com"},
        "BCT-CLIENT",
        "JOURNAL_PRO",
        "https://example.test/",
        client=client,
    )
    params = client.checkout_sessions.calls[0][0]
    assert url.startswith("https://checkout.stripe.com/")
    assert params["customer"] == "cus_existing"
    assert "customer_email" not in params
    assert params["line_items"] == [{"price": "price_journal_pro", "quantity": 1}]
    assert params["metadata"]["product"] == "BECTANSE_JOURNAL"
    assert params["integration_identifier"].startswith("bectanse_journal_")
    assert len(params["integration_identifier"].rsplit("_", 1)[-1]) == 8
    assert params["allow_promotion_codes"] is True
    assert "payment_method_types" not in params


def test_expired_academy_member_can_purchase_standalone_journal(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_bectanse")
    monkeypatch.setenv("STRIPE_JOURNAL_PRO_PRICE_ID", "price_journal_pro")
    connection = _BillingConnection([
        ("cus_existing", "sub_old", "canceled", False,
         datetime.now(timezone.utc) - timedelta(days=1)),
    ])
    client = _StripeClient()
    url = create_checkout(
        lambda: connection,
        {"actif": True, "access_level": "member", "billing_status": "canceled",
         "date_fin": datetime.now(timezone.utc) - timedelta(days=1),
         "email": "returning@example.com"},
        "BCT-RETURNING", "JOURNAL_PRO", "https://example.test/", client=client,
    )
    assert url.startswith("https://checkout.stripe.com/")


def test_valid_standalone_subscription_cannot_be_duplicated_while_cancellation_is_pending(monkeypatch):
    monkeypatch.setenv("STRIPE_JOURNAL_PRO_PRICE_ID", "price_journal_pro")
    connection = _BillingConnection([
        ("cus_existing", "sub_current", "active", True,
         datetime.now(timezone.utc) + timedelta(days=7)),
    ])
    with pytest.raises(PermissionError, match="déjà actif"):
        create_checkout(
            lambda: connection,
            {"actif": False, "access_level": "explorer", "email": "client@example.com"},
            "BCT-CLIENT", "JOURNAL_PRO", "https://example.test/", client=_StripeClient(),
        )


def test_standalone_portal_uses_the_journal_customer_and_optional_configuration(monkeypatch):
    monkeypatch.setenv("STRIPE_JOURNAL_PORTAL_CONFIGURATION", "bpc_journal")
    connection = _BillingConnection([("cus_journal",)])
    client = _StripeClient()
    url = create_portal(lambda: connection, "BCT-CLIENT", "https://example.test/", client=client)
    params = client.portal_sessions.calls[0][0]
    assert url.startswith("https://billing.stripe.com/")
    assert params == {
        "customer": "cus_journal",
        "return_url": "https://example.test/journal",
        "locale": "fr",
        "configuration": "bpc_journal",
    }


def test_academy_activation_schedules_standalone_cancellation(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_bectanse")
    connection = _BillingConnection([("sub_journal", "active", False)])
    client = _StripeClient()
    assert schedule_standalone_cancellation(
        lambda: connection, "BCT-MEMBER", client=client,
    )
    object_id, params, options = client.subscriptions.calls[0]
    assert object_id == "sub_journal"
    assert params == {"cancel_at_period_end": True}
    assert options["idempotency_key"] == "academy-included-BCT-MEMBER-sub_journal"


def test_access_reconciliation_suspends_jobs_but_preserves_accounts_and_history():
    connection = _AccessConnection([(17, "SYNCED"), (18, "SYNCING")])
    result = reconcile_trading_access(
        connection, "BCT-EXPIRED", entitlements=PLAN_RULES["NONE"],
    )
    sql = "\n".join(query for query, _ in connection.queries)
    assert result == {"allowed": False, "suspended_accounts": 2}
    assert "UPDATE trading_sync_jobs SET status='DEAD'" in sql
    assert "UPDATE trading_accounts SET status='ACCESS_EXPIRED'" in sql
    assert "DELETE FROM trading_accounts" not in sql
    assert "DELETE FROM trading_deals" not in sql


def test_access_reconciliation_restores_account_and_queues_full_sync():
    connection = _AccessConnection([(17, "ACCESS_EXPIRED")])
    result = reconcile_trading_access(
        connection, "BCT-RETURNING", entitlements=PLAN_RULES["JOURNAL_PRO"],
    )
    sql = "\n".join(query for query, _ in connection.queries)
    assert result == {"allowed": True, "restored_accounts": 1, "queued_jobs": 1,
                      "suspended_accounts": 0, "suspended_account_ids": []}
    assert "SET status='PENDING_VERIFICATION'" in sql
    assert "'FULL_HISTORY_SYNC','PENDING',100" in sql


def test_pro_fallback_keeps_one_account_and_suspends_the_rest():
    connection = _AccessConnection([(17, "SYNCED"), (18, "SYNCED"), (19, "SYNCED")])
    result = reconcile_trading_access(
        connection, "BCT-PRO", entitlements=PLAN_RULES["JOURNAL_PRO"],
    )
    assert result["allowed"] is True
    assert result["suspended_accounts"] == 2
    assert result["suspended_account_ids"] == [18, 19]


def test_portal_upgrade_uses_current_price_instead_of_stale_subscription_metadata(monkeypatch):
    monkeypatch.setenv("STRIPE_JOURNAL_PRO_PRICE_ID", "price_journal_pro")
    monkeypatch.setenv("STRIPE_JOURNAL_ELITE_PRICE_ID", "price_journal_elite")
    connection = _WebhookConnection([])
    event = {
        "id": "evt_upgrade",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_journal", "customer": "cus_journal", "status": "active",
            "metadata": {
                "member_code": "BCT-CLIENT", "product": "BECTANSE_JOURNAL",
                "journal_plan": "JOURNAL_PRO",
            },
            "items": {"data": [{"price": {"id": "price_journal_elite"}}]},
        }},
    }
    process_webhook(event, lambda: connection)
    subscription_write = next(
        params for query, params in connection.queries
        if "INSERT INTO trading_subscriptions" in query
    )
    assert subscription_write["plan"] == "JOURNAL_ELITE"
    assert subscription_write["price_id"] == "price_journal_elite"


def test_broker_catalog_combines_verified_and_successfully_seen_servers(monkeypatch):
    monkeypatch.setenv("MT5_BROKER_CATALOG_JSON", '{"IC Markets":["ICMarketsSC-MT5-4"]}')
    result = build_broker_catalog([
        ("Exness", "Exness-MT5Real34"),
        ("Pepperstone", "Pepperstone-MT5-Live01"),
    ])
    by_name = {row["name"]: row["servers"] for row in result}
    assert by_name["Exness"] == ["Exness-MT5Real34"]
    assert by_name["IC Markets"] == ["ICMarketsSC-MT5-4"]
    assert by_name["Pepperstone"] == ["Pepperstone-MT5-Live01"]
    assert broker_name_from_server("Broker-MT5-Live") == "Broker"


@pytest.mark.parametrize("error,code", [
    (PermissionError("included"), "already-active"),
    (ValueError("email"), "account-required"),
    (RuntimeError("stripe"), "unavailable"),
])
def test_checkout_errors_are_mapped_to_safe_browser_statuses(error, code):
    assert _checkout_failure_code(error) == code


@pytest.mark.parametrize("member,subscription,allowed,source", [
    ({"actif": True, "access_level": "member", "billing_status": "active", "date_fin": datetime(2026, 10, 1)}, None, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "active", "current_period_end": datetime(2026, 10, 1)}, True, "JOURNAL_SUBSCRIPTION"),
    ({"actif": False, "access_level": "explorer"}, None, False, "NONE"),
    ({"actif": True, "access_level": "member", "billing_status": "active", "date_fin": datetime(2026, 10, 1)}, {"plan": "JOURNAL_ELITE", "subscription_status": "active", "current_period_end": datetime(2026, 10, 1)}, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "member", "billing_status": "canceled", "date_fin": datetime(2026, 1, 1)}, {"plan": "JOURNAL_PRO", "subscription_status": "active", "current_period_end": datetime(2026, 10, 1)}, True, "JOURNAL_SUBSCRIPTION"),
    ({"actif": True, "access_level": "member", "billing_status": "active", "date_fin": datetime(2026, 10, 1)}, {"plan": "JOURNAL_PRO", "subscription_status": "canceled", "current_period_end": datetime(2026, 10, 1)}, True, "ACADEMY_INCLUDED"),
    ({"actif": False, "access_level": "member", "billing_status": "canceled", "date_fin": datetime(2026, 1, 1)}, None, False, "NONE"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "past_due", "current_period_end": datetime(2026, 10, 1)}, False, "NONE"),
    ({"actif": True, "access_level": "member", "billing_status": "active"}, None, False, "NONE"),
    ({"actif": True, "access_level": "member", "billing_status": "active", "date_fin": datetime(2026, 9, 6)}, None, False, "NONE"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "active"}, False, "NONE"),
    ({"actif": False, "access_level": "explorer"}, {"plan": "JOURNAL_PRO", "subscription_status": "active", "current_period_end": datetime(2026, 1, 1)}, False, "NONE"),
    ({"actif": True, "access_level": "member", "billing_status": "past_due", "date_fin": datetime(2026, 10, 1)}, {"plan": "JOURNAL_PRO", "subscription_status": "active", "current_period_end": datetime(2026, 10, 1)}, True, "JOURNAL_SUBSCRIPTION"),
])
def test_critical_entitlement_cases(member, subscription, allowed, source):
    result = resolve_entitlements(subscription, member, now=datetime(2026, 9, 6, tzinfo=timezone.utc))
    assert result.allowed is allowed
    assert result.source == source


def test_feature_grant_cannot_bypass_missing_subscription():
    result = resolve_entitlements(None, {"actif": False, "access_level": "explorer"}, [{
        "feature_key": "journal.basic",
        "source": "ADMIN",
        "status": "ACTIVE",
        "valid_from": datetime(2026, 1, 1),
        "valid_until": datetime(2026, 10, 1),
    }], now=datetime(2026, 9, 6, tzinfo=timezone.utc))
    assert result.allowed is False
    assert result.source == "NONE"


def test_retry_policy_matches_documented_backoff():
    assert RETRY_DELAYS_SECONDS == (30, 120, 300, 900, 3600)


def test_mock_provider_has_full_read_contract():
    account = AccountSnapshot("123", "B", "S", "EUR", Decimal("1"), Decimal("1"), Decimal("0"), Decimal("1"), 10, "READ_ONLY")
    provider = MockTradingProvider(account)
    provider.connect("123", "S", "p")
    assert provider.get_open_positions() == []
    assert provider.get_open_orders() == []
    assert provider.health_check()["healthy"]


def test_mt5_provider_captures_behavioral_position_fields():
    class FakeMT5:
        POSITION_TYPE_BUY = 0
        POSITION_TYPE_SELL = 1
        POSITION_REASON_CLIENT = 3

        @staticmethod
        def positions_get():
            return [type("Position", (), {
                "ticket": 12, "identifier": 99, "symbol": "XAUUSD", "type": 0,
                "volume": .2, "price_open": 2400, "price_current": 2405,
                "sl": 2380, "tp": 2450, "profit": 10, "swap": -1,
                "magic": 7, "reason": 3, "comment": "manual",
                "time": 1_780_000_000, "time_msc": 0,
                "time_update": 1_780_000_100, "time_update_msc": 0,
            })()]

    provider = MetaTrader5Provider()
    provider._mt5 = FakeMT5()
    provider._connected = True
    position = provider.get_open_positions()[0]
    assert position["position_id"] == 99
    assert position["sl"] == "2380"
    assert position["tp"] == "2450"
    assert position["reason"] == "CLIENT"


def test_mt5_entity_normalization_is_allowlisted_and_json_safe():
    entity_id, payload = JournalService._normalize_mt5_entity("POSITION", {
        "position_id": 42, "symbol": "XAUUSD", "type": "BUY", "sl": "2380.50",
        "password": "must-not-be-stored", "comment": "manual",
    })
    assert entity_id == 42
    assert payload["sl"] == "2380.50"
    assert "password" not in payload


def test_mt5_provider_initializes_with_account_credentials():
    class FakeMT5:
        def __init__(self):
            self.initialize_args = None

        def initialize(self, *args, **kwargs):
            self.initialize_args = (args, kwargs)
            return True

        def account_info(self):
            return type("Info", (), {
                "login": 123456, "company": "Broker", "server": "Broker-Live",
                "currency": "EUR", "balance": 1000, "equity": 1000,
                "margin": 0, "margin_free": 1000, "leverage": 100,
                "trade_allowed": False, "trade_mode": 0,
            })()

        def shutdown(self):
            pass

    fake = FakeMT5()
    provider = MetaTrader5Provider("C:\\MT5\\NODE-01\\terminal64.exe", timeout_ms=12_345)
    provider._mt5 = fake
    snapshot = provider.connect("123456", "Broker-Live", "investor-secret")
    assert snapshot.access_mode == "READ_ONLY"
    assert fake.initialize_args == (("C:\\MT5\\NODE-01\\terminal64.exe",), {
        "login": 123456,
        "password": "investor-secret",
        "server": "Broker-Live",
        "timeout": 12_345,
        "portable": True,
    })


def test_failed_initialize_is_always_shutdown_and_keeps_mt5_diagnostic_code():
    class FailingMT5:
        shutdown_calls = 0

        @staticmethod
        def initialize(*args, **kwargs):
            return False

        @staticmethod
        def last_error():
            return -10005, "IPC timeout"

        def shutdown(self):
            self.shutdown_calls += 1

    fake = FailingMT5()
    provider = MetaTrader5Provider("C:\\MT5\\NODE-01\\terminal64.exe")
    provider._mt5 = fake
    with pytest.raises(ProviderError) as captured:
        provider.connect("123456", "Broker-Live", "investor-secret")
    provider.disconnect()
    assert captured.value.diagnostic_code == -10005
    assert fake.shutdown_calls == 1


def test_main_mt5_password_is_accepted_when_read_only_enforcement_is_disabled(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_READ_ONLY", "false")

    class TradingEnabledMT5:
        @staticmethod
        def initialize(*args, **kwargs):
            return True

        @staticmethod
        def account_info():
            return type("Info", (), {
                "login": 123456, "company": "Broker", "server": "Broker-Live",
                "currency": "EUR", "balance": 1000, "equity": 1000,
                "margin": 0, "margin_free": 1000, "leverage": 100,
                "trade_allowed": True, "trade_mode": 0,
            })()

        @staticmethod
        def shutdown():
            return None

    provider = MetaTrader5Provider("C:\\MT5\\NODE-01\\terminal64.exe")
    provider._mt5 = TradingEnabledMT5()
    assert provider.connect("123456", "Broker-Live", "main-password").access_mode == "TRADING_ALLOWED"


def test_mt5_ipc_timeout_is_retryable_terminal_error():
    class FailingMT5:
        @staticmethod
        def initialize(*args, **kwargs):
            return False

        @staticmethod
        def last_error():
            return (-10005, "IPC timeout")

    provider = MetaTrader5Provider("C:\\MT5\\NODE-01\\terminal64.exe")
    provider._mt5 = FailingMT5()
    with pytest.raises(ProviderError) as error:
        provider.connect("123456", "Broker-Live", "investor-secret")
    assert error.value.code == "TERMINAL_ERROR"
    assert error.value.retryable


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


def test_mt5_slots_are_process_isolated():
    source = (ROOT / "trading_journal" / "worker.py").read_text(encoding="utf-8")
    assert 'multiprocessing.get_context("spawn")' in source
    assert "ThreadPoolExecutor" not in source
