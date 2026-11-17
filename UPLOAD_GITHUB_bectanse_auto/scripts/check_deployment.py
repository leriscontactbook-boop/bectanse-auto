"""Validate a clean checkout without connecting to production services."""
import ast
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

assert os.environ.get('BECTANSE_SKIP_STARTUP') == '1', 'Run with startup disabled'
config = json.loads((ROOT / 'railway.json').read_text())
assert config == json.loads((ROOT.parent / 'railway.json').read_text()), 'Railway configurations diverged'
assert (ROOT / config['build']['dockerfilePath'].lstrip('/')).is_file(), 'Dockerfile missing'
assert 'persistent/' not in config['deploy']['startCommand'], 'Code must not come from a data volume'
for directory in ('trading_journal', 'copy_trading', 'stripe_reliability'):
    for path in (ROOT / directory).rglob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
for path in ROOT.glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))

import app

assert app._runtime_scheduler is None, 'Unexpected background scheduler'
assert '/health' in {rule.rule for rule in app.app.url_map.iter_rules()}
assert 'gunicorn' in config['deploy']['startCommand']
print('Deployment paths, syntax and application imports validated; background startup disabled.')
