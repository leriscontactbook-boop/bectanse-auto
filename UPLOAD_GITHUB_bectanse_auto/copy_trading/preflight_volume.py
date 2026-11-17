"""Configuration-bound qualification ceilings; actual orders are sized separately."""
from .compatibility import number

def configured(settings, symbol):
    mode=settings['mode_risque']
    if mode=='Lots fixes':return number(settings['lots'])
    if mode=='Lot par symbole':return number(settings['lot_symboles'].get(symbol,0))
    return number(settings['lots_max'])

def matches(proof,settings):
    value=proof['volume']
    if isinstance(value,dict):
        return bool(value) and all(number(v)>0 and number(v)==configured(settings,s) for s,v in value.items())
    return settings['mode_risque']=='Lots fixes' and number(value)==number(settings['lots'])
