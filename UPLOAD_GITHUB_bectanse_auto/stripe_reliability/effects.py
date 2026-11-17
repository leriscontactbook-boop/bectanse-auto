"""Durable, deduplicated follow-up work after a verified Academy payment."""
import json


def drain(service, limit=20):
    if not service.after_payment:return
    c=service.get_conn()
    try:
        jobs=c.run("""UPDATE billing_effects SET status='processing',attempts=attempts+1,
            retry_at=NOW()+INTERVAL '5 minutes' WHERE id IN
            (SELECT id FROM billing_effects WHERE status<>'done' AND retry_at<=NOW()
             ORDER BY id FOR UPDATE SKIP LOCKED LIMIT :limit)
            RETURNING id,member_code,details""",limit=limit)
    finally:c.close()
    for ident,code,details in jobs:
        if isinstance(details,str):details=json.loads(details)
        try:
            service.after_payment(code,details)
            state,error='done',''
        except Exception:
            state,error='failed','PAYMENT_FOLLOWUP_FAILED'
            service.incident(error,member=code,decision=details,error=error)
        c=service.get_conn()
        try:
            c.run("""UPDATE billing_effects SET status=:status,last_error=:error,
                retry_at=NOW()+INTERVAL '5 minutes' WHERE id=:id""",status=state,error=error,id=ident)
            if state=='done':service._resolve(c,code,details.get('subscription_id',''),['PAYMENT_FOLLOWUP_FAILED'])
        finally:c.close()


def deliver_once(service,key,member,decision,send):
    """Ambiguous delivery requires provider verification, never an automatic resend."""
    c=service.get_conn()
    try:
        claimed=c.run("""INSERT INTO billing_deliveries(delivery_key,status)
            VALUES(:key,'sending') ON CONFLICT DO NOTHING RETURNING delivery_key""",key=key)
        if not claimed:
            state=c.run('SELECT status FROM billing_deliveries WHERE delivery_key=:key',key=key)[0][0]
            if state!='sent':service.incident('DELIVERY_OUTCOME_UNKNOWN',member=member,decision=decision,status='review')
            return
    finally:c.close()
    try:
        result=send()
        confirmed=isinstance(result,dict) and result.get('ok') is True
    except Exception:
        confirmed=False
    c=service.get_conn()
    try:c.run('UPDATE billing_deliveries SET status=:status WHERE delivery_key=:key',status='sent' if confirmed else 'unknown',key=key)
    finally:c.close()
    if not confirmed:service.incident('DELIVERY_OUTCOME_UNKNOWN',member=member,decision=decision,status='review')
