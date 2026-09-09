"""Standalone Bectanse Journal billing on the existing Stripe account."""

from __future__ import annotations

import os
import secrets
import string
import uuid
from datetime import datetime, timezone

from .entitlements import academy_membership_state, resolve_entitlements, standalone_subscription_state

JOURNAL_PLANS = {
    "JOURNAL_PRO": "STRIPE_JOURNAL_PRO_PRICE_ID",
    "JOURNAL_ELITE": "STRIPE_JOURNAL_ELITE_PRICE_ID",
}
ACTIVE_STATUSES = {"active", "trialing"}
STRIPE_API_VERSION = "2026-07-29.dahlia"


def _stripe_client():
    import stripe

    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise RuntimeError("La facturation Journal n’est pas configurée.")
    return stripe.StripeClient(
        secret,
        stripe_version=STRIPE_API_VERSION,
        max_network_retries=2,
    )


def _field(value, name: str, default=""):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _integration_identifier() -> str:
    suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))
    return f"bectanse_journal_{suffix}"


def price_id_for_plan(plan: str) -> str:
    return os.environ.get(JOURNAL_PLANS.get(str(plan).upper(), ""), "").strip()


def plan_for_price(price_id: str) -> str | None:
    for plan, variable in JOURNAL_PLANS.items():
        if price_id and price_id == os.environ.get(variable, "").strip():
            return plan
    return None


def _database_entitlements(conn, user_id: str):
    member_rows = conn.run("""SELECT actif,COALESCE(access_level,'member'),
        COALESCE(billing_status,'legacy'),billing_current_period_end,date_fin,
        COALESCE(admin_suspended,FALSE) FROM members WHERE code=:user_id""", user_id=user_id)
    member = None
    if member_rows:
        member = dict(zip(
            ("actif", "access_level", "billing_status", "billing_current_period_end",
             "date_fin", "admin_suspended"),
            member_rows[0],
        ))
    subscription_rows = conn.run("""SELECT plan,subscription_status,current_period_end
        FROM trading_subscriptions WHERE user_id=:user_id""", user_id=user_id)
    subscription = None
    if subscription_rows:
        subscription = dict(zip(
            ("plan", "subscription_status", "current_period_end"), subscription_rows[0],
        ))
    return resolve_entitlements(subscription, member)


def reconcile_trading_access(conn, user_id: str, *, entitlements=None) -> dict:
    """Suspend or restore MT5 processing without deleting the customer's history."""
    entitlements = entitlements or _database_entitlements(conn, user_id)
    account_rows = conn.run("""SELECT id,status FROM trading_accounts
        WHERE user_id=:user_id AND status NOT IN ('DISCONNECTED','AUTH_ERROR')
        ORDER BY created_at,id""", user_id=user_id)
    limit = max(0, int(entitlements.max_accounts if entitlements.allowed else 0))
    allowed_rows = list(account_rows or [])[:limit]
    blocked_rows = list(account_rows or [])[limit:]

    suspended_ids = []
    for raw_account_id, _ in blocked_rows:
        account_id = int(raw_account_id)
        conn.run("""UPDATE trading_workers SET status='ONLINE',current_job_id='',updated_at=NOW()
            WHERE current_job_id IN (SELECT id FROM trading_sync_jobs
                WHERE trading_account_id=:account_id
                AND status IN ('PENDING','LEASED','RUNNING','RETRY'))""", account_id=account_id)
        conn.run("""UPDATE trading_sync_runs SET status='FAILED',error_code='ACCESS_EXPIRED',finished_at=NOW()
            WHERE status='RUNNING' AND sync_job_id IN (SELECT id FROM trading_sync_jobs
                WHERE trading_account_id=:account_id)""", account_id=account_id)
        conn.run("""UPDATE trading_sync_jobs SET status='DEAD',completed_at=NOW(),finished_at=NOW(),
            lease_expires_at=NULL,last_error_code='ACCESS_EXPIRED',
            last_error_message='Accès Journal expiré.'
            WHERE trading_account_id=:account_id
            AND status IN ('PENDING','LEASED','RUNNING','RETRY')""", account_id=account_id)
        suspended = conn.run("""UPDATE trading_accounts SET status='ACCESS_EXPIRED',
            sync_status='ACCESS_EXPIRED',last_error_code='ACCESS_EXPIRED',
            last_error_message='Accès Journal expiré.',updated_at=NOW()
            WHERE id=:account_id AND user_id=:user_id AND status<>'ACCESS_EXPIRED'
            RETURNING id""", account_id=account_id, user_id=user_id)
        if suspended:
            suspended_ids.append(account_id)

    if entitlements.allowed:
        restored = []
        for raw_account_id, status in allowed_rows:
            if str(status) != "ACCESS_EXPIRED":
                continue
            rows = conn.run("""UPDATE trading_accounts SET status='PENDING_VERIFICATION',
                sync_status='PENDING',last_error_code='',last_error_message='',updated_at=NOW()
                WHERE id=:account_id AND user_id=:user_id AND status='ACCESS_EXPIRED'
                AND EXISTS (SELECT 1 FROM trading_credentials c
                    WHERE c.trading_account_id=trading_accounts.id)
                RETURNING id""", account_id=int(raw_account_id), user_id=user_id)
            restored.extend(rows or [])
        queued = 0
        for row in restored or []:
            account_id = int(row[0])
            inserted = conn.run("""INSERT INTO trading_sync_jobs
                (id,trading_account_id,job_type,status,priority)
                SELECT :id,:account_id,'FULL_HISTORY_SYNC','PENDING',100
                WHERE NOT EXISTS (SELECT 1 FROM trading_sync_jobs
                    WHERE trading_account_id=:account_id
                    AND status IN ('PENDING','LEASED','RUNNING','RETRY'))
                RETURNING id""", id=str(uuid.uuid4()), account_id=account_id)
            queued += int(bool(inserted))
        return {"allowed": True, "restored_accounts": len(restored), "queued_jobs": queued,
                "suspended_accounts": len(suspended_ids), "suspended_account_ids": suspended_ids}

    return {"allowed": False, "suspended_accounts": len(suspended_ids)}


def create_checkout(get_conn, member: dict, user_id: str, plan: str, root_url: str, *, client=None) -> str:
    plan = str(plan or "").upper()
    if plan not in JOURNAL_PLANS:
        raise ValueError("Cette formule Journal n’existe pas.")
    if academy_membership_state(member)[0]:
        raise PermissionError("Bectanse Journal est déjà inclus dans votre adhésion Académie.")
    conn = get_conn()
    try:
        rows = conn.run("""SELECT stripe_customer_id,stripe_subscription_id,subscription_status,
            cancel_at_period_end,current_period_end FROM trading_subscriptions
            WHERE user_id=:user_id""", user_id=user_id)
    finally:
        conn.close()
    existing_subscription = ({
        "subscription_status": rows[0][2], "current_period_end": rows[0][4],
    } if rows else None)
    if standalone_subscription_state(existing_subscription):
        raise PermissionError("Votre abonnement Journal est déjà actif.")
    email = str(member.get("email") or "").strip().lower()
    if "@" not in email:
        raise ValueError("Une adresse e-mail vérifiée est requise.")
    price_id = price_id_for_plan(plan)
    if not os.environ.get("STRIPE_SECRET_KEY", "").strip() or not price_id:
        raise RuntimeError("La souscription Journal n’est pas encore configurée.")
    params = {
        "mode": "subscription", "success_url": root_url.rstrip("/") + "/journal?checkout=success",
        "cancel_url": root_url.rstrip("/") + "/journal?checkout=cancelled",
        "client_reference_id": user_id,
        "metadata": {"member_code": user_id, "product": "BECTANSE_JOURNAL", "journal_plan": plan},
        "subscription_data": {"metadata": {
            "member_code": user_id, "product": "BECTANSE_JOURNAL", "journal_plan": plan,
        }},
        "line_items": [{"price": price_id, "quantity": 1}],
        "billing_address_collection": "auto", "allow_promotion_codes": True,
        "integration_identifier": _integration_identifier(),
    }
    customer_id = str(rows[0][0] or "") if rows else ""
    if customer_id:
        params["customer"] = customer_id
    else:
        params["customer_email"] = email
    session = (client or _stripe_client()).v1.checkout.sessions.create(params)
    checkout_url = str(_field(session, "url") or "")
    if not checkout_url.startswith("https://checkout.stripe.com/"):
        raise RuntimeError("Checkout indisponible")
    return checkout_url


def create_portal(get_conn, user_id: str, root_url: str, *, client=None) -> str:
    """Create a short-lived Stripe portal session for a standalone Journal customer."""
    conn = get_conn()
    try:
        rows = conn.run("""SELECT stripe_customer_id FROM trading_subscriptions
            WHERE user_id=:user_id LIMIT 1""", user_id=user_id)
    finally:
        conn.close()
    customer_id = str(rows[0][0] or "") if rows else ""
    if not customer_id:
        raise LookupError("Aucun abonnement Journal Stripe n’est rattaché à ce compte.")
    params = {
        "customer": customer_id,
        "return_url": root_url.rstrip("/") + "/journal",
        "locale": "fr",
    }
    configuration = os.environ.get("STRIPE_JOURNAL_PORTAL_CONFIGURATION", "").strip()
    if configuration:
        params["configuration"] = configuration
    portal = (client or _stripe_client()).v1.billing_portal.sessions.create(params)
    portal_url = str(_field(portal, "url") or "")
    if not portal_url.startswith("https://billing.stripe.com/"):
        raise RuntimeError("Portail de facturation indisponible")
    return portal_url


def _id(value) -> str:
    return str(value.get("id") if isinstance(value, dict) else value or "")


def _metadata(obj: dict) -> dict:
    parent = obj.get("parent") or {}
    details = parent.get("subscription_details") or {}
    return {**(details.get("metadata") or {}), **(obj.get("metadata") or {})}


def is_journal_event(event: dict, get_conn=None) -> bool:
    obj = (event.get("data") or {}).get("object") or {}
    metadata = _metadata(obj)
    if str(metadata.get("product") or "").upper() == "BECTANSE_JOURNAL":
        return True
    price_ids = []
    for container in (obj.get("items") or {}, obj.get("lines") or {}):
        for row in container.get("data") or []:
            price_ids.append(_id(row.get("price")) or _id(((row.get("pricing") or {}).get("price_details") or {}).get("price")))
    if any(plan_for_price(price_id) for price_id in price_ids):
        return True
    if get_conn:
        subscription_id = _id(obj.get("subscription")) or (_id(obj.get("id")) if _id(obj.get("id")).startswith("sub_") else "")
        customer_id = _id(obj.get("customer"))
        conn = get_conn()
        try:
            rows = conn.run("""SELECT 1 FROM trading_subscriptions WHERE
                (:subscription<>'' AND stripe_subscription_id=:subscription) OR
                (:customer<>'' AND stripe_customer_id=:customer) LIMIT 1""",
                subscription=subscription_id, customer=customer_id)
            return bool(rows)
        finally:
            conn.close()
    return False


def process_webhook(event: dict, get_conn) -> dict:
    event_id, event_type = str(event.get("id") or ""), str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}
    metadata = _metadata(obj)
    subscription_id = _id(obj.get("subscription")) or (_id(obj.get("id")) if _id(obj.get("id")).startswith("sub_") else "")
    customer_id = _id(obj.get("customer"))
    user_id = str(metadata.get("member_code") or obj.get("client_reference_id") or "")
    price_id = ""
    period_end = obj.get("current_period_end")
    for container in (obj.get("items") or {}, obj.get("lines") or {}):
        for row in container.get("data") or []:
            price_id = _id(row.get("price")) or _id(((row.get("pricing") or {}).get("price_details") or {}).get("price")) or price_id
            period_end = period_end or row.get("current_period_end") or (row.get("period") or {}).get("end")
    # Portal plan changes retain the subscription's original metadata. The current
    # line-item price is therefore authoritative whenever Stripe includes it.
    plan = str(plan_for_price(price_id) or metadata.get("journal_plan") or "JOURNAL_PRO").upper()
    status = str(obj.get("status") or "").lower()
    if event_type in {"checkout.session.completed", "invoice.paid"}:
        status = "active"
    elif event_type == "invoice.payment_failed":
        status = "past_due"
    elif event_type == "customer.subscription.deleted":
        status = "canceled"
    try:
        period_end_dt = datetime.fromtimestamp(int(period_end), timezone.utc) if period_end else None
    except (TypeError, ValueError, OSError):
        period_end_dt = None
    conn = get_conn()
    try:
        conn.run("BEGIN")
        inserted = conn.run("""INSERT INTO stripe_journal_events
            (event_id,event_type,user_id,stripe_object_id,subscription_id)
            VALUES (:event_id,:event_type,:user_id,:object_id,:subscription_id)
            ON CONFLICT DO NOTHING RETURNING event_id""", event_id=event_id,
            event_type=event_type, user_id=user_id, object_id=_id(obj.get("id")), subscription_id=subscription_id)
        if not inserted:
            conn.run("COMMIT")
            return {"handled": True, "duplicate": True}
        if not user_id:
            rows = conn.run("""SELECT user_id FROM trading_subscriptions WHERE
                (:subscription<>'' AND stripe_subscription_id=:subscription) OR
                (:customer<>'' AND stripe_customer_id=:customer) LIMIT 1""",
                subscription=subscription_id, customer=customer_id)
            user_id = str(rows[0][0]) if rows else ""
        if not user_id:
            conn.run("""UPDATE stripe_journal_events SET status='ignored',error='unmatched_customer',
                processed_at=NOW() WHERE event_id=:event_id""", event_id=event_id)
            conn.run("COMMIT")
            return {"handled": True, "ignored": "unmatched_customer"}
        payment_failed_at = datetime.now(timezone.utc) if event_type == "invoice.payment_failed" else None
        conn.run("""INSERT INTO trading_subscriptions
            (user_id,product,plan,subscription_status,stripe_customer_id,stripe_subscription_id,
             stripe_price_id,cancel_at_period_end,payment_failed_at,current_period_end,billing_provider)
            VALUES (:user_id,'JOURNAL',:plan,:status,:customer,:subscription,:price_id,
             :cancel_at_period_end,:payment_failed_at,:period_end,'stripe')
            ON CONFLICT (user_id) DO UPDATE SET plan=EXCLUDED.plan,
             subscription_status=EXCLUDED.subscription_status,
             billing_provider='stripe',
             stripe_customer_id=CASE WHEN EXCLUDED.stripe_customer_id<>'' THEN EXCLUDED.stripe_customer_id ELSE trading_subscriptions.stripe_customer_id END,
             stripe_subscription_id=CASE WHEN EXCLUDED.stripe_subscription_id<>'' THEN EXCLUDED.stripe_subscription_id ELSE trading_subscriptions.stripe_subscription_id END,
             stripe_price_id=CASE WHEN EXCLUDED.stripe_price_id<>'' THEN EXCLUDED.stripe_price_id ELSE trading_subscriptions.stripe_price_id END,
             cancel_at_period_end=EXCLUDED.cancel_at_period_end,
             payment_failed_at=EXCLUDED.payment_failed_at,current_period_end=COALESCE(EXCLUDED.current_period_end,trading_subscriptions.current_period_end),updated_at=NOW()""",
            user_id=user_id, plan=plan, status=status or "inactive", customer=customer_id,
            subscription=subscription_id, price_id=price_id, cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
            payment_failed_at=payment_failed_at, period_end=period_end_dt)
        conn.run("""INSERT INTO trading_audit_logs (user_id,action,metadata)
            VALUES (:user_id,'SUBSCRIPTION_CHANGED',jsonb_build_object('status',:status,'plan',:plan,'event_id',:event_id))""",
            user_id=user_id, status=status, plan=plan, event_id=event_id)
        reconcile_trading_access(conn, user_id)
        conn.run("""UPDATE stripe_journal_events SET user_id=:user_id,status='processed',processed_at=NOW()
            WHERE event_id=:event_id""", user_id=user_id, event_id=event_id)
        conn.run("COMMIT")
    except Exception as exc:
        try:
            conn.run("ROLLBACK")
            conn.run("UPDATE stripe_journal_events SET status='failed',error=:error WHERE event_id=:event_id",
                     error=str(exc)[:500], event_id=event_id)
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return {"handled": True, "user_id": user_id, "status": status}


def schedule_standalone_cancellation(get_conn, user_id: str, *, client=None) -> bool:
    """Avoid double billing when Academy becomes the effective entitlement."""
    conn = get_conn()
    try:
        rows = conn.run("""SELECT stripe_subscription_id,subscription_status,cancel_at_period_end
            FROM trading_subscriptions WHERE user_id=:user_id""", user_id=user_id)
    finally:
        conn.close()
    if not rows or str(rows[0][1]).lower() not in ACTIVE_STATUSES or bool(rows[0][2]) or not rows[0][0]:
        return False
    subscription_id = str(rows[0][0])
    (client or _stripe_client()).v1.subscriptions.update(
        subscription_id,
        {"cancel_at_period_end": True},
        options={"idempotency_key": f"academy-included-{user_id}-{subscription_id}"},
    )
    conn = get_conn()
    try:
        conn.run("""UPDATE trading_subscriptions SET cancel_at_period_end=TRUE,updated_at=NOW()
            WHERE user_id=:user_id AND stripe_subscription_id=:subscription""",
            user_id=user_id, subscription=subscription_id)
    finally:
        conn.close()
    return True
