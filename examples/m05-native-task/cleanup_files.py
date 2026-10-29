"""Run inside a specified existing node; only this POC's known IDs are eligible."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time


def checked_targets(spec):
    sid = spec["sid"]
    if not re.fullmatch(r"[0-9a-f]{24}", sid):
        raise RuntimeError("Invalid owned Spider ID")
    tasks = spec["tasks"]
    if len(tasks) > 1 or any(not re.fullmatch(r"[0-9a-f]{24}", t) for t in tasks):
        raise RuntimeError("Invalid owned Task IDs")
    bases = ["/root/crawlab_workspace", "/root/crawlab_repo", "/workspace", "/repo", "/spiders"]
    for key in ("CRAWLAB_FS_WORKSPACE_PATH", "CRAWLAB_FS_REPO_PATH"):
        value = os.environ.get(key)
        if value and value not in bases:
            raise RuntimeError("Unexpected filesystem setting; review before cleanup")
    candidates = [(Path(base), sid) for base in bases]
    candidates += [(Path("/var/log/crawlab"), tid) for tid in tasks]
    result = []
    for base, leaf in candidates:
        target = base / leaf
        # Verify the resolved absolute target and every ancestor before traversal.
        if not target.is_absolute() or target.resolve() != target:
            raise RuntimeError("Refusing a redirected POC path")
        for ancestor in (target,) + tuple(target.parents):
            if ancestor.is_symlink():
                raise RuntimeError("Refusing symlink in POC cleanup path")
        if target.parent != base or target.name != leaf:
            raise RuntimeError("POC path escaped its registered base")
        result.append(target)
    return result


def matching_processes(spec):
    batch = spec.get("batch")
    if not batch:
        return []
    if not re.fullmatch(r"[0-9a-f]{32}", batch):
        raise RuntimeError("Invalid batch identity")
    result = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            marker = argv.index(b"--batch-id")
            if marker + 1 >= len(argv) or argv[marker + 1] != batch.encode("ascii"):
                continue
            if b"worker.py" not in argv or not Path(argv[0].decode()).name.startswith("python"):
                continue
            env = (proc / "environ").read_bytes().split(b"\0")
            ids = [item.split(b"=", 1)[1].decode() for item in env if item.startswith(b"CRAWLAB_TASK_ID=")]
            if len(ids) != 1 or ids[0] not in spec["tasks"]:
                raise RuntimeError("Matching process has unexpected task identity")
            result.append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError, ValueError):
            continue
    if len(result) > 1:
        raise RuntimeError("More than one matching POC process; review required")
    return result


def run(spec):
    paths = checked_targets(spec)
    action = spec["action"]
    if action == "preflight":
        if any(path.exists() for path in paths):
            raise RuntimeError("POC path already exists")
    elif action in ("process", "stop"):
        if action == "stop":
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid in matching_processes(spec):
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 3
                while matching_processes(spec) and time.monotonic() < deadline:
                    time.sleep(0.1)
        return {"process_absent": not matching_processes(spec)}
    elif action == "prune":
        if matching_processes(spec):
            raise RuntimeError("POC Worker is still executing")
        # All UUID paths were absent at preflight and are private to this Spider.
        # No recursive removal of base directories, data volumes, or shared logs.
        for path in paths:
            if path.exists():
                if not path.is_dir():
                    raise RuntimeError("Unexpected non-directory POC target")
                shutil.rmtree(path)
    elif action != "verify":
        raise RuntimeError("Invalid POC file action")
    absent = not any(path.exists() for path in paths)
    if action in ("prune", "verify") and not absent:
        raise RuntimeError("POC node files remain")
    return {"files_absent": absent}


if __name__ == "__main__":
    try:
        print(json.dumps(run(json.load(sys.stdin))))
    except Exception:
        print(json.dumps({"error": "node_cleanup_not_confirmed"}))
        raise SystemExit(1)
