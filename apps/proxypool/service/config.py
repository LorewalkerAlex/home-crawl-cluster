"""Small explicit configuration; secrets are read only from the environment."""

from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def secret(name):
    if os.getenv(name):
        return os.environ[name]
    filename = os.getenv(name + "_FILE")
    return Path(filename).read_text(encoding="utf-8").strip() if filename else ""


@dataclass(frozen=True)
class Settings:
    database: str = "runtime/proxypool.sqlite3"
    host: str = "127.0.0.1"
    port: int = 13004
    write_flush_seconds: float = 1.0
    write_batch_records: int = 1000
    queue_max_records: int = 10000
    queue_max_requests: int = 128
    max_request_records: int = 1000
    max_request_bytes: int = 262144
    read_threads: int = 2
    window_size: int = 63
    wilson_z: float = 1.96
    latency_alpha: float = 0.2
    min_test_interval_seconds: float = 300
    test_timeout_seconds: float = 600
    candidate_count: int = 100
    candidate_ttl_seconds: float = 60
    candidate_min_score: float = 50
    feedback_retry_window_seconds: float = 60
    dedup_max_ids: int = 20000
    pool_limit: int = 100000
    eviction_min_feedback: int = 50
    max_targets: int = 32
    max_sources: int = 64
    source_registry_file: str = ""
    publishing_enabled: bool = False
    capture_interval_seconds: float = 3600
    test_publish_interval_seconds: float = 60
    test_batch_size: int = 100
    test_max_batches_per_tick: int = 1
    crawlab_url: str = ""
    crawlab_ui_url: str = ""
    capture_spider_id: str = ""
    tester_spider_id: str = ""
    native_task_limit: int = 8
    worker_concurrency: int = 20
    request_timeout_seconds: float = 10
    source_timeout_seconds: float = 120
    source_max_records: int = 10000
    source_max_pages: int = 50
    retry_attempts: int = 3
    login_session_seconds: int = 86400
    session_https_only: bool = True

    def __post_init__(self):
        if type(self.session_https_only) is not bool:
            raise ValueError("session_https_only must be a JSON boolean")
        if type(self.publishing_enabled) is not bool:
            raise ValueError("publishing_enabled must be a JSON boolean")
        if self.crawlab_ui_url and urlsplit(self.crawlab_ui_url).scheme not in (
            "http",
            "https",
        ):
            raise ValueError("Crawlab UI URL must be HTTP(S)")
        integers = [
            "port",
            "login_session_seconds",
            "write_batch_records",
            "queue_max_records",
            "queue_max_requests",
            "max_request_records",
            "max_request_bytes",
            "read_threads",
            "window_size",
            "candidate_count",
            "dedup_max_ids",
            "pool_limit",
            "max_targets",
            "max_sources",
            "test_batch_size",
            "test_max_batches_per_tick",
            "native_task_limit",
            "worker_concurrency",
            "retry_attempts",
            "source_max_records",
            "source_max_pages",
        ]
        for name in integers:
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError("Configuration must be a positive integer: " + name)
        for name in [
            "write_flush_seconds",
            "wilson_z",
            "min_test_interval_seconds",
            "test_timeout_seconds",
            "candidate_ttl_seconds",
            "feedback_retry_window_seconds",
            "capture_interval_seconds",
            "test_publish_interval_seconds",
            "request_timeout_seconds",
            "source_timeout_seconds",
        ]:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 < value < float("inf")
            ):
                raise ValueError("Configuration must be positive and finite: " + name)
        if not 1 <= self.window_size <= 63 or not 0 < self.latency_alpha <= 1:
            raise ValueError("Invalid window or EWMA coefficient")
        if not 0 <= self.candidate_min_score <= 100 or not 1 <= self.port <= 65535:
            raise ValueError("Invalid score or port")
        if (
            not self.max_request_records
            <= self.write_batch_records
            <= self.queue_max_records
        ):
            raise ValueError(
                "Require request records <= batch records <= queue records"
            )
        if (
            self.test_batch_size > self.max_request_records
            or self.candidate_count > self.max_request_records
        ):
            raise ValueError("Batch or candidates exceed request limit")
        if (
            type(self.eviction_min_feedback) is not int
            or self.eviction_min_feedback < 0
        ):
            raise ValueError("Invalid eviction threshold")

    @classmethod
    def load(cls, path=None):
        path = path or os.getenv("PROXYPOOL_CONFIG")
        data = {} if not path else json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError("Unknown configuration fields")
        return cls(**data)
