from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from copy_trading.domain import (
    CopyDecisionError,
    academy_settings,
    enabled_unsupported_academy_guards,
    enabled_unsupported_pending_order_settings,
    evaluate_academy_protection,
    ensure_event_fresh,
    event_expiry,
    event_idempotency_key,
    execution_release_allowed,
    normalized_volume,
    requested_volume,
    verify_native_symbol_mapping,
    worker_claims_enabled,
)
from copy_trading.config import CopyTradingConfigurationError, validate_worker_config
from copy_trading.providers.mt5 import MetaTrader5ExecutionProvider
from copy_trading.security import (
    canonical_copy_signature,
    terminal_account_fingerprint,
    verify_copy_worker_request,
)
from copy_trading.service import (
    CopyTradingService, _native_update_datetime, _native_update_msc,
    _pending_event_source_time, _pending_terminal_event,
)
from trading_journal.providers.base import ProviderError


def test_event_expiry_is_absolute_120_seconds_and_requires_aware_time():
    source = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert event_expiry(source) == source + timedelta(seconds=120)
    assert ensure_event_fresh(source, source + timedelta(seconds=119))[1] == 1
    with pytest.raises(CopyDecisionError, match="120 secondes"):
        ensure_event_fresh(source, source + timedelta(seconds=120))
    with pytest.raises(CopyDecisionError):
        event_expiry(datetime(2026, 11, 8, 10, 0))


def test_native_mt5_epoch_milliseconds_are_the_event_source_time():
    source = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    stamp = int(source.timestamp() * 1000)
    assert _native_update_msc(stamp) == stamp
    assert _native_update_datetime(stamp) == source
    assert _native_update_msc(0) is None
    assert _native_update_datetime("invalid") is None


def test_pending_order_first_seen_uses_only_fresh_native_setup_time():
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(seconds=30)
    stale = now - timedelta(seconds=121)
    assert _pending_event_source_time(
        {"time_setup_msc": int(fresh.timestamp() * 1000)}, None, now,
    ) == fresh
    assert _pending_event_source_time(
        {"time_setup_msc": int(stale.timestamp() * 1000)}, None, now,
    ) is None
    assert _pending_event_source_time({"time_setup_msc": 0}, None, now) is None


def test_pending_order_modification_is_anchored_to_last_complete_snapshot():
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    previous_seen = now - timedelta(seconds=90)
    old = ["9", "EURUSD", "BUY", "BUY_LIMIT", Decimal("0.1"), Decimal("1.0"),
           Decimal("0"), Decimal("0"), Decimal("0"), None, 0, "v1", True, 0,
           previous_seen]
    # A fresh-looking setup timestamp cannot extend a modification's TTL.
    assert _pending_event_source_time(
        {"time_setup_msc": int(now.timestamp() * 1000)}, old, now,
    ) == previous_seen
    old[14] = now - timedelta(seconds=121)
    assert _pending_event_source_time(
        {"time_setup_msc": int(now.timestamp() * 1000)}, old, now,
    ) is None


@pytest.mark.parametrize("state, expected", [
    ("CANCELED", "PENDING_CANCEL"), ("REJECTED", "PENDING_CANCEL"),
    ("EXPIRED", "PENDING_EXPIRE"), ("FILLED", "QUARANTINE"),
])
def test_pending_terminal_order_state_requires_fresh_native_done_time(state, expected):
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(seconds=30)
    stale = now - timedelta(seconds=121)
    action, source_time = _pending_terminal_event(
        {"state": state, "time_done_msc": int(fresh.timestamp() * 1000)}, now,
    )
    assert action == expected
    assert source_time == (None if expected == "QUARANTINE" else fresh)
    if state != "FILLED":
        assert _pending_terminal_event(
            {"state": state, "time_done_msc": int(stale.timestamp() * 1000)}, now,
        ) == ("QUARANTINE", None)
    assert _pending_terminal_event({"state": state, "time_done_msc": 0}, now) == ("QUARANTINE", None)


def test_pending_quarantine_blocks_linked_accounts_and_unfinished_create_jobs():
    class Connection:
        def __init__(self):
            self.calls = []

        def run(self, sql, **params):
            self.calls.append((sql, params))
            if "SELECT follower_member_code FROM copy_trading_pending_order_links" in sql:
                return [("FOLLOWER-LINKED",)]
            if "SELECT j.job_id::text,j.follower_member_code" in sql:
                return [("job-1", "FOLLOWER-CREATING")]
            return []

    connection = Connection()
    CopyTradingService._require_pending_order_reconciliation(
        connection, "MASTER", "123", "MASTER_PENDING_ORDER_FILLED_RECONCILIATION_REQUIRED",
    )
    sql = [call[0] for call in connection.calls]
    assert any("UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED'" in statement for statement in sql)
    assert sum("INSERT INTO copy_trading_follower_locks" in statement for statement in sql) == 2
    assert sum("UPDATE copy_trading_pending_order_links SET state='VERIFY_REQUIRED'" in statement
               for statement in sql) == 2
    assert sum(call[1].get("event") == "PENDING_ORDER_RECONCILIATION_REQUIRED"
               for call in connection.calls) == 2


def test_idempotency_key_tracks_account_position_action_and_source_version():
    args = dict(master_login="123", master_server="Broker-Demo", position_id="p-1",
                action="OPEN", source_version="v1")
    original = event_idempotency_key(**args)
    assert original == event_idempotency_key(**args)
    assert original != event_idempotency_key(**{**args, "source_version": "v2"})
    assert original != event_idempotency_key(**{**args, "action": "CLOSE"})
    assert original != event_idempotency_key(**{**args, "master_server": "Broker-Live"})


def _symbol(name="EURUSD", *, contract="100000", tick="0.00001", trade_mode=4):
    return {
        "name": name, "trade_mode": trade_mode, "contract_size": contract,
        "tick_size": tick, "currency_base": "EUR", "currency_profit": "USD",
        "currency_margin": "EUR", "trade_calc_mode": 0,
    }


def test_native_mapping_auto_accepts_only_economically_aligned_exact_name():
    assert verify_native_symbol_mapping(_symbol(), _symbol())["state"] == "VERIFIED"
    alias = verify_native_symbol_mapping(_symbol(), _symbol("EURUSD.a"))
    assert alias["state"] == "PENDING_REVIEW"
    assert verify_native_symbol_mapping(_symbol(), _symbol("EURUSD.a"), reviewed_alias=True)["state"] == "VERIFIED"
    assert verify_native_symbol_mapping(_symbol(), _symbol("EURUSD.a", contract="1000"))["state"] == "REJECTED"
    assert verify_native_symbol_mapping(_symbol(), _symbol(trade_mode=3))["state"] == "REJECTED"


def test_academy_lot_modes_and_conservative_step_rounding():
    settings = academy_settings({"mode_risque": "Copier les lots de l'envoyeur", "multiplicateur": "0.5"})
    assert requested_volume(settings, source_symbol="EURUSD", source_volume=Decimal("0.3"),
                            balance=Decimal("1000"), equity=Decimal("900")) == Decimal("0.15")
    assert normalized_volume(Decimal("0.279"), minimum=Decimal("0.1"), step=Decimal("0.05"),
                             maximum=Decimal("10"), configured_max=Decimal("5")) == Decimal("0.25")
    with pytest.raises(CopyDecisionError, match="minimum"):
        normalized_volume(Decimal("0.05"), minimum=Decimal("0.1"), step=Decimal("0.01"),
                          maximum=Decimal("10"), configured_max=Decimal("5"))
    forced = normalized_volume(Decimal("0.05"), minimum=Decimal("0.1"), step=Decimal("0.01"),
                               maximum=Decimal("10"), configured_max=Decimal("5"), force_minimum=True)
    assert forced == Decimal("0.1")


def test_academy_risk_sizing_needs_native_stop_loss_calculation():
    settings = academy_settings({"mode_risque": "Risque par capitaux (Equity)", "risque_equity_pct": "2"})
    assert requested_volume(settings, source_symbol="EURUSD", source_volume=Decimal("1"),
                            balance=Decimal("1000"), equity=Decimal("1500"),
                            loss_per_lot=Decimal("50")) == Decimal("0.6")
    with pytest.raises(CopyDecisionError, match="stop"):
        requested_volume(settings, source_symbol="EURUSD", source_volume=Decimal("1"),
                         balance=Decimal("1000"), equity=Decimal("1500"))


def test_academy_drawdown_and_objective_settings_are_supported():
    assert enabled_unsupported_academy_guards({
        "drawdown_actif": "false", "drawdown_gain_actif": 0, "objectif_actif": False,
    }) == ()
    assert enabled_unsupported_academy_guards({
        "drawdown_actif": "on", "drawdown_gain_actif": True, "objectif_actif": "1",
    }) == ()


def test_drawdown_uses_durable_high_water_equity_and_includes_floating_pnl():
    settings = {"drawdown_actif": True, "drawdown_pct": 5}
    start = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
    state = None
    state = evaluate_academy_protection(settings, {"balance": "1000", "equity": "1000"}, state, start)
    assert not state["suspended"]
    state = evaluate_academy_protection(settings, {"balance": "1000", "equity": "1100"}, state,
                                        start + timedelta(seconds=10))
    assert not state["suspended"]
    state = evaluate_academy_protection(settings, {"balance": "1050", "equity": "1045"}, state,
                                        start + timedelta(seconds=20))
    assert state["suspended"]
    assert state["trigger"] == "DRAWDOWN_LOSS"
    assert state["loss_drawdown_pct"] == Decimal("5")
    assert state["baseline_balance"] == Decimal("1000")
    # A process restart uses the persisted state and does not reset the high-water mark.
    restarted = evaluate_academy_protection(settings, {"balance": "1070", "equity": "1070"}, state,
                                            start + timedelta(seconds=40))
    assert restarted["suspended"]
    assert restarted["suspension_reason"] == "DRAWDOWN_LOSS"


def test_profit_cap_and_period_objectives_use_documented_bases():
    start = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
    profit_settings = {"drawdown_gain_actif": True, "drawdown_gain_pct": 5}
    state = evaluate_academy_protection(profit_settings, {"balance": 1000, "equity": 1000}, None, start)
    state = evaluate_academy_protection(profit_settings, {"balance": 1000, "equity": 1050}, state,
                                        start + timedelta(seconds=1))
    assert state["trigger"] == "DRAWDOWN_GAIN"
    objective_settings = {"objectif_actif": True, "objectif_gain_pct": 5,
                          "objectif_perte_pct": 3, "objectif_periode": "Quotidien"}
    state = evaluate_academy_protection(objective_settings, {"balance": 1000, "equity": 1200}, None, start)
    assert not state["suspended"]
    state = evaluate_academy_protection(objective_settings, {"balance": 1050, "equity": 1200}, state,
                                        start + timedelta(seconds=1))
    assert state["trigger"] == "OBJECTIVE_GAIN"
    assert state["objective_pct"] == Decimal("5")
    next_day = evaluate_academy_protection(objective_settings, {"balance": 1050, "equity": 1050}, state,
                                           start + timedelta(days=1))
    assert next_day["suspended"]  # rollover is applied by the service only after close links reconcile
    assert next_day["period_changed"] is True
    requested = evaluate_academy_protection(objective_settings,
        {"balance": 1050, "equity": 1050}, {**next_day, "resume_requested": True},
        start + timedelta(days=1))
    assert not requested["suspended"]
    assert requested["baseline_balance"] == Decimal("1050")


def test_objective_loss_and_invalid_native_samples_fail_closed():
    start = datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
    settings = {"objectif_actif": True, "objectif_gain_pct": 5,
                "objectif_perte_pct": 3, "objectif_periode": "Mensuel"}
    state = evaluate_academy_protection(settings, {"balance": 1000, "equity": 1000}, None, start)
    state = evaluate_academy_protection(settings, {"balance": 970, "equity": 900}, state,
                                        start + timedelta(seconds=1))
    assert state["trigger"] == "OBJECTIVE_LOSS"
    with pytest.raises(CopyDecisionError, match="balance"):
        evaluate_academy_protection(settings, {"balance": "NaN", "equity": "1000"}, state, start)


def _protection_pending_link_row():
    return ("MASTER", "9", "88", "EURUSD", "EURUSD", "BUY_LIMIT", "BUY_LIMIT",
            Decimal("0.2"), Decimal("1.08"), Decimal("1.07"), Decimal("1.10"),
            Decimal("0"), None, 0)


class _ProtectionPendingConnection:
    def __init__(self):
        self.now = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
        self.calls = []

    def run(self, sql, **params):
        self.calls.append((sql, params))
        if "FROM copy_trading_pending_order_links" in sql and "SELECT master_member_code" in sql:
            return [_protection_pending_link_row()]
        if "SELECT j.status" in sql:
            return []
        if "SELECT COUNT(*)" in sql:
            return [(0,)]
        if sql.strip() == "SELECT NOW()":
            return [(self.now,)]
        if "INSERT INTO copy_trading_events" in sql:
            return [("event-pending-protection",)]
        return []


def _protection_follower_order(**overrides):
    order = {
        "order_ticket": "88", "symbol": "EURUSD", "pending_type": "BUY_LIMIT",
        "side": "BUY", "volume": "0.2", "price_open": "1.08",
        "stop_loss": "1.07", "take_profit": "1.10", "stop_limit": "0",
        "time_type": 0, "expiration_at": None,
    }
    return {**order, **overrides}


def test_academy_protection_queues_cancel_for_exact_linked_pending_order_when_copy_disabled():
    conn = _ProtectionPendingConnection()
    service = CopyTradingService(
        lambda: conn,
        lambda code: {"params": {"mt_login": "123", "mt_server": "Demo-1"}}
        if code == "MASTER" else {"params": {"copier_ordres_en_attente": False}},
        lambda _member: True,
        consent_version="v1",
    )
    service._queue_protection_pending_cancels(
        conn, "FOLLOWER", {"params": {"copier_ordres_en_attente": False}},
        [_protection_follower_order()], trigger="DRAWDOWN_LOSS", triggered_at=conn.now,
    )
    job_inserts = [(sql, params) for sql, params in conn.calls if "INSERT INTO copy_trading_jobs" in sql]
    assert len(job_inserts) == 1
    sql, params = job_inserts[0]
    assert "protection_close" in sql and "TRUE" in sql
    assert params["follower"] == "FOLLOWER"
    assert params["event"] == "event-pending-protection"
    event = next(params for sql, params in conn.calls if "INSERT INTO copy_trading_events" in sql)
    assert event["action"] == "PENDING_CANCEL"
    assert event["position"] == "9"


def test_academy_protection_quarantines_missing_pending_order_instead_of_canceling_blindly():
    conn = _ProtectionPendingConnection()
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    service._queue_protection_pending_cancels(
        conn, "FOLLOWER", {"params": {}}, [],
        trigger="DRAWDOWN_LOSS", triggered_at=conn.now,
    )
    statements = [sql for sql, _params in conn.calls]
    assert any("SET state='VERIFY_REQUIRED'" in sql and "copy_trading_pending_order_links" in sql
               for sql in statements)
    lock = next(params for sql, params in conn.calls if "INSERT INTO copy_trading_follower_locks" in sql)
    assert lock["reason"] == "ACADEMY_PROTECTION:PENDING_ORDER_RECONCILIATION_REQUIRED"
    assert not any("INSERT INTO copy_trading_events" in sql for sql in statements)


@pytest.mark.parametrize("terminal_state", ["CANCELED", "REJECTED", "EXPIRED"])
def test_missing_follower_pending_order_closes_link_only_with_terminal_native_history(terminal_state):
    conn = _ProtectionPendingConnection()
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    result = service._resolve_missing_pending_order(
        conn, job_id="job-1", follower_code="FOLLOWER", master_code="MASTER",
        order_ticket="9", market={
            "follower_pending_order_ticket": "88", "pending_order_history_complete": True,
            "pending_order_history": [{"order_ticket": "88", "state": terminal_state}],
        },
    )
    assert result == {"status": "COMPLETE", "execution_action": "NOOP"}
    assert any("SET state='CLOSED'" in sql for sql, _params in conn.calls)


@pytest.mark.parametrize("history, expected_reason", [
    ([{"order_ticket": "88", "state": "FILLED"}],
     "PENDING_ORDER_FILLED_REQUIRES_POSITION_RECONCILIATION"),
    ([], "PENDING_ORDER_HISTORY_UNVERIFIED"),
])
def test_missing_follower_pending_order_is_quarantined_if_filled_or_history_unknown(history, expected_reason):
    conn = _ProtectionPendingConnection()
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    result = service._resolve_missing_pending_order(
        conn, job_id="job-1", follower_code="FOLLOWER", master_code="MASTER",
        order_ticket="9", market={
            "follower_pending_order_ticket": "88", "pending_order_history_complete": True,
            "pending_order_history": history,
        },
    )
    assert result == {"status": "VERIFY_REQUIRED", "execution_action": "NOOP"}
    lock = next(params for sql, params in conn.calls if "INSERT INTO copy_trading_follower_locks" in sql)
    assert lock["reason"] == expected_reason
    assert any("SET state='VERIFY_REQUIRED'" in sql for sql, _params in conn.calls)


def test_pending_order_copy_is_supported_but_conversion_remains_disabled():
    assert enabled_unsupported_pending_order_settings({
        "copier_ordres_en_attente": "false", "convertir_pending_invalide": False,
    }) == ()
    assert enabled_unsupported_pending_order_settings({
        "copier_ordres_en_attente": "on", "convertir_pending_invalide": True,
    }) == ("convertir_pending_invalide",)


def test_calendar_state_is_ignored_when_academy_filter_is_off():
    class NeverConnect:
        def __call__(self):
            raise AssertionError("calendar storage must not be queried when filter is off")

    service = CopyTradingService(NeverConnect(), lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": False}))
    assert allowed is True
    assert reason == "FILTER_DISABLED"


def test_calendar_filter_fails_closed_when_durable_feed_is_stale(monkeypatch):
    monkeypatch.setenv("COPY_TRADING_CALENDAR_PROVIDER", "tradingeconomics")
    monkeypatch.setenv("COPY_TRADING_CALENDAR_FAILURE_POLICY", "BLOCK")
    class EmptyCalendar:
        columns = []

        def __init__(self):
            self.calls = 0

        def run(self, _sql, **_params):
            self.calls += 1
            return []

        def close(self):
            pass

    conn = EmptyCalendar()
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": True}))
    assert allowed is False
    assert reason == "CALENDAR_STALE_OR_UNAVAILABLE"
    assert conn.calls == 1


@pytest.mark.parametrize(
    "mode,enabled,live,demo,expected",
    [("simulation", True, True, True, False), ("demo", False, False, True, False),
     ("demo", True, False, True, True), ("demo", True, True, False, False),
     ("live", True, False, False, False), ("live", True, True, False, True),
     ("live", True, True, True, False)],
)
def test_runtime_execution_release_gates(mode, enabled, live, demo, expected):
    assert execution_release_allowed(mode=mode, execution_enabled=enabled,
                                     live_release_approved=live, account_is_demo=demo) is expected


def test_follower_slots_can_claim_live_jobs_only_after_global_release():
    assert not worker_claims_enabled(mode="simulation", execution_enabled=True,
                                     live_release_approved=True)
    assert worker_claims_enabled(mode="demo", execution_enabled=True,
                                 live_release_approved=False)
    assert not worker_claims_enabled(mode="live", execution_enabled=True,
                                     live_release_approved=False)
    assert worker_claims_enabled(mode="live", execution_enabled=True,
                                 live_release_approved=True)


def test_worker_signature_binds_role_identity_path_and_body():
    secret, body, timestamp, nonce = "x" * 40, b'{"job":"1"}', "1794132000", "nonce-1"
    sig = canonical_copy_signature(secret, "worker-a", "FOLLOWER", "POST", "/jobs/1", timestamp, body, nonce)
    check = lambda role="FOLLOWER", worker="worker-a", path="/jobs/1", raw=body: verify_copy_worker_request(
        secret, "POST", path, timestamp, raw, sig, nonce, worker, role, now=1794132000,
    )
    assert check()
    assert not check(role="MASTER")
    assert not check(worker="worker-b")
    assert not check(path="/jobs/2")
    assert not check(raw=b"{}")
    assert not verify_copy_worker_request(secret, "POST", "/jobs/1", timestamp,
                                          body, sig, nonce, "worker-a", "FOLLOWER",
                                          now=1794132031)


def test_terminal_account_fingerprint_is_secret_bound_and_does_not_expose_login():
    first = terminal_account_fingerprint("s" * 40, "123456", "Broker-Demo")
    assert len(first) == 64
    assert first == terminal_account_fingerprint("s" * 40, "123456", "broker-demo")
    assert first != terminal_account_fingerprint("x" * 40, "123456", "Broker-Demo")
    assert first != terminal_account_fingerprint("s" * 40, "654321", "Broker-Demo")
    assert "123456" not in first


def test_worker_config_rejects_terminals_sharing_portable_ipc_directory(monkeypatch):
    values = {
        "COPY_TRADING_BACKEND_URL": "https://backend.example",
        "COPY_TRADING_MASTER_WORKER_SECRET": "m" * 40,
        "COPY_TRADING_FOLLOWER_WORKER_SECRET": "f" * 40,
        "COPY_TRADING_MASTER_MEMBER_CODE": "MASTER",
        "COPY_TRADING_WORKER_ID": "node-1",
        "MT5_COPY_MASTER_TERMINAL_PATH": r"C:\MT5\NODE-01\terminal64.exe",
        "MT5_COPY_TERMINAL_PATHS": r"C:\MT5\NODE-01\other-terminal64.exe",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(CopyTradingConfigurationError, match="portable data paths"):
        validate_worker_config()


def test_mt5_initialize_failure_shuts_down_stale_ipc_state():
    class NativeMT5:
        shutdown_calls = 0

        @staticmethod
        def initialize(*_args, **_kwargs):
            return False

        @staticmethod
        def last_error():
            return (-10005, "IPC timeout")

        def shutdown(self):
            self.shutdown_calls += 1

    native = NativeMT5()
    provider = MetaTrader5ExecutionProvider(r"C:\MT5\NODE-01\terminal64.exe")
    provider._mt5 = native
    with pytest.raises(ProviderError) as error:
        provider.connect("123", "Broker-Demo", "password")
    assert error.value.code == "TERMINAL_CONNECTION_FAILED"
    assert error.value.diagnostic_code == -10005
    assert native.shutdown_calls == 1
    assert provider._connected is False


def test_mt5_connection_requires_expected_terminal_process_path():
    class NativeMT5:
        shutdown_calls = 0

        @staticmethod
        def initialize(*_args, **_kwargs):
            return True

        @staticmethod
        def terminal_info():
            return SimpleNamespace(connected=True, path=r"C:\MT5\OTHER-NODE")

        @staticmethod
        def account_info():
            return SimpleNamespace(login=123, server="Broker-Demo", company="Broker",
                                  currency="USD", balance=1000, equity=1000,
                                  trade_mode=0, trade_allowed=True)

        @staticmethod
        def last_error():
            return (0, "OK")

        def shutdown(self):
            self.shutdown_calls += 1

    native = NativeMT5()
    provider = MetaTrader5ExecutionProvider(r"C:\MT5\NODE-01\terminal64.exe")
    provider._mt5 = native
    with pytest.raises(ProviderError) as error:
        provider.connect("123", "Broker-Demo", "password")
    assert error.value.code == "TERMINAL_PROCESS_PATH_MISMATCH"
    assert native.shutdown_calls == 1
    assert provider._connected is False


def test_mt5_connection_confirms_expected_terminal_process_path():
    class NativeMT5:
        shutdown_calls = 0

        @staticmethod
        def initialize(*_args, **_kwargs):
            return True

        @staticmethod
        def terminal_info():
            return SimpleNamespace(connected=True, path=r"C:\MT5\NODE-01", build=5420)

        @staticmethod
        def account_info():
            return SimpleNamespace(login=123, server="Broker-Demo", company="Broker",
                                  currency="USD", balance=1000, equity=1000,
                                  trade_mode=0, trade_allowed=True)

        @staticmethod
        def last_error():
            return (0, "OK")

        def shutdown(self):
            self.shutdown_calls += 1

    native = NativeMT5()
    provider = MetaTrader5ExecutionProvider(r"C:\MT5\NODE-01\terminal64.exe")
    provider._mt5 = native
    account = provider.connect("123", "Broker-Demo", "password")
    assert account["terminal_path_verified"] is True
    provider.disconnect()
    assert native.shutdown_calls == 1


def test_native_close_evidence_uses_latest_confirmed_exit_deal():
    class NativeMT5:
        DEAL_ENTRY_OUT = 1
        DEAL_ENTRY_OUT_BY = 3

        def history_deals_get(self, *, position):
            assert position == 123
            return [
                SimpleNamespace(position_id=123, ticket=7, entry=0, time_msc=1000),
                SimpleNamespace(position_id=123, ticket=8, entry=1, time_msc=2000),
                SimpleNamespace(position_id=123, ticket=9, entry=3, time_msc=3000),
            ]

    provider = MetaTrader5ExecutionProvider("terminal.exe")
    provider._mt5 = NativeMT5()
    assert provider.closing_deals(["123"])[0] == {
        "position_id": "123", "ticket": 9, "entry": 3, "time_msc": 3000,
    }


def test_native_close_evidence_fails_closed_on_history_read_error():
    class NativeMT5:
        def history_deals_get(self, *, position):
            return None

        @staticmethod
        def last_error():
            return (-10005, "diagnostic not exposed")

    provider = MetaTrader5ExecutionProvider("terminal.exe")
    provider._mt5 = NativeMT5()
    with pytest.raises(ProviderError) as error:
        provider.closing_deals(["123"])
    assert error.value.code == "TERMINAL_CONNECTION_FAILED"


def test_expired_master_close_blocks_followers_with_open_position_links():
    class ReconciliationConnection:
        def __init__(self):
            self.calls = []

        def run(self, sql, **params):
            self.calls.append((sql, params))
            if "SELECT follower_member_code FROM copy_trading_position_links" in sql:
                return [("FOLLOWER-1",), ("FOLLOWER-2",)]
            return []

    conn = ReconciliationConnection()
    CopyTradingService._require_position_reconciliation(
        conn, "MASTER", "position-7", "MASTER_CLOSE_EVENT_EXPIRED",
    )
    lock_inserts = [params for sql, params in conn.calls
                    if "INSERT INTO copy_trading_follower_locks" in sql]
    assert [item["follower"] for item in lock_inserts] == ["FOLLOWER-1", "FOLLOWER-2"]
    assert all(item["reason"] == "MASTER_CLOSE_EVENT_EXPIRED" for item in lock_inserts)


class _DispatchConnection:
    """Small DB double: verifies the completion transition without Postgres."""

    columns = []

    def __init__(self, state="DISPATCHING", existing_result=None):
        self.job_updates = []
        self.lock_blocked = False
        self.closed = False
        self.state = state
        self.existing_result = existing_result or {}

    def run(self, sql, **params):
        if "SELECT j.status,e.master_member_code" in sql:
            return [(self.state, "MASTER", "position-1", "OPEN", "EURUSD",
                     Decimal("0.2"), "FOLLOWER", "EURUSD", Decimal("0.2"), "OPEN",
                     {"market": {}, "plan": {}}, "BCT-job1", "event-1", "idem-1",
                     self.existing_result)]
        if "UPDATE copy_trading_jobs SET status=:status" in sql:
            self.job_updates.append(params["status"])
        elif "UPDATE copy_trading_jobs SET status='VERIFY_REQUIRED'" in sql:
            self.job_updates.append("VERIFY_REQUIRED")
        elif "UPDATE copy_trading_follower_locks SET blocked=TRUE" in sql:
            self.lock_blocked = True
        return []

    def close(self):
        self.closed = True


def test_dispatch_is_quarantined_without_native_postcondition_verification():
    conn = _DispatchConnection()
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    result = service.complete_dispatch("job-1", result={"confirmed": True, "verified": False,
                                                         "position_id": "ticket-1", "volume": "0.2"})
    assert result["status"] == "VERIFY_REQUIRED"
    assert conn.lock_blocked is True
    assert "COMPLETE" not in conn.job_updates


def test_dispatch_result_retry_is_idempotent_after_database_commit():
    result = {"confirmed": False, "ambiguous": True, "failure_code": "ORDER_RESULT_UNKNOWN"}
    conn = _DispatchConnection(state="VERIFY_REQUIRED", existing_result=result)
    service = CopyTradingService(lambda: conn, lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    returned = service.complete_dispatch("job-1", result=result)
    assert returned["status"] == "VERIFY_REQUIRED"
    assert returned["already_recorded"] is True


def test_native_postcondition_is_recomputed_from_terminal_evidence():
    evidence = {
        "market": {"login": "123", "server": "Demo-1"},
        "follower": _symbol(),
        "plan": {"side": "BUY", "position_ticket": "77",
                 "follower_before_volume": "0.3", "stop_loss": "1.08",
                 "take_profit": "1.12"},
    }
    opened = {"confirmed": True, "position_id": "88", "volume": "0.2",
              "native_postcondition": {"terminal_connected": True, "terminal_path_verified": True,
                                        "positions_complete": True,
                                        "login": "123", "server": "Demo-1", "matches": [{
                                            "ticket": "88", "position_id": "88", "symbol": "EURUSD",
                                            "side": "BUY", "volume": "0.2", "comment": "BCT-job",
                                        }]}}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="OPEN", evidence=evidence, request_comment="BCT-job", result=opened,
    )
    opened["native_postcondition"]["terminal_path_verified"] = False
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="OPEN", evidence=evidence, request_comment="BCT-job", result=opened,
    )
    opened["native_postcondition"]["terminal_path_verified"] = True
    opened["native_postcondition"]["matches"][0]["volume"] = "0.3"
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="OPEN", evidence=evidence, request_comment="BCT-job", result=opened,
    )
    close = {"volume": "0.3", "native_postcondition": {
        "terminal_connected": True, "terminal_path_verified": True,
        "positions_complete": True, "login": "123",
        "server": "Demo-1", "matches": [],
    }}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="CLOSE", evidence=evidence, request_comment="", result=close,
    )
    resize = {"volume": "0.1", "native_postcondition": {
        "terminal_connected": True, "terminal_path_verified": True,
        "positions_complete": True, "login": "123", "server": "Demo-1",
        "matches": [{"ticket": "77", "position_id": "77", "symbol": "EURUSD", "volume": "0.2"}],
    }}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="RESIZE_CLOSE", evidence=evidence, request_comment="", result=resize,
    )


def test_pending_order_postcondition_requires_complete_exact_native_order():
    evidence = {"market": {"login": "123", "server": "Demo-1"},
                "follower": {"name": "EURUSD"},
                "plan": {"action": "PENDING_CREATE", "master_order_ticket": "9",
                         "follower_order_ticket": "88", "pending_type": "BUY_LIMIT",
                         "side": "BUY", "volume": "0.2", "price": "1.08",
                         "stop_loss": "1.07", "take_profit": "1.10", "stop_limit": "0",
                         "time_type": 0, "expiration_at": None}}
    result = {"order_ticket": "88", "native_postcondition": {
        "terminal_connected": True, "terminal_path_verified": True,
        "orders_complete": True, "login": "123", "server": "Demo-1", "matches": [{
            "order_ticket": "88", "symbol": "EURUSD", "pending_type": "BUY_LIMIT",
            "side": "BUY", "volume": "0.2", "price_open": "1.08", "stop_loss": "1.07",
            "take_profit": "1.10", "stop_limit": "0", "time_type": "0",
            "expiration_at": "", "comment": "BCT-job",
        }]}}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="PENDING_CREATE", evidence=evidence,
        request_comment="BCT-job", result=result,
    )
    result["native_postcondition"]["orders_complete"] = False
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="PENDING_CREATE", evidence=evidence,
        request_comment="BCT-job", result=result,
    )
    modify_evidence = {
        **evidence,
        "plan": {**evidence["plan"], "action": "PENDING_MODIFY",
                 "follower_before": {"comment": "BCT-original"}},
    }
    result["native_postcondition"] = {**result["native_postcondition"],
        "orders_complete": True, "matches": [{
            "order_ticket": "88", "symbol": "EURUSD", "pending_type": "BUY_LIMIT",
            "side": "BUY", "volume": "0.2", "price_open": "1.08", "stop_loss": "1.07",
            "take_profit": "1.10", "stop_limit": "0", "time_type": "0",
            "expiration_at": "", "comment": "BCT-original",
        }]}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="PENDING_MODIFY", evidence=modify_evidence,
        request_comment="BCT-new-job", result=result,
    )
    result["native_postcondition"]["matches"][0]["comment"] = "BCT-new-job"
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="PENDING_MODIFY", evidence=modify_evidence,
        request_comment="BCT-new-job", result=result,
    )
    result["native_postcondition"] = {**result["native_postcondition"],
        "orders_complete": True, "matches": []}
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="PENDING_CREATE", evidence=evidence,
        request_comment="BCT-job", result=result,
    )


class _FakeMT5:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_TIMEOUT = 10012
    TRADE_RETCODE_CONNECTION = 10031

    def __init__(self, retcode):
        self.retcode = retcode
        self.order_send_calls = 0

    def order_send(self, _request):
        self.order_send_calls += 1
        return SimpleNamespace(retcode=self.retcode, order=7, deal=8, position=9, volume=0.1, comment="ok")

    def last_error(self):
        return (None, "")


def test_mt5_partial_execution_is_ambiguous_and_never_claimed_confirmed():
    provider = MetaTrader5ExecutionProvider("fake")
    fake = _FakeMT5(_FakeMT5.TRADE_RETCODE_DONE_PARTIAL)
    provider._mt5 = fake
    result = provider.submit_request({"action": "FAKE"})
    assert result["confirmed"] is False
    assert result["ambiguous"] is True
    assert fake.order_send_calls == 1


def test_mt5_order_check_failure_never_calls_order_send():
    class Fake(_FakeMT5):
        def order_check(self, _request):
            return SimpleNamespace(retcode=10016, comment="invalid stops")

    provider = MetaTrader5ExecutionProvider("fake")
    fake = Fake(10009)
    provider._mt5 = fake
    with pytest.raises(ProviderError, match="précontrôle"):
        provider.check_request({"action": "FAKE"})
    assert fake.order_send_calls == 0


def test_pending_request_uses_native_create_modify_and_remove_actions():
    class Fake:
        TRADE_ACTION_PENDING = 5
        TRADE_ACTION_MODIFY = 6
        TRADE_ACTION_REMOVE = 8
        ORDER_TYPE_BUY_LIMIT = 2
        ORDER_TYPE_SELL_LIMIT = 3
        ORDER_TYPE_BUY_STOP = 4
        ORDER_TYPE_SELL_STOP = 5
        ORDER_TYPE_BUY_STOP_LIMIT = 6
        ORDER_TYPE_SELL_STOP_LIMIT = 7
        ORDER_FILLING_RETURN = 2

    provider = MetaTrader5ExecutionProvider("fake")
    provider._mt5 = Fake()
    common = {"symbol": "EURUSD", "pending_type": "BUY_LIMIT", "volume": Decimal("0.2"),
              "price": Decimal("1.08"), "stop_loss": Decimal("1.07"),
              "take_profit": Decimal("1.10"), "stop_limit": Decimal("0"),
              "time_type": 0, "expiration_at": None, "comment": "BCT-test"}
    created = provider.pending_request(action="PENDING_CREATE", **common)
    assert created["action"] == Fake.TRADE_ACTION_PENDING
    assert created["volume"] == 0.2
    modified = provider.pending_request(action="PENDING_MODIFY", **common, order_ticket="88")
    assert modified["action"] == Fake.TRADE_ACTION_MODIFY
    assert modified["order"] == 88
    assert "volume" not in modified and "type" not in modified
    canceled = provider.pending_request(action="PENDING_CANCEL", **common, order_ticket="88")
    assert canceled == {"action": Fake.TRADE_ACTION_REMOVE, "order": 88}
    expired = provider.pending_request(action="PENDING_EXPIRE", **common, order_ticket="88")
    assert expired == {"action": Fake.TRADE_ACTION_REMOVE, "order": 88}


def test_pending_order_history_distinguishes_native_terminal_states_and_fails_on_missing_history():
    class Fake:
        ORDER_STATE_CANCELED = 2
        ORDER_STATE_FILLED = 4
        ORDER_STATE_REJECTED = 5
        ORDER_STATE_EXPIRED = 6

        def history_orders_get(self, *, ticket):
            states = {"1": self.ORDER_STATE_CANCELED, "2": self.ORDER_STATE_FILLED,
                      "3": self.ORDER_STATE_REJECTED, "4": self.ORDER_STATE_EXPIRED}
            return [SimpleNamespace(ticket=ticket, state=states[str(ticket)], time_done_msc=1234567890000)]

        def last_error(self):
            return (10004, "history unavailable")

    provider = MetaTrader5ExecutionProvider("fake")
    provider._mt5 = Fake()
    history = provider.pending_order_history(["1", "2", "3", "4", "invalid"])
    assert [row["state"] for row in history] == ["CANCELED", "FILLED", "REJECTED", "EXPIRED"]
    assert all(row["time_done_msc"] == 1234567890000 for row in history)

    class Failed(Fake):
        def history_orders_get(self, *, ticket):
            return None

    provider._mt5 = Failed()
    with pytest.raises(ProviderError) as error:
        provider.pending_order_history(["1"])
    assert error.value.code == "TERMINAL_CONNECTION_FAILED"
