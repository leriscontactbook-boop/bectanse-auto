"""Read-only statistics from the journal, never from a trading command."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def financials(account, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = account.get('last_successful_sync_at')
    try:
        observed = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        age = (now - observed.replace(tzinfo=observed.tzinfo or timezone.utc)).total_seconds()
    except (ValueError, TypeError):
        age = None
    fresh = age is not None and 0 <= age <= 30 and account.get('sync_status') not in ('ERROR','FAILED')
    def number(key):
        try:
            value=Decimal(str(account.get(key)))
            return float(value) if value.is_finite() else None
        except (InvalidOperation, ValueError, TypeError):return None
    return dict(balance=number('balance'),equity=number('equity'),currency=account.get('currency') or '',
                broker=account.get('broker') or '',platform=account.get('platform') or 'MT5',
                observed_at=str(stamp) if stamp else None,fresh=fresh,
                source='Journal MT5',daily=None,weekly=None,monthly=None,total=None)


def add_history(conn, followers):
    # Older installations and isolated fixtures may not have the journal table.
    if not conn.run("SELECT to_regclass('trading_deals')")[0][0]:return
    import json
    eligible=[r for r in followers if r.get('statistics',{}).get('observed_at') and r.get('account_id')]
    if not eligible:return
    inputs=[dict(account=r['account_id'],code=r['code'],observed=r['statistics']['observed_at']) for r in eligible]
    rows=conn.run("""SELECT wanted.account,wanted.code,COUNT(*),
      SUM(profit+commission+swap+fee) FILTER (WHERE executed_at >= date_trunc('day', NOW() AT TIME ZONE 'Europe/Paris') AT TIME ZONE 'Europe/Paris'),
      SUM(profit+commission+swap+fee) FILTER (WHERE executed_at >= date_trunc('week', NOW() AT TIME ZONE 'Europe/Paris') AT TIME ZONE 'Europe/Paris'),
      SUM(profit+commission+swap+fee) FILTER (WHERE executed_at >= date_trunc('month', NOW() AT TIME ZONE 'Europe/Paris') AT TIME ZONE 'Europe/Paris'),
      SUM(profit+commission+swap+fee)
      FROM jsonb_to_recordset(CAST(:wanted AS JSONB)) AS wanted(account BIGINT,code TEXT,observed TIMESTAMPTZ)
      JOIN trading_accounts a ON a.id=wanted.account AND a.user_id=wanted.code
      JOIN trading_deals d ON d.trading_account_id=a.id
      WHERE d.is_trading_deal=TRUE AND d.executed_at<=wanted.observed
      GROUP BY wanted.account,wanted.code""",wanted=json.dumps(inputs))
    lookup={(r[0],r[1]):r[3:] for r in rows}
    for row in eligible:
        values=lookup.get((row['account_id'],row['code']))
        if values is not None:
            for key,value in zip(('daily','weekly','monthly','total'),values):row['statistics'][key]=float(value or 0)
