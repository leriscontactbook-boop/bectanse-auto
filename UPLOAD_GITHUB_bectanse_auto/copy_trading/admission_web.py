"""Preparation-only HTTP adapter. No order API or engine configuration writes."""
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from pathlib import Path
from functools import wraps

from flask import jsonify, request, session
from .client_preparation import Actor, PreparationRegister, CHECKS
from .access import copy_access


from .admission_evidence import canonical, SignedEvidence, deliver_alerts


def factory_from_application(app, get_conn, get_member):
    def factory():
        path = app.config.get('ADMISSION_DATABASE_PATH')
        reader = app.extensions.get('admission_evidence_reader')
        if not path or reader is None:
            raise RuntimeError('ADMISSION_NOT_CONFIGURED')
        def accounts(account):
            c = get_conn()
            try:
                rows = c.run('''SELECT user_id,platform,broker,server,login,account_type
                    FROM trading_accounts WHERE id=:id AND provider='MT5' ''',id=account)
            finally:
                c.close()
            if not rows:return None
            data=dict(zip(('member','platform','broker','server','login','environment'),rows[0]))
            # Unknown classification cannot be inferred from a server name.
            if data['environment'] not in {'DEMO','REAL'}:data['environment']='UNKNOWN'
            return data
        r=PreparationRegister(path,account_reader=accounts,
              access_reader=lambda member:copy_access(get_member(member)),clock=time.time)
        return r,reader
    return factory


def register_admission_routes(app, factory, admin_session_valid):
    def protect(admin=False, mutate=False):
        def decorate(fn):
            @wraps(fn)
            def call(*args,**kwargs):
                if app.config.get('ADMISSION_PREPARATION_ENABLED') is not True:
                    return jsonify(ok=False),404
                is_admin = admin_session_valid() is True
                member = session.get('member_code')
                if admin and not is_admin:return jsonify(ok=False),403
                if not admin and not member:return jsonify(ok=False),401
                if mutate:
                    expected = session.get('admission_csrf')
                    provided = request.headers.get('X-CSRF-Token','')
                    if not expected or not hmac.compare_digest(expected,provided):
                        return jsonify(ok=False,error='CSRF_REQUIRED'),403
                try:
                    actor=Actor('admission-operator' if admin else member,admin)
                    return fn(actor,*args,**kwargs)
                except PermissionError:return jsonify(ok=False,error='ACCESS_DENIED'),403
                except (ValueError,KeyError,TypeError):return jsonify(ok=False,error='ADMISSION_REFUSED'),409
                except Exception:
                    # No exception payloads: collectors/DB drivers may contain secrets.
                    return jsonify(ok=False,error='ADMISSION_UNAVAILABLE'),503
            return call
        return decorate

    def observed(r, reader, actor, account):
        result=r.readiness(actor,account)
        if result['state']=='DEMO_VALIDATED':
            reason=None
            if 'SUBSCRIPTION_REQUIRED' in result['reasons']:reason='SUBSCRIPTION_EXPIRED'
            elif 'ENTITLEMENT_UNAVAILABLE' in result['reasons']:reason='REPEATED_ERRORS'
            try:
                row=r.db.execute('SELECT evidence FROM admissions WHERE account=?',(account,)).fetchone()
                identity,_=r._identity(actor,account)
                reader(json.loads(row['evidence'])['sha256'],identity)
            except Exception:
                reason=reason or 'REVIEW_REQUIRED'
            if reason:
                r.transition(Actor('admission-monitor',True),account,'SUSPENDED',reason=reason)
                result=r.readiness(actor,account)
        return result

    @app.get('/api/copy/admissions/csrf')
    @protect()
    def admission_csrf(actor):
        session.setdefault('admission_csrf',secrets.token_urlsafe(32))
        response=jsonify(ok=True,csrf_token=session['admission_csrf']);response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/admin/copy/admissions/csrf')
    @protect(admin=True)
    def admin_admission_csrf(actor):return admission_csrf.__wrapped__(actor)

    @app.route('/api/copy/admissions/<int:account>',methods=['GET','POST'])
    @protect()
    def admission_account(actor,account):
        if request.method=='POST':
            if not session.get('admission_csrf') or not hmac.compare_digest(session['admission_csrf'],request.headers.get('X-CSRF-Token','')):
                return jsonify(ok=False,error='CSRF_REQUIRED'),403
        r,reader=factory()
        try:
            if request.method=='POST':r.register(actor,account)
            response=jsonify(ok=True,**observed(r,reader,actor,account));response.headers['Cache-Control']='no-store'
            return response
        finally:r.close()

    @app.post('/api/admin/copy/admissions/<int:account>/decision')
    @protect(admin=True,mutate=True)
    def admission_decision(actor,account):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)-{'state','reason','evidence_ref'}:
            raise ValueError('INVALID_REQUEST')
        key=request.headers.get('Idempotency-Key')
        if not key:raise ValueError('IDEMPOTENCY_REQUIRED')
        r,reader=factory()
        try:
            evidence=None
            if data.get('state')=='DEMO_VALIDATED':
                current=r.readiness(actor,account)
                if any(x in current['reasons'] for x in ('SUBSCRIPTION_REQUIRED','ENTITLEMENT_UNAVAILABLE')):
                    raise ValueError('SUBSCRIPTION_REQUIRED')
                identity,_=r._identity(actor,account)
                evidence=reader(data.get('evidence_ref'),identity)
            r.transition(actor,account,data.get('state'),reason=data.get('reason','REVIEW_REQUIRED'),
                         evidence=evidence,request_id=key)
            return jsonify(ok=True,**observed(r,reader,actor,account))
        finally:r.close()

    @app.post('/api/admin/copy/admissions/<int:account>/reconcile')
    @protect(admin=True,mutate=True)
    def admission_reconcile(actor,account):
        r,reader=factory()
        try:return jsonify(ok=True,**observed(r,reader,actor,account))
        finally:r.close()

