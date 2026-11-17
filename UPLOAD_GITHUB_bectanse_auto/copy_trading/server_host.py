"""Supervise the web server and the read-only preparation worker in one container.

If either child exits, stop both and return an error for the platform's restart
policy. Queue leases make recovery safe. No MT5 process is launched here.
"""
import os
import signal
import subprocess
import sys
import time


def main():
    children=[]
    stopping=[False]
    def stop(_sig,_frame):stopping[0]=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    code=1
    try:
        children.append(subprocess.Popen([sys.executable,'-m','copy_trading.onboarding_server','--watch']))
        children.append(subprocess.Popen([sys.executable,'-m','gunicorn','app:app','--bind','0.0.0.0:'+os.environ.get('PORT','8080'),
            '--workers','1','--threads','8','--worker-class','gthread','--keep-alive','10','--timeout','180']))
        if os.environ.get('APP_ENV')=='production' and os.environ.get('BECTANSE_TELEGRAM_SCHEDULER_ENABLED')=='true':
            children.append(subprocess.Popen([sys.executable,'-m','telegram_scheduler_worker']))
        if os.environ.get('APP_ENV')=='production':
            children.append(subprocess.Popen([sys.executable,'-m','canal_sync_worker']))
            children.append(subprocess.Popen([sys.executable,'-m','canal_alert_worker']))
            children.append(subprocess.Popen([sys.executable,'-m','billing_access_worker']))
        if os.environ.get('APP_ENV')=='production' and os.environ.get('BROKER_TRIAL_ENABLED')=='1':
            children.append(subprocess.Popen([sys.executable,'-m','broker_trial_worker']))
        if os.environ.get('APP_ENV')=='production' and os.environ.get('CANDIDATURE_ENABLED')=='1':
            children.append(subprocess.Popen([sys.executable,'-m','candidature_worker']))
        while not stopping[0] and all(p.poll() is None for p in children):time.sleep(.5)
        if stopping[0]:code=0
    finally:
        for p in children:
            if p.poll() is None:p.terminate()
        for p in children:
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill();p.wait()
    return code


if __name__=='__main__':raise SystemExit(main())
