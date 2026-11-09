from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from copy_trading.domain import (
    CopyDecisionError,
    academy_settings,
    enabled_unsupported_academy_guards,
    ensure_event_fresh,
    event_expiry,
    event_idempotency_key,
    execution_release_allowed,
    normalized_volume,
    requested_volume,
    verify_native_symbol_mapping,
    worker_claims_enabled,
)
from copy_trading.providers.mt5 import MetaTrader5ExecutionProvider
from copy_trading.security import (
    canonical_copy_signature,
    verify_copy_worker_request,
)
from copy_trading.service import CopyTradingService, _native_update_datetime, _native_update_msc
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


def test_enabled_academy_drawdown_and_objective_guards_are_explicitly_unsupported():
    assert enabled_unsupported_academy_guards({
        "drawdown_actif": "false", "drawdown_gain_actif": 0, "objectif_actif": False,
    }) == ()
    assert enabled_unsupported_academy_guards({
        "drawdown_actif": "on", "drawdown_gain_actif": True, "objectif_actif": "1",
    }) == ("drawdown_actif", "drawdown_gain_actif", "objectif_actif")


def test_calendar_state_is_ignored_when_academy_filter_is_off():
    class NeverConnect:
        def __call__(self):
            raise AssertionError("calendar storage must not be queried when filter is off")

    service = CopyTradingService(NeverConnect(), lambda _code: {}, lambda _member: True,
                                 consent_version="v1")
    allowed, reason = service.calendar_allows(academy_settings({"filtre_news": False}))
    assert allowed is True
    assert reason == "FILTER_DISABLED"


def test_calendar_filter_fails_closed_when_durable_feed_is_stale():
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
    assert conn.calls == 2


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
              "native_postcondition": {"terminal_connected": True, "positions_complete": True,
                                        "login": "123", "server": "Demo-1", "matches": [{
                                            "ticket": "88", "position_id": "88", "symbol": "EURUSD",
                                            "side": "BUY", "volume": "0.2", "comment": "BCT-job",
                                        }]}}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="OPEN", evidence=evidence, request_comment="BCT-job", result=opened,
    )
    opened["native_postcondition"]["matches"][0]["volume"] = "0.3"
    assert not CopyTradingService._verify_native_postcondition(
        execution_action="OPEN", evidence=evidence, request_comment="BCT-job", result=opened,
    )
    close = {"volume": "0.3", "native_postcondition": {
        "terminal_connected": True, "positions_complete": True, "login": "123",
        "server": "Demo-1", "matches": [],
    }}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="CLOSE", evidence=evidence, request_comment="", result=close,
    )
    resize = {"volume": "0.1", "native_postcondition": {
        "terminal_connected": True, "positions_complete": True, "login": "123", "server": "Demo-1",
        "matches": [{"ticket": "77", "position_id": "77", "symbol": "EURUSD", "volume": "0.2"}],
    }}
    assert CopyTradingService._verify_native_postcondition(
        execution_action="RESIZE_CLOSE", evidence=evidence, request_comment="", result=resize,
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
