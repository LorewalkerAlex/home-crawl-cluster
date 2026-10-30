"""FreeOnly's current paginated JSON and legacy list share the endpoint parser."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from apps.proxypool.crawler.spiders.base import SourceSpider
from apps.proxypool.contracts import Endpoint


class ProxySourceSpider(SourceSpider):
    name = "ipfreeonly"
    # Preserve the old requested 500-item scope; never download the entire pool.
    maximum_items = 500

    def __init__(self, source_url, **kwargs):
        super().__init__(source_url=source_url, **kwargs)
        self.processed_items = 0

    def parse(self, response):
        payload = response.json()
        paginated = isinstance(payload, dict)
        if paginated:
            items, total = payload.get("items"), payload.get("totalItems")
            if not isinstance(items, list) or type(total) is not int or total < 0:
                raise ValueError("FreeOnly paginated list schema changed")
        elif isinstance(payload, list):
            items, total = payload, len(payload)
        else:
            raise ValueError("FreeOnly proxy list schema changed")
        for item in items:
            if self.processed_items >= self.maximum_items:
                break
            self.processed_items += 1
            if not isinstance(item, dict):
                continue
            country = item.get("country")
            if isinstance(country, dict):
                country = country.get("countryCode")
            protocols = item.get("protocols")
            if country == "CN" or not isinstance(protocols, list) or "http" not in protocols:
                continue
            # This model cannot represent authenticated proxies; never strip their credentials
            # and turn them into apparently anonymous endpoints.
            if item.get("username") or item.get("password"):
                continue
            try:
                yield Endpoint(scheme="http", host=item["ip"], port=int(item["port"])).model_dump()
            except (KeyError, ValueError, TypeError):
                continue
        if paginated and items and self.processed_items < min(total, self.maximum_items):
            parsed = urlsplit(response.url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query["page"] = str(int(query.get("page", "1")) + 1)
            yield response.follow(
                urlunsplit(parsed._replace(query=urlencode(query))), callback=self.parse
            )
