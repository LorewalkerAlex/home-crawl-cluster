"""Copy to a reviewed site script, implement parse, test, then explicitly register."""

from apps.proxypool.crawler.spiders.base import SourceSpider, endpoint_line


class ProxySourceSpider(SourceSpider):
    name = "registered_source"

    def parse(self, response):
        for line in response.text.splitlines():
            endpoint = endpoint_line(line)
            if endpoint:
                yield endpoint
        # Site pagination belongs here: yield response.follow(next_url, callback=self.parse).
