"""Dedicated real-terminal preparation, reusable by the Windows supervisor.

One process per session (the MT5 Python API is process-global). This component
does not register tasks, change member consent, or start an execution loop.
It keeps the existing account/terminal lock until the caller closes the session.
"""
import hashlib
from pathlib import Path
from .demo_runtime import ProcessLocks
from .fleet_terminal import provision
from .real_mt5 import RealMT5
from .account_observation import collect
from .demo_mt5 import NotSent


class ObservationOnlyMT5(RealMT5):
    """Read netting/hedging accounts without granting execution capability."""
    def check_position_model(self, account):
        # Position accounting is observed, not approved for the executor.
        pass

    def execute(self, logical):
        raise NotSent('OBSERVATION_ONLY')


class RealSession:
    def __init__(self, module, root, template, identity, *, password,
                 lock_factory=ProcessLocks, provisioner=provision, native_reader=None):
        self.broker = None
        self.lock = None
        self.module = module
        self.identity = dict(identity)
        required = {'member_code', 'account_id', 'version', 'login', 'server', 'trade_mode'}
        if (set(identity) != required or identity['trade_mode'] != 2
                or type(identity['account_id']) is not int or identity['account_id'] < 1
                or type(identity['version']) is not int or identity['version'] < 1
                or not isinstance(identity['login'], str) or not identity['login'].isdigit()
                or any(not isinstance(identity[k], str) or not identity[k] for k in ('member_code', 'server'))):
            raise ValueError('REAL_SESSION_IDENTITY_REQUIRED')
        root, template = Path(root).resolve(), Path(template).resolve()
        # No user-controlled path component; same account/server always maps to
        # the same installation, including after a configuration revision.
        slot = hashlib.sha256((identity['login'] + '@' + identity['server']).encode()).hexdigest()
        destination = root / slot / 'Terminal'
        if template == destination or destination in template.parents or template in destination.parents:
            raise ValueError('TEMPLATE_AND_SESSION_MUST_BE_SEPARATE')
        self.identity['terminal'] = str(destination / 'terminal64.exe')
        try:
            self.lock = lock_factory({'login': identity['login'], 'server': identity['server'],
                                      'terminal': self.identity['terminal']})
            provisioner(template, destination)
            self.broker = ObservationOnlyMT5(module, login=identity['login'], server=identity['server'],
                                 terminal_path=self.identity['terminal'], password=password, writable=False)
            self.broker.connect()
            if native_reader is not None:
                from .native_reader_install import ensure
                ensure(self, destination, identity, native_reader['artifact'], native_reader['sha256'])
            # Credentials are needed again only through the authenticated parent
            # on a new connection, not retained by this prepared session.
            self.broker.password = None
        except Exception:
            self.close()
            raise

    def observe(self, volumes, *, history_from, key, **kwargs):
        if self.broker is None:
            raise ValueError('SESSION_CLOSED')
        self.broker.guard()
        return collect(self.module, self.identity, volumes, history_from=history_from, key=key, **kwargs)

    def close(self):
        try:
            if self.broker is not None:
                self.broker.password = None
                self.broker.disconnect()
        finally:
            self.broker = None
            if self.lock is not None:
                self.lock.close()
                self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
