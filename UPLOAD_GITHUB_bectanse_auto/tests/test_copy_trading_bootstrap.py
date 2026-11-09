import base64
import os


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test-public")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test-private")
os.environ["BECTANSE_SKIP_STARTUP"] = "1"

import app as bectanse_app
from trading_journal import migrate


class _Connection:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    def run(self, sql):
        assert sql == "SELECT 1"
        if self.fail:
            raise RuntimeError("database unavailable")
        return [(1,)]

    def close(self):
        self.closed = True


def test_health_waits_until_database_migrations_finish():
    bectanse_app._DATABASE_READY.clear()
    response = bectanse_app.app.test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json() == {"status": "starting", "database": "not_ready"}


def test_health_checks_live_database_after_startup(monkeypatch):
    connection = _Connection()
    monkeypatch.setattr(bectanse_app, "get_conn", lambda: connection)
    bectanse_app._DATABASE_READY.set()
    response = bectanse_app.app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}
    assert connection.closed is True
    bectanse_app._DATABASE_READY.clear()


def test_health_fails_if_database_stops_responding(monkeypatch):
    connection = _Connection(fail=True)
    monkeypatch.setattr(bectanse_app, "get_conn", lambda: connection)
    bectanse_app._DATABASE_READY.set()
    response = bectanse_app.app.test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json() == {"status": "not_ready", "database": "unavailable"}
    assert connection.closed is True
    bectanse_app._DATABASE_READY.clear()


def test_deployment_migration_runs_journal_and_copy_schemas(monkeypatch, capsys):
    connection = _Connection()
    calls = []
    monkeypatch.setattr(migrate, "connection_from_environment", lambda: connection)
    monkeypatch.setattr(migrate, "ensure_trading_schema", lambda conn: calls.append(("journal", conn)))
    monkeypatch.setattr(migrate, "ensure_copy_schema", lambda conn: calls.append(("copy", conn)))

    migrate.main()

    assert calls == [("journal", connection), ("copy", connection)]
    assert connection.closed is True
    assert "trading journal and copy trading migrations: OK" in capsys.readouterr().out
