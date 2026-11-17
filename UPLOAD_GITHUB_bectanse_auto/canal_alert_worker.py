"""Independent VIP watchdog; durable incident transitions, no periodic admin spam."""
import json
import os
import signal
import threading
import time

INTERVAL = 30
STATE_KEY = 'admin_sync_incident_v1'


def transition(previous, issues):
    state = dict(previous)
    if issues:
        state['healthy_checks'] = 0
        state['issues'] = issues
        if not state.get('active'):
            state.update(active=True, opened_at=time.time())
            return state, 'incident'
    elif state.get('active'):
        state['healthy_checks'] = state.get('healthy_checks', 0) + 1
        if state['healthy_checks'] >= 2:
            state.update(active=False, issues=[])
            return state, 'recovery'
    return state, None


def inspect(conn, requests, token, expected_url):
    issues = []
    rows = conn.run("""SELECT COUNT(*),COALESCE(MAX(EXTRACT(EPOCH FROM (NOW()-created_at))),0)
        FROM canal_inbox WHERE status<>'done'""")
    count, age = rows[0]
    failed = conn.run("SELECT COUNT(*) FROM canal_inbox WHERE status='failed'")[0][0]
    if failed or (count and float(age) > 120):
        issues.append(f'{count} publication(s) en attente, dont {failed} en échec ; retard maximal {int(age)} s')
    heartbeat = conn.run("SELECT EXTRACT(EPOCH FROM (NOW()-updated_at)) FROM canal_runtime WHERE name='health'")
    if not heartbeat or float(heartbeat[0][0]) > 180:
        issues.append('Le contrôle de synchronisation ne répond plus depuis plus de 3 minutes')
    response = requests.get(f'https://api.telegram.org/bot{token}/getWebhookInfo', timeout=10).json()
    if not response.get('ok'):
        issues.append('Telegram refuse le contrôle de réception')
    else:
        info = response.get('result') or {}
        if info.get('url') != expected_url:
            issues.append('La réception Telegram est déconnectée ou dirigée vers une autre adresse')
        pending = info.get('pending_update_count', 0)
        error_time = info.get('last_error_date', 0)
        if pending and error_time and time.time() - error_time < 600:
            issues.append(f'Telegram ne parvient pas à livrer les messages : {pending} mise(s) à jour en attente')
    return issues


def check(get_conn, requests, source_token, admin_token, admin_id, expected_url, logger):
    conn = get_conn()
    try:
        # Session lock prevents duplicate sends during overlapping deployments.
        if not conn.run('SELECT pg_try_advisory_lock(73291024)')[0][0]:
            return
        try:
            issues = inspect(conn, requests, source_token, expected_url)
        except Exception as exc:
            # Persisted debounce below absorbs a single temporary network error.
            issues = ['Impossible de contrôler la synchronisation (' + type(exc).__name__ + ')']
        row = conn.run('SELECT value FROM canal_runtime WHERE name=:key', key=STATE_KEY)
        previous = json.loads(row[0][0]) if row else {}
        failures = previous.get('bad_checks', 0) + 1 if issues else 0
        if issues and failures < 2 and not previous.get('active'):
            state, event = dict(previous), None
        else:
            state, event = transition(previous, issues)
        state['bad_checks'] = failures
        if event:
            state.update(notification='attempting', event=event)
        # Claim BEFORE network IO: an ambiguous timeout must never spam on restart.
        conn.run("""INSERT INTO canal_runtime(name,value) VALUES (:key,:value)
            ON CONFLICT(name) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()""",
            key=STATE_KEY, value=json.dumps(state))
        if not event:
            return
        text = ('🚨 BECTANSE — Synchronisation Canal VIP bloquée\n\n' + '\n'.join('• '+x for x in issues)
                + '\n\nIntervention admin nécessaire. Aucun rappel répétitif ne sera envoyé pour cet incident.'
                if event == 'incident' else
                '✅ BECTANSE — Synchronisation Canal VIP rétablie\n\nLa réception et le traitement sont à nouveau opérationnels. Deux contrôles successifs sont revenus à la normale.')
        try:
            result = requests.post(f'https://api.telegram.org/bot{admin_token}/sendMessage',
                json={'chat_id': str(admin_id), 'text': text}, timeout=15).json()
            state['notification'] = 'confirmed' if result.get('ok') else 'rejected'
            state['message_id'] = (result.get('result') or {}).get('message_id')
        except Exception:
            state['notification'] = 'unknown'
        conn.run('UPDATE canal_runtime SET value=:value,updated_at=NOW() WHERE name=:key',
                 key=STATE_KEY, value=json.dumps(state))
        logger.info('VIP admin transition %s: %s', event, state['notification'])
    finally:
        conn.close()  # also releases advisory lock


def main():
    os.environ['BECTANSE_SKIP_STARTUP'] = '1'
    from app import app, get_conn, requests, CANAL_BOT_TOKEN, BOT_TOKEN, ADMIN_ID, BASE_URL
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    app.logger.info('Independent VIP admin watchdog started')
    while not stop.is_set():
        try:
            check(get_conn, requests, CANAL_BOT_TOKEN, BOT_TOKEN, ADMIN_ID,
                  BASE_URL.rstrip('/') + '/canal-webhook', app.logger)
        except Exception as exc:
            app.logger.error('VIP admin watchdog unavailable: %s', type(exc).__name__)
        stop.wait(INTERVAL)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
