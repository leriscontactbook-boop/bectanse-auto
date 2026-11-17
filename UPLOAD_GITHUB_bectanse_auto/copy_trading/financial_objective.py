"""Member-approved rule (2026-10-15): calendar Paris, realized + floating,
percentage of starting balance, next-period reset. Returns decisions only.
"""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .demo_policy import SafetyError,decimal

RULE='PARIS_CALENDAR_REALIZED_PLUS_FLOATING_START_BALANCE_PERIOD_RESET_V2'


def period_start(period,now):
    value=datetime.fromtimestamp(now,ZoneInfo('Europe/Paris')).replace(hour=0,minute=0,second=0,microsecond=0)
    if period=='Hebdomadaire':value-=timedelta(days=value.weekday())
    elif period=='Mensuel':value=value.replace(day=1)
    elif period!='Quotidien':raise SafetyError('OBJECTIVE_PERIOD_INVALID')
    return value.timestamp()


def evaluate(settings,observation,now):
    drawdown=evaluate_drawdown(settings,observation,now)
    if drawdown['blocked']:return drawdown
    if settings.get('objectif_actif') is not True:return {'enabled':False,'blocked':False,'rule':RULE}
    start=period_start(settings['objectif_periode'],now)
    o=observation
    if (o.get('source')!='MT5_TERMINAL' or not o.get('financial_consistent')
        or not start<=o['history_to']<=o['observed_at']<=now<o['expires_at']
        or o['history_from']>start
        or now-o['history_to']>30 or o['expires_at']-o['history_to']>30):
        raise SafetyError('OBJECTIVE_FINANCIAL_EVIDENCE_REQUIRED')
    balance_change=decimal(0);realized=decimal(0);credit_change=decimal(0);seen=set()
    for deal in o['deals']:
        if deal['ticket'] in seen:raise SafetyError('OBJECTIVE_DUPLICATE_DEAL')
        seen.add(deal['ticket'])
        if not o['history_from']<=deal['time']<=o['history_to']:raise SafetyError('OBJECTIVE_HISTORY_INVALID')
        if deal['time']<start:continue
        # Preserve the existing financial collector's realized-trade convention.
        # Unclassified financial adjustments must be reviewed, never guessed.
        if deal['type']==3:
            # MT5 DEAL_TYPE_CREDIT changes broker credit, separate from balance.
            # https://www.mql5.com/en/book/automation/account/account_state
            # Preserve it in evidence; never treat it as trading P/L or a deposit.
            if any(decimal(deal[k]) for k in ('commission','swap','fee')):
                raise SafetyError('OBJECTIVE_UNCLASSIFIED_BALANCE_OPERATION')
            credit_change+=decimal(deal['profit'])
            continue
        if deal['type'] not in (0,1,2):raise SafetyError('OBJECTIVE_UNCLASSIFIED_BALANCE_OPERATION')
        amount=sum((decimal(deal[k]) for k in ('profit','commission','swap','fee')),decimal(0))
        balance_change+=amount
        if deal['type'] in (0,1):realized+=amount
    baseline=decimal(o['balance'])-balance_change
    if baseline<=0:raise SafetyError('OBJECTIVE_START_BALANCE_INVALID')
    positions=o['positions']
    ids=[p.get('identifier') for p in positions]
    if any(i is None for i in ids) or len(set(ids))!=len(ids):
        raise SafetyError('OBJECTIVE_POSITION_IDENTITY_INVALID')
    floating=sum((decimal(p['profit'])+decimal(p['swap']) for p in positions),decimal(0))
    net=realized+floating;percent=100*net/baseline
    gain=decimal(settings['objectif_gain_pct']);loss=decimal(settings['objectif_perte_pct'])
    if gain<=0 or loss<=0:raise SafetyError('OBJECTIVE_THRESHOLDS_INVALID')
    reason='GAIN_TARGET_REACHED' if percent>=gain else 'LOSS_LIMIT_REACHED' if percent<=-loss else None
    return dict(enabled=True,rule=RULE,period_start=start,period=settings['objectif_periode'],
                currency=o['currency'],starting_balance=str(baseline),realized=str(realized),
                floating=str(floating),net=str(net),percent=str(percent),credit_change=str(credit_change),blocked=reason is not None,
                reason=reason,observed_at=o['observed_at'],expires_at=o['expires_at'])


def evaluate_drawdown(settings,observation,now):
    if not (settings.get('drawdown_actif') or settings.get('drawdown_gain_actif')):
        return {'enabled':False,'blocked':False}
    o=observation
    if (o.get('source')!='MT5_TERMINAL' or not o.get('financial_consistent')
        or not o.get('observed_at',now+1)<=now<o.get('expires_at',0)
        or now-o['observed_at']>30):raise SafetyError('DRAWDOWN_FINANCIAL_EVIDENCE_REQUIRED')
    balance=decimal(o['balance'])
    if balance<=0:raise SafetyError('DRAWDOWN_BALANCE_INVALID')
    floating=sum((decimal(p['profit'])+decimal(p['swap']) for p in o['positions']),decimal(0))
    percent=100*floating/balance
    reason=('DRAWDOWN_LOSS_REACHED' if settings.get('drawdown_actif') and percent<=-decimal(settings['drawdown_pct'])
            else 'DRAWDOWN_GAIN_REACHED' if settings.get('drawdown_gain_actif') and percent>=decimal(settings['drawdown_gain_pct']) else None)
    return dict(enabled=True,blocked=reason is not None,reason=reason,close_copies=reason is not None,
                rule='ACCOUNT_FLOATING_OVER_CURRENT_BALANCE_V1',percent=str(percent),balance=str(balance),
                observed_at=o['observed_at'],expires_at=o['expires_at'])
