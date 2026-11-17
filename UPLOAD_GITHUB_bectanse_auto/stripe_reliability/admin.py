"""Extension of the existing authenticated admin, with dedicated CSRF token."""
import hashlib
import hmac
import json
import secrets
from functools import wraps
from datetime import datetime,timezone
from flask import request,session,jsonify,render_template,redirect
from .policy import utc


def register_admin(app,service,session_valid):
    def secure(fn):
        @wraps(fn)
        def wrapped(*args,**kw):
            if not session_valid():return jsonify(ok=False,error='ADMIN_REQUIRED'),403
            token=session.get('billing_csrf','')
            if request.method!='GET' and (not token or not hmac.compare_digest(token,request.headers.get('X-CSRF-Token',''))):
                return jsonify(ok=False,error='CSRF_REQUIRED'),403
            try:
                r=app.make_response(fn(*args,**kw));r.headers['Cache-Control']='no-store';return r
            except (ValueError,KeyError):return jsonify(ok=False,error='INVALID_REQUEST'),400
            except Exception:return jsonify(ok=False,error='BILLING_SERVICE_UNAVAILABLE'),503
        return wrapped

    def actor():return 'admin:'+hashlib.sha256(session['billing_csrf'].encode()).hexdigest()[:12]

    @app.get('/admin/billing')
    @secure
    def billing_admin():
        session.setdefault('billing_csrf',secrets.token_urlsafe(32))
        return render_template('admin_billing.html',csrf=session['billing_csrf'])

    @app.get('/admin/api/billing')
    @secure
    def billing_overview():
        state=request.args.get('status','')
        if state not in ('','new','in_progress','resolved','review'):raise ValueError()
        page=max(0,int(request.args.get('page','0')))
        c=service.get_conn()
        try:
            incidents=c.run("""SELECT row_to_json(i) FROM billing_incidents i
                WHERE (:status='' OR status=:status) ORDER BY checked_at DESC LIMIT 100 OFFSET :offset""",status=state,offset=page*100)
            runs=c.run('SELECT row_to_json(r) FROM billing_runs r ORDER BY started_at DESC LIMIT 10')
            actions=c.run('SELECT row_to_json(a) FROM billing_actions a ORDER BY created_at DESC LIMIT 100')
            events=c.run("SELECT status,count(*) FROM billing_events GROUP BY status")
            exceptions=c.run('SELECT row_to_json(e) FROM billing_access_exceptions e ORDER BY created_at DESC LIMIT 100')
            return jsonify(ok=True,incidents=[r[0] for r in incidents],runs=[r[0] for r in runs],
                actions=[r[0] for r in actions],events=dict(events),pending_events=[r[0] for r in c.run("SELECT row_to_json(e) FROM billing_events e WHERE status IN ('failed','review','processing') ORDER BY received_at DESC LIMIT 50")],exceptions=[r[0] for r in exceptions])
        finally:c.close()

    @app.post('/admin/api/billing/reconcile')
    @secure
    def billing_reconcile():return jsonify(ok=True,result=service.reconcile(actor()))

    @app.post('/admin/api/billing/retry')
    @secure
    def billing_retry():
        data=request.get_json();ident=int(data['incident_id']);c=service.get_conn()
        try:
            rows=c.run('SELECT subscription_id FROM billing_incidents WHERE id=:id',id=ident)
            if not rows or not rows[0][0]:raise ValueError()
            c.run("INSERT INTO billing_actions(incident_id,actor,action) VALUES(:id,:actor,'retry_requested')",id=ident,actor=actor())
        finally:c.close()
        return jsonify(ok=True,result=service.sync(rows[0][0],actor=actor()))

    @app.post('/admin/api/billing/event-retry')
    @secure
    def billing_event_retry():
        ident=str((request.get_json() or {}).get('event_id',''))
        if not ident.startswith('evt_'):raise ValueError()
        c=service.get_conn()
        try:
            if not c.run('SELECT 1 FROM billing_events WHERE event_id=:id',id=ident):raise ValueError()
            c.run("INSERT INTO billing_actions(actor,action,details) VALUES(:actor,'event_retry',CAST(:d AS JSONB))",
                  actor=actor(),d=json.dumps({'event_id':ident}))
        finally:c.close()
        return jsonify(ok=True,result=service.process_event(ident))

    @app.post('/admin/api/billing/status')
    @secure
    def billing_status_update():
        data=request.get_json();ident=int(data['incident_id']);state=data['status'];reason=str(data.get('reason','')).strip()
        if state not in ('new','in_progress','resolved','review') or not 5<=len(reason)<=500:raise ValueError()
        c=service.get_conn()
        try:
            c.run('BEGIN')
            rows=c.run("""UPDATE billing_incidents SET status=:status,
                resolved_at=CASE WHEN :status='resolved' THEN NOW() ELSE NULL END WHERE id=:id RETURNING id""",status=state,id=ident)
            if not rows:raise ValueError()
            c.run("""INSERT INTO billing_actions(incident_id,actor,action,details)
                VALUES(:id,:actor,'incident_status',CAST(:d AS JSONB))""",id=ident,actor=actor(),d=json.dumps({'status':state,'reason':reason}))
            c.run('COMMIT')
        except Exception:c.run('ROLLBACK');raise
        finally:c.close()
        return jsonify(ok=True)

    @app.post('/admin/api/billing/exception')
    @secure
    def billing_exception():
        data=request.get_json();code=str(data['member_code']);kind=data['kind'];reason=str(data['reason']).strip()
        source=str(data['source']).strip();end=utc(data['expires_at'])
        if kind not in ('external_payment','gift','manual_extension','historical','test') or not 5<=len(reason)<=500 or not 3<=len(source)<=200 or not end or end<=datetime.now(timezone.utc):raise ValueError()
        c=service.get_conn()
        try:
            c.run('BEGIN')
            member=c.run('SELECT code FROM members WHERE code=:code FOR UPDATE',code=code)
            if not member:raise ValueError()
            ident=c.run("""INSERT INTO billing_access_exceptions(member_code,kind,reason,source,actor,verified,expires_at)
                VALUES(:code,:kind,:reason,:source,:actor,TRUE,:end) RETURNING id""",code=code,kind=kind,reason=reason,source=source,actor=actor(),end=end)[0][0]
            # Documentation alone never lifts a suspension or invents an entitlement.
            # Extending access remains the existing explicit admin workflow.
            c.run("""INSERT INTO billing_actions(member_code,actor,action,details)
                VALUES(:code,:actor,'exception_documented',CAST(:d AS JSONB))""",code=code,actor=actor(),d=json.dumps({'exception_id':ident,'kind':kind,'expires_at':end.isoformat()}))
            c.run('COMMIT')
        except Exception:c.run('ROLLBACK');raise
        finally:c.close()
        return jsonify(ok=True,exception_id=ident)
