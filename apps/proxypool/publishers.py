"""Short fixed-cadence publishers. Never wait for native task completion."""

import asyncio
import logging
import os
from pathlib import Path
import time
from integrations.crawlab.client import CrawlabClient
from .crawler.registry import load_entries

LOG = logging.getLogger(__name__)


class Publishers:
    def __init__(self, settings, database, write, read, invalidate, *, client=None):
        self.settings, self.database = settings, database
        self.write, self.read, self.invalidate = write, read, invalidate
        token = os.getenv("CRAWLAB_API_TOKEN", "")
        if not token and os.getenv("CRAWLAB_API_TOKEN_FILE"):
            token = Path(os.environ["CRAWLAB_API_TOKEN_FILE"]).read_text().strip()
        self.configured = bool(
            client
            or (
                settings.crawlab_url
                and token
                and settings.worker_node_ids
                and settings.capture_spider_id
                and settings.tester_spider_id
            )
        )
        self.client = client or (
            CrawlabClient(settings.crawlab_url, token, settings.worker_node_ids)
            if self.configured
            else None
        )
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.busy = False
        self.last_error = None
        self.last_publication = None
        self.target_cursor = 0

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def start(self):
        if self.settings.publishing_enabled:
            if not self.configured:
                raise ValueError("Publishing enabled but Crawlab is not configured")
            self._spawn(self._timer("capture", self.settings.capture_interval_seconds))
            self._spawn(self._timer("test", self.settings.test_publish_interval_seconds))

    async def _timer(self, action, interval):
        deadline = asyncio.get_running_loop().time() + interval
        while True:
            await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
            await self._execute(action)
            # Fixed cadence; skip missed ticks rather than create catch-up task storms.
            now = asyncio.get_running_loop().time()
            deadline += interval
            if deadline <= now:
                deadline += (int((now - deadline) // interval) + 1) * interval

    def trigger(self, action):
        self.busy = True
        self._spawn(self._execute(action))

    async def _execute(self, action):
        async with self.lock:
            self.busy = True
            try:
                count = await (self.capture() if action == "capture" else self.tests())
                self.last_publication = {
                    "action": action,
                    "tasks": count,
                    "at": int(time.time() * 1000),
                }
                self.last_error = None
            except Exception as error:
                self.last_error = type(error).__name__
                LOG.error("Publication failed (%s); no automatic task retry", type(error).__name__)
            finally:
                self.busy = False

    def _common_job(self):
        return {
            "request_timeout_seconds": self.settings.request_timeout_seconds,
            "retry_attempts": self.settings.retry_attempts,
            "retry_window_seconds": self.settings.feedback_retry_window_seconds,
            "max_request_records": self.settings.max_request_records,
        }

    async def prepare_capture(self):
        plan = await self.write("plan_eviction", None, True)
        if len(plan["proxy_ids"]) < plan["requested"]:
            raise RuntimeError("Insufficient eligible cleanup candidates")
        ids = plan["proxy_ids"]
        for offset in range(0, len(ids), self.settings.max_request_records):
            await self.write(
                "delete_proxies", ids[offset : offset + self.settings.max_request_records], True
            )
            self.invalidate()
        return plan

    async def capture(self):
        await self.prepare_capture()
        sources = await self.read(self.database.sources)
        entries = load_entries(self.settings.source_registry_file)
        count = 0
        for source in sources:
            if not source["enabled"]:
                continue
            entry = entries.get(source["spider_key"])
            if entry is None:
                raise ValueError("Source is no longer registered")
            scheduled = int(time.time() * 1000)
            job = dict(
                self._common_job(),
                kind="capture",
                source={key: source[key] for key in ("source_id", "spider_key", "url")},
                source_timeout_seconds=self.settings.source_timeout_seconds,
                source_max_records=self.settings.source_max_records,
                source_max_pages=self.settings.source_max_pages,
                workspace_spider=bool(entry.get("workspace_spider")),
            )
            task_id = await self.client.run_job(
                entry.get("spider_id", self.settings.capture_spider_id), job
            )
            await self.write(
                "source_published",
                {"source_id": source["source_id"], "task_id": task_id, "scheduled_at": scheduled},
                True,
            )
            count += 1
        return count

    async def tests(self):
        targets = [x for x in await self.read(self.database.targets) if x["enabled"]]
        if not targets:
            return 0
        start = self.target_cursor % len(targets)
        self.target_cursor = (start + 1) % len(targets)
        targets = targets[start:] + targets[:start]
        count = 0
        for target in targets:
            remaining = self.settings.test_max_batches_per_tick - count
            if remaining <= 0:
                break
            rows = await self.read(
                self.database.select_tests,
                target["target_id"],
                int(time.time() * 1000),
                remaining * self.settings.test_batch_size,
            )
            for offset in range(0, len(rows), self.settings.test_batch_size):
                members = rows[offset : offset + self.settings.test_batch_size]
                scheduled = int(time.time() * 1000)
                job = dict(
                    self._common_job(),
                    kind="test",
                    target=target,
                    proxies=members,
                    concurrency=self.settings.worker_concurrency,
                )
                await self.client.run_job(self.settings.tester_spider_id, job)
                await self.write(
                    "scheduled",
                    {
                        "target_id": target["target_id"],
                        "target_revision": target["revision"],
                        "proxy_ids": [x["proxy_id"] for x in members],
                        "scheduled_at": scheduled,
                    },
                    True,
                )
                count += 1
        return count

    def status(self):
        return {
            "configured": self.configured,
            "enabled": self.settings.publishing_enabled,
            "busy": self.busy,
            "last_error": self.last_error,
            "last_publication": self.last_publication,
        }

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.client:
            await self.client.close()
