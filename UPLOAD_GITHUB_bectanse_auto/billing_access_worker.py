"""Verified Academy access and daily unpaid-renewal reminders, independent of legacy jobs."""
import hashlib
import json
import os
import signal
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from stripe_reliability.policy import utc, sid

MESSAGE = 'Ton abonnement n’a pas été renouvelé et tes accès sont suspendus. Reprends ta formation, tes outils et les échanges avec la communauté : régularise ton paiement depuis ton espace membre.'
URL = 'https://acces.bectanse-academie.com/abonnement'


def reminder_content(member, decision):
    ended=decision.get('stripe_status') in {'canceled','incomplete_expired'}
    if ended:
        body=['Ton précédent abonnement est résilié. Pour retrouver les accès de l’Académie, connecte-toi à ton espace avec ton compte existant et choisis une nouvelle formule.',
              'Le paiement d’une ancienne facture ne réactive pas un abonnement résilié. Les éventuelles factures antérieures restent distinctes du nouvel abonnement.']
        if member.get('admin_suspended'):
            body.append('Une suspension manuelle est également présente sur ton compte : ton espace t’indiquera comment contacter l’équipe avant de payer.')
        return dict(subject='Ton abonnement Bectanse : reprends avec ton compte existant',
            title='Reprends ton abonnement',body=body,cta='Reprendre un abonnement',
            url=URL+'?reprise=1#formules',preheader='Retrouve les formules depuis ton espace, sans recréer de compte.')
    return dict(subject='Ton abonnement Bectanse : paiement à régulariser',title='Ton accès est suspendu',
        body=[MESSAGE],cta='Régulariser mon paiement',url=URL,
        preheader='Régularise ton paiement depuis ton espace membre.')


def access_decision(member, decision, now):
    manual = utc(member.get('manual_access_until'))
    paid = decision.get('paid_until')
    end = max([x for x in (manual, paid) if x], default=None)
    overdue = bool(decision.get('payment_state') == 'open' and (decision.get('amount') or 0) > 0
                   and paid and paid <= now)
    allowed = bool(not member.get('admin_suspended') and not overdue and
                   (decision['allowed'] or (manual and manual > now and member.get('actif'))))
    return end, overdue, allowed


def send_reminder(appmod, conn, member, decision, now):
    import requests
    email = (member.get('email') or '').strip().lower()
    if '@' not in email or not os.environ.get('BREVO_KEY'):
        return 'unavailable'
    if any(member.get(k) for k in ('email_unsubscribed','email_blacklisted','do_not_email')):
        return 'excluded'
    headers = {'api-key': os.environ['BREVO_KEY'], 'Content-Type':'application/json'}
    from urllib.parse import quote
    contact = requests.get('https://api.brevo.com/v3/contacts/'+quote(email, safe=''), headers=headers, timeout=15)
    if contact.status_code == 200 and contact.json().get('emailBlacklisted'):
        return 'excluded'
    if contact.status_code not in (200,404):
        return 'provider_unavailable'
    digest=hashlib.sha256(email.encode()).hexdigest()
    invoice=decision['invoice_id']
    if conn.run("SELECT 1 FROM billing_daily_reminders WHERE recipient_hash=:h AND invoice_id=:i AND status IN ('sending','unknown')",h=digest,i=invoice):
        return 'unknown_previous_delivery'
    day=now.astimezone(ZoneInfo('Europe/Paris')).date()
    claimed=conn.run("""INSERT INTO billing_daily_reminders(recipient_hash,day,member_code,invoice_id,status)
        VALUES(:h,:day,:code,:invoice,'sending') ON CONFLICT DO NOTHING RETURNING day""",
        h=digest,day=day,code=member['code'],invoice=invoice)
    if not claimed:return 'already_sent'
    content=reminder_content(member,decision)
    html=appmod._premium_transactional_email('Ton abonnement', content['title'], content['body'],
        content['cta'], content['url'], preheader=content['preheader'])
    status='unknown';message_id=''
    try:
        response=requests.post('https://api.brevo.com/v3/smtp/email',headers=headers,timeout=20,json={
            'sender':{'email':'lerisluketo@bectanse-academie.com','name':'Bectanse Académie'},
            'to':[{'email':email,'name':member.get('nom') or 'Membre'}],
            'subject':content['subject'],'htmlContent':html,
            'tags':['bectanse-renewal-daily']})
        if response.status_code==201 and response.json().get('messageId'):
            status='sent';message_id=response.json()['messageId']
        elif 400<=response.status_code<500:status='rejected'
    except requests.RequestException:pass
    conn.run("UPDATE billing_daily_reminders SET status=:s,provider_message_id=:mid WHERE recipient_hash=:h AND day=:d",
             s=status,mid=message_id,h=digest,d=day)
    return status


def cycle(appmod, *, send=False, force_today=False):
    from trading_journal.billing import reconcile_trading_access
    service=appmod.billing_service();conn=appmod.get_conn()
    result={'checked':0,'corrected':[],'overdue':[],'errors':[],'emails':{}}
    try:
        if not conn.run('SELECT pg_try_advisory_lock(73291028)')[0][0]:return {'busy':True}
        conn.run("""CREATE TABLE IF NOT EXISTS billing_daily_reminders(
            recipient_hash TEXT NOT NULL,day DATE NOT NULL,member_code TEXT NOT NULL,invoice_id TEXT NOT NULL,
            status TEXT NOT NULL,provider_message_id TEXT NOT NULL DEFAULT '',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(recipient_hash,day))""")
        subs=conn.run("SELECT code,stripe_subscription_id FROM members WHERE COALESCE(stripe_subscription_id,'')<>''")
        for code,sub in subs:
            try:
                conn.run('BEGIN')
                conn.run("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))", k='billing:'+sub)
                d,_=service._current(sub)
                if d['product']!='ACADEMY':raise ValueError('WRONG_PRODUCT')
                rows=conn.run('SELECT row_to_json(m) FROM members m WHERE code=:c FOR UPDATE',c=code)
                m=rows[0][0];m=json.loads(m) if isinstance(m,str) else m
                if m.get('stripe_subscription_id')!=sub or m.get('stripe_customer_id')!=d['customer_id']:
                    raise ValueError('MEMBER_IDENTITY_CHANGED')
                now=datetime.now(timezone.utc);end,overdue,allowed=access_decision(m,d,now)
                # Never manufacture a period for a trial or an invoice with missing evidence.
                status='past_due' if overdue else d['status']
                old=utc(m.get('date_fin'))
                details={'subscription':sub,'invoice':d['invoice_id'],'old_end':str(old),'new_end':str(end),
                         'stripe_status':d['stripe_status'],'status':status,'allowed':allowed}
                if overdue:
                    from stripe_reliability.copy_incidents import suspend
                    suspend(conn,code,d['invoice_id'],'Contrôle de secours Stripe')
                conn.run("""UPDATE members SET date_fin=:end,billing_status=:status,billing_current_period_end=:period,
                    billing_period_start=:start,billing_verified_at=NOW(),billing_access_reason=:reason,
                    actif=:allowed,copy_actif=CASE WHEN :allowed THEN copy_actif ELSE FALSE END
                    WHERE code=:code""",end=end.replace(tzinfo=None) if end else None,status=status,
                    period=d['period_end'].replace(tzinfo=None) if d['period_end'] else None,start=d['period_start'],
                    reason='admin_suspended' if m.get('admin_suspended') else 'payment_overdue' if overdue else d['reason'],allowed=allowed,code=code)
                if old!=end or m.get('billing_status')!=status or bool(m.get('actif'))!=allowed:
                    conn.run("INSERT INTO billing_actions(member_code,actor,action,details) VALUES(:c,'verified-worker','access_reconciled',CAST(:d AS JSONB))",c=code,d=json.dumps(details))
                if allowed and d['payment_state']=='paid':
                    from stripe_reliability.copy_incidents import resume
                    resume(conn,code)
                reconcile_trading_access(conn,code)
                conn.run('COMMIT')
                result['checked']+=1
                if old!=end:result['corrected'].append({'code':code,**details})
                if overdue:
                    result['overdue'].append(code)
                    if send and (force_today or now.astimezone(ZoneInfo('Europe/Paris')).hour>=9):
                        # Re-read Stripe immediately before any message: a regularized account is excluded.
                        fresh,_=service._current(sub)
                        if access_decision(m,fresh,datetime.now(timezone.utc))[1]:
                            result['emails'][code]=send_reminder(appmod,conn,m,fresh,now)
            except Exception as exc:
                try:conn.run('ROLLBACK')
                except Exception:pass
                result['errors'].append({'code':code,'error':type(exc).__name__+':'+str(exc)[:100]})
        conn.run("INSERT INTO billing_runs(actor,status,finished_at,summary) VALUES('verified-daily-worker',:s,NOW(),CAST(:d AS JSONB))",s='partial' if result['errors'] else 'done',d=json.dumps(result))
        return result
    finally:conn.close()


def main():
    os.environ['BECTANSE_SKIP_STARTUP']='1'
    import app
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    from stripe_reliability.copy_incidents import deliver
    import time
    next_cycle=0
    while not stop.is_set():
        try:deliver(app)
        except Exception as e:app.app.logger.error("Billing alert delivery unavailable: %s",type(e).__name__)
        if time.monotonic()<next_cycle:
            stop.wait(5)
            continue
        next_cycle=time.monotonic()+300
        try:app.app.logger.info('Verified billing cycle: %s',cycle(app,send=True))
        except Exception as e:app.app.logger.error('Verified billing cycle unavailable: %s',type(e).__name__)
        stop.wait(5)

if __name__=='__main__':main()
