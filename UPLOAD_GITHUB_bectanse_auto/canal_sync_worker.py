"""Continuously consume the durable VIP inbox, independently of legacy jobs.

Supervised by server_host: a failed process is restarted with the web service.
No messages are posted to Telegram here. Old inbox publications retain their
original timestamp and do not become fresh trading alerts.
"""
import logging
import os
import signal
import threading
import time

POLL_SECONDS = 5
MONITOR_SECONDS = 60
MAX_FAILURES = 3


def run(app, deliver, monitor, register, stop, *, monotonic=time.monotonic):
    next_monitor = 0.0
    failures = 0
    registered = False
    while not stop.is_set():
        started = monotonic()
        try:
            with app.app_context():
                deliver()
            failures = 0
        except Exception:
            failures += 1
            app.logger.exception('VIP inbox delivery failed (%s/%s)', failures, MAX_FAILURES)
            if failures >= MAX_FAILURES:
                return 1
        if started >= next_monitor:
            try:
                with app.app_context():
                    if not registered:
                        register()
                        registered = True
                    state = monitor()
                if state.get('issues'):
                    app.logger.warning('VIP sync health: %s', ','.join(state['issues']))
                else:
                    app.logger.info('VIP sync healthy; last publication: %s', state.get('last_publication'))
            except Exception:
                app.logger.exception('VIP synchronization health check failed')
            next_monitor = started + MONITOR_SECONDS
        stop.wait(max(0.1, POLL_SECONDS - (monotonic() - started)))
    return 0


def main():
    # This worker owns only VIP delivery, never the legacy trading/mail jobs.
    os.environ['BECTANSE_SKIP_STARTUP'] = '1'
    from app import app, job_canal_delivery, job_canal_monitor, register_canal_webhook
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    app.logger.info('VIP sync worker started: inbox every %ss, health every %ss', POLL_SECONDS, MONITOR_SECONDS)
    return run(app, job_canal_delivery, job_canal_monitor, register_canal_webhook, stop)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
