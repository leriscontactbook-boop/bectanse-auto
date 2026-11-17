"""Daily, auditable conversion decisions using first-party evidence.

No anonymous identity matching, invented scarcity, autonomous price changes or
unconstrained generated claims. Delivery is delegated to the existing outbox.
"""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import jsonify, render_template, request, session

PARIS = ZoneInfo('Europe/Paris')
LOCK = 81423071
STRATEGIES = {
    'checkout_help': ('Lever un blocage au paiement', '/offres',
        'Besoin d’un éclaircissement avant de rejoindre Bectanse ?',
        'Prendre une décision avec toutes les informations',
        ['Tu peux retrouver les formules et leurs conditions sur la page de présentation.',
         'Si une question te bloque, contacte notre équipe avant de choisir. Nous pouvons t’aider à comprendre le fonctionnement de l’académie.'],
        'Consulter les formules'),
    'offer_clarity': ('Clarifier l’offre', '/offres',
        'Quelle formule correspond à ton besoin ?',
        'Comprendre ce que tu rejoins',
        ['Prends le temps de comparer les contenus, les accès et les conditions de chaque formule.',
         'Le bon choix dépend de ton besoin et du temps que tu souhaites consacrer à ton apprentissage. Notre équipe peut répondre à tes questions.'],
        'Comparer les formules'),
    'product_discovery': ('Relier la découverte à l’offre', '/vip',
        'Découvrir Bectanse dans son ensemble',
        'Passer de la découverte à un choix éclairé',
        ['L’espace Explorer permet de découvrir une partie de l’univers Bectanse.',
         'La présentation de l’académie détaille les ressources et les accès disponibles. Tu peux la parcourir à ton rythme et vérifier si elle correspond à ton projet.'],
        'Découvrir l’académie'),
    'human_support': ('Proposer un échange humain', '/support',
        'Une question sur Bectanse ?',
        'Parlons de ce qui reste à clarifier',
        ['Une question sur les accès, le fonctionnement ou les formules mérite une réponse claire.',
         'Notre équipe est disponible depuis la page d’assistance pour t’aider à comprendre les prochaines étapes, sans obligation de souscrire.'],
        'Contacter l’équipe'),
}


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def decoded(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def ensure_schema(c):
    c.run('''CREATE TABLE IF NOT EXISTS conversion_agent_settings(
        id INTEGER PRIMARY KEY CHECK(id=1), enabled BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW())''')
    c.run('INSERT INTO conversion_agent_settings(id) VALUES(1) ON CONFLICT DO NOTHING')
    c.run('''CREATE TABLE IF NOT EXISTS conversion_agent_reports(
        id BIGSERIAL PRIMARY KEY, run_day DATE NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'running', created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMP, snapshots JSONB NOT NULL DEFAULT '{}',
        findings JSONB NOT NULL DEFAULT '[]', recommendations JSONB NOT NULL DEFAULT '[]',
        summary JSONB NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')''')
    c.run('''CREATE TABLE IF NOT EXISTS conversion_agent_actions(
        id BIGSERIAL PRIMARY KEY, report_id BIGINT NOT NULL REFERENCES conversion_agent_reports(id),
        member_code TEXT NOT NULL, email_key TEXT NOT NULL, strategy TEXT NOT NULL,
        variant TEXT NOT NULL, reason TEXT NOT NULL, evidence JSONB NOT NULL,
        content JSONB NOT NULL, stage TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'queued',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(), expires_at TIMESTAMP NOT NULL,
        UNIQUE(report_id,email_key))''')
    c.run('CREATE INDEX IF NOT EXISTS conversion_agent_queue_idx ON conversion_agent_actions(member_code,state,expires_at)')
    c.run('''CREATE TABLE IF NOT EXISTS conversion_agent_audit(
        id BIGSERIAL PRIMARY KEY, created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        action TEXT NOT NULL, actor TEXT NOT NULL)''')


def observations(snapshot):
    k = snapshot.get('kpis', {})
    findings = [f"{k.get('visitors', 0)} visiteurs, {k.get('sessions', 0)} sessions et {k.get('page_views', 0)} pages vues sur 7 jours.",
        f"{k.get('prospect_visitors', 0)} visiteurs prospects ; {k.get('active_member_visitors', 0)} membres actifs. Le trafic membre ne sert pas de dénominateur de conversion.",
        f"{k.get('explorer_activations', 0)} confirmations Explorer, {k.get('checkout_starts', 0)} sessions de paiement et {k.get('confirmed_payments', 0)} paiements initiaux confirmés.",
        f"{k.get('single_page_rate', 0)} % de sessions à une page ; {k.get('avg_active_seconds', 0)} secondes actives moyennes ; profondeur moyenne {k.get('avg_scroll_depth', 0)} %.",
        f"{k.get('anonymous_visitors', 0)} visiteurs anonymes : analyse agrégée uniquement, aucune relance individuelle possible."]
    for key,label in [('sources','Source principale'),('pages','Page la plus consultée'),('entry_pages','Principale entrée'),('exit_pages','Principale sortie'),('devices','Appareil principal')]:
        items=snapshot.get(key,[])
        if items:
            top=max(items,key=lambda x:x.get('value',0))
            findings.append(f"{label} : {top.get('label','Non renseigné')} ({top.get('value',0)} observations). Le volume seul ne prouve pas la qualité ni la cause d’un abandon.")
    recommendations = []
    if k.get('prospect_visitors', 0) < 100:
        recommendations.append({'priority':'Mesure', 'action':'Accumuler davantage de visites qualifiées avant de conclure à une hausse ou une baisse de conversion.', 'measure':'Au moins 100 visiteurs prospects ; ce seuil de lecture ne constitue pas une preuve statistique.'})
    if k.get('checkout_starts', 0) > k.get('confirmed_payments', 0):
        recommendations.append({'priority':'Paiement', 'action':'Proposer une aide aux prospects vérifiés ayant un paiement commencé sans achat confirmé.', 'measure':'Paiements confirmés dans les 7 jours suivant le message ; association temporelle, pas causalité démontrée.'})
    if k.get('single_page_rate', 0) >= 60:
        recommendations.append({'priority':'Parcours', 'action':'Tester un appel à l’action plus explicite sur les principales pages d’entrée et examiner le rendu mobile.', 'measure':'Sessions engagées, clics vers l’offre et confirmations Explorer ; modification du site à préparer séparément.'})
    if k.get('explorer_activations', 0) > k.get('checkout_starts', 0):
        recommendations.append({'priority':'Offre', 'action':'Expliquer les formules aux Explorers revenus sur la présentation, sans relancer les comptes inactifs sans signal récent.', 'measure':'Passages Explorer → paiement commencé → paiement confirmé.'})
    if not recommendations:
        recommendations.append({'priority':'Apprentissage','action':'Poursuivre les variantes de message et observer les parcours avant de modifier la stratégie.', 'measure':'Clics, paiements confirmés, désinscriptions et rebonds.'})
    return findings, recommendations


def choose_strategy(e):
    if e.get('checkout'):
        return 'checkout_help', 'Session de paiement non convertie, créée entre 2 heures et 7 jours.'
    if e.get('support'):
        return 'human_support', 'Consultation récente de la page d’assistance ; proposer une réponse humaine.'
    if e.get('offer_views', 0) >= 2:
        return 'offer_clarity', 'Au moins deux consultations de la présentation sur les sept derniers jours.'
    if e.get('sessions', 0) >= 2 and e.get('engaged', 0):
        return 'product_discovery', 'Plusieurs sessions et au moins un signal d’engagement récents.'
    return None, 'Signaux insuffisants : aucun e-mail supplémentaire.'


def choose_variant(code, strategy, day, performance):
    """Explore both variants; never declare a small-sample winner."""
    a, b = performance.get(strategy+'-a', {}), performance.get(strategy+'-b', {})
    if min(a.get('sent', 0), b.get('sent', 0)) >= 30:
        ar = a.get('converted', 0) / a['sent']; br = b.get('converted', 0) / b['sent']
        if abs(ar-br) >= .05:
            # Keep 20% exploration. This is a heuristic allocation, not an A/B significance claim.
            bucket = int(hashlib.sha256(f'{code}:{day}'.encode()).hexdigest()[:8], 16) % 10
            if bucket >= 2:
                return 'a' if ar > br else 'b'
    return 'ab'[int(hashlib.sha256(f'{code}:{strategy}:{day}'.encode()).hexdigest()[:8],16) % 2]


def make_content(strategy, variant, stage):
    label, path, subject, title, body, cta = STRATEGIES[strategy]
    if variant == 'b':
        subject = 'Bectanse : avançons à ton rythme'
        body = ['Avant de choisir, assure-toi d’avoir les réponses à tes questions.'] + body
    return {'stage':stage, 'subject':subject, 'preheader':label, 'eyebrow':'BECTANSE ACADÉMIE',
        'title':title, 'body':body, 'highlight':'Choisis uniquement si le contenu et les conditions correspondent à ton besoin.',
        'cta':cta, 'target_url':('https://t.me/lerisluketobot' if strategy == 'human_support' else 'https://acces.bectanse-academie.com/offres'), 'show_support':True,
        'disclaimer':'Le trading comporte des risques de perte. Aucun résultat financier n’est garanti.'}


# A matching verified e-mail is required. Anonymous sessions are never inferred
# from IP, device or fingerprint. Multiple accounts share suppression and caps.
ELIGIBLE = '''SELECT mc.member_code,mc.email,mc.first_name FROM marketing_contacts mc
 JOIN members m ON m.code=mc.member_code
 WHERE mc.segment='explorer' AND mc.unsubscribed_at IS NULL AND mc.consent_at IS NOT NULL
 AND mc.consent_source='double-opt-in-explorer' AND m.email_verified_at IS NOT NULL
 AND COALESCE(m.access_level,'member')='explorer' AND NOT COALESCE(m.admin_suspended,FALSE)
 AND COALESCE(m.stripe_subscription_id,'')='' AND LOWER(TRIM(m.email))=LOWER(TRIM(mc.email))
 AND m.code NOT LIKE 'TEST-%' AND m.code NOT LIKE 'BCT-DEMO%' AND m.code NOT LIKE 'BCT-APPREVIEW%'
 AND NOT EXISTS(SELECT 1 FROM marketing_contacts other WHERE LOWER(other.email)=LOWER(mc.email)
   AND (other.unsubscribed_at IS NOT NULL OR other.segment IN ('active','expiring','suspended')))
 AND NOT EXISTS(SELECT 1 FROM members other WHERE LOWER(other.email)=LOWER(mc.email)
   AND (COALESCE(other.admin_suspended,FALSE) OR COALESCE(other.stripe_subscription_id,'')<>''
        OR (COALESCE(other.access_level,'member')='member' AND other.actif=TRUE)))
 AND NOT EXISTS(SELECT 1 FROM marketing_legacy_leads l WHERE LOWER(l.email)=LOWER(mc.email)
   AND (l.unsubscribed_at IS NOT NULL OR l.status='suppressed'))
 AND NOT EXISTS(SELECT 1 FROM stripe_academy_events p WHERE p.member_code=m.code AND p.status='processed'
   AND (p.event_type='invoice.paid' OR (p.event_type='checkout.session.completed' AND p.payment_status IN ('paid','no_payment_required'))))'''


def evidence(c, code):
    r=c.run('''SELECT COUNT(DISTINCT session_id) AS sessions,
      COUNT(*) FILTER(WHERE event_name='page_view' AND page_path LIKE '/vip%') AS offer_views,
      COUNT(*) FILTER(WHERE event_name='page_engaged') AS engaged,
      COUNT(*) FILTER(WHERE event_name='page_view' AND page_path LIKE '/support%') AS support,MAX(created_at) AS last_seen
      FROM analytics_events WHERE member_code=:code AND created_at>NOW()-INTERVAL '7 days' ''',code=code)[0]
    checkout=bool(c.run('''SELECT 1 FROM marketing_checkout_intents WHERE member_code=:code
      AND status='open' AND stripe_session_id<>'' AND started_at<NOW()-INTERVAL '2 hours'
      AND started_at>NOW()-INTERVAL '7 days' LIMIT 1''',code=code))
    return {'sessions':int(r[0] or 0),'offer_views':int(r[1] or 0),'engaged':int(r[2] or 0),
        'support':bool(r[3]),'last_seen':str(r[4] or ''),'checkout':checkout}


def contact_allowed(c, code, email):
    settings=c.run('SELECT weekly_contact_limit,min_gap_hours FROM marketing_settings WHERE id=1')[0]
    row=c.run('''SELECT COUNT(*) FILTER(WHERE status='sent' AND sent_at>NOW()-INTERVAL '7 days') AS weekly_sent,
      COUNT(*) FILTER(WHERE (status='sent' AND sent_at>NOW()-INTERVAL '1 hour'*:gap) OR status IN ('pending','unknown')) AS cooling,
      COUNT(*) FILTER(WHERE journey='conversion_agent' AND status='sent' AND sent_at>NOW()-INTERVAL '7 days') AS agent_sent
      FROM marketing_email_log WHERE LOWER(recipient_email)=LOWER(:email)''',email=email,gap=max(48,int(settings[1])))[0]
    if row[0]>=int(settings[0]) or row[1] or row[2]>=2:return False
    return not c.run('''SELECT 1 FROM renewal_email_log r JOIN members m ON m.code=r.member_code
        WHERE LOWER(m.email)=LOWER(:email) AND r.status='sent'
          AND r.sent_at>NOW()-INTERVAL '1 hour'*:gap LIMIT 1''',email=email,gap=max(48,int(settings[1])))


def performance(c):
    # Compare fully observed seven-day windows only. A payment after a send is
    # associated with that send, not proof the e-mail caused the purchase.
    rows=c.run('''SELECT a.strategy,a.variant,COUNT(*) AS sent,
       COUNT(*) FILTER(WHERE l.clicked_at IS NOT NULL) AS clicked,
       COUNT(*) FILTER(WHERE EXISTS(SELECT 1 FROM stripe_academy_events p
         WHERE p.member_code=a.member_code AND p.status='processed'
         AND p.processed_at>=l.sent_at AND p.processed_at<l.sent_at+INTERVAL '7 days'
         AND NOT EXISTS(SELECT 1 FROM marketing_email_log later WHERE later.member_code=a.member_code
             AND later.journey='conversion_agent' AND later.status='sent' AND later.sent_at>l.sent_at AND later.sent_at<=p.processed_at)
         AND (p.event_type='invoice.paid' OR (p.event_type='checkout.session.completed'
              AND p.payment_status IN ('paid','no_payment_required'))))) AS converted
       FROM conversion_agent_actions a JOIN marketing_email_log l
        ON l.journey='conversion_agent' AND l.stage=a.stage AND l.member_code=a.member_code
       WHERE l.status='sent' AND l.sent_at<NOW()-INTERVAL '7 days' AND l.sent_at>NOW()-INTERVAL '60 days'
       GROUP BY a.strategy,a.variant''')
    return {r[0]+'-'+r[1]:{'sent':int(r[2]),'clicked':int(r[3]),'converted':int(r[4])} for r in rows}


def delivery_healthy(c):
    row=c.run("""SELECT COUNT(*) AS sent,
      COUNT(*) FILTER(WHERE provider_status IN ('hardbounce','invalid','blocked')) AS rejected,
      COUNT(*) FILTER(WHERE provider_status='spam') AS complaints
      FROM marketing_email_log WHERE journey='conversion_agent'
      AND created_at>NOW()-INTERVAL '7 days' AND status='sent'""")[0]
    return not row[2] and not (row[0]>=20 and row[1]>=3 and row[1]/row[0]>=.05)


def candidate(c, contact):
    """Called by the existing sender, immediately before its ordinary journeys."""
    code,email=contact[:2]
    if contact[3]!='explorer' or not delivery_healthy(c):return None
    if not c.run('SELECT enabled FROM conversion_agent_settings WHERE id=1')[0][0]:return None
    rows=c.run('''SELECT id,strategy,content,stage,created_at FROM conversion_agent_actions
      WHERE member_code=:code AND state='queued' AND expires_at>NOW()
      AND report_id IN (SELECT id FROM conversion_agent_reports WHERE status='completed')
      ORDER BY created_at DESC LIMIT 1''',code=code)
    if not rows:return None
    ident,strategy,content,stage,created=rows[0]
    if not c.run(ELIGIBLE+' AND mc.member_code=:code',code=code):
        c.run("UPDATE conversion_agent_actions SET state='suppressed' WHERE id=:id",id=ident);return None
    # Do not retry an uncertain provider outcome. Human reconciliation is required.
    if c.run("SELECT 1 FROM marketing_email_log WHERE journey='conversion_agent' AND stage=:stage",stage=stage):return None
    selected,_=choose_strategy(evidence(c,code))
    if selected!=strategy:
        c.run("UPDATE conversion_agent_actions SET state='obsolete' WHERE id=:id",id=ident);return None
    if not contact_allowed(c,code,email):return None
    return 'conversion_agent',decoded(content),str(ident),created


class ConversionAgent:
    def __init__(self,get_conn,analytics,dispatch,advisor=None):
        self.get_conn,self.analytics,self.dispatch=get_conn,analytics,dispatch
        self.advisor=advisor

    def run(self,manual=False):
        now=datetime.now(PARIS)
        if not manual and (now.hour,now.minute)<(9,15):return {'ok':True,'not_due':True}
        c=self.get_conn();report_id=None
        try:
            ensure_schema(c)
            if not c.run('SELECT pg_try_advisory_lock(:id)',id=LOCK)[0][0]:return {'ok':True,'busy':True}
            if not c.run('SELECT enabled FROM conversion_agent_settings WHERE id=1')[0][0]:return {'ok':True,'paused':True}
            rows=c.run('''INSERT INTO conversion_agent_reports(run_day) VALUES(:day)
                ON CONFLICT(run_day) DO UPDATE SET status='running',error='',created_at=NOW()
                WHERE conversion_agent_reports.status='failed'
                  OR (conversion_agent_reports.status='running' AND conversion_agent_reports.created_at<NOW()-INTERVAL '30 minutes')
                RETURNING id''',day=now.date())
            if not rows:return {'ok':True,'already_done':True}
            report_id=int(rows[0][0])
            snapshots={period:self.analytics(period) for period in ('1d','7d','30d')}
            if any(not x.get('ok') for x in snapshots.values()):raise RuntimeError('ANALYTICS_UNAVAILABLE')
            # Only aggregates are retained in the report; individual evidence is
            # restricted to the explicitly selected, identified prospect.
            for snap in snapshots.values():snap.pop('visitors',None)
            findings,recommendations=observations(snapshots['7d'])
            perf=performance(c)
            insight=self.advisor(snapshots,perf) if self.advisor else {}
            priorities=insight.get('priorities',list(STRATEGIES))
            recommendations+=insight.get('experiments',[])
            from marketing_automation import sync_marketing_segments
            sync_marketing_segments(c)
            settings=c.run('SELECT enabled,daily_send_limit,batch_limit FROM marketing_settings WHERE id=1')[0]
            candidates=c.run(ELIGIBLE+''' AND (EXISTS(SELECT 1 FROM analytics_events e WHERE e.member_code=m.code
                 AND e.created_at>NOW()-INTERVAL '7 days') OR EXISTS(SELECT 1 FROM marketing_checkout_intents i
                 WHERE i.member_code=m.code AND i.started_at>NOW()-INTERVAL '7 days'))
                 ORDER BY mc.created_at DESC LIMIT 1000''')
            considered=0;queued=0;skipped=0;seen=set()
            healthy=delivery_healthy(c)
            cap=min(30,int(settings[1]),int(settings[2])) if settings[0] and healthy else 0
            c.run("UPDATE conversion_agent_actions SET state='expired' WHERE state='queued' AND expires_at<=NOW()")
            ranked=[]
            for code,email,name in candidates:
                ev=evidence(c,code);strategy,reason=choose_strategy(ev)
                rank=priorities.index(strategy) if strategy in priorities else len(priorities)
                ranked.append((rank,code,email,name,ev,strategy,reason))
            ranked.sort(key=lambda x:x[0])
            for _rank,code,email,name,ev,strategy,reason in ranked:
                emailkey=hashlib.sha256(str(email).strip().lower().encode()).hexdigest()
                if emailkey in seen:continue
                seen.add(emailkey);considered+=1
                if not strategy or not contact_allowed(c,code,email) or queued>=cap:
                    skipped+=1;continue
                # A queued action on a previous day still owns this contact.
                if c.run("SELECT 1 FROM conversion_agent_actions WHERE email_key=:key AND state='queued' AND expires_at>NOW()",key=emailkey):
                    skipped+=1;continue
                variant=choose_variant(code,strategy,now.date(),perf)
                stage=f'{report_id}-{emailkey[:12]}-{strategy}-{variant}'
                content=make_content(strategy,variant,stage)
                added=c.run('''INSERT INTO conversion_agent_actions(report_id,member_code,email_key,strategy,variant,reason,evidence,content,stage,expires_at)
                   VALUES(:report,:code,:key,:strategy,:variant,:reason,CAST(:evidence AS JSONB),CAST(:content AS JSONB),:stage,NOW()+INTERVAL '36 hours')
                   ON CONFLICT(report_id,email_key) DO NOTHING RETURNING id''',report=report_id,code=code,key=emailkey,strategy=strategy,
                   variant=variant,reason=reason,evidence=dump(ev),content=dump(content),stage=stage)
                queued+=bool(added)
            queued=int(c.run('SELECT COUNT(*) FROM conversion_agent_actions WHERE report_id=:id',id=report_id)[0][0])
            summary={'considered':considered,'queued':queued,'skipped':skipped,'marketing_enabled':bool(settings[0]),'delivery_healthy':bool(healthy),
                     'performance':perf,'ai':insight,'decision_engine':'Règles de parcours et allocation adaptative des variantes ; aucune causalité revendiquée.'}
            c.run('''UPDATE conversion_agent_reports SET snapshots=CAST(:snapshots AS JSONB),findings=CAST(:findings AS JSONB),
                recommendations=CAST(:recommendations AS JSONB),summary=CAST(:summary AS JSONB),status='completed',completed_at=NOW() WHERE id=:id''',
                snapshots=dump(snapshots),findings=dump(findings),recommendations=dump(recommendations),summary=dump(summary),id=report_id)
            c.run("INSERT INTO conversion_agent_audit(action,actor) VALUES('daily_report',:actor)",actor='admin' if manual else 'scheduler')
        except Exception:
            logging.exception('Conversion agent run failed')
            if report_id:
                c.run("UPDATE conversion_agent_reports SET status='failed',error='Analyse indisponible. Aucun nouvel envoi déclenché par cette exécution.' WHERE id=:id",id=report_id)
            return {'ok':False,'error':'Analyse indisponible ; consulter le rapport.'}
        finally:
            try:c.run('SELECT pg_advisory_unlock(:id)',id=LOCK)
            finally:c.close()
        # The dispatcher respects the global pause, business hours, daily caps,
        # suppression and existing deduplication. No separate Brevo pipeline.
        try:delivery=self.dispatch()
        except Exception:
            logging.exception('Conversion dispatcher failed');delivery={'ok':False,'error':'Envoi différé ; consulter le moteur marketing.'}
        return {'ok':True,'report_id':report_id,'queued':queued,'delivery':delivery}


def register(app, agent, session_valid):
    @app.after_request
    def conversion_private_response(response):
        if request.path.startswith(('/admin/conversion-agent','/admin/api/conversion-agent')):
            response.headers['Cache-Control']='private, no-store'
        return response

    def allowed(write=False):
        if not session_valid():return False
        return not write or hmac.compare_digest(session.get('conversion_csrf',''),request.headers.get('X-CSRF-Token','')) and bool(session.get('conversion_csrf'))

    @app.get('/admin/conversion-agent')
    def conversion_agent_page():
        if not allowed():return jsonify(ok=False,error='ADMIN_REQUIRED'),403
        session.setdefault('conversion_csrf',secrets.token_urlsafe(32))
        c=agent.get_conn()
        try:
            ensure_schema(c)
            settings=c.run('SELECT enabled FROM conversion_agent_settings WHERE id=1')[0]
            reports=c.run('SELECT id,run_day,status,summary,error FROM conversion_agent_reports ORDER BY run_day DESC LIMIT 30')
            chosen=request.args.get('report','')
            ident=int(chosen) if chosen.isdigit() else (reports[0][0] if reports else 0)
            report=c.run('SELECT id,run_day,status,snapshots,findings,recommendations,summary,error FROM conversion_agent_reports WHERE id=:id',id=ident)
            actions=c.run('''SELECT a.member_code,COALESCE(m.nom,a.member_code),a.strategy,a.variant,a.reason,a.evidence,
                 a.content,a.state,l.status,l.provider_status,l.sent_at,l.clicked_at,l.error,a.expires_at,
                 EXISTS(SELECT 1 FROM stripe_academy_events p WHERE p.member_code=a.member_code AND p.status='processed'
                   AND p.processed_at>=l.sent_at AND p.processed_at<l.sent_at+INTERVAL '7 days'
         AND NOT EXISTS(SELECT 1 FROM marketing_email_log later WHERE later.member_code=a.member_code
             AND later.journey='conversion_agent' AND later.status='sent' AND later.sent_at>l.sent_at AND later.sent_at<=p.processed_at)
                   AND (p.event_type='invoice.paid' OR (p.event_type='checkout.session.completed' AND p.payment_status IN ('paid','no_payment_required'))))
                 FROM conversion_agent_actions a LEFT JOIN members m ON m.code=a.member_code
                 LEFT JOIN marketing_email_log l ON l.journey='conversion_agent' AND l.stage=a.stage AND l.member_code=a.member_code
                 WHERE a.report_id=:id ORDER BY a.id''',id=ident) if report else []
            audit=c.run('SELECT created_at,action,actor FROM conversion_agent_audit ORDER BY id DESC LIMIT 15')
            stats={'sent':sum(x[8]=='sent' for x in actions),'clicked':sum(bool(x[11]) for x in actions),
                'converted':sum(bool(x[14]) for x in actions),'failed':sum(x[8] in ('failed','unknown','pending') for x in actions)}
            reports=[list(x) for x in reports]
            for x in reports:x[3]=decoded(x[3])
            if report:
                report=list(report[0])
                for i in (3,4,5,6):report[i]=decoded(report[i])
            else:report=None
            actions=[list(x) for x in actions]
            for x in actions:
                x[5]=decoded(x[5]);x[6]=decoded(x[6])
            return render_template('admin_conversion_agent.html',enabled=settings[0],reports=reports,report=report,actions=actions,
                audit=audit,stats=stats,strategies=STRATEGIES,csrf=session['conversion_csrf'])
        finally:c.close()

    @app.post('/admin/api/conversion-agent/run')
    def conversion_agent_run():
        if not allowed(True):return jsonify(ok=False,error='ADMIN_OR_CSRF_REQUIRED'),403
        result=agent.run(manual=True)
        return jsonify(result),200 if result.get('ok') else 503

    @app.post('/admin/api/conversion-agent/settings')
    def conversion_agent_settings():
        if not allowed(True):return jsonify(ok=False,error='ADMIN_OR_CSRF_REQUIRED'),403
        value=(request.get_json(silent=True) or {}).get('enabled')
        if not isinstance(value,bool):return jsonify(ok=False,error='État invalide'),400
        c=agent.get_conn()
        try:
            ensure_schema(c)
            c.run('UPDATE conversion_agent_settings SET enabled=:enabled,updated_at=NOW() WHERE id=1',enabled=value)
            c.run('INSERT INTO conversion_agent_audit(action,actor) VALUES(:action,\'admin\')',action='enabled' if value else 'paused')
        finally:c.close()
        return jsonify(ok=True,enabled=value)
