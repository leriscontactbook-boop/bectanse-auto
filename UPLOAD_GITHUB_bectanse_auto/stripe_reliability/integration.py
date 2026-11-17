"""Shared wiring without importing Flask's application from domain code."""
from .service import BillingService
_service=None


def configure(get_conn,prices,after_payment=None,onboard=None):
    global _service
    _service=BillingService(get_conn,prices,after_payment=after_payment,onboard=onboard)
    return _service


def current():
    if _service is None:raise RuntimeError('BILLING_NOT_CONFIGURED')
    return _service
