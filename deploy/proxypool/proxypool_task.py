"""This small file is synced by Crawlab; application code is in the Worker image."""

import os
import sys

interpreter = os.environ.get("PROXYPOOL_WORKER_PYTHON", "/opt/proxypool/venv/bin/python")
os.execv(interpreter, [interpreter, "-B", "-m", "apps.proxypool.worker", *sys.argv[1:]])
