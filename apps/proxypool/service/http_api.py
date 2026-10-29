"""Loopback-only HTTP-to-SQLite integration fixture; no production authentication."""

from http.server import HTTPServer
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

from .contracts import ValidationError, identifier, parse_callback
from .http_transport import HttpError, JsonHandler
from .storage import CapacityError, ConflictError, StaleCheckError


def object_fields(payload, fields):
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise ValidationError("Invalid request fields")
    return payload


def query_fields(query, required, optional=()):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=5)
    except ValueError:
        raise ValidationError("Invalid query") from None
    if not set(required) <= set(values) <= set(required) | set(optional):
        raise ValidationError("Invalid query fields")
    if any(len(value) != 1 or not value[0] for value in values.values()):
        raise ValidationError("Duplicate or blank query field")
    return {key: value[0] for key, value in values.items()}


def make_sqlite_api(store, port=0, *, clock=None):
    now = clock or (lambda: int(time.time() * 1000))

    class Handler(JsonHandler):
        validation_code = "invalid_request"

        def error(self, status, code):
            self.respond(status, {"error": code, "committed": False})

        def do_POST(self):
            self.run_json(lambda: self.route("POST"))

        def do_GET(self):
            self.run_json(lambda: self.route("GET"))

        def route(self, method):
            try:
                return self.dispatch(method)
            except StaleCheckError:
                raise HttpError(409, "stale_check") from None
            except ConflictError:
                raise HttpError(409, "identity_conflict") from None
            except CapacityError:
                raise HttpError(429, "capacity_limit") from None
            except sqlite3.Error:
                raise HttpError(503, "storage_unavailable") from None

        def dispatch(self, method):
            url = urlsplit(self.path)
            if method == "POST":
                if url.query or url.fragment:
                    raise HttpError(404, "not_found")
                if url.path == "/internal/v1/check-batches":
                    payload = object_fields(self.read_json(), ("request_id", "target_id", "limit"))
                    batch = store.reserve_checks(payload["request_id"], payload["target_id"],
                                                 payload["limit"], now_ms=now())
                    return 200, {"committed": True, "batch": batch}
                binding = re.fullmatch(r"/internal/v1/check-batches/([^/]+)/task", url.path)
                if binding:
                    batch_id = identifier(binding[1], "batch_id")
                    payload = object_fields(self.read_json(), ("task_id",))
                    return 200, store.bind_task(batch_id, payload["task_id"])
                if url.path == "/internal/v1/check-results":
                    payload = self.read_json()
                    records = parse_callback(payload)
                    summary = store.record_test_results(payload["batch_id"], records,
                                                        now_ms=now(), task_id=payload["task_id"])
                    return 200, {"received": True, "persisted": True, **summary,
                                 "batch_id": payload["batch_id"], "task_id": payload["task_id"],
                                 "record_count": len(records)}
            else:
                batch = re.fullmatch(r"/internal/v1/check-batches/([^/]+)", url.path)
                if batch and not url.query:
                    return 200, {"batch": store.read_batch(identifier(batch[1], "batch_id"))}
                if url.path == "/internal/v1/quality":
                    params = query_fields(url.query, ("proxy_id", "target_id"))
                    proxy_id = identifier(params["proxy_id"], "proxy_id")
                    target_id = identifier(params["target_id"], "target_id")
                    quality = store.quality(proxy_id, target_id)
                    if quality is None:
                        raise HttpError(404, "quality_not_found")
                    return 200, {"quality": quality}
                if url.path == "/internal/v1/proxies":
                    params = query_fields(url.query, ("target_id",), ("limit", "min_score"))
                    target_id = identifier(params["target_id"], "target_id")
                    try:
                        limit = int(params.get("limit", "10"))
                        min_score = float(params.get("min_score", "0"))
                    except ValueError:
                        raise ValidationError("Invalid numeric query") from None
                    return 200, {"proxies": store.get_proxies(target_id, limit=limit, min_score=min_score)}
            raise HttpError(404, "not_found")

    return HTTPServer(("127.0.0.1", port), Handler)
