"""89IP's extraction endpoint contains HTML fragments, not one endpoint per line."""

import re

from apps.proxypool.crawler.spiders.base import SourceSpider, endpoint_line


class ProxySourceSpider(SourceSpider):
    name = "ip89"

    def parse(self, response):
        text = " ".join(
            response.xpath("//text()[not(ancestor::script) and not(ancestor::style)]").getall()
        )
        for value in re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}(?!\d)", text):
            endpoint = endpoint_line(value)
            if endpoint:
                yield endpoint
