"""User-run native Crawlab -> M05 -> SQLite trial with scoped cleanup.

Host mutations are performed only when the user runs this reviewed zipapp.
No real proxy requests, image pulls, configuration changes or host port mappings.
"""

import argparse
import getpass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile

from native_task import Crawlab, JsonClient, PublishUncertain, TaskError, object_id, publish_reserved_batch
from inspect_platform import authenticate, inspect_nodes


TASK = "m05-native-20261028-01"
UPLOAD = Path("/tmp/home-server-ops") / TASK / "m05_native.pyz"
JOURNAL = UPLOAD.parent / "state.json"
WORKER = "crawlab-worker-1"
MASTER = "crawlab-master-1"
MONGO = "crawlab-mongo-1"
PORT = 18081
DATABASE = Path("/tmp/synthetic.sqlite3")
OWNER = "local.m05.native-poc"
HASH = "local.m05.bundle-sha256"
TERMINAL = {"finished", "error", "cancelled", "abnormal"}


def emit(phase, **fields):
    print(json.dumps({"phase": phase, **fields}), flush=True)


def command(args, *, data=None, timeout=15, missing=False):
    try:
        result = subprocess.run(args, input=data, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise TaskError("Command timed out; do not repeat the trial") from None
    if result.returncode:
        if missing and re.search(r"no such (object|container)", result.stderr, re.I):
            return None
        # Docker/Mongo errors may include internal configuration. Omit their bodies.
        raise TaskError("Command failed ({})".format(args[0]))
    if len(result.stdout) > 262144:
        raise TaskError("Command output exceeded the POC diagnostic limit")
    return result.stdout.strip()


def docker(args, **kwargs):
    return command(["docker"] + list(args), **kwargs)


def inspect(name, template, *, missing=False):
    return docker(["container", "inspect", "--format", template, name], missing=missing)


def asset(name):
    with zipfile.ZipFile(UPLOAD) as archive:
        return archive.read(name).decode("utf-8")


def save_state(state):
    partial = JOURNAL.with_suffix(".json.partial")
    if JOURNAL.is_symlink() or partial.exists() or partial.is_symlink():
        raise TaskError("Journal path is a symlink")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    partial.replace(JOURNAL)


def step(state, name):
    state["step"] = name
    save_state(state)
    emit(name, check="START")


RUNTIME_CHECK = r'''
import ast, errno, json, socket, sqlite3, sys
sources = json.load(sys.stdin)
reason = None
if sys.version_info < (3, 10) or sqlite3.sqlite_version_info < (3, 24, 0):
    reason = "unsupported_worker_runtime"
try:
    for name, text in sources.items():
        ast.parse(text, filename=name)
except (SyntaxError, ValueError):
    reason = "bundle_syntax_incompatible_with_worker"
with socket.socket() as conn:
    conn.settimeout(2)
    available = conn.connect_ex(("127.0.0.1", 18081)) == errno.ECONNREFUSED
if not available:
    reason = reason or "worker_loopback_port_unavailable"
print(json.dumps({"check": "FAIL" if reason else "PASS", "reason": reason,
                  "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                  "port_available": available}))
'''


def runtime_preflight():
    """Read-only checks before credentials and before starting any POC resource."""
    for name in (MASTER, WORKER, MONGO):
        if inspect(name, "{{.State.Running}}") != "true":
            raise TaskError("Required container is not running: " + name)
    if inspect(TASK, "{{.Id}}", missing=True) is not None:
        raise TaskError("POC container already exists; stop and review its owner")
    with zipfile.ZipFile(UPLOAD) as archive:
        sources = {name: archive.read(name).decode("utf-8")
                   for name in archive.namelist() if name.endswith(".py")}
    result = json.loads(docker(["exec", "-i", WORKER, "python", "-B", "-c", RUNTIME_CHECK],
                               data=json.dumps(sources), timeout=15))
    emit("worker_runtime", **result)
    if result.get("check") != "PASS" or result.get("port_available") is not True:
        raise TaskError("Worker runtime preflight was not confirmed")


def mongo(state, action):
    spec = {key: state[key] for key in ("sid", "run", "name", "collection", "batch", "node", "tasks", "dc")}
    spec["action"] = action
    script = "const P = " + json.dumps(spec) + ";\n" + asset("cleanup_native.js")
    output = docker(["exec", MONGO, "mongosh", "--quiet", "--norc", "--eval", script], timeout=20)
    lines = [line[len("M05_POC_JSON:"):] for line in output.splitlines() if line.startswith("M05_POC_JSON:")]
    if len(lines) != 1:
        raise TaskError("Missing POC database ownership acknowledgement")
    return json.loads(lines[0])


def node_files(state, name, action):
    spec = {"sid": state["sid"], "tasks": state["tasks"], "batch": state["batch"], "action": action}
    return json.loads(docker(["exec", "-i", name, "python", "-B", "-c", asset("cleanup_files.py")],
                             data=json.dumps(spec), timeout=20))


FILER_REQUEST = r'''
import http.client, json, os, re, sys
spec = json.load(sys.stdin)
if not re.fullmatch(r"[0-9a-f]{24}", spec["sid"]):
    raise RuntimeError("Invalid owned ID")
# Filer authorization differs from the management JWT. Keep the existing key
# inside Master; never pass it through host arguments, journal, or output.
key = os.environ["CRAWLAB_FS_FILER_AUTHKEY"]
path = "/filer/fs/" + spec["sid"] + "/"
conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=8)
try:
    conn.request("DELETE" if spec["delete"] else "GET",
                 path + ("?recursive=true" if spec["delete"] else ""),
                 headers={"Authorization": key, "Accept": "application/json"})
    response = conn.getresponse()
    body = response.read(65537)
    if len(body) > 65536 or response.status not in (200, 202, 204, 404):
        raise RuntimeError("Filer response not confirmed")
    if spec["delete"] and response.status != 404 and body:
        result = json.loads(body)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("Filer deletion not confirmed")
    print(json.dumps({"exists": response.status != 404}))
finally:
    conn.close()
'''


def filer(api, state, *, delete=False):
    """Invoke Master-local Filer authentication without exporting its secret."""
    spec = {"sid": object_id(state["sid"]), "delete": delete}
    result = json.loads(docker(["exec", "-i", MASTER, "python", "-B", "-c", FILER_REQUEST],
                               data=json.dumps(spec)))
    if type(result.get("exists")) is not bool:
        raise TaskError("Filer inspection or removal was not confirmed")
    return result["exists"]


class Relay:
    def __init__(self, run_id):
        self.run_id = run_id

    def request(self, method, path, payload=None):
        spec = {"method": method, "path": path, "payload": payload}
        return json.loads(docker(["exec", "-i", TASK, "python", "-B", "/poc.pyz", "--request", "--run-id", self.run_id],
                                 data=json.dumps(spec)))


def temporary_mounts(volumes):
    flags = ["--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777"]
    for path in sorted(set(volumes or {}) - {"/tmp"}):
        if (not isinstance(path, str) or not path.startswith("/") or
                any(c in path for c in (",", ":", "\n")) or path == "/" or
                path.startswith(("/usr", "/opt", "/bin", "/lib", "/poc"))):
            raise TaskError("Unexpected image volume declaration")
        flags += ["--tmpfs", path + ":rw,nosuid,nodev,noexec,size=4m,mode=1777"]
    return flags


def start_m05(state):
    if inspect(TASK, "{{.Id}}", missing=True) is not None:
        raise TaskError("POC container name already exists")
    image = inspect(WORKER, "{{.Image}}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise TaskError("Invalid local Worker image identity")
    volumes = json.loads(docker(["image", "inspect", "--format", "{{json .Config.Volumes}}", image]) or "null")
    # Bind the bundle only; its parent remains private. Nobody can read it outside Docker.
    UPLOAD.chmod(0o644)
    args = ["run", "--detach", "--rm", "--pull", "never", "--name", TASK,
            "--label", OWNER + "=" + state["run"], "--label", HASH + "=" + state["sha"],
            "--network", "container:" + WORKER, "--read-only", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.25", "--pids-limit", "32",
            "--log-driver", "none", "--no-healthcheck", "--workdir", "/tmp",
            "--mount", "type=bind,src=" + str(UPLOAD) + ",dst=/poc.pyz,readonly"]
    args += temporary_mounts(volumes)
    args += ["--entrypoint", "python", image, "-B", "/poc.pyz", "--serve", "--run-id", state["run"]]
    state["container_attempted"] = True
    save_state(state)
    container_id = docker(args, timeout=30)
    state["container"] = container_id
    save_state(state)
    mounts = json.loads(inspect(container_id, "{{json .Mounts}}"))
    if (any(mount["Type"] == "volume" for mount in mounts) or
            json.loads(inspect(container_id, "{{json .HostConfig.PortBindings}}") or "null")):
        raise TaskError("Unexpected persistent volume or published port")
    relay = Relay(state["run"])
    deadline = time.monotonic() + 12
    while True:
        try:
            relay.request("GET", "/internal/v1/proxies?target_id=site-demo")
            return relay
        except TaskError:
            if time.monotonic() >= deadline:
                raise TaskError("Temporary M05 did not become ready; no Task should be created") from None
            time.sleep(0.3)


def choose_node(api):
    report = inspect_nodes(api)
    emit("node_selection", **report)
    if report["readiness"] != "ready":
        raise TaskError("Node inspection: " + json.dumps(report, ensure_ascii=True))
    return object_id(report["selected_node"])


def trial(api, state):
    step(state, "node_selection")
    state["node"] = choose_node(api)
    save_state(state)
    step(state, "namespace_preflight")
    mongo(state, "preflight")
    for node in (MASTER, WORKER):
        node_files(state, node, "preflight")
    if filer(api, state):
        raise TaskError("Unexpected pre-existing Filer path")
    state["baseline_absent"] = True
    save_state(state)
    step(state, "m05_start")
    relay = start_m05(state)
    # This operation exercises the image's real SQLite schema/UPSERT before Task creation.
    step(state, "reserve_checks")
    batch = relay.request("POST", "/internal/v1/check-batches",
                          {"request_id": state["run"], "target_id": "site-demo", "limit": 3})["batch"]
    if len(batch["members"]) != 3:
        raise TaskError("Could not reserve three synthetic checks")
    state["batch"] = batch["batch_id"]
    step(state, "spider_create")
    state["spider_attempted"] = True
    save_state(state)
    spider = api.request("POST", "/spiders", {
        "_id": state["sid"], "name": state["name"], "description": state["run"],
        "col_name": state["collection"], "type": "customized", "mode": "selected-nodes",
        "node_ids": [state["node"]], "cmd": "python -B -u worker.py"})["data"]
    if spider.get("_id") != state["sid"]:
        raise TaskError("Unexpected Spider identity")
    state["dc"] = object_id(spider["col_id"])
    save_state(state)
    step(state, "source_sync")
    for name in ("worker.py", "native_task.py"):
        content = asset(name)
        api.request("POST", "/spiders/" + state["sid"] + "/files/save", {"path": "/" + name, "data": content})
        result = api.request("GET", "/spiders/" + state["sid"] + "/files/get?path=/" + name)
        if result.get("data") != content:
            raise TaskError("Uploaded source did not round-trip exactly")
    # Re-read only the selected node before publishing: readiness is not a lock.
    step(state, "publish_and_bind")
    if choose_node(api) != state["node"]:
        raise TaskError("Selected Worker identity changed; no Task published")
    state["publishing"] = True
    save_state(state)
    class TrackedCrawlab(Crawlab):
        def run_batch(self, *args):
            tid = super().run_batch(*args)
            state["tasks"] = [tid]
            state["publishing"] = False
            save_state(state)
            return tid
    try:
        task_id = publish_reserved_batch(TrackedCrawlab(api), relay, spider_id=state["sid"], node_id=state["node"],
                                        batch_id=state["batch"], m05_base_url="http://127.0.0.1:18081")
    except PublishUncertain:
        state["uncertain"] = True
        save_state(state)
        raise
    state["publishing"] = False
    state["tasks"] = [task_id]
    save_state(state)
    step(state, "task_wait")
    deadline = time.monotonic() + 75
    while True:
        task = api.request("GET", "/tasks/" + task_id)["data"]
        if task.get("status") in TERMINAL:
            break
        if time.monotonic() >= deadline:
            raise TaskError("Native Task did not reach a terminal state")
        time.sleep(1)
    if task.get("status") != "finished" or task.get("node_id") != state["node"]:
        raise TaskError("Native Task did not finish on the selected Worker")
    step(state, "sqlite_readback")
    checked = json.loads(docker(["exec", TASK, "python", "-B", "/poc.pyz", "--verify",
                                  "--run-id", state["run"], "--batch-id", state["batch"], "--task-id", task_id]))
    if checked.get("check") != "PASS":
        raise TaskError("Committed SQLite readback did not pass")
    emit("native_task", native_task_created=True, task_status="finished", task_id=task_id, **checked)


def cleanup_container(state):
    container_id = inspect(TASK, "{{.Id}}", missing=True)
    if container_id is None:
        return
    labels = json.loads(inspect(container_id, "{{json .Config.Labels}}") or "{}")
    if labels.get(OWNER) != state["run"] or labels.get(HASH) != state["sha"]:
        raise TaskError("Refusing to remove a container owned by another run")
    try:
        docker(["container", "rm", "--force", "--volumes", container_id], missing=True)
    finally:
        if inspect(container_id, "{{.Id}}", missing=True) is not None:
            raise TaskError("Temporary M05 container remains")


def cleanup(api, state):
    # A publication response may be lost while the server is still handling it.
    # Preserve the journal instead of claiming a complete concurrent cleanup.
    if state.get("uncertain") or state.get("publishing"):
        raise TaskError("Publication was uncertain; preserve this journal and report before cleanup")
    if state.get("spider_attempted"):
        if not state.get("baseline_absent"):
            raise TaskError("Missing namespace preflight proof")
        snapshot = mongo(state, "snapshot")
        state["tasks"] = sorted(set(state["tasks"] + snapshot["tasks"]))
        state["dc"] = snapshot["dc"] or state["dc"]
        save_state(state)
        for tid, status in zip(snapshot["tasks"], snapshot["statuses"]):
            if status not in TERMINAL:
                try:
                    api.request("POST", "/tasks/" + tid + "/cancel", {})
                except TaskError:
                    # The task may have finished between snapshot and cancel.
                    current = api.request("GET", "/tasks/" + tid)["data"]
                    if current.get("status") not in TERMINAL:
                        raise
        if node_files(state, WORKER, "stop").get("process_absent") is not True:
            raise TaskError("Synthetic Worker process did not stop")
        deadline = time.monotonic() + 15
        while True:
            snapshot = mongo(state, "snapshot")
            if all(status in TERMINAL for status in snapshot["statuses"]):
                break
            if time.monotonic() >= deadline:
                raise TaskError("Native cancellation was not confirmed")
            time.sleep(1)
        time.sleep(2)  # Allow native final stats/log delivery before removing owned records.
        filer(api, state, delete=True)
        if filer(api, state):
            raise TaskError("POC Filer directory remains")
        for node in (MASTER, WORKER):
            node_files(state, node, "prune")
        mongo(state, "prune")
        time.sleep(2)
        mongo(state, "verify")
        for node in (MASTER, WORKER):
            node_files(state, node, "verify")
    cleanup_container(state)


def remove_uploads():
    allowed = {UPLOAD.name, JOURNAL.name}
    if any(p.name not in allowed or p.is_symlink() for p in UPLOAD.parent.iterdir()):
        raise TaskError("Unexpected task-directory contents; refusing recursive cleanup")
    JOURNAL.unlink(missing_ok=True)
    UPLOAD.unlink()
    UPLOAD.parent.rmdir()


def host(cleanup_only=False):
    source = Path(sys.argv[0])
    if (source != UPLOAD or source.resolve() != UPLOAD or not source.is_file() or
            source.parent.is_symlink() or source.parent.parent.is_symlink()):
        raise TaskError("Use only the documented exact upload path")
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or not sys.stdin.isatty():
        raise TaskError("Run with sudo in an interactive SSH terminal")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if JOURNAL.exists() and not cleanup_only:
        raise TaskError("An existing trial journal requires review; do not rerun")
    if cleanup_only:
        if JOURNAL.is_symlink():
            raise TaskError("Journal symlink refused")
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
        if state.get("sha") != digest or state.get("task") != TASK:
            raise TaskError("Journal does not belong to this bundle")
    else:
        run_id = uuid.uuid4().hex
        state = {"task": TASK, "run": run_id, "sha": digest, "sid": os.urandom(12).hex(),
                 "name": "m05_native_" + run_id, "collection": "m05_native_" + run_id,
                 "node": "", "batch": "", "tasks": [], "dc": None}
        save_state(state)
    api = JsonClient("http://127.0.0.1:13003/api", timeout=15)
    ok = False
    try:
        if not cleanup_only:
            step(state, "runtime_preflight")
            runtime_preflight()
            step(state, "api_connection")
            api.request("GET", "/version")
        step(state, "login")
        authenticate(api, read=input, read_password=getpass.getpass)
        if not cleanup_only:
            trial(api, state)
        ok = True
    except (Exception, KeyboardInterrupt) as error:
        emit("native_task", check="FAIL", failed_step=state.get("step"),
             error=str(error)[:250] if isinstance(error, TaskError) else type(error).__name__)
    finally:
        try:
            step(state, "cleanup")
            cleanup(api, state)
            remove_uploads()
            emit("cleanup", check="PASS", native_records_absent=True, filer_absent=True,
                 node_files_absent=True, worker_process_absent=True, container_absent=True,
                 sqlite_absent=True, upload_absent=True, task_directory_absent=True)
        except (Exception, KeyboardInterrupt) as error:
            ok = False
            emit("cleanup", check="FAIL", error=str(error)[:250] if isinstance(error, TaskError) else type(error).__name__,
                 next="Stop and report this output; do not repeat --run")
        finally:
            api.token = None
    return 0 if ok else 1


def serve(run_id):
    from apps.proxypool.service.http_api import make_sqlite_api
    from apps.proxypool.service.quality import wilson_preview
    from apps.proxypool.service.storage import LocalStore
    if sqlite3.sqlite_version_info < (3, 24, 0):
        raise TaskError("SQLite UPSERT support is required")
    if not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
        raise TaskError("Invalid POC run identity")
    with LocalStore(DATABASE, score_fn=wilson_preview) as store:
        store.register_target("site-demo")
        for index in range(3):
            store.register_proxy("p" + str(index), "192.0.2." + str(index + 1), 8080)
        with make_sqlite_api(store, port=PORT) as server:
            # Mark readiness only after this container successfully owns the socket.
            # A conflicting existing service must never be mistaken for our M05.
            Path("/tmp/ready.json").write_text(json.dumps({"run": run_id}), encoding="utf-8")
            server.timeout = 0.3
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                server.handle_request()


def verify(batch_id, task_id):
    client = JsonClient("http://127.0.0.1:18081")
    batch = client.request("GET", "/internal/v1/check-batches/" + batch_id)["batch"]
    if batch.get("task_id") != task_id or len(batch["members"]) != 3:
        raise TaskError("Unexpected stored batch identity")
    qualities = [client.request("GET", "/internal/v1/quality?proxy_id=p{}&target_id=site-demo".format(i))["quality"]
                 for i in range(3)]
    if ([m["status"] for m in batch["members"]] != ["reported"] * 3 or
            [q["sample_count"] for q in qualities] != [1, 1, 1] or
            [q["success_count"] for q in qualities] != [1, 0, 1] or
            [q["test_status"] for q in qualities] != ["available"] * 3):
        raise TaskError("Stored feedback or test occupancy mismatch")
    ranked = client.request("GET", "/internal/v1/proxies?target_id=site-demo&limit=3&min_score=1")["proxies"]
    if [item["proxy_id"] for item in ranked] != ["p0", "p2"]:
        raise TaskError("Score-based proxy readback mismatch")
    # Independent read-only connection proves these facts are already committed.
    connection = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)
    try:
        counts = [connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                  for table in ("observations", "receipts")]
        if counts != [3, 3] or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise TaskError("Independent SQLite readback failed")
    finally:
        connection.close()
    return {"check": "PASS", "sent": 3, "persisted": True, "committed": True,
            "sample_counts": [1, 1, 1], "success_counts": [1, 0, 1],
            "sqlite_version": sqlite3.sqlite_version, "score_policy": "explicit_Wilson_preview"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("run", "cleanup", "serve", "request", "verify"):
        mode.add_argument("--" + name, action="store_true")
    parser.add_argument("--batch-id")
    parser.add_argument("--task-id")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.serve:
        serve(args.run_id)
        return 0
    if args.request or args.verify:
        ready = json.loads(Path("/tmp/ready.json").read_text(encoding="utf-8"))
        if not args.run_id or ready.get("run") != args.run_id:
            raise TaskError("Temporary M05 readiness identity mismatch")
    if args.request:
        request = json.load(sys.stdin)
        print(json.dumps(JsonClient("http://127.0.0.1:18081").request(
            request["method"], request["path"], request.get("payload"))))
        return 0
    if args.verify:
        print(json.dumps(verify(args.batch_id, args.task_id)))
        return 0
    def interrupted(_number, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    return host(cleanup_only=args.cleanup)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        emit("controller", check="FAIL", error=str(error)[:250] if isinstance(error, TaskError) else type(error).__name__)
        raise SystemExit(1)
