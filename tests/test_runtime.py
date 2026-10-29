"""Current business contract; all data and publisher doubles are local."""

import asyncio
from contextlib import closing
from dataclasses import replace
import sqlite3
import threading
import time
import uuid
from fastapi.testclient import TestClient
import pytest
from apps.proxypool.service.app import create_app
from apps.proxypool.service.config import Settings
from apps.proxypool.service.database import Database
from apps.proxypool.service.writer import Busy, Writer
from apps.proxypool.service.cache import CandidateCache
from apps.proxypool.service.policy import needs_test
from integrations.crawlab.client import encode_job, PublishUncertain

KEY = "local-test-key-never-for-deployment"
AUTH = {"Authorization": "Bearer " + KEY}


class FakeCrawlab:
    def __init__(self):
        self.jobs = []
        self.fail = False

    async def outstanding(self, spider_ids):
        return 0

    async def run_job(self, spider, job):
        self.jobs.append((spider, job))
        if self.fail:
            raise PublishUncertain("synthetic ambiguous publication")
        return f"{len(self.jobs):024x}"

    async def close(self):
        pass


def settings(tmp_path, **changes):
    return replace(
        Settings(), database=str(tmp_path / "runtime.sqlite"), write_flush_seconds=0.01, **changes
    )


@pytest.fixture
def runtime(tmp_path):
    config = settings(tmp_path)
    native = FakeCrawlab()
    app = create_app(config, api_key=KEY, publisher_client=native)
    with TestClient(app, headers=AUTH) as client:
        yield client, app, config, native


def target(client, **changes):
    payload = dict(name="test target", test_url="https://example.invalid/", enabled=True)
    payload.update(changes)
    response = client.post("/v1/targets", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def import_proxies(client, count=1):
    payload = {
        "proxies": [
            {"host": "192.0.2." + str(i + 1), "port": 8080, "scheme": "http"} for i in range(count)
        ]
    }
    response = client.post("/v1/proxies/import", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def result(target_id, proxy_id=1, status=200, identifier=None, **changes):
    data = dict(
        result_id=identifier or uuid.uuid4().hex,
        proxy_id=proxy_id,
        target_id=target_id,
        target_revision=1,
        outcome="response",
        http_status=status,
        latency_ms=100.0,
    )
    data.update(changes)
    return data


def feedback(client, *records):
    response = client.post("/v1/proxies/feedback", json={"results": list(records)})
    assert response.status_code == 200, response.text
    assert response.json()["committed"] is True
    return response.json()


def wait_publication(client):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = client.get("/v1/stats").json()["publishing"]
        if not state["busy"]:
            return state
        time.sleep(0.01)
    raise AssertionError("Local publisher did not finish")


def test_authentication_body_bounds_and_static(runtime):
    client, app, config, _ = runtime
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/v1/stats", headers={"Authorization": "wrong"}).status_code == 401
    assert "Proxypool" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert (
        client.post(
            "/v1/targets", json={"name": "x", "test_url": "https://user:secret@example.invalid"}
        ).status_code
        == 422
    )
    response = client.post("/v1/proxies/import", content=b"x" * (config.max_request_bytes + 1))
    assert response.status_code == 413
    assert "secret" not in client.post("/v1/targets", json={"password": "secret"}).text


def test_63_bit_history_feedback_dedup_and_ewma(runtime):
    client, app, config, _ = runtime
    t = target(client)
    assert import_proxies(client)["inserted"] == 1
    assert import_proxies(client)["inserted"] == 0
    records = [
        result(t["target_id"], status=503 if i < 7 else 200, latency_ms=100.0 + i)
        for i in range(70)
    ]
    assert feedback(client, *records)["accepted"] == 70
    committed_before = app.state.writer.commits
    assert feedback(client, *records)["duplicate_count"] == 70
    assert app.state.writer.commits == committed_before
    with app.state.writer.database.reader() as db:
        row = dict(db.execute("SELECT * FROM proxy_quality").fetchone())
        tables = [x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert row["result_bits"] == (1 << 63) - 1
    assert row["sample_count"] == 63 and row["feedback_count"] == 70
    assert row["score"] == pytest.approx(94.2526, abs=0.001)
    expected = None
    for i in range(7, 70):
        expected = 100 + i if expected is None else expected * 0.8 + (100 + i) * 0.2
    assert row["successful_latency_ewma_ms"] == pytest.approx(expected)
    assert "receipts" not in tables and "observations" not in tables and "checks" not in tables
    assert not {"active_check_id", "test_status"} & row.keys()
    last = row["last_feedback_at"]
    feedback(client, records[-1])
    feedback(
        client,
        result(
            t["target_id"],
            outcome="local_error",
            http_status=None,
            latency_ms=None,
            error_code="local_failure",
        ),
    )
    with app.state.writer.database.reader() as db:
        row = db.execute("SELECT * FROM proxy_quality").fetchone()
        assert row["last_feedback_at"] == last and row["feedback_count"] == 70


def test_new_proxy_left_join_and_shared_feedback_releases_wait(runtime):
    client, app, config, _ = runtime
    t = target(client)
    import_proxies(client, 2)
    db = app.state.writer.database
    assert len(db.select_tests(t["target_id"], 1000000)) == 2
    app.state.writer.submit(
        "scheduled",
        {
            "target_id": t["target_id"],
            "target_revision": 1,
            "proxy_ids": [1],
            "scheduled_at": 1000000,
        },
        urgent=True,
    ).result(2)
    assert [x["proxy_id"] for x in db.select_tests(t["target_id"], 1000001)] == [2]
    app.state.writer.clock = lambda: 1001000
    feedback(client, result(t["target_id"]))
    assert not any(x["proxy_id"] == 1 for x in db.select_tests(t["target_id"], 1001001))
    # Any valid use feedback starts the minimum interval, even before test timeout.
    assert any(x["proxy_id"] == 1 for x in db.select_tests(t["target_id"], 1301000))
    assert db.select_tests(t["target_id"], 1301000, limit=1)[0]["proxy_id"] == 2
    # No feedback: timeout alone permits another test.
    assert needs_test(None, 1000000, 1600000, 300000, 600000)
    assert not needs_test(1599000, 1000000, 1600000, 300000, 600000)


def test_revision_disable_and_delete(runtime):
    client, app, config, _ = runtime
    t = target(client)
    import_proxies(client)
    feedback(client, *[result(t["target_id"]) for _ in range(10)])
    request = client.post("/v1/proxies/acquire", json={"target_id": t["target_id"]})
    assert request.status_code == 200 and "result_bits" not in request.json()
    t2 = client.put(
        "/v1/targets/1", json={"name": "renamed", "test_url": t["test_url"], "enabled": False}
    ).json()
    assert t2["revision"] == 1
    assert client.post("/v1/proxies/acquire", json={"target_id": 1}).status_code == 409
    with app.state.writer.database.reader() as db:
        assert db.execute("SELECT COUNT(*) FROM proxy_quality").fetchone()[0] == 1
    changed = client.put(
        "/v1/targets/1",
        json={"name": "changed", "test_url": "https://example.invalid/new", "enabled": True},
    ).json()
    assert changed["revision"] == 2
    assert feedback(client, result(1))["ignored"] == 1
    assert client.get("/v1/proxies?target_id=1").json()["total"] == 0
    feedback(client, result(1, target_revision=2))
    assert client.delete("/v1/targets/1").status_code == 200
    with app.state.writer.database.reader() as db:
        assert db.execute("SELECT COUNT(*) FROM proxy_quality").fetchone()[0] == 0
    assert feedback(client, result(1, target_revision=2))["ignored"] == 1


def test_source_registration_summary_and_short_memory_dedup(runtime):
    client, app, config, _ = runtime
    source = {
        "name": "local source",
        "url": "http://127.0.0.1/list",
        "spider_key": "proxyscrape",
        "enabled": False,
    }
    assert (
        client.post("/v1/sources", json=dict(source, spider_key="arbitrary_module")).status_code
        == 422
    )
    assert client.post("/v1/sources", json=source).status_code == 200
    package = {
        "source_id": 1,
        "task_id": "native-task",
        "outcome": "success",
        "duration_ms": 5.0,
        "proxies": [{"scheme": "http", "host": "192.0.2.1", "port": 8080}],
        "pages_ok": 1,
    }
    first = client.post("/v1/source-results", json=package).json()
    second = client.post("/v1/source-results", json=package).json()
    assert first["inserted"] == 1 and second["inserted"] == 1 and second["duplicate"] is True
    row = client.get("/v1/sources").json()["items"][0]
    assert row["inserted_count"] == 1 and row["output_count"] == 1
    package["task_id"] = "new-task"
    assert client.post("/v1/source-results", json=package).json()["inserted"] == 0
    assert client.get("/v1/sources").json()["items"][0]["outcome"] == "success"


def test_capture_prunes_only_excess_then_publishes_without_waiting(tmp_path):
    config = settings(tmp_path, pool_limit=2, eviction_min_feedback=2)
    native = FakeCrawlab()
    app = create_app(config, api_key=KEY, publisher_client=native)
    with TestClient(app, headers=AUTH) as client:
        target(client)
        import_proxies(client, 4)
        feedback(
            client,
            result(1, 1, 503),
            result(1, 1, 503),
            result(1, 2),
            result(1, 2),
            result(1, 4, 503),
            result(1, 4),
        )
        client.post(
            "/v1/sources",
            json={
                "name": "source",
                "spider_key": "proxyscrape",
                "url": "http://127.0.0.1/list",
                "enabled": True,
            },
        )
        assert client.post("/v1/actions/capture").status_code == 202
        state = wait_publication(client)
        assert state["last_error"] is None and state["last_publication"]["tasks"] == 1
        assert len(native.jobs) == 1  # No completion, polling or source-result callback was needed.
        with app.state.writer.database.reader() as db:
            assert [x[0] for x in db.execute("SELECT proxy_id FROM proxies ORDER BY proxy_id")] == [
                2,
                3,
            ]
        # The next fixed/manual tick does not wait for that task to finish.
        client.post("/v1/actions/capture")
        wait_publication(client)
        assert len(native.jobs) == 2


def test_test_publisher_marks_after_success_not_after_ambiguous_request(runtime):
    client, app, config, native = runtime
    target(client)
    import_proxies(client, 3)
    native.fail = True
    client.post("/v1/actions/test")
    state = wait_publication(client)
    assert state["last_error"] == "PublishUncertain" and len(native.jobs) == 1
    assert len(app.state.writer.database.select_tests(1, int(time.time() * 1000))) == 3
    native.fail = False
    client.post("/v1/actions/test")
    wait_publication(client)
    assert len(native.jobs) == 2
    assert app.state.writer.database.select_tests(1, int(time.time() * 1000)) == []
    assert native.jobs[-1][1]["kind"] == "test"


def test_writer_accepts_next_package_and_read_during_commit(tmp_path):
    config = settings(tmp_path)
    db = Database(config)
    writer = Writer(db)
    entered, release = threading.Event(), threading.Event()
    original = db.apply
    try:
        writer.submit(
            "import", [{"scheme": "http", "host": "192.0.2.1", "port": 8080}], urgent=True
        ).result(2)

        def blocked(connection, kind, payload, now):
            value = original(connection, kind, payload, now)
            if kind == "import" and payload[0]["host"] == "192.0.2.2":
                entered.set()
                assert release.wait(3)
            return value

        db.apply = blocked
        first = writer.submit(
            "import", [{"scheme": "http", "host": "192.0.2.2", "port": 8080}], urgent=True
        )
        assert entered.wait(2)
        second = writer.submit("import", [{"scheme": "http", "host": "192.0.2.3", "port": 8080}])
        assert not first.done() and not second.done()
        assert db.stats()["proxy_count"] == 1
        release.set()
        assert first.result(2)["committed"] and second.result(2)["committed"]
        assert db.stats()["proxy_count"] == 3
    finally:
        release.set()
        writer.close()


def test_failed_commit_does_not_poison_memory_dedup(tmp_path):
    config = settings(tmp_path)
    db = Database(config)
    writer = Writer(db)
    try:
        writer.submit(
            "target_save",
            {"name": "t", "test_url": "https://example.invalid", "enabled": True},
            urgent=True,
        ).result(2)
        writer.submit(
            "import", [{"scheme": "http", "host": "192.0.2.1", "port": 80}], urgent=True
        ).result(2)
        original = db.apply

        def failing(connection, kind, data, now):
            value = original(connection, kind, data, now)
            if kind == "feedback":
                raise sqlite3.OperationalError("synthetic transaction failure")
            return value

        db.apply = failing
        item = result(1)
        with pytest.raises(sqlite3.OperationalError):
            writer.submit("feedback", [item], urgent=True).result(2)
        assert db.quality_page(1)["total"] == 0
        db.apply = original
        assert writer.submit("feedback", [item], urgent=True).result(2)["accepted"] == 1
    finally:
        writer.close()


def test_candidate_cache_one_refill_ttl_and_non_sliding_time(tmp_path):
    config = settings(tmp_path, candidate_count=2)
    cache = CandidateCache(config)
    calls = []
    target = {"target_id": 1, "revision": 1, "enabled": True}

    async def load(*args):
        calls.append(args)
        await asyncio.sleep(0.01)
        return [{"proxy_id": 1}, {"proxy_id": 2}]

    async def exercise():
        items = await asyncio.gather(cache.acquire(target, load), cache.acquire(target, load))
        assert {x["proxy_id"] for x in items} == {1, 2} and len(calls) == 1
        await cache.acquire(target, load)
        assert len(calls) == 2
        revision, timestamp, items = cache.queues[1]
        cache.queues[1] = (revision, timestamp - config.candidate_ttl_seconds - 1, items)
        await cache.acquire(target, load)
        assert len(calls) == 3
        old = cache.queues[1][1]
        await cache.acquire(target, load)
        assert cache.queues[1][1] == old

    asyncio.run(exercise())


def test_job_encoding_is_shell_safe_and_no_secrets():
    job = {"kind": "test", "target": {"test_url": "https://example.invalid/'$(anything)"}}
    import re, json, base64

    encoded = encode_job(job)
    assert re.fullmatch(r"[A-Za-z0-9_=-]+", encoded)
    assert json.loads(base64.urlsafe_b64decode(encoded)) == job


def test_refuse_legacy_database(tmp_path):
    config = settings(tmp_path)
    with closing(sqlite3.connect(config.database)) as db:
        db.execute("CREATE TABLE old_poc(x)")
    with pytest.raises(ValueError, match="new path"):
        Writer(Database(config))


def test_bounded_queue_rejects_without_blocking_current_writer(tmp_path):
    config = settings(
        tmp_path,
        max_request_records=1,
        write_batch_records=1,
        queue_max_records=1,
        queue_max_requests=1,
        test_batch_size=1,
        candidate_count=1,
    )
    db = Database(config)
    writer = Writer(db)
    original = db.apply
    entered = threading.Event()
    release = threading.Event()

    def blocked(connection, kind, data, now):
        value = original(connection, kind, data, now)
        entered.set()
        assert release.wait(3)
        return value

    db.apply = blocked
    try:
        first = writer.submit("import", [{"scheme": "http", "host": "192.0.2.1", "port": 80}])
        assert entered.wait(2)
        second = writer.submit("import", [{"scheme": "http", "host": "192.0.2.2", "port": 80}])
        with pytest.raises(Busy):
            writer.submit("import", [{"scheme": "http", "host": "192.0.2.3", "port": 80}])
        release.set()
        assert first.result(2)["inserted"] == 1 and second.result(2)["inserted"] == 1
    finally:
        release.set()
        writer.close()


def test_unrelated_request_in_rolled_back_batch_can_retry(tmp_path):
    from apps.proxypool.service.database import NotFound

    db = Database(replace(settings(tmp_path), write_flush_seconds=1))
    writer = Writer(db)
    endpoints = [{"scheme": "http", "host": "192.0.2.1", "port": 8080}]
    try:
        first = writer.submit("import", endpoints)
        failing = writer.submit("target_delete", 999, urgent=True)
        with pytest.raises(NotFound):
            failing.result(3)
        with pytest.raises(Busy):
            first.result(3)
        assert db.stats()["proxy_count"] == 0
        assert writer.submit("import", endpoints, urgent=True).result(3)["inserted"] == 1
    finally:
        writer.close()
