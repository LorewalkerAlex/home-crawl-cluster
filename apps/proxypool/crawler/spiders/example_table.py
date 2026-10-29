from .base import SourceSpider, endpoint_line


class ExampleTableSpider(SourceSpider):
    """Offline teaching fixture: one source parser handles its own pagination."""

    name = "example_table"

    def parse(self, response):
        for row in response.css("table#proxies tr"):
            values = [value.strip() for value in row.css("td::text").getall()]
            if len(values) >= 2:
                endpoint = endpoint_line(values[0] + ":" + values[1])
                if endpoint:
                    yield endpoint
        following = response.css("a.next::attr(href)").get()
        if following:
            yield response.follow(following, callback=self.parse)
