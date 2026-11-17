"""Configuration-only protocol with a separate worker secret and replay protection."""

import os
import hmac
import secrets
from functools import wraps

from flask import jsonify, request, session

from trading_journal.security import verify_worker_request
from . import enabled


def register_copy_routes(app, service, login_required):
    @app.route("/api/copy/configuration", methods=["GET"])
    @login_required
    def copy_configuration_status():
        if not enabled():
            return jsonify({"ok": False}), 404
        try:
            session.setdefault("copy_member_csrf",secrets.token_urlsafe(32))
            response=jsonify(ok=True, csrf=session["copy_member_csrf"], **service.status(session["member_code"]))
            response.headers["Cache-Control"]="no-store"
            return response
        except Exception:
            app.logger.exception("Copy configuration status failed")
            return jsonify(ok=False, error="Configuration momentanément indisponible."), 503

    @app.post('/api/copy/activation')
    @login_required
    def copy_member_activation():
        if not enabled():return jsonify(ok=False),404
        token=session.get('copy_member_csrf','')
        if not token or not hmac.compare_digest(token,request.headers.get('X-CSRF-Token','')):
            return jsonify(ok=False,error='Rechargez la page pour confirmer votre session.'),403
        conn=service.get_conn()
        try:
            from .member_activation import command
            result=command(conn,session['member_code'],request.get_json(silent=True))
            response=jsonify(result);response.headers['Cache-Control']='no-store'
            return response,200 if result['ok'] else 409
        except ValueError as e:
            conn.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:m,:actor)',
                     a='REAL_MEMBER_COMMAND_REFUSED',m=session['member_code'],actor='member:'+session['member_code'])
            return jsonify(ok=False,error=str(e)),409
        except Exception:return jsonify(ok=False,error='Service indisponible. Aucun démarrage confirmé.'),503
        finally:conn.close()

    def worker_guard(function, *, secret_name='COPY_CONFIG_WORKER_SECRET', node_name=None):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return jsonify(ok=False), 404
            secret = os.environ.get(secret_name, "")
            data = request.get_json(silent=True)
            worker = request.headers.get("X-MT5-Worker-ID", "")
            nonce = request.headers.get("X-MT5-Nonce", "")
            if (len(secret) < 32 or not isinstance(data, dict) or not 1 <= len(worker) <= 100
                    or (node_name and worker != os.environ.get(node_name,''))
                    or data.get("worker_id") != worker or not 16 <= len(nonce) <= 128
                    or not verify_worker_request(secret, request.method, request.path,
                        request.headers.get("X-MT5-Timestamp", ""), request.get_data(cache=True),
                        request.headers.get("X-MT5-Signature", ""), nonce=nonce)):
                return jsonify(ok=False), 401
            try:
                if not service.accept_nonce(nonce):
                    return jsonify(ok=False), 401
                return function(worker, data, *args, **kwargs)
            except ValueError as error:
                return jsonify(ok=False, error=str(error)), 409
            except Exception:
                app.logger.exception("Copy configuration worker request failed")
                return jsonify(ok=False, error="Service indisponible."), 503
        return wrapped

    def real_worker_guard(function):
        return worker_guard(function,secret_name='COPY_REAL_PREPARATION_SECRET',node_name='COPY_REAL_PREPARATION_NODE')

    @app.get('/api/copy/execution')
    @login_required
    def real_execution_status():
        if not enabled():return jsonify(ok=False),404
        from .real_control import state
        conn=service.get_conn()
        try:
            response=jsonify(ok=True,**state(conn,session['member_code']))
            response.headers['Cache-Control']='no-store'
            return response
        finally:conn.close()

    @app.post('/internal/copy/real/news-calendar/publish')
    @real_worker_guard
    def real_news_calendar_publish(worker, data):
        # Public calendar only, nevertheless bounded and authenticated because
        # it changes the entry filter used by live copy execution.
        if request.content_length is None or request.content_length > 2_100_000:
            return jsonify(ok=False), 413
        from .economic_news import ingest
        result = ingest(data.get('rows'), observed_at=data.get('observed_at'),
                        source='VPS_RELAY:' + worker)
        response = jsonify(ok=True, result=result)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/internal/copy/real/execution/<action>')
    @real_worker_guard
    def real_execution_protocol(worker,data,**kwargs):
        from . import real_control
        action=request.view_args['action']
        if action not in {'poll','reserve','report','abandon'}:return jsonify(ok=False),404
        conn=service.get_conn()
        try:
            extra={'observation_key':os.environ['COPY_REAL_PREPARATION_SECRET'].encode()} if action in {'report','reserve'} else {}
            try:result=getattr(real_control,action)(conn,worker,data,**extra)
            except ValueError:
                conn.run('INSERT INTO copy_admin_events(action,target,actor) VALUES(:a,:m,:actor)',
                         a='REAL_EXECUTION_'+action.upper()+'_REFUSED',m=str(data.get('member_code',''))[:100],actor='worker:'+worker)
                raise
            response=jsonify(ok=True,result=result);response.headers['Cache-Control']='no-store'
            return response
        finally:conn.close()

    @app.post('/internal/copy/real/preflight/<action>')
    @real_worker_guard
    def real_preflight_protocol(worker,data,**kwargs):
        from . import preflight_transport
        action=request.view_args['action']
        if action not in {'publish','approved'}:return jsonify(ok=False),404
        if request.content_length is None or request.content_length>2_100_000:return jsonify(ok=False),413
        conn=service.get_conn()
        try:
            response=jsonify(ok=True,result=getattr(preflight_transport,action)(conn,worker,data))
            response.headers['Cache-Control']='no-store'
            return response
        finally:conn.close()

    @app.route("/internal/copy/configurations/claim", methods=["POST"])
    @worker_guard
    def copy_configuration_claim(worker, data):
        return jsonify(ok=True, job=service.claim(worker))

    @app.post('/internal/copy/real/preparation/claim')
    @real_worker_guard
    def real_preparation_claim(worker, data):
        if worker != os.environ.get('COPY_REAL_PREPARATION_NODE', ''):
            return jsonify(ok=False),403
        from .real_preparation import claim_plan
        role=data.get('role','preparation')
        if role not in {'preparation','executor'}:
            return jsonify(ok=False),400
        from trading_journal.security import CredentialCipher, EncryptedCredential
        def decrypt(values, account, code):
            return CredentialCipher.from_environment().decrypt(
                EncryptedCredential(*(bytes(v) for v in values)), f'trading-account:{account}:{code}')
        conn=service.get_conn()
        try:
            response=jsonify(ok=True,plan=claim_plan(conn,decrypt,data.get('member_code'),role=role))
            response.headers['Cache-Control']='no-store'
            return response
        finally:conn.close()

    @app.post('/internal/copy/real/preparation/report')
    @real_worker_guard
    def real_preparation_report(worker, data):
        if worker != os.environ.get('COPY_REAL_PREPARATION_NODE', ''):
            return jsonify(ok=False),403
        from .real_preparation import accept_observation
        conn=service.get_conn()
        try:
            result=accept_observation(conn,worker,data['job'],data['observation'],
                                      os.environ['COPY_REAL_PREPARATION_SECRET'].encode())
            return jsonify(ok=True,**result)
        finally:conn.close()

    @app.post('/internal/copy/real/preparation/failed')
    @real_worker_guard
    def real_preparation_failed(worker, data):
        from .onboarding_server import complete
        reasons={'TERMINAL_AUTH_FAILED','TERMINAL_CONNECTION_FAILED','TERMINAL_DISCONNECTED',
                 'ACCOUNT_IDENTITY_CHANGED','TERMINAL_IDENTITY_CHANGED','REAL_ACCOUNT_REQUIRED',
                 'HEDGING_REQUIRED','FIFO_UNSUPPORTED','COLLECTION_FAILED','CHECK_UNAVAILABLE'}
        reason=data.get('reason')
        if reason not in reasons:return jsonify(ok=False),400
        conn=service.get_conn()
        try:return jsonify(ok=complete(conn,data['job'],reason))
        finally:conn.close()

    @app.route("/internal/copy/configurations/ack", methods=["POST"])
    @worker_guard
    def copy_configuration_ack(worker, data):
        return jsonify(ok=True, **service.acknowledge(worker, data))
