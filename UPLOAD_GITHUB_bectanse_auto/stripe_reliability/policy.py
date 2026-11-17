"""Pure decisions from fresh subscription + invoice; no inferred billing dates."""
from datetime import datetime, timezone
import re


class ReviewRequired(ValueError):
    pass


def sid(value):
    return str(value.get('id','') if isinstance(value,dict) else value or '')


def utc(value):
    if isinstance(value,(int,float)):
        return datetime.fromtimestamp(value,timezone.utc)
    if isinstance(value,str):
        # Python 3.9 accepts only 3/6 fractional digits; PostgreSQL JSON trims zeros.
        value=re.sub(r'(?<=\d)\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),value)
        value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value is None:return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def subscription_id(obj):
    ident=sid(obj.get('id'))
    return (ident if ident.startswith('sub_') else '') or sid(obj.get('subscription')) or sid(
        ((obj.get('parent') or {}).get('subscription_details') or {}).get('subscription'))


def event_reference(event):
    obj=(event.get('data') or {}).get('object') or {}
    # Persist identifiers only, never raw card/payment details or arbitrary metadata.
    return {'object_id':sid(obj.get('id')), 'subscription_id':subscription_id(obj),
            'customer_id':sid(obj.get('customer')), 'member_code':str((obj.get('metadata') or {}).get('member_code') or obj.get('client_reference_id') or '')[:100]}


def decide(subscription, invoice, prices, now=None):
    now=now or datetime.now(timezone.utc)
    matches=[i for i in (subscription.get('items') or {}).get('data',[]) if sid(i.get('price')) in prices]
    if len(matches)!=1:raise ReviewRequired('UNKNOWN_OR_AMBIGUOUS_PRODUCT')
    item=matches[0];price_id=sid(item['price']);product,plan=prices[price_id]
    start=utc(item.get('current_period_start') or subscription.get('current_period_start'))
    end=utc(item.get('current_period_end') or subscription.get('current_period_end'))
    status=str(subscription.get('status') or '')
    customer=sid(subscription.get('customer'))
    if not customer or not sid(subscription).startswith('sub_'):raise ReviewRequired('SUBSCRIPTION_IDENTITY_MISSING')
    paid=False
    if invoice:
        if subscription_id(invoice)!=sid(subscription) or sid(invoice.get('customer'))!=customer:
            raise ReviewRequired('INVOICE_IDENTITY_MISMATCH')
        if sid(subscription.get('latest_invoice'))!=sid(invoice):raise ReviewRequired('INVOICE_NOT_CURRENT')
        paid=invoice.get('status')=='paid' and int(invoice.get('amount_remaining') or 0)==0
    if status in {'active','trialing'} and (not start or not end or start>=end):
        raise ReviewRequired('BILLING_PERIOD_MISSING')
    trial=status=='trialing' and utc(subscription.get('trial_end')) and utc(subscription['trial_end'])>now
    allowed=bool((status=='active' and paid or trial) and end and end>now)
    effective=status
    if status=='active' and not paid:
        effective='past_due' if invoice and (invoice.get('attempted') or invoice.get('status')=='uncollectible') else 'incomplete'
    return {'subscription_id':sid(subscription),'customer_id':customer,'product':product,'plan':plan,
            'price_id':price_id,'period_start':start,'period_end':end,'status':effective,
            'stripe_status':status,'allowed':allowed,'reason':'stripe_paid' if allowed and paid else 'trial' if allowed else 'payment_pending' if effective=='incomplete' else 'revoked',
            'amount_paid_cents':int(invoice.get('amount_paid') or 0),
            'billing_reason':str(invoice.get('billing_reason') or ''),
            'invoice_id':sid(invoice),'amount':invoice.get('amount_remaining') if invoice else None,
            'currency':str((invoice or {}).get('currency') or ''),
            'payment_state':str((invoice or {}).get('status') or ''),
            'cancel_at_period_end':bool(subscription.get('cancel_at_period_end')),
            'metadata_member':str((subscription.get('metadata') or {}).get('member_code') or '')}
