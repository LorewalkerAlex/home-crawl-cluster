"""Provisional callback contract shared by the receiver and local storage tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


MAX_RECORDS = 100
MAX_BODY_BYTES = 64 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
ERROR_CODES = {"connect_timeout", "read_timeout", "connection_error", "tls_error",
               "http_error", "execution_error", "cancelled"}


class ValidationError(ValueError):
    pass


def identifier(value, field="id"):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValidationError(f"Invalid {field}")
    return value


def integer(value, field, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValidationError(f"Invalid {field}")
    return value


def parse_time(value):
    if not isinstance(value, str) or len(value) > 32 or not value.endswith("Z"):
        raise ValidationError("observed_at must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
        milliseconds = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
        return integer(milliseconds, "observed_at")
    except (ValueError, OverflowError, OSError):
        raise ValidationError("Invalid observed_at") from None


@dataclass(frozen=True)
class Result:
    result_id: str
    proxy_id: str
    target_id: str
    revision: int
    observed_at_ms: int
    outcome: str
    http_status: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None

    def __post_init__(self):
        for field in ("result_id", "proxy_id", "target_id"):
            identifier(getattr(self, field), field)
        integer(self.revision, "revision", 1)
        integer(self.observed_at_ms, "observed_at_ms")
        if self.outcome not in {"response", "proxy_error", "execution_error", "cancelled"}:
            raise ValidationError("Invalid outcome")
        if self.outcome == "response":
            integer(self.http_status, "http_status", 100, 599)
            if self.error_code is not None:
                raise ValidationError("A response must not contain error_code")
        else:
            if self.http_status is not None or self.error_code not in ERROR_CODES:
                raise ValidationError("An error requires error_code and no http_status")
        if self.latency_ms is not None:
            integer(self.latency_ms, "latency_ms", 0, 3_600_000)

    @property
    def scorable(self):
        return self.outcome in {"response", "proxy_error"}

    @property
    def success(self):
        return self.outcome == "response" and self.http_status == 200

    def fingerprint(self, origin, batch_id=None):
        payload = {"origin": origin, "batch_id": batch_id, "result": asdict(self)}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_wire(self):
        timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=self.observed_at_ms)
        return {
            "check_id": self.result_id, "proxy_id": self.proxy_id,
            "target_id": self.target_id, "target_revision": self.revision,
            "observed_at": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "outcome": self.outcome, "http_status": self.http_status,
            "latency_ms": self.latency_ms, "error_code": self.error_code,
        }


def parse_callback(payload):
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "batch_id", "task_id", "records"}:
        raise ValidationError("Invalid callback envelope fields")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValidationError("Unsupported schema_version")
    identifier(payload["batch_id"], "batch_id")
    identifier(payload["task_id"], "task_id")
    records = payload["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise ValidationError(f"records must contain 1..{MAX_RECORDS} items")
    fields = {"check_id", "proxy_id", "target_id", "target_revision", "observed_at",
              "outcome", "http_status", "latency_ms", "error_code"}
    parsed = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise ValidationError("Invalid result fields")
        result = Result(record["check_id"], record["proxy_id"], record["target_id"],
                        record["target_revision"], parse_time(record["observed_at"]),
                        record["outcome"], record["http_status"], record["latency_ms"], record["error_code"])
        if result.result_id in seen:
            raise ValidationError("Duplicate check_id in one callback")
        seen.add(result.result_id)
        parsed.append(result)
    return tuple(parsed)
