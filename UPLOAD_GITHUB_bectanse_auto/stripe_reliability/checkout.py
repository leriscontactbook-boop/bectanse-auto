"""Session-level advisory lock + persisted attempt key survive network ambiguity."""
import os
import uuid
from datetime import datetime, timezone
from .policy import sid, utc, ReviewRequired
from .stripe_api import StripeUnavailable


def create_checkout(service,member_code,product,plan,root,*,success_url=None,promotion=None,renewal=False,restart=False):
    choices=[price for price,pair in service.prices.items() if pair==(product,plan)]
    if len(choices)!=1:raise ReviewRequired('UNKNOWN_PLAN')
    c=service.get_conn();locked=False
    try:
        c.run('SELECT pg_advisory_lock(hashtextextended(:key,0))',key='checkout:'+member_code+':'+product);locked=True
        rows=c.run('SELECT row_to_json(m) FROM members m WHERE code=:code',code=member_code)
        if not rows:raise ReviewRequired('MEMBER_MISSING')
        m=rows[0][0]
        if isinstance(m,str):
            import json
            m=json.loads(m)
        if not m.get('email_verified_at') or '@' not in str(m.get('email','')):
            raise ReviewRequired('VERIFIED_EMAIL_REQUIRED')
        if m.get('admin_suspended'):raise ReviewRequired('ADMIN_SUSPENDED')
        if product=='ACADEMY':
            customer=m.get('stripe_customer_id') or '';stored=m.get('stripe_subscription_id') or ''
        else:
            previous=c.run('SELECT stripe_customer_id,stripe_subscription_id FROM trading_subscriptions WHERE user_id=:code',code=member_code)
            customer,stored=previous[0] if previous else ('','')
        if stored:
            sub=service.api.get('/subscriptions/'+stored)
            if customer and sid(sub.get('customer'))!=customer:raise ReviewRequired('CUSTOMER_IDENTITY_MISMATCH')
            if sub.get('status') not in {'canceled','incomplete_expired'}:
                raise ReviewRequired('EXISTING_SUBSCRIPTION_USE_PORTAL')
        # Discover a remote subscription even if its checkout webhook was lost.
        for sub in service.api.pages('/subscriptions',status='all',**({'customer':customer} if customer else {})):
            linked=(sid(sub.get('customer'))==customer if customer else str((sub.get('metadata') or {}).get('member_code',''))==member_code)
            relevant=any(service.prices.get(sid(i.get('price')),('',))[0]==product for i in (sub.get('items') or {}).get('data',[]))
            if linked and relevant and sub.get('status') not in {'canceled','incomplete_expired'}:
                raise ReviewRequired('EXISTING_SUBSCRIPTION_USE_PORTAL')
        renewal_data=None;coupon=None
        if renewal:
            from renewal_offer import renewal_offer, ensure_coupon
            renewal_data=renewal_offer(m)
            if not renewal_data:raise ReviewRequired('RENEWAL_OFFER_EXPIRED')
            coupon=ensure_coupon(service.api,renewal_data)
        rows=c.run('SELECT attempt_id,session_id,status,plan,created_at FROM billing_checkouts WHERE member_code=:code AND product=:product',code=member_code,product=product)
        attempt=None
        if rows:
            key,session_id,state,old_plan,created=rows[0]
            if session_id:
                existing=service.api.get('/checkout/sessions/'+session_id)
                if sid(existing)!=session_id:raise ReviewRequired('CHECKOUT_IDENTITY_MISMATCH')
                if str((existing.get('metadata') or {}).get('member_code') or '')!=member_code:
                    raise ReviewRequired('CHECKOUT_MEMBER_MISMATCH')
                if existing.get('status')=='complete':
                    # A paid checkout from the now-ended subscription must not block a new contract.
                    if not (restart and stored and sid(existing.get('subscription'))==stored):
                        raise ReviewRequired('PAYMENT_CONFIRMATION_PENDING')
                if restart and existing.get('status')=='open' and old_plan!=plan:
                    expired=service.api.request('POST','/checkout/sessions/'+session_id+'/expire',{},
                        idempotency=key+'_expire',write_key=os.environ.get('STRIPE_SECRET_KEY',''))
                    if sid(expired)!=session_id or expired.get('status')!='expired':
                        raise ReviewRequired('CHECKOUT_EXPIRATION_UNCONFIRMED')
                    existing=expired
                if existing.get('status')=='open':
                    if renewal and str((existing.get('metadata') or {}).get('renewal_coupon',''))!=coupon:
                        raise ReviewRequired('OPEN_CHECKOUT_DIFFERENT_OFFER')
                    if old_plan!=plan:raise ReviewRequired('OPEN_CHECKOUT_DIFFERENT_PLAN')
                    url=str(existing.get('url') or '')
                    if not url.startswith('https://checkout.stripe.com/'):raise ReviewRequired('CHECKOUT_URL_INVALID')
                    return url
            elif state=='creating':
                if renewal:raise ReviewRequired('PAYMENT_CONFIRMATION_PENDING')
                if old_plan!=plan:raise ReviewRequired('PAYMENT_CONFIRMATION_PENDING')
                # Stripe may prune idempotency keys after 24h; never retry an unknown old attempt.
                if (datetime.now(timezone.utc)-utc(created)).total_seconds()>23*3600:
                    raise ReviewRequired('CHECKOUT_OUTCOME_UNKNOWN')
                attempt=key
        if attempt is None:
            attempt='bct_'+uuid.uuid4().hex
            c.run("""INSERT INTO billing_checkouts(member_code,product,plan,attempt_id)
                VALUES(:code,:product,:plan,:attempt) ON CONFLICT(member_code,product) DO UPDATE
                SET plan=EXCLUDED.plan,attempt_id=EXCLUDED.attempt_id,session_id='',url='',status='creating',
                expires_at=NULL,created_at=NOW()""",code=member_code,product=product,plan=plan,attempt=attempt)
        # Autocommitted attempt above remains durable even if the POST times out.
        params={'mode':'subscription','success_url':success_url or root+'/abonnement',
            'cancel_url':root+'/abonnement','client_reference_id':member_code,
            'metadata[member_code]':member_code,'metadata[billing_attempt]':attempt,
            'subscription_data[metadata][member_code]':member_code,
            'subscription_data[metadata][billing_attempt]':attempt,
            'line_items[0][price]':choices[0],'line_items[0][quantity]':'1'}
        if product=='ACADEMY':
            params['metadata[academy_plan_id]']=plan
            params['subscription_data[metadata][academy_plan_id]']=plan
        else:
            for prefix in ('metadata','subscription_data[metadata]'):
                params[prefix+'[product]']='BECTANSE_JOURNAL';params[prefix+'[journal_plan]']=plan
        params['customer' if customer else 'customer_email']=customer or m['email']
        if coupon:
            params['discounts[0][coupon]']=coupon
            params['metadata[renewal_coupon]']=coupon
            # A member reserves the code before its deadline; checkout remains open 30 minutes.
            params['expires_at']=str(int(datetime.now(timezone.utc).timestamp())+1800)
        elif promotion:params['discounts[0][promotion_code]']=promotion
        else:params['allow_promotion_codes']='true'
        result=service.api.request('POST','/checkout/sessions',params,idempotency=attempt,
                                   write_key=os.environ.get('STRIPE_SECRET_KEY',''))
        url=str(result.get('url') or '')
        if not sid(result).startswith('cs_') or not url.startswith('https://checkout.stripe.com/'):
            raise StripeUnavailable('CHECKOUT_RESPONSE_INVALID')
        c.run("""UPDATE billing_checkouts SET session_id=:id,url=:url,status='open',expires_at=:expires
            WHERE member_code=:code AND product=:product AND attempt_id=:attempt""",
            id=sid(result),url=url,expires=utc(result.get('expires_at')),code=member_code,product=product,attempt=attempt)
        return url
    except Exception as e:
        service.incident('CHECKOUT_REVIEW',member=member_code,error=str(e) if isinstance(e,(ReviewRequired,StripeUnavailable)) else 'CHECKOUT_UNAVAILABLE',status='review')
        raise
    finally:
        if locked:c.run('SELECT pg_advisory_unlock(hashtextextended(:key,0))',key='checkout:'+member_code+':'+product)
        c.close()
