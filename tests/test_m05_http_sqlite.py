from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from apps.proxypool.service.contracts import Result, ValidationError
from apps.proxypool.service.http_api import make_sqlite_api
from apps.proxypool.service.quality import wilson_preview
from apps.proxypool.service.storage import Limits, LocalStore


NOW = 1_793_052_000_000


class HttpSqliteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m05-http-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "isolated.sqlite3"
        limits = Limits(sample_window_ms=10_000, check_interval_ms=100,
                        check_timeout_ms=1000, receipt_retention_ms=20_000, clock_skew_ms=1000)
        self.store = LocalStore(self.path, score_fn=wilson_preview, limits=limits)
        self.addCleanup(self.store.close)
        self.store.register_target("site-demo")
        self.store.register_proxy("p0", "192.0.2.1", 8080)
        self.store.register_proxy("p1", "192.0.2.2", 8080)
        self.now = NOW
        self.server = make_sqlite_api(self.store, clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, payload=None, *, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def reserve(self, request_id="request-demo", count=2, *, bind=True, task_id="task-demo"):
        status, body = self.request("POST", "/internal/v1/check-batches",
                                    {"request_id": request_id, "target_id": "site-demo", "limit": count})
        self.assertEqual(status, 200)
        self.assertTrue(body["committed"])
        batch = body["batch"]
        if bind:
            status, result = self.request("POST", f"/internal/v1/check-batches/{batch['batch_id']}/task",
                                          {"task_id": task_id})
            self.assertEqual(status, 200)
            self.assertTrue(result["committed"])
        return batch

    def callback(self, batch, *, task_id="task-demo", statuses=(200, 503)):
        records = [Result(member["check_id"], member["proxy_id"], "site-demo", 1,
                          self.now, "response", statuses[index % len(statuses)], 80+index)
                   for index, member in enumerate(batch["members"])]
        return {"schema_version": 1, "batch_id": batch["batch_id"], "task_id": task_id,
                "records": [record.to_wire() for record in records]}

    def post_results(self, payload):
        return self.request("POST", "/internal/v1/check-results", payload)

    def quality(self, proxy="p0"):
        status, body = self.request("GET", f"/internal/v1/quality?proxy_id={proxy}&target_id=site-demo")
        self.assertEqual(status, 200)
        return body["quality"]

    def database_scalar(self, sql):
        with closing(sqlite3.connect(self.path)) as independent_reader:
            return independent_reader.execute(sql).fetchone()[0]

    def test_reserve_http_marks_members_and_replay_does_not_reserve_another_batch(self):
        first = self.reserve(bind=False)
        self.assertEqual(self.quality()["test_status"], "pending")
        second = self.reserve(bind=False)
        self.assertEqual(first, second)
        status, body = self.request("POST", "/internal/v1/check-batches",
                                    {"request_id": "another", "target_id": "site-demo", "limit": 2})
        self.assertEqual(status, 200)
        self.assertEqual(body["batch"]["members"], [])
        self.assertEqual(self.database_scalar("SELECT count(*) FROM checks"), 2)

    def test_roundtrip_commits_before_ack_and_can_be_read_through_http_and_sqlite(self):
        batch = self.reserve()
        status, ack = self.post_results(self.callback(batch))
        self.assertEqual(status, 200)
        self.assertTrue(ack["persisted"])
        self.assertTrue(ack["committed"])
        self.assertEqual((ack["accepted"], ack["duplicates"]), (2, 0))
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 2)
        for proxy, successes in [("p0", 1), ("p1", 0)]:
            quality = self.quality(proxy)
            self.assertEqual(quality["test_status"], "available")
            self.assertEqual(quality["success_count"], successes)
            self.assertEqual(quality["last_feedback_ms"], NOW)
        status, body = self.request("GET", f"/internal/v1/check-batches/{batch['batch_id']}")
        self.assertEqual(status, 200)
        self.assertEqual({member["status"] for member in body["batch"]["members"]}, {"reported"})
        self.assertEqual(body["batch"]["task_id"], "task-demo")

    def test_duplicate_http_callback_does_not_count_or_refresh_evidence_twice(self):
        batch = self.reserve()
        payload = self.callback(batch)
        self.assertEqual(self.post_results(payload)[0], 200)
        before = self.quality()
        self.now += 200
        status, ack = self.post_results(payload)
        self.assertEqual(status, 200)
        self.assertEqual((ack["accepted"], ack["duplicates"]), (0, 2))
        self.assertEqual(self.quality(), before)
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 2)

    def test_conflicting_retransmission_returns_409_without_overwriting_old_score(self):
        batch = self.reserve()
        payload = self.callback(batch)
        self.post_results(payload)
        before = self.quality()
        payload["records"][0]["http_status"] = 500
        status, body = self.post_results(payload)
        self.assertEqual((status, body), (409, {"error": "identity_conflict", "committed": False}))
        self.assertEqual(self.quality(), before)

    def test_task_binding_is_required_immutable_and_durable(self):
        batch = self.reserve(bind=False)
        status, body = self.post_results(self.callback(batch))
        self.assertEqual(status, 409)
        self.assertFalse(body["committed"])
        path = f"/internal/v1/check-batches/{batch['batch_id']}/task"
        for _ in range(2):
            self.assertEqual(self.request("POST", path, {"task_id": "task-demo"})[0], 200)
        self.assertEqual(self.request("POST", path, {"task_id": "other-task"})[0], 409)
        self.assertEqual(self.post_results(self.callback(batch, task_id="other-task"))[0], 409)
        with LocalStore(self.path, score_fn=wilson_preview, limits=self.store.limits) as reopened:
            self.assertEqual(reopened.read_batch(batch["batch_id"])["task_id"], "task-demo")
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 0)
        self.assertEqual(self.post_results(self.callback(batch))[0], 200)

    def test_task_identity_cannot_be_bound_to_a_second_batch(self):
        self.reserve(count=1)
        second = self.reserve("second", 1, bind=False)
        path = f"/internal/v1/check-batches/{second['batch_id']}/task"
        self.assertEqual(self.request("POST", path, {"task_id": "task-demo"})[0], 409)

    def test_wrong_member_in_a_package_rolls_back_the_entire_http_submission(self):
        batch = self.reserve()
        payload = self.callback(batch)
        payload["records"][1]["proxy_id"] = "p0"
        status, body = self.post_results(payload)
        self.assertEqual(status, 422)
        self.assertFalse(body["committed"])
        self.assertEqual(self.database_scalar("SELECT count(*) FROM receipts"), 0)
        self.assertIsNone(self.quality()["score"])
        self.assertEqual(self.quality()["test_status"], "pending")

    def test_unknown_revision_does_not_change_current_quality(self):
        batch = self.reserve()
        payload = self.callback(batch)
        payload["records"][0]["target_revision"] = 2
        self.assertEqual(self.post_results(payload)[0], 422)
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 0)

    def test_actual_sqlite_commit_failure_returns_503_and_preserves_pending_state(self):
        batch = self.reserve()
        payload = self.callback(batch)
        with closing(sqlite3.connect(self.path)) as setup:
            setup.executescript("""
                CREATE TABLE commit_guard (
                    proxy_id TEXT REFERENCES proxies(proxy_id) DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TRIGGER fail_commit AFTER INSERT ON observations BEGIN
                    INSERT INTO commit_guard VALUES('missing-proxy');
                END;
            """)
        status, body = self.post_results(payload)
        self.assertEqual((status, body), (503, {"error": "storage_unavailable", "committed": False}))
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 0)
        self.assertEqual(self.database_scalar("SELECT count(*) FROM receipts"), 0)
        self.assertEqual(self.quality()["test_status"], "pending")
        with closing(sqlite3.connect(self.path)) as setup:
            setup.execute("DROP TRIGGER fail_commit")
            setup.commit()
        self.assertTrue(self.post_results(payload)[1]["committed"])

    def test_http_success_is_not_returned_while_transaction_is_still_open(self):
        batch = self.reserve(count=1)
        entered, release = threading.Event(), threading.Event()
        def blocked_score(successes, count):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("Test did not release the transaction")
            return wilson_preview(successes, count)
        with patch.object(self.store, "_score_fn", side_effect=blocked_score), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post_results, self.callback(batch))
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(future.done())
                self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 0)
            finally:
                release.set()
            status, ack = future.result(timeout=3)
        self.assertEqual(status, 200)
        self.assertTrue(ack["committed"])
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 1)

    def test_lost_response_can_be_replayed_without_repeating_the_observation(self):
        batch = self.reserve(count=1)
        payload = self.callback(batch)
        entered, release = threading.Event(), threading.Event()
        def blocked_score(successes, count):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("Test did not release the transaction")
            return wilson_preview(successes, count)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        with patch.object(self.store, "_score_fn", side_effect=blocked_score), \
                patch.object(self.server, "handle_error") as unexpected_server_error:
            try:
                connection.request("POST", "/internal/v1/check-results", json.dumps(payload).encode("utf-8"),
                                   {"Content-Type": "application/json"})
                self.assertTrue(entered.wait(2))
                connection.close()  # Deliberately discard the eventual acknowledgement.
            finally:
                release.set()
                connection.close()
            before = self.quality()  # Single-server barrier: the prior request has committed.
            unexpected_server_error.assert_not_called()
        status, ack = self.post_results(payload)
        self.assertEqual((status, ack["accepted"], ack["duplicates"]), (200, 0, 1))
        self.assertEqual(self.quality(), before)
        self.assertEqual(self.database_scalar("SELECT count(*) FROM observations"), 1)

    def test_expired_old_result_cannot_release_a_new_test_over_http(self):
        old = self.reserve()
        payload = self.callback(old)
        self.now += self.store.limits.check_timeout_ms
        self.store.expire_checks(now_ms=self.now)
        new = self.reserve("new", task_id="new-task")
        new_id = next(member["check_id"] for member in new["members"] if member["proxy_id"] == "p0")
        status, body = self.post_results(payload)
        self.assertEqual((status, body["error"]), (409, "stale_check"))
        self.assertEqual(self.quality()["active_check_id"], new_id)
        self.assertIsNone(self.quality()["score"])

    def test_old_committed_replay_does_not_clear_the_next_pending_test(self):
        first = self.reserve()
        payload = self.callback(first)
        self.post_results(payload)
        self.now += self.store.limits.check_interval_ms
        new = self.reserve("new", task_id="new-task")
        new_id = next(member["check_id"] for member in new["members"] if member["proxy_id"] == "p0")
        self.assertEqual(self.post_results(payload)[1]["duplicates"], 2)
        self.assertEqual(self.quality()["active_check_id"], new_id)

    def test_http_get_proxies_filters_by_score_and_keeps_pending_candidates(self):
        record = Result("usage-one", "p0", "site-demo", 1, NOW, "response", 200, 50)
        self.store.record_usage([record], now_ms=NOW)
        self.now += self.store.limits.check_interval_ms
        self.reserve()
        self.assertEqual(self.quality()["test_status"], "pending")
        status, body = self.request("GET", "/internal/v1/proxies?target_id=site-demo&min_score=20")
        self.assertEqual(status, 200)
        self.assertEqual([proxy["proxy_id"] for proxy in body["proxies"]], ["p0"])
        self.assertEqual(self.request("GET", "/internal/v1/proxies?target_id=site-demo&min_score=50")[1]["proxies"], [])

    def test_capacity_failure_is_not_reported_as_committed_and_replay_still_works(self):
        self.store.limits = replace(self.store.limits, max_receipts=1)
        batch = self.reserve()
        payload = self.callback(batch)
        status, body = self.post_results(payload)
        self.assertEqual((status, body), (429, {"error": "capacity_limit", "committed": False}))
        self.assertEqual(self.database_scalar("SELECT count(*) FROM receipts"), 0)
        one = {**payload, "records": payload["records"][:1]}
        self.assertEqual(self.post_results(one)[0], 200)
        self.assertEqual(self.post_results(one)[1]["duplicates"], 1)
        self.assertEqual(self.quality("p1")["test_status"], "pending")

    def test_invalid_http_input_cannot_write_or_claim_commit(self):
        invalid = [("POST", "/internal/v1/check-results", b"not-json", 422),
                   ("POST", "/unknown", b"{}", 404),
                   ("POST", "/internal/v1/check-batches",
                    b'{"request_id":"r","target_id":"site-demo","limit":1,"limit":2}', 422),
                   ("GET", "/internal/v1/quality?proxy_id=p0&proxy_id=p1&target_id=site-demo", None, 422),
                   ("GET", "/internal/v1/proxies?target_id=site-demo&min_score=NaN", None, 422)]
        for method, path, raw, expected in invalid:
            with self.subTest(path=path):
                status, body = self.request(method, path, raw=raw)
                self.assertEqual(status, expected)
                self.assertFalse(body["committed"])
        self.assertEqual(self.database_scalar("SELECT count(*) FROM receipts"), 0)
        self.assertEqual(self.database_scalar("SELECT count(*) FROM checks"), 0)

    def test_old_local_schema_is_rejected_without_implicit_migration(self):
        old_path = Path(self.temp.name) / "version-one.sqlite3"
        with closing(sqlite3.connect(old_path)) as setup:
            setup.executescript("CREATE TABLE sentinel(value TEXT); PRAGMA user_version=1; PRAGMA application_id=1347964213;")
        with self.assertRaises(ValidationError):
            LocalStore(old_path, score_fn=wilson_preview)
        with closing(sqlite3.connect(old_path)) as check:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("sentinel",)])


if __name__ == "__main__":
    unittest.main()
