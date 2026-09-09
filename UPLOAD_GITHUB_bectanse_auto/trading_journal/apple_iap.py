"""Verified App Store subscriptions for Bectanse Track.

The mobile client never grants access itself. It sends Apple's signed JWS to
this module, which validates the certificate chain, bundle, environment,
product, account token, expiry and ownership before changing entitlements.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone

from .billing import reconcile_trading_access


BUNDLE_ID = "com.bectanse.track"
PRODUCT_PLANS = {
    os.environ.get("APPLE_TRACK_PRO_PRODUCT_ID", "com.bectanse.track.pro.monthly").strip(): "JOURNAL_PRO",
    os.environ.get("APPLE_TRACK_ELITE_PRODUCT_ID", "com.bectanse.track.elite.monthly").strip(): "JOURNAL_ELITE",
}
APPLE_ROOT_CA_G3 = base64.b64decode("""MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwS
QXBwbGUgUm9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9u
IEF1dGhvcml0eTETMBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcN
MTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2WjBnMRswGQYDVQQDDBJBcHBsZSBS
b290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmljYXRpb24gQXV0aG9y
aXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqGSM49
AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHcFBbZDuWmBSp3ZHtf
TjjTuxxEtX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dvMVztK517
IDvYuVTZXpmkOlEKMaNCMEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySr
MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2gA
MGUCMQCD6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3meoyhpmvOwgPUnPWTxnS4
at+qIxUCMG1mihDK1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkLF1vLUagM
6BgD56KyKA==""")


def product_ids() -> list[str]:
    return [product_id for product_id in PRODUCT_PLANS if product_id]


def storekit_context(get_conn, user_id: str) -> dict:
    conn = get_conn()
    try:
        rows = conn.run(
            "SELECT app_account_token FROM trading_apple_accounts WHERE user_id=:user_id",
            user_id=user_id,
        )
        if rows:
            token = str(rows[0][0])
        else:
            token = str(uuid.uuid4())
            conn.run("""INSERT INTO trading_apple_accounts (user_id,app_account_token)
                VALUES (:user_id,:token) ON CONFLICT (user_id) DO NOTHING""",
                user_id=user_id, token=token)
            rows = conn.run(
                "SELECT app_account_token FROM trading_apple_accounts WHERE user_id=:user_id",
                user_id=user_id,
            )
            token = str(rows[0][0])
        return {"app_account_token": token, "product_ids": product_ids()}
    finally:
        conn.close()


def _unverified_environment(jws: str) -> str:
    try:
        payload = str(jws).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return str(decoded.get("environment") or (decoded.get("data") or {}).get("environment") or "")
    except Exception as exc:
        raise ValueError("Transaction Apple invalide.") from exc


def _verifier(environment_name: str):
    try:
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier
    except ImportError as exc:
        raise RuntimeError("La validation Apple n’est pas installée.") from exc

    if environment_name == "Production":
        app_id = os.environ.get("APPLE_APP_ID", "").strip()
        if not app_id.isdigit():
            raise RuntimeError("L’identifiant App Store de Bectanse Track n’est pas configuré.")
        environment, app_apple_id = Environment.PRODUCTION, int(app_id)
    elif environment_name == "Sandbox":
        environment, app_apple_id = Environment.SANDBOX, None
    else:
        raise ValueError("Environnement Apple non autorisé.")
    return SignedDataVerifier(
        [APPLE_ROOT_CA_G3], True, environment, BUNDLE_ID, app_apple_id,
    )


def verify_transaction(jws: str):
    jws = str(jws or "").strip()
    if len(jws) < 100 or len(jws) > 50000:
        raise ValueError("Transaction Apple invalide.")
    try:
        return _verifier(_unverified_environment(jws)).verify_and_decode_signed_transaction(jws)
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise ValueError("La signature Apple n’a pas pu être vérifiée.") from exc


def _millis_to_datetime(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc) if value else None
    except (TypeError, ValueError, OSError):
        return None


def apply_verified_transaction(get_conn, user_id: str, transaction) -> dict:
    product_id = str(transaction.productId or "")
    plan = PRODUCT_PLANS.get(product_id)
    if not plan:
        raise ValueError("Produit Apple non autorisé.")
    transaction_id = str(transaction.transactionId or "")
    original_id = str(transaction.originalTransactionId or "")
    app_token = str(transaction.appAccountToken or "").lower()
    expires_at = _millis_to_datetime(transaction.expiresDate)
    if not transaction_id or not original_id or not expires_at:
        raise ValueError("Transaction Apple incomplète.")
    status = "active" if not transaction.revocationDate and expires_at > datetime.now(timezone.utc) else "expired"
    environment = getattr(transaction.environment, "value", None) or str(transaction.rawEnvironment or "")

    conn = get_conn()
    try:
        conn.run("BEGIN")
        owner = conn.run("""SELECT user_id,app_account_token FROM trading_apple_accounts
            WHERE user_id=:user_id OR original_transaction_id=:original_id
               OR last_transaction_id=:transaction_id FOR UPDATE""",
            user_id=user_id, original_id=original_id, transaction_id=transaction_id)
        expected = next((str(row[1]).lower() for row in owner if str(row[0]) == user_id), "")
        if not expected:
            raise PermissionError("Compte Apple non associé à ce profil.")
        if app_token != expected:
            raise PermissionError("Cet achat Apple appartient à un autre profil.")
        if any(str(row[0]) != user_id for row in owner):
            raise PermissionError("Cet abonnement Apple est déjà rattaché à un autre profil.")

        conn.run("""UPDATE trading_apple_accounts SET original_transaction_id=:original_id,
            last_transaction_id=:transaction_id,product_id=:product_id,environment=:environment,
            subscription_status=:status,expires_at=:expires_at,updated_at=NOW()
            WHERE user_id=:user_id""", user_id=user_id, original_id=original_id,
            transaction_id=transaction_id, product_id=product_id, environment=environment,
            status=status, expires_at=expires_at)
        current = conn.run("""SELECT billing_provider,subscription_status,current_period_end
            FROM trading_subscriptions WHERE user_id=:user_id FOR UPDATE""", user_id=user_id)
        may_replace = not current or status == "active" or str(current[0][0] or "") in {"", "apple"}
        if may_replace:
            conn.run("""INSERT INTO trading_subscriptions
                (user_id,product,plan,subscription_status,current_period_end,billing_provider,
                 apple_original_transaction_id,apple_product_id)
                VALUES (:user_id,'JOURNAL',:plan,:status,:expires_at,'apple',:original_id,:product_id)
                ON CONFLICT (user_id) DO UPDATE SET plan=EXCLUDED.plan,
                 subscription_status=EXCLUDED.subscription_status,
                 current_period_end=EXCLUDED.current_period_end,billing_provider='apple',
                 apple_original_transaction_id=EXCLUDED.apple_original_transaction_id,
                 apple_product_id=EXCLUDED.apple_product_id,updated_at=NOW()""",
                user_id=user_id, plan=plan, status=status, expires_at=expires_at,
                original_id=original_id, product_id=product_id)
        conn.run("""INSERT INTO trading_audit_logs (user_id,action,metadata)
            VALUES (:user_id,'APPLE_SUBSCRIPTION_CHANGED',jsonb_build_object(
                'status',:status,'plan',:plan,'product_id',:product_id,
                'transaction_id',:transaction_id,'expires_at',:expires_at))""",
            user_id=user_id, status=status, plan=plan, product_id=product_id,
            transaction_id=transaction_id, expires_at=expires_at)
        access = reconcile_trading_access(conn, user_id)
        conn.run("COMMIT")
    except Exception:
        try:
            conn.run("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return {"status": status, "plan": plan, "expires_at": expires_at.isoformat(), "access": access}


def synchronize_transaction(get_conn, user_id: str, signed_transaction: str) -> dict:
    return apply_verified_transaction(get_conn, user_id, verify_transaction(signed_transaction))


def process_notification(get_conn, signed_payload: str) -> dict:
    environment = _unverified_environment(signed_payload)
    try:
        notification = _verifier(environment).verify_and_decode_notification(signed_payload)
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise ValueError("Notification Apple non vérifiée.") from exc
    notification_uuid = str(notification.notificationUUID or "")
    if not notification_uuid:
        raise ValueError("Notification Apple incomplète.")
    data = notification.data
    signed_transaction = str(data.signedTransactionInfo or "") if data else ""
    notification_type = getattr(notification.notificationType, "value", None) or str(notification.rawNotificationType or "")
    subtype = getattr(notification.subtype, "value", None) or str(notification.rawSubtype or "")

    conn = get_conn()
    try:
        inserted = conn.run("""INSERT INTO trading_apple_notifications
            (notification_uuid,notification_type,subtype) VALUES (:uuid,:type,:subtype)
            ON CONFLICT DO NOTHING RETURNING notification_uuid""",
            uuid=notification_uuid, type=notification_type, subtype=subtype)
        if not inserted:
            return {"handled": True, "duplicate": True}
    finally:
        conn.close()
    try:
        if not signed_transaction:
            conn = get_conn()
            try:
                conn.run("""UPDATE trading_apple_notifications SET status='ignored',processed_at=NOW()
                    WHERE notification_uuid=:uuid""", uuid=notification_uuid)
            finally:
                conn.close()
            return {"handled": True, "ignored": "no_transaction"}
        transaction = verify_transaction(signed_transaction)
        original_id = str(transaction.originalTransactionId or "")
        app_token = str(transaction.appAccountToken or "").lower()
        conn = get_conn()
        try:
            rows = conn.run("""SELECT user_id FROM trading_apple_accounts
                WHERE original_transaction_id=:original_id OR app_account_token=:app_token LIMIT 1""",
                original_id=original_id, app_token=app_token)
        finally:
            conn.close()
        if not rows:
            raise LookupError("Abonnement Apple non associé.")
        result = apply_verified_transaction(get_conn, str(rows[0][0]), transaction)
        conn = get_conn()
        try:
            conn.run("""UPDATE trading_apple_notifications SET status='processed',
                original_transaction_id=:original_id,processed_at=NOW()
                WHERE notification_uuid=:uuid""", original_id=original_id, uuid=notification_uuid)
        finally:
            conn.close()
        return {"handled": True, **result}
    except Exception as exc:
        conn = get_conn()
        try:
            conn.run("""UPDATE trading_apple_notifications SET status='failed',error=:error
                WHERE notification_uuid=:uuid""", error=str(exc)[:500], uuid=notification_uuid)
        finally:
            conn.close()
        raise
