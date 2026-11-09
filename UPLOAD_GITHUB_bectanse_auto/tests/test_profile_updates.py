import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch


os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("ADMIN_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/test")
os.environ.setdefault("VAPID_PUBLIC_KEY", "test")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test")
os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")

import app


class ProfileConnection:
    def __init__(self, params=None):
        self.params = app._protect_params(params or {})
        self.calls = []
        self.closed = False

    def run(self, query, **params):
        compact = " ".join(query.split()).lower()
        self.calls.append((compact, params))
        if compact.startswith("select date_fin, actif, params from members"):
            return [[datetime.now() + timedelta(days=30), True, json.dumps(self.params)]]
        if compact.startswith("select params from members"):
            return [[json.dumps(self.params)]]
        if compact.startswith("select historique from members"):
            return [["[]"]]
        if "update members set params=:p" in compact:
            self.params = json.loads(params["p"])
            return []
        if compact.startswith("update members set"):
            return []
        raise AssertionError(f"Unexpected query: {compact}")

    def close(self):
        self.closed = True


def _member(code="BCT-OWNER001", params=None):
    return {
        "code": code,
        "nom": "Membre Test",
        "access_level": "member",
        "actif": True,
        "date_fin": datetime.now() + timedelta(days=30),
        "params": params or {},
    }


def test_member_profile_update_is_scoped_to_the_logged_in_bct_account():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    connection = ProfileConnection({
        "mode_risque": "Lots fixes",
        "mt_password": "ancien-secret",
        "serveur": "Ancien-Serveur",
    })
    with client.session_transaction() as session:
        session["member_code"] = "BCT-OWNER001"

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=_member()), \
         patch.object(app, "get_conn", return_value=connection):
        response = client.post("/api/profil", json={
            "code": "BCT-OTHER999",
            "nom": "Nouveau Nom",
            "mt_login": "998877",
            "mt_server": "PUPrime-Live",
            "mt_password": "nouveau-secret",
        })

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    update_calls = [params for query, params in connection.calls if query.startswith("update members set")]
    assert update_calls
    assert all(params["c"] == "BCT-OWNER001" for params in update_calls)
    assert "nouveau-secret" not in json.dumps(connection.params)
    revealed = app._reveal_params(connection.params)
    assert revealed["mode_risque"] == "Lots fixes"
    assert revealed["mt_login"] == "998877"
    assert revealed["serveur"] == "PUPrime-Live"
    assert revealed["mt_password"] == "nouveau-secret"


def test_robot_settings_save_keeps_the_member_trading_profile():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    trading_profile = {
        "mt_login": "445566",
        "mt_password": "secret-mt",
        "serveur": "PUPrime-Live",
        "plateforme": "MT5",
    }
    member = _member(params=trading_profile)
    connection = ProfileConnection(trading_profile)
    with client.session_transaction() as session:
        session["member_code"] = member["code"]

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection), \
         patch.object(app, "build_notif", return_value="test"), \
         patch.object(app, "send_telegram"):
        response = client.post("/save", json={"mode_risque": "Lots fixes", "lots": 0.02})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    revealed = app._reveal_params(connection.params)
    assert revealed["mt_login"] == "445566"
    assert revealed["mt_password"] == "secret-mt"
    assert revealed["serveur"] == "PUPrime-Live"
    assert revealed["plateforme"] == "MT5"
    assert revealed["lots"] == 0.02


def test_robot_settings_persist_enabled_close_guards_for_native_monitoring():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    member = _member(params={"mt_login": "445566", "serveur": "PUPrime-Live"})
    connection = ProfileConnection(member["params"])
    with client.session_transaction() as session:
        session["member_code"] = member["code"]

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection), \
         patch.object(app, "build_notif", return_value="test"), \
         patch.object(app, "send_telegram"):
        response = client.post("/save", json={"drawdown_actif": True})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    saved = app._reveal_params(connection.params)
    assert saved["drawdown_actif"] is True
    assert saved["drawdown_pct"] == 5.0


def test_robot_settings_enable_pending_copy_but_keep_calendar_closed_without_provider():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    member = _member(params={"mt_login": "445566", "serveur": "PUPrime-Live"})
    connection = ProfileConnection(member["params"])
    with client.session_transaction() as session:
        session["member_code"] = member["code"]

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection), \
         patch.object(app.copy_trading_service, "calendar_allows",
                      return_value=(False, "CALENDAR_PROVIDER_NOT_CONFIGURED")):
        pending = client.post("/save", json={"copier_ordres_en_attente": True})
        calendar = client.post("/save", json={"filtre_news": True})

    assert pending.status_code == 200
    assert app._reveal_params(connection.params)["copier_ordres_en_attente"] is True
    assert calendar.status_code == 409
    assert calendar.get_json()["code"] == "CALENDAR_PROVIDER_NOT_CONFIGURED"
    assert sum(query.startswith("update members set params=") for query, _ in connection.calls) == 1


def test_calendar_profile_settings_are_persisted_after_a_fresh_provider_check():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    member = _member(params={"mt_login": "445566", "serveur": "PUPrime-Live"})
    connection = ProfileConnection(member["params"])
    with client.session_transaction() as session:
        session["member_code"] = member["code"]
    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection), \
         patch.object(app.copy_trading_service, "calendar_allows",
                      return_value=(True, "NO_BLOCKING_WINDOW")), \
         patch.object(app, "build_notif", return_value="test"), \
         patch.object(app, "send_telegram"):
        response = client.post("/save", json={
            "filtre_news": True, "calendar_currencies": "usd, EUR,USD",
            "calendar_min_importance": 3, "calendar_minutes_before": 45,
            "calendar_minutes_after": 60, "calendar_timezone": "Europe/Paris",
        })
    assert response.status_code == 200
    saved = app._reveal_params(connection.params)
    assert saved["calendar_currencies"] == "EUR,USD"
    assert saved["calendar_min_importance"] == 3
    assert saved["calendar_minutes_before"] == 45
    assert saved["calendar_minutes_after"] == 60
    assert saved["calendar_timezone"] == "Europe/Paris"


def test_invalid_calendar_timezone_is_rejected_before_profile_write():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    member = _member(params={"mt_login": "445566", "serveur": "PUPrime-Live"})
    connection = ProfileConnection(member["params"])
    with client.session_transaction() as session:
        session["member_code"] = member["code"]
    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection):
        response = client.post("/save", json={"calendar_timezone": "not/a-timezone"})
    assert response.status_code == 400
    assert response.get_json()["code"] == "ACADEMY_SETTINGS_INVALID"
    assert not any(query.startswith("update members set params=") for query, _ in connection.calls)


def test_robot_settings_parse_false_strings_as_false():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    member = _member(params={"mt_login": "445566", "serveur": "PUPrime-Live"})
    connection = ProfileConnection(member["params"])
    with client.session_transaction() as session:
        session["member_code"] = member["code"]

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection), \
         patch.object(app, "build_notif", return_value="test"), \
         patch.object(app, "send_telegram"):
        response = client.post("/save", json={
            "drawdown_actif": "false", "drawdown_gain_actif": "false",
            "objectif_actif": "false", "copier_ordres_en_attente": "false",
            "filtre_news": "false", "mode_risque": "Lots fixes", "lots": 0.02,
        })

    assert response.status_code == 200
    saved = app._reveal_params(connection.params)
    assert saved["drawdown_actif"] is False
    assert saved["copier_ordres_en_attente"] is False
    assert saved["filtre_news"] is False


def test_admin_profile_update_targets_the_selected_member():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    connection = ProfileConnection({"mt_login": "111"})

    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_conn", return_value=connection):
        response = client.post("/admin/api/membre/update", json={
            "key": app.ADMIN_KEY,
            "code": "BCT-TARGET01",
            "telephone": "+33600000000",
            "mt_login": "222",
        })

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    update_calls = [params for query, params in connection.calls if query.startswith("update members set")]
    assert update_calls
    assert all(params["c"] == "BCT-TARGET01" for params in update_calls)
    assert app._reveal_params(connection.params)["mt_login"] == "222"
