"""Public JSON shapes. No client supplied scores or test state."""

import ipaddress
from typing import Literal, Optional
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def http_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Expected HTTP(S) URL without credentials")
    if len(value) > 2048 or parsed.fragment:
        raise ValueError("Invalid URL")
    parsed.port  # Validate port syntax.
    return value


class Endpoint(Model):
    scheme: Literal["http", "https"] = "http"
    host: str = Field(min_length=1, max_length=64)
    port: int = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value):
        return str(ipaddress.ip_address(value))


class TargetInput(Model):
    name: str = Field(min_length=1, max_length=80)
    test_url: str
    enabled: bool = True

    @field_validator("test_url")
    @classmethod
    def valid_url(cls, value):
        return http_url(value)


class SourceInput(Model):
    name: str = Field(min_length=1, max_length=80)
    spider_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    url: str
    enabled: bool = False

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return http_url(value)


class Feedback(Model):
    result_id: str = Field(min_length=1, max_length=128)
    proxy_id: int = Field(gt=0)
    target_id: int = Field(gt=0)
    target_revision: int = Field(gt=0)
    outcome: Literal["response", "request_error", "local_error", "cancelled"]
    http_status: Optional[int] = Field(default=None, ge=100, le=599)
    latency_ms: Optional[float] = Field(
        default=None, ge=0, le=3600000, allow_inf_nan=False
    )
    error_code: Optional[str] = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9_]+$"
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.outcome == "response":
            if (
                self.http_status is None
                or self.latency_ms is None
                or self.error_code is not None
            ):
                raise ValueError("Response requires status and latency")
        elif self.http_status is not None:
            raise ValueError("Only response can have HTTP status")
        elif self.outcome == "request_error" and self.latency_ms is None:
            raise ValueError("Request error requires elapsed time")
        return self


class FeedbackPackage(Model):
    results: list[Feedback] = Field(min_length=1)


class ImportPackage(Model):
    proxies: list[Endpoint] = Field(min_length=1)


class SourceResult(Model):
    source_id: int = Field(gt=0)
    task_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["success", "failed"]
    proxies: list[Endpoint] = Field(default_factory=list)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    pages_ok: int = Field(default=0, ge=0)
    pages_failed: int = Field(default=0, ge=0)
    error_code: Optional[str] = Field(
        default=None, max_length=64, pattern=r"^[a-z0-9_]+$"
    )
    final: bool = True
    part: int = Field(default=0, ge=0)
    output_count: Optional[int] = Field(default=None, ge=0)
    total_inserted: Optional[int] = Field(default=None, ge=0)


class ScheduledPackage(Model):
    target_id: int = Field(gt=0)
    target_revision: int = Field(gt=0)
    proxy_ids: list[int] = Field(min_length=1)
    scheduled_at: int = Field(gt=0)


class Acquire(Model):
    target_id: int = Field(gt=0)
