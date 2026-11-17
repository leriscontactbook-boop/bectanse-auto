"""Private, manually approved broker trial. No broker/API payment or trading actions.

Members remain Explorer until two separate administrator reviews succeed. Every
transition and its notification intents commit together; provider calls happen
only after commit through the leased outbox. PostgreSQL is the authority.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import abort, jsonify, render_template, request, session, send_from_directory, redirect
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

BROKER_URL = 'https://go.bullwaves.com/visit/?bta=35465&brand=bullwaves&afp=leris'
STATES = ('invited', 'referral_pending', 'referral_correction', 'deposit_ready',
          'deposit_pending', 'deposit_correction', 'active', 'expired', 'revoked', 'rejected')
DDL = (
"""ALTER TABLE members ADD COLUMN IF NOT EXISTS broker_trial_id TEXT""",
"""ALTER TABLE members ADD COLUMN IF NOT EXISTS broker_trial_status TEXT NOT NULL DEFAULT ''""",
"""ALTER TABLE members ADD COLUMN IF NOT EXISTS broker_trial_approved_at TIMESTAMPTZ""",
"""ALTER TABLE members ADD COLUMN IF NOT EXISTS broker_trial_until TIMESTAMPTZ""",
"""CREATE TABLE IF NOT EXISTS broker_trials (
 id TEXT PRIMARY KEY,member_code TEXT NOT NULL UNIQUE REFERENCES members(code) ON DELETE RESTRICT,
 identity_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'invited'
 CHECK (status IN ('invited','referral_pending','referral_correction','deposit_ready',
 'deposit_pending','deposit_correction','active','expired','revoked','rejected')),
 first_name TEXT NOT NULL DEFAULT '',last_name TEXT NOT NULL DEFAULT '',
 broker_email TEXT NOT NULL DEFAULT '',broker_reference TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',
 invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),invited_by TEXT NOT NULL,
 referral_requested_at TIMESTAMPTZ,referral_reviewed_at TIMESTAMPTZ,referral_reviewed_by TEXT,
 deposit_requested_at TIMESTAMPTZ,approved_at TIMESTAMPTZ,approved_by TEXT,ends_at TIMESTAMPTZ,
 deposit_reference TEXT,deposit_amount_cents INTEGER,deposit_currency TEXT,
 deposit_operations INTEGER,version INTEGER NOT NULL DEFAULT 1,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CONSTRAINT broker_trials_active_window_check CHECK (status <> 'active' OR (approved_at IS NOT NULL AND ends_at IS NOT NULL
 AND approved_by IS NOT NULL AND referral_reviewed_at IS NOT NULL
 AND deposit_amount_cents>=35000 AND deposit_currency='EUR' AND deposit_operations=1
 AND deposit_reference IS NOT NULL AND ends_at IN (approved_at+INTERVAL '15 days', approved_at+INTERVAL '30 days'))))
""",
"""DO $$ DECLARE c RECORD; BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='broker_trials'::regclass AND conname='broker_trials_active_window_check' AND pg_get_constraintdef(oid) LIKE '%deposit_amount_cents >= 35000%') THEN
  FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='broker_trials'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%approved_at%' AND pg_get_constraintdef(oid) LIKE '%ends_at%' LOOP
   EXECUTE format('ALTER TABLE broker_trials DROP CONSTRAINT %I', c.conname);
  END LOOP;
  ALTER TABLE broker_trials ADD CONSTRAINT broker_trials_active_window_check CHECK (status <> 'active' OR (approved_at IS NOT NULL AND ends_at IS NOT NULL AND approved_by IS NOT NULL AND referral_reviewed_at IS NOT NULL AND deposit_amount_cents>=35000 AND deposit_currency='EUR' AND deposit_operations=1 AND deposit_reference IS NOT NULL AND ends_at IN (approved_at+INTERVAL '15 days',approved_at+INTERVAL '30 days')));
 END IF;
END $$""",
"""CREATE UNIQUE INDEX IF NOT EXISTS broker_trials_account_unique ON broker_trials
 (broker_reference) WHERE broker_reference<>''""",
"""CREATE UNIQUE INDEX IF NOT EXISTS broker_trials_deposit_unique ON broker_trials
 (deposit_reference) WHERE deposit_reference IS NOT NULL""",
"""CREATE TABLE IF NOT EXISTS broker_trial_events (
 id BIGSERIAL PRIMARY KEY,trial_id TEXT NOT NULL REFERENCES broker_trials(id) ON DELETE RESTRICT,
 actor TEXT NOT NULL,event TEXT NOT NULL,details JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS broker_trial_outbox (
 id BIGSERIAL PRIMARY KEY,trial_id TEXT NOT NULL REFERENCES broker_trials(id) ON DELETE RESTRICT,
 event TEXT NOT NULL,version INTEGER NOT NULL,channel TEXT NOT NULL CHECK(channel IN ('email','telegram')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed','skipped')),
 attempts INTEGER NOT NULL DEFAULT 0,retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 lease_until TIMESTAMPTZ,lease_token TEXT,last_error TEXT NOT NULL DEFAULT '',sent_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(trial_id,event,version,channel))""",
"""CREATE INDEX IF NOT EXISTS broker_trial_outbox_pending ON broker_trial_outbox(status,retry_at)""",
"""CREATE INDEX IF NOT EXISTS broker_trials_status ON broker_trials(status,updated_at DESC)""",
)
DDL += (
"""CREATE TABLE IF NOT EXISTS broker_discovery_links (
 id TEXT PRIMARY KEY,label TEXT NOT NULL,created_by TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '30 days',
 revoked_at TIMESTAMPTZ,revoked_by TEXT,claimed_member TEXT REFERENCES members(code),claimed_at TIMESTAMPTZ)""",
"""ALTER TABLE broker_discovery_links ADD COLUMN IF NOT EXISTS short_code TEXT""",
"""ALTER TABLE broker_discovery_links ADD COLUMN IF NOT EXISTS is_campaign BOOLEAN NOT NULL DEFAULT FALSE""",
"""INSERT INTO broker_discovery_links(id,label,created_by,is_campaign)
 VALUES('campaign-accesgratuit','Canal prospects — accès gratuit','system:shared-discovery',TRUE) ON CONFLICT(id) DO NOTHING""",
"""CREATE UNIQUE INDEX IF NOT EXISTS broker_discovery_short_code ON broker_discovery_links(short_code)""",
"""CREATE TABLE IF NOT EXISTS broker_discovery_mail_attempts (
 id BIGSERIAL PRIMARY KEY,link_id TEXT NOT NULL REFERENCES broker_discovery_links(id),
 email_hash TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
"""ALTER TABLE broker_discovery_mail_attempts ADD COLUMN IF NOT EXISTS ip_hash TEXT NOT NULL DEFAULT ''""",
"""CREATE INDEX IF NOT EXISTS broker_discovery_mail_rate ON broker_discovery_mail_attempts(created_at,link_id,email_hash)""",
)
MEMBER_COLUMNS = ('code','nom','email','access_level','actif','copy_actif','admin_suspended',
                  'stripe_subscription_id','billing_status','manual_access_until','email_verified_at')

class TrialError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def ensure_trial_schema(conn):
    for sql in DDL:
        conn.run(sql)
    for (link_id,) in conn.run('SELECT id FROM broker_discovery_links WHERE short_code IS NULL'):
        conn.run('UPDATE broker_discovery_links SET short_code=:code WHERE id=:id AND short_code IS NULL',
                 code=secrets.token_urlsafe(12),id=link_id)


def row_dict(conn, sql, **params):
    rows = conn.run(sql, **params)
    return dict(zip([c['name'] for c in conn.columns], rows[0])) if rows else None


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def eligibility(member):
    """Never infer new-customer eligibility from an active flag or future date."""
    return bool(member and str(member.get('code','')).upper() != 'BCT-DEMO2026'
                and member.get('access_level') in ('explorer','demo')
                and member.get('email_verified_at') and member.get('email')
                and not member.get('admin_suspended')
                and not member.get('stripe_subscription_id')
                and not member.get('manual_access_until')
                and str(member.get('billing_status') or '').lower() not in
                ('active','trialing','past_due','unpaid','canceled','paused'))


def clean(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise TrialError(f'{label} invalide', 400)
    text = value.strip()
    if any(ord(c) < 32 for c in text):
        raise TrialError(f'{label} invalide', 400)
    return text


def referral_fields(data):
    required = {'first_name','last_name','broker_email','broker_reference','phone','documents_verified'}
    if set(data) != required or data.get('documents_verified') is not True:
        raise TrialError('Confirme les documents et renseigne uniquement les champs demandés', 400)
    result = {key:clean(data.get(key), key, 254 if key=='broker_email' else 100) for key in required-{'documents_verified'}}
    result['broker_email'] = result['broker_email'].lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', result['broker_email']):
        raise TrialError('Adresse e-mail invalide',400)
    phone = re.sub(r'[ ()-]', '', result['phone'])
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', phone):
        raise TrialError('Indique le numéro avec son indicatif international',400)
    result['phone'] = phone
    result['broker_reference'] = result['broker_reference'].upper()
    return result


@contextmanager
def transaction(get_conn):
    conn = get_conn()
    try:
        conn.run('BEGIN')
        conn.run("SET LOCAL statement_timeout='15s'")
        conn.run("SET LOCAL TIME ZONE 'UTC'")
        yield conn
        conn.run('COMMIT')
    except BaseException:
        conn.run('ROLLBACK')
        raise
    finally:
        conn.close()


class BrokerTrialService:
    def __init__(self, get_conn, send_email, send_telegram, *, origin, telegram_chat_id=None):
        self.get_conn = get_conn
        self.send_email = send_email
        self.send_telegram = send_telegram
        parsed = urlparse(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Trial public origin must be an HTTPS origin')
        self.origin = origin.rstrip('/')
        self.telegram_chat_id = telegram_chat_id
        self.invitation_url = lambda t: self.origin+'/offre-decouverte'

    def _member(self, conn, code):
        return row_dict(conn, 'SELECT '+','.join(MEMBER_COLUMNS)+' FROM members WHERE code=:code FOR UPDATE',code=code)

    def _trial(self, conn, code):
        return row_dict(conn,'SELECT * FROM broker_trials WHERE member_code=:code FOR UPDATE',code=code)

    def _audit(self, conn, trial, actor, event, details=None):
        conn.run('INSERT INTO broker_trial_events(trial_id,actor,event,details) VALUES(:id,:actor,:event,CAST(:details AS JSONB))',
                 id=trial['id'],actor=actor,event=event,details=json.dumps(details or {}))

    def _queue(self, conn, trial, event, telegram=False):
        for channel in (('email','telegram') if telegram else ('email',)):
            conn.run('''INSERT INTO broker_trial_outbox(trial_id,event,version,channel)
                VALUES(:id,:event,:version,:channel) ON CONFLICT DO NOTHING''',
                id=trial['id'],event=event,version=trial['version'],channel=channel)

    def _advance(self, conn, trial, state, actor, event, note='', telegram=False):
        updated = row_dict(conn,'''UPDATE broker_trials SET status=:status,review_note=:note,
            version=version+1,updated_at=NOW() WHERE id=:id RETURNING *''',status=state,note=note,id=trial['id'])
        conn.run("UPDATE members SET broker_trial_status=:status WHERE code=:code AND broker_trial_id=:id",status=state,code=trial['member_code'],id=trial['id'])
        self._audit(conn,updated,actor,event,{'from':trial['status'],'to':state,'note':note})
        self._queue(conn,updated,event,telegram)
        return updated

    def invite(self, code, actor):
        with transaction(self.get_conn) as c:
            return self._invite_in_connection(c, code, actor)

    def _invite_in_connection(self, c, code, actor):
        member = self._member(c,code)
        existing = self._trial(c,code)
        if existing:
            return existing
        if not eligibility(member):
            raise TrialError('Ce compte n’est pas un nouveau compte Explorer éligible')
        # Durable identity prevents a second trial after an e-mail/account recreation.
        identity = hashlib.sha256(member['email'].strip().lower().encode()).hexdigest()
        prior_members = c.run('''SELECT 1 FROM members WHERE LOWER(TRIM(email))=LOWER(TRIM(:email))
            AND code<>:code AND (COALESCE(access_level,'member') NOT IN ('explorer','demo')
            OR COALESCE(stripe_subscription_id,'')<>'' OR manual_access_until IS NOT NULL) LIMIT 1''',
            email=member['email'],code=code)
        if prior_members:
            raise TrialError('Cette identité a déjà rejoint l’Académie')
        trial=row_dict(c,'''INSERT INTO broker_trials(id,member_code,identity_key,invited_by)
            VALUES(:id,:code,:identity,:actor) RETURNING *''',id=str(uuid.uuid4()),code=code,identity=identity,actor=actor)
        c.run("UPDATE members SET broker_trial_id=:id,broker_trial_status='invited' WHERE code=:code",id=trial['id'],code=code)
        # Invitation alone never promotes an Explorer or starts its timer.
        self._audit(c,trial,actor,'invited')
        self._queue(c,trial,'invited')
        return trial

    def member_action(self, code, action, data):
        with transaction(self.get_conn) as c:
            member=self._member(c,code)
            trial=self._trial(c,code)
            if not trial:
                raise TrialError('Invitation nécessaire',403)
            if not member or member.get('admin_suspended'):
                raise TrialError('Compte indisponible',403)
            if action=='request_referral':
                fields=referral_fields(data)
                if trial['status']=='referral_pending':
                    if all(trial[k]==v for k,v in fields.items()):
                        return trial
                    raise TrialError('Une demande est déjà en cours')
                if trial['status'] not in ('invited','referral_correction'):
                    raise TrialError('Cette étape ne peut plus être modifiée')
                if not eligibility(member):
                    raise TrialError('Compte non éligible',403)
                c.run('''UPDATE broker_trials SET first_name=:first_name,last_name=:last_name,
                    broker_email=:broker_email,broker_reference=:broker_reference,phone=:phone,
                    referral_requested_at=NOW() WHERE id=:id''',id=trial['id'],**fields)
                return self._advance(c,trial,'referral_pending',code,'referral_requested',telegram=True)
            if action=='request_deposit':
                if set(data)!={'deposit_confirmed'} or data['deposit_confirmed'] is not True:
                    raise TrialError('Confirme un dépôt unique d’au moins 350 € reçu par le broker',400)
                if trial['status']=='deposit_pending':
                    return trial
                if trial['status'] not in ('deposit_ready','deposit_correction') or not trial['referral_reviewed_at']:
                    raise TrialError('Attends la validation de ton rattachement')
                c.run('UPDATE broker_trials SET deposit_requested_at=NOW() WHERE id=:id',id=trial['id'])
                return self._advance(c,trial,'deposit_pending',code,'deposit_requested',telegram=True)
            raise TrialError('Action inconnue',400)

    def review(self, code, action, data, actor):
        with transaction(self.get_conn) as c:
            member=self._member(c,code)
            trial=self._trial(c,code)
            if not member or not trial:
                raise TrialError('Dossier introuvable',404)
            if action=='confirm_referral':
                if set(data)!={'account_matches','documents_verified','affiliate_confirmed'} or not all(v is True for v in data.values()):
                    raise TrialError('Les trois contrôles du rattachement sont nécessaires',400)
                if trial['status']=='deposit_ready':
                    return trial
                if trial['status']!='referral_pending':
                    raise TrialError('Demande de rattachement attendue')
                if not eligibility(member):
                    raise TrialError('Compte non éligible')
                c.run('''UPDATE broker_trials SET referral_reviewed_at=NOW(),referral_reviewed_by=:actor
                    WHERE id=:id''',actor=actor,id=trial['id'])
                return self._advance(c,trial,'deposit_ready',actor,'referral_confirmed')
            if action=='activate':
                if trial['status']=='active':
                    return trial  # Never restart the timer on double-click/retry.
                required={'account_matches','affiliate_confirmed','documents_verified','deposit_reference',
                          'deposit_amount_cents','deposit_currency','deposit_operations'}
                if set(data)!=required or any(data[k] is not True for k in ('account_matches','affiliate_confirmed','documents_verified')):
                    raise TrialError('Confirme tous les contrôles avant activation',400)
                if type(data['deposit_amount_cents']) is not int or data['deposit_amount_cents'] < 35000 or data['deposit_currency']!='EUR' or type(data['deposit_operations']) is not int or data['deposit_operations']!=1:
                    raise TrialError('Un seul dépôt confirmé d’au moins 350 EUR est requis',400)
                reference=clean(data['deposit_reference'],'Référence de transaction',120).upper()
                if trial['status']!='deposit_pending' or not trial['referral_reviewed_at'] or not eligibility(member):
                    raise TrialError('Le dossier ne permet pas une activation')
                trial=row_dict(c,'''UPDATE broker_trials SET status='active',version=version+1,
                    approved_at=NOW(),approved_by=:actor,ends_at=NOW()+INTERVAL '15 days',review_note='',
                    deposit_reference=:reference,deposit_amount_cents=:amount,deposit_currency='EUR',
                    deposit_operations=1,updated_at=NOW() WHERE id=:id RETURNING *''',actor=actor,reference=reference,amount=data['deposit_amount_cents'],id=trial['id'])
                c.run('''UPDATE members SET access_level='broker_trial',actif=TRUE,copy_actif=FALSE,
                    broker_trial_id=:id,broker_trial_status='active',broker_trial_approved_at=:start,
                    broker_trial_until=:end,date_fin=:legacy_end WHERE code=:code''',
                    id=trial['id'],start=trial['approved_at'],end=trial['ends_at'],
                    legacy_end=utc(trial['ends_at']).replace(tzinfo=None),code=code)
                self._audit(c,trial,actor,'activated',{'deposit_reference':reference,'amount_cents':data['deposit_amount_cents'],'currency':'EUR'})
                self._queue(c,trial,'activated',telegram=True)
                return trial
            if action in ('correct_referral','correct_deposit','reject','revoke'):
                if set(data)!={'note'}:
                    raise TrialError('Un motif est requis',400)
                note=clean(data['note'],'Motif',1000)
                allowed={'correct_referral':('referral_pending',),'correct_deposit':('deposit_pending',),
                         'reject':('invited','referral_pending','referral_correction','deposit_ready','deposit_pending','deposit_correction'),
                         'revoke':('active',)}
                target={'correct_referral':'referral_correction','correct_deposit':'deposit_correction','reject':'rejected','revoke':'revoked'}[action]
                if trial['status']==target:
                    return trial
                if trial['status'] not in allowed[action]:
                    raise TrialError('Transition non autorisée')
                if action=='revoke':
                    c.run("UPDATE members SET broker_trial_status='revoked' WHERE code=:code AND broker_trial_id=:id",code=code,id=trial['id'])
                    c.run("UPDATE members SET access_level='explorer',copy_actif=FALSE WHERE code=:code AND access_level='broker_trial' AND broker_trial_id=:id",code=code,id=trial['id'])
                return self._advance(c,trial,target,actor,target,note,telegram=action=='revoke')
            raise TrialError('Action inconnue',400)

    def state(self,code):
        c=self.get_conn()
        try:
            trial=row_dict(c,'SELECT * FROM broker_trials WHERE member_code=:code',code=code)
            if trial and trial['status']=='active' and utc(trial['ends_at'])<=datetime.now(timezone.utc):
                trial['status']='expired'  # Exact cutoff even before the scheduled sweep.
            return trial
        finally:
            c.close()

    def sweep(self):
        # DB locks serialize multiple web workers. Rights already fail closed at expiry.
        c=self.get_conn()
        try:
            codes=[r[0] for r in c.run("SELECT member_code FROM broker_trials WHERE status='active'")]
        finally:
            c.close()
        for code in codes:
            with transaction(self.get_conn) as c:
                member=self._member(c,code)
                trial=self._trial(c,code)
                if not trial or trial['status']!='active':
                    continue
                if utc(trial['ends_at'])<=datetime.now(timezone.utc):
                    self._advance(c,trial,'expired','system','expired')
                    c.run("UPDATE members SET broker_trial_status='expired' WHERE code=:code AND broker_trial_id=:id",code=code,id=trial['id'])
                    # A later paid subscription remains untouched.
                    c.run("UPDATE members SET access_level='explorer',actif=TRUE,copy_actif=FALSE WHERE code=:code AND access_level='broker_trial' AND broker_trial_id=:id",code=code,id=trial['id'])
                elif member and member.get('access_level')=='broker_trial':
                    remaining=utc(trial['ends_at'])-datetime.now(timezone.utc)
                    for days in (7,3,1):
                        if remaining<=timedelta(days=days) and remaining>timedelta(days=days-1):
                            self._queue(c,trial,f'reminder_{days}')

    def dispatch(self,limit=20):
        delivered=0
        for _ in range(limit):
            token=secrets.token_hex(16)
            with transaction(self.get_conn) as c:
                job=row_dict(c,'''WITH next_job AS (SELECT id FROM broker_trial_outbox
                    WHERE ((status='pending' AND retry_at<=NOW()) OR (status='sending' AND lease_until<=NOW()))
                    AND attempts<8 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                    UPDATE broker_trial_outbox o SET status='sending',attempts=attempts+1,
                    lease_until=NOW()+INTERVAL '5 minutes',lease_token=:token
                    FROM next_job WHERE o.id=next_job.id RETURNING o.*''',token=token)
            if not job:
                break
            c=self.get_conn()
            try:
                t=row_dict(c,'SELECT * FROM broker_trials WHERE id=:id',id=job['trial_id'])
                m=row_dict(c,'SELECT code,nom,email,access_level FROM members WHERE code=:code',code=t['member_code'])
            finally:
                c.close()
            expected={'invited':('invited',),'referral_requested':('referral_pending',),
                      'referral_confirmed':('deposit_ready',),'deposit_requested':('deposit_pending',),
                      'referral_correction':('referral_correction',),'deposit_correction':('deposit_correction',),
                      'activated':('active',),'expired':('expired',),'revoked':('revoked',),'rejected':('rejected',)}
            allowed=expected.get(job['event'],('active',))
            skipped=(not m or t['status'] not in allowed or
                     (job['event'].startswith('reminder_') or job['event']=='expired') and m.get('access_level')!='broker_trial' and t['status']!='expired')
            if job['event']=='expired' and m and m.get('access_level')!='explorer':
                skipped=True
            ok=False
            error=''
            try:
                if not skipped:
                    subject,body=self.email_content(t,job['event'])
                    if job['channel']=='email':
                        result=self.send_email(m['email'],m['nom'],subject,body,'broker-trial-'+job['event'])
                        ok=result is True or isinstance(result,dict) and result.get('ok') is True
                    else:
                        label={'referral_pending':'Rattachement à vérifier','deposit_pending':'Dépôt à vérifier','active':'Essai activé','revoked':'Essai interrompu'}.get(t['status'],t['status'].replace('_',' '))
                        text=f"Offre 15 jours — {subject}\nÉtape : {label}\nDossier : {t['id']}\nOuvre la fiche sécurisée pour les coordonnées et les contrôles."
                        ok=self.send_telegram(text,reply_markup={'inline_keyboard':[[{'text':'Ouvrir le dossier sécurisé','url':self.origin+'/admin/essais-broker#'+t['id']}]]},chat_id=self.telegram_chat_id) is True
                    if not ok:
                        error='provider_delivery_failed'
            except Exception as exc:
                error=type(exc).__name__  # No provider secrets or PII in stored error.
            with transaction(self.get_conn) as c:
                status='skipped' if skipped else 'sent' if ok else 'failed' if job['attempts']>=8 else 'pending'
                c.run('''UPDATE broker_trial_outbox SET status=:status,last_error=:error,
                    sent_at=CASE WHEN :ok THEN NOW() ELSE sent_at END,lease_until=NULL,lease_token=NULL,
                    retry_at=NOW()+(:delay * INTERVAL '1 second') WHERE id=:id AND lease_token=:token''',
                    status=status,error=error,ok=bool(ok),delay=min(3600,30*2**job['attempts']),id=job['id'],token=token)
            delivered+=int(bool(ok))
        return delivered

    def email_content(self,t,event):
        end=utc(t.get('ends_at'))
        date=end.astimezone(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y à %H:%M') if end else ''
        content={
          'invited':('Ton invitation privée à l’Académie','Ton compte BCT reste en démo. Découvre les vidéos et le parcours. Avant tout dépôt, notre équipe devra confirmer ton rattachement au broker.'),
          'referral_requested':('Nous avons reçu ta demande de rattachement','Ton dossier est enregistré. Attends la confirmation de l’équipe avant de déposer ton capital. Ton compte BCT reste en démo.'),
          'referral_confirmed':('Ton rattachement est confirmé','L’équipe a vérifié ton rattachement. Tu peux reprendre le guide et effectuer un dépôt unique d’au moins 350 € selon les conditions du broker. Ce minimum correspond à la gestion du risque 0,01 ; 500 € ou plus est recommandé pour la gestion du risque 0,02, sans garantie de résultat. Ton compte BCT reste en démo jusqu’au contrôle final. Le trading comporte un risque de perte du capital.'),
          'deposit_requested':('Ton dépôt est en cours de vérification','Ta déclaration est enregistrée. Notre équipe vérifie le dépôt auprès du broker. Aucun accès membre n’est encore activé.'),
          'referral_correction':('Une précision est nécessaire sur ton compte','Reviens sur ton dossier pour corriger les informations. N’effectue pas de dépôt avant confirmation du rattachement.'),
          'deposit_correction':('Une précision est nécessaire sur ton dépôt','Consulte le retour de l’équipe sur ton dossier. Ne refais pas de dépôt sans clarification.'),
          'activated':('Tes 15 jours offerts sont activés',f'L’équipe a validé ton essai. Tu conserves ton compte et ton code BCT. Ton accès est ouvert jusqu’au {date}, heure de Paris. Aucun prélèvement automatique n’est prévu. À la fin, tu pourras choisir un abonnement ou retrouver le mode démo.'),
          'expired':('Tes 15 jours offerts sont terminés','Ton accès offert est terminé et ton compte retrouve le mode démo. Aucun abonnement n’a été facturé automatiquement. Tu peux consulter les offres pour poursuivre.'),
          'revoked':('Ton accès offert a été interrompu','Consulte le retour de l’équipe dans ton dossier. Ton compte conserve son accès démo.'),
          'rejected':('Mise à jour de ta demande d’essai','Ton essai n’a pas été activé. Consulte le motif et contacte l’équipe si nécessaire. Ton compte reste en démo.'),
        }
        subject,text=content.get(event,('Tes 15 jours offerts arrivent à échéance',f'Ton accès offert se termine le {date}, heure de Paris. Aucun renouvellement automatique : tu choisis librement de poursuivre ou de retrouver le mode démo.'))
        note=f'<p>Retour de l’équipe : {html.escape(t["review_note"])}</p>' if t.get('review_note') else ''
        body=f'''<!doctype html><html lang="fr"><body style="font-family:Arial,sans-serif;color:#202124;line-height:1.7;max-width:620px;margin:24px auto;padding:20px"><p>Bectanse Académie</p><h1 style="font-size:24px">{html.escape(subject)}</h1><p>{html.escape(text)}</p>{note}<p><a href="{html.escape(self.invitation_url(t),quote=True)}" style="display:inline-block;background:#ff762f;color:#211007;padding:13px 22px;border-radius:8px;text-decoration:none">Retrouver mon parcours</a></p><p>Connecte-toi avec ton accès BCT habituel. Ne transmets jamais ton code ni tes mots de passe à un tiers.</p><p style="font-size:12px">Message de suivi de ton offre privée, sans inscription automatique à une newsletter.</p></body></html>'''
        return subject,body


def public_state(t):
    if not t:
        return None
    return {k:(utc(v).isoformat() if isinstance(v,datetime) else v) for k,v in t.items()
            if k in {'id','status','first_name','last_name','broker_email','broker_reference','phone',
                     'review_note','approved_at','ends_at','version'}}


def register_broker_trial(app, *, get_conn, get_member, login_required, admin_required,
                          send_email, send_telegram, telegram_chat_id=None, send_verification=None):
    import os
    service=BrokerTrialService(get_conn,send_email,send_telegram,
        origin=os.environ.get('BROKER_TRIAL_PUBLIC_ORIGIN','https://acces.bectanse-academie.com'),
        telegram_chat_id=telegram_chat_id)
    # Private UI files survive code-only deployments just like protected videos.
    persistent_assets=Path(app.root_path)/'persistent'/'broker-trial-assets'
    assets=persistent_assets if persistent_assets.is_dir() else Path(app.root_path)/'broker_trial_assets'
    signer=URLSafeTimedSerializer(app.secret_key,salt='private-broker-invitation-v1')
    service.invitation_url=lambda t: service.origin+'/invitation-academie/'+signer.dumps({'trial_id':t['id']})

    @app.get('/invitation-academie/<token>')
    def broker_trial_invitation(token):
        if not enabled():
            abort(404)
        try:
            payload=signer.loads(token,max_age=60*24*3600)
        except (BadSignature,SignatureExpired):
            abort(404)
        if not isinstance(payload,dict) or set(payload)!={'trial_id'}:
            abort(404)
        if not session.get('member_code'):
            session['pending_broker_invitation']=token
            return redirect('/')
        t=service.state(session['member_code'])
        if not t or t['id']!=payload['trial_id']:
            abort(404)
        session['broker_trial_invitation_id']=t['id']
        return redirect('/offre-decouverte')

    def enabled():
        return os.environ.get('BROKER_TRIAL_ENABLED','0')=='1'

    def csrf_token():
        if 'broker_trial_csrf' not in session:
            session['broker_trial_csrf']=secrets.token_urlsafe(32)
        return session['broker_trial_csrf']

    def require_write(admin=False):
        # No secret in query strings. Cookie-authenticated mutations require CSRF.
        if not enabled():
            raise TrialError('Offre privée temporairement indisponible',503)
        if admin and not session.get('admin_authenticated'):
            raise TrialError('Session administrateur nécessaire',403)
        expected=session.get('broker_trial_csrf','')
        supplied=request.headers.get('X-CSRF-Token','')
        if not expected or not supplied or not secrets.compare_digest(expected,supplied):
            raise TrialError('Session expirée, recharge la page',403)
        if request.content_length and request.content_length>8192:
            raise TrialError('Demande trop volumineuse',413)
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            raise TrialError('Requête JSON nécessaire',400)
        return data

    def actor():
        if 'broker_trial_actor' not in session:
            session['broker_trial_actor']='admin:'+secrets.token_hex(8)
        return session['broker_trial_actor']

    def perform(fn):
        try:
            return jsonify(ok=True,state=public_state(fn()))
        except TrialError as exc:
            return jsonify(ok=False,error=str(exc)),exc.status
        except Exception as exc:
            # pg8000 uniqueness errors: rollback has already occurred.
            info=exc.args[0] if exc.args and isinstance(exc.args[0],dict) else {}
            if info.get('C')=='23505':
                return jsonify(ok=False,error='Cette identité, ce compte ou ce dépôt possède déjà un dossier'),409
            app.logger.error('Broker trial operation failed: %s',type(exc).__name__)
            return jsonify(ok=False,error='Impossible de traiter la demande. Réessaie plus tard.'),503

    def invited_member():
        code=session.get('member_code','')
        t=service.state(code)
        if not enabled() or not t or session.get('broker_trial_invitation_id')!=t['id']:
            abort(403)
        return code,t

    def presentation_access():
        if discovery['access']():
            return
        invited_member()

    @app.after_request
    def private_trial_headers(response):
        if request.path.startswith(('/invitation-academie/','/offre-decouverte','/api/offre-decouverte','/admin/essais-broker','/admin/api/essais-broker','/decouverte/','/accesgratuit')):
            response.headers['Cache-Control']='private, no-store'
            response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='same-origin'
        return response

    @app.get('/offre-decouverte')
    @login_required
    def broker_trial_page():
        code,t=invited_member()
        member=get_member(code)
        response=app.make_response(render_template('broker_trial.html',trial=public_state(t),
            csrf=csrf_token(),member=member,whatsapp_url=_whatsapp_url()))
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; media-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/offre-decouverte/assets/<path:filename>')
    def broker_trial_asset(filename):
        presentation_access()
        allowed={'guide.js','videos.js','experience.js','premium.css','studio.css','academy-colors.css','trial-client.js','broker-trial-refinement.css','broker-trial-refinement.js','broker-trial-alternating-sections.css','broker-trial-art-direction.css','broker-trial-testimonials.css'}
        media={'academie','copy-trading','objectifs','bases-trading','broker','presentation','inscription'}
        safe=(filename in allowed or filename.startswith('assets/') and Path(filename).suffix.lower() in {'.png','.jpg','.webp','.svg'}
              or filename.startswith('media/') and Path(filename).stem in media and Path(filename).suffix.lower() in {'.mp4','.jpg'}
              or filename.startswith('testimonials/') and Path(filename).suffix.lower() in {'.json','.jpg','.jpeg','.png','.webp','.mp3','.mp4'})
        if not safe:
            abort(404)
        return send_from_directory(assets,filename,conditional=True)

    @app.get('/offre-decouverte/assets/media.json')
    def broker_trial_media():
        presentation_access()
        return jsonify(videos=[{'id':key,'src':f'media/{key}.mp4','poster':f'media/{key}.jpg'}
            for key in ('academie','copy-trading','objectifs','bases-trading','broker','inscription')])

    @app.get('/api/offre-decouverte')
    @login_required
    def broker_trial_state():
        _,t=invited_member()
        return jsonify(ok=True,state=public_state(t),csrf=csrf_token(),whatsapp_url=_whatsapp_url(),broker_url=BROKER_URL)

    @app.post('/api/offre-decouverte/<action>')
    @login_required
    def broker_trial_member_action(action):
        invited_member()
        return perform(lambda:service.member_action(session['member_code'],action,require_write()))

    @app.get('/admin/essais-broker')
    @admin_required
    def broker_trial_admin():
        if not session.get('admin_authenticated'):
            abort(403)
        csrf_token()
        actor()
        return render_template('admin_broker_trials.html',csrf=csrf_token(),enabled=enabled())

    @app.get('/admin/api/essais-broker')
    @admin_required
    def broker_trial_admin_list():
        if not session.get('admin_authenticated'):
            abort(403)
        c=get_conn()
        try:
            trials=c.run('''SELECT t.*,m.nom,m.email FROM broker_trials t JOIN members m ON m.code=t.member_code
                ORDER BY t.updated_at DESC LIMIT 200''')
            columns=[col['name'] for col in c.columns]
            trials=[dict(zip(columns,row)) for row in trials]
            for trial in trials:
                trial['invitation_url']=service.invitation_url(trial)
            candidates=c.run('''SELECT code,nom,email FROM members WHERE access_level IN ('explorer','demo')
                AND email_verified_at IS NOT NULL AND code<>'BCT-DEMO2026' AND NOT admin_suspended
                AND COALESCE(stripe_subscription_id,'')='' AND manual_access_until IS NULL
                AND NOT EXISTS(SELECT 1 FROM broker_trials t WHERE t.member_code=members.code)
                ORDER BY created_at DESC LIMIT 200''')
            jobs=c.run("SELECT status,COUNT(*) FROM broker_trial_outbox GROUP BY status")
            failures=c.run("SELECT id,trial_id,event,channel,attempts,last_error FROM broker_trial_outbox WHERE status='failed' ORDER BY id DESC LIMIT 50")
        finally:
            c.close()
        return jsonify(ok=True,trials=trials,candidates=[dict(zip(('code','nom','email'),r)) for r in candidates],
                       outbox=dict(jobs),failures=[dict(zip(('id','trial_id','event','channel','attempts','error'),r)) for r in failures])

    @app.get('/admin/api/essais-broker/<code>/history')
    @admin_required
    def broker_trial_history(code):
        if not session.get('admin_authenticated'):
            abort(403)
        c=get_conn()
        try:
            rows=c.run("""SELECT e.actor,e.event,e.details,e.created_at FROM broker_trial_events e
                JOIN broker_trials t ON t.id=e.trial_id WHERE t.member_code=:code ORDER BY e.id""",code=code)
        finally:
            c.close()
        return jsonify(ok=True,events=[dict(zip(('actor','event','details','created_at'),row)) for row in rows])

    @app.post('/admin/api/essais-broker/invite')
    @admin_required
    def broker_trial_invite():
        def invoke():
            data=require_write(admin=True)
            if set(data)!={'member_code'}:
                raise TrialError('Indique uniquement le compte Explorer à inviter',400)
            return service.invite(clean(data['member_code'],'Compte BCT',32),actor())
        return perform(invoke)

    @app.post('/admin/api/essais-broker/<code>/<action>')
    @admin_required
    def broker_trial_review(code,action):
        return perform(lambda:service.review(code,action,require_write(admin=True),actor()))

    @app.post('/admin/api/essais-broker/outbox/retry')
    @admin_required
    def broker_trial_retry():
        def invoke():
            data=require_write(admin=True)
            if set(data)!={'id'} or type(data['id']) is not int:
                raise TrialError('Notification invalide',400)
            with transaction(get_conn) as c:
                job=row_dict(c,"UPDATE broker_trial_outbox SET status='pending',attempts=0,retry_at=NOW(),last_error='' WHERE id=:id AND status='failed' RETURNING *",id=data['id'])
                if not job:
                    raise TrialError('Notification non relançable')
                service._audit(c,{'id':job['trial_id']},actor(),'notification_retry',{'outbox_id':data['id']})
            return None
        return perform(invoke)

    def tick():
        if enabled():
            service.sweep()
            # A crash after the final attempt must not leave a perpetual lease.
            c=get_conn()
            try:
                c.run("UPDATE broker_trial_outbox SET status='failed',last_error='delivery_lease_expired' WHERE status='sending' AND lease_until<=NOW() AND attempts>=8")
            finally:
                c.close()
            service.dispatch()

    from broker_discovery import register_discovery
    discovery = register_discovery(app,service,get_conn,get_member,admin_required,
        require_write,csrf_token,actor,enabled,send_verification)
    return {'service':service,'tick':tick,'discovery':discovery}


def _whatsapp_url():
    import os
    value=os.environ.get('BROKER_TRIAL_WHATSAPP_URL','').strip()
    parsed=urlparse(value)
    return value if parsed.scheme=='https' and parsed.netloc in {'wa.me','api.whatsapp.com'} and not parsed.username else ''
