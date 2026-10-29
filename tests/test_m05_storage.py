from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from apps.proxypool.service.contracts import Result, ValidationError
from apps.proxypool.service.quality import wilson_preview
from apps.proxypool.service.storage import CapacityError, ConflictError, Limits, LocalStore, StaleCheckError


NOW = 1_793_052_000_000  # Fixed synthetic clock; tests never sleep for expiry.


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m05-check-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local.sqlite3"
        self.limits = Limits(sample_window_ms=10_000, check_interval_ms=100,
                             check_timeout_ms=100, receipt_retention_ms=20_000, clock_skew_ms=1000)
        self.store = self.open_store(self.path, self.limits)
        self.seed(self.store)

    def open_store(self, path, limits):
        # Explicitly inject the candidate. This is not a production-policy selection.
        store = LocalStore(path, score_fn=wilson_preview, limits=limits)
        self.addCleanup(store.close)
        return store

    @staticmethod
    def seed(store, count=4):
        store.register_target("site-a")
        store.register_target("site-b")
        for i in range(count):
            store.register_proxy(f"p{i}", f"192.0.2.{i+1}", 8080)

    def reserve(self, request="request-a", count=1, now=NOW, target="site-a"):
        return self.store.reserve_checks(request, target, count, now_ms=now)

    @staticmethod
    def active_result(batch, index=0, when=NOW, status=200):
        member = batch["members"][index]
        return Result(member["check_id"], member["proxy_id"], batch["target_id"], batch["revision"],
                      when, "response", status, 80)

    @staticmethod
    def usage(result_id="usage-a", proxy="p0", when=NOW, status=200, target="site-a"):
        return Result(result_id, proxy, target, 1, when, "response", status, 80)

    def test_database_settings_and_endpoint_deduplication(self):
        diagnostics = self.store.diagnostics()
        self.assertEqual(diagnostics["pragmas"]["journal_mode"], "wal")
        self.assertEqual(diagnostics["pragmas"]["foreign_keys"], 1)
        self.assertEqual(diagnostics["pragmas"]["synchronous"], 2)
        self.assertLessEqual(diagnostics["pragmas"]["max_page_count"] * diagnostics["pragmas"]["page_size"],
                             self.limits.max_database_bytes)
        self.assertEqual(self.store.register_proxy("another-id", "192.0.2.1", 8080), "p0")
        self.assertEqual(self.store.diagnostics()["counts"]["proxies"], 4)

    def test_unrelated_database_is_rejected_before_changing_its_journal_mode(self):
        path = Path(self.temp.name) / "unrelated.sqlite3"
        with closing(sqlite3.connect(path)) as setup:
            setup.executescript("CREATE TABLE sentinel(value TEXT); PRAGMA user_version=1;")
        with self.assertRaises(ValidationError):
            self.open_store(path, self.limits)
        with closing(sqlite3.connect(path)) as check:
            self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(check.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("sentinel",)])

    def test_concurrent_reservations_do_not_repeat_pending_members(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            batches = list(pool.map(lambda key: self.reserve(key, 2), ["one", "two"]))
        members = [item["proxy_id"] for batch in batches for item in batch["members"]]
        self.assertEqual(len(members), 4)
        self.assertEqual(len(set(members)), 4)
        self.assertEqual(self.reserve("three", 4)["members"], [])

    def test_separate_connections_also_reserve_distinct_members(self):
        another = self.open_store(self.path, self.limits)
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(store.reserve_checks, request, "site-a", 2, now_ms=NOW)
                     for store, request in [(self.store, "one"), (another, "two")]]
            batches = [call.result() for call in calls]
        self.assertEqual(len({item["check_id"] for batch in batches for item in batch["members"]}), 4)
        self.assertEqual(len({item["proxy_id"] for batch in batches for item in batch["members"]}), 4)

    def test_request_replay_returns_same_members_and_deadline(self):
        first = self.reserve(count=2)
        self.assertEqual(self.reserve(count=2, now=NOW+10), first)
        with self.assertRaises(ConflictError):
            self.reserve(count=3)
        self.assertEqual(self.store.diagnostics()["counts"]["checks"], 2)

    def test_feedback_updates_score_and_releases_only_the_matching_test(self):
        batch = self.reserve()
        result = self.active_result(batch)
        response = self.store.record_test_results(batch["batch_id"], [result], now_ms=NOW+1)
        self.assertEqual(response, {"committed": True, "accepted": 1, "duplicates": 0})
        state = self.store.quality("p0", "site-a")
        self.assertEqual(state["test_status"], "available")
        self.assertEqual(state["sample_count"], 1)
        self.assertAlmostEqual(state["score"], 20.654329, places=5)
        self.assertEqual(state["last_feedback_ms"], NOW)
        self.assertEqual(self.store.read_batch(batch["batch_id"])["members"][0]["status"], "reported")

    def test_pending_test_does_not_block_proxy_getting(self):
        self.store.record_usage([self.usage()], now_ms=NOW)
        batch = self.reserve(now=NOW+101)
        self.assertEqual(batch["members"][0]["proxy_id"], "p1")  # Unknown evidence is warmed first.
        # Reserve the remaining unknowns, then p0, without changing its score.
        self.reserve("rest", 4, now=NOW+101)
        self.assertEqual(self.store.quality("p0", "site-a")["test_status"], "pending")
        self.assertEqual(self.store.get_proxies("site-a", min_score=20)[0]["proxy_id"], "p0")
        self.assertEqual(self.store.get_proxies("site-a", min_score=50), [])

    def test_usage_feedback_keeps_the_test_pending_and_delays_later_selection(self):
        batch = self.reserve()
        self.store.record_usage([self.usage(when=NOW+20)], now_ms=NOW+20)
        state = self.store.quality("p0", "site-a")
        self.assertEqual(state["active_check_id"], batch["members"][0]["check_id"])
        self.assertEqual(state["last_feedback_ms"], NOW+20)
        self.store.expire_checks(now_ms=NOW+100)
        proxies = [m["proxy_id"] for m in self.reserve("next", 4, now=NOW+100)["members"]]
        self.assertNotIn("p0", proxies)

    def test_failure_is_a_completed_observation_and_lowers_score(self):
        self.store.record_usage([self.usage()], now_ms=NOW)
        batch = self.reserve("warm-rest", 4, now=NOW+101)
        index = next(i for i, m in enumerate(batch["members"]) if m["proxy_id"] == "p0")
        old_score = self.store.quality("p0", "site-a")["score"]
        result = self.active_result(batch, index=index, when=NOW+101, status=503)
        self.store.record_test_results(batch["batch_id"], [result], now_ms=NOW+102)
        state = self.store.quality("p0", "site-a")
        self.assertLess(state["score"], old_score)
        self.assertEqual(state["test_status"], "available")
        self.assertEqual(state["sample_count"], 2)
        self.assertEqual(len(self.store.get_proxies("site-a")), 1)

    def test_proxy_timeout_counts_as_failure_but_execution_error_does_not(self):
        batch = self.reserve(count=2)
        timeout = replace(self.active_result(batch), outcome="proxy_error", http_status=None,
                          error_code="connect_timeout")
        error = replace(self.active_result(batch, index=1), outcome="execution_error", http_status=None,
                        error_code="execution_error")
        self.store.record_test_results(batch["batch_id"], [timeout, error], now_ms=NOW+1)
        timed = self.store.quality("p0", "site-a")
        broken = self.store.quality("p1", "site-a")
        self.assertEqual((timed["sample_count"], timed["score"], timed["test_status"]), (1, 0, "available"))
        self.assertEqual((broken["sample_count"], broken["score"], broken["last_feedback_ms"]), (0, None, None))
        self.assertEqual(broken["test_status"], "available")

    def test_duplicate_replay_does_not_change_score_or_feedback_time(self):
        batch = self.reserve()
        result = self.active_result(batch)
        self.store.record_test_results(batch["batch_id"], [result], now_ms=NOW+1)
        before = self.store.quality("p0", "site-a")
        replay = self.store.record_test_results(batch["batch_id"], [result], now_ms=NOW+50)
        self.assertEqual(replay["duplicates"], 1)
        self.assertEqual(self.store.quality("p0", "site-a"), before)
        with self.assertRaises(ConflictError):
            self.store.record_test_results(batch["batch_id"], [replace(result, http_status=500)], now_ms=NOW+60)

    def test_real_new_failure_refreshes_time_even_if_score_stays_zero(self):
        self.store.record_usage([self.usage(status=500)], now_ms=NOW)
        self.store.record_usage([self.usage("second", when=NOW+20, status=500)], now_ms=NOW+20)
        state = self.store.quality("p0", "site-a")
        self.assertEqual(state["score"], 0)
        self.assertEqual(state["last_feedback_ms"], NOW+20)

    def test_out_of_order_feedback_does_not_move_latest_time_backward(self):
        self.store.record_usage([self.usage(when=NOW+20)], now_ms=NOW+20)
        self.store.record_usage([self.usage("older", when=NOW, status=500)], now_ms=NOW+30)
        state = self.store.quality("p0", "site-a")
        self.assertEqual(state["last_feedback_ms"], NOW+20)
        self.assertEqual(state["sample_count"], 2)

    def test_targets_are_independent(self):
        batch_a = self.reserve()
        batch_b = self.reserve("b", target="site-b")
        self.assertEqual(batch_a["members"][0]["proxy_id"], batch_b["members"][0]["proxy_id"])
        self.store.record_test_results(batch_a["batch_id"], [self.active_result(batch_a)], now_ms=NOW+1)
        self.assertIsNotNone(self.store.quality("p0", "site-a")["score"])
        self.assertIsNone(self.store.quality("p0", "site-b")["score"])
        self.assertEqual(self.store.quality("p0", "site-b")["test_status"], "pending")

    def test_timeout_and_old_callback_cannot_release_a_new_test(self):
        batch = self.reserve()
        old = self.active_result(batch)
        self.assertEqual(self.store.expire_checks(now_ms=NOW+100), 1)
        state = self.store.quality("p0", "site-a")
        self.assertEqual((state["test_status"], state["score"], state["last_feedback_ms"]), ("available", None, None))
        new = self.reserve("new", now=NOW+101)
        current_id = new["members"][0]["check_id"]
        with self.assertRaises(StaleCheckError):
            self.store.record_test_results(batch["batch_id"], [old], now_ms=NOW+102)
        self.assertEqual(self.store.quality("p0", "site-a")["active_check_id"], current_id)

    def test_payload_must_match_the_reserved_member_and_batch(self):
        first = self.reserve()
        second = self.reserve("second")
        with self.assertRaises(ValidationError):
            self.store.record_test_results(second["batch_id"], [self.active_result(first)], now_ms=NOW+1)
        with self.assertRaises(ValidationError):
            self.store.record_test_results(first["batch_id"], [replace(self.active_result(first), revision=2)], now_ms=NOW+1)
        self.assertEqual(self.store.diagnostics()["counts"]["receipts"], 0)

    def test_whole_package_rolls_back_when_one_member_is_invalid(self):
        batch = self.reserve(count=2)
        valid = self.active_result(batch)
        invalid = replace(self.active_result(batch, index=1), proxy_id="p3")
        with self.assertRaises(ValidationError):
            self.store.record_test_results(batch["batch_id"], [valid, invalid], now_ms=NOW+1)
        self.assertEqual(self.store.diagnostics()["counts"]["receipts"], 0)
        self.assertEqual(self.store.quality("p0", "site-a")["test_status"], "pending")
        self.assertIsNone(self.store.quality("p0", "site-a")["score"])

    def test_actual_commit_failure_cannot_acknowledge_or_release_pending_state(self):
        batch = self.reserve()
        with closing(sqlite3.connect(self.path)) as setup:
            setup.executescript("""
                CREATE TABLE commit_guard (
                    proxy_id TEXT REFERENCES proxies(proxy_id) DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TRIGGER fail_commit AFTER INSERT ON observations BEGIN
                    INSERT INTO commit_guard VALUES('missing-proxy');
                END;
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_test_results(batch["batch_id"], [self.active_result(batch)], now_ms=NOW+1)
        self.assertEqual(self.store.diagnostics()["counts"]["receipts"], 0)
        self.assertEqual(self.store.diagnostics()["counts"]["observations"], 0)
        self.assertEqual(self.store.quality("p0", "site-a")["test_status"], "pending")
        with closing(sqlite3.connect(self.path)) as setup:
            setup.execute("DROP TRIGGER fail_commit")
            setup.commit()
        response = self.store.record_test_results(batch["batch_id"], [self.active_result(batch)], now_ms=NOW+2)
        self.assertTrue(response["committed"])

    def test_reopen_preserves_reservations_and_can_expire_them(self):
        batch = self.reserve()
        reopened = self.open_store(self.path, self.limits)
        self.assertEqual(reopened.quality("p0", "site-a")["active_check_id"], batch["members"][0]["check_id"])
        self.assertEqual(reopened.expire_checks(now_ms=NOW+100), 1)
        self.assertEqual(self.store.quality("p0", "site-a")["test_status"], "available")

    def test_sample_limit_preserves_receipts_for_evicted_observations(self):
        store = self.open_store(Path(self.temp.name) / "finite.sqlite3", replace(self.limits, sample_limit=2))
        self.seed(store, 1)
        records = [self.usage(str(i), when=NOW+i) for i in range(4)]
        for record in records:
            store.record_usage([record], now_ms=record.observed_at_ms)
        self.assertEqual(store.diagnostics()["counts"]["observations"], 2)
        self.assertEqual(store.diagnostics()["counts"]["receipts"], 4)
        before = store.quality("p0", "site-a")
        self.assertEqual(store.record_usage([records[0]], now_ms=NOW+10)["duplicates"], 1)
        self.assertEqual(store.quality("p0", "site-a"), before)

    def test_maintenance_does_not_fake_a_new_feedback_time(self):
        record = self.usage()
        self.store.record_usage([record], now_ms=NOW)
        self.store.maintenance(now_ms=NOW+10_001)
        state = self.store.quality("p0", "site-a")
        self.assertEqual((state["sample_count"], state["score"], state["last_feedback_ms"]), (0, None, NOW))
        self.store.maintenance(now_ms=NOW+20_001)
        self.assertEqual(self.store.diagnostics()["counts"]["receipts"], 0)
        with self.assertRaises(ValidationError):
            self.store.record_usage([record], now_ms=NOW+20_002)

    def test_capacity_error_rolls_back_new_reservations(self):
        store = self.open_store(Path(self.temp.name) / "quota.sqlite3", replace(self.limits, max_checks=1))
        self.seed(store, 2)
        with self.assertRaises(CapacityError):
            store.reserve_checks("too-large", "site-a", 2, now_ms=NOW)
        counts = store.diagnostics()["counts"]
        self.assertEqual((counts["checks"], counts["batches"], counts["quality"]), (0, 0, 0))

    def test_receipt_capacity_does_not_release_a_test_or_block_replay(self):
        store = self.open_store(Path(self.temp.name) / "receipts.sqlite3", replace(self.limits, max_receipts=1))
        self.seed(store, 1)
        record = self.usage()
        store.record_usage([record], now_ms=NOW)
        batch = store.reserve_checks("one", "site-a", 1, now_ms=NOW+101)
        result = self.active_result(batch, when=NOW+101)
        with self.assertRaises(CapacityError):
            store.record_test_results(batch["batch_id"], [result], now_ms=NOW+102)
        self.assertEqual(store.quality("p0", "site-a")["test_status"], "pending")
        self.assertEqual(store.record_usage([record], now_ms=NOW+103)["duplicates"], 1)

    def test_bulk_body_cannot_bypass_record_limit_or_use_future_evidence(self):
        with self.assertRaises(ValidationError):
            self.store.record_usage([self.usage(str(i)) for i in range(101)], now_ms=NOW)
        with self.assertRaises(ValidationError):
            self.store.record_usage([self.usage(when=NOW+1001)], now_ms=NOW)
        self.assertEqual(self.store.diagnostics()["counts"]["receipts"], 0)


if __name__ == "__main__":
    unittest.main()
