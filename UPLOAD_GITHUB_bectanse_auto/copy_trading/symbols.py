"""Broker-verified instrument mappings. Names alone never establish compatibility."""
from .demo_policy import compatible, SafetyError

MAX_SYMBOLS = 5000


def verified_mapping(master, follower, aliases=None):
    """Match exact names, or explicit operator aliases; never guess suffix equivalence."""
    aliases = aliases or {}
    def index(rows):
        result = {}
        for row in rows:
            name = row.get('name')
            if not isinstance(name, str) or not 1 <= len(name) <= 64 or name in result:
                raise ValueError('INVALID_BROKER_CATALOG')
            result[name] = row
        if not result or len(result) > MAX_SYMBOLS:
            raise ValueError('INVALID_BROKER_CATALOG')
        return result
    sources, targets = index(master), index(follower)
    mapping, rejected, used = {}, {}, set()
    for name, source in sources.items():
        target_name = aliases.get(name, name)
        target = targets.get(target_name)
        if target is None:
            rejected[name] = 'SYMBOL_NOT_AVAILABLE_ON_FOLLOWER'
            continue
        if target_name in used:
            rejected[name] = 'AMBIGUOUS_SYMBOL_MAPPING'
            continue
        try:
            compatible(source, target)
        except SafetyError as error:
            rejected[name] = str(error)
            continue
        mapping[name] = target_name
        used.add(target_name)
    return mapping, rejected
