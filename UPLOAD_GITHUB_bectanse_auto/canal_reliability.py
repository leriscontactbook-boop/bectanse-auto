"""Durable private-group inbox and per-device VIP notification delivery."""
import json


VIP_ELIGIBLE = """m.actif=TRUE AND NOT COALESCE(m.admin_suspended,FALSE)
    AND COALESCE(m.access_level,'member') NOT IN ('explorer','demo')
    AND COALESCE(NULLIF(m.billing_status,''),'legacy') IN ('active','trialing','legacy')
    AND COALESCE(m.billing_current_period_end,m.date_fin)>NOW()
    AND m.code<>'BCT-DEMO2026'"""


def private_message(update, group_id):
    """Accept only messages from the configured private group, including topics."""
    for key in ('message', 'edited_message'):
        message = update.get(key)
        if not isinstance(message, dict):
            continue
        chat = message.get('chat') or {}
        if str(chat.get('id')) == str(group_id) and chat.get('type') in ('group', 'supergroup'):
            return message, key == 'edited_message'
    return None, False


def ensure_schema(conn):
    # A first deployment must not depend on manually visiting the admin init route.
    conn.run("""CREATE TABLE IF NOT EXISTS canal_messages (
        id SERIAL PRIMARY KEY,tg_msg_id BIGINT UNIQUE,text_content TEXT DEFAULT '',
        msg_type TEXT DEFAULT 'message',photo_url TEXT DEFAULT '',
        edited BOOLEAN DEFAULT FALSE,sent_at TIMESTAMP DEFAULT NOW())""")
    conn.run("""CREATE TABLE IF NOT EXISTS canal_inbox (
        update_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), processed_at TIMESTAMPTZ,
        error TEXT NOT NULL DEFAULT '')""")
    conn.run("""CREATE TABLE IF NOT EXISTS canal_push_deliveries (
        id BIGSERIAL PRIMARY KEY, tg_msg_id BIGINT NOT NULL, endpoint TEXT NOT NULL,
        member_code TEXT NOT NULL, payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), delivered_at TIMESTAMPTZ,
        error TEXT NOT NULL DEFAULT '', UNIQUE(tg_msg_id,endpoint))""")
    conn.run("""CREATE INDEX IF NOT EXISTS canal_push_due_idx
        ON canal_push_deliveries(status,available_at)""")
    conn.run("""CREATE TABLE IF NOT EXISTS canal_runtime (
        name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    conn.run("ALTER TABLE canal_messages ADD COLUMN IF NOT EXISTS topic_id BIGINT")
    from canal_reactions import ensure_schema as ensure_reactions
    ensure_reactions(conn)


def enqueue_push(get_conn, message_id, title, body):
    conn = get_conn()
    try:
        conn.run('BEGIN')
        claimed = conn.run("""SELECT id FROM canal_messages WHERE tg_msg_id=:mid
            AND push_notified_at IS NULL AND NOT COALESCE(deleted,FALSE) FOR UPDATE""", mid=message_id)
        if not claimed:
            conn.run('COMMIT')
            return {'queued': 0, 'skipped': True}
        payload = json.dumps({'title': title, 'body': body, 'url': '/canal',
                              'tag': f'bectanse-vip-{message_id}'})
        rows = conn.run(f"""INSERT INTO canal_push_deliveries(tg_msg_id,endpoint,member_code,payload)
            SELECT :mid,p.endpoint,p.member_code,:payload FROM push_subscriptions p
            JOIN members m ON m.code=p.member_code WHERE {VIP_ELIGIBLE}
            ON CONFLICT(tg_msg_id,endpoint) DO NOTHING RETURNING id""", mid=message_id, payload=payload)
        conn.run('UPDATE canal_messages SET push_notified_at=NOW() WHERE tg_msg_id=:mid', mid=message_id)
        conn.run('COMMIT')
        return {'queued': len(rows), 'skipped': False}
    except Exception:
        conn.run('ROLLBACK')
        raise
    finally:
        conn.close()


def drain_push(get_conn, deliver, limit=100):
    """Claim a bounded batch. Recheck access; recover interrupted deliveries after 5 min."""
    conn = get_conn()
    try:
        conn.run("""UPDATE canal_push_deliveries SET status='expired',error='delivery window expired'
            WHERE status IN ('pending','sending') AND created_at<NOW()-INTERVAL '24 hours'""")
        rows = conn.run("""UPDATE canal_push_deliveries d SET status='sending',
            attempts=attempts+1,available_at=NOW()+INTERVAL '5 minutes'
            WHERE id IN (SELECT id FROM canal_push_deliveries
                WHERE status IN ('pending','sending') AND available_at<=NOW()
                ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED)
            RETURNING id,endpoint,member_code,payload,attempts""", limit=limit)
    finally:
        conn.close()
    result = {'accepted': 0, 'retry': 0, 'expired': 0, 'cancelled': 0, 'failed': 0}
    for delivery_id, endpoint, member, payload, attempts in rows:
        conn = get_conn()
        try:
            subscription = conn.run(f"""SELECT p.endpoint,p.p256dh,p.auth FROM push_subscriptions p
                JOIN members m ON m.code=p.member_code WHERE p.endpoint=:ep
                AND p.member_code=:member AND {VIP_ELIGIBLE}""", ep=endpoint, member=member)
            if not subscription:
                state, error = 'cancelled', 'subscription absent or access ended'
            else:
                try:
                    deliver(subscription[0], payload)
                    state, error = 'accepted', ''
                except Exception as exc:
                    response = getattr(exc, 'response', None)
                    code = getattr(response, 'status_code', None)
                    mismatch = code == 400 and 'VapidPkHashMismatch' in str(getattr(response, 'text', ''))
                    state = 'expired' if code in (404, 410) or mismatch else ('failed' if attempts >= 8 else 'retry')
                    # Exception strings can contain endpoint URLs/credentials: retain only safe diagnostics.
                    error = f'HTTP {code}' if code else type(exc).__name__
            conn.run("""UPDATE canal_push_deliveries SET status=:status,error=:error,
                delivered_at=CASE WHEN :status='accepted' THEN NOW() ELSE NULL END,
                available_at=NOW()+(:delay*INTERVAL '1 second') WHERE id=:id""",
                status='pending' if state == 'retry' else state, error=error,
                delay=min(3600, 30 * 2 ** min(attempts, 7)), id=delivery_id)
            if state == 'accepted':
                conn.run("""UPDATE push_subscriptions SET last_delivery_at=NOW(),updated_at=NOW(),
                    failure_count=0,last_error='' WHERE endpoint=:ep""", ep=endpoint)
            elif state == 'expired':
                conn.run('DELETE FROM push_subscriptions WHERE endpoint=:ep', ep=endpoint)
            elif state in ('retry', 'failed'):
                conn.run("""UPDATE push_subscriptions SET failure_count=failure_count+1,
                    last_error=:error,updated_at=NOW() WHERE endpoint=:ep""", ep=endpoint, error=error)
            result[state] += 1
        finally:
            conn.close()
    return result
