"""Real local HTTP/SQLite/processes; Crawlab API and task ID are test doubles."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from apps.proxypool.service.http_api import make_sqlite_api
from apps.proxypool.service.quality import wilson_preview
from apps.proxypool.service.storage import LocalStore


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "m05-native-task"
sys.path.insert(0, str(EXAMPLE))
from native_task import Crawlab, JsonClient, PublishUncertain, TaskError, publish_reserved_batch
import worker


TASK = "0123456789abcdef01234567"
SPIDER = "1123456789abcdef01234567"
NODE = "2123456789abcdef01234567"


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        if thread.is_alive():
            raise RuntimeError("Local fixture server failed to stop")


class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m05-native-prep-")
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(Path(self.temp.name) / "synthetic.sqlite3", score_fn=wilson_preview)
        self.addCleanup(self.store.close)
        self.store.register_target("site-demo")
        for index in range(3):
            self.store.register_proxy("p" + str(index), "192.0.2." + str(index + 1), 8080)
        self.server = self.enterContext(serving(make_sqlite_api(self.store)))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.m05 = JsonClient(self.base)
        self.batch = self.m05.request("POST", "/internal/v1/check-batches",
                                      {"request_id": "native-prep", "target_id": "site-demo", "limit": 3})["batch"]
        self.publish_calls = []
        self.api_status = 200
        self.api_body = {"data": [TASK]}
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.publish_calls.append((self.path, self.headers.get("Authorization"), body))
                data = json.dumps(test.api_body).encode("utf-8")
                self.send_response(test.api_status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        native = self.enterContext(serving(HTTPServer(("127.0.0.1", 0), Handler)))
        self.crawlab = Crawlab(JsonClient("http://127.0.0.1:{}/api".format(native.server_port), token="lab-token"))

    def publish(self, *, m05=None):
        return publish_reserved_batch(self.crawlab, m05 or self.m05, spider_id=SPIDER, node_id=NODE,
                                      batch_id=self.batch["batch_id"], m05_base_url=self.base)

    def assert_pending(self):
        for index in range(3):
            quality = self.store.quality("p" + str(index), "site-demo")
            self.assertEqual(quality["test_status"], "pending")
            self.assertEqual(quality["sample_count"], 0)

    def process(self, *, task_id=TASK):
        environment = os.environ.copy()
        environment.pop("CRAWLAB_TASK_ID", None)
        if task_id is not None:
            environment["CRAWLAB_TASK_ID"] = task_id
        command = [sys.executable, "-B", str(EXAMPLE / "worker.py"),
                   "--batch-id", self.batch["batch_id"], "--m05-base-url", self.base]
        process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_worker_starts_before_binding_then_posts_once_and_commits(self):
        process = self.process()
        time.sleep(0.15)
        self.assertIsNone(process.poll(), "Worker should wait for publisher binding")
        self.assertEqual(self.publish(), TASK)
        output, error = process.communicate(timeout=5)
        self.assertEqual((process.returncode, error), (0, ""))
        self.assertEqual(json.loads(output)["task_id"], TASK)
        path, auth, payload = self.publish_calls[0]
        self.assertEqual(path, "/api/tasks/run")
        self.assertEqual(auth, "lab-token")
        self.assertEqual(payload["mode"], "selected-nodes")
        self.assertEqual(payload["node_ids"], [NODE])
        self.assertIn("--batch-id " + self.batch["batch_id"], payload["param"])
        self.assertNotIn("params", payload)
        self.assertEqual(len(self.publish_calls), 1)
        snapshot = self.store.read_batch(self.batch["batch_id"])
        self.assertEqual(snapshot["task_id"], TASK)
        self.assertEqual([item["status"] for item in snapshot["members"]], ["reported"] * 3)
        for index, successes in enumerate((1, 0, 1)):
            quality = self.m05.request("GET", "/internal/v1/quality?proxy_id=p{}&target_id=site-demo".format(index))["quality"]
            self.assertEqual((quality["test_status"], quality["sample_count"], quality["success_count"]),
                             ("available", 1, successes))

    def test_missing_native_id_exits_unsuccessfully_without_results(self):
        process = self.process(task_id=None)
        output, error = process.communicate(timeout=5)
        self.assertEqual((process.returncode, output), (1, ""))
        self.assertEqual(json.loads(error)["check"], "FAIL")
        self.assert_pending()

    def test_another_native_id_cannot_report_this_batch(self):
        self.publish()
        process = self.process(task_id="3123456789abcdef01234567")
        output, error = process.communicate(timeout=5)
        self.assertEqual((process.returncode, output), (1, ""))
        self.assertEqual(json.loads(error)["check"], "FAIL")
        self.assert_pending()

    def test_readiness_timeout_only_reads_and_does_not_retry_a_proxy_test(self):
        client = Mock()
        client.request.return_value = {"batch": self.batch}
        ticks = iter((0, 2))
        with self.assertRaisesRegex(TaskError, "readiness deadline"):
            worker.run(client, self.batch["batch_id"], TASK, wait_seconds=1,
                       monotonic=lambda: next(ticks), sleep=lambda delay: None)
        self.assertTrue(all(call.args[0] == "GET" for call in client.request.call_args_list))
        self.assert_pending()

    def test_receive_only_ack_cannot_be_reported_as_a_successful_task(self):
        self.publish()
        batch = self.store.read_batch(self.batch["batch_id"])
        client = Mock()
        client.request.side_effect = [{"batch": batch}, {"received": True, "persisted": False}]
        with self.assertRaisesRegex(TaskError, "committed results"):
            worker.run(client, self.batch["batch_id"], TASK)
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ["GET", "POST"])
        self.assert_pending()

    def test_callback_transport_error_is_not_retried_or_scored_as_proxy_failure(self):
        self.publish()
        client = Mock()
        client.request.side_effect = [{"batch": self.store.read_batch(self.batch["batch_id"])}, TaskError("lost response")]
        with self.assertRaisesRegex(TaskError, "lost response"):
            worker.run(client, self.batch["batch_id"], TASK)
        self.assertEqual(client.request.call_count, 2)
        self.assert_pending()

    def test_already_bound_batch_is_never_published_twice(self):
        self.publish()
        with self.assertRaisesRegex(TaskError, "already bound"):
            self.publish()
        self.assertEqual(len(self.publish_calls), 1)

    def test_binding_failure_reports_known_task_id_without_republication(self):
        m05 = Mock()
        m05.request.side_effect = [{"batch": self.batch}, TaskError("response lost")]
        with self.assertRaisesRegex(TaskError, TASK + ".*do not republish"):
            self.publish(m05=m05)
        self.assertEqual(len(self.publish_calls), 1)
        self.assert_pending()

    def test_server_error_after_run_is_ambiguous_and_never_retried(self):
        self.api_status = 500
        self.api_body = {"error": "simulated"}
        with self.assertRaises(PublishUncertain):
            self.publish()
        self.assertEqual(len(self.publish_calls), 1)
        self.assert_pending()

    def test_multiple_native_ids_are_rejected_without_republication(self):
        self.api_body = {"data": [TASK, "3123456789abcdef01234567"]}
        with self.assertRaises(PublishUncertain):
            self.publish()
        self.assertEqual(len(self.publish_calls), 1)
        self.assert_pending()

    def test_empty_reserved_batch_is_not_published(self):
        self.batch["members"] = []
        m05 = Mock()
        m05.request.return_value = {"batch": self.batch}
        with self.assertRaisesRegex(TaskError, "nonempty"):
            self.publish(m05=m05)
        self.assertEqual(self.publish_calls, [])

    def test_invalid_ids_are_rejected_before_native_mutation(self):
        for bad in ("short", "A" * 24, "x; other-command"):
            with self.subTest(value=bad), self.assertRaises(TaskError):
                self.crawlab.run_batch(bad, NODE, self.batch["batch_id"], self.base)
        self.assertEqual(self.publish_calls, [])

    def test_transport_refuses_remote_destinations_and_embedded_credentials(self):
        for bad in ("https://127.0.0.1", "http://192.168.1.110", "http://localhost",
                    "http://user:password@127.0.0.1", self.base + "/untrusted", self.base + "?q=1"):
            with self.subTest(value=bad), self.assertRaises(TaskError):
                JsonClient(bad)

    def test_redirect_is_an_error_and_does_not_trigger_another_request(self):
        self.api_status = 302
        with self.assertRaises(PublishUncertain):
            self.publish()
        self.assertEqual(len(self.publish_calls), 1)

    def test_http_error_identifies_route_without_logging_query_credentials_or_bodies(self):
        self.api_status = 500
        self.api_body = {"error": "private-response-content"}
        with self.assertRaises(TaskError) as failure:
            self.crawlab.client.request("POST", "/login?diagnostic=private-query-content",
                                        {"password": "private-password-content"})
        message = str(failure.exception)
        self.assertIn("POST /api/login returned HTTP 500", message)
        for private in ("private-response-content", "private-query-content", "private-password-content", "lab-token"):
            self.assertNotIn(private, message)


if __name__ == "__main__":
    unittest.main()
