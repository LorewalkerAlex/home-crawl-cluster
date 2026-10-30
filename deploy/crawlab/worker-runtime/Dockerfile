ARG CRAWLAB_IMAGE=crawlabteam/crawlab:0.6.3@sha256:9dbe97240ba2670ce49fd1be5d557b59653e602d8fa29a71c9e05b26c557193b
FROM ${CRAWLAB_IMAGE}

LABEL org.opencontainers.image.title="Crawlab shared task runtime"

# Only shared dependencies. Preserve the upstream Python/SDK, PATH, WORKDIR and CMD.
COPY requirements/tasks.txt /tmp/crawl-runtime-requirements.txt
RUN python -c 'import pip; assert tuple(map(int, pip.__version__.split(".")[:2])) >= (22, 3), "pip >=22.3 required"' \
    && python -m venv --without-pip /opt/crawl-runtime \
    && python -m pip --python /opt/crawl-runtime --disable-pip-version-check install \
      --no-input --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple \
      -r /tmp/crawl-runtime-requirements.txt \
    && python -m pip --python /opt/crawl-runtime --disable-pip-version-check check \
    && /opt/crawl-runtime/bin/python -c 'import scrapy, aiohttp, pydantic; print("Shared task dependencies ready")' \
    && rm -f /tmp/crawl-runtime-requirements.txt
