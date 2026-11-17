"""Bounded observational I/O. Never sends trades or authorizes an operation."""
import copy
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
import time


def masked(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


class RuntimeTelemetry:
    FIELDS = {'correlation_id', 'event_id', 'account', 'source_position', 'symbol',
              'volume', 'requested_volume', 'kind', 'state', 'retcode', 'error_type',
              'duration_ms', 'dropped', 'write_errors'}

    def __init__(self, path, capacity=2048):
        self.queue = queue.Queue(maxsize=capacity)
        self.stop = threading.Event()
        self.context = threading.local()
        self.dropped = self.write_errors = 0
        self.handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        self.thread = threading.Thread(target=self._run, name='copy-telemetry', daemon=True)
        self.thread.start()

    def emit(self, stage, **fields):
        row = {'utc_ns': time.time_ns(), 'stage': stage,
               'correlation_id': getattr(self.context, 'identity', None),
               'dropped': self.dropped, 'write_errors': self.write_errors}
        row.update({k: v for k, v in fields.items() if k in self.FIELDS
                    and isinstance(v, (str, int, float, bool, type(None)))})
        try:
            self.queue.put_nowait(json.dumps(row, allow_nan=False))
        except (queue.Full, TypeError, ValueError):
            self.dropped += 1

    def measure(self, stage, call):
        start = time.perf_counter_ns()
        fields = {}
        try:
            result = call()
            code = getattr(result, 'retcode', None)
            if isinstance(code, int):
                fields['retcode'] = code
            return result
        except Exception as exc:
            fields['error_type'] = type(exc).__name__
            raise
        finally:
            self.emit(stage, duration_ms=(time.perf_counter_ns()-start)/1e6, **fields)

    def _run(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    continue
                try:
                    # Direct stream write keeps error accounting under our control.
                    if self.handler.shouldRollover(logging.makeLogRecord({'msg': item})):
                        self.handler.doRollover()
                    self.handler.stream.write(item+'\n')
                    self.handler.flush()
                except Exception:
                    self.write_errors += 1
                finally:
                    self.queue.task_done()
        finally:
            self.handler.close()

    def close(self, timeout=1):
        self.stop.set()
        self.thread.join(timeout)
        return not self.thread.is_alive()


class LatestStatusReporter:
    """One in-flight snapshot plus one replaceable pending snapshot, no retries.

    Superseding a status is permitted: durable execution evidence lives in SQLite.
    The background thread owns its HTTP client. No MT5 access is permitted here.
    """
    def __init__(self, factory, telemetry=None):
        self.factory, self.telemetry = factory, telemetry
        self.condition = threading.Condition()
        self.pending = None
        self.stopping = False
        self.replaced = self.failures = self.sent = 0
        self.thread = threading.Thread(target=self._run, name='copy-status', daemon=True)
        self.thread.start()

    def submit(self, report):
        # Bound memory and detach caller-owned state before crossing threads.
        if len(json.dumps(report)) > 2_000_000:
            self.failures += 1
            return False
        item = copy.deepcopy(report)
        with self.condition:
            if self.stopping:
                return False
            if self.pending is not None:
                self.replaced += 1
            self.pending = item
            self.condition.notify()
        return True

    def _run(self):
        client = None
        try:
            client = self.factory()
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.pending is not None or self.stopping)
                    if self.pending is None and self.stopping:
                        return
                    item, self.pending = self.pending, None
                try:
                    call = lambda: client.request('/internal/copy/demo/status', {'report': item})
                    self.telemetry.measure('status_http', call) if self.telemetry else call()
                    self.sent += 1
                except Exception:
                    self.failures += 1
        except Exception:
            self.failures += 1
        finally:
            if client and hasattr(client, 'close'):
                client.close()

    def close(self, timeout=3):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        self.thread.join(timeout)
        return not self.thread.is_alive()


class MeasuredMT5:
    CALLS = {'initialize', 'account_info', 'terminal_info', 'symbol_info',
             'symbol_info_tick', 'symbol_select', 'positions_get', 'orders_get',
             'history_deals_get', 'order_check', 'order_send', 'shutdown'}

    def __init__(self, module, telemetry):
        self.module, self.telemetry = module, telemetry

    def __getattr__(self, name):
        value = getattr(self.module, name)
        if name not in self.CALLS:
            return value
        def measured(*args, **kwargs):
            # MT5's native binding distinguishes no keyword dictionary from an
            # empty one: order_check(request, **{}) returns None / error -2.
            # Preserve the original native calling convention, including send.
            call = (lambda: value(*args, **kwargs)) if kwargs else (lambda: value(*args))
            return self.telemetry.measure('mt5.' + name, call)
        return measured


def observed_ledger(base, telemetry, path):
    class ObservedLedger(base):
        def intent(self, owner, source_id, link, kind, request, expected, now=None):
            start = time.perf_counter_ns()
            command = super().intent(owner, source_id, link, kind, request, expected, now)
            telemetry.context.identity = command['id']
            telemetry.emit('intent_persisted', event_id=command.get('event_id'), account=masked(owner),
                           source_position=masked(source_id), symbol=request.get('symbol'),
                           kind=kind, volume=request.get('volume'),
                           requested_volume=request.get('requested_volume', request.get('volume')),
                           duration_ms=(time.perf_counter_ns()-start)/1e6)
            return command

        def record_result(self, command, result):
            value = super().record_result(command, result)
            telemetry.emit('broker_result_persisted', correlation_id=command['id'],
                           state=command['state'], retcode=(result or {}).get('retcode'))
            return value

        def finish(self, owner, source_id, link, command, outcome):
            value = super().finish(owner, source_id, link, command, outcome)
            telemetry.emit('reconciliation_persisted', correlation_id=command['id'], state=outcome)
            return value
    return ObservedLedger(path)
