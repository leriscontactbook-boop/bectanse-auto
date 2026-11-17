"""Member-owned emoji reactions; deliberately no comments or message publishing."""
import secrets
from flask import jsonify, request, session

EMOJIS = ('👍', '❤️', '🔥', '👏', '💯', '🙏')


def ensure_schema(conn):
    conn.run('''CREATE TABLE IF NOT EXISTS canal_reactions (
        message_id INTEGER NOT NULL REFERENCES canal_messages(id) ON DELETE CASCADE,
        member_code TEXT NOT NULL, emoji TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (message_id, member_code, emoji))''')


def register_routes(app, get_conn, login_required, academy_access_required):
    def response(conn, ids):
        rows = conn.run('''SELECT r.message_id,r.emoji,COUNT(*),
            BOOL_OR(r.member_code=:member) FROM canal_reactions r
            JOIN canal_messages m ON m.id=r.message_id
            WHERE r.message_id=ANY(CAST(:ids AS INTEGER[]))
            AND NOT COALESCE(m.deleted,FALSE)
            GROUP BY r.message_id,r.emoji''', ids=ids, member=session['member_code'])
        result = {str(i): [] for i in ids}
        for mid, emoji, count, mine in rows:
            if emoji in EMOJIS:
                result[str(mid)].append(dict(emoji=emoji, count=count, reacted=bool(mine)))
        session.setdefault('canal_reaction_csrf', secrets.token_urlsafe(32))
        reply = jsonify(reactions=result, emojis=EMOJIS, csrf=session['canal_reaction_csrf'])
        reply.headers['Cache-Control'] = 'private, no-store'
        return reply

    @app.get('/api/canal/reactions')
    @login_required
    @academy_access_required
    def canal_reaction_list():
        try:
            parts = request.args.get('ids', '').split(',')
            if len(parts) > 200:
                raise ValueError
            ids = list(dict.fromkeys(int(i) for i in parts))
            if any(i < 1 or i > 2147483647 for i in ids):
                raise ValueError
        except ValueError:
            return jsonify(error='Publications invalides'), 400
        conn = get_conn()
        try:
            return response(conn, ids)
        finally:
            conn.close()

    @app.post('/api/canal/messages/<int:message_id>/reactions')
    @login_required
    @academy_access_required
    def canal_reaction_set(message_id):
        token = session.get('canal_reaction_csrf', '')
        if not token or not secrets.compare_digest(token, request.headers.get('X-CSRF-Token', '')):
            return jsonify(error='Recharge le canal avant de réagir'), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('emoji') not in EMOJIS or type(body.get('active')) is not bool:
            return jsonify(error='Réaction invalide'), 400
        conn = get_conn()
        try:
            conn.run('BEGIN')
            exists = conn.run('''SELECT id FROM canal_messages WHERE id=:mid
                AND NOT COALESCE(deleted,FALSE) FOR SHARE''', mid=message_id)
            if not exists:
                conn.run('ROLLBACK')
                return jsonify(error='Publication indisponible'), 404
            params = dict(mid=message_id, member=session['member_code'], emoji=body['emoji'])
            if body['active']:
                conn.run('''INSERT INTO canal_reactions(message_id,member_code,emoji)
                    VALUES (:mid,:member,:emoji) ON CONFLICT DO NOTHING''', **params)
            else:
                conn.run('''DELETE FROM canal_reactions WHERE message_id=:mid
                    AND member_code=:member AND emoji=:emoji''', **params)
            conn.run('COMMIT')
            return response(conn, [message_id])
        except Exception:
            conn.run('ROLLBACK')
            raise
        finally:
            conn.close()
