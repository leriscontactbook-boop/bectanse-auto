import os
from datetime import datetime

from flask import Flask

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("BECTANSE_SKIP_STARTUP", "1")

from activation_journey import register_activation_journey


class ActivationConnection:
    def __init__(self):
        self.row = {
            "member_code": "BCT-TEST0001",
            "broker_email": "",
            "broker_reference": "",
            "broker_status": "not_started",
            "broker_opened_at": None,
            "broker_requested_at": None,
            "broker_reviewed_at": None,
            "broker_review_note": "",
            "trading_platform": "",
            "trading_account_completed_at": None,
            "funding_completed_at": None,
            "credentials_completed_at": None,
            "app_installed_at": None,
            "notifications_enabled_at": None,
            "completed_at": None,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        self.events = []
        self.push_subscription = False

    def close(self):
        pass

    def run(self, query, **params):
        sql = " ".join(query.split()).lower()
        now = datetime.now()
        if sql.startswith("insert into member_activation_journeys"):
            return []
        if sql.startswith("insert into member_activation_events"):
            self.events.append((params["event"], params.get("metadata")))
            return []
        if sql.startswith("select count(*) from push_subscriptions"):
            return [[1 if self.push_subscription else 0]]
        if sql.startswith("select member_code,broker_email"):
            keys = (
                "member_code", "broker_email", "broker_reference", "broker_status",
                "broker_opened_at", "broker_requested_at", "broker_reviewed_at",
                "broker_review_note", "trading_platform",
                "trading_account_completed_at", "funding_completed_at",
                "credentials_completed_at", "app_installed_at",
                "notifications_enabled_at", "completed_at", "created_at", "updated_at",
            )
            return [[self.row[key] for key in keys]]
        if sql.startswith("update member_activation_journeys set"):
            if "broker_opened_at=coalesce" in sql:
                self.row["broker_opened_at"] = self.row["broker_opened_at"] or now
            if "broker_email=:email" in sql:
                self.row.update({
                    "broker_email": params["email"],
                    "broker_reference": params["reference"],
                    "broker_status": "pending",
                    "broker_requested_at": self.row["broker_requested_at"] if params["duplicate"] else now,
                    "broker_review_note": "",
                })
            if "broker_status=:decision" in sql:
                self.row.update({
                    "broker_status": params["decision"],
                    "broker_reviewed_at": now,
                    "broker_review_note": params["note"],
                })
            if "trading_account_completed_at=coalesce" in sql:
                self.row.update({
                    "trading_platform": params["platform"],
                    "trading_account_completed_at": now,
                    "funding_completed_at": now,
                })
            if "credentials_completed_at=now()" in sql:
                self.row.update({"trading_platform": params["platform"], "credentials_completed_at": now})
            if "app_installed_at=now()" in sql:
                self.row["app_installed_at"] = now
            if "notifications_enabled_at=now()" in sql:
                self.row["notifications_enabled_at"] = now
            if "app_installed_at=coalesce" in sql and self.push_subscription:
                self.row["app_installed_at"] = self.row["app_installed_at"] or now
                self.row["notifications_enabled_at"] = self.row["notifications_enabled_at"] or now
            if "completed_at=now()" in sql:
                self.row["completed_at"] = now
            self.row["updated_at"] = now
            return []
        raise AssertionError(f"Unexpected query: {sql}")


def identity_decorator(function):
    return function


def test_member_activation_flow_is_sequential_and_telegram_reviewed(tmp_path):
    templates = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    web = Flask(__name__, template_folder=templates)
    web.secret_key = "activation-test"
    web.config["TESTING"] = True
    connection = ActivationConnection()
    member = {
        "code": "BCT-TEST0001",
        "nom": "Client Test",
        "email": "client@example.com",
        "params": {},
        "access_level": "member",
    }
    telegram = []
    notifications = []
    saved_profiles = []

    register_activation_journey(
        app=web,
        get_conn=lambda: connection,
        get_member=lambda code: member if code == member["code"] else None,
        login_required=identity_decorator,
        academy_access_required=identity_decorator,
        admin_required=identity_decorator,
        action_token=lambda action, payload, lifetime_seconds=0: f"token-{payload['decision']}",
        action_payload=lambda token, action: None,
        save_profile=lambda conn, code, member_data, params: saved_profiles.append((code, params)),
        send_telegram=lambda text, reply_markup=None: telegram.append((text, reply_markup)) or True,
        notify_member=lambda *args: notifications.append(args),
    )
    client = web.test_client()
    with client.session_transaction() as session:
        session["member_code"] = member["code"]

    blocked = client.post("/api/demarrage/action", json={
        "action": "trading_ready", "platform": "MT5", "funding_confirmed": True,
    })
    assert blocked.status_code == 409

    assert client.post("/api/demarrage/action", json={"action": "broker_opened"}).status_code == 200
    requested = client.post("/api/demarrage/action", json={
        "action": "request_verification",
        "broker_email": "client@example.com",
        "broker_reference": "PU-4521",
    })
    assert requested.status_code == 200
    assert len(telegram) == 1
    assert "Valider et débloquer" in str(telegram[0][1])
    assert "mot de passe" in telegram[0][0].lower()
    assert "secret-mt" not in telegram[0][0]

    approved = client.post("/admin/api/activation/review", json={
        "member_code": member["code"], "decision": "approved",
    })
    assert approved.status_code == 200
    assert notifications[-1][0] == member["code"]

    assert client.post("/api/demarrage/action", json={
        "action": "trading_ready", "platform": "MT5", "funding_confirmed": True,
    }).status_code == 200
    invalid_server = client.post("/api/demarrage/action", json={
        "action": "save_credentials", "platform": "MT5", "mt_login": "8484775595",
        "mt_server": "serveur-invente", "mt_password": "secret-mt",
    })
    assert invalid_server.status_code == 400
    assert client.post("/api/demarrage/action", json={
        "action": "save_credentials", "platform": "MT5", "mt_login": "8484775595",
        "mt_server": "PUPrime-Live", "mt_password": "secret-mt",
    }).status_code == 200
    assert saved_profiles == [(member["code"], {
        "plateforme": "MT5", "mt_login": "8484775595",
        "serveur": "PUPrime-Live", "mt_password": "secret-mt",
    })]
    assert client.post("/api/demarrage/action", json={"action": "app_installed"}).status_code == 200

    no_push = client.post("/api/demarrage/action", json={"action": "notifications_enabled"})
    assert no_push.status_code == 409
    connection.push_subscription = True
    complete = client.post("/api/demarrage/action", json={"action": "notifications_enabled"})
    assert complete.status_code == 200
    assert complete.get_json()["state"]["finished"] is True
    assert connection.row["completed_at"] is not None


def test_local_preview_is_available_without_member_data():
    templates = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    web = Flask(__name__, template_folder=templates)
    web.secret_key = "activation-preview"
    register_activation_journey(
        app=web,
        get_conn=lambda: None,
        get_member=lambda code: None,
        login_required=identity_decorator,
        academy_access_required=identity_decorator,
        admin_required=identity_decorator,
        action_token=lambda *args, **kwargs: "token",
        action_payload=lambda *args, **kwargs: None,
        save_profile=lambda *args: None,
        send_telegram=lambda *args, **kwargs: True,
        notify_member=lambda *args: None,
    )
    response = web.test_client().get("/preview-demarrage?stage=credentials")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Ton compte prêt" in page
    assert "Crée ton accès de trading" in page
    assert "Installe Bectanse" in page
    assert '<select id="mtServer"' in page
    assert "PUPrime-Live 7" in page
    assert "Créer mon compte de trading" in page
