"""Standalone Bectanse Journal billing on the existing Stripe account."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests


JOURNAL_PLANS = {
    "JOURNAL_PRO": "STRIPE_JOURNAL_PRO_PRICE_ID",
    "JOURNAL_ELITE": "STRIPE_JOURNAL_ELITE_PRICE_ID",
}
ACTIVE_STATUSES = {"active", "trialing"}


def price_id_for_plan(plan: str) -> str:
    return os.environ.get(JOURNAL_PLANS.get(str(plan).upper(), ""), "").strip()


def plan_for_price(price_id: str) -> str | None:
    for plan, variable in JOURNAL_PLANS.items():
        if price_id and price_id == os.environ.get(variable, "").strip():
            return plan
    return None


def create_checkout(get_conn, member: dict, user_id: str, plan: str, root_url: str) -> str:
    plan = str(plan or "").upper()
    price_id = price_id_for_plan(plan)
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret or not price_id:
        raise RuntimeError("La souscription Journal n’est pas encore configurée.")
    if bool(member.get("actif")) and str(member.get("access_level") or "member").lower() not in {"explorer", "demo"}:
        raise PermissionError("Bectanse Journal est déjà inclus dans votre adhésion Académie.")
    conn = get_conn()
    try:
        rows = conn.run("""SELECT stripe_customer_id,stripe_subscription_id,subscription_status,
            cancel_at_period_end FROM trading_subscriptions WHERE user_id=:user_id""", user_id=user_id)
    finally:
        conn.close()
    if rows and str(rows[0][2]).lower() in ACTIVE_STATUSES and not bool(rows[0][3]):
        raise PermissionError("Votre abonnement Journal est déjà actif.")
    email = str(member.get("email") or "").strip().lower()
    if "@" not in email:
        raise ValueError("Une adresse e-mail vérifiée est requise.")
    form = {
        "mode": "subscription", "success_url": root_url.rstrip("/") + "/journal?checkout=success",
        "cancel_url": root_url.rstrip("/") + "/journal?checkout=cancelled",
        "client_reference_id": user_id, "customer_email": email,
        "metadata[member_code]": user_id, "metadata[product]": "BECTANSE_JOURNAL",
        "metadata[journal_plan]": plan,
        "subscription_data[metadata][member_code]": user_id,
        "subscription_data[metadata][product]": "BECTANSE_JOURNAL",
        "subscription_data[metadata][journal_plan]": plan,
        "line_items[0][price]": price_id, "line_items[0][quantity]": "1",
        "billing_address_collection": "auto", "allow_promotion_codes": "true",
    }
    response = requests.post("https://api.stripe.com/v1/checkout/sessions",
                             auth=(secret, ""), data=form, timeout=25)
    data = response.json()
    if not response.ok or not str(data.get("url") or "").startswith("https://checkout.stripe.com/"):
        raise RuntimeError((data.get("error") or {}).get("message") or "Checkout indisponible")
    return str(data["url"])


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
    plan = str(metadata.get("journal_plan") or plan_for_price(price_id) or "JOURNAL_PRO").upper()
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
             stripe_price_id,cancel_at_period_end,payment_failed_at,current_period_end)
            VALUES (:user_id,'JOURNAL',:plan,:status,:customer,:subscription,:price_id,
             :cancel_at_period_end,:payment_failed_at,:period_end)
            ON CONFLICT (user_id) DO UPDATE SET plan=EXCLUDED.plan,
             subscription_status=EXCLUDED.subscription_status,
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


def schedule_standalone_cancellation(get_conn, user_id: str) -> bool:
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
    response = requests.post(f"https://api.stripe.com/v1/subscriptions/{subscription_id}",
        auth=(os.environ.get("STRIPE_SECRET_KEY", ""), ""), data={"cancel_at_period_end": "true"},
        headers={"Idempotency-Key": f"academy-included-{user_id}-{subscription_id}"}, timeout=25)
    if not response.ok:
        raise RuntimeError((response.json().get("error") or {}).get("message") or "Stripe cancellation failed")
    conn = get_conn()
    try:
        conn.run("""UPDATE trading_subscriptions SET cancel_at_period_end=TRUE,updated_at=NOW()
            WHERE user_id=:user_id AND stripe_subscription_id=:subscription""",
            user_id=user_id, subscription=subscription_id)
    finally:
        conn.close()
    return True
