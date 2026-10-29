"""Published as proxypool_task.py at the root of each complete Crawlab program."""

from pathlib import Path
import sys

# Resolve only this delivered program. No node-specific installation path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from apps.proxypool.task_main import main

if __name__ == "__main__":
    raise SystemExit(main())
