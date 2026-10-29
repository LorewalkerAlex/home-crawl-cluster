"""Explicit, reviewed source entries. Adding a file does not register code."""

import json
from pathlib import Path
import re

SOURCES = {
    "proxyscrape": {
        "name": "ProxyScrape HTTP text",
        "entry": "apps.proxypool.crawler.spiders.proxyscrape:ProxyScrapeSpider",
        "example_url": "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
    },
    "proxy_list_download": {
        "name": "Proxy List Download HTTP text",
        "entry": "apps.proxypool.crawler.spiders.proxy_list_download:ProxyListDownloadSpider",
        "example_url": "https://www.proxy-list.download/api/v1/get?type=http",
    },
    "example_table": {
        "name": "Offline/example HTML table",
        "entry": "apps.proxypool.crawler.spiders.example_table:ExampleTableSpider",
        "example_url": "https://example.invalid/proxies",
    },
}


def load_entries(filename=""):
    entries = dict(SOURCES)
    if not filename:
        return entries
    path = Path(filename)
    if path.stat().st_size > 65536:
        raise ValueError("Source registry exceeds 64 KiB")
    extra = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(extra, dict) or len(extra) > 64:
        raise ValueError("Invalid source registry")
    for key, value in extra.items():
        if (
            not re.fullmatch("[a-z0-9_]{1,64}", key)
            or not isinstance(value, dict)
            or set(value) != {"name", "spider_id", "example_url"}
            or not re.fullmatch("[0-9a-f]{24}", value["spider_id"])
        ):
            raise ValueError("Invalid registered source entry")
        entries[key] = dict(value, workspace_spider=True)
    return entries
