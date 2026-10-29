"""One network attempt per proxy; only result transmission is retried."""

import asyncio
import ssl
import time
from uuid import uuid4
import aiohttp

DEFAULT_HEADERS = {"User-Agent": "Proxypool/1.0"}


async def test_proxies(job, *, headers=None, ssl_context=None):
    concurrency = int(job["concurrency"])
    if not 1 <= concurrency <= 1000 or not 0 < float(job["request_timeout_seconds"]) <= 600:
        raise ValueError("Invalid test execution bounds")
    semaphore = asyncio.Semaphore(concurrency)
    target = job["target"]
    timeout = aiohttp.ClientTimeout(total=float(job["request_timeout_seconds"]))
    connector = aiohttp.TCPConnector(
        limit=concurrency, ssl=ssl_context or ssl.create_default_context()
    )
    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:

        async def visit(member):
            record = {
                "result_id": uuid4().hex,
                "proxy_id": member["proxy_id"],
                "target_id": target["target_id"],
                "target_revision": target["revision"],
                "outcome": "local_error",
                "http_status": None,
                "error_code": None,
                "latency_ms": None,
            }
            async with semaphore:
                start = time.monotonic()
                try:
                    async with session.get(
                        target["test_url"],
                        proxy=member["proxy_url"],
                        headers=headers or DEFAULT_HEADERS,
                        timeout=timeout,
                        allow_redirects=True,
                    ) as response:
                        record.update(outcome="response", http_status=response.status)
                except asyncio.TimeoutError:
                    record.update(outcome="request_error", error_code="timeout")
                except aiohttp.ClientError:
                    record.update(outcome="request_error", error_code="connection_error")
                except (ValueError, TypeError):
                    record.update(outcome="local_error", error_code="invalid_request")
                record["latency_ms"] = round((time.monotonic() - start) * 1000, 3)
            return record

        return await asyncio.gather(*(visit(member) for member in job["proxies"]))
