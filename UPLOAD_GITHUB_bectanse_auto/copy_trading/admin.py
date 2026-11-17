"""Admin control plane. Saving a master never connects it or starts execution."""
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import jsonify, redirect, render_template, request, session
from .service import decoded, digest
from .settings import validate, number, DEFAULTS
from .access import trading_access_state


def account_visibility(row, member):
    """Read-only presentation; membership never substitutes for execution evidence."""
    known = isinstance(member, dict) and member.get("actif") is not None
    active, manual_end = trading_access_state(member) if known else (False, None)
    row["membership"] = "ACTIVE" if active else "INACTIVE" if known else "UNKNOWN"
    row["membership_label"] = ("Accès manuel actif" if manual_end else "Abonnement actif" if member.get("billing_status") == "active" else "Essai actif" if member.get("billing_status") == "trialing" else "Accès actif (historique)") if active else "Accès inactif" if known else "À vérifier"
    if member.get('access_level') == 'admin_trading':
        row['membership_label'] = 'Compte administré' if active else 'Compte administré suspendu'
        row['admin_managed'] = True
    row["billing_status"] = member.get("billing_status") if known else None
    row["connected"] = bool(row.get("report_current") and not row.get("errors") and row.get("account_id"))
    row["copy_ready"] = bool(row["connected"] and active and row.get("requested") and row.get("decision") == "APPROVED")
    row["connection_label"] = "Connecté au service" if row["connected"] else "Incident / à vérifier" if row.get("online") else "Hors ligne" if row.get("last_seen") else "Non raccordé"
    return row


ADMIN_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS copy_admin_master (
        id INTEGER PRIMARY KEY CHECK(id=1), revision BIGINT NOT NULL,
        alias TEXT NOT NULL, login TEXT NOT NULL, server TEXT NOT NULL,
        environment TEXT NOT NULL CHECK(environment IN ('DEMO','REAL')),
        password_cipher TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS copy_admin_controls (
        member_code TEXT PRIMARY KEY REFERENCES members(code) ON DELETE CASCADE,
        version BIGINT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('APPROVED','PAUSED','REJECTED')),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS copy_admin_events (
        id BIGSERIAL PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
        actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
)


def control_allows(conn, code, version):
    rows = conn.run('SELECT version,decision FROM copy_admin_controls WHERE member_code=:code', code=code)
    # An operator binding alone does not approve a member's current settings.
    if not (rows and rows[0][0] == version and rows[0][1] == 'APPROVED'):
        return False
    from .master_assignment import selection_allows
    configs = conn.run('SELECT payload FROM copy_configurations WHERE member_code=:code AND version=:version', code=code, version=version)
    return bool(configs) and selection_allows(conn, decoded(configs[0][0]))


class AdminCopyService:
    def __init__(self, get_conn, encrypt):
        self.get_conn, self.encrypt = get_conn, encrypt

    def overview(self):
        conn = self.get_conn()
        try:
            master = conn.run('SELECT revision,alias,login,server,environment,password_cipher<>\'\' FROM copy_admin_master WHERE id=1')
            rows = conn.run("""SELECT m.code,m.nom,m.email,c.version AS configuration_version,c.status,c.payload AS configuration_payload,
                a.login_masked,a.server,r.payload AS runtime_payload,r.received_at,k.version AS reviewed_version,k.decision,jsonb_build_object('actif',to_jsonb(m)->'actif','access_level',to_jsonb(m)->'access_level','billing_status',to_jsonb(m)->'billing_status','billing_current_period_end',to_jsonb(m)->'billing_current_period_end','date_fin',to_jsonb(m)->'date_fin','admin_suspended',to_jsonb(m)->'admin_suspended','manual_access_until',to_jsonb(m)->'manual_access_until','broker_trial_id',to_jsonb(m)->'broker_trial_id','broker_trial_status',to_jsonb(m)->'broker_trial_status','broker_trial_approved_at',to_jsonb(m)->'broker_trial_approved_at','broker_trial_until',to_jsonb(m)->'broker_trial_until') AS membership_data,jsonb_build_object('balance',to_jsonb(a)->'balance','equity',to_jsonb(a)->'equity','currency',to_jsonb(a)->'currency','broker',to_jsonb(a)->'broker','platform',to_jsonb(a)->'platform','sync_status',to_jsonb(a)->'sync_status','last_successful_sync_at',to_jsonb(a)->'last_successful_sync_at') AS statistics_data
                FROM members m
                JOIN copy_configurations c ON c.member_code=m.code
                  AND c.version=(SELECT MAX(version) FROM copy_configurations WHERE member_code=m.code)
                LEFT JOIN trading_accounts a ON a.id=c.account_id AND a.user_id=m.code
                LEFT JOIN LATERAL (SELECT payload,received_at FROM copy_demo_runtime
                  WHERE member_code=m.code AND account_id=c.account_id ORDER BY received_at DESC LIMIT 1) r ON TRUE
                LEFT JOIN copy_admin_controls k ON k.member_code=m.code
                ORDER BY c.created_at DESC LIMIT 500""")
            followers = []
            for code, name, email, version, status, payload, login, server, report, received, reviewed, decision, membership_data, statistics_data in rows:
                payload = decoded(payload)
                report = decoded(report) if report else {}
                age = (datetime.now(timezone.utc) - received.replace(tzinfo=received.tzinfo or timezone.utc)).total_seconds() if received else None
                online = age is not None and 0 <= age <= 15
                followers.append(dict(code=code,name=name or code,email=email or '',version=version,
                    validation=status,requested=payload.get('requested_enabled') is True,
                    account=login or 'Non disponible',server=server,decision=decision if reviewed==version else 'PENDING',
                    online=online,last_seen=received.isoformat() if received else None,
                    report_current=online and report.get('version')==version,
                    open_positions=report.get('open_positions') if online else None,
                    errors=report.get('errors',[]) if online else [],positions=report.get('positions',[]) if online else [],
                    account_id=payload.get('account_id'),settings=payload.get('settings',{}),master_assignment=payload.get('master_assignment')))
                account_visibility(followers[-1], decoded(membership_data))
                from .admin_statistics import financials
                followers[-1]['statistics']=financials(decoded(statistics_data))
            legacy = conn.run("""SELECT code,nom,email,params,historique,jsonb_build_object('actif',to_jsonb(m)->'actif','access_level',to_jsonb(m)->'access_level','billing_status',to_jsonb(m)->'billing_status','billing_current_period_end',to_jsonb(m)->'billing_current_period_end','date_fin',to_jsonb(m)->'date_fin','admin_suspended',to_jsonb(m)->'admin_suspended','manual_access_until',to_jsonb(m)->'manual_access_until','broker_trial_id',to_jsonb(m)->'broker_trial_id','broker_trial_status',to_jsonb(m)->'broker_trial_status','broker_trial_approved_at',to_jsonb(m)->'broker_trial_approved_at','broker_trial_until',to_jsonb(m)->'broker_trial_until') FROM members m
                WHERE NOT EXISTS(SELECT 1 FROM copy_configurations c WHERE c.member_code=m.code)
                AND historique IS NOT NULL AND historique NOT IN ('','[]') ORDER BY code LIMIT 500""")
            for code,name,email,params,history,membership_data in legacy:
                if len(followers)>=500: break
                try:
                    history=decoded(history); params=decoded(params) if params else {}
                    pending=isinstance(history,list) and any(isinstance(h,dict) and h.get('statut')=='en_attente' for h in history)
                except (ValueError,TypeError):
                    continue
                if not pending: continue
                login=str(params.get('mt_login',''))
                followers.append(dict(code=code,name=name or code,email=email or '',version=None,
                    validation='LEGACY_REQUEST',requested=True,account='***'+login[-4:] if login else 'À renseigner',
                    server=str(params.get('mt_server','')),decision='PENDING',online=False,last_seen=None,
                    report_current=False,open_positions=None,errors=[],positions=[],
                    settings={k:params[k] for k in ('lots','mode_risque','lot_symboles') if k in params}))
                account_visibility(followers[-1], decoded(membership_data))
            from .admin_statistics import add_history
            add_history(conn, followers)
            preparations=conn.run('SELECT member_code,state,reason,checked_at FROM copy_preparation_jobs')
            from .onboarding_server import LABELS
            by_member={code:dict(state=state,reason=reason,message=LABELS.get(reason,''),checked_at=checked.isoformat() if checked else None) for code,state,reason,checked in preparations}
            from .real_preparation import public_observation
            real_cache={(code,account,version):payload for code,account,version,payload in
                        conn.run('SELECT member_code,account_id,version,payload FROM copy_real_observations')}
            from .client_status import status as client_status
            for follower in followers:
                follower['onboarding']=client_status(conn,follower['code'])
                follower['preparation']=by_member.get(follower['code'])
                real=public_observation(conn,follower['code'],follower.get('account_id'),follower.get('version'),cached=real_cache)
                follower['real_connection']=real
                if real:
                    from .dashboard_telemetry import project
                    project(follower,real)
            events = conn.run('SELECT action,target,actor,created_at FROM copy_admin_events ORDER BY id DESC LIMIT 50')
            from .dashboard_telemetry import master_view
            master_row=master_view(conn,dict(zip(('revision','alias','login','server','environment','has_password'),master[0])) if master else None)
            from .dashboard_metrics import apply_snapshots
            apply_snapshots(conn,master_row,followers)
            return dict(master=master_row,settings_defaults=DEFAULTS,
                        followers=followers,events=[dict(action=a,target=t,actor=u,at=d.isoformat()) for a,t,u,d in events],
                        execution_mode='DEMO_ONLY',limit=500)
        finally:
            conn.close()

    def save_master(self, data, actor):
        if not isinstance(data,dict): raise ValueError('Formulaire invalide.')
        alias,login,server = (str(data.get(k,'')).strip() for k in ('alias','login','server'))
        environment,password,revision = data.get('environment'),data.get('password',''),data.get('revision',0)
        if (not 1<=len(alias)<=80 or not re.fullmatch(r'[0-9]{3,20}',login) or not 1<=len(server)<=120
                or any(ord(c)<32 for c in alias+server) or environment not in ('DEMO','REAL')
                or not isinstance(password,str) or len(password)>256 or password.startswith('enc:v1:')
                or type(revision) is not int or revision<0):
            raise ValueError('Vérifie le nom, le login, le serveur et le type de compte.')
        conn=self.get_conn()
        try:
            conn.run('BEGIN')
            conn.run('LOCK TABLE copy_admin_master IN EXCLUSIVE MODE')
            rows=conn.run('SELECT revision,login,server,password_cipher FROM copy_admin_master WHERE id=1')
            if revision != (rows[0][0] if rows else 0): raise ValueError('Configuration modifiée ailleurs. Actualise la page.')
            if rows and (login,server)!=(rows[0][1],rows[0][2]) and not password:
                raise ValueError('Un changement de compte nécessite son mot de passe.')
            cipher=self.encrypt(password) if password else (rows[0][3] if rows else '')
            if not cipher: raise ValueError('Renseigne le mot de passe du compte maître.')
            conn.run("""INSERT INTO copy_admin_master(id,revision,alias,login,server,environment,password_cipher)
                VALUES(1,:revision,:alias,:login,:server,:environment,:cipher) ON CONFLICT(id) DO UPDATE SET
                revision=EXCLUDED.revision,alias=EXCLUDED.alias,login=EXCLUDED.login,server=EXCLUDED.server,
                environment=EXCLUDED.environment,password_cipher=EXCLUDED.password_cipher,updated_at=NOW()""",
                revision=revision+1,alias=alias,login=login,server=server,environment=environment,cipher=cipher)
            self.event(conn,'MASTER_SAVED','master',actor)
            conn.run('COMMIT')
        except Exception:
            conn.run('ROLLBACK'); raise
        finally: conn.close()

    @staticmethod
    def event(conn,action,target,actor):
        conn.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:action,:target,:actor)',
                 action=action,target=target,actor=actor)

    def request_safe_recovery(self, actor):
        """Wake only stalled evidence collection; never grant trading authority."""
        conn = self.get_conn()
        try:
            conn.run('BEGIN')
            rows = conn.run("""SELECT j.member_code,j.account_id,j.version
                FROM copy_preparation_jobs j
                JOIN copy_configurations c ON c.member_code=j.member_code
                  AND c.account_id=j.account_id AND c.version=j.version
                WHERE j.state<>'UNASSIGNED'
                  AND (c.payload->'master_assignment') IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM copy_real_objective_latches l
                                  WHERE l.member_code=j.member_code)
                FOR UPDATE""")
            members = []
            for code, account, version in rows:
                # Never steal a live terminal lease. This changes no desired
                # ON/OFF state, risk latch, settings, credentials or position.
                updated = conn.run("""UPDATE copy_preparation_jobs
                    SET state='PREPARING',reason='',due_at=NOW(),lease_token=NULL,
                        lease_until=NULL,updated_at=NOW()
                    WHERE member_code=:code AND account_id=:account AND version=:version
                      AND (lease_until IS NULL OR lease_until<NOW())
                    RETURNING member_code""", code=code, account=account, version=version)
                if updated:
                    members.append(code)
                    self.event(conn, 'SAFE_RECOVERY_QUEUED', code, actor)
            conn.run('COMMIT')
            return {'queued': len(members), 'members': members}
        except Exception:
            conn.run('ROLLBACK')
            raise
        finally:
            conn.close()

    def save_lots(self, data, actor):
        # Preserve the existing quick-lot API for older clients.
        lots = number(data.get('lots') if isinstance(data,dict) else None, 'Lots', 0.01, 1)
        return self._save_settings(data,actor,{'mode_risque':'Lots fixes','lots':lots,'lots_max':lots},'LOTS_UPDATED')

    def save_settings(self, data, actor):
        if not isinstance(data,dict) or not isinstance(data.get('settings'),dict) or set(data['settings'])!=set(DEFAULTS):
            raise ValueError('Tous les réglages du compte sont requis. Actualisez la fiche.')
        return self._save_settings(data,actor,validate(data['settings']),'SETTINGS_UPDATED')

    def _save_settings(self, data, actor, changes, event):
        if not isinstance(data, dict) or type(data.get('version')) is not int or type(data.get('account_id')) is not int:
            raise ValueError('Compte et version requis.')
        code = data.get('code')
        if not isinstance(code, str) or not 1 <= len(code) <= 100:
            raise ValueError('Membre invalide.')
        conn = self.get_conn()
        try:
            conn.run('BEGIN')
            members = conn.run('SELECT params FROM members WHERE code=:code FOR UPDATE', code=code)
            if not members:
                raise ValueError('Membre introuvable.')
            rows = conn.run('SELECT version,account_id,payload,status FROM copy_configurations WHERE member_code=:code ORDER BY version DESC LIMIT 1', code=code)
            if not rows or rows[0][0] != data['version'] or rows[0][1] != data['account_id']:
                raise ValueError('Le compte ou ses paramètres ont changé. Actualise la page.')
            version, account, raw, status = rows[0]
            payload = dict(decoded(raw))
            accounts = conn.run("SELECT login,server FROM trading_accounts WHERE id=:account AND user_id=:code AND provider='MT5' AND platform='MT5' FOR SHARE", account=account, code=code)
            if not accounts or tuple(accounts[0]) != (payload['login'], payload['server']):
                raise ValueError('Identité du compte modifiée. Vérifie sa connexion.')
            settings = validate({**payload['settings'], **changes})
            lots = settings['lots']
            if settings == payload.get('settings'):
                conn.run('COMMIT')
                return {'version':version,'lots':lots,'account_id':account,'settings':settings,'unchanged':True}
            payload['settings'] = settings
            new_status = 'VALIDATED_SIMULATION' if status == 'VALIDATED_SIMULATION' else 'PENDING'
            conn.run("UPDATE copy_configurations SET status='SUPERSEDED',lease_token=NULL,lease_until=NULL WHERE member_code=:code AND status='PENDING'", code=code)
            conn.run("""INSERT INTO copy_configurations(member_code,version,account_id,payload,digest,status,confirmed_at)
                VALUES(:code,:version,:account,CAST(:payload AS JSONB),:digest,:status,
                CASE WHEN :status='VALIDATED_SIMULATION' THEN NOW() ELSE NULL END)""",
                code=code, version=version+1, account=account, payload=json.dumps(payload,allow_nan=False), digest=digest(payload), status=new_status)
            # Carry only a decision already attached to the exact previous version.
            conn.run('UPDATE copy_admin_controls SET version=:new,updated_at=NOW() WHERE member_code=:code AND version=:old', new=version+1, code=code, old=version)
            params = dict(decoded(members[0][0]) or {})
            params.update(settings)
            conn.run('UPDATE members SET params=:params WHERE code=:code', params=json.dumps(params), code=code)
            if 'master_assignment' in payload:
                from .onboarding_server import enqueue
                enqueue(conn,code,version+1,account,payload['master_assignment'] is not None)
            self.event(conn,event,code,actor)
            conn.run('COMMIT')
            return {'version':version+1,'lots':lots,'account_id':account,'settings':settings}
        except Exception:
            conn.run('ROLLBACK')
            raise
        finally:
            conn.close()

    def review(self, data, actor):
        if not isinstance(data,dict) or data.get('decision') not in ('APPROVED','PAUSED','REJECTED') or type(data.get('version')) is not int:
            raise ValueError('Décision invalide.')
        code=data.get('code')
        if not isinstance(code,str) or not 1<=len(code)<=100: raise ValueError('Membre invalide.')
        conn=self.get_conn()
        try:
            conn.run('BEGIN')
            conn.run('SELECT code FROM members WHERE code=:code FOR UPDATE',code=code)
            rows=conn.run('SELECT MAX(version) FROM copy_configurations WHERE member_code=:code',code=code)
            if not rows or rows[0][0]!=data['version']: raise ValueError('La demande a changé. Actualise avant de décider.')
            if data['decision']=='APPROVED':
                from .master_assignment import member_record, selection_allows
                configs=conn.run('SELECT payload FROM copy_configurations WHERE member_code=:code AND version=:version',code=code,version=data['version'])
                payload=decoded(configs[0][0]) if configs else {}
                if 'master_assignment' in payload and (not trading_access_state(member_record(conn,code))[0] or not selection_allows(conn,payload)):
                    raise ValueError('Un accès actif et un maître valide sont requis avant approbation.')
            conn.run("""INSERT INTO copy_admin_controls(member_code,version,decision) VALUES(:code,:version,:decision)
                ON CONFLICT(member_code) DO UPDATE SET version=EXCLUDED.version,decision=EXCLUDED.decision,updated_at=NOW()""",
                code=code,version=data['version'],decision=data['decision'])
            if data['decision'] in ('PAUSED','REJECTED'):
                # Revoke execution in this same member-locked transaction.
                # Approval alone must never restore the member's consent.
                conn.run("""UPDATE copy_real_controls SET desired=:desired,
                    revision=revision+1,confirmed='REQUEST_RECEIVED',confirmed_at=NULL
                    WHERE member_code=:code AND desired<>:desired""",
                    desired='PAUSED' if data['decision']=='PAUSED' else 'STOPPED',code=code)
            self.event(conn,data['decision'],code,actor)
            conn.run('COMMIT')
        except Exception:
            conn.run('ROLLBACK');raise
        finally: conn.close()


def register_admin_copy(app, service, session_valid):
    def secure(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not session_valid():
                if request.path=='/admin/copy-trading' and request.method=='GET':
                    return redirect('/admin-panel?next=/admin/copy-trading')
                return jsonify(ok=False,error='Connexion administrateur requise.'),403
            if request.method!='GET' and not hmac.compare_digest(session.get('copy_admin_csrf',''),request.headers.get('X-CSRF-Token','')):
                return jsonify(ok=False,error='Session expirée. Recharge la page.'),403
            if request.method!='GET' and not session.get('copy_admin_csrf'): return jsonify(ok=False),403
            try:
                response=app.make_response(fn(*args,**kwargs));response.headers['Cache-Control']='no-store';return response
            except ValueError as e: return jsonify(ok=False,error=str(e)),409
            except Exception:
                app.logger.error('Copy admin operation failed')
                return jsonify(ok=False,error='Service indisponible. Aucune confirmation enregistrée.'),503
        return wrapped

    @app.get('/admin/copy-trading')
    @secure
    def admin_copy_panel():
        session.setdefault('copy_admin_csrf',secrets.token_urlsafe(32))
        return render_template('admin_copy.html',csrf=session['copy_admin_csrf'])

    @app.get('/admin/api/copy-trading')
    @secure
    def admin_copy_overview(): return jsonify(ok=True,**service.overview())

    @app.get('/admin/api/copy-trading/journal')
    @secure
    def admin_client_journal():
        from .admin_journal import journal, native_fallback
        conn=service.get_conn()
        try:
            args=(conn,request.args.get('member',''),request.args.get('account','0'),request.args.get('month',''),request.args.get('page','0'),request.args.get('day'))
            result=journal(*args)
            return jsonify(ok=True,**native_fallback(*args,result))
        finally:conn.close()

    @app.post('/admin/api/copy-trading/master')
    @secure
    def admin_copy_master():
        service.save_master(request.get_json(silent=True),'admin-session')
        return jsonify(ok=True,message='Configuration enregistrée. Connexion MT5 non vérifiée ; aucune copie démarrée.')

    @app.post('/admin/api/copy-trading/review')
    @secure
    def admin_copy_review():
        service.review(request.get_json(silent=True),'admin-session')
        return jsonify(ok=True,message='Décision enregistrée. La connexion et l’exécution restent contrôlées séparément.')

    @app.post('/admin/api/copy-trading/safe-recovery')
    @secure
    def admin_copy_safe_recovery():
        result = service.request_safe_recovery('admin-session')
        return jsonify(ok=True, **result,
            message='Vérification MT5 relancée pour les comptes éligibles. Aucun ordre, consentement, réglage ou protection n’a été modifié.')

    @app.post('/admin/api/copy-trading/lots')
    @secure
    def admin_copy_lots():
        result=service.save_lots(request.get_json(silent=True),'admin-session')
        return jsonify(ok=True,**result)

    @app.post('/admin/api/copy-trading/settings')
    @secure
    def admin_copy_settings():
        result=service.save_settings(request.get_json(silent=True),'admin-session')
        return jsonify(ok=True,**result)

    @app.get('/admin/api/copy-trading/operational')
    @secure
    def admin_copy_operational_status():
        from .operational_release import status
        conn=service.get_conn()
        try:
            from .preflight_transport import candidate
            code=request.args.get('code','')
            return jsonify(ok=True,**status(conn,code),candidate=candidate(conn,code))
        finally: conn.close()

    @app.post('/admin/api/copy-trading/operational')
    @secure
    def admin_copy_operational_decision():
        from .operational_release import decide
        conn=service.get_conn()
        try: return jsonify(**decide(conn,request.get_json(silent=True),'admin-session'))
        finally: conn.close()

    @app.get('/admin/api/copy-trading/members')
    @secure
    def admin_copy_members():
        query=request.args.get('q','').strip()
        if not 2 <= len(query) <= 100: return jsonify(ok=True,members=[])
        conn=service.get_conn()
        try:
            rows=conn.run("SELECT code,nom,email,to_jsonb(m) FROM members m WHERE POSITION(LOWER(:q) IN LOWER(COALESCE(nom,'') || ' ' || COALESCE(email,'') || ' ' || code))>0 ORDER BY code LIMIT 30",q=query)
            return jsonify(ok=True,members=[dict(code=c,name=n or c,email=e or '',active=trading_access_state(decoded(m))[0]) for c,n,e,m in rows])
        finally:conn.close()

    def assisted_journal():
        from trading_journal.service import JournalService
        from .master_assignment import member_record
        def member(code):
            conn=service.get_conn()
            try: return member_record(conn,code)
            finally: conn.close()
        return JournalService(service.get_conn,member,app.logger)

    @app.get('/admin/api/copy-trading/brokers')
    @secure
    def admin_copy_brokers():
        return jsonify(ok=True,brokers=assisted_journal().broker_catalog())

    @app.post('/admin/api/copy-trading/manual-account')
    @secure
    def admin_manual_trading_account():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('name'), str):
            raise ValueError('Nom du dossier requis.')
        account = assisted_journal().create_account('', data, admin_actor='admin-session', managed_name=data['name'])
        return jsonify(ok=True, code=account['managed_code'], account_id=account['id'],
            message='Compte enregistré. Copie désactivée ; vérification MT5 en attente.')

    @app.post('/admin/api/copy-trading/account')
    @secure
    def admin_copy_account():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('code'),str) or not 1<=len(data['code'])<=100:
            raise ValueError('Membre requis.')
        try:
            account=assisted_journal().create_account(data['code'],data,admin_actor='admin-session')
        except PermissionError as error:
            return jsonify(ok=False,error=str(error)),403
        return jsonify(ok=True,account_id=account['id'],message='Compte enregistré. Vérification MT5 en attente.')

    @app.post('/admin/api/copy-trading/activation')
    @secure
    def admin_copy_activation():
        from .real_control import command
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or not isinstance(data.get('code'),str) or not 1<=len(data['code'])<=100:
            raise ValueError('Membre requis.')
        code=data.pop('code')
        conn=service.get_conn()
        try:
            return jsonify(command(conn,code,data,admin_actor='admin-session'))
        except ValueError as error:
            from .client_status import reason_message
            return jsonify(ok=False,error=str(error),message=reason_message(str(error))),409
        finally: conn.close()

    @app.get('/admin/api/copy-trading/assignment')
    @secure
    def admin_copy_assignment_options():
        from .master_assignment import options
        return jsonify(ok=True,**options(service,request.args.get('code','')))

    @app.post('/admin/api/copy-trading/assignment')
    @secure
    def admin_copy_assignment_save():
        from .master_assignment import assign
        result=assign(service,request.get_json(silent=True),'admin-session')
        return jsonify(ok=True,**result,message='Rattachement enregistré. La copie reste en pause jusqu’à sa vérification et son autorisation.')
