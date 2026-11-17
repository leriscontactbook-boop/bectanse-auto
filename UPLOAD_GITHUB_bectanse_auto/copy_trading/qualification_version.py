"""Code fingerprint shared by lab evidence, operational review and executor."""
import hashlib,json
from pathlib import Path
FILES=('demo_engine.py','demo_mt5.py','demo_policy.py','demo_store.py','real_engine.py',
       'real_mt5.py','real_execution_boundary.py','real_execution_worker.py','real_runtime.py','native_orders.py',
       'native_lifecycle.py','orphan_recovery.py','compatibility.py','compatibility_runtime.py',
       'financial_objective.py','account_observation.py','qualification_version.py',
       'executor_pool.py','real_execution_host.py','broker_clock.py',
       'external_positions.py','runtime_collection.py','instrument_collection.py','symbol_mapping.py','native_preflight_review.py','preflight_delivery.py',
       'account_bootstrap.py','initial_instruments.py','native_reader_install.py','real_session.py')
def executor_digest():
    root=Path(__file__).parent
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in FILES}
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def authorization_digest(matrix,executor):
    return hashlib.sha256(json.dumps({'matrix':matrix,'executor':executor},sort_keys=True,separators=(',',':')).encode()).hexdigest()
