"""Provision a dedicated terminal from an operator-supplied program template.

Never copy saved accounts, certificates, profiles, scripts, experts or history.
Broker initialization and demo-only guards still run before any possible order.
"""
import os
from pathlib import Path
import shutil
import tempfile


def provision(template, destination):
    template, destination = Path(template), Path(destination)
    if (destination / 'terminal64.exe').is_file():
        return False  # Never overwrite or reset an existing terminal/account.
    if destination.exists():
        raise ValueError('TERMINAL_DIRECTORY_REQUIRES_REVIEW')
    if not (template / 'terminal64.exe').is_file():
        raise ValueError('VERIFIED_TERMINAL_TEMPLATE_REQUIRED')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.terminal-', dir=destination.parent))
    try:
        for file in template.iterdir():
            if file.is_file() and not file.is_symlink() and (file.name.lower() in
                    {'terminal64.exe', 'metaeditor64.exe', 'metatester64.exe'} or file.suffix.lower() == '.dll'):
                shutil.copy2(file, stage / file.name)
        (stage / 'Config').mkdir()
        # MT5 needs the broker directory before Python can authenticate a new
        # installation. This file contains server endpoints, not saved accounts.
        # Never copy the rest of Config (accounts, certificates or settings).
        catalog = template / 'Config' / 'servers.dat'
        if catalog.is_file() and not catalog.is_symlink():
            shutil.copy2(catalog, stage / 'Config' / 'servers.dat')
        (stage / 'Config' / 'common.ini').write_text('[Common]\nKeepPrivate=0\n[Charts]\nMaxBars=1000\n', encoding='utf-16')
        os.replace(stage, destination)
        return True
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only this function's own incomplete temporary directory.
