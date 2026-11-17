"""Read-only onboarding projection from existing persistent records.

No new lifecycle table, admission decision, command or financial side effect.
Execution remains exclusively governed by real_control and its native executor.
"""
from datetime import datetime, timezone
import time
from .service import decoded
from .onboarding_server import status as preparation_status, LABELS
from .real_control import current, state, eligibility

MESSAGES={
 'ACTIVATION_TIMEOUT':'Activation non confirmée dans les 2 minutes. Vérifiez la connexion MT5 et réessayez ; aucune copie ni position n’a été modifiée.',
 'NEWS_HIGH_IMPACT_WINDOW':'Nouvelles entrées suspendues pendant une annonce majeure. Reprise automatique après la fenêtre de 15 minutes.',
 'TERMINAL_CONNECTION_FAILED':'Connexion MT5 impossible après les tentatives automatiques. Vérifiez la disponibilité du serveur et relancez le diagnostic.',
 'TERMINAL_AUTH_FAILED':'Authentification MT5 refusée. Vérifiez les identifiants dans le compte concerné.',
 'CHECK_UNAVAILABLE':'Vérification MT5 indisponible. Le système relancera une vérification bornée.',
 'NEWS_CALENDAR_UNAVAILABLE':'Calendrier économique temporairement indisponible. Les positions copiées restent gérées.',
 'RISK_STOP_LOSS_REQUIRED':'Le calcul du risque en pourcentage nécessite un stop-loss valide sur le signal du maître.',
 'VOLUME_BELOW_MINIMUM':'Le volume calculé est inférieur au minimum du courtier. Le réglage Lot minimum peut être utilisé dans la limite de votre plafond.',
 'UNSUPPORTED_LOT_MODE':'Le mode de calcul des lots choisi n’est pas encore pris en charge. Sélectionnez Lots fixes ou Lot par symbole.',
 'REQUESTED_VOLUME_NOT_EXECUTABLE':'Le volume choisi ne respecte pas le minimum, le maximum ou le pas de lot du courtier.',
 'CONFIGURATION_REQUIRED':'Enregistrez votre compte MT5 et ses paramètres.',
 'MASTER_CHANGED':'Choisissez un maître ou faites vérifier le rattachement.',
 'ADMIN_PERMISSION_REQUIRED':'Le rattachement attend la validation de l’administration.',
 'CONNECTION_EVIDENCE_REQUIRED':'Le serveur attend la confirmation de connexion MT5.',
 'CONNECTION_EVIDENCE_STALE_OR_CHANGED':'La connexion MT5 doit être confirmée à nouveau.',
 'OPERATIONAL_RELEASE_REQUIRED':'Synchronisation technique en cours : la connexion MT5 est établie et le moteur renouvelle sa validation des nouvelles entrées.',
 'OBJECTIVE_MANUAL_RESUME_REQUIRED':'Seuil de protection atteint. Les objectifs se réinitialisent à la prochaine période ; la protection du solde nécessite une reprise.',
 'OBJECTIVE_STILL_REACHED':'Le seuil financier est toujours atteint. La reprise est refusée.',
 'TERMINAL_EXECUTION_NOT_READY':'Le compte est connecté mais les autorisations du terminal restent à vérifier.',
 'MEMBERSHIP_INACTIVE':'L’accès à l’Académie est inactif. La copie ne peut pas démarrer.',
}

ACTIVATION_TIMEOUT_SECONDS=120


def activation_timed_out(preparation, started_at=None, now=None):
    """True only for a real, still-pending ON older than the stated limit."""
    marker=started_at or (preparation or {}).get('updated_at')
    if not marker:
        return False
    try:
        updated=marker if isinstance(marker,datetime) else datetime.fromisoformat(marker.replace('Z','+00:00'))
        if updated.tzinfo is None:
            updated=updated.replace(tzinfo=timezone.utc)
        age=(time.time() if now is None else now)-updated.timestamp()
    except (TypeError,ValueError,AttributeError):
        return False
    return age>ACTIVATION_TIMEOUT_SECONDS


def status(conn,code):
    result=dict(state='REGISTERED',message=MESSAGES['CONFIGURATION_REQUIRED'],
                execution_enabled=False,can_activate=False,can_resume=False,
                connected=False,reason='CONFIGURATION_REQUIRED')
    try:a,v,p,_=current(conn,code)
    except ValueError:return result
    result.update(account_id=a,version=v)
    owned=conn.run("SELECT 1 FROM trading_accounts WHERE id=:a AND user_id=:m",a=a,m=code)
    if not owned:
        return {**result,'state':'BLOCKED','reason':'ACCOUNT_IDENTITY_CHANGED','message':LABELS['ACCOUNT_IDENTITY_CHANGED']}
    from .real_preparation import public_observation
    connection=public_observation(conn,code,a,v)
    connected=bool(connection and connection['connected'])
    result['connected']=connected
    control=state(conn,code)
    result['control']=control
    # A worker retry updates the preparation job. That must never extend the
    # member's 120-second activation window; anchor it to the latest explicit
    # ON command instead. A new click intentionally starts a new window.
    # The deadline belongs to the ACTIVE transition, not to a command receipt.
    # Repeated clicks, retrying a lost HTTP response, and polling must never
    # extend an activation that is already pending.
    activation_started=control.get('activation_started_at')
    if activation_started is None and control.get('desired')=='ACTIVE':
        # One-time compatibility path for controls created before the durable
        # transition timestamp existed.  The earliest request is conservative:
        # it ends a historical pending activation instead of letting it run on.
        activation_rows=conn.run("""SELECT MIN(created_at) FROM copy_member_commands
          WHERE member_code=:m AND response->>'desired'='ACTIVE'""",m=code)
        activation_started=activation_rows[0][0] if activation_rows and activation_rows[0][0] else None
    # Historical risk latch must remain visible even while desired=STOPPED.
    latched=bool(conn.run('SELECT 1 FROM copy_real_objective_latches WHERE member_code=:m',m=code))
    if latched:
        # Offering a request never clears the latch; server rechecks every gate.
        from .financial_objective import evaluate
        from .demo_policy import SafetyError
        import time
        try:
            rows=conn.run('SELECT payload FROM copy_real_observations WHERE member_code=:m AND account_id=:a AND version=:v',m=code,a=a,v=v)
            result['can_resume']=bool(rows) and connected and not evaluate(p['settings'],decoded(rows[0][0]),time.time())['blocked']
        except (SafetyError,ValueError,TypeError,KeyError):pass
        return {**result,'state':'BLOCKED_RISK','reason':'OBJECTIVE_MANUAL_RESUME_REQUIRED',
                'message':MESSAGES['OBJECTIVE_MANUAL_RESUME_REQUIRED']}
    prep=preparation_status(conn,code)
    try:
        eligibility(conn,code)
        reason=None
    except (ValueError,RuntimeError) as error:
        reason=str(error)
    if reason is None:
        if control.get('execution_enabled') is True:
            return {**result,'state':'ACTIVE','reason':None,'message':'Copie active, confirmée par le moteur.', 'execution_enabled':True}
        if control.get('desired')=='ACTIVE':
            if activation_timed_out(prep,activation_started):
                final_reason=(prep or {}).get('reason') or reason or 'ACTIVATION_TIMEOUT'
                return {**result,'state':'ERROR','reason':final_reason,
                        'message':'Activation non confirmée dans les 2 minutes. Cause : '+reason_message(final_reason)}
            return {**result,'state':'ACTIVATION_PENDING','reason':None,
                    'message':'Activation technique en cours (maximum 2 minutes).'}
        paused=control.get('desired')=='PAUSED'
        if paused and control.get('confirmed')!='PAUSED':
            return {**result,'state':'PAUSE_PENDING','reason':None,'message':'OFF enregistré. Les nouvelles entrées s’arrêtent automatiquement.'}
        return {**result,'state':'PAUSED' if paused else 'READY_PENDING_AUTHORIZATION','reason':None,
                'message':'Nouvelles entrées en pause. Les positions déjà copiées restent suivies automatiquement.' if paused else 'Compte connecté. Appuyez sur ON pour démarrer la copie.',
                'can_activate':not paused,'can_resume':paused}
    if reason.startswith('OBJECTIVE_'):
        stage='BLOCKED_RISK'
    elif reason in {'ACCOUNT_DISCONNECTED','MEMBERSHIP_INACTIVE','REAL_ACCOUNT_REQUIRED','ACCOUNT_IDENTITY_CHANGED','CONFIGURATION_CHANGED'}:
        stage='BLOCKED'
    elif not p.get('master_assignment'):
        stage='UNASSIGNED';reason='MASTER_CHANGED'
    elif connected:stage='CONNECTED'
    elif prep and prep['reason'] not in ('','REAL_EXECUTOR_UNAVAILABLE'):
        stage='BLOCKED';reason=prep['reason']
    else:stage='VERIFIED' # Account ownership and persisted configuration, not execution.
    # A technical renewal never overrides the ON decision made by the client
    # or administration. It remains pending until native execution evidence is
    # current; it is not an administrative block.
    waiting=control.get('desired')=='ACTIVE' and reason in {'OPERATIONAL_RELEASE_REQUIRED','CONNECTION_EVIDENCE_REQUIRED','CONNECTION_EVIDENCE_STALE_OR_CHANGED','QUALIFICATION_CHANGED'}
    if waiting:
        if activation_timed_out(prep,activation_started):
            final_reason=(prep or {}).get('reason') or reason or 'ACTIVATION_TIMEOUT'
            return {**result,'state':'ERROR','reason':final_reason,
                    'message':'Activation non confirmée dans les 2 minutes. Cause : '+reason_message(final_reason)}
        return {**result,'state':'ACTIVATION_PENDING','reason':reason,
                'message':'Activation technique en cours (maximum 2 minutes).'}
    return {**result,'state':stage,'reason':reason,
            'can_activate':reason in {'OPERATIONAL_RELEASE_REQUIRED','CONNECTION_EVIDENCE_REQUIRED','CONNECTION_EVIDENCE_STALE_OR_CHANGED'},
            'message':reason_message(reason)}


def reason_message(reason):
    if reason in MESSAGES:
        return MESSAGES[reason]
    if reason in LABELS:
        return LABELS[reason]
    if isinstance(reason,str) and reason.startswith('UNSUPPORTED_OPTION_'):
        names={'FORCER_LOT_MINIMUM':'Forcer le lot minimum','INVERSER_TRADES':'Inverser les trades',
               'CONVERTIR_PENDING_INVALIDE':'Convertir les ordres en attente invalides',
               'DRAWDOWN_ACTIF':'Limite de drawdown','DRAWDOWN_GAIN_ACTIF':'Objectif de drawdown',
               'FILTRE_NEWS':'Filtre des actualités'}
        name=names.get(reason.removeprefix('UNSUPPORTED_OPTION_'),reason.removeprefix('UNSUPPORTED_OPTION_').lower().replace('_',' '))
        return 'Option non prise en charge : '+name+'. La connexion MT5 est indépendante de ce réglage.'
    return 'Démarrage impossible ('+str(reason)+').'
