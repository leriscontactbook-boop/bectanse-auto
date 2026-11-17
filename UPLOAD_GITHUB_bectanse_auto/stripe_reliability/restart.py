"""Read Stripe status before offering a new subscription instead of an old debt."""
from .policy import sid


def state(member, api):
    subscription=member.get('stripe_subscription_id')
    if not subscription:return {'ended':False,'allowed':False,'manual':bool(member.get('admin_suspended'))}
    sub=api.get('/subscriptions/'+subscription)
    if sid(sub.get('customer'))!=member.get('stripe_customer_id'):
        raise ValueError('CUSTOMER_IDENTITY_MISMATCH')
    ended=sub.get('status') in {'canceled','incomplete_expired'}
    manual=bool(member.get('admin_suspended'))
    return {'ended':ended,'allowed':ended and not manual,'manual':manual}
