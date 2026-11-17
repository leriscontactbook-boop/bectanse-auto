"""Short lived, account/configuration-bound native sizing evidence.

The reservation server verifies provenance and recomputes the member formula;
this receipt alone never grants execution authority or skips reconciliation.
"""
import hashlib
import hmac
import json
from .demo_policy import SafetyError, decimal
from .member_sizing import volume

FIELDS=('kind','symbol','source_symbol','side','magic','volume','sl','tp','price','reference_price','order_type','converted_pending','source_order_type')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sign(operation,settings,sizing,owner,source,now,key):
    if not isinstance(key,bytes) or len(key)<32:raise SafetyError('SIZING_SIGNER_REQUIRED')
    body=dict(schema=1,operation={k:operation.get(k) for k in FIELDS},settings=settings,
              sizing=sizing,owner=owner,source=source,observed_at=now,expires_at=now+10)
    return dict(body=body,signature=hmac.new(key,canonical(body),hashlib.sha256).hexdigest())


def verify(operation,settings,key,now):
    try:
        if not isinstance(key,bytes) or len(key)<32:raise ValueError()
        envelope=operation['sizing_receipt'];body=envelope['body']
        if not hmac.compare_digest(envelope['signature'],hmac.new(key,canonical(body),hashlib.sha256).hexdigest()):raise ValueError()
        if (body['schema']!=1 or body['settings']!=settings
            or body['owner']!=operation['owner'] or body['source']!=operation['source']
            or body['operation']!={k:operation.get(k) for k in FIELDS}
            or not body['observed_at']<=now<body['expires_at']
            or not 0<body['expires_at']-body['observed_at']<=10):raise ValueError()
        if operation.get('converted_pending') and not settings.get('convertir_pending_invalide'):raise ValueError()
        expected=volume(settings,operation['source_symbol'],body['sizing']['spec'],body['sizing']['inputs'])
        if expected!=decimal(operation['volume']):raise ValueError()
        return body
    except (KeyError,TypeError,ValueError,SafetyError):
        raise ValueError('SIZING_EVIDENCE_INVALID_OR_EXPIRED') from None
