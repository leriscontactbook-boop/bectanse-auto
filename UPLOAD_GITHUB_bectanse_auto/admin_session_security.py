"""Bound administrator authority independently of persistent member sessions."""
import math
import time

from flask import session

ADMIN_SESSION_SECONDS = 8 * 60 * 60


def expire_admin_session():
    if not session.get('admin_authenticated'):
        return
    issued = session.get('admin_authenticated_at')
    valid = (type(issued) in (int, float) and math.isfinite(issued) and issued > 0
             and 0 <= time.time() - issued < ADMIN_SESSION_SECONDS)
    if not valid:
        session.pop('admin_authenticated', None)
        session.pop('admin_authenticated_at', None)
        for key in ('copy_admin_csrf', 'broker_trial_csrf', 'conversion_csrf'):
            session.pop(key, None)
