"""Persistent retry budgets and bounded integrity repair, independent of job IDs."""

def retry_delay(failures):
    return min(21600, 30 * 2 ** min(max(int(failures) - 1, 0), 10))


def record_failure(conn, account_id, code):
    failures = conn.run("""INSERT INTO trading_account_retry_state
        (trading_account_id,failures,last_error_code) VALUES (:id,1,:code)
        ON CONFLICT(trading_account_id) DO UPDATE SET
        failures=LEAST(trading_account_retry_state.failures+1,100000),
        last_error_code=EXCLUDED.last_error_code,updated_at=NOW()
        RETURNING failures""", id=account_id, code=code)[0][0]
    delay = retry_delay(failures)
    conn.run("""UPDATE trading_account_retry_state SET next_attempt_at=NOW()+(:delay*INTERVAL '1 second')
        WHERE trading_account_id=:id""", id=account_id, delay=delay)
    return delay


def clear_failures(conn, account_id):
    conn.run('DELETE FROM trading_account_retry_state WHERE trading_account_id=:id', id=account_id)


def normalize_diagnostic(value):
    """Accept native integer error codes only; never persist broker error strings."""
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2147483647:
        return None
    return value


def resolve_covered_reports(conn):
    return conn.run("""UPDATE trading_reconciliation_reports r SET resolved_at=NOW()
        WHERE r.status='MISMATCH' AND r.resolved_at IS NULL AND EXISTS (
            SELECT 1 FROM trading_reconciliation_reports good
            WHERE good.trading_account_id=r.trading_account_id AND good.status='PASS'
            AND good.created_at>r.created_at AND good.range_from<=r.range_from
            AND good.range_to>=r.range_to) RETURNING r.id""")


def enqueue_integrity_repairs(conn, enqueue):
    resolve_covered_reports(conn)
    candidates = conn.run("""SELECT DISTINCT ON (r.trading_account_id)
        r.id,r.trading_account_id,r.range_from,r.range_to
        FROM trading_reconciliation_reports r
        JOIN trading_accounts a ON a.id=r.trading_account_id
        JOIN trading_credentials c ON c.trading_account_id=a.id
        WHERE r.status='MISMATCH' AND r.resolved_at IS NULL AND r.repair_attempts<3
        AND (r.last_repair_at IS NULL OR r.last_repair_at<NOW()-INTERVAL '15 minutes')
        AND a.status NOT IN ('DISCONNECTED','ACCESS_EXPIRED','AUTH_ERROR')
        AND NOT EXISTS (SELECT 1 FROM trading_account_retry_state budget
            WHERE budget.trading_account_id=a.id AND budget.next_attempt_at>NOW())
        AND NOT EXISTS (SELECT 1 FROM trading_sync_jobs j WHERE j.trading_account_id=a.id
            AND j.status IN ('PENDING','LEASED','RUNNING','RETRY'))
        ORDER BY r.trading_account_id,r.created_at LIMIT 25""")
    created = 0
    for report_id, account_id, start, end in candidates:
        if enqueue(conn, int(account_id), 'INTEGRITY_REPAIR', priority=70, range_from=start, range_to=end):
            conn.run("""UPDATE trading_reconciliation_reports SET repair_attempts=repair_attempts+1,
                last_repair_at=NOW() WHERE id=:id""", id=report_id)
            created += 1
    return created
