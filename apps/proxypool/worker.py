"""Crawlab program entry; task arguments contain data, never credentials or code."""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import ssl
import time
import aiohttp
from .service.config import secret
from .service.models import Feedback, SourceResult


async def post_committed(base_url, key, path, payload, job):
    deadline = time.monotonic() + float(job["retry_window_seconds"])
    error = None
    async with aiohttp.ClientSession(trust_env=False) as session:
        for attempt in range(int(job["retry_attempts"])):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                timeout = aiohttp.ClientTimeout(total=min(remaining, 30))
                async with session.post(
                    base_url.rstrip("/") + path,
                    json=payload,
                    headers={"Authorization": "Bearer " + key},
                    timeout=timeout,
                    allow_redirects=False,
                ) as response:
                    if response.status == 429 or response.status >= 500:
                        raise aiohttp.ClientConnectionError("Temporary callback failure")
                    if response.status != 200:
                        raise ValueError("Callback rejected HTTP " + str(response.status))
                    raw = await response.content.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("Callback response too large")
                    result = json.loads(raw)
                    if result.get("committed") is not True:
                        raise ValueError("Callback did not confirm a commit")
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError) as caught:
                error = caught
                if attempt + 1 < int(job["retry_attempts"]):
                    await asyncio.sleep(min(2**attempt, max(0, deadline - time.monotonic())))
    raise RuntimeError("Callback retries exhausted") from error


async def report_tests(job, base_url, key):
    from .tester.execute import test_proxies

    config_path = os.getenv("PROXYPOOL_WORKER_SETTINGS")
    options = json.loads(Path(config_path).read_text(encoding="utf-8")) if config_path else {}
    context = ssl.create_default_context(cafile=options.get("ca_file"))
    records = await test_proxies(job, headers=options.get("request_headers"), ssl_context=context)
    for item in records:
        Feedback.model_validate(item)
    size = int(job["max_request_records"])
    for start in range(0, len(records), size):
        await post_committed(
            base_url, key, "/v1/proxies/feedback", {"results": records[start : start + size]}, job
        )
    return len(records)


async def report_source(job, summary, base_url, key, task_id):
    records = summary.pop("proxies")
    size = int(job["max_request_records"])
    if len(records) <= size:
        payload = dict(
            summary, task_id=task_id, proxies=records, part=0, final=True, output_count=len(records)
        )
        SourceResult.model_validate(payload)
        return await post_committed(base_url, key, "/v1/source-results", payload, job)
    inserted = 0
    chunks = [records[i : i + size] for i in range(0, len(records), size)] or [[]]
    for part, chunk in enumerate(chunks):
        # The last summary-only package makes its total inserted count accurate.
        payload = dict(summary, task_id=task_id, proxies=chunk, part=part, final=False)
        SourceResult.model_validate(payload)
        ack = await post_committed(base_url, key, "/v1/source-results", payload, job)
        inserted += ack.get("inserted", 0)
    final = dict(
        summary,
        task_id=task_id,
        proxies=[],
        part=len(chunks),
        final=True,
        output_count=len(records),
        total_inserted=inserted,
    )
    await post_committed(base_url, key, "/v1/source-results", final, job)


def decode_job(encoded):
    if not encoded or len(encoded) > 65536:
        raise ValueError("Invalid task input length")
    job = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
    if not isinstance(job, dict) or job.get("kind") not in ("test", "capture"):
        raise ValueError("Unknown task kind")
    return job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    options = parser.parse_args()
    job = decode_job(options.job)
    base_url = os.environ["PROXYPOOL_M05_URL"]
    key = secret("PROXYPOOL_API_KEY")
    if not key:
        raise ValueError("Worker API key is not configured")
    try:
        if job["kind"] == "test":
            count = asyncio.run(report_tests(job, base_url, key))
            print(json.dumps({"kind": "test", "reported": count, "committed": True}), flush=True)
            return 0
        start = time.monotonic()
        try:
            from .crawler.execute import capture

            summary = capture(job)
        except Exception:
            summary = {
                "source_id": job["source"]["source_id"],
                "outcome": "failed",
                "proxies": [],
                "duration_ms": round((time.monotonic() - start) * 1000, 3),
                "pages_ok": 0,
                "pages_failed": 0,
                "error_code": "execution_error",
            }
        failed = summary["outcome"] != "success"
        task_id = os.environ.get("CRAWLAB_TASK_ID")
        if not task_id:
            raise ValueError("Crawlab did not supply CRAWLAB_TASK_ID")
        asyncio.run(report_source(job, summary, base_url, key, task_id))
        print(json.dumps({"kind": "capture", "committed": True, "failed": failed}), flush=True)
        return 1 if failed else 0
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "committed": False}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
