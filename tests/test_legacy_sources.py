"""Legacy source contracts, provider failures and bounded same-site pagination."""

import importlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, TextResponse

from integrations.crawlab.program import program_files
from tools.register_source import validate_source


def spider(key, url="https://example.invalid/list?page=1"):
    return importlib.import_module("apps.proxypool.crawler.spiders." + key).ProxySourceSpider(
        source_url=url
    )


def json_response(value, url="https://example.invalid/list?page=1"):
    return TextResponse(url, body=json.dumps(value).encode(), encoding="utf-8")


def test_ip89_extracts_visible_fragment_and_rejects_invalid_endpoints():
    response = HtmlResponse(
        "https://example.invalid/list",
        body=b"<script>192.0.2.9:9000</script>192.0.2.1:8080<br>999.0.0.1:8080<br>192.0.2.2:99999<br>192.0.2.3:3128",
        encoding="utf-8",
    )
    rows = list(spider("ip89").parse(response))
    assert [(x["host"], x["port"]) for x in rows] == [("192.0.2.1", 8080), ("192.0.2.3", 3128)]


def test_scdn_is_one_request_and_https_capability_keeps_http_proxy_scheme():
    result = list(
        spider("ipscdn").parse(
            json_response({"code": 200, "data": {"proxies": ["192.0.2.1:8080", "bad", 12]}})
        )
    )
    assert result == [{"scheme": "http", "host": "192.0.2.1", "port": 8080}]
    assert not any(isinstance(x, Request) for x in result)


@pytest.mark.parametrize(
    "key,payload",
    [
        ("ipscdn", {"code": 429, "message": "limit"}),
        ("ipscdn", {"code": 200, "data": []}),
        ("ipfreeonly", {"items": [], "totalItems": "unknown"}),
        ("ipfreeonly", {"error": "unavailable"}),
    ],
)
def test_provider_error_is_not_silently_an_empty_success(key, payload):
    with pytest.raises(ValueError):
        list(spider(key).parse(json_response(payload)))


def test_ip3366_pagination_preserves_query_and_stops_at_original_seven_pages():
    source = spider("ip3366")
    for page in (1, 7):
        response = HtmlResponse(
            f"http://example.invalid/?stype=1&page={page}",
            body=b"<table><tbody><tr><td>192.0.2.1</td><td>8080</td></tr></tbody></table>",
            encoding="utf-8",
        )
        result = list(source.parse(response))
        assert result[0]["port"] == 8080
        requests = [x for x in result if isinstance(x, Request)]
        assert len(requests) == (1 if page == 1 else 0)
        if requests:
            assert parse_qs(urlsplit(requests[0].url).query) == {"stype": ["1"], "page": ["2"]}
    with pytest.raises(ValueError, match="proxy table"):
        list(
            source.parse(
                HtmlResponse(
                    "http://example.invalid/",
                    body=b"<script>challenge()</script>",
                    encoding="utf-8",
                )
            )
        )


def test_freeonly_keeps_legacy_country_protocol_filters_and_skips_credentials():
    sample = {"ip": "192.0.2.1", "port": "8080", "country": "US", "protocols": ["http"]}
    items = [
        sample,
        dict(sample, country="CN"),
        dict(sample, protocols=["socks5"]),
        dict(sample, username="synthetic-user", password="synthetic-password"),
    ]
    assert list(spider("ipfreeonly").parse(json_response(items))) == [
        {"scheme": "http", "host": "192.0.2.1", "port": 8080}
    ]


def test_freeonly_current_country_shape_and_bounded_pagination():
    source = spider("ipfreeonly", "https://example.invalid/list?page=1&locale=en&where=%7B%7D")
    source.maximum_items = 2
    sample = {
        "ip": "192.0.2.1",
        "port": "8080",
        "country": {"countryCode": "US"},
        "protocols": ["http"],
        "username": "",
        "password": "",
    }
    first = list(
        source.parse(json_response({"items": [sample], "totalItems": 100}, source.start_urls[0]))
    )
    following = first[-1]
    assert isinstance(following, Request)
    assert parse_qs(urlsplit(following.url).query) == {
        "page": ["2"],
        "locale": ["en"],
        "where": ["{}"],
    }
    assert (
        list(
            source.parse(
                json_response(
                    {"items": [dict(sample, country={"countryCode": "CN"})], "totalItems": 100},
                    following.url,
                )
            )
        )
        == []
    )
    # An empty provider page cannot cause an infinite next-page loop.
    assert list(spider("ipfreeonly").parse(json_response({"items": [], "totalItems": 100}))) == []


def test_all_four_sources_fit_existing_hot_registration_and_full_delivery():
    files = program_files()
    root = Path(__file__).resolve().parents[1]
    for key in ("ip89", "ipscdn", "ipfreeonly", "ip3366"):
        name = f"apps/proxypool/crawler/spiders/{key}.py"
        assert validate_source(root / name) == files[name]
