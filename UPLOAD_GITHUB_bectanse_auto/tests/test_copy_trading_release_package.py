import json
import sys
import zipfile

from tools import build_copy_trading_release as release


def test_release_archive_is_deterministic_and_contains_only_allowlisted_files(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_hash = release.write_archive(release.ROOT, first, "a" * 40)
    second_hash = release.write_archive(release.ROOT, second, "a" * 40)

    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        version = (release.ROOT / "COPY_TRADING_RELEASE_VERSION").read_text(encoding="utf-8").strip()
        assert all(name.startswith(f"bectanse-copy-trading-release-{version}/") for name in names)
        assert not any(".env" in name or ".git" in name or "__pycache__" in name for name in names)
        assert not any(name.endswith((".log", ".sqlite", ".db")) for name in names)
        manifest_name = next(name for name in names if name.endswith("COPY_TRADING_RELEASE_MANIFEST.json"))
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["source_commit"] == "a" * 40
        assert manifest["runtime"] == {"architecture": "x64", "os": "windows", "python": "3.14.10"}
        listed = {entry["path"] for entry in manifest["files"]}
        assert "copy_trading/worker.py" in listed
        assert "scripts/windows/copy-trading-rollback.ps1" in listed
        assert "scripts/windows/copy-trading-validate.ps1" in listed
        assert "requirements-copy-trading-worker.lock" in listed
        assert all(name.split("/", 1)[1] in listed or name.endswith("COPY_TRADING_RELEASE_MANIFEST.json")
                   for name in names)


def test_release_builder_rejects_hidden_and_parent_paths():
    for unsafe in ("../secret", ".env", "scripts/../../secret", "folder\\secret"):
        try:
            release._safe_archive_path(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe archive path accepted: {unsafe}")


def test_distribution_build_requires_clean_committed_sources(monkeypatch):
    monkeypatch.setattr(release.subprocess, "check_output", lambda *args, **kwargs: " M copy_trading/worker.py\n")
    try:
        release.ensure_clean_source(release.ROOT)
    except RuntimeError as error:
        assert "copy_trading/worker.py" in str(error)
    else:
        raise AssertionError("dirty source was accepted")


def test_windows_install_and_probe_scripts_do_not_kill_mt5_or_pass_password_as_argument():
    install_mt5 = (release.ROOT / "scripts/windows/install-mt5-terminals.ps1").read_text(encoding="utf-8")
    smoke = (release.ROOT / "scripts/windows/test-terminal-interactive.ps1").read_text(encoding="utf-8")

    assert "Stop-Process" not in install_mt5
    assert "download.mql5.com" in install_mt5
    assert "download.terminal.free" not in install_mt5
    assert "--password" not in smoke
    assert "-Password" not in smoke
    assert "--prompt-demo-credentials" in smoke


def test_windows_installer_runtime_probe_avoids_nested_native_argument_quotes():
    installer = (
        release.ROOT / "scripts/windows/install-copy-trading-worker.ps1"
    ).read_text(encoding="utf-8")

    assert "chr(124)" in installer
    assert "-split '\\|'" in installer
    assert "json.dumps" not in installer


def test_single_command_windows_validator_is_read_only_and_demo_scoped():
    validator = (release.ROOT / "scripts/windows/copy-trading-validate.ps1").read_text(encoding="utf-8")
    probe = (release.ROOT / "tools/probe_mt5_terminal_demo.py").read_text(encoding="utf-8")

    assert "ExpectedMasterLogin" in validator and "ExpectedFollowerLogin" in validator
    assert "ExpectedMasterMemberCode" in validator and "ExpectedFollowerMemberCode" in validator
    assert "copy-trading-health.ps1" in validator
    assert "test-terminal-interactive.ps1" in validator
    assert "/admin/api/copy-trading/operations" in validator
    assert "-Method Get" in validator
    assert "Start-Process" in validator
    assert "orders_sent_by_validation = $false" in validator
    assert "order_send" not in validator and "order_send" not in probe
    assert "ACCOUNT_TRADE_MODE_DEMO" in probe
