"""Access dates come from settled invoice lines, never the billing calendar."""
from .policy import sid, subscription_id, utc, ReviewRequired


def invoice_paid_end(invoice, sub_id, customer_id, prices):
    if invoice.get('status') != 'paid' or int(invoice.get('amount_remaining') or 0) != 0:
        return None
    if subscription_id(invoice) != sub_id or sid(invoice.get('customer')) != customer_id:
        raise ReviewRequired('PAID_INVOICE_IDENTITY_MISMATCH')
    lines = invoice.get('lines') or {}
    if lines.get('has_more'):
        raise ReviewRequired('PAID_INVOICE_LINES_INCOMPLETE')
    ends = []
    for line in lines.get('data', []):
        price = sid(line.get('price')) or sid(((line.get('pricing') or {}).get('price_details') or {}).get('price'))
        if price not in prices:
            continue
        period = line.get('period') or {}
        start, end = utc(period.get('start')), utc(period.get('end'))
        if not start or not end or start >= end:
            raise ReviewRequired('PAID_INVOICE_PERIOD_MISSING')
        ends.append(end)
    return max(ends) if ends else None


def paid_through(api, sub_id, customer_id, prices):
    ends = []
    for invoice in api.pages('/invoices', subscription=sub_id, status='paid'):
        if (invoice.get('lines') or {}).get('has_more'):
            invoice = dict(invoice, lines={'data':list(api.pages('/invoices/'+sid(invoice)+'/lines')), 'has_more':False})
        end = invoice_paid_end(invoice, sub_id, customer_id, prices)
        if end:
            ends.append(end)
    return max(ends) if ends else None
