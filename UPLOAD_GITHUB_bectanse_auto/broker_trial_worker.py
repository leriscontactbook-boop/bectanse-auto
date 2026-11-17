"""Explicit broker trial lifecycle worker for integrated production hosting."""
import os
import time
from wsgi import app
from app import broker_trial_runtime

if __name__ == '__main__':
    while True:
        if os.environ.get('BROKER_TRIAL_ENABLED') == '1':
            try:
                broker_trial_runtime['tick']()
            except Exception as error:
                app.logger.error('Broker trial worker: %s', type(error).__name__)
        time.sleep(60)
