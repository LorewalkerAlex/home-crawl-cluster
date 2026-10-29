from contextlib import ExitStack, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/m05-native-task"
sys.path.insert(0, str(EXAMPLE))

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, EXAMPLE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

poc = module("native_server_poc", "server_poc.py")
files = module("native_cleanup_files", "cleanup_files.py")
builder = module("native_bundle_builder", "build_bundle.py")


class NativeServerPocTests(unittest.TestCase):
    def test_node_query_supplies_positive_page_for_pinned_crawlab_pagination(self):
        # Model the pinned external API's zero-valued page binding and Mongo skip.
        routes = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                routes.append(self.path)
                query = parse_qs(urlsplit(self.path).query)
                page = int(query.get("page", ["0"])[0])
                size = int(query.get("size", ["0"])[0])
                skip = size * (page - 1)
                node = {"_id": "a"*24, "name": "home-worker-01", "is_master": False,
                        "enabled": True, "status": "on", "available_runners": 1, "max_runners": 1}
                body = json.dumps({"data": [node], "total": 1} if skip >= 0 else {"error": "negative skip"}).encode()
                self.send_response(200 if skip >= 0 else 500)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                api = poc.JsonClient("http://127.0.0.1:{}/api".format(server.server_port))
                with self.assertRaisesRegex(poc.TaskError, "GET /api/nodes returned HTTP 500"):
                    api.request("GET", "/nodes?size=100")
                self.assertEqual(poc.choose_node(api), "a"*24)
                self.assertEqual(routes, ["/api/nodes?size=100", "/api/nodes?page=1&size=100"])
            finally:
                server.shutdown()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())

    def test_real_mongo_cleanup_source_scopes_deletes_and_refuses_conflicts_before_any_write(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed only for offline mongosh-script checks")
        result = subprocess.run([node, str(ROOT / "tests/native_cleanup_fixture.js"),
                                 str(EXAMPLE / "cleanup_native.js")], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"check": "PASS", "scenarios": 21})

    def test_container_cleanup_uses_verified_id_and_keeps_another_owner(self):
        state = {"run": "owner", "sha": "hash"}
        with patch.object(poc, "inspect", side_effect=["cid", '{"local.m05.native-poc":"foreign"}']), \
                patch.object(poc, "docker") as docker:
            with self.assertRaises(poc.TaskError):
                poc.cleanup_container(state)
            docker.assert_not_called()
        labels = json.dumps({poc.OWNER: "owner", poc.HASH: "hash"})
        with patch.object(poc, "inspect", side_effect=["cid", labels, None]), patch.object(poc, "docker") as docker:
            poc.cleanup_container(state)
            self.assertEqual(docker.call_args.args[0], ["container", "rm", "--force", "--volumes", "cid"])

    def test_uncertain_publication_preserves_records_and_container_for_review(self):
        with patch.object(poc, "mongo") as mongo, patch.object(poc, "cleanup_container") as container:
            with self.assertRaisesRegex(poc.TaskError, "uncertain"):
                poc.cleanup(Mock(), {"uncertain": True})
            mongo.assert_not_called()
            container.assert_not_called()

    def test_cleanup_orders_process_stop_filer_files_and_database_before_container(self):
        state = {"spider_attempted": True, "baseline_absent": True, "tasks": [], "dc": None}
        snapshot = {"tasks": ["b"*24], "statuses": ["finished"], "dc": "c"*24}
        events = []
        def database(_state, action):
            events.append("db:" + action)
            return snapshot
        def node(_state, name, action):
            events.append("node:" + action)
            return {"process_absent": True, "files_absent": True}
        with patch.object(poc, "mongo", side_effect=database), patch.object(poc, "node_files", side_effect=node), \
                patch.object(poc, "save_state"), patch.object(poc.time, "sleep"), \
                patch.object(poc, "filer", side_effect=lambda *a, **k: events.append("filer") or False), \
                patch.object(poc, "cleanup_container", side_effect=lambda s: events.append("container")):
            poc.cleanup(Mock(), state)
        self.assertLess(events.index("node:stop"), events.index("filer"))
        self.assertLess(events.index("node:prune"), events.index("db:prune"))
        self.assertLess(events.index("db:verify"), events.index("container"))
        self.assertEqual(state["tasks"], ["b"*24])

    def test_no_database_prune_if_worker_process_still_exists(self):
        state = {"spider_attempted": True, "baseline_absent": True, "tasks": [], "dc": None}
        snap = {"tasks": ["b"*24], "statuses": ["finished"], "dc": None}
        with patch.object(poc, "mongo", return_value=snap) as database, patch.object(poc, "save_state"), \
                patch.object(poc, "node_files", return_value={"process_absent": False}):
            with self.assertRaisesRegex(poc.TaskError, "did not stop"):
                poc.cleanup(Mock(), state)
            self.assertEqual(database.call_count, 1)

    def test_unexpected_upload_file_prevents_any_upload_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "m05_native.pyz"
            journal = Path(directory) / "state.json"
            source.write_text("poc")
            journal.write_text("{}")
            (Path(directory) / "unexpected").write_text("preserve")
            with patch.object(poc, "UPLOAD", source), patch.object(poc, "JOURNAL", journal):
                with self.assertRaisesRegex(poc.TaskError, "Unexpected"):
                    poc.remove_uploads()
            self.assertTrue(source.exists())
            self.assertTrue(journal.exists())

    def test_journal_is_replaced_atomically_and_contains_no_password_or_token(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "state.json"
            with patch.object(poc, "JOURNAL", journal):
                poc.save_state({"task": poc.TASK, "tasks": []})
                poc.save_state({"task": poc.TASK, "tasks": ["b"*24]})
            self.assertEqual(json.loads(journal.read_text())["tasks"], ["b"*24])
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["state.json"])
            self.assertNotIn("token", journal.read_text())

    def test_node_cleanup_rejects_path_escape_before_any_removal(self):
        for sid in ("../anything", "/", "a"*25):
            with self.subTest(sid=sid), patch.object(files.shutil, "rmtree") as remove:
                with self.assertRaises(RuntimeError):
                    files.checked_targets({"sid": sid, "tasks": []})
                remove.assert_not_called()

    def test_node_cleanup_removes_only_owned_directory_and_keeps_other_spiders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bases = ["/root/crawlab_workspace", "/root/crawlab_repo", "/workspace", "/repo", "/spiders"]
            mapping = {name: root / str(index) for index, name in enumerate(bases)}
            sid = "a" * 24
            owned = mapping[bases[0]] / sid
            owned.mkdir(parents=True)
            (owned / "worker.py").write_text("owned")
            other = mapping[bases[0]] / ("b" * 24)
            other.mkdir()
            (other / "keep").write_text("existing")
            with patch.object(files, "Path", side_effect=lambda name: mapping[name]):
                result = files.run({"sid": sid, "tasks": [], "batch": "", "action": "prune"})
            self.assertTrue(result["files_absent"])
            self.assertFalse(owned.exists())
            self.assertEqual((other / "keep").read_text(), "existing")

    def test_image_volumes_are_bounded_tmpfs_and_dangerous_mounts_rejected(self):
        flags = poc.temporary_mounts({"/data": {}, "/root/.crawlab": {}})
        self.assertIn("/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777", flags)
        self.assertIn("/data:rw,nosuid,nodev,noexec,size=4m,mode=1777", flags)
        for path in ("/", "/usr/lib", "/tmp,other=1", "/poc.pyz"):
            with self.subTest(path=path), self.assertRaises(poc.TaskError):
                poc.temporary_mounts({path: {}})

    def test_filer_key_stays_inside_master_and_management_jwt_is_not_forwarded(self):
        api = Mock(token="management-token-must-not-be-forwarded")
        with patch.object(poc, "docker", return_value='{"exists": false}') as docker:
            self.assertFalse(poc.filer(api, {"sid": "a"*24}, delete=True))
        arguments = docker.call_args.args[0]
        self.assertEqual(arguments[:5], ["exec", "-i", poc.MASTER, "python", "-B"])
        self.assertNotIn(api.token, str(docker.call_args))
        self.assertIn('os.environ["CRAWLAB_FS_FILER_AUTHKEY"]', arguments[-1])

    def test_wrong_readiness_identity_cannot_send_any_request_to_an_existing_service(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.json"
            ready.write_text('{"run":"another-container"}')
            with patch.object(poc, "Path", return_value=ready), \
                    patch.object(poc.sys, "argv", ["poc.pyz", "--request", "--run-id", "our-container"]), \
                    patch.object(poc, "JsonClient") as client:
                with self.assertRaisesRegex(poc.TaskError, "readiness identity"):
                    poc.main()
                client.assert_not_called()

    def test_verifier_reads_real_http_scores_and_an_independent_committed_sqlite_connection(self):
        from apps.proxypool.service.http_api import make_sqlite_api
        from apps.proxypool.service.storage import LocalStore
        from apps.proxypool.service.quality import wilson_preview
        from native_task import JsonClient
        import worker
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "synthetic.sqlite3"
            with LocalStore(path, score_fn=wilson_preview) as store:
                store.register_target("site-demo")
                for index in range(3):
                    store.register_proxy("p" + str(index), "192.0.2." + str(index + 1), 8080)
                with make_sqlite_api(store) as server:
                    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
                    thread.start()
                    try:
                        client = JsonClient("http://127.0.0.1:" + str(server.server_port))
                        batch = client.request("POST", "/internal/v1/check-batches",
                                               {"request_id": "verify", "target_id": "site-demo", "limit": 3})["batch"]
                        task_id = "b" * 24
                        client.request("POST", "/internal/v1/check-batches/" + batch["batch_id"] + "/task", {"task_id": task_id})
                        worker.run(client, batch["batch_id"], task_id)
                        with patch.object(poc, "DATABASE", path), patch.object(poc, "JsonClient", return_value=client):
                            result = poc.verify(batch["batch_id"], task_id)
                        self.assertEqual(result["success_counts"], [1, 0, 1])
                        self.assertTrue(result["committed"])
                    finally:
                        server.shutdown()
                        thread.join(timeout=3)
                        self.assertFalse(thread.is_alive())

    def test_cleanup_failure_preserves_upload_and_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "m05_native.pyz"
            source.write_text("reviewed fixture")
            journal = Path(directory) / "state.json"
            api = Mock()
            api.request.return_value = {"data": "ephemeral-test-token"}
            output = io.StringIO()
            with patch.object(poc, "UPLOAD", source), patch.object(poc, "JOURNAL", journal), \
                    patch.object(poc.sys, "argv", [str(source)]), patch.object(poc.sys.stdin, "isatty", return_value=True), \
                    patch.object(poc.os, "geteuid", return_value=0, create=True), patch("builtins.input", return_value="admin"), \
                    patch.object(poc.getpass, "getpass", return_value="synthetic-secret"), \
                    patch.object(poc, "JsonClient", return_value=api), patch.object(poc, "trial"), \
                    patch.object(poc, "runtime_preflight"), \
                    patch.object(poc, "cleanup", side_effect=poc.TaskError("cleanup failed")), redirect_stdout(output):
                self.assertEqual(poc.host(), 1)
            self.assertTrue(source.exists())
            self.assertTrue(journal.exists())
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["check"], "FAIL")
            self.assertIsNone(api.token)

    def test_runtime_failure_does_not_ask_for_password_or_create_native_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "m05_native.pyz"
            source.write_text("fixture")
            journal = source.parent / "state.json"
            api = Mock(token=None)
            output = io.StringIO()
            with patch.object(poc, "UPLOAD", source), patch.object(poc, "JOURNAL", journal), \
                    patch.object(poc.sys, "argv", [str(source)]), patch.object(poc.sys.stdin, "isatty", return_value=True), \
                    patch.object(poc.os, "geteuid", return_value=0, create=True), \
                    patch.object(poc, "JsonClient", return_value=api), \
                    patch.object(poc, "runtime_preflight", side_effect=poc.TaskError("runtime unavailable")), \
                    patch.object(poc.getpass, "getpass") as password, patch.object(poc, "trial") as trial, \
                    patch.object(poc, "cleanup_container"), redirect_stdout(output):
                self.assertEqual(poc.host(), 1)
            password.assert_not_called()
            trial.assert_not_called()
            api.request.assert_not_called()
            self.assertFalse(source.exists())
            self.assertFalse(journal.exists())
            self.assertIsNone(api.token)
            failure = [json.loads(line) for line in output.getvalue().splitlines()
                       if json.loads(line)["phase"] == "native_task"][0]
            self.assertEqual(failure["failed_step"], "runtime_preflight")

    def test_runtime_script_detects_occupied_port_and_invalid_source_without_executing_it(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            script = poc.RUNTIME_CHECK.replace("18081", str(listener.getsockname()[1]))
            for source, reason in (("raise RuntimeError('must not run')", "worker_loopback_port_unavailable"),
                                   ("def incomplete(", "bundle_syntax_incompatible_with_worker")):
                result = subprocess.run([sys.executable, "-B", "-c", script],
                    input=json.dumps({"worker.py": source}), text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual((report["check"], report["reason"]), ("FAIL", reason))
                self.assertNotIn(source, result.stdout)
                # Empty the listener backlog before the next independent probe.
                with listener.accept()[0]:
                    pass

    def test_unified_host_reuses_login_for_real_http_task_process_callback_and_sqlite_readback(self):
        # Crawlab and Docker are substitutes; M05/SQLite, uploaded Worker code and
        # the Python child process are real. This is not release compatibility proof.
        from apps.proxypool.service.http_api import make_sqlite_api
        from apps.proxypool.service.storage import LocalStore
        from apps.proxypool.service.quality import wilson_preview
        from native_task import JsonClient
        from test_m05_native_preparation import serving
        native_id, node_id, dc = "b" * 24, "a" * 24, "c" * 24
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            task_directory = root / "upload"
            task_directory.mkdir()
            source = task_directory / "m05_native.pyz"
            builder.build(ROOT, source)
            journal = task_directory / "state.json"
            worker_directory = root / "worker"
            worker_directory.mkdir()
            database = root / "synthetic.sqlite3"
            requests, state, processes, events = [], {}, [], []
            with LocalStore(database, score_fn=wilson_preview) as store:
                store.register_target("site-demo")
                for i in range(3):
                    store.register_proxy("p" + str(i), "192.0.2." + str(i + 1), 8080)
                with serving(make_sqlite_api(store)) as m05_server:
                    m05_url = "http://127.0.0.1:" + str(m05_server.server_port)
                    m05 = JsonClient(m05_url)
                    class Platform(BaseHTTPRequestHandler):
                        def answer(self, data):
                            body = json.dumps(data).encode()
                            self.send_response(200)
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)
                        def do_GET(self):
                            requests.append(("GET", self.path, self.headers.get("Authorization")))
                            if self.path == "/api/version":
                                return self.answer({"data": "v0.6.2"})
                            if self.path == "/api/nodes?page=1&size=100":
                                return self.answer({"data": [{"_id": node_id, "name": "home-worker-01",
                                    "is_master": False, "enabled": True, "status": "on",
                                    "available_runners": 1, "max_runners": 1}], "total": 1})
                            if "/files/get?" in self.path:
                                filename = parse_qs(urlsplit(self.path).query)["path"][0].lstrip("/")
                                return self.answer({"data": (worker_directory / filename).read_text()})
                            if self.path == "/api/tasks/" + native_id:
                                code = processes[0].poll()
                                return self.answer({"data": {"status": "running" if code is None else
                                    ("finished" if code == 0 else "error"), "node_id": node_id}})
                            raise AssertionError(self.path)
                        def do_POST(self):
                            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                            requests.append(("POST", self.path, self.headers.get("Authorization")))
                            if self.path == "/api/login":
                                return self.answer({"data": "one-process-token"})
                            if self.path == "/api/spiders":
                                state.update(payload)
                                return self.answer({"data": {"_id": payload["_id"], "col_id": dc}})
                            if self.path.endswith("/files/save"):
                                (worker_directory / payload["path"].lstrip("/")).write_text(payload["data"], encoding="utf-8")
                                return self.answer({})
                            if self.path == "/api/tasks/run":
                                batch = payload["param"].split()[1]
                                env = dict(os.environ, CRAWLAB_TASK_ID=native_id)
                                processes.append(subprocess.Popen([sys.executable, "-B", "worker.py", "--batch-id", batch,
                                    "--m05-base-url", m05_url], cwd=worker_directory, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
                                return self.answer({"data": [native_id]})
                            raise AssertionError(self.path)
                        def log_message(self, *args):
                            pass
                    with serving(HTTPServer(("127.0.0.1", 0), Platform)) as platform:
                        api = JsonClient("http://127.0.0.1:{}/api".format(platform.server_port))
                        def local_client(url, **kwargs):
                            return api if url.endswith("13003/api") else JsonClient(m05_url, **kwargs)
                        def database_action(current, action):
                            events.append(action)
                            return {"tasks": [native_id], "statuses": ["finished"], "dc": dc}
                        def docker(args, **kwargs):
                            self.assertIn("--verify", args)
                            return json.dumps(poc.verify(args[args.index("--batch-id") + 1], native_id))
                        output = io.StringIO()
                        try:
                            password = Mock(return_value="hidden-fixture-password")
                            with ExitStack() as stack:
                                for context in (
                                    patch.object(poc, "UPLOAD", source), patch.object(poc, "JOURNAL", journal),
                                    patch.object(poc, "DATABASE", database), patch.object(poc.sys, "argv", [str(source)]),
                                    patch.object(poc.sys.stdin, "isatty", return_value=True),
                                    patch.object(poc.os, "geteuid", return_value=0, create=True),
                                    patch("builtins.input", return_value="admin"), patch.object(poc.getpass, "getpass", password),
                                    patch.object(poc, "JsonClient", side_effect=local_client), patch.object(poc, "runtime_preflight"),
                                    patch.object(poc, "mongo", side_effect=database_action), patch.object(poc, "node_files",
                                        return_value={"files_absent": True, "process_absent": True}),
                                    patch.object(poc, "filer", return_value=False), patch.object(poc, "start_m05", return_value=m05),
                                    patch.object(poc, "docker", side_effect=docker),
                                    patch.object(poc, "cleanup_container", side_effect=lambda s: events.append("container_removed")),
                                    redirect_stdout(output)):
                                    stack.enter_context(context)
                                self.assertEqual(poc.host(), 0, output.getvalue())
                            password.assert_called_once()
                            self.assertEqual(sum(p == "/api/login" for _, p, _ in requests), 1)
                            self.assertEqual(sum(p == "/api/tasks/run" for _, p, _ in requests), 1)
                            self.assertTrue(all(token == "one-process-token" for _, path, token in requests
                                                if path not in ("/api/login", "/api/version")))
                            self.assertIsNone(api.token)
                            self.assertFalse(task_directory.exists())
                            self.assertLess(events.index("prune"), events.index("container_removed"))
                            reports = [json.loads(line) for line in output.getvalue().splitlines()]
                            success = next(r for r in reports if r["phase"] == "native_task")
                            self.assertEqual(success["success_counts"], [1, 0, 1])
                            self.assertEqual(reports[-1]["phase"], "cleanup")
                            self.assertEqual(reports[-1]["check"], "PASS")
                            self.assertNotIn("hidden-fixture-password", output.getvalue())
                            self.assertNotIn("one-process-token", output.getvalue())
                        finally:
                            for process in processes:
                                if process.poll() is None:
                                    process.kill()
                                process.communicate(timeout=3)

    def test_bundle_allowlist_is_deterministic_and_has_no_database_or_credentials(self):
        import apps.proxypool.service as actual_service
        service = Path(actual_service.__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            shutil.copytree(EXAMPLE, root / "examples/m05-native-task")
            shutil.copytree(service, root / "apps/proxypool/service")
            first = Path(directory) / "one.pyz"
            second = Path(directory) / "two.pyz"
            self.assertEqual(builder.build(root, first), builder.build(root, second))
            with self.assertRaises(FileExistsError):
                builder.build(root, first)
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(len(archive.namelist()), 14)
                self.assertFalse(any(name.endswith((".db", ".sqlite3", ".env", "state.json")) for name in archive.namelist()))
                self.assertIn("cleanup_native.js", archive.namelist())
                self.assertIn("apps/proxypool/service/storage.py", archive.namelist())

    def test_trial_failure_still_attempts_cleanup_and_preserves_failure_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "m05_native.pyz"
            source.write_text("reviewed fixture")
            journal = Path(directory) / "state.json"
            api = Mock()
            api.request.return_value = {"data": "ephemeral-test-token"}
            output = io.StringIO()
            with patch.object(poc, "UPLOAD", source), patch.object(poc, "JOURNAL", journal), \
                    patch.object(poc.sys, "argv", [str(source)]), patch.object(poc.sys.stdin, "isatty", return_value=True), \
                    patch.object(poc.os, "geteuid", return_value=0, create=True), patch("builtins.input", return_value="admin"), \
                    patch.object(poc.getpass, "getpass", return_value="synthetic-secret"), \
                    patch.object(poc, "JsonClient", return_value=api), patch.object(poc, "trial", side_effect=poc.TaskError("trial failed")), \
                    patch.object(poc, "runtime_preflight"), \
                    patch.object(poc, "cleanup") as cleanup, patch.object(poc, "remove_uploads") as uploads, redirect_stdout(output):
                self.assertEqual(poc.host(), 1)
            cleanup.assert_called_once()
            uploads.assert_called_once()
            self.assertNotIn("synthetic-secret", output.getvalue())
            self.assertNotIn("ephemeral-test-token", journal.read_text())
            finals = [json.loads(line) for line in output.getvalue().splitlines()
                      if json.loads(line)["phase"] in ("native_task", "cleanup") and json.loads(line)["check"] != "START"]
            self.assertEqual([line["check"] for line in finals], ["FAIL", "PASS"])
            self.assertIsNone(api.token)


if __name__ == "__main__":
    unittest.main()
