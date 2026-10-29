"""Private settings provided by a node administrator, outside the synced program."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
from .contracts import http_url


@dataclass(frozen=True)
class TaskSettings:
    m05_url: str
    api_key: str
    request_headers: dict | None = None
    ca_file: str | None = None
    max_concurrency: int = 20

    def __post_init__(self):
        http_url(self.m05_url)
        if len(self.api_key) < 16:
            raise ValueError("Configure a private M05 API key (at least 16 characters)")
        if (
            type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= 1000
        ):
            raise ValueError("Invalid node request concurrency limit")
        if self.request_headers is not None and (
            not isinstance(self.request_headers, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in self.request_headers.items()
            )
        ):
            raise ValueError("Request headers must be a string mapping")

    @classmethod
    def load(cls, filename=None):
        filename = filename or os.getenv("PROXYPOOL_TASK_SETTINGS")
        data = (
            json.loads(Path(filename).read_text(encoding="utf-8")) if filename else {}
        )
        if not isinstance(data, dict):
            raise ValueError("Task settings must be a JSON object")
        data.setdefault("m05_url", os.getenv("PROXYPOOL_M05_URL", ""))
        if "api_key" not in data:
            key_file = os.getenv("PROXYPOOL_API_KEY_FILE")
            data["api_key"] = os.getenv("PROXYPOOL_API_KEY") or (
                Path(key_file).read_text(encoding="utf-8").strip() if key_file else ""
            )
        return cls(**data)

    def execution_options(self):
        return {
            "request_headers": self.request_headers,
            "ca_file": self.ca_file,
            "max_concurrency": self.max_concurrency,
        }
