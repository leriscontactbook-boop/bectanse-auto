"""Transactional copy revocation and one durable admin notification per invoice."""
import json
import requests

SCHEMA = """CREATE TABLE IF NOT EXISTS billing_copy_alerts(
 member_code TEXT NOT NULL, invoice_id TEXT NOT NULL, message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', message_id TEXT, error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 PRIMARY KEY(member_code,invoice_id))"""


RECOVERY_SCHEMA = """CREATE TABLE IF NOT EXISTS billing_copy_recovery(
 member_code TEXT PRIMARY KEY, snapshot JSONB NOT NULL, paused_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 resolved_at TIMESTAMPTZ, outcome TEXT NOT NULL DEFAULT 'pending')"""


def remember(conn, code):
    conn.run(RECOVERY_SCHEMA)
    conn.run("SELECT code FROM members WHERE code=:c FOR UPDATE",c=code)
    conn.run("""INSERT INTO billing_copy_recovery(member_code,snapshot)
      SELECT m.code,jsonb_build_object('copy_actif',m.copy_actif,
        'control',(SELECT to_jsonb(t) FROM copy_real_controls t WHERE t.member_code=m.code),
        'approval',(SELECT to_jsonb(t) FROM copy_admin_controls t WHERE t.member_code=m.code),
        'release',(SELECT to_jsonb(t) FROM copy_real_releases t WHERE t.member_code=m.code))
      FROM members m WHERE m.code=:c
      ON CONFLICT(member_code) DO UPDATE SET snapshot=EXCLUDED.snapshot,paused_at=NOW(),
        resolved_at=NULL,outcome='pending' WHERE billing_copy_recovery.resolved_at IS NOT NULL""",c=code)


def resume(conn, code):
    """Restore prior start intent after confirmed payment, never invent execution permission."""
    conn.run(RECOVERY_SCHEMA)
    member=conn.run('SELECT to_jsonb(m) FROM members m WHERE code=:c FOR UPDATE',c=code)[0][0]
    if isinstance(member,str):member=json.loads(member)
    from trading_journal.entitlements import academy_membership_state
    if not academy_membership_state(member)[0]:return 'access_still_blocked'
    rows=conn.run('SELECT snapshot,paused_at FROM billing_copy_recovery WHERE member_code=:c AND resolved_at IS NULL',c=code)
    if not rows:return 'no_previous_copy_state'
    saved,at=rows[0]
    if isinstance(saved,str):saved=json.loads(saved)
    # An admin/member instruction made during the pause always wins over billing recovery.
    manual=conn.run("""SELECT 1 FROM copy_admin_events WHERE target=:c AND created_at>=:at
        AND actor<>'billing' LIMIT 1""",c=code,at=at)
    control=saved.get('control');approval=saved.get('approval')
    outcome='previously_inactive'
    if manual:outcome='manual_change_preserved'
    elif control and control.get('desired')=='ACTIVE' and approval and approval.get('decision')=='APPROVED':
        current=conn.run('SELECT account_id,version,revision,desired FROM copy_real_controls WHERE member_code=:c',c=code)
        config=conn.run('SELECT account_id,version FROM copy_configurations WHERE member_code=:c ORDER BY version DESC LIMIT 1',c=code)
        current_approval=conn.run('SELECT version,decision FROM copy_admin_controls WHERE member_code=:c',c=code)
        pending=conn.run("SELECT COUNT(*) FROM copy_real_operations WHERE member_code=:c AND state IN ('RESERVED','UNKNOWN')",c=code)[0][0]
        expected=[control['account_id'],control['version'],control['revision']+1,'STOPPED']
        if pending:return 'pending_operation_reconciliation'
        if (current and list(current[0])==expected and config and list(config[0])==expected[:2]
                and current_approval and list(current_approval[0])==[approval['version'],'PAUSED']):
            conn.run("UPDATE copy_admin_controls SET decision='APPROVED',updated_at=NOW() WHERE member_code=:c",c=code)
            conn.run("""UPDATE copy_real_controls SET desired='ACTIVE',revision=revision+1,
                confirmed='REQUEST_RECEIVED',confirmed_at=NULL WHERE member_code=:c""",c=code)
            release=saved.get('release')
            if release:
                # Restore only the original, still-valid proof; never extend its lifetime.
                conn.run("""UPDATE copy_real_releases SET expires_at=CAST(:expiry AS TIMESTAMPTZ)
                  WHERE member_code=:c AND configuration_digest=:d AND qualification_digest=:q
                    AND approved_by=:actor AND CAST(:expiry AS TIMESTAMPTZ)>NOW()""",
                  expiry=release['expires_at'],c=code,d=release['configuration_digest'],
                  q=release['qualification_digest'],actor=release['approved_by'])
            from copy_trading.onboarding_server import enqueue
            enqueue(conn,code,control['version'],control['account_id'],True)
            conn.run("""UPDATE copy_preparation_jobs SET due_at=NOW(),updated_at=NOW()
                WHERE member_code=:c AND (lease_until IS NULL OR lease_until<NOW())""",c=code)
            conn.run('UPDATE members SET copy_actif=:a WHERE code=:c',a=bool(saved.get('copy_actif')),c=code)
            outcome='resume_requested'
        else:outcome='configuration_or_control_changed'
    elif not control and saved.get('copy_actif'):
        # No real engine was active: restore only the existing legacy access flag.
        conn.run('UPDATE members SET copy_actif=TRUE WHERE code=:c',c=code)
        outcome='legacy_access_restored'
    conn.run('UPDATE billing_copy_recovery SET resolved_at=NOW(),outcome=:o WHERE member_code=:c',o=outcome,c=code)
    conn.run("INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:c,'billing')",a='BILLING_RECOVERY_'+outcome.upper(),c=code)
    return outcome


def suspend(conn, code, invoice, source):
    # Caller owns the billing transaction. Same member lock as trade reservations.
    remember(conn,code)
    rows=conn.run('SELECT nom,email FROM members WHERE code=:c FOR UPDATE',c=code)
    if not rows:raise ValueError('MEMBER_NOT_FOUND')
    conn.run("UPDATE members SET actif=FALSE,copy_actif=FALSE,billing_status='past_due' WHERE code=:c",c=code)
    conn.run("""UPDATE copy_real_controls SET desired='STOPPED',revision=revision+1,
        confirmed='REQUEST_RECEIVED',confirmed_at=NULL WHERE member_code=:c AND desired<>'STOPPED'""",c=code)
    conn.run("UPDATE copy_admin_controls SET decision='PAUSED',updated_at=NOW() WHERE member_code=:c AND decision='APPROVED'",c=code)
    conn.run("UPDATE copy_real_releases SET expires_at=LEAST(expires_at,NOW()) WHERE member_code=:c",c=code)
    # Preserve operation evidence; an already submitted order cannot be called cancelled.
    count=conn.run("SELECT COUNT(*) FROM copy_real_operations WHERE member_code=:c AND state IN ('RESERVED','UNKNOWN')",c=code)[0][0]
    name,email=rows[0]
    message=(f"ALERTE — RENOUVELLEMENT IMPAYÉ\n{name}\n{email}\nCode : {code}\n"
             f"Facture : {invoice}\nSource : {source}\n"
             "Accès suspendu ; nouvelles copies interdites ; arrêt du moteur demandé.\n"
             f"Opérations à rapprocher : {count}. Les positions existantes ne sont pas clôturées.\n"
             f"https://acces.bectanse-academie.com/admin-panel?member={code}")
    conn.run(SCHEMA)
    conn.run("""INSERT INTO billing_copy_alerts(member_code,invoice_id,message)
        VALUES(:c,:i,:m) ON CONFLICT(member_code,invoice_id) DO NOTHING""",c=code,i=invoice,m=message)


def deliver(appmod):
    c=appmod.get_conn()
    try:
        c.run(SCHEMA)
        if not appmod.BOT_TOKEN or not appmod.ADMIN_ID:
            raise RuntimeError('ADMIN_TELEGRAM_NOT_CONFIGURED')
        # Claim durably before I/O. An ambiguous delivery is not resent blindly.
        rows=c.run("""UPDATE billing_copy_alerts SET status='sending',updated_at=NOW()
            WHERE (member_code,invoice_id) IN (
              SELECT member_code,invoice_id FROM billing_copy_alerts WHERE status='pending'
              ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED)
            RETURNING member_code,invoice_id,message""")
        for code,invoice,message in rows:
            status='unknown';mid='';error=''
            try:
                response=requests.post(f'https://api.telegram.org/bot{appmod.BOT_TOKEN}/sendMessage',
                    json={'chat_id':appmod.ADMIN_ID,'text':message,'disable_web_page_preview':True},timeout=10)
                data=response.json()
                if data.get('ok'):
                    status='sent';mid=str(data['result']['message_id'])
                else:
                    status='failed';error=str(data.get('description','Telegram rejected notification'))[:200]
            except Exception as exc:error=type(exc).__name__
            c.run("""UPDATE billing_copy_alerts SET status=:s,message_id=:m,error=:e,updated_at=NOW()
                WHERE member_code=:c AND invoice_id=:i""",s=status,m=mid,e=error,c=code,i=invoice)
            if status!='sent':appmod.app.logger.error('Billing admin alert %s: %s %s',code,status,error)
    finally:c.close()


def unpaid_invoice_url(member, api):
    """Read-only recovery link, scoped to this authenticated customer's subscription."""
    from urllib.parse import urlparse
    from stripe_reliability.policy import sid
    customer=member.get('stripe_customer_id');subscription=member.get('stripe_subscription_id')
    if not customer or not subscription:return None
    invoices=list(api.pages('/invoices',customer=customer,subscription=subscription,status='open'))
    for invoice in sorted(invoices,key=lambda row:row.get('created',0),reverse=True):
        linked=sid(invoice.get('subscription') or ((invoice.get('parent') or {}).get('subscription_details') or {}).get('subscription'))
        url=str(invoice.get('hosted_invoice_url') or '')
        parsed=urlparse(url)
        if (sid(invoice.get('customer'))==customer and linked==subscription
                and invoice.get('status')=='open' and (invoice.get('amount_remaining') or 0)>0
                and parsed.scheme=='https' and parsed.hostname=='invoice.stripe.com'):
            return url
    return None
