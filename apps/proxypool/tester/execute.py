"""One network attempt per proxy; only result transmission is retried."""

import asyncio
import ssl
import time
from uuid import uuid4
import aiohttp

DEFAULT_HEADERS = {"User-Agent": "Proxypool/1.0"}


async def test_proxies(job, *, headers=None, ssl_context=None, on_result=None):
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
            diagnostic = {}
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
                except asyncio.TimeoutError as error:
                    record.update(outcome="request_error", error_code="timeout")
                    diagnostic["exception_type"] = type(error).__name__
                except aiohttp.ClientError as error:
                    record.update(outcome="request_error", error_code="connection_error")
                    diagnostic["exception_type"] = type(error).__name__
                    if isinstance(error, aiohttp.ClientHttpProxyError):
                        diagnostic["proxy_http_status"] = error.status
                except (ValueError, TypeError) as error:
                    record.update(outcome="local_error", error_code="invalid_request")
                    diagnostic["exception_type"] = type(error).__name__
                record["latency_ms"] = round((time.monotonic() - start) * 1000, 3)
            if on_result is not None:
                on_result(record, diagnostic)
            return record

        return await asyncio.gather(*(visit(member) for member in job["proxies"]))
