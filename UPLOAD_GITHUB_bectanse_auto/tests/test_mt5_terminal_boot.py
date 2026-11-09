import json
import sys

from tools import probe_mt5_terminal_demo as terminal_boot


class _Terminal:
    connected = True
    path = r"C:\Bectanse\CopyTradingTerminals\NODE-01"
    build = 6000


class _Account:
    login = 123456
    trade_mode = 0


class _FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self):
        self.initialize_kwargs = None
        self.shutdown_calls = 0

    def initialize(self, *args, **kwargs):
        self.initialize_kwargs = (args, kwargs)
        return True

    @staticmethod
    def terminal_info():
        return _Terminal()

    @staticmethod
    def account_info():
        return _Account()

    @staticmethod
    def last_error():
        return (1, "Success")

    def shutdown(self):
        self.shutdown_calls += 1


def _run(monkeypatch, capsys, argv, *, inputs=(), password=""):
    fake = _FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    input_values = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(input_values))
    monkeypatch.setattr(terminal_boot.getpass, "getpass", lambda _prompt="": password)
    monkeypatch.setattr(sys, "argv", ["probe_mt5_terminal_demo.py", *argv])
    result = terminal_boot.main()
    output = capsys.readouterr().out
    return result, json.loads(output), fake, output


def test_terminal_boot_probe_verifies_native_demo_path_without_orders(monkeypatch, capsys):
    result, payload, fake, _output = _run(
        monkeypatch, capsys, ["--terminal-path", r"C:\Bectanse\CopyTradingTerminals\NODE-01\terminal64.exe"]
    )

    assert result == 0
    assert payload["connected"] is True
    assert payload["terminal_path_verified"] is True
    assert payload["demo_account_verified"] is True
    assert payload["login_masked"].endswith("3456")
    assert fake.initialize_kwargs[1]["portable"] is True
    assert fake.shutdown_calls == 1


def test_terminal_boot_prompts_for_credentials_without_exposing_password(monkeypatch, capsys):
    result, payload, fake, output = _run(
        monkeypatch,
        capsys,
        ["--terminal-path", r"C:\Bectanse\CopyTradingTerminals\NODE-01\terminal64.exe",
         "--prompt-demo-credentials"],
        inputs=("123456", "Demo-Server"),
        password="not-a-real-secret",
    )

    assert result == 0
    assert payload["demo_account_verified"] is True
    assert fake.initialize_kwargs[1]["password"] == "not-a-real-secret"
    assert "not-a-real-secret" not in output


def test_terminal_boot_fails_closed_for_non_demo_account(monkeypatch, capsys):
    class _LiveAccount:
        login = 123456
        trade_mode = 2

    fake_account = _LiveAccount()
    fake = _FakeMT5()
    fake.account_info = lambda: fake_account
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setattr(sys, "argv", ["probe_mt5_terminal_demo.py", "--terminal-path",
                                      r"C:\Bectanse\CopyTradingTerminals\NODE-01\terminal64.exe"])

    result = terminal_boot.main()
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["demo_account_verified"] is False
    assert fake.shutdown_calls == 1
