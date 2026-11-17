"""Server-observed member events. No inference of reading or legal consent."""
import hashlib
import ipaddress
import json
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from flask import jsonify, redirect, request, session

DDL = (
"""CREATE TABLE IF NOT EXISTS member_evidence (
 id BIGSERIAL PRIMARY KEY, member_code TEXT NOT NULL, kind TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), source TEXT NOT NULL,
 details JSONB NOT NULL DEFAULT '{}', network_encrypted TEXT NOT NULL DEFAULT '',
 email_encrypted TEXT NOT NULL DEFAULT '', expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '365 days')""",
"CREATE INDEX IF NOT EXISTS member_evidence_profile_idx ON member_evidence(member_code,id DESC)",
"CREATE INDEX IF NOT EXISTS member_evidence_expiry_idx ON member_evidence(expires_at)",
"""CREATE OR REPLACE FUNCTION evidence_member_created() RETURNS trigger AS $$
 BEGIN
 INSERT INTO member_evidence(member_code,kind,source,details)
 VALUES(NEW.code,'code_created','database',jsonb_build_object('meaning','Code attribué au compte ; réception non attestée'));
 RETURN NEW;
 END; $$ LANGUAGE plpgsql""",
"DROP TRIGGER IF EXISTS evidence_member_created_trigger ON members",
"""CREATE TRIGGER evidence_member_created_trigger AFTER INSERT ON members
 FOR EACH ROW EXECUTE FUNCTION evidence_member_created()""",
)


def ensure_evidence_schema(conn):
    conn.run('BEGIN')
    try:
        for sql in DDL:
            conn.run(sql)
        conn.run('COMMIT')
    except Exception:
        conn.run('ROLLBACK')
        raise


def clean_ip(value):
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return ''


def network_context():
    # Forwarded headers remain explicitly unverified; never confuse a proxy or
    # a payment-provider request with the member's address.
    from flask import has_request_context
    if not has_request_context():
        return {}
    return {'peer_ip': clean_ip(request.remote_addr),
            'forwarded_ips_unverified': [clean_ip(x) for x in request.headers.get('X-Forwarded-For','')[:512].split(',') if clean_ip(x)][:8],
            'user_agent_declared': request.headers.get('User-Agent','')[:300]}


LABELS = {
 'checkout_started': 'Paiement ouvert (non confirmé)',
 'code_created': 'Code BCT attribué', 'login_success': 'Connexion réussie',
 'member_page': 'Page membre servie', 'resource_redirect': 'Lien de formation ouvert',
 'pdf_response': 'Réponse PDF servie', 'lesson_declared': 'Progression déclarée par le membre',
 'email_accepted': 'E-mail accepté par le prestataire', 'email_failed': 'Envoi e-mail non confirmé',
 'admin_view': 'Dossier consulté par un administrateur', 'admin_export': 'Dossier exporté par un administrateur',
}


def register_evidence(app, get_conn, get_member, encrypt, decrypt, login_required,
                      academy_access_required, admin_valid, curriculum):
    cleanup_lock = threading.Lock()
    last_cleanup = [0.0]

    def record(code, kind, details=None, *, email='', network=True):
        if not code or code == 'BCT-DEMO2026':
            return False
        conn = None
        try:
            conn = get_conn()
            if time.monotonic()-last_cleanup[0] > 3600 and cleanup_lock.acquire(False):
                try:
                    conn.run('DELETE FROM member_evidence WHERE expires_at < NOW()')
                    last_cleanup[0] = time.monotonic()
                finally:
                    cleanup_lock.release()
            if not email and kind not in {'admin_view','admin_export'}:
                rows = conn.run('SELECT email FROM members WHERE code=:code',code=code)
                email = rows[0][0] if rows else ''
            conn.run("""INSERT INTO member_evidence(member_code,kind,source,details,network_encrypted,email_encrypted)
                VALUES(:code,:kind,'application',CAST(:details AS JSONB),:network,:email)""",
                code=code, kind=kind, details=json.dumps(details or {},ensure_ascii=False),
                network=encrypt(json.dumps(network_context())) if network else '', email=encrypt(email) if email else '')
            return True
        except Exception as exc:
            # No personal data or credentials in logs, and no false successful audit.
            app.logger.error('MEMBER_EVIDENCE_WRITE_FAILED %s %s',kind,type(exc).__name__)
            return False
        finally:
            if conn:
                conn.close()

    @app.after_request
    def evidence_response(response):
        code = session.get('member_code') if request.endpoint=='academy_subscription_checkout' or request.path in {'/','/accueil','/academie','/dashboard','/guide-robot/telecharger','/api/academy/lesson'} or request.endpoint in {'confirm_explorer_email','confirm_member_registration'} else None
        if not code:
            return response
        if request.endpoint=='academy_subscription_checkout' and response.status_code in {302,303} and response.location and response.location.startswith('https://checkout.stripe.com/'):
            details={'plan':str((request.view_args or {}).get('plan_id',''))[:80],'meaning':'Redirection vers Stripe, distincte de la confirmation du paiement'}
            conn=None
            try:
                conn=get_conn()
                if conn.run("SELECT to_regclass('billing_checkouts')")[0][0]:
                    rows=conn.run("SELECT session_id FROM billing_checkouts WHERE member_code=:code AND product='ACADEMY'",code=code)
                elif conn.run("SELECT to_regclass('marketing_checkout_intents')")[0][0]:
                    rows=conn.run("SELECT stripe_session_id FROM marketing_checkout_intents WHERE member_code=:code ORDER BY id DESC LIMIT 1",code=code)
                else: rows=[]
                if rows: details['stripe_session_id']=rows[0][0]
            except Exception:
                app.logger.error('MEMBER_EVIDENCE_CHECKOUT_REFERENCE_UNAVAILABLE')
            finally:
                if conn: conn.close()
            record(code,'checkout_started',details)
        elif request.endpoint in {'login','confirm_explorer_email','confirm_member_registration'} and response.status_code in {302,303} and (request.method=='POST' or request.endpoint in {'confirm_explorer_email','confirm_member_registration'}):
            member = get_member(code) or {}
            record(code,'login_success',{'method': 'code' if request.endpoint=='login' else 'email_confirmation'},email=member.get('email',''))
        elif request.method == 'GET' and response.status_code == 200 and request.path in {'/accueil','/academie','/dashboard'}:
            record(code,'member_page',{'path':request.path})
        elif request.method == 'GET' and response.status_code in {200,206} and request.path=='/guide-robot/telecharger':
            path = app.static_folder+'/guides/bectanse-auto-guide-membre.pdf'
            try:
                with open(path,'rb') as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
            except OSError:
                digest=''
            record(code,'pdf_response',{'document':'Guide-Bectanse-AUTO.pdf','sha256':digest,'http_status':response.status_code,'meaning':'Réponse du serveur ; téléchargement complet et lecture non attestés'})
        elif request.method=='POST' and response.status_code==200 and request.path=='/api/academy/lesson':
            data=request.get_json(silent=True) or {}
            if (response.get_json(silent=True) or {}).get('ok'):
                record(code,'lesson_declared',{'lesson_key':str(data.get('lesson_key',''))[:100],'status':str(data.get('status',''))[:30]})
        return response

    lessons = {lesson['key']:lesson for phase in curriculum for lesson in phase['lessons']}

    @app.route('/academie/ressource/<lesson_key>')
    @login_required
    @academy_access_required
    def evidence_resource(lesson_key):
        lesson=lessons.get(lesson_key)
        if not lesson:
            return jsonify(ok=False,error='Ressource introuvable'),404
        # Destination exclusively from the server catalogue, never from input.
        if not record(session['member_code'],'resource_redirect',{'lesson_key':lesson_key,'title':lesson['title'],'type':lesson['type'],'url':lesson['url'],'meaning':('URL du lecteur fournie ; lecture non attestée' if request.args.get('format') == 'preview' else 'Redirection vers le fournisseur ; réception du fichier non confirmée')}):
            return jsonify(ok=False,error='Accès temporairement indisponible. Réessayez.'),503
        if request.args.get('format') == 'preview':
            from urllib.parse import urlparse, parse_qs
            import re
            source = urlparse(lesson['url'])
            target = None
            if lesson['type'] == 'video' and source.hostname in {'youtube.com','www.youtube.com','youtu.be'}:
                video_id = source.path.strip('/') if source.hostname == 'youtu.be' else parse_qs(source.query).get('v',[''])[0]
                if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    target = 'https://www.youtube-nocookie.com/embed/' + video_id + '?rel=0&playsinline=1'
            elif lesson['type'] == 'pdf' and source.hostname == 'drive.google.com':
                match = re.fullmatch(r'/file/d/([A-Za-z0-9_-]+)/view', source.path)
                if match:
                    target = 'https://drive.google.com/file/d/' + match[1] + '/preview'
            if not target:
                return jsonify(ok=False,error='Aperçu indisponible'), 422
            response=jsonify(ok=True,url=target,type=lesson['type'],title=lesson['title'])
        else:
            response=redirect(lesson['url'])
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        return response

    @app.route('/admin/api/membre/tracabilite')
    def evidence_profile():
        if not admin_valid():
            return jsonify(ok=False,error='Non autorisé'),403
        code=str(request.args.get('code',''))[:80]
        try:
            before=max(0,int(request.args.get('before','0')))
        except ValueError:
            return jsonify(ok=False,error='Pagination invalide'),400
        conn=get_conn()
        try:
            members=conn.run('SELECT email,created_at,last_login FROM members WHERE code=:code',code=code)
            if not members:
                return jsonify(ok=False,error='Profil introuvable'),404
            exported=request.args.get('export')=='1'
            if not record(code,'admin_export' if exported else 'admin_view',{'actor':'authenticated_admin'}):
                return jsonify(ok=False,error='Journal de consultation indisponible'),503
            limit=1000 if exported else 50
            rows=conn.run("""SELECT id,kind,observed_at,source,details,network_encrypted,email_encrypted
                FROM member_evidence WHERE member_code=:code AND expires_at>NOW()
                AND (:before=0 OR id<:before) ORDER BY id DESC LIMIT :limit""",code=code,before=before,limit=limit+1)
            events=[]
            for row in rows[:limit]:
                ident,kind,stamp,source,details,network,email=row
                events.append({'id':ident,'kind':kind,'label':LABELS.get(kind,kind),'at':stamp.isoformat(),'source':source,
                               'details':json.loads(details) if isinstance(details,str) else details,
                               'network':json.loads(decrypt(network)) if network else {},'email':decrypt(email) if email else ''})
            # Existing verified records are shown as historical sources, never
            # assigned a fictional network address or backdated audit event.
            payments=[]
            if conn.run("SELECT to_regclass('billing_effects')")[0][0]:
                payments=conn.run("""SELECT invoice_id,details,status FROM billing_effects
                    WHERE member_code=:code ORDER BY id DESC LIMIT 101""",code=code)
            legacy=[]
            if conn.run("SELECT to_regclass('stripe_academy_events')")[0][0]:
                legacy=conn.run("""SELECT row_to_json(e) FROM stripe_academy_events e
                    WHERE member_code=:code AND status='processed' AND event_type='invoice.paid'
                    ORDER BY created_at DESC LIMIT 101""",code=code)
            purchases=conn.run("""SELECT stripe_session_id,amount_cents,currency,status,created_at
                FROM analysis_purchases WHERE member_code=:code ORDER BY id DESC LIMIT 101""",code=code)
            invoices=[]
            for invoice,details,state in payments[:100]:
                d=json.loads(details) if isinstance(details,str) else details
                invoices.append({k:d.get(k) for k in ('amount','currency','period_start','period_end','payment_state','subscription_id') } | {'invoice_id':invoice,'followup_status':state,'source':'billing_effects'})
            for row in legacy[:100]:
                d=json.loads(row[0]) if isinstance(row[0],str) else row[0]
                invoices.append({k:d.get(k) for k in ('event_id','event_type','invoice_id','subscription_id','payment_status','amount_paid_cents','currency','created_at','processed_at')} | {'source':'stripe_academy_events'})
            data={'ok':True,'code':code,'email':members[0][0] or '',
                  'account_created_at':str(members[0][1] or ''),'last_login_legacy':str(members[0][2] or ''),
                  'events':events,'next_before':events[-1]['id'] if len(rows)>limit and events else None,
                  'payments':invoices,'payments_truncated':len(payments)>100 or len(legacy)>100,
                  'credit_purchases':[dict(zip(('session_id','amount_cents','currency','status','at'),[r[0],r[1],r[2],r[3],str(r[4])])) for r in purchases[:100]],
                  'credit_purchases_truncated':len(purchases)>100,
                  'notice':'Historique technique conservé 365 jours. Les événements antérieurs non enregistrés et la réception des PDF externes ne sont pas reconstitués. Une trace ne vaut pas consentement ni preuve de lecture.',
                  'generated_at':datetime.now(timezone.utc).isoformat()}
            response=jsonify(data)
            response.headers['Cache-Control']='no-store, private'
            if exported:
                response.headers['Content-Disposition']='attachment; filename="dossier-tracabilite.json"'
            return response
        except Exception as exc:
            app.logger.error('MEMBER_EVIDENCE_READ_FAILED %s',type(exc).__name__)
            return jsonify(ok=False,error='Traçabilité temporairement indisponible'),503
        finally:
            conn.close()

    return record
