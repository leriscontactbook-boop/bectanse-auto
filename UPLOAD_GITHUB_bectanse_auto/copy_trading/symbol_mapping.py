"""Conservative LOCAL symbol qualification; discovery never logs in or places orders."""
import json
import sqlite3
import time
import re
from pathlib import Path
from .compatibility import digest, number, FIELDS
from .demo_policy import SafetyError

ALGORITHM = 'mapping-1'
STATUSES = {'EXACT_MATCH_CANDIDATE','AUTO_MATCH_ELIGIBLE','REQUIRES_REVIEW','AMBIGUOUS',
            'INCOMPATIBLE','NOT_FOUND','STALE_METADATA'}
EXTRA = ('description','path','volume_limit','start_time','expiration_time','basis','isin')
REQUIRED = tuple(dict.fromkeys(FIELDS + EXTRA))
ECONOMIC = ('currency_base','currency_profit','currency_margin','trade_contract_size',
            'trade_tick_value','trade_tick_value_profit','trade_tick_value_loss','trade_tick_size',
            'point','digits','trade_calc_mode','volume_min','volume_max','volume_step','volume_limit',
            'trade_exemode','filling_mode','expiration_mode','order_mode','order_gtc_mode',
            'trade_mode','trade_stops_level','trade_freeze_level','start_time','expiration_time')
IDENTITY = ('underlying','instrument_class','venue','contract_kind','expiry')


def fingerprint(row):
    return digest({k:row[k] for k in ('spec','identity','terminal','asset')})


def fresh(row, now, max_age=5):
    try:
        return 0 <= now-float(row['observed_at']) <= max_age and 0 <= now-float(row['quote_at']) <= max_age
    except (KeyError,TypeError,ValueError):
        return False


class SymbolDiscovery:
    """Already-attached terminal only. Identity records use a caller supplied account alias.

    Asset evidence is external reviewed data, not guessed from descriptions/names.
    Missing API properties are recorded explicitly. No symbol_select or login.
    """
    def __init__(self, module, *, expected_identity, terminal_path, account_ref, asset_reader, clock=time.time, broker_time_offset_seconds=0):
        from .broker_clock import offset_seconds
        self.broker_time_offset=offset_seconds(broker_time_offset_seconds)
        self.mt5,self.expected,self.path,self.ref,self.assets,self.clock=module,expected_identity,terminal_path,account_ref,asset_reader,clock

    def _identity(self):
        import ntpath
        a,t=self.mt5.account_info(),self.mt5.terminal_info()
        if a is None or t is None or not t.connected:raise SafetyError('DISCOVERY_TERMINAL_UNAVAILABLE')
        if (str(a.login),a.server,a.company)!=self.expected or ntpath.normcase(t.path)!=ntpath.normcase(self.path):
            raise SafetyError('DISCOVERY_IDENTITY_CHANGED')
        if a.trade_mode!=self.mt5.ACCOUNT_TRADE_MODE_DEMO:raise SafetyError('DISCOVERY_DEMO_ONLY')
        return {'account_ref':self.ref,'identity_hash':digest(self.expected),'broker':a.company,'server':a.server}, {
            'path_hash':digest(self.path),'build':t.build,'api_version':getattr(self.mt5,'__version__',None)}

    def collect(self):
        identity,terminal=self._identity(); catalog=self.mt5.symbols_get()
        if catalog is None:raise SafetyError('DISCOVERY_CATALOG_UNAVAILABLE')
        if len(catalog)>5000:raise SafetyError('DISCOVERY_CATALOG_LIMIT')
        rows=[];names=set()
        for item in catalog:
            name=getattr(item,'name',None)
            if not isinstance(name,str) or not name or name in names:raise SafetyError('DISCOVERY_AMBIGUOUS_CATALOG')
            names.add(name);at=self.clock();info=self.mt5.symbol_info(name);tick=self.mt5.symbol_info_tick(name)
            spec={k:getattr(info,k,None) for k in REQUIRED}
            missing=[k for k,v in spec.items() if v is None]
            try:asset=self.assets(name)
            except Exception:asset=None
            quote_valid = tick is not None and number(tick.bid)>0 and number(tick.ask)>=number(tick.bid)
            rows.append({'spec':spec,'identity':identity,'terminal':terminal,'observed_at':at,
                'quote_at':getattr(tick,'time_msc',0)/1000-self.broker_time_offset if quote_valid else None,
                'quote_time_msc_raw':getattr(tick,'time_msc',None),'broker_time_offset_seconds':self.broker_time_offset,
                'asset':asset,'missing_fields':missing,'provenance':'MT5.symbols_get/symbol_info/symbol_info_tick',
                'errors':(['SYMBOL_ABSENT'] if info is None else [])+(['QUOTE_UNAVAILABLE'] if not quote_valid else [])})
        if self._identity()!=(identity,terminal):raise SafetyError('DISCOVERY_IDENTITY_CHANGED')
        return rows


class SymbolCompatibilityValidator:
    def validate(self, source, target, now):
        if not fresh(source,now) or not fresh(target,now):return 'STALE_METADATA',['METADATA_OR_QUOTE_STALE']
        try:
            for row in (source,target):
                if not isinstance(row.get('spec'),dict):return 'REQUIRES_REVIEW',['MISSING_API_FIELDS']
                if any(row['spec'].get(k) is None for k in REQUIRED):return 'REQUIRES_REVIEW',['MISSING_API_FIELDS']
                if not row['identity'].get('identity_hash') or not row['terminal'].get('build') or not row['terminal'].get('api_version'):
                    return 'REQUIRES_REVIEW',['MISSING_TERMINAL_IDENTITY']
                asset=row['asset']
                if not asset or asset.get('reviewed') is not True or not asset.get('evidence') or any(k not in asset for k in IDENTITY):
                    return 'REQUIRES_REVIEW',['UNDERLYING_EVIDENCE_REQUIRED']
                if any(not isinstance(asset[k],str) or not asset[k] for k in IDENTITY if k!='expiry'):return 'REQUIRES_REVIEW',['UNDERLYING_EVIDENCE_REQUIRED']
                for k in ('trade_contract_size','trade_tick_value','trade_tick_size','point','volume_min','volume_max','volume_step'):
                    if number(row['spec'][k])<=0:return 'INCOMPATIBLE',['INVALID_'+k]
                if number(row['spec']['volume_min'])>number(row['spec']['volume_max']):return 'INCOMPATIBLE',['INVALID_VOLUME_RANGE']
            differences=[k for k in IDENTITY if source['asset'][k]!=target['asset'][k]]
            differences += [k for k in ECONOMIC if str(source['spec'][k])!=str(target['spec'][k])]
            if differences:return 'INCOMPATIBLE',differences
            if source['asset']['contract_kind']!='SPOT_CFD' or source['spec']['expiration_time']!=0:
                return 'REQUIRES_REVIEW',['DERIVATIVE_OR_EXPIRING_CONTRACT_POLICY_REQUIRED']
            return 'EXACT_MATCH_CANDIDATE',[]
        except (KeyError,TypeError,ValueError):return 'REQUIRES_REVIEW',['INVALID_METADATA']


class VolumeCompatibilityValidator:
    def validate(self, row, volume, filling='FOK'):
        try:
            v=number(volume);s=row['spec'];step=number(s['volume_step'])
            if step<=0 or v<number(s['volume_min']) or v>number(s['volume_max']):return 'VOLUME_RANGE'
            if v%step:return 'VOLUME_STEP'
            if filling not in ('FOK','IOC') or not s['filling_mode'] & {'FOK':1,'IOC':2}[filling]:return 'FILLING_UNSUPPORTED'
            if not s['expiration_mode'] & 1 or s['trade_mode']!=4 or not s['order_mode'] & 1:return 'EXECUTION_RULES_UNSUPPORTED'
            # Aggregate volume_limit is also enforced by the business exposure preflight.
            if number(s['volume_limit'])>0 and v>number(s['volume_limit']):return 'VOLUME_LIMIT'
            return None
        except (KeyError,TypeError,ValueError):return 'VOLUME_METADATA_INVALID'


class BrokerQualificationGate:
    """Adapter to the existing twelve-control gate; no qualification from matching."""
    def __init__(self, gate, profile, source_observer, target_observer):
        self.gate,self.profile,self.source,self.target=gate,dict(profile),source_observer,target_observer

    def evaluate(self, source, target, volume, filling='FOK'):
        scope={**self.profile,'source_symbol':source['spec']['name'],'target_symbol':target['spec']['name']}
        a,b=self.source(scope['source_symbol']),self.target(scope['target_symbol'])
        for side,row,obs in [('source',source,a),('target',target,b)]:
            if row['identity']['broker']!=scope[side+'_broker'] or row['identity']['server']!=scope[side+'_server'] or any(str(row['spec'].get(k))!=str(obs['spec'].get(k)) for k in FIELDS):
                return {'allowed':False,'reason':'DISCOVERY_QUALIFICATION_MISMATCH'}
        result=self.gate.evaluate(scope,source_reader=lambda:a,
            target_reader=lambda:b,volume=volume,filling=filling,validity='GTC')
        return result


class SymbolCandidateMatcher:
    def __init__(self):self.compatibility=SymbolCompatibilityValidator();self.volume=VolumeCompatibilityValidator()

    def rank(self, source, targets, *, now, volume, qualification):
        rows=[];source_name=source['spec']['name'];root=re.sub('[^A-Z0-9]','',source_name.upper())
        for target in targets:
            name=target['spec']['name'];other=re.sub('[^A-Z0-9]','',name.upper())
            a,b=source.get('asset') or {},target.get('asset') or {}
            signals=[]
            if name==source_name:signals.append(('exact_name',20))
            elif root and other and (root in other or other in root):signals.append(('name_affix_hint_only',5))
            if a.get('underlying') and a.get('underlying')==b.get('underlying'):signals.append(('underlying',40))
            if source['spec'].get('description') and source['spec'].get('description')==target['spec'].get('description'):signals.append(('description',5))
            if not signals:continue
            status,diffs=self.compatibility.validate(source,target,now)
            volume_error=self.volume.validate(target,volume)
            if volume_error and status=='EXACT_MATCH_CANDIDATE':status='INCOMPATIBLE';diffs.append(volume_error)
            q={'allowed':False,'reason':'NOT_EVALUATED'}
            if status=='EXACT_MATCH_CANDIDATE':
                signals.append(('specifications_equal',30))
                try:q=qualification.evaluate(source,target,volume)
                except Exception:q={'allowed':False,'reason':'QUALIFICATION_COLLECTION_FAILED'}
            rows.append({'target':name,'status':status,'score':sum(s[1] for s in signals),
                'signals':[s[0] for s in signals],'differences':diffs,'qualification':q})
        # Every plausible candidate with unexcluded economics contributes ambiguity,
        # even if its qualification is absent. Never choose on score alone.
        plausible=[r for r in rows if r['status']!='INCOMPATIBLE']
        for r in plausible:
            if len(plausible)>1:r['status']='AMBIGUOUS'
            elif r['status']=='EXACT_MATCH_CANDIDATE' and r['qualification'].get('allowed') is True:r['status']='AUTO_MATCH_ELIGIBLE'
        return sorted(rows,key=lambda r:(-r['score'],r['target'])) or [{'target':None,'status':'NOT_FOUND','score':0,'signals':[],'differences':[],'qualification':{'allowed':False}}]


class SymbolMappingRegistry:
    """Append-only decisions, transactional CAS and verified chain.

    Expected head must come from an independently preserved trusted checkpoint.
    Restoring BOTH journal and checkpoint cannot be detected without external authority.
    """
    def __init__(self,path):self.path=Path(path)
    def create(self):
        # Exclusive file creation: never silently recreate missing/corrupt journals.
        with self.path.open('xb'):pass
        with self.connect() as c:
            c.executescript('PRAGMA synchronous=FULL; CREATE TABLE decisions(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL UNIQUE);')
        return 'GENESIS'
    def connect(self):
        c=sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=5)
        c.execute('PRAGMA synchronous=FULL');return c
    def _verify(self,c,expected_head):
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise SafetyError('MAPPING_REGISTRY_CORRUPT')
        rows=c.execute('SELECT seq,payload,previous,hash FROM decisions ORDER BY seq').fetchall();head='GENESIS';result=[]
        for n,(seq,payload,previous,h) in enumerate(rows,1):
            data=json.loads(payload)
            if seq!=n or previous!=head or digest([seq,previous,data])!=h:raise SafetyError('MAPPING_REGISTRY_CORRUPT')
            head=h;result.append(data)
        if head!=expected_head:raise SafetyError('MAPPING_CHECKPOINT_MISMATCH')
        return result,head
    def append(self,record,*,expected_head):
        required={'source_account','target_account','source_symbol','target_symbol','source','target','decision',
                  'reason','reviewer','origin','qualification_revision','validated_at','review_at','algorithm'}
        if set(record)!=required or record['algorithm']!=ALGORITHM or record['origin'] not in {'HUMAN','AUTO'} or not record['reason'] or not record['reviewer']:
            raise SafetyError('MAPPING_REVIEW_INVALID')
        if record['decision'] not in {'APPROVED','REVOKED'}:raise SafetyError('MAPPING_REVIEW_INVALID')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');rows,head=self._verify(c,expected_head)
            seq=len(rows)+1;h=digest([seq,head,record]);c.execute('INSERT INTO decisions VALUES(?,?,?,?)',(seq,json.dumps(record,sort_keys=True,allow_nan=False),head,h))
        return h
    def latest(self,source_account,target_account,symbol,*,expected_head):
        try:
            with self.connect() as c:
                rows,_=self._verify(c,expected_head)
                return next((r for r in reversed(rows) if (r['source_account'],r['target_account'],r['source_symbol'])==(source_account,target_account,symbol)),None)
        except (sqlite3.Error,ValueError,OSError):raise SafetyError('MAPPING_REGISTRY_CORRUPT') from None


class MappingAdmission:
    """Per-follower opening guard. No admission/remapping of existing positions."""
    def __init__(self,registry,*,head_reader,source_account,target_account,source_catalog,target_catalog,qualification,incidents,clock=time.time):
        self.registry,self.head,self.source_account,self.target_account=registry,head_reader,source_account,target_account
        self.sources,self.targets,self.qualification,self.incidents,self.clock=source_catalog,target_catalog,qualification,incidents,clock
    def resolve(self,source_account,target_account,symbol,volume):
        incident={'source_account':digest(source_account),'target_account':digest(target_account),'source_symbol':symbol,'operation':'MAPPING_OPEN'}
        try:
            if (source_account,target_account)!=(self.source_account,self.target_account):raise SafetyError('MAPPING_ACCOUNT_MISMATCH')
            row=self.registry.latest(source_account,target_account,symbol,expected_head=self.head())
            if not row or row['decision']!='APPROVED':raise SafetyError('SYMBOL_NOT_MAPPED')
            if not row['validated_at']<=self.clock()<row['review_at']:raise SafetyError('MAPPING_EXPIRED')
            sources=[r for r in self.sources() if r['spec']['name']==symbol];targets=self.targets()
            chosen=[r for r in targets if r['spec']['name']==row['target_symbol']]
            if len(sources)!=1 or len(chosen)!=1:raise SafetyError('MAPPING_ABSENT_OR_AMBIGUOUS')
            a,b=sources[0],chosen[0]
            if a['identity']['account_ref']!=source_account or b['identity']['account_ref']!=target_account:raise SafetyError('MAPPING_ACCOUNT_MISMATCH')
            for current,approved in ((a,row['source']),(b,row['target'])):
                if fingerprint(current)!=fingerprint(approved):raise SafetyError('MAPPING_CHARACTERISTICS_CHANGED')
            status,diffs=SymbolCompatibilityValidator().validate(a,b,self.clock())
            if status!='EXACT_MATCH_CANDIDATE':raise SafetyError('MAPPING_'+status)
            problem=VolumeCompatibilityValidator().validate(b,volume)
            if problem:raise SafetyError('MAPPING_'+problem)
            if row['origin']=='AUTO':
                ranked=SymbolCandidateMatcher().rank(a,targets,now=self.clock(),volume=volume,qualification=self.qualification)
                if sum(r['status']=='AUTO_MATCH_ELIGIBLE' for r in ranked)!=1 or not any(r['target']==b['spec']['name'] and r['status']=='AUTO_MATCH_ELIGIBLE' for r in ranked):raise SafetyError('MAPPING_AMBIGUOUS')
            result=self.qualification.evaluate(a,b,volume)
            if result.get('allowed') is not True or result.get('matrix_revision')!=row['qualification_revision']:raise SafetyError('MAPPING_BROKER_NOT_QUALIFIED')
            self.incidents.record(incident,None)
            return b['spec']['name']
        except Exception as error:
            known = {'MAPPING_ACCOUNT_MISMATCH','SYMBOL_NOT_MAPPED','MAPPING_EXPIRED',
                     'MAPPING_ABSENT_OR_AMBIGUOUS','MAPPING_CHARACTERISTICS_CHANGED',
                     'MAPPING_STALE_METADATA','MAPPING_REQUIRES_REVIEW','MAPPING_INCOMPATIBLE',
                     'MAPPING_VOLUME_RANGE','MAPPING_VOLUME_STEP','MAPPING_VOLUME_LIMIT',
                     'MAPPING_VOLUME_METADATA_INVALID','MAPPING_FILLING_UNSUPPORTED',
                     'MAPPING_EXECUTION_RULES_UNSUPPORTED','MAPPING_AMBIGUOUS',
                     'MAPPING_BROKER_NOT_QUALIFIED','MAPPING_REGISTRY_CORRUPT','MAPPING_CHECKPOINT_MISMATCH'}
            reason=str(error) if isinstance(error,SafetyError) and str(error) in known else 'MAPPING_COLLECTION_OR_REGISTRY_FAILED'
            # Local diagnostic index contains symbol + pseudonymous target, never credentials.
            with self.incidents.connect() as c:
                c.execute('CREATE TABLE IF NOT EXISTS mapping_diagnostics(id INTEGER PRIMARY KEY, at REAL, source_symbol TEXT, target_hash TEXT, reason TEXT)')
                c.execute('INSERT INTO mapping_diagnostics(at,source_symbol,target_hash,reason) VALUES(?,?,?,?)',
                          (self.clock(),str(symbol)[:64],digest(target_account),reason))
            self.incidents.record(incident,reason)
            raise SafetyError(reason) from None
    def require_open(self,logical):
        result=self.resolve(self.source_account,self.target_account,logical.get('source_symbol',''),logical.get('requested_volume',logical.get('volume')))
        if result!=logical.get('symbol'):raise SafetyError('MAPPING_TARGET_CHANGED')


def local_report(sources, targets, *, qualification, now, volume):
    """Read-only report, no approval; contains no login/password/terminal paths."""
    result = {'algorithm':ALGORITHM, 'environment':'LOCAL_REVIEW_ONLY', 'at':now,
              'coverage':{}, 'symbols':[], 'collection_errors':[]}
    groups = {}
    for target in targets:
        try:groups.setdefault(target['identity']['account_ref'],[]).append(target)
        except (KeyError,TypeError):result['collection_errors'].append('TARGET_IDENTITY_MISSING')
    if not groups:groups={'NO_TARGET_CATALOG':[]}
    for source in sources:
        for account,catalog in groups.items():
            try:
                ranked=SymbolCandidateMatcher().rank(source,catalog,now=now,volume=volume,qualification=qualification)
                status=ranked[0]['status']
                result['coverage'][status]=result['coverage'].get(status,0)+1
                result['symbols'].append({'source_symbol':source['spec']['name'],
                    'source_account_hash':digest(source['identity']['account_ref']),
                    'target_account_hash':digest(account),
                    'broker':source['identity']['broker'],'server':source['identity']['server'],
                    'candidates':ranked,'executable':False})
            except Exception:result['collection_errors'].append('CATALOG_OR_QUALIFICATION_UNAVAILABLE')
    return result
