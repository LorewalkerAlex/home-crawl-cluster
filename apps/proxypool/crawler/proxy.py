"""Give source requests to Scrapy's standard HttpProxyMiddleware (priority 750)."""


class CaptureProxyMiddleware:
    def __init__(self, proxy_url):
        self.proxy_url = proxy_url

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get("CAPTURE_PROXY_URL"))

    def process_request(self, request):
        request.meta.setdefault("proxy", self.proxy_url)
