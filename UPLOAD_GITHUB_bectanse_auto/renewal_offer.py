"""Account-bound renewal invitation. No browser clock can extend eligibility."""
import hashlib
from datetime import datetime, timedelta, timezone


def renewal_offer(member, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not member or member.get('admin_suspended') or str(member.get('access_level', '')).lower() in {'explorer', 'demo'}:
        return None
    if member.get('billing_status') in {'active', 'trialing', 'past_due', 'unpaid', 'incomplete'}:
        return None
    expiry = member.get('date_fin')
    if isinstance(expiry, str):
        try:
            expiry = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(expiry, datetime):
        return None
    # Existing member timestamps are stored in UTC without a timezone suffix.
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    deadline = expiry + timedelta(hours=48)
    if not expiry <= now < deadline or not member.get('code'):
        return None
    identity = str(member['code']) + ':' + expiry.isoformat()
    coupon = 'retour100_' + hashlib.sha256(identity.encode()).hexdigest()[:32]
    return dict(code='RETOUR100', amount_cents=10000, deadline=deadline.isoformat(),
                deadline_ts=int(deadline.timestamp()), coupon=coupon,
                label=deadline.strftime('%d/%m/%Y à %H:%M UTC'))


def ensure_coupon(api, offer):
    """A private coupon per member and expiry, redeemed at most once.

    Created only when an eligible member starts checkout, never by a page view.
    The API's existing write key and retry policy are preserved.
    """
    import os
    from stripe_reliability.stripe_api import StripeUnavailable
    try:
        coupon = api.get('/coupons/' + offer['coupon'])
    except StripeUnavailable as error:
        # Only a definite missing resource may cause creation; never swallow auth/network errors.
        if error.status != 404 or error.code != 'STRIPE_OBJECT_MISSING':
            raise
        coupon = api.request('POST', '/coupons', {
            'id': offer['coupon'], 'name': 'RETOUR100 · renouvellement',
            'amount_off': '10000', 'currency': 'eur', 'duration': 'once',
            'max_redemptions': '1', 'redeem_by': str(offer['deadline_ts']),
        }, idempotency=offer['coupon'], write_key=os.environ.get('STRIPE_SECRET_KEY', ''))
    if (coupon.get('amount_off') != 10000 or coupon.get('currency') != 'eur'
            or coupon.get('duration') != 'once' or coupon.get('max_redemptions') != 1
            or coupon.get('redeem_by') != offer['deadline_ts'] or not coupon.get('valid')):
        raise StripeUnavailable('RENEWAL_COUPON_UNAVAILABLE')
    return offer['coupon']
