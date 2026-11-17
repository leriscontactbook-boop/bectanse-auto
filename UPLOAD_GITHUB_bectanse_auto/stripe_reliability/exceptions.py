"""Record existing manual grants atomically with their provenance."""
import json
from .policy import utc


def extend(conn, code, end, actor, source, reason):
    conn.run('BEGIN')
    try:
        conn.run("""UPDATE members SET date_fin=:df,manual_access_until=:df,
            access_level='member',actif=NOT COALESCE(admin_suspended,FALSE)
            WHERE code=:c""",df=utc(end).replace(tzinfo=None),c=code)
        conn.run("""UPDATE billing_access_exceptions SET revoked_at=NOW()
            WHERE member_code=:code AND kind='manual_extension' AND revoked_at IS NULL""",code=code)
        ident=conn.run("""INSERT INTO billing_access_exceptions(member_code,kind,reason,source,actor,verified,expires_at)
            VALUES(:code,'manual_extension',:reason,:source,:actor,TRUE,:end) RETURNING id""",
            code=code,reason=reason,source=source,actor=actor,end=utc(end))[0][0]
        conn.run("""INSERT INTO billing_actions(member_code,actor,action,details)
            VALUES(:code,:actor,'manual_extension',CAST(:details AS JSONB))""",
            code=code,actor=actor,details=json.dumps({'exception_id':ident,'expires_at':utc(end).isoformat(),'reason':reason}))
        conn.run('COMMIT')
    except Exception:
        conn.run('ROLLBACK');raise
