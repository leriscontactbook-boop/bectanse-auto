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


def active_member():
    return {
        "code": "BCT-REFERRAL1",
        "nom": "Ambassadeur Test",
        "access_level": "member",
        "actif": True,
        "date_fin": datetime.now() + timedelta(days=30),
        "filleuls_count": 7,
        "gains_parrainage": 350,
        "paiement_type": "",
        "paiement_iban": "",
        "paiement_bic": "",
        "paiement_titulaire": "",
        "paiement_crypto_reseau": "",
        "paiement_crypto_adresse": "",
    }


class ReferralConnection:
    def __init__(self):
        self.calls = []
        self.closed = False

    def run(self, query, **params):
        compact = " ".join(query.split()).lower()
        self.calls.append((compact, params))
        if compact.startswith("select coalesce(filleuls_count"):
            return [[7, 350]]
        if compact.startswith("select nom, created_at"):
            return [["Client Exemple", datetime(2026, 8, 20, 12, 0)]]
        if compact.startswith("update members set paiement_type"):
            return []
        raise AssertionError(f"Unexpected query: {compact}")

    def close(self):
        self.closed = True


def logged_client():
    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    client = app.app.test_client()
    with client.session_transaction() as session:
        session["member_code"] = "BCT-REFERRAL1"
    return client


def test_referral_hub_uses_real_balance_and_official_domain():
    client = logged_client()
    connection = ReferralConnection()
    member = active_member()
    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection):
        response = client.get("/parrainage")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Programme Ambassadeur — Bectanse Académie" in html
    assert 'data-total="7"' in html
    assert "350 €" in html
    assert "https://acces.bectanse-academie.com/rejoindre/BCT-REFERRAL1" in html
    assert "bectanse-auto.up.railway.app" not in html
    assert 'data-share="whatsapp"' in html
    assert "Client E." in html
    assert connection.closed is True


def test_payment_destination_rejects_invalid_iban_before_storage():
    client = logged_client()
    member = active_member()
    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn") as get_conn:
        response = client.post("/save-paiement", json={
            "type": "virement", "titulaire": "Client Test",
            "iban": "pas-un-iban", "bic": "BNPAFRPPXXX"
        })

    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    get_conn.assert_not_called()


def test_payment_destination_is_encrypted_and_scoped_to_session_member():
    client = logged_client()
    member = active_member()
    connection = ReferralConnection()
    with patch.object(app, "enforce_member_access_state"), \
         patch.object(app, "get_member", return_value=member), \
         patch.object(app, "get_conn", return_value=connection):
        response = client.post("/save-paiement", json={
            "type": "virement", "titulaire": "Client Test",
            "iban": "FR7630006000011234567890189", "bic": "BNPAFRPPXXX"
        })

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "type": "virement"}
    update = next(params for query, params in connection.calls if query.startswith("update members set paiement_type"))
    assert update["c"] == "BCT-REFERRAL1"
    assert update["i"].startswith("enc:v1:")
    assert "FR7630006000011234567890189" not in update["i"]
    assert connection.closed is True
