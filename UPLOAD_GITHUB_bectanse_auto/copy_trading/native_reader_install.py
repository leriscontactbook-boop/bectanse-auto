"""Install only the pinned read-only reader in a dedicated account terminal."""
import hashlib
import base64
import os
import time
import subprocess
from pathlib import Path


def start(destination, identity, artifact, expected_sha256, *, launch=subprocess.Popen, symbol=None,
          replace_identity=False):
    destination, artifact = Path(destination), Path(artifact)
    if (artifact.is_symlink() or not artifact.is_file()
        or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_sha256):
        raise ValueError('NATIVE_READER_ARTIFACT_CHANGED')
    if (identity['trade_mode'] not in (0, 2) or not str(identity['login']).isdigit()
        or any(c in identity['server'] for c in '\r\n\x00')):
        raise ValueError('NATIVE_READER_IDENTITY_INVALID')
    experts = destination / 'MQL5' / 'Experts'
    presets = destination / 'MQL5' / 'Presets'
    experts.mkdir(parents=True, exist_ok=True); presets.mkdir(parents=True, exist_ok=True)
    files=destination / 'MQL5' / 'Files';files.mkdir(parents=True,exist_ok=True)
    request=files/'BectanseNativeSymbols.txt'
    if not request.exists():request.write_text('\n',encoding='utf-8')
    target = experts / 'BectanseNativeSessions.ex5'
    if target.is_symlink() or (target.exists() and target.read_bytes() != artifact.read_bytes()):
        raise ValueError('NATIVE_READER_EXISTING_ARTIFACT_CHANGED')
    if not target.exists():
        with target.open('xb') as stream: stream.write(artifact.read_bytes())
    preset = presets / 'BectanseNativeSessions.set'
    text = 'ExpectedLogin={}\r\nExpectedServer={}\r\nExpectedMode={}\r\n'.format(identity['login'], identity['server'], identity['trade_mode'])
    # Python preserves Windows CRLF whereas ``Path.write_bytes`` and the
    # reader use a normalized comparison.  Normalize both sides so a valid
    # identity preset is not mistaken for an identity change on every launch.
    if preset.is_symlink():
        raise ValueError('NATIVE_READER_EXISTING_IDENTITY_CHANGED')
    existing_preset = preset.read_text(encoding='utf-16').replace('\r\n','\n') if preset.exists() else None
    if existing_preset is not None and existing_preset != text.replace('\r\n','\n'):
        # Only ``ensure`` enables this after a successful authenticated
        # connection to the currently configured master.  The preset belongs
        # to the dedicated read-only terminal, not to a client account.
        if not replace_identity:
            raise ValueError('NATIVE_READER_EXISTING_IDENTITY_CHANGED')
        preset.write_bytes(text.encode('utf-16'))
    elif not preset.exists():
        preset.write_bytes(text.encode('utf-16'))
    config = destination / 'BectanseNativeReader.ini'
    if config.is_symlink(): raise ValueError('NATIVE_READER_CONFIG_INVALID')
    if symbol is not None and (not isinstance(symbol, str) or not symbol or any(c in symbol for c in '\r\n\x00')):
        raise ValueError('NATIVE_READER_SYMBOL_INVALID')
    config.write_bytes(('[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\nAllowDllImport=0\r\nAccount=0\r\nProfile=0\r\n[StartUp]\r\nExpert=BectanseNativeSessions\r\n'
        'ExpertParameters=BectanseNativeSessions.set\r\n' + (('Symbol='+symbol+'\r\n') if symbol else '') + 'Period=M1\r\nShutdownTerminal=0\r\n').encode('utf-16'))
    # MT5 authenticates separately through its existing in-memory SDK path.
    # A launch is not a claim that the expert loaded: NativeSessions validates
    # actual terminal-local output before any compatibility can be accepted.
    process=launch([str(destination / 'terminal64.exe'), '/portable', '/config:' + str(config)],
           cwd=str(destination), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # initialize() can otherwise race this launch and start an unconfigured MT5.
    # Wait for the configured process's message loop before attaching the SDK.
    if os.name=='nt' and isinstance(process,subprocess.Popen):
        import ctypes
        wait=ctypes.WinDLL('user32',use_last_error=True).WaitForInputIdle
        wait.argtypes=[ctypes.c_void_p,ctypes.c_uint32];wait.restype=ctypes.c_uint32
        if wait(int(process._handle),20000)!=0:
            raise ValueError('NATIVE_READER_PROCESS_NOT_READY')


def stop_owned_terminal(destination):
    """Fence the dedicated terminal, including instances missed by Windows WMI."""
    import ntpath
    import psutil
    executable = ntpath.normcase(str(Path(destination) / 'terminal64.exe'))
    owned = []
    for process in psutil.process_iter(['name']):
        try:
            if (process.info['name'].lower() == 'terminal64.exe'
                    and ntpath.normcase(process.exe()) == executable):
                owned.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise ValueError('TERMINAL_STOP_UNCONFIRMED') from None
    for process in owned:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=10)
    if alive:
        raise ValueError('TERMINAL_STOP_UNCONFIRMED')


def ensure(session, destination, identity, artifact, expected_sha256, *,
           stop_terminal=stop_owned_terminal, launch=subprocess.Popen, clock=time.time, sleep=time.sleep,
           heartbeat=None, startup_timeout=480):
    """Prepare the dedicated terminal while the account lock is held, before execution.

    Platform permissions are separate from backend consent. Only the pinned
    observation expert is installed; this function never sends orders or sets ON.
    """
    from .instrument_collection import NativeSessions
    module=session.module
    terminal=module.terminal_info()
    try:
        NativeSessions(module,clock).clock_offset()
        ready=terminal.trade_allowed and not terminal.tradeapi_disabled
    except Exception:ready=False
    if ready:return
    symbols=module.symbols_get() or ()
    names=sorted(s.name for s in symbols if getattr(s,'visible',False) and s.name)
    if not names:names=sorted(s.name for s in symbols if s.name)
    if not names:raise ValueError('NATIVE_READER_SYMBOL_REQUIRED')
    # Refuse to enable a terminal containing any other expert or script.
    destination=Path(destination)
    # MT5 installs example programs in subfolders on first launch; these are
    # not loaded by our explicit StartUp expert. Reject other root experts.
    if any(p.name!='BectanseNativeSessions.ex5' for p in (destination/'MQL5'/'Experts').glob('*.ex5')):
        raise ValueError('DEDICATED_READER_TERMINAL_REQUIRED')
    # Validate and stage before stopping; do not launch until the old process exits.
    start(destination,identity,artifact,expected_sha256,launch=lambda *a,**k:None,symbol=names[0],
          replace_identity=True)
    session.broker.disconnect()
    stop_terminal(destination)
    start(destination,identity,artifact,expected_sha256,launch=launch,symbol=names[0],
          replace_identity=True)
    session.broker.connect()
    # A terminal can take longer than the Python SDK to reconnect after a
    # controlled reader restart.  Keep the observer alive and report a local
    # liveness heartbeat while waiting for the *actual* identity-bound reader
    # file.  The previous 12-second window caused a restart loop: each attempt
    # killed the terminal before MT5 had a chance to attach the reader.
    if type(startup_timeout) not in (int, float) or not 12 <= startup_timeout <= 900:
        raise ValueError('NATIVE_READER_START_TIMEOUT_INVALID')
    deadline=clock()+startup_timeout
    while True:
        if heartbeat is not None:
            heartbeat()
        try:
            NativeSessions(module,clock).clock_offset()
            return
        except Exception:
            if clock()>=deadline:raise ValueError('NATIVE_READER_START_UNCONFIRMED') from None
            sleep(.5)
