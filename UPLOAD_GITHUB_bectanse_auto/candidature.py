"""Verified trial applications. Disabled by default; never grants member access.
Only a server-confirmed email + Telegram phone pair can create an application.
Unverified payloads are encrypted temporary challenges, not CRM dossiers.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from cryptography.fernet import Fernet
from flask import abort, jsonify, render_template, request, session, send_from_directory

GOALS = ('Apprendre les bases','Progresser dans ma pratique','Découvrir le système')
LEVELS = ('Débutant','Intermédiaire','Expérimenté')
AVAILABILITY = ('Immédiatement','Dans une semaine','Dans deux semaines')
STATUSES = ('À étudier','Contacté','Retenu','Non retenu')
SCHEMA = (
'''CREATE TABLE IF NOT EXISTS candidature_runtime (
 name TEXT PRIMARY KEY, ready BOOLEAN NOT NULL DEFAULT FALSE, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
'''CREATE TABLE IF NOT EXISTS candidature_challenges (
 id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, email_hash TEXT NOT NULL, phone_hash TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ NOT NULL,
 email_code TEXT, phone_code TEXT, email_expires TIMESTAMPTZ, phone_expires TIMESTAMPTZ,
 email_verified TIMESTAMPTZ, phone_verified TIMESTAMPTZ,
 email_attempts INTEGER NOT NULL DEFAULT 0, phone_attempts INTEGER NOT NULL DEFAULT 0,
 email_sent_at TIMESTAMPTZ, phone_sent_at TIMESTAMPTZ,
 email_send_state TEXT, phone_send_state TEXT, email_message_id TEXT, phone_message_id TEXT,
 application_id TEXT)''',
'''CREATE TABLE IF NOT EXISTS candidature_telegram (
 challenge_id TEXT PRIMARY KEY REFERENCES candidature_challenges(id) ON DELETE CASCADE,
 token_hash TEXT UNIQUE NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 user_id BIGINT, start_message_id BIGINT, verified_at TIMESTAMPTZ)''',
'''CREATE TABLE IF NOT EXISTS candidature_telegram_updates (
 update_id BIGINT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
'''CREATE INDEX IF NOT EXISTS candidature_challenge_expiry ON candidature_challenges(expires_at)''',
'''CREATE TABLE IF NOT EXISTS candidature_limits (
 id BIGSERIAL PRIMARY KEY, scope TEXT NOT NULL, key_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
'''CREATE INDEX IF NOT EXISTS candidature_limits_key ON candidature_limits(scope,key_hash,created_at)''',
'''CREATE TABLE IF NOT EXISTS candidatures (
 id TEXT PRIMARY KEY, email_hash TEXT UNIQUE NOT NULL, phone_hash TEXT UNIQUE NOT NULL,
 payload TEXT NOT NULL, email_verified_at TIMESTAMPTZ NOT NULL, phone_verified_at TIMESTAMPTZ NOT NULL,
 tag TEXT NOT NULL CHECK(tag IN ('Chaud','Froid')), status TEXT NOT NULL DEFAULT 'À étudier',
 note TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 version INTEGER NOT NULL DEFAULT 1)''',
'''CREATE TABLE IF NOT EXISTS candidature_events (
 id BIGSERIAL PRIMARY KEY, application_id TEXT NOT NULL REFERENCES candidatures(id),
 actor TEXT NOT NULL, event TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
'''CREATE TABLE IF NOT EXISTS candidature_notifications (
 application_id TEXT PRIMARY KEY REFERENCES candidatures(id), state TEXT NOT NULL DEFAULT 'pending',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''',
)

class ApplicationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message); self.status=status


def ensure_candidature_schema(c):
    for sql in SCHEMA:c.run(sql)


def row(c, sql, **kw):
    rs=c.run(sql,**kw)
    return dict(zip([x['name'] for x in c.columns],rs[0])) if rs else None


@contextmanager
def transaction(connect):
    c=connect()
    try:
        c.run('BEGIN');c.run("SET LOCAL statement_timeout='25s'")
        yield c
        c.run('COMMIT')
    except BaseException:
        c.run('ROLLBACK');raise
    finally:c.close()


def normalize(data):
    keys={'first_name','last_name','email','phone','goal','level','motivation','availability','budget'}
    if not isinstance(data,dict) or set(data)!=keys:raise ApplicationError('Renseigne tous les champs demandés.')
    if not all(isinstance(v,str) for v in data.values()):raise ApplicationError('Format invalide.')
    d={k:v.strip() for k,v in data.items()}
    for k,limit in [('first_name',80),('last_name',80),('motivation',1200)]:
        if not d[k] or len(d[k])>limit or any(ord(ch)<32 and ch not in '\n\r\t' for ch in d[k]):raise ApplicationError('Texte invalide.')
    if len(d['motivation'])<10:raise ApplicationError('Précise ta motivation en quelques mots.')
    d['email']=d['email'].lower()
    if len(d['email'])>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',d['email']):raise ApplicationError('Adresse e-mail invalide.')
    d['phone']=re.sub(r'[ ()\-]','',d['phone'])
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}',d['phone']):raise ApplicationError('Indique un téléphone avec son indicatif international.')
    if d['goal'] not in GOALS or d['level'] not in LEVELS or d['availability'] not in AVAILABILITY or d['budget'] not in ('0','300','500','1000'):raise ApplicationError('Choix invalide.')
    return d


class BrevoVerification:
    def __init__(self,key,email_sender='lerisluketo@bectanse-academie.com'):
        self.key=key;self.email_sender=email_sender
    def send(self,channel,target,code):
        text=f'Bectanse : ton code de verification est {code}. Valable 10 minutes. Ne le partage pas.'
        if channel=='email':
            url='https://api.brevo.com/v3/smtp/email'
            payload={'sender':{'email':self.email_sender,'name':'Bectanse Académie'},'to':[{'email':target}],
                'subject':'Ton code de vérification Bectanse','textContent':text,
                'htmlContent':f'<div style="background:#fff8ee;color:#24180f;padding:32px;font-family:Arial,sans-serif"><h2 style="color:#24180f">Confirme ton adresse e-mail</h2><p style="color:#24180f">Ton code de vérification :</p><p style="font-size:36px;color:#ac4b0e;letter-spacing:6px"><b>{code}</b></p><p style="color:#24180f">Valable 10 minutes. Ne le partage pas.</p></div>',
                'tags':['candidature-verification']}
        else:
            raise ApplicationError('Le téléphone se vérifie avec Telegram, pas par SMS.')
        try:
            r=requests.post(url,headers={'api-key':self.key},json=payload,timeout=12)
            if r.status_code in (200,201,202):
                mid=r.json().get('messageId')
                return {'state':'accepted' if mid else 'unknown','id':str(mid or '')}
            return {'state':'unknown' if r.status_code>=500 else 'failed','id':''}
        except (requests.RequestException,ValueError):return {'state':'unknown','id':''}


class CandidatureService:
    def __init__(self,connect,secret,cipher_key,provider,clock=None,telegram_username='',telegram_token=''):
        self.connect=connect;self.secret=secret.encode();self.cipher=Fernet(cipher_key.encode());self.provider=provider
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.telegram_username=telegram_username;self.telegram_token=telegram_token
    @property
    def telegram_secret(self):return self.digest('candidature-telegram-webhook-v1')
    @property
    def telegram_ready(self):return bool(self.telegram_token and re.fullmatch(r'[A-Za-z0-9_]{5,32}',self.telegram_username))
    def digest(self,value):return hmac.new(self.secret,value.encode(),hashlib.sha256).hexdigest()
    def seal(self,data):return self.cipher.encrypt(json.dumps(data,ensure_ascii=False).encode()).decode()
    def unseal(self,data):return json.loads(self.cipher.decrypt(data.encode()))
    def runtime_ready(self):
        with transaction(self.connect) as c:
            r=row(c,"SELECT ready,updated_at FROM candidature_runtime WHERE name='telegram'")
            return bool(r and r['ready'] and r['updated_at']>self.clock()-timedelta(minutes=3))
    def heartbeat(self,ready=True):
        with transaction(self.connect) as c:
            c.run("INSERT INTO candidature_runtime(name,ready,updated_at) VALUES('telegram',:ready,:now) ON CONFLICT(name) DO UPDATE SET ready=EXCLUDED.ready,updated_at=EXCLUDED.updated_at",ready=ready,now=self.clock())
    def cleanup(self):
        with transaction(self.connect) as c:
            c.run("DELETE FROM candidature_telegram_updates WHERE created_at<:cutoff",cutoff=self.clock()-timedelta(days=2))
            c.run("UPDATE candidature_notifications SET state='unknown',updated_at=NOW() WHERE state='sending' AND updated_at<:cutoff",cutoff=self.clock()-timedelta(minutes=5))
            c.run('DELETE FROM candidature_challenges WHERE expires_at<:now',now=self.clock())
            c.run("DELETE FROM candidature_limits WHERE created_at<:cutoff",cutoff=self.clock()-timedelta(days=2))
    def limit(self,c,scope,key,maximum,seconds):
        digest=self.digest(key)
        c.run('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key=scope+digest)
        count=c.run('SELECT count(*) FROM candidature_limits WHERE scope=:s AND key_hash=:k AND created_at>:t',s=scope,k=digest,t=self.clock()-timedelta(seconds=seconds))[0][0]
        if count>=maximum:raise ApplicationError('Trop de tentatives. Réessaie plus tard.',429)
        c.run('INSERT INTO candidature_limits(scope,key_hash,created_at) VALUES(:s,:k,:t)',s=scope,k=digest,t=self.clock())
    def load(self,c,ident,owner):
        r=row(c,'SELECT * FROM candidature_challenges WHERE id=:id FOR UPDATE',id=ident)
        if not r or not hmac.compare_digest(r['owner_hash'],self.digest(owner)) or r['expires_at']<=self.clock():raise ApplicationError('Vérification expirée. Recommence le parcours.',410)
        return r
    def start(self,data,owner,ip):
        d=normalize(data)
        self.cleanup()
        with transaction(self.connect) as c:
            self.limit(c,'start-ip',ip,5,3600);self.limit(c,'start-email',d['email'],3,3600);self.limit(c,'start-phone',d['phone'],3,3600)
            ident=uuid.uuid4().hex
            c.run('DELETE FROM candidature_challenges WHERE owner_hash=:o AND application_id IS NULL',o=self.digest(owner))
            c.run('''INSERT INTO candidature_challenges(id,owner_hash,email_hash,phone_hash,payload,expires_at,created_at)
                VALUES(:id,:o,:e,:p,:data,:expiry,:now)''',id=ident,o=self.digest(owner),e=self.digest(d['email']),p=self.digest(d['phone']),data=self.seal(d),expiry=self.clock()+timedelta(minutes=30),now=self.clock())
        # Email first; Telegram phone confirmation only after email proof.
        try:result=self.send(ident,owner,'email',ip)
        except ApplicationError as e:result={'state':'unconfirmed','error':str(e)}
        return {'id':ident,**result}
    def send(self,ident,owner,channel,ip):
        if channel!='email':raise ApplicationError('Le téléphone se vérifie avec Telegram.')
        with transaction(self.connect) as c:
            r=self.load(c,ident,owner)
            if r['application_id']:return {'state':'submitted'}
            if r[channel+'_verified']:return {'state':'verified'}
            last=r[channel+'_sent_at']
            if last and self.clock()-last<timedelta(seconds=60):raise ApplicationError('Attends 60 secondes avant un nouvel envoi.',429)
            d=self.unseal(r['payload']);target=d['email' if channel=='email' else 'phone']
            self.limit(c,'send-'+channel,target,3,3600);self.limit(c,'send-ip-'+channel,ip,8,3600)
            code=f'{secrets.randbelow(1000000):06d}'
            code_hash=self.digest(ident+channel+code)
            c.run(f'''UPDATE candidature_challenges SET {channel}_code=:hash,{channel}_expires=:exp,
                {channel}_sent_at=:now,{channel}_send_state='sending' WHERE id=:id''',hash=code_hash,exp=self.clock()+timedelta(minutes=10),now=self.clock(),id=ident)
        # Commit the reservation before contacting Brevo. A slow provider must not
        # block unrelated candidates.
        try:
            result=self.provider.send(channel,target,code)
        except Exception:
            result={'state':'unknown','id':''}
        with transaction(self.connect) as c:
            c.run(f'''UPDATE candidature_challenges SET {channel}_send_state=:s,{channel}_message_id=:m
                WHERE id=:id AND {channel}_code=:code_hash''',s=result['state'],m=result.get('id',''),id=ident,code_hash=code_hash)
        # Never retry automatically after timeout / ambiguous provider response.
        if result['state']!='accepted':raise ApplicationError('Envoi non confirmé. Attends avant de demander un nouveau code.',503)
        return {'state':'sent','retry_after':60}
    def verify(self,ident,owner,channel,code):
        if channel!='email' or not isinstance(code,str) or not re.fullmatch('[0-9]{6}',code):raise ApplicationError('Code à six chiffres requis.')
        error=None
        with transaction(self.connect) as c:
            r=self.load(c,ident,owner)
            if r[channel+'_verified']:return {'verified':True}
            if not r[channel+'_code'] or r[channel+'_send_state'] not in ('accepted','unknown') or r[channel+'_expires']<=self.clock():raise ApplicationError('Code expiré ou indisponible.',410)
            if r[channel+'_attempts']>=5:raise ApplicationError('Trop de codes incorrects. Recommence plus tard.',429)
            if hmac.compare_digest(r[channel+'_code'],self.digest(ident+channel+code)):
                c.run(f'UPDATE candidature_challenges SET {channel}_verified=:now,{channel}_code=NULL WHERE id=:id',now=self.clock(),id=ident)
            else:
                c.run(f'UPDATE candidature_challenges SET {channel}_attempts={channel}_attempts+1 WHERE id=:id',id=ident)
                error=ApplicationError('Code incorrect.')
        if error:raise error
        return {'verified':True}
    def status(self,ident,owner):
        with transaction(self.connect) as c:
            r=self.load(c,ident,owner)
            return {'email_verified':bool(r['email_verified']),'phone_verified':bool(r['phone_verified']),
                'submitted':bool(r['application_id']),
                'data':self.unseal(r['payload']) if not r['application_id'] else None}
    def telegram_start(self,ident,owner,ip):
        if not self.telegram_ready:raise ApplicationError('Vérification Telegram temporairement indisponible.',503)
        with transaction(self.connect) as c:
            r=self.load(c,ident,owner)
            if not r['email_verified']:raise ApplicationError('Vérifie d’abord ton adresse e-mail.',409)
            if r['phone_verified']:return {'verified':True}
            self.limit(c,'telegram-start',ident,8,3600)
            self.limit(c,'telegram-start-ip',ip,20,3600)
            token=secrets.token_urlsafe(24)
            c.run('''INSERT INTO candidature_telegram(challenge_id,token_hash,expires_at) VALUES(:id,:hash,:expiry)
                ON CONFLICT(challenge_id) DO UPDATE SET token_hash=EXCLUDED.token_hash,expires_at=EXCLUDED.expires_at,
                user_id=NULL,start_message_id=NULL,verified_at=NULL''',id=ident,hash=self.digest(token),expiry=min(r['expires_at'],self.clock()+timedelta(minutes=10)))
        return {'url':'https://t.me/'+self.telegram_username+'?start=cand_'+token,'expires_in':600}
    def telegram_handle(self,update):
        """Only call after authenticating Telegram's webhook secret. Never trust browser contacts."""
        if not isinstance(update,dict) or type(update.get('update_id')) is not int:return None
        m=update.get('message')
        if not isinstance(m,dict):return None
        chat=m.get('chat') or {};sender=m.get('from') or {};uid=sender.get('id')
        if chat.get('type')!='private' or type(uid) is not int or uid!=chat.get('id') or sender.get('is_bot'):return None
        if any(m.get(k) for k in ('forward_origin','forward_date','forward_from','via_bot')):return None
        text=m.get('text','');contact=m.get('contact');mid=m.get('message_id')
        if not isinstance(text,str) or type(mid) is not int:return None
        match=re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? cand_([A-Za-z0-9_-]{32})',text)
        if not match and not isinstance(contact,dict):return None
        reply=None
        with transaction(self.connect) as c:
            c.run('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key='candidate-telegram-user:'+str(uid))
            if not c.run('INSERT INTO candidature_telegram_updates(update_id) VALUES(:id) ON CONFLICT DO NOTHING RETURNING update_id',id=update['update_id']):return None
            if match:
                # Expiring one-use link; bind to this Telegram user before asking for contact.
                t=row(c,'SELECT * FROM candidature_telegram WHERE token_hash=:hash',hash=self.digest(match[1]))
                if not t or t['expires_at']<=self.clock() or t['verified_at']:
                    return {'chat_id':uid,'text':'Ce lien a expiré ou a déjà servi. Retourne sur ta candidature pour continuer.'}
                if t['user_id'] not in (None,uid):return None
                r=row(c,'SELECT email_verified,expires_at,application_id FROM candidature_challenges WHERE id=:id FOR UPDATE',id=t['challenge_id'])
                if not r or not r['email_verified'] or r['expires_at']<=self.clock() or r['application_id']:return None
                current=row(c,'SELECT * FROM candidature_telegram WHERE challenge_id=:id FOR UPDATE',id=t['challenge_id'])
                if not current or current['token_hash']!=t['token_hash']:return None
                # One active verification per Telegram conversation, so contacts cannot confirm the wrong tab.
                c.run('DELETE FROM candidature_telegram WHERE user_id=:uid AND challenge_id<>:id AND verified_at IS NULL',uid=uid,id=t['challenge_id'])
                c.run('UPDATE candidature_telegram SET user_id=:uid,start_message_id=:mid WHERE challenge_id=:id',uid=uid,mid=mid,id=t['challenge_id'])
                reply={'chat_id':uid,'text':'Pour confirmer ton téléphone pour ta candidature Bectanse, utilise le bouton ci-dessous. Le numéro associé à ton compte Telegram doit être celui saisi sur le formulaire.',
                    'reply_markup':{'keyboard':[[{'text':'Partager mon numéro pour le vérifier','request_contact':True}]],'resize_keyboard':True,'one_time_keyboard':True}}
            else:
                t=row(c,'SELECT * FROM candidature_telegram WHERE user_id=:uid AND verified_at IS NULL ORDER BY expires_at DESC LIMIT 1',uid=uid)
                if not t or t['expires_at']<=self.clock() or mid<=t['start_message_id']:return None
                if type(contact.get('user_id')) is not int or contact['user_id']!=uid:
                    return {'chat_id':uid,'text':'Utilise le bouton « Partager mon numéro pour le vérifier ». Le contact d’une autre personne ne peut pas être accepté.'}
                phone='+'+re.sub(r'[^0-9]','',str(contact.get('phone_number','')))
                if not re.fullmatch(r'\+[1-9][0-9]{7,14}',phone):return None
                r=row(c,'SELECT * FROM candidature_challenges WHERE id=:id FOR UPDATE',id=t['challenge_id'])
                if not r or not r['email_verified'] or r['expires_at']<=self.clock() or r['application_id']:return None
                current=row(c,'SELECT * FROM candidature_telegram WHERE challenge_id=:id FOR UPDATE',id=t['challenge_id'])
                if not current or current['token_hash']!=t['token_hash']:return None
                if not hmac.compare_digest(r['phone_hash'],self.digest(phone)):
                    return {'chat_id':uid,'text':'Ce numéro ne correspond pas au formulaire. Retourne sur ta candidature et corrige le téléphone, ou utilise le compte Telegram associé au numéro saisi.'}
                c.run("UPDATE candidature_challenges SET phone_verified=:now,phone_code=NULL,phone_send_state='telegram_verified' WHERE id=:id",now=self.clock(),id=t['challenge_id'])
                c.run('UPDATE candidature_telegram SET verified_at=:now WHERE challenge_id=:id',now=self.clock(),id=t['challenge_id'])
                reply={'chat_id':uid,'text':'Ton téléphone est confirmé. Retourne dans l’onglet de ta candidature, puis appuie sur « Envoyer ma candidature ».','reply_markup':{'remove_keyboard':True}}
        return reply
    def telegram_reply(self,payload):
        # A lost acknowledgement is never retried blindly. Proof remains available in the form.
        try:
            response=requests.post('https://api.telegram.org/bot'+self.telegram_token+'/sendMessage',json=payload,timeout=12)
            return response.status_code==200 and response.json().get('ok') is True
        except (requests.RequestException,ValueError):return False
    def submit(self,ident,owner):
        with transaction(self.connect) as c:
            r=self.load(c,ident,owner)
            if r['application_id']:return {'id':r['application_id'],'submitted':True}
            if not r['email_verified'] or not r['phone_verified']:raise ApplicationError('Confirme ton e-mail et ton téléphone avant l’envoi.',409)
            d=self.unseal(r['payload']);ident_new=uuid.uuid4().hex
            inserted=c.run('''INSERT INTO candidatures(id,email_hash,phone_hash,payload,email_verified_at,phone_verified_at,tag)
                VALUES(:id,:e,:p,:data,:ev,:pv,:tag) ON CONFLICT DO NOTHING RETURNING id''',id=ident_new,e=r['email_hash'],p=r['phone_hash'],data=r['payload'],ev=r['email_verified'],pv=r['phone_verified'],tag='Froid' if d['budget']=='0' else 'Chaud')
            if not inserted:raise ApplicationError('Une candidature existe déjà pour ces coordonnées. Contacte l’équipe pour son suivi.',409)
            c.run('INSERT INTO candidature_notifications(application_id) VALUES(:id)',id=ident_new)
            c.run("INSERT INTO candidature_events(application_id,actor,event) VALUES(:id,'candidate','submitted')",id=ident_new)
            c.run("UPDATE candidature_challenges SET application_id=:a,payload='' WHERE id=:id",a=ident_new,id=ident)
        return {'id':ident_new,'submitted':True}
    def list(self, before=None):
        with transaction(self.connect) as c:
            rs=c.run('SELECT id,payload,tag,status,note,created_at,version FROM candidatures WHERE (CAST(:before AS TEXT) IS NULL OR id < CAST(:before AS TEXT)) ORDER BY id DESC LIMIT 100',before=before)
        return [{**self.unseal(r[1]),'id':r[0],'tag':r[2],'status':r[3],'note':r[4],'created_at':r[5].isoformat(),'version':r[6],'email_verified':True,'phone_verified':True,'phone_verification_method':'telegram'} for r in rs]
    def update(self,ident,status,note,version,actor):
        if status not in STATUSES or not isinstance(note,str) or len(note)>4000 or type(version)!=int:raise ApplicationError('Modification invalide.')
        with transaction(self.connect) as c:
            rs=c.run('UPDATE candidatures SET status=:s,note=:n,version=version+1,updated_at=NOW() WHERE id=:id AND version=:v RETURNING version',s=status,n=note,id=ident,v=version)
            if not rs:raise ApplicationError('Fiche modifiée par un autre administrateur. Actualise la liste.',409)
            c.run('INSERT INTO candidature_events(application_id,actor,event) VALUES(:id,:actor,:event)',id=ident,actor=actor,event='status:'+status)
        return {'version':rs[0][0]}
    def notify(self,send):
        with transaction(self.connect) as c:
            r=row(c,"SELECT application_id FROM candidature_notifications WHERE state='pending' FOR UPDATE SKIP LOCKED LIMIT 1")
            if not r:return
            ident=r['application_id'];c.run("UPDATE candidature_notifications SET state='sending',updated_at=NOW() WHERE application_id=:id",id=ident)
        try:ok=send(ident);state='sent' if ok else 'unknown'
        except Exception:state='unknown'
        with transaction(self.connect) as c:c.run('UPDATE candidature_notifications SET state=:s,updated_at=NOW() WHERE application_id=:id',s=state,id=ident)


def register_candidature(app,get_conn,admin_required,send_notification):
    provider=BrevoVerification(os.environ.get('BREVO_KEY',''))
    svc=CandidatureService(get_conn,app.secret_key,os.environ['DATA_ENCRYPTION_KEY'],provider,
        telegram_username=os.environ.get('CANDIDATURE_TELEGRAM_USERNAME',''),telegram_token=os.environ.get('BOT_TOKEN',''))
    def csrf():
        if 'candidature_csrf' not in session:session['candidature_csrf']=secrets.token_urlsafe(32)
        return session['candidature_csrf']
    def owner():
        if 'candidature_owner' not in session:session['candidature_owner']=secrets.token_urlsafe(32)
        return session['candidature_owner']
    @app.before_request
    def candidature_guard():
        # The public landing page embeds only two explicitly whitelisted announcement files.
        # They remain available even while applications themselves are temporarily closed.
        if request.path.startswith('/candidature/media/'):
            return
        if request.path.startswith(('/candidature','/api/candidature','/admin/candidatures','/admin/api/candidatures')):
            if (os.environ.get('CANDIDATURE_ENABLED')!='1' or not provider.key
                    or not svc.telegram_ready):
                abort(503,description='Candidatures temporairement indisponibles')
            if os.environ.get('CANDIDATURE_PUBLIC_ENABLED')!='1' and not session.get('admin_authenticated') and not request.path.startswith('/admin'):
                abort(503,description='Le parcours est en cours de vérification avant ouverture.')
            # The Telegram heartbeat gates new public applications only.  It
            # must never hide the back-office: administrators still need to
            # consult existing verified profiles and team follow-up whenever
            # Telegram is briefly unavailable.
            if (not request.path.startswith('/admin')
                    and os.environ.get('APP_ENV')=='production'
                    and not svc.runtime_ready()):
                abort(503,description='Vérification temporairement indisponible. Réessaie dans un instant.')
            if request.method=='POST':
                expected=session.get('candidature_csrf','');provided=request.headers.get('X-CSRF-Token','')
                if not expected or not hmac.compare_digest(expected,provided):abort(403)
                origin=request.headers.get('Origin','');expected_origin=os.environ.get('PUBLIC_APP_URL','https://acces.bectanse-academie.com').rstrip('/')
                if origin!=expected_origin:abort(403)
                if request.content_length is None or request.content_length>12000:abort(413)
    @app.after_request
    def candidature_headers(response):
        if request.path.startswith(('/candidature','/api/candidature','/admin/candidatures','/admin/api/candidatures')):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Robots-Tag']='noindex, nofollow'
            response.headers['Referrer-Policy']='no-referrer'
            response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        return response
    def response_call(fn):
        try:return jsonify(ok=True,**fn())
        except ApplicationError as e:return jsonify(ok=False,error=str(e)),e.status
    def body():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise ApplicationError('Format de requête invalide.')
        return data
    def ip():
        # Railway edge appends the observed client to X-Forwarded-For; no first-value trust.
        return request.headers.get('X-Forwarded-For',request.remote_addr or '').split(',')[-1].strip()
    @app.get('/candidature')
    def candidature_page():
        owner();return render_template('candidature.html',csrf=csrf())
    @app.get('/candidature/media/<filename>')
    def candidature_announcement(filename):
        # Publish only the requested announcement, never the private trial library.
        if filename not in ('annonce.mp4','annonce.jpg'):abort(404)
        media_dir=Path(os.environ.get('CANDIDATURE_ANNOUNCE_DIR') or
            Path(app.root_path)/'persistent'/'broker-trial-assets'/'media')
        return send_from_directory(media_dir,'presentation'+Path(filename).suffix,conditional=True)
    @app.post('/api/candidature/start')
    def candidature_start():
        def run():
            result=svc.start(request.get_json(silent=True),owner(),ip());session['candidature_id']=result['id'];return result
        return response_call(run)
    @app.post('/api/candidature/send')
    def candidature_send():
        return response_call(lambda:svc.send(session.get('candidature_id',''),owner(),body().get('channel'),ip()))
    @app.post('/api/candidature/verify')
    def candidature_verify():
        def run():
            d=body()
            return svc.verify(session.get('candidature_id',''),owner(),d.get('channel'),d.get('code'))
        return response_call(run)
    @app.post('/api/candidature/telegram/start')
    def candidature_telegram_start():return response_call(lambda:svc.telegram_start(session.get('candidature_id',''),owner(),ip()))
    @app.get('/api/candidature/status')
    def candidature_status():return response_call(lambda:svc.status(session.get('candidature_id',''),owner()))
    @app.post('/api/candidature/submit')
    def candidature_submit():return response_call(lambda:svc.submit(session.get('candidature_id',''),owner()))
    @app.get('/admin/candidatures')
    @admin_required
    def candidature_admin():return render_template('admin_candidatures.html',csrf=csrf())
    @app.get('/admin/api/candidatures')
    @admin_required
    def candidature_admin_list():
        candidates=svc.list(request.args.get('before') or None)
        return jsonify(ok=True,candidates=candidates,next_cursor=candidates[-1]['id'] if len(candidates)==100 else None)
    @app.post('/admin/api/candidatures/<ident>')
    @admin_required
    def candidature_admin_update(ident):
        def run():
            d=body()
            return svc.update(ident,d.get('status'),d.get('note',''),d.get('version'),'admin')
        return response_call(run)
    def tick():
        svc.cleanup()
        for _ in range(10):svc.notify(send_notification)
    return {'service':svc,'tick':tick}
