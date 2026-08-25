import os
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


class ClaimConnection:
    def __init__(self, claimed=True):
        self.claimed = claimed
        self.closed = False

    def run(self, query, **params):
        assert "push_notified_at" in query
        assert params["mid"] == 4242
        return [[1]] if self.claimed else []

    def close(self):
        self.closed = True


def test_legacy_push_alias_uses_current_subscription_pipeline():
    expected = {"registered": 2, "delivered": 2, "failed": 0, "members": 2}
    with patch.object(app, "send_push_to_all", return_value=expected) as sender:
        result = app.send_push_to_all_fcm("Titre", "Message", "/canal")
    assert result == expected
    sender.assert_called_once_with("Titre", "Message", "/canal")


def test_canal_push_claims_once_and_targets_the_vip_page():
    connection = ClaimConnection(claimed=True)
    expected = {"registered": 3, "delivered": 3, "failed": 0, "members": 3}
    with patch.object(app, "get_conn", return_value=connection), \
         patch.object(app, "send_push_to_all", return_value=expected) as sender:
        result = app._dispatch_canal_push(4242, "alerte", "Le marché bouge maintenant")
    assert result["delivered"] == 3
    assert result["skipped"] is False
    sender.assert_called_once_with(
        "🚨 Alerte VIP prioritaire", "Le marché bouge maintenant", "/canal")
    assert connection.closed


def test_duplicate_canal_update_does_not_send_a_second_push():
    connection = ClaimConnection(claimed=False)
    with patch.object(app, "get_conn", return_value=connection), \
         patch.object(app, "send_push_to_all") as sender:
        result = app._dispatch_canal_push(4242, "message", "Même publication")
    assert result["skipped"] is True
    sender.assert_not_called()
