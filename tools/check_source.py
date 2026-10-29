"""Explicitly test a reviewed source parser on one offline HTML/text fixture."""

import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.register_source import validate_source
from apps.proxypool.service.models import Endpoint
from scrapy.http import HtmlResponse
from scrapy import Request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--url", default="https://example.invalid/list")
    args = parser.parse_args()
    validate_source(args.source)
    spec = importlib.util.spec_from_file_location("reviewed_source_fixture", args.source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    spider = module.ProxySourceSpider(source_url=args.url)
    response = HtmlResponse(args.url, body=args.fixture.read_bytes(), encoding="utf-8")
    proxies = requests = 0
    for item in spider.parse(response):
        if isinstance(item, Request):
            requests += 1
        else:
            Endpoint.model_validate(item)
            proxies += 1
    print({"valid_proxies": proxies, "followup_requests": requests, "network_requests": 0})


if __name__ == "__main__":
    main()
