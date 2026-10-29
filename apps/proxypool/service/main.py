"""Run exactly one API process: python -m apps.proxypool.service.main."""

import argparse
import uvicorn
from .app import create_app
from .config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    options = parser.parse_args()
    settings = Settings.load(options.config)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        limit_concurrency=settings.queue_max_requests,
        access_log=False,
    )


if __name__ == "__main__":
    main()
