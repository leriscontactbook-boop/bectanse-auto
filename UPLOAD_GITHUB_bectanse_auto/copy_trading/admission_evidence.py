"""Headless evidence and outbox adapters; no Flask or MT5 initialization."""
import hashlib,hmac,json,math,re,time
from pathlib import Path
from .client_preparation import CHECKS

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class SignedEvidence:
    """Read reports installed by a trusted collector, never supplied as HTTP JSON.

    HMAC authenticates provenance, not truth of measurements. Collector session,
    metadata and execution checks must originate in independently reviewed MT5 code.
    """
    def __init__(self, root, key, clock=time.time):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('EVIDENCE_KEY_REQUIRED')
        self.root, self.key, self.clock = Path(root), key, clock

    def __call__(self, reference, identity):
        if not isinstance(reference, str) or not re.fullmatch('[a-f0-9]{64}', reference):
            raise ValueError('EVIDENCE_REFERENCE_INVALID')
        path = self.root / (reference + '.json')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError('EVIDENCE_UNAVAILABLE')
        document = json.loads(path.read_text())
        payload = document['payload']; encoded = canonical(payload)
        if (hashlib.sha256(encoded).hexdigest() != reference or
                not hmac.compare_digest(hmac.new(self.key, encoded, hashlib.sha256).hexdigest(),
                                        str(document.get('signature', '')))):
            raise ValueError('EVIDENCE_SIGNATURE_INVALID')
        expected = {k: identity[k] for k in ('member','platform','broker','server','login','environment')}
        if payload.get('identity') != expected or expected['environment'] != 'DEMO':
            raise ValueError('EVIDENCE_IDENTITY_INVALID')
        now = self.clock()
        # Approval report is immutable; live observation is refreshed separately.
        # A valid old review must never substitute for current connectivity.
        live_path = self.root / (hashlib.sha256(canonical(expected)).hexdigest() + '.live.json')
        if live_path.is_symlink() or not live_path.is_file() or live_path.stat().st_size > 2_000_000:
            raise ValueError('LIVE_EVIDENCE_UNAVAILABLE')
        live_document = json.loads(live_path.read_text())
        live = live_document['payload']
        if not hmac.compare_digest(hmac.new(self.key, canonical(live), hashlib.sha256).hexdigest(),
                                   str(live_document.get('signature',''))):
            raise ValueError('LIVE_SIGNATURE_INVALID')
        if live.get('identity') != expected:
            raise ValueError('LIVE_IDENTITY_INVALID')
        stamp = live.get('observed_at')
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0 <= now-stamp <= 30:
            raise ValueError('LIVE_EVIDENCE_STALE')
        for key in ('observed_at','expires'):
            value = payload.get(key)
            if type(value) not in (int,float) or not math.isfinite(value):
                raise ValueError('EVIDENCE_TIME_INVALID')
        if not 0 <= now - payload['observed_at'] <= 86400 or not now < payload['expires'] <= payload['observed_at'] + 86400:
            raise ValueError('EVIDENCE_STALE')
        if (any(live.get(k) is not True for k in ('connected','trade_allowed','reconciled'))
                or live.get('terminal_build') != payload.get('terminal_build')):
            raise ValueError('TERMINAL_NOT_READY')
        if (payload.get('kind') != 'MT5_DEMO' or payload.get('checks') != {k:True for k in CHECKS}
                or any(type(v) is not bool for v in payload['checks'].values())
                or type(payload.get('terminal_build')) is not int or payload['terminal_build'] <= 0):
            raise ValueError('EVIDENCE_CHECKS_INCOMPLETE')
        # Digests bind the reviewed symbol/volume catalog and account settings to
        # fresh observations. No equivalence inferred from instrument names.
        for field in ('catalog','settings'):
            observed, approved = live.get(field+'_observed'), payload.get(field+'_approved')
            if not isinstance(observed,str) or not re.fullmatch('[a-f0-9]{64}',observed) or observed != approved:
                raise ValueError('METADATA_CHANGED')
        return dict(sha256=reference,kind='MT5_DEMO',checks=payload['checks'],expires=payload['expires'])



def deliver_alerts(register, transport):
    """Explicit worker hook. Transport must deduplicate by id (at-least-once).
    No transport is configured or invoked automatically by this module.
    """
    delivered=0
    for row in register.db.execute('SELECT * FROM admission_alerts WHERE delivered=0 ORDER BY id LIMIT 100').fetchall():
        try:
            if transport({'id':row['id'],'account_id':row['account'],'reason':row['reason'],'at':row['at']}) is not True:
                continue
        except Exception:continue
        with register.db:
            register.db.execute('UPDATE admission_alerts SET delivered=1 WHERE id=?',(row['id'],))
        delivered+=1
    return delivered
