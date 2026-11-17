"""Reconcile paid access without charging, canceling or placing trading orders."""
from trading_journal.entitlements import academy_membership_state


def enforce_copy_access(conn):
    conn.run("""UPDATE members SET actif=FALSE
        WHERE COALESCE(access_level,'member') NOT IN ('explorer','demo')
        AND actif=TRUE AND date_fin IS NOT NULL AND date_fin<=NOW()
        AND (manual_access_until IS NULL OR manual_access_until<=NOW())""")
    rows = conn.run("""SELECT code,actif,access_level,billing_status,
        billing_current_period_end,date_fin,admin_suspended,manual_access_until,
        broker_trial_id,broker_trial_status,broker_trial_approved_at,broker_trial_until
        FROM members WHERE copy_actif=TRUE""")
    fields = ('code','actif','access_level','billing_status',
              'billing_current_period_end','date_fin','admin_suspended','manual_access_until',
              'broker_trial_id','broker_trial_status','broker_trial_approved_at','broker_trial_until')
    changed = []
    for row in rows:
        member = dict(zip(fields, row))
        if not academy_membership_state(member)[0]:
            conn.run('UPDATE members SET copy_actif=FALSE WHERE code=:code', code=row[0])
            changed.append(row[0])
    return changed
