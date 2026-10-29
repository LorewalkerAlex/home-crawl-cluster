"""Reviewed Scrapy building blocks; a registry entry is still required."""

from urllib.parse import urlsplit
import scrapy
from ...service.models import Endpoint


def endpoint_line(line):
    line = line.strip()
    try:
        if "://" not in line:
            host, port = line.rsplit(":", 1)
            return Endpoint(scheme="http", host=host.strip("[]"), port=int(port)).model_dump()
        parsed = urlsplit(line)
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        return Endpoint(scheme=parsed.scheme, host=parsed.hostname, port=parsed.port).model_dump()
    except (ValueError, TypeError):
        return None


class SourceSpider(scrapy.Spider):
    name = "source_base"

    def __init__(self, source_url, **kwargs):
        super().__init__(**kwargs)
        self.start_urls = [source_url]
        self.allowed_domains = [urlsplit(source_url).hostname]

    async def start(self):
        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse, dont_filter=True)


class TextSourceSpider(SourceSpider):
    def parse(self, response):
        for line in response.text.splitlines():
            endpoint = endpoint_line(line)
            if endpoint:
                yield endpoint
