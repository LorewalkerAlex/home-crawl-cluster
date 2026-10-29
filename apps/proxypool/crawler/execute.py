"""Run a registered source in a fresh task process, using Scrapy's own engine."""

import importlib
import importlib.util
from pathlib import Path
import time
from scrapy import signals
from scrapy.crawler import CrawlerProcess
from .registry import SOURCES
from ..service.models import Endpoint


def capture(job):
    source = job["source"]
    if job.get("workspace_spider"):
        path = Path.cwd() / "source_spider.py"
        spec = importlib.util.spec_from_file_location("registered_proxy_source", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        spider = module.ProxySourceSpider
        from .spiders.base import SourceSpider

        if not issubclass(spider, SourceSpider):
            raise ValueError("Registered source must implement SourceSpider")
    else:
        entry = SOURCES.get(source["spider_key"])
        if entry is None:
            raise ValueError("Source is not explicitly registered")
        module, name = entry["entry"].split(":")
        spider = getattr(importlib.import_module(module), name)
    maximum = int(job["source_max_records"])
    settings = {
        "LOG_LEVEL": "ERROR",
        "LOG_STDOUT": False,
        "TELNETCONSOLE_ENABLED": False,
        "COOKIES_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "HTTPPROXY_ENABLED": False,
        "CONCURRENT_REQUESTS": 2,
        "RETRY_TIMES": 1,
        "DOWNLOAD_TIMEOUT": float(job["request_timeout_seconds"]),
        "DOWNLOAD_MAXSIZE": 2 * 1024 * 1024,
        "DOWNLOAD_WARNSIZE": 1024 * 1024,
        "CLOSESPIDER_ITEMCOUNT": maximum,
        "CLOSESPIDER_PAGECOUNT": int(job["source_max_pages"]),
        "CLOSESPIDER_TIMEOUT": float(job["source_timeout_seconds"]),
        "USER_AGENT": "Proxypool/1.0",
        "DOWNLOADER_CLIENTCONTEXTFACTORY": "scrapy.core.downloader.contextfactory.BrowserLikeContextFactory",
    }
    process = CrawlerProcess(settings=settings, install_root_handler=False)
    crawler = process.create_crawler(spider)
    output, seen = [], set()
    errors = []

    def item_scraped(item, response, spider):
        try:
            endpoint = Endpoint.model_validate(dict(item)).model_dump()
        except ValueError:
            return
        key = tuple(endpoint[x] for x in ("scheme", "host", "port"))
        if key not in seen and len(output) < maximum:
            seen.add(key)
            output.append(endpoint)

    def spider_error(failure, response, spider):
        errors.append("parse_error")

    crawler.signals.connect(item_scraped, signal=signals.item_scraped, weak=False)
    crawler.signals.connect(spider_error, signal=signals.spider_error, weak=False)
    start = time.monotonic()
    process.crawl(crawler, source_url=source["url"])
    process.start()
    stats = crawler.stats.get_stats()
    ok = sum(
        value
        for key, value in stats.items()
        if key.startswith("downloader/response_status_count/2")
    )
    # A followed redirect is part of a successful fetch, not a failed page.
    failed = sum(
        value
        for key, value in stats.items()
        if key.startswith("downloader/response_status_count/") and int(key.rsplit("/", 1)[1]) >= 400
    ) + int(stats.get("downloader/exception_count", 0))
    reason = stats.get("finish_reason", "unknown")
    good = bool(ok) and not failed and not errors and reason == "finished"
    return {
        "source_id": source["source_id"],
        "outcome": "success" if good else "failed",
        "proxies": output,
        "duration_ms": round((time.monotonic() - start) * 1000, 3),
        "pages_ok": ok,
        "pages_failed": failed,
        "error_code": None if good else ("parse_error" if errors else "capture_incomplete"),
    }
