"""Broker-native instrument discovery; candidates still require live qualification."""
from .symbol_mapping import ECONOMIC
from .compatibility import valuation_identity, VALUATION_FIELDS
from .demo_policy import SafetyError


# Native currency pairs identify spot FX/metals independently of broker suffixes.
# Futures and share/ETF contracts require ISIN or native underlying evidence.
CONTRACT_FIELDS=('currency_base','currency_profit','trade_contract_size','trade_tick_size','point','digits','trade_calc_mode','start_time','expiration_time')

CURRENCIES=frozenset('USD EUR GBP JPY CHF AUD NZD CAD SGD HKD NOK SEK DKK PLN CZK HUF TRY ZAR MXN BRL CNH CNY INR KRW THB ILS RUB XAU XAG XPT XPD BTC ETH LTC BCH XRP SOL ADA DOT DOGE'.split())

def same_underlying(source,target):
    a,b=source['spec'],target['spec']
    isin=a.get('isin');other=b.get('isin')
    if isin or other:return bool(isin) and isin==other
    # Expiring instruments never match a spot contract, including similar labels.
    if any(a.get(k)!=b.get(k) for k in ('start_time','expiration_time')):return False
    basis=a.get('basis')
    if isinstance(basis,str) and basis.strip() and basis==b.get('basis'):return True
    base,quote=a.get('currency_base'),a.get('currency_profit')
    if (base in CURRENCIES and quote in CURRENCIES and base!=quote
        and (base,quote)==(b.get('currency_base'),b.get('currency_profit'))
        and a.get('trade_calc_mode') in (0,2,4,5) and a.get('trade_calc_mode')==b.get('trade_calc_mode')
        and a.get('expiration_time')==b.get('expiration_time')==0):return True
    return (source['broker']==target['broker'] and bool(a.get('description'))
            and a['description']==b.get('description') and bool(a.get('path')) and a['path']==b.get('path'))


def candidates(source, target, existing):
    result = dict(existing)
    for name, observation in source.items():
        if name in result:
            continue
        a = observation['spec']
        matches = []
        try:valuation_identity(observation)
        except (KeyError,ValueError,TypeError):continue
        for target_name, other in target.items():
            b = other['spec']
            # Unrelated instruments cannot qualify. Avoid repeating monetary
            # validation across the entire broker catalog for each source.
            if not same_underlying(observation, other):continue
            try:
                # Monetary tick values may differ with account currency. They
                # must be valid on both terminals, not equal across currencies.
                valuation_identity(other)
                valid=True
            except (KeyError,ValueError,TypeError):valid=False
            if (valid and all(a.get(k) is not None and b.get(k) is not None
                    and str(a[k]) == str(b[k]) for k in CONTRACT_FIELDS)):
                matches.append(target_name)
        if name in matches:matches=[name]
        if len(matches) == 1:
            result[name] = matches[0]
    if not result:
        raise ValueError('NATIVE_INSTRUMENT_EQUIVALENCE_REQUIRES_REVIEW')
    return result


class ReviewedMapping:
    """Use the installed, admin-approved native matrix as the mapping authority.

    Legacy registries remain unchanged. This adapter applies only to the native
    review policy, and the same live qualification gate runs before submission.
    """
    def __init__(self, symbols, source_account, target_account, gate, profile, source_reader, target_reader):
        self.symbols, self.accounts = dict(symbols), (source_account, target_account)
        self.gate, self.profile = gate, dict(profile)
        self.source, self.target = source_reader, target_reader

    def resolve(self, source_account, target_account, symbol, volume):
        if (source_account, target_account) != self.accounts:
            raise SafetyError('MAPPING_ACCOUNT_MISMATCH')
        target = self.symbols.get(symbol)
        if not target:
            raise SafetyError('SYMBOL_NOT_MAPPED')
        observed = self.target(target)
        flags = observed['spec']['filling_mode']
        filling = 'FOK' if flags & 1 else 'IOC' if flags & 2 else 'UNSUPPORTED'
        result = self.gate.evaluate({**self.profile, 'source_symbol': symbol, 'target_symbol': target},
            source_reader=lambda: self.source(symbol), target_reader=lambda: observed,
            volume=volume, filling=filling, validity='GTC')
        if result.get('allowed') is not True:
            raise SafetyError('MAPPING_BROKER_NOT_QUALIFIED')
        return target

    def require_open(self, logical):
        target = self.resolve(*self.accounts, logical.get('source_symbol', ''),
                              logical.get('requested_volume', logical.get('volume')))
        if target != logical.get('symbol'):
            raise SafetyError('MAPPING_TARGET_CHANGED')
