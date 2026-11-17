"""Persistent delivery of signed native captures; no approval or trading API.

The caller supplies an explicitly reviewed instrument map and restrictions.
A failed upload is retried with the identical evidence ID. Freshness rejection
requires a new capture, never a modified timestamp or fabricated receipt.
"""
import json,time
from pathlib import Path
from .atomic_io import write_json
from .native_preflight_review import build_review

class PreflightDelivery:
    def __init__(self,backend,binding,directory,*,clock=time.time):
        self.backend,self.binding,self.clock=backend,dict(binding),clock
        self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=True)
        self.pending=self.root/'pending.json';self.receipt=self.root/'receipt.json'

    def prepare(self,manifest,*,identity,volume,reviewer,restrictions,configuration_digest,executor_digest,proposed_symbols=None):
        if self.pending.exists():return json.loads(self.pending.read_text())['evidence_id']
        import uuid
        folder=self.root/uuid.uuid4().hex;now=self.clock()
        result=build_review(folder,manifest,binding=dict(member_code=self.binding['member_code'],
            account_id=self.binding['account_id'],configuration_digest=configuration_digest,executor_digest=executor_digest),
            identity=identity,worker_id=self.backend.identity,secret=self.backend.secret,volume=volume,
            reviewer=reviewer,restrictions=restrictions,expires_at=now+3600,now=now,proposed_symbols=proposed_symbols)
        bundle=dict(evidence_id=result['evidence_id'],files={p.name:p.read_text() for p in folder.glob('*.json')})
        write_json(self.pending,bundle)
        return bundle['evidence_id']

    def deliver(self):
        if not self.pending.exists():return None
        bundle=json.loads(self.pending.read_text())
        try:
            result=self.backend.request('/internal/copy/real/preflight/publish',{**self.binding,'bundle':bundle})
        except Exception as error:
            response=getattr(error,'response',None)
            if response is not None and response.status_code==409:
                try:reason=response.json().get('error')
                except Exception:reason=None
                if reason in {'PREFLIGHT_UPLOAD_STALE','PREFLIGHT_REVIEW_EXPIRED','PREFLIGHT_CONFIGURATION_CHANGED'}:
                    # Definitive server rejection only. A timeout keeps the exact
                    # package for idempotent retry after a possibly committed upload.
                    archive=self.root/(bundle['evidence_id']+'.rejected.json')
                    write_json(archive,dict(bundle=bundle,reason=reason,at=self.clock()))
                    self.pending.unlink()
            raise
        ack=result.get('result',{})
        if (result.get('ok') is not True or ack.get('stored') is not True
            or ack.get('evidence_id')!=bundle['evidence_id'] or ack.get('execution_enabled') is not False):
            raise ValueError('PREFLIGHT_DELIVERY_UNCONFIRMED')
        # Retain the immutable package even after acknowledgement.
        write_json(self.root/(bundle['evidence_id']+'.bundle.json'),bundle)
        # Retain the acknowledgement only for the lifetime of its signed
        # evidence. A receipt proves transport, not perpetual freshness.
        proof=json.loads(bundle['files'][bundle['evidence_id']+'.json'])
        write_json(self.receipt,dict(evidence_id=bundle['evidence_id'],
            configuration_digest=proof['configuration_digest'],
            received_at=self.clock(),expires_at=proof['expires_at'],execution_enabled=False))
        self.pending.unlink()
        return ack

    def approved(self):
        result=self.backend.request('/internal/copy/real/preflight/approved',self.binding)
        if result.get('ok') is not True:raise ValueError('PREFLIGHT_APPROVAL_UNCONFIRMED')
        return result['result'].get('bundle')

class CollectorDelivery:
    """Attach delivery to the existing collection cycle while execution is stopped.

    Publication never changes the installed qualification or the symbol registry.
    Existing matrix restrictions are reused exactly, never widened by transport.
    """
    def __init__(self,collector,delivery,manifest,identity,executor_digest,*,clock=time.time,manifest_path=None):
        self.collector,self.delivery,self.manifest=collector,delivery,manifest
        self.identity,self.executor_digest,self.clock=identity,executor_digest,clock
        self.next_attempt=0
        self.manifest_path=manifest_path

    def __call__(self,snapshot,engine):
        return self.collector(snapshot,engine)

    def acknowledged_for(self, configuration_digest):
        try:
            receipt=json.loads(self.delivery.receipt.read_text(encoding='utf-8-sig'))
            expiry=receipt.get('expires_at')
            return (receipt.get('configuration_digest') == configuration_digest
                    and isinstance(expiry,(int,float)) and self.clock()<expiry)
        except (AttributeError, OSError, ValueError, TypeError):
            return False

    def publish(self,state):
        stopped=state.get('desired') in {'STOPPED','PAUSED'}
        awaiting_install=(state.get('desired')=='ACTIVE' and state.get('authorized') is False
                          and state.get('reason') in {'QUALIFICATION_CHANGED','OPERATIONAL_RELEASE_REQUIRED'}
                          and state.get('pending_operations')==0)
        active_idle=(state.get('desired')=='ACTIVE' and state.get('authorized') is True
                     and state.get('pending_operations')==0)
        if (not stopped and not awaiting_install and not active_idle) or self.clock()<self.next_attempt:return
        self.next_attempt=self.clock()+1
        from .service import digest
        if self.manifest_path is not None and not active_idle:
            approved=self.delivery.approved()
            if approved is not None:
                proof=json.loads(approved['files'][approved['evidence_id']+'.json'])
                if proof.get('executor_digest')!=self.executor_digest:approved=None
            if approved is not None:
                if install_approved(approved,self.manifest_path,identity=self.identity,
                    binding={'configuration_digest':digest(state['configuration'])},worker_id=self.delivery.backend.identity,
                    secret=self.delivery.backend.secret,now=self.clock()):raise RuntimeReviewReload()
                if not active_idle:return
        # Keep polling the authenticated approval endpoint: an ON request can
        # be released by the server after the first native-evidence receipt.
        # Once that receipt is present, however, do not create the identical
        # evidence package again on every collection cycle.
        if awaiting_install and self.acknowledged_for(digest(state['configuration'])):
            return
        matrix=json.loads(Path(self.manifest['matrix']).read_text(encoding='utf-8-sig'))
        rows=matrix.get('records',[])
        proposed_symbols = None
        discovered = False
        # A pending release must renew the *already reviewed* matrix as-is.
        # Discovering the whole broker catalogue at that point can reject an
        # unrelated contract and prevents an explicit ON request from ever
        # reaching its existing, valid mapping.  Discovery remains mandatory
        # only for an account with no reviewed mapping, and for an already
        # active account that explicitly needs a fresh extension.
        if (not self.manifest['symbols'] and not rows) or active_idle:
            from .initial_instruments import candidates
            observations = {side:json.loads(Path(self.manifest[side+'_evidence']).read_text(encoding='utf-8-sig'))['body']['qualification']
                            for side in ('source','target')}
            available={side:{name:row for name,row in rows.items() if row.get('session_open') is True
                and 0<=self.clock()-row.get('quote_at',0)<=5} for side,rows in observations.items()}
            proposed_symbols = candidates(available['source'], available['target'], self.manifest['symbols'])
            discovered = True
            if active_idle and proposed_symbols==self.manifest['symbols']:return
            # These are proposed permissions shown to the administrator, not
            # an installed qualification or authority to send an operation.
            restrictions=dict(operations=['OPEN','MODIFY','CLOSE'],filling_modes=['FOK','IOC'],
                              validity_modes=['GTC'],max_volume=state['configuration']['settings']['lots'])
        # Review only fresh pairs; retain the durable mapping for reconciliation.
        # A missing pair receives no qualification and remains denied by the gate.
        if 'source_evidence' in self.manifest and 'target_evidence' in self.manifest:
            bodies={side:json.loads(Path(self.manifest[side+'_evidence']).read_text(encoding='utf-8-sig'))['body'] for side in ('source','target')}
            def fresh(side,symbol):
                b=bodies[side];q=b.get('qualification',{}).get(symbol,{})
                return (not b.get('error') and symbol not in b.get('errors',{})
                    and q.get('connected') is True and q.get('reconciled') is True
                    and q.get('uncertain') is False and q.get('session_open') is True
                    and all(0<=self.clock()-q.get(k,0)<=5 for k in ('observed_at','quote_at','session_checked_at','reconciled_at')))
            proposed_symbols={s:t for s,t in (proposed_symbols if proposed_symbols is not None else self.manifest['symbols']).items() if fresh('source',s) and fresh('target',t)}
            if not proposed_symbols:raise ValueError('PREFLIGHT_INSTRUMENTS_REQUIRED')
        selected=[]
        for source,target in (proposed_symbols if proposed_symbols is not None else self.manifest['symbols']).items():
            matches=[r for r in rows if r.get('scope',{}).get('source_symbol')==source and r['scope'].get('target_symbol')==target]
            if not matches and discovered:
                # First-time native candidates cannot already have a reviewed row.
                # Publish their signed checks through the normal server approval
                # path; this does not install a mapping or grant execution.
                continue
            if len(matches)!=1:raise ValueError('REVIEWED_INSTRUMENT_MAPPING_REQUIRED')
            selected.append(matches[0])
        if discovered and len(selected) < len(proposed_symbols):
            # Preserve existing operation/filling restrictions for an extension.
            # Incompatible per-instrument policies still need separate review.
            selected = rows
        if selected:
            restrictions=selected[0]['restrictions']
            if any({k:v for k,v in r['restrictions'].items() if k!='max_volume'}!={k:v for k,v in restrictions.items() if k!='max_volume'} for r in selected):raise ValueError('PER_INSTRUMENT_REVIEW_REQUIRED')
        elif proposed_symbols is None:raise ValueError('REVIEWED_INSTRUMENT_MAPPING_REQUIRED')
        from .service import digest
        settings=state['configuration']['settings']
        from .preflight_volume import configured
        volume=settings['lots'] if settings['mode_risque']=='Lots fixes' else {s:str(configured(settings,s)) for s in (proposed_symbols if proposed_symbols is not None else self.manifest['symbols'])}
        if isinstance(volume,dict):
            volume={s:v for s,v in volume.items() if configured(settings,s)>0}
            proposed_symbols={s:t for s,t in (proposed_symbols or self.manifest['symbols']).items() if s in volume}
            if not volume:raise ValueError('PREFLIGHT_INSTRUMENTS_REQUIRED')
        # Requalify the exact newly configured lot size using fresh native evidence.
        # Keep operation/filling permissions; only the explicit volume changes.
        restrictions={**restrictions,'max_volume':settings['lots'] if not isinstance(volume,dict) else settings['lots_max']}
        self.delivery.prepare(self.manifest,identity=self.identity,volume=volume,
            reviewer='registered-collector-pending-admin-review',restrictions=restrictions,
            configuration_digest=digest(state['configuration']),executor_digest=self.executor_digest,
            proposed_symbols=proposed_symbols)
        self.delivery.deliver()

class RuntimeReviewReload(Exception):
    """Reviewed files changed while stopped; release session and rebuild normally."""


def install_approved(bundle,manifest_path,*,identity,binding,worker_id,secret,now):
    """Install server-approved evidence without changing lots, mapping or risk.

    The server still rechecks its current approval and member permission at every
    submission. This package is evidence, never a bearer execution permission.
    """
    import hashlib
    from .native_preflight_review import materialize_bundle as materialize
    from .native_preflight_review import validate_review
    from .compatibility import digest
    from .qualification_version import executor_digest
    path=Path(manifest_path);manifest=json.loads(path.read_text(encoding='utf-8-sig'))
    with materialize(bundle) as folder:
        raw=(folder/(bundle['evidence_id']+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=bundle['evidence_id']:raise ValueError('EVIDENCE_CHANGED')
        proof=json.loads(raw)
        validate_review(proof,folder,now=now,secrets={worker_id:secret})
        if (proof['worker_id']!=worker_id or proof['target_review']['identity']!=identity
            or proof['configuration_digest']!=binding['configuration_digest']
            or proof['executor_digest']!=executor_digest()
            or proof['master']!=manifest['master']):raise ValueError('PREFLIGHT_INSTALL_BINDING_CHANGED')
        if proof['qualification_digest']==manifest['matrix_sha256']:return False
        if proof['manifest_digest']!=digest(manifest):raise ValueError('PREFLIGHT_MANIFEST_CHANGED')
        matrix=json.loads((folder/proof['matrix']['artifact']).read_text())
        symbols={r['scope']['source_symbol']:r['scope']['target_symbol'] for r in matrix['records']}
        if any(k in manifest['symbols'] and manifest['symbols'][k]!=v for k,v in symbols.items()):raise ValueError('PREFLIGHT_MAPPING_CHANGED')
        destination=path.parent/'approved-evidence'/bundle['evidence_id'];destination.mkdir(parents=True,exist_ok=True)
        for source in folder.iterdir():
            target=destination/source.name
            if target.is_symlink() or (target.exists() and target.read_bytes()!=source.read_bytes()):
                raise ValueError('INSTALLED_EVIDENCE_CHANGED')
            if not target.exists():
                import os
                with target.open('xb') as output:
                    output.write(source.read_bytes())
                    output.flush()
                    os.fsync(output.fileno())
        updated={**manifest,'matrix':str(destination/proof['matrix']['artifact']),
                 'matrix_sha256':proof['qualification_digest'],'symbols':{**manifest['symbols'],**symbols}}
        # A byte-for-byte backup is retained; no account/financial ledger is touched.
        backup=path.with_name(path.name+'.before-'+bundle['evidence_id'])
        if not backup.exists():
            with backup.open('xb') as output:output.write(path.read_bytes())
        write_json(path,updated)
        return True
