"""Generic program delivery, native backpressure and byte-limited batches."""

import asyncio
from dataclasses import replace
import json
import os
import subprocess
import sys
import httpx
from integrations.crawlab.program import program_files
from integrations.crawlab.client import CrawlabClient, encode_job
from apps.proxypool.publishers import Publishers
from apps.proxypool.service.config import Settings
from apps.proxypool.task_batches import callback_chunks


def test_delivered_program_checks_from_unrelated_directory(tmp_path):
    program = tmp_path / "synced-program"
    for name, content in program_files().items():
        path = program / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(program / "proxypool_task.py"), "--check"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime_check"] == "PASS"
    assert not (program / "apps/proxypool/service").exists()


def test_native_count_uses_platform_filters():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [], "total": 7})

    async def exercise():
        client = CrawlabClient(
            "http://127.0.0.1/api",
            "synthetic-token",
            transport=httpx.MockTransport(respond),
        )
        try:
            assert await client.outstanding(["a" * 24, "b" * 24]) == 7
        finally:
            await client.close()

    asyncio.run(exercise())
    assert calls[0].url.path == "/api/tasks"
    conditions = json.loads(calls[0].url.params["conditions"])
    assert conditions == [
        {"key": "spider_id", "op": "in", "value": ["a" * 24, "b" * 24]},
        {"key": "status", "op": "in", "value": ["pending", "running"]},
    ]


def test_publisher_queue_full_then_frees_and_splits_large_input():
    class Native:
        def __init__(self):
            self.occupied = 8
            self.jobs = []

        async def outstanding(self, identifiers):
            return self.occupied

        async def run_job(self, spider, job):
            encode_job(job)
            self.jobs.append(job)
            return "a" * 24

    class DB:
        def targets(self):
            return [
                {
                    "target_id": 1,
                    "revision": 1,
                    "enabled": True,
                    "test_url": "https://example.invalid/",
                }
            ]

        def select_tests(self, target, now, limit):
            return [
                {"proxy_id": i, "proxy_url": f"http://192.0.2.1:{10000 + i}"}
                for i in range(min(1000, limit))
            ]

    writes = []

    async def read(method, *args):
        return method(*args)

    async def write(action, value, *args):
        writes.append((action, value))

    native = Native()
    config = replace(
        Settings(),
        test_batch_size=1000,
        test_max_batches_per_tick=8,
        capture_spider_id="a" * 24,
        tester_spider_id="b" * 24,
    )
    publisher = Publishers(config, DB(), write, read, lambda: None, client=native)

    async def exercise():
        assert await publisher.tests() == 0
        assert not writes and not native.jobs
        native.occupied = 0
        assert await publisher.tests() > 1

    asyncio.run(exercise())
    sent = [item["proxy_id"] for job in native.jobs for item in job["proxies"]]
    marked = [identifier for _, value in writes for identifier in value["proxy_ids"]]
    assert sent == marked == list(range(1000))


def test_callback_packages_obey_real_json_byte_limit():
    rows = [{"result_id": str(i), "message": "x" * 80} for i in range(100)]
    packages = list(
        callback_chunks(
            rows,
            lambda chunk, part: {"results": chunk},
            {"max_request_records": 100, "max_request_bytes": 1000},
        )
    )
    assert len(packages) > 1
    assert all(len(json.dumps(payload).encode()) <= 1000 for payload in packages)
    assert [row for payload in packages for row in payload["results"]] == rows
