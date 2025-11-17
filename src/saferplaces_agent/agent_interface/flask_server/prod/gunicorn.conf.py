# gunicorn.conf.py
import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")

# 2*CPU + 1 per default, ma consentiamo override via ENV
workers = int(os.getenv("GUNICORN_WORKERS", (multiprocessing.cpu_count() * 2) + 1))
threads = int(os.getenv("GUNICORN_THREADS", "2"))

worker_class = "gthread"      # buono per Flask I/O-bound
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
loglevel = os.getenv("LOG_LEVEL", "info")
accesslog = "-"               # stdout
errorlog = "-"                # stderr
