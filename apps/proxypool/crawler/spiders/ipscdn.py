"""SCDN: one bounded API request; HTTPS support does not imply TLS to the proxy."""

from apps.proxypool.crawler.spiders.base import SourceSpider, endpoint_line


class ProxySourceSpider(SourceSpider):
    name = "ipscdn"

    def parse(self, response):
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ValueError("SCDN API did not return success")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("proxies"), list):
            raise ValueError("SCDN proxy list schema changed")
        for value in data["proxies"]:
            if not isinstance(value, str):
                continue
            endpoint = endpoint_line(value)
            if endpoint:
                yield endpoint
