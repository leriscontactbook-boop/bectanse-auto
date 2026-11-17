"""Read-only copy access decision, using the existing Academy entitlement policy."""
from datetime import datetime, timezone


def trading_access_state(member, *, now=None):
    from trading_journal.entitlements import academy_membership_state, admin_trading_enabled
    if admin_trading_enabled(member):
        return True, None
    return academy_membership_state(member, now=now)


def copy_access(member, *, now=None):
    from trading_journal.entitlements import academy_membership_state, _as_utc
    now = now or datetime.now(timezone.utc)
    from trading_journal.entitlements import admin_trading_enabled
    if admin_trading_enabled(member):
        return True, now.timestamp() + 60
    allowed, manual_end = academy_membership_state(member, now=now)
    if not allowed:
        return False, now.timestamp()
    end = _as_utc(manual_end or member.get('billing_current_period_end') or member.get('date_fin'))
    if not isinstance(end, datetime):
        return False, now.timestamp()
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return True, end.timestamp()


def configuration_deadline(configuration, issued_at, now):
    """Old servers retain their existing 5s window; new grants cannot outlive access."""
    import math
    expiry = configuration.get('access_until', issued_at + 5)
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
        raise ValueError('INVALID_ACCESS_DEADLINE')
    return min(now + 3, issued_at + 5, expiry)
