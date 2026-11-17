"""Private bearer links reveal presentation media only; verified BCT starts the review workflow."""
import hashlib
import json
import re
import secrets
import uuid
from urllib.parse import quote
from flask import abort, jsonify, redirect, render_template, request, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from broker_trial import TrialError, row_dict, transaction, eligibility


def register_discovery(app,service,get_conn,get_member,admin_required,require_write,csrf_token,actor,enabled,send_verification):
    signer=URLSafeTimedSerializer(app.secret_key,salt='private-discovery-v1')
    def url(link):
        return service.origin+'/accesgratuit'+('' if link.get('is_campaign') else '/'+link['short_code'])
    def access():
        if not enabled() or not session.get('broker_discovery_id'):
            return None
        c=get_conn()
        try:
            return row_dict(c,"SELECT * FROM broker_discovery_links WHERE id=:id AND revoked_at IS NULL AND (is_campaign OR expires_at>NOW())",id=session['broker_discovery_id'])
        finally:c.close()
    def required():
        link=access()
        if not link:abort(403)
        return link
    def secure(response):
        response=app.make_response(response)
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; media-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        # Keep same-origin form POSTs identifiable while hiding private links from external sites.
        # no-referrer makes browsers send Origin: null, which the mutation guard rejects.
        response.headers['Referrer-Policy']='same-origin'
        return response
    def form_response(error='',notice='',status=200):
        member=get_member(session['member_code']) if session.get('member_code') else None
        return secure((render_template('broker_discovery_start.html',csrf=csrf_token(),member=member,error=error,notice=notice),status))
    def claim(code):
        with transaction(get_conn) as c:
            link=row_dict(c,"SELECT * FROM broker_discovery_links WHERE id=:id AND revoked_at IS NULL AND (is_campaign OR expires_at>NOW()) FOR UPDATE",id=session.get('broker_discovery_id',''))
            if not link:raise TrialError('Ce lien privé a expiré ou a été désactivé',403)
            if not link.get('is_campaign') and link['claimed_member'] and link['claimed_member']!=code:
                raise TrialError('Ce lien est déjà associé à un compte. Demande un nouveau lien à l’équipe.',409)
            member=service._member(c,code)
            trial=service._trial(c,code)
            if not member or member.get('admin_suspended') or not member.get('email_verified_at'):
                raise TrialError('Confirme ton adresse e-mail avant de démarrer le parcours',403)
            if not trial and not eligibility(member):
                raise TrialError('Cette offre est réservée aux nouveaux comptes Explorer éligibles',403)
            trial=service._invite_in_connection(c,code,'discovery:'+link['id'])
            if not link.get('is_campaign'):
                c.run('UPDATE broker_discovery_links SET claimed_member=:code,claimed_at=COALESCE(claimed_at,NOW()) WHERE id=:id',code=code,id=link['id'])
            return trial

    @app.get('/accesgratuit/<short_code>')
    def broker_discovery_short(short_code):
        if not enabled() or not re.fullmatch(r'[A-Za-z0-9_-]{16}',short_code):abort(404)
        c=get_conn()
        try:
            link=row_dict(c,'SELECT * FROM broker_discovery_links WHERE short_code=:code AND revoked_at IS NULL AND (is_campaign OR expires_at>NOW())',code=short_code)
        finally:c.close()
        if not link:abort(404)
        session['broker_discovery_id']=link['id']
        return secure(render_template('broker_trial.html',csrf=csrf_token(),discovery_mode=True,discovery_url=request.path))

    @app.get('/accesgratuit')
    def broker_discovery_current():
        if not enabled():abort(404)
        c=get_conn()
        try:link=row_dict(c,"SELECT * FROM broker_discovery_links WHERE id='campaign-accesgratuit' AND is_campaign AND revoked_at IS NULL")
        finally:c.close()
        if not link:abort(404)
        session['broker_discovery_id']=link['id']
        return secure(render_template('broker_trial.html',csrf=csrf_token(),discovery_mode=True,discovery_url='/accesgratuit'))

    @app.get('/decouverte/<token>')
    def broker_discovery_page(token):
        try:payload=signer.loads(token,max_age=30*24*3600)
        except (BadSignature,SignatureExpired):abort(404)
        if not isinstance(payload,dict) or set(payload)!={'link_id'}:abort(404)
        old=session.get('broker_discovery_id')
        session['broker_discovery_id']=payload['link_id']
        if not access():
            if old:session['broker_discovery_id']=old
            else:session.pop('broker_discovery_id',None)
            abort(404)
        return secure(render_template('broker_trial.html',csrf=csrf_token(),discovery_mode=True,discovery_url=request.path))

    @app.route('/decouverte/commencer',methods=['GET','POST'])
    def broker_discovery_start():
        link=required()
        if request.method=='GET':return form_response()
        if not secrets.compare_digest(str(session.get('broker_trial_csrf','')),str(request.form.get('csrf',''))) or not session.get('broker_trial_csrf'):
            abort(403)
        if request.content_length and request.content_length>8192:abort(413)
        if request.form.get('action')=='claim':
            if not session.get('member_code'):abort(403)
            try:t=claim(session['member_code'])
            except TrialError as exc:return form_response(error=str(exc),status=exc.status)
            session['broker_trial_invitation_id']=t['id']
            return redirect('/offre-decouverte#guide')
        if session.get('member_code'):
            return form_response(error='Utilise ton compte BCT connecté pour poursuivre',status=400)
        name=request.form.get('prenom','').strip();email=request.form.get('email','').strip().lower()
        if not name or len(name)>80 or any(ord(x)<32 for x in name) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:
            return form_response(error='Indique ton prénom et une adresse e-mail valide',status=400)
        if request.form.get('consent')!='yes':
            return form_response(error='Confirme l’envoi des messages nécessaires à ton accès',status=400)
        remote=request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[0].strip()
        ip_hash=hashlib.sha256(remote.encode()).hexdigest()
        raw=secrets.token_urlsafe(32);token_hash=hashlib.sha256(raw.encode()).hexdigest();email_hash=hashlib.sha256(email.encode()).hexdigest()
        try:
            with transaction(get_conn) as c:
                current=row_dict(c,'SELECT * FROM broker_discovery_links WHERE id=:id AND revoked_at IS NULL AND (is_campaign OR expires_at>NOW()) FOR UPDATE',id=link['id'])
                if not current:raise TrialError('Ce lien privé est indisponible',403)
                if not current.get('is_campaign') and current['claimed_member']:raise TrialError('Ce lien est déjà associé à un compte BCT. Connecte-toi avec ce compte ou demande un nouveau lien.',409)
                email_attempts=c.run("SELECT COUNT(*) FROM broker_discovery_mail_attempts WHERE created_at>NOW()-INTERVAL '1 hour' AND email_hash=:email",email=email_hash)[0][0]
                if current.get('is_campaign'):
                    attempts=c.run("SELECT COUNT(*) FROM broker_discovery_mail_attempts WHERE created_at>NOW()-INTERVAL '1 hour' AND link_id=:id AND ip_hash=:ip",id=link['id'],ip=ip_hash)[0][0]
                    limit=20
                else:
                    attempts=c.run("SELECT COUNT(*) FROM broker_discovery_mail_attempts WHERE created_at>NOW()-INTERVAL '1 hour' AND link_id=:id",id=link['id'])[0][0]
                    limit=5
                if email_attempts>=5 or attempts>=limit:raise TrialError('Trop de demandes rapprochées. Vérifie tes e-mails ou réessaie dans une heure.',429)
                c.run('INSERT INTO broker_discovery_mail_attempts(link_id,email_hash,ip_hash) VALUES(:id,:email,:ip)',id=link['id'],email=email_hash,ip=ip_hash)
                c.run("""INSERT INTO prospect_email_verifications(email,prenom,token_hash,source,status,payload,account_code,created_at,expires_at,verified_at)
                    VALUES(:email,:name,:token,'explorer','pending',:payload,'',NOW(),NOW()+INTERVAL '24 hours',NULL)
                    ON CONFLICT(email) DO UPDATE SET prenom=:name,token_hash=:token,source='explorer',status='pending',payload=:payload,account_code='',created_at=NOW(),expires_at=NOW()+INTERVAL '24 hours',verified_at=NULL""",
                    email=email,name=name,token=token_hash,payload=json.dumps({'broker_discovery_id':link['id']}))
            result=send_verification(email,name,service.origin+'/explorer/confirmer/'+quote(raw)) if send_verification else None
            if not isinstance(result,dict) or result.get('ok') is not True:
                return form_response(error='L’e-mail n’a pas pu partir. Réessaie dans quelques instants.',status=503)
        except TrialError as exc:return form_response(error=str(exc),status=exc.status)
        return form_response(notice='Confirme ton e-mail pour continuer. Ouvre le message reçu, puis clique sur le lien de confirmation. Ton compte restera Explorer jusqu’aux validations de l’équipe.')

    @app.get('/admin/api/essais-broker/liens-decouverte')
    @admin_required
    def broker_discovery_list():
        if not session.get('admin_authenticated'):abort(403)
        c=get_conn()
        try:
            rows=c.run('SELECT * FROM broker_discovery_links ORDER BY is_campaign DESC,created_at DESC LIMIT 100')
            keys=[x['name'] for x in c.columns];links=[dict(zip(keys,r)) for r in rows]
        finally:c.close()
        for link in links:link['url']=url(link)
        return jsonify(ok=True,links=links)

    @app.post('/admin/api/essais-broker/liens-decouverte')
    @admin_required
    def broker_discovery_create():
        try:
            data=require_write(admin=True)
            if set(data)!={'label'} or not isinstance(data['label'],str):raise TrialError('Indique un repère pour ce lien',400)
            label=data['label'].strip()
            if not label or len(label)>120 or any(ord(x)<32 for x in label):raise TrialError('Indique un repère de 1 à 120 caractères',400)
            with transaction(get_conn) as c:
                link=row_dict(c,'INSERT INTO broker_discovery_links(id,label,created_by,short_code) VALUES(:id,:label,:actor,:short) RETURNING *',id=str(uuid.uuid4()),label=label,actor=actor(),short=secrets.token_urlsafe(12))
            link['url']=url(link)
            return jsonify(ok=True,link=link)
        except TrialError as exc:return jsonify(ok=False,error=str(exc)),exc.status

    @app.post('/admin/api/essais-broker/liens-decouverte/<link_id>/revoquer')
    @admin_required
    def broker_discovery_revoke(link_id):
        try:
            data=require_write(admin=True)
            if data:raise TrialError('Requête invalide',400)
            with transaction(get_conn) as c:
                found=c.run('UPDATE broker_discovery_links SET revoked_at=COALESCE(revoked_at,NOW()),revoked_by=COALESCE(revoked_by,:actor) WHERE id=:id RETURNING id',id=link_id,actor=actor())
                if not found:raise TrialError('Lien introuvable',404)
            return jsonify(ok=True)
        except TrialError as exc:return jsonify(ok=False,error=str(exc)),exc.status

    return {'access':access,'url':url,'claim':claim}
