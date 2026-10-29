"""User-run, receive-only server POC with exact ownership-checked cleanup.

Packed as m05_poc.pyz. Host mode requires sudo and its one fixed upload path.
Container mode reuses the project's real callback parser/receiver; no SQLite.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid


TASK = "m05-poc-20261027-01"
NAME = TASK
WORKER = "crawlab-worker-1"
NETWORK = "crawlab_default"
PORT = 18080
UPLOAD = Path("/tmp/home-server-ops") / TASK / "m05_poc.pyz"
OWNER_LABEL = "local.m05.poc"
HASH_LABEL = "local.m05.bundle-sha256"


class PocError(RuntimeError):
    pass


def docker(args, *, input_text=None, timeout=15, optional=False):
    result = subprocess.run(["docker"] + list(args), input=input_text, capture_output=True,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        missing = re.search(r"no such (?:object|container)", result.stderr, re.I)
        if optional and missing:
            return None
        raise PocError("Docker command failed: " + result.stderr.strip()[:400])
    return result.stdout.strip()


def inspect(reference, template, *, optional=False):
    return docker(["container", "inspect", "--format", template, reference], optional=optional)


def cleanup_container(bundle_sha):
    container_id = inspect(NAME, "{{.Id}}", optional=True)
    if container_id is None:
        return
    labels = json.loads(inspect(container_id, "{{json .Config.Labels}}") or "{}")
    if labels.get(OWNER_LABEL) != TASK or labels.get(HASH_LABEL) != bundle_sha:
        raise PocError("Refusing to remove a container without this POC's exact ownership labels")
    try:
        docker(["container", "rm", "--force", "--volumes", container_id], optional=True)
    finally:
        # --rm and explicit removal may race. Inspect treats only a genuine not-found as absent.
        if inspect(container_id, "{{.Id}}", optional=True) is not None:
            raise PocError("Temporary container still exists")


def cleanup_upload(source):
    if source != UPLOAD or source.is_symlink() or source.parent.is_symlink():
        raise PocError("Refusing cleanup outside the exact POC upload path")
    source.unlink(missing_ok=True)
    # No recursive deletion. Unexpected extra files cause a visible cleanup failure.
    source.parent.rmdir()
    if source.exists() or source.parent.exists():
        raise PocError("POC upload cleanup is incomplete")


def temporary_mounts(volume_paths):
    paths = set(volume_paths or {}) | {"/tmp"}
    flags = []
    for path in sorted(paths):
        if (not isinstance(path, str) or not path.startswith("/") or
                any(char in path for char in (":", ",", "\n")) or
                path == "/" or path.startswith(("/usr", "/opt", "/bin", "/lib", "/poc"))):
            raise PocError("Image declares a volume that this POC cannot safely override")
        flags.extend(["--tmpfs", path + ":rw,nosuid,nodev,noexec,size=4m,mode=1777"])
    return flags


SENDER = r'''
import json, socket, sys, time, urllib.request
host, port = sys.argv[1], int(sys.argv[2])
payload = json.load(sys.stdin)
deadline = time.monotonic() + 20
while True:
    try:
        with socket.create_connection((host, port), timeout=1):
            break
    except OSError:
        if time.monotonic() >= deadline:
            raise RuntimeError("POC receiver did not become reachable")
        time.sleep(0.2)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request(
    "http://{}:{}/internal/v1/check-results".format(host, port),
    json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"})
with opener.open(request, timeout=4) as response:
    body = json.load(response)
    if (response.status != 200 or body.get("received") is not True or
            body.get("persisted") is not False or body.get("record_count") != 3 or
            body.get("batch_id") != payload["batch_id"] or body.get("task_id") != payload["task_id"]):
        raise RuntimeError("Unexpected receive-only acknowledgement")
print(json.dumps({"check":"PASS", "sent":3, "received":3, "persisted":False,
                  "sender":"existing_worker_container", "native_task_created":False}))
'''


CLEAN_SENDER = r'''
import hashlib, json, os, re, signal, sys, time
marker = sys.argv[1].encode("ascii")
signature = sys.argv[2]
def matching():
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/cmdline", "rb") as stream:
                args = stream.read(32768).split(b"\0")
            if args and args[-1] == b"":
                args.pop()
            if (len(args) == 7 and args[1:3] == [b"-B", b"-c"] and
                    args[4:6] == [b"m05-poc-20261027-01", b"18080"] and
                    re.fullmatch(b"[0-9a-f]{32}", args[6]) and
                    hashlib.sha256(args[3]).hexdigest() == signature and
                    (marker == b"all" or args[6] == marker)):
                found.append(int(name))
        except (OSError, ValueError):
            pass
    return found
for sig in (signal.SIGTERM, signal.SIGKILL):
    for pid in matching():
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    while matching() and time.monotonic() < deadline:
        time.sleep(0.05)
left = matching()
if left:
    raise RuntimeError("The exact POC sender process is still present")
print(json.dumps({"sender_absent":True}))
'''


def cleanup_sender():
    if inspect(WORKER, "{{.State.Running}}", optional=True) != "true":
        return
    signature = hashlib.sha256(SENDER.encode("utf-8")).hexdigest()
    result = json.loads(docker(["exec", WORKER, "python", "-B", "-c", CLEAN_SENDER,
                               "all", signature], timeout=8))
    if result.get("sender_absent") is not True:
        raise PocError("Sender process cleanup is unconfirmed")


def run_trial(source, bundle_sha, run_id):
    from apps.proxypool.service.contracts import Result
    if inspect(NAME, "{{.Id}}", optional=True) is not None:
        raise PocError("POC container name already exists; stop and review cleanup")
    if inspect(WORKER, "{{.State.Running}}") != "true":
        raise PocError("Existing Worker is not running")
    networks = json.loads(inspect(WORKER, "{{json .NetworkSettings.Networks}}"))
    if NETWORK not in networks:
        raise PocError("Existing Worker is not on the expected Crawlab network")
    python_version = json.loads(docker(["exec", "--user", "65534:65534", WORKER, "python", "-B", "-c",
                                       "import json,sys;print(json.dumps(list(sys.version_info[:2])))"]))
    if tuple(python_version) < (3, 8):
        raise PocError("The POC requires Python 3.8 or newer in the Worker image")
    image = inspect(WORKER, "{{.Image}}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise PocError("Invalid local image identity")
    volumes = json.loads(docker(["image", "inspect", "--format", "{{json .Config.Volumes}}", image]) or "null")
    if volumes is not None and not isinstance(volumes, dict):
        raise PocError("Invalid image volume declaration")
    source.chmod(0o644)  # Parent upload directory remains private; the read-only bind is readable by nobody.
    args = ["run", "--detach", "--rm", "--pull", "never", "--name", NAME,
            "--label", OWNER_LABEL + "=" + TASK, "--label", HASH_LABEL + "=" + bundle_sha,
            "--network", NETWORK, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--user", "65534:65534",
            "--memory", "64m", "--memory-swap", "64m", "--cpus", "0.25",
            "--pids-limit", "32", "--log-driver", "none", "--no-healthcheck",
            "--workdir", "/tmp", "--mount", "type=bind,src=" + str(source) + ",dst=/poc/m05_poc.pyz,readonly"]
    args += temporary_mounts(volumes)
    args += ["--entrypoint", "python", image, "-B", "/poc/m05_poc.pyz", "--serve", "--run-id", run_id]
    container_id = docker(args, timeout=25)
    labels = json.loads(inspect(container_id, "{{json .Config.Labels}}"))
    if labels.get(HASH_LABEL) != bundle_sha or labels.get(OWNER_LABEL) != TASK:
        raise PocError("Unexpected POC ownership")
    mounts = json.loads(inspect(container_id, "{{json .Mounts}}"))
    if any(mount.get("Type") == "volume" for mount in mounts):
        raise PocError("Unexpected persistent volume; aborting POC")
    if json.loads(inspect(container_id, "{{json .HostConfig.PortBindings}}") or "null"):
        raise PocError("Unexpected published host port")
    now_ms = int(time.time() * 1000)
    records = [Result("check-one", "proxy-one", "site-demo", 1, now_ms, "response", 200, 80),
               Result("check-two", "proxy-two", "site-demo", 1, now_ms, "response", 503, 120),
               Result("check-three", "proxy-three", "site-demo", 1, now_ms,
                      "proxy_error", error_code="connect_timeout")]
    payload = {"schema_version": 1, "batch_id": "batch-" + run_id, "task_id": "exec-poc-" + run_id,
               "records": [record.to_wire() for record in records]}
    output = docker(["exec", "-i", WORKER, "python", "-B", "-c", SENDER, NAME, str(PORT), run_id],
                    input_text=json.dumps(payload), timeout=30)
    result = json.loads(output)
    if result.get("check") != "PASS" or result.get("persisted") is not False:
        raise PocError("Missing successful Worker callback result")
    print(json.dumps({"phase": "callback", **result}), flush=True)


def serve(run_id):
    from apps.proxypool.service.receiver import make_receiver
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise PocError("Invalid run marker")
    received = []
    def accept(summary):
        if summary != {"batch_id": "batch-" + run_id, "task_id": "exec-poc-" + run_id, "record_count": 3}:
            raise PocError("Wrong synthetic run")
        received.append(True)
    with make_receiver(PORT, accept, bind_host="0.0.0.0") as server:
        server.timeout = 1
        deadline = time.monotonic() + 45
        while not received and time.monotonic() < deadline:
            server.handle_request()
    if not received:
        raise PocError("POC receiver reached its deadline without a callback")


def host(cleanup_only=False):
    source = Path(sys.argv[0]).resolve(strict=True)
    if source != UPLOAD or not source.is_file() or source.parent.is_symlink():
        raise PocError("Run only the uploaded bundle at its documented exact path")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise PocError("Host execution requires sudo for Docker and cleanup")
    bundle_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    run_id = uuid.uuid4().hex
    trial_ok, cleanup_ok, docker_checked = False, True, False
    try:
        docker(["version", "--format", "{{.Server.Version}}"])
        docker_checked = True
        if not cleanup_only:
            run_trial(source, bundle_sha, run_id)
        trial_ok = True
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({"phase": "callback", "check": "FAIL", "error": str(error)[:500]}), flush=True)
    finally:
        try:
            if not docker_checked:
                raise PocError("Docker was unavailable; container absence could not be verified")
            cleanup_sender()
            cleanup_container(bundle_sha)
            cleanup_upload(source)
            print(json.dumps({"phase": "cleanup", "check": "PASS", "container_absent": True,
                              "sender_absent": True, "upload_absent": True,
                              "task_directory_absent": True}), flush=True)
        except (Exception, KeyboardInterrupt) as error:
            cleanup_ok = False
            print(json.dumps({"phase": "cleanup", "check": "FAIL", "error": str(error)[:500],
                              "next": "Stop and report this output; do not repeat the trial"}), flush=True)
    return 0 if trial_ok and cleanup_ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.serve:
        serve(args.run_id or "")
        return 0
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Signal received; attempting exact POC cleanup")
    signal.signal(signal.SIGTERM, interrupted)
    return host(cleanup_only=args.cleanup)


if __name__ == "__main__":
    raise SystemExit(main())
