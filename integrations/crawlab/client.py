"""Pinned Community 0.6.x run contract, already exercised by the historical PoC."""

import base64
import json
import re
from urllib.parse import urlsplit
import httpx


class PublishUncertain(RuntimeError):
    pass


def encode_job(job):
    raw = json.dumps(job, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > 45000:
        raise ValueError("Task input exceeds 45 KiB; reduce test_batch_size")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def object_id(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{24}", value):
        raise ValueError("Expected a Crawlab object ID")
    return value


class CrawlabClient:
    def __init__(self, base_url, token, node_ids, *, transport=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
        ):
            raise ValueError("Invalid Crawlab API base URL")
        if not token or not node_ids:
            raise ValueError("Crawlab token and selected Worker IDs required")
        self.nodes = [object_id(value) for value in node_ids]
        self.position = 0
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": token},
            timeout=10,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def run_job(self, spider_id, job):
        object_id(spider_id)
        payload = {
            "spider_id": spider_id,
            "mode": "selected-nodes",
            "node_ids": [self.nodes[self.position % len(self.nodes)]],
            "cmd": "python -B -u proxypool_task.py",
            "param": "--job " + encode_job(job),
        }
        self.position += 1
        try:
            response = await self.http.post("tasks/run", json=payload)
            response.raise_for_status()
            value = response.json()
            identifiers = value.get("data")
            if (
                value.get("error")
                or value.get("status") == "error"
                or not isinstance(identifiers, list)
                or len(identifiers) != 1
            ):
                raise ValueError("Unexpected native run response")
            return object_id(identifiers[0])
        except (httpx.HTTPError, ValueError, TypeError):
            # A request may already have reached Master. The next timer is independent.
            raise PublishUncertain(
                "Crawlab publication uncertain; inspect native tasks, no automatic retry"
            ) from None

    async def close(self):
        await self.http.aclose()
