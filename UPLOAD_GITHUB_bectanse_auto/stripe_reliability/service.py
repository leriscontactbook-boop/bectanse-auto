"""One authoritative decision path shared by webhook, scheduler and admin."""
import hashlib
import json
from datetime import datetime, timezone
from .policy import ReviewRequired, decide, sid, subscription_id, event_reference, utc
from .stripe_api import StripeReader, StripeUnavailable
from trading_journal.billing import reconcile_trading_access


EVENT_TYPES = {
 'checkout.session.completed','checkout.session.expired',
 'checkout.session.async_payment_succeeded','checkout.session.async_payment_failed',
 'customer.subscription.created','customer.subscription.updated',
 'customer.subscription.deleted','customer.subscription.paused','customer.subscription.resumed',
 'invoice.paid','invoice.payment_failed','invoice.payment_action_required',
 'invoice.voided','invoice.marked_uncollectible',
}


class BillingService:
    def __init__(self,get_conn,prices,api=None,access_sync=reconcile_trading_access,after_payment=None,onboard=None):
        self.get_conn,self.prices=get_conn,prices
        self.api=api or StripeReader()
        self.access_sync=access_sync
        self.after_payment=after_payment
        self.onboard=onboard

    def incident(self,kind,*,decision=None,member='',error='',details=None,status='new'):
        d=decision or {}
        key='|'.join((kind,member,d.get('subscription_id',''),d.get('invoice_id','')))
        c=self.get_conn()
        try:
            rows=c.run("""INSERT INTO billing_incidents(fingerprint,kind,member_code,customer_id,
                subscription_id,invoice_id,amount,currency,payment_state,access_state,status,last_error,details)
                VALUES(:fp,:kind,:member,:customer,:sub,:invoice,:amount,:currency,:payment,:access,:status,:error,CAST(:details AS JSONB))
                ON CONFLICT(fingerprint) DO UPDATE SET checked_at=NOW(),attempts=billing_incidents.attempts+1,
                last_error=EXCLUDED.last_error,details=EXCLUDED.details,payment_state=EXCLUDED.payment_state,
                access_state=EXCLUDED.access_state,amount=EXCLUDED.amount,
                status=CASE WHEN billing_incidents.status='resolved' THEN EXCLUDED.status ELSE billing_incidents.status END,
                resolved_at=NULL RETURNING id""",fp=hashlib.sha256(key.encode()).hexdigest(),kind=kind,member=member,
                customer=d.get('customer_id',''),sub=d.get('subscription_id',''),invoice=d.get('invoice_id',''),
                amount=d.get('amount'),currency=d.get('currency',''),payment=d.get('payment_state',''),
                access=d.get('access_state', 'allowed' if d.get('allowed') else 'blocked' if 'allowed' in d else 'unknown'),status=status,error=error[:150],details=json.dumps(details or {}))
            return rows[0][0]
        finally:c.close()

    def _resolve(self,c,member,sub,kinds):
        for kind in kinds:
            rows=c.run("""UPDATE billing_incidents SET status='resolved',resolved_at=NOW(),checked_at=NOW(),
                member_code=:member WHERE (member_code=:member OR member_code='') AND subscription_id=:sub AND kind=:kind AND status<>'resolved'
                RETURNING id""",member=member,sub=sub,kind=kind)
            for row in rows:
                c.run("""INSERT INTO billing_actions(incident_id,member_code,actor,action)
                    VALUES(:id,:member,'system','auto_resolved')""",id=row[0],member=member)

    def _current(self,sub_id):
        sub=self.api.get('/subscriptions/'+sub_id,**{'expand[]':'latest_invoice'})
        if sid(sub)!=sub_id:raise ReviewRequired('SUBSCRIPTION_IDENTITY_MISMATCH')
        invoice=sub.get('latest_invoice')
        if isinstance(invoice,str):invoice=self.api.get('/invoices/'+invoice)
        d = decide(sub,invoice or {},self.prices)
        if d['product'] == 'ACADEMY':
            from .paid_periods import paid_through
            academy_prices = {p for p, rule in self.prices.items() if rule[0] == 'ACADEMY'}
            d['paid_until'] = paid_through(self.api, sub_id, d['customer_id'], academy_prices)
            if d['allowed'] and d['reason'] != 'trial':
                d['allowed'] = bool(d['paid_until'] and d['paid_until'] > datetime.now(timezone.utc))
                if not d['allowed']:
                    d['status'], d['reason'] = 'incomplete', 'no_paid_period'
        return d,sub

    def _member(self,c,d,sub,checkout=None):
        if d['product']=='ACADEMY':
            rows=c.run('SELECT code FROM members WHERE stripe_subscription_id=:sub',sub=d['subscription_id'])
        else:
            rows=c.run('SELECT user_id FROM trading_subscriptions WHERE stripe_subscription_id=:sub',sub=d['subscription_id'])
        code=rows[0][0] if rows else d['metadata_member']
        if not rows and checkout and self.onboard and d['product']=='ACADEMY' and d['allowed']:
            code,created=self.onboard(c,d,checkout)
            d['created_member']=created
            # Evidence is the freshly retrieved completed Checkout, not an email-only webhook.
            d['verified_checkout_member']=code
        if not code:raise ReviewRequired('STRIPE_SUBSCRIPTION_UNLINKED')
        if d['metadata_member'] and code!=d['metadata_member']:raise ReviewRequired('MEMBER_METADATA_MISMATCH')
        members=c.run('SELECT row_to_json(m) FROM members m WHERE code=:code FOR UPDATE',code=code)
        if not members:raise ReviewRequired('LOCAL_MEMBER_MISSING')
        member=members[0][0]
        if isinstance(member,str):member=json.loads(member)
        if d['product']=='ACADEMY':
            stored_sub=member.get('stripe_subscription_id') or ''
            stored_customer=member.get('stripe_customer_id') or ''
        else:
            previous=c.run('SELECT stripe_subscription_id,stripe_customer_id FROM trading_subscriptions WHERE user_id=:code FOR UPDATE',code=code)
            stored_sub,stored_customer=previous[0] if previous else ('','')
        if stored_sub!=d['subscription_id']:
            # New binding requires a server-recorded checkout operation, not just metadata.
            attempt=str((sub.get('metadata') or {}).get('billing_attempt') or '')
            matching=c.run("""SELECT 1 FROM billing_checkouts WHERE member_code=:code AND product=:product
                AND attempt_id=:attempt AND plan=:plan""",code=code,product=d['product'],attempt=attempt,plan=d['plan'])
            if not matching and d.get('verified_checkout_member')!=code:raise ReviewRequired('STRIPE_SUBSCRIPTION_UNLINKED')
            if stored_sub:
                old=self.api.get('/subscriptions/'+stored_sub)
                if old.get('status') not in {'canceled','incomplete_expired'}:
                    raise ReviewRequired('CONCURRENT_SUBSCRIPTIONS')
        if stored_customer and stored_customer!=d['customer_id']:
            raise ReviewRequired('CUSTOMER_IDENTITY_MISMATCH')
        return code,member

    def sync(self,sub_id,*,expected=None,actor='system'):
        c=self.get_conn();d={'subscription_id':sub_id};code=''
        try:
            c.run('BEGIN')
            c.run("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",key='billing:'+sub_id)
            # Fetch after serialization: old events can never overwrite a newer snapshot.
            try:
                d,sub=self._current(sub_id)
            except ReviewRequired as error:
                if str(error)=='UNKNOWN_OR_AMBIGUOUS_PRODUCT':
                    linked=c.run('SELECT code FROM members WHERE stripe_subscription_id=:sub UNION SELECT user_id FROM trading_subscriptions WHERE stripe_subscription_id=:sub',sub=sub_id)
                    if not linked:
                        c.run('ROLLBACK')
                        return {'handled':False,'ignored':'unrelated_product'}
                raise
            if expected and expected.get('customer_id') and expected['customer_id']!=d['customer_id']:
                raise ReviewRequired('EVENT_CUSTOMER_MISMATCH')
            code,m=self._member(c,d,sub,(expected or {}).get('checkout'))
            if expected and expected.get('member_code') and expected['member_code']!=code:
                raise ReviewRequired('EVENT_MEMBER_MISMATCH')
            previous_end=utc(m.get('billing_current_period_end')) if d['product']=='ACADEMY' else None
            if d['product']=='ACADEMY':
                # Legacy TIMESTAMP columns store UTC; new audit columns are timestamptz.
                end=d['period_end'].replace(tzinfo=None) if d['period_end'] else None
                paid_end = d.get('paid_until')
                if d['reason'] == 'trial':
                    paid_end = d['period_end']
                paid_end = paid_end.replace(tzinfo=None) if paid_end else None
                manual=utc(m.get('manual_access_until'))
                manual_valid=bool(manual and manual>datetime.now(timezone.utc) and d['status'] not in {'past_due','unpaid'})
                effective_allowed=bool(not m.get('admin_suspended') and (d['allowed'] or (manual_valid and m.get('actif'))))
                d['access_state']='allowed' if effective_allowed else 'blocked'
                reason='admin_suspended' if m.get('admin_suspended') else 'manual_extension' if manual_valid else d['reason']
                c.run("""UPDATE members SET stripe_subscription_id=:sub,stripe_customer_id=:customer,
                    stripe_price_id=:price,billing_status=:status,billing_current_period_end=:end,
                    billing_period_start=:start,billing_cancel_at_period_end=:cancel,
                    billing_verified_at=NOW(),billing_access_reason=:reason,
                    date_souscription=CASE WHEN :allowed THEN COALESCE(date_souscription,NOW()) ELSE date_souscription END,
                    email_verified_at=CASE WHEN :allowed THEN COALESCE(email_verified_at,NOW()) ELSE email_verified_at END,
                    date_fin=GREATEST(:paid_end,manual_access_until),
                    actif=CASE WHEN admin_suspended THEN FALSE WHEN :allowed THEN TRUE ELSE actif END,
                    access_level=CASE WHEN :allowed OR manual_access_until>NOW() THEN 'member' ELSE access_level END,
                    copy_actif=CASE WHEN admin_suspended OR :status IN ('past_due','unpaid')
                        OR (NOT :allowed AND NOT COALESCE(manual_access_until>NOW(),FALSE))
                        THEN FALSE ELSE copy_actif END
                    WHERE code=:code""",sub=sub_id,customer=d['customer_id'],price=d['price_id'],status=d['status'],
                    end=end,paid_end=paid_end,start=d['period_start'],cancel=d['cancel_at_period_end'],reason=reason,allowed=d['allowed'],code=code)
            else:
                c.run("""INSERT INTO trading_subscriptions(user_id,product,plan,subscription_status,
                    stripe_customer_id,stripe_subscription_id,stripe_price_id,current_period_end,
                    cancel_at_period_end,billing_provider)
                    VALUES(:code,'JOURNAL',:plan,:status,:customer,:sub,:price,:end,:cancel,'stripe')
                    ON CONFLICT(user_id) DO UPDATE SET plan=EXCLUDED.plan,subscription_status=EXCLUDED.subscription_status,
                    stripe_customer_id=EXCLUDED.stripe_customer_id,stripe_subscription_id=EXCLUDED.stripe_subscription_id,
                    stripe_price_id=EXCLUDED.stripe_price_id,current_period_end=EXCLUDED.current_period_end,
                    cancel_at_period_end=EXCLUDED.cancel_at_period_end,billing_provider='stripe',updated_at=NOW()""",
                    code=code,plan=d['plan'],status=d['status'],customer=d['customer_id'],sub=sub_id,
                    price=d['price_id'],end=d['period_end'],cancel=d['cancel_at_period_end'])
            self.access_sync(c,code)
            if d['product']=='ACADEMY' and d['allowed'] and d['payment_state']=='paid':
                payload={k:v.isoformat() if isinstance(v,datetime) else v for k,v in d.items()}
                c.run("""INSERT INTO billing_effects(invoice_id,member_code,details)
                    VALUES(:invoice,:code,CAST(:details AS JSONB)) ON CONFLICT DO NOTHING""",
                    invoice=d['invoice_id'],code=code,details=json.dumps(payload))
            c.run("""INSERT INTO billing_actions(member_code,actor,action,details)
                VALUES(:code,:actor,'stripe_verified',CAST(:details AS JSONB))""",code=code,actor=actor,
                details=json.dumps({'subscription':sub_id,'invoice':d['invoice_id'],'status':d['status'],
                    'period_end':d['period_end'].isoformat() if d['period_end'] else None,
                    'previous_period_end':previous_end.isoformat() if previous_end else None,
                    'admin_suspended':bool(m.get('admin_suspended'))}))
            self._resolve(c,code,sub_id,['SYNC_FAILED','BILLING_PERIOD_MISSING'])
            if d['allowed']:self._resolve(c,code,sub_id,['PAYMENT_REQUIRED'])
            c.run('COMMIT')
        except (ReviewRequired,StripeUnavailable) as e:
            c.run('ROLLBACK')
            self.incident(str(e) if isinstance(e,ReviewRequired) else 'SYNC_FAILED',decision=d,member=code,
                          error=str(e),status='review' if isinstance(e,ReviewRequired) else 'new')
            e.incident_recorded=True
            raise
        except Exception:
            c.run('ROLLBACK')
            self.incident('SYNC_FAILED',decision=d,member=code,error='DATABASE_OR_PROCESSING_ERROR')
            raise
        finally:c.close()
        if d['status'] in {'past_due','unpaid'}:
            self.incident('PAYMENT_REQUIRED',decision=d,member=code)
        from .effects import drain
        drain(self)
        return {'member_code':code,'status':d['status'],'product':d['product'],'allowed':d['allowed']}

    def receive(self,event):
        from .environment import validate_event
        validate_event(event)
        event_id=str(event.get('id') or '')
        event_type=str(event.get('type') or '')
        if not event_id.startswith('evt_'):raise ReviewRequired('INVALID_EVENT_ID')
        if event_type not in EVENT_TYPES:return {'handled':False}
        ref=event_reference(event)
        c=self.get_conn()
        try:
            c.run("""INSERT INTO billing_events(event_id,event_type,reference)
                VALUES(:id,:type,CAST(:ref AS JSONB)) ON CONFLICT DO NOTHING""",id=event_id,type=event_type,ref=json.dumps(ref))
        finally:c.close()
        return self.process_event(event_id)

    def process_event(self,event_id):
        c=self.get_conn()
        try:
            # Durable receipt precedes work; atomic lease prevents concurrent duplicates.
            rows=c.run("""UPDATE billing_events SET status='processing',attempts=attempts+1,
                next_attempt_at=NOW()+INTERVAL '5 minutes'
                WHERE event_id=:id AND (status IN ('pending','failed','review') OR
                 (status='processing' AND next_attempt_at<NOW()))
                RETURNING event_type,reference""",id=event_id)
            if not rows:return {'handled':True,'duplicate':True}
        finally:c.close()
        typ,ref=rows[0]
        if isinstance(ref,str):ref=json.loads(ref)
        try:
            sub_id=ref['subscription_id']
            if typ.startswith('checkout.'):
                session=self.api.get('/checkout/sessions/'+ref['object_id'])
                if sid(session)!=ref['object_id']:raise ReviewRequired('CHECKOUT_IDENTITY_MISMATCH')
                if ref.get('customer_id') and ref['customer_id']!=sid(session.get('customer')):
                    raise ReviewRequired('CHECKOUT_CUSTOMER_MISMATCH')
                sub_id=subscription_id(session)
                if session.get('mode')!='subscription':result={'handled':False}
                elif typ=='checkout.session.expired' or session.get('status')=='expired':
                    db=self.get_conn()
                    try:db.run("UPDATE billing_checkouts SET status='expired' WHERE session_id=:id",id=ref['object_id'])
                    finally:db.close()
                    result={'handled':True,'status':'expired'}
                elif sub_id:result=self.sync(sub_id,expected={**ref,'checkout':session},actor='webhook:'+event_id)
                else:result={'handled':True,'status':'pending'}
            elif sub_id:result=self.sync(sub_id,expected=ref,actor='webhook:'+event_id)
            else:raise ReviewRequired('EVENT_SUBSCRIPTION_MISSING')
            state='processed';error=''
        except ReviewRequired as e:
            state='review';error=str(e);result={'handled':True,'review':error}
            if not getattr(e,'incident_recorded',False):
                self.incident(error,decision={'subscription_id':ref.get('subscription_id','')},error=error,status='review')
        except Exception as e:
            state='failed';error=str(e) if isinstance(e,StripeUnavailable) else 'PROCESSING_UNAVAILABLE'
            result=None
        c=self.get_conn()
        try:
            c.run("""UPDATE billing_events SET status=:status,last_error=:error,
                processed_at=CASE WHEN :status='processed' THEN NOW() ELSE NULL END,
                next_attempt_at=NOW()+INTERVAL '2 minutes' WHERE event_id=:id""",
                status=state,error=error,id=event_id)
        finally:c.close()
        if result is None:raise StripeUnavailable(error)
        return result

    def reconcile(self,actor='scheduler'):
        c=self.get_conn()
        try:
            if not c.run("SELECT pg_try_advisory_lock(hashtextextended(:key,0))",key="billing:reconcile")[0][0]:
                return {'status':'already_running'}
            run_id=c.run('INSERT INTO billing_runs(actor) VALUES(:actor) RETURNING id',actor=actor)[0][0]
            summary={'checked':0,'review':0,'errors':0,'missing_local':0,'missing_stripe':0}
            linked=c.run("""SELECT code,stripe_subscription_id FROM members WHERE COALESCE(stripe_subscription_id,'')<>''
                UNION SELECT user_id,stripe_subscription_id FROM trading_subscriptions WHERE COALESCE(stripe_subscription_id,'')<>''""")
            local={row[1]:row[0] for row in linked};seen=set()
            try:
                # Full pagination must succeed before inferring a remote deletion.
                remote=list(self.api.pages('/subscriptions',status='all'))
                for sub in remote:
                    sub_id=sid(sub);seen.add(sub_id)
                    relevant=any(sid(i.get('price')) in self.prices for i in (sub.get('items') or {}).get('data',[]))
                    if not relevant and sub_id not in local:continue
                    if sub_id not in local:
                        summary['missing_local']+=1
                    try:
                        self.sync(sub_id,actor=actor);summary['checked']+=1
                    except ReviewRequired:summary['review']+=1
                    except Exception:summary['errors']+=1
                for sub_id,code in local.items():
                    if sub_id not in seen:
                        summary['missing_stripe']+=1
                        self.incident('LOCAL_SUBSCRIPTION_NOT_IN_STRIPE',member=code,
                                      decision={'subscription_id':sub_id},status='review')
                historical=c.run("""SELECT code,manual_access_until FROM members
                    WHERE COALESCE(stripe_subscription_id,'')='' AND actif=TRUE
                    AND access_level='member' AND date_fin>NOW()
                    AND NOT EXISTS(SELECT 1 FROM billing_access_exceptions e WHERE e.member_code=members.code
                     AND e.verified AND e.revoked_at IS NULL AND e.expires_at>NOW())""")
                for code,manual in historical:
                    self.incident('EXCEPTION_DOCUMENTATION_REQUIRED',member=code,status='review',details={'manual_extension_present':bool(manual)})
                pending=c.run("SELECT event_id FROM billing_events WHERE status IN ('failed','pending','processing') AND next_attempt_at<=NOW() ORDER BY received_at LIMIT 50")
                for row in pending:
                    try:self.process_event(row[0])
                    except Exception:summary['errors']+=1
            except Exception as e:
                summary['errors']+=1
                self.incident('RECONCILIATION_UNAVAILABLE',error=str(e) if isinstance(e,StripeUnavailable) else 'RECONCILIATION_FAILED')
            from .effects import drain
            drain(self)
            status='failed' if summary['errors'] else 'review' if summary['review'] or summary['missing_local'] or summary['missing_stripe'] else 'completed'
            c.run("UPDATE billing_runs SET status=:status,finished_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id",
                  status=status,s=json.dumps(summary),id=run_id)
            return {'status':status,**summary}
        finally:
            try:c.run("SELECT pg_advisory_unlock(hashtextextended(:key,0))",key="billing:reconcile")
            finally:c.close()
