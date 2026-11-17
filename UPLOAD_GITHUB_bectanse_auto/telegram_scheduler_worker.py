"""Dedicated publication clock for Telegram Control Center posts.

Integrated production keeps the HTTP worker free of background schedulers. This
process runs only the Telegram queue and relies on database slot claims to make
every delivery idempotent.
"""

import logging
import signal
import time


POLL_SECONDS = 20


def main() -> int:
    try:
        from wsgi import app
    except ImportError:
        from app import app
    from app import process_scheduled_telegram_posts

    stopping = [False]

    def stop(_signal, _frame):
        stopping[0] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.logger.info("Telegram scheduler worker started (%ss cadence)", POLL_SECONDS)

    while not stopping[0]:
        started = time.monotonic()
        try:
            with app.app_context():
                sent = process_scheduled_telegram_posts()
            if sent:
                app.logger.info("Telegram scheduler delivered %s publication(s)", sent)
        except Exception:
            app.logger.exception("Telegram scheduler cycle failed")
        time.sleep(max(1.0, POLL_SECONDS - (time.monotonic() - started)))

    app.logger.info("Telegram scheduler worker stopped")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
