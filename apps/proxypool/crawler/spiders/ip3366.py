"""Legacy seven-page table source; JS challenge pages remain explicit failures."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from apps.proxypool.crawler.spiders.base import SourceSpider, endpoint_line


class ProxySourceSpider(SourceSpider):
    name = "ip3366"
    last_page = 7

    def parse(self, response):
        if not response.xpath("//table"):
            raise ValueError("IP3366 did not return its proxy table")
        for row in response.xpath("//table//tr[td]"):
            host = row.xpath("string(td[1])").get().strip()
            port = row.xpath("string(td[2])").get().strip()
            endpoint = endpoint_line(host + ":" + port)
            if endpoint:
                yield endpoint
        parsed = urlsplit(response.url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        page = int(query.get("page", "1"))
        if 1 <= page < self.last_page:
            query["page"] = str(page + 1)
            yield response.follow(
                urlunsplit(parsed._replace(query=urlencode(query))), callback=self.parse
            )
