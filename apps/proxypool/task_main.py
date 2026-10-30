"""Crawlab program entry; task arguments contain data, never credentials or code."""

import argparse
import asyncio
import base64
from collections import Counter
import json
import logging
import os
from pathlib import Path
import ssl
import sys
import time
from urllib.parse import urlsplit
import aiohttp
from .contracts import Feedback, SourceResult
from .task_settings import TaskSettings
from .task_batches import callback_chunks

logger = logging.getLogger(__name__)


def task_event(event, **fields):
    """Bounded task diagnostics; never dump job data, credentials or proxy URLs."""
    print(json.dumps({"event": event, **fields}), flush=True)


async def post_committed(base_url, key, path, payload, job):
    deadline = time.monotonic() + float(job["retry_window_seconds"])
    error = None
    async with aiohttp.ClientSession(trust_env=False) as session:
        for attempt in range(int(job["retry_attempts"])):
            http_status = None
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
                    http_status = response.status
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
                    task_event(
                        "callback_retry",
                        path=path,
                        attempt=attempt + 1,
                        next_attempt=attempt + 2,
                        http_status=http_status,
                        error_type=type(caught).__name__,
                    )
                    await asyncio.sleep(min(2**attempt, max(0, deadline - time.monotonic())))
    task_event(
        "callback_failed", path=path, error_type=type(error).__name__ if error else "deadline"
    )
    raise RuntimeError("Callback retries exhausted") from error


async def report_tests(job, base_url, key, options=None):
    from .tester.execute import test_proxies

    options = options or {}
    job = dict(
        job,
        concurrency=min(int(job["concurrency"]), int(options.get("max_concurrency", 20))),
    )
    started = last_progress = time.monotonic()
    total = len(job["proxies"])
    completed = successes = responses = request_errors = local_errors = 0
    statuses, errors, exception_types, proxy_statuses = Counter(), Counter(), Counter(), Counter()
    successful_latencies, examples = [], []
    target = job["target"]
    task_event(
        "test_start",
        task_id=os.getenv("CRAWLAB_TASK_ID"),
        target_id=target["target_id"],
        target_host=urlsplit(target["test_url"]).hostname,
        proxy_count=total,
        concurrency=job["concurrency"],
        timeout_seconds=job["request_timeout_seconds"],
    )

    def completed_result(record, diagnostic):
        nonlocal completed, successes, responses, request_errors, local_errors, last_progress
        completed += 1
        if record["outcome"] == "response":
            responses += 1
            statuses[str(record["http_status"])] += 1
            if record["http_status"] == 200:
                successes += 1
                successful_latencies.append(record["latency_ms"])
        elif record["outcome"] == "request_error":
            request_errors += 1
        else:
            local_errors += 1
        if record.get("error_code"):
            errors[record["error_code"]] += 1
        if diagnostic.get("exception_type"):
            exception_types[diagnostic["exception_type"]] += 1
        if diagnostic.get("proxy_http_status") is not None:
            proxy_statuses[str(diagnostic["proxy_http_status"])] += 1
        if (
            not (record["outcome"] == "response" and record["http_status"] == 200)
            and len(examples) < 3
        ):
            examples.append(
                {k: record[k] for k in ("proxy_id", "outcome", "http_status", "error_code")}
                | diagnostic
            )
        now = time.monotonic()
        if completed == 1 or completed == total or completed % 10 == 0 or now - last_progress >= 15:
            task_event(
                "test_progress",
                completed=completed,
                total=total,
                http_200=successes,
                other_http=responses - successes,
                request_errors=request_errors,
                local_errors=local_errors,
                elapsed_seconds=round(now - started, 2),
            )
            last_progress = now

    context = ssl.create_default_context(cafile=options.get("ca_file"))
    records = await test_proxies(
        job, headers=options.get("request_headers"), ssl_context=context, on_result=completed_result
    )
    for item in records:
        Feedback.model_validate(item)
    task_event(
        "test_summary",
        target_id=target["target_id"],
        tested=len(records),
        success_count=successes,
        failure_count=responses - successes + request_errors,
        local_error_count=local_errors,
        http_status_counts=dict(statuses),
        error_counts=dict(errors),
        exception_types=dict(exception_types),
        proxy_http_status_counts=dict(proxy_statuses),
        failure_examples=examples,
        successful_latency_avg_ms=round(sum(successful_latencies) / len(successful_latencies), 3)
        if successful_latencies
        else None,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
    )
    for part, payload in enumerate(
        callback_chunks(records, lambda chunk, part: {"results": chunk}, job)
    ):
        task_event("feedback_send", part=part, records=len(payload["results"]))
        ack = await post_committed(base_url, key, "/v1/proxies/feedback", payload, job)
        task_event(
            "feedback_committed",
            part=part,
            records=len(payload["results"]),
            accepted=ack.get("accepted"),
            ignored=ack.get("ignored"),
            committed=True,
        )
    return len(records)


async def report_source(job, summary, base_url, key, task_id):
    records = summary.pop("proxies")
    inserted = 0
    part_count = 0
    for payload in callback_chunks(
        records,
        lambda chunk, part: dict(summary, task_id=task_id, proxies=chunk, part=part, final=False),
        job,
    ):
        SourceResult.model_validate(payload)
        ack = await post_committed(base_url, key, "/v1/source-results", payload, job)
        task_event(
            "capture_import_committed",
            part=part_count,
            records=len(payload["proxies"]),
            inserted=ack.get("inserted"),
            committed=True,
        )
        inserted += ack.get("inserted", 0)
        part_count += 1
    final = dict(
        summary,
        task_id=task_id,
        proxies=[],
        part=part_count,
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
    parser.add_argument("--job")
    parser.add_argument(
        "--settings", help="Node-private task settings JSON; never synced with source"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check delivered code and runtime without network",
    )
    options = parser.parse_args()
    if options.check:
        from importlib import import_module, metadata
        from .crawler.registry import SOURCES

        for entry in SOURCES.values():
            module, _ = entry["entry"].split(":")
            import_module(module)
        import_module("apps.proxypool.tester.execute")
        import_module("apps.proxypool.crawler.proxy")
        settings = TaskSettings.load(options.settings) if options.settings else None
        print(
            json.dumps(
                {
                    "runtime_check": "PASS",
                    "python": sys.version.split()[0],
                    "dependencies": {
                        name: metadata.version(name) for name in ("aiohttp", "pydantic", "Scrapy")
                    },
                    "settings_configured": settings is not None,
                    "capture_proxy_configured": bool(settings and settings.capture_proxy_url),
                }
            ),
            flush=True,
        )
        return 0
    if not options.job:
        parser.error("--job is required unless --check is used")
    job = decode_job(options.job)
    settings = TaskSettings.load(options.settings)
    base_url, key = settings.m05_url, settings.api_key
    try:
        if job["kind"] == "test":
            count = asyncio.run(report_tests(job, base_url, key, settings.execution_options()))
            print(
                json.dumps({"kind": "test", "reported": count, "committed": True}),
                flush=True,
            )
            return 0
        start = time.monotonic()
        task_event(
            "capture_start",
            task_id=os.getenv("CRAWLAB_TASK_ID"),
            source_id=job["source"]["source_id"],
            source_key=job["source"]["spider_key"],
            source_host=urlsplit(job["source"]["url"]).hostname,
            capture_proxy_configured=bool(settings.capture_proxy_url),
        )
        try:
            from .crawler.execute import capture

            summary = capture(
                job,
                program_root=Path(__file__).resolve().parents[2],
                proxy_url=settings.capture_proxy_url,
            )
        except Exception:
            logger.exception("Capture execution failed")
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
        task_event(
            "capture_summary",
            source_id=summary["source_id"],
            outcome=summary["outcome"],
            output_count=len(summary["proxies"]),
            pages_ok=summary["pages_ok"],
            pages_failed=summary["pages_failed"],
            duration_ms=summary["duration_ms"],
            error_code=summary["error_code"],
        )
        task_id = os.environ.get("CRAWLAB_TASK_ID")
        if not task_id:
            raise ValueError("Crawlab did not supply CRAWLAB_TASK_ID")
        asyncio.run(report_source(job, summary, base_url, key, task_id))
        print(
            json.dumps({"kind": "capture", "committed": True, "failed": failed}),
            flush=True,
        )
        return 1 if failed else 0
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "committed": False}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
