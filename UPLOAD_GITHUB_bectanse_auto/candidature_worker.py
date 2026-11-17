"""Explicit verified-application worker for the integrated production host."""
import os
import signal
import time
import requests
from wsgi import app
from app import candidature_runtime, register_webhook


def main():
    if os.environ.get('CANDIDATURE_ENABLED') != '1':return
    svc=candidature_runtime['service'];stopping=[False];registered=False
    def stop(*_):stopping[0]=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping[0]:
        try:
            if not registered:
                response=requests.get('https://api.telegram.org/bot'+svc.telegram_token+'/getMe',timeout=12)
                data=response.json()
                if response.status_code!=200 or data.get('result',{}).get('username','').lower()!=svc.telegram_username.lower():
                    raise RuntimeError('Bot de vérification incohérent ou indisponible')
                register_webhook();registered=True
                app.logger.info('Candidatures: webhook Telegram authentifié et enregistré')
            candidature_runtime['tick']()
            svc.heartbeat(True)
        except Exception:
            app.logger.exception('Candidatures: traitement temporairement indisponible')
            try:svc.heartbeat(False)
            except Exception:pass
        for _ in range(10):
            if stopping[0]:break
            time.sleep(1)
    # Do not mark unavailable here: the successor may already be healthy during a rolling deploy.

if __name__=='__main__':main()
