import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parents[1] / "tools" / "probe_mt5_terminal_demo.py"
SPEC = importlib.util.spec_from_file_location("probe_mt5_terminal_demo", MODULE_PATH)
probe_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe_module)


def _fake_mt5(login=123456):
    return SimpleNamespace(
        initialize=lambda *args, **kwargs: True,
        terminal_info=lambda: SimpleNamespace(connected=True, path=r"C:\Bectanse\MT5\Master", build=5000),
        account_info=lambda: SimpleNamespace(login=login, trade_mode=0),
        ACCOUNT_TRADE_MODE_DEMO=0,
        last_error=lambda: (1, "ok"),
        shutdown=lambda: None,
    )


def test_demo_terminal_probe_requires_expected_login(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "MetaTrader5", _fake_mt5())
    monkeypatch.setattr(sys, "argv", [
        str(MODULE_PATH), "--terminal-path", r"C:\Bectanse\MT5\Master\terminal64.exe",
        "--expected-login", "123456",
    ])

    assert probe_module.main() == 0
    assert '"expected_login_matches":true' in capsys.readouterr().out


def test_demo_terminal_probe_fails_on_wrong_expected_login(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "MetaTrader5", _fake_mt5())
    monkeypatch.setattr(sys, "argv", [
        str(MODULE_PATH), "--terminal-path", r"C:\Bectanse\MT5\Master\terminal64.exe",
        "--expected-login", "999999",
    ])

    assert probe_module.main() == 1
    assert '"expected_login_matches":false' in capsys.readouterr().out
