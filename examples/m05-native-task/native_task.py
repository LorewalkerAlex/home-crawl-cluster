"""Local preparation for pinned Crawlab 0.6.3; not a deployment entry point.

Loopback HTTP is the default; an explicit option allows verified HTTPS for the
read-only PC inspector. No proxy environment, redirect or TLS bypass is used.
"""

import http.client
import json
import re
import ssl
from urllib.parse import urlsplit


class TaskError(RuntimeError):
    pass


class PublishUncertain(TaskError):
    """The run request may have created a native task. Never blindly resubmit."""


def object_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{24}", value):
        raise TaskError("Expected a 24-character lowercase Crawlab object ID")
    return value


def batch_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise TaskError("Expected a locally generated batch ID")
    return value


class JsonClient:
    """Small bounded lab transport. No proxy environment, redirect or retry."""

    def __init__(self, base_url, *, token=None, timeout=3, allow_https=False):
        url = urlsplit(base_url)
        local = url.scheme == "http" and url.hostname == "127.0.0.1"
        secure = allow_https and url.scheme == "https" and bool(url.hostname)
        if (not (local or secure) or
                url.username is not None or url.password is not None or
                url.query or url.fragment or url.path not in ("", "/api")):
            raise TaskError("Use loopback HTTP or explicitly enabled verified HTTPS")
        if not 0 < timeout <= 15:
            raise TaskError("Invalid HTTP timeout")
        self.secure = bool(secure)
        self.host = url.hostname
        try:
            self.port = url.port if url.port is not None else (443 if self.secure else 80)
        except ValueError:
            raise TaskError("Invalid API port") from None
        if not 1 <= self.port <= 65535:
            raise TaskError("Invalid API port")
        self.prefix = url.path
        self.token = token
        self.timeout = timeout

    def request(self, method, path, payload=None):
        if not path.startswith("/") or path.startswith("//"):
            raise TaskError("Invalid relative API path")
        # Keep the route for diagnostics, while omitting query values and bodies.
        context = method + " " + self.prefix + urlsplit(path).path
        headers = {"Accept": "application/json"}
        if self.token is not None:
            # Pinned 0.6.3 expects a raw token, without a Bearer prefix.
            headers["Authorization"] = self.token
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
            if len(body) > 65536:
                raise TaskError("Request exceeds the 64 KiB lab limit")
        if self.secure:
            connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout,
                                                    context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request(method, self.prefix + path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if response.status != 200:
                raise TaskError("API {} returned HTTP {}; body omitted".format(context, response.status))
            if len(raw) > 65536:
                raise TaskError("API response exceeds the 64 KiB lab limit")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("error") or result.get("message") == "error":
                raise TaskError("API {} returned an error; body omitted".format(context))
            return result
        except (OSError, http.client.HTTPException, ValueError):
            raise TaskError("HTTP exchange failed for {}; request and response bodies omitted".format(context)) from None
        finally:
            connection.close()


class Crawlab:
    def __init__(self, client):
        self.client = client

    def run_batch(self, spider_id, node_id, batch_id, m05_base_url):
        # Validate everything before the one mutating API request.
        object_id(spider_id)
        object_id(node_id)
        batch_identifier(batch_id)
        JsonClient(m05_base_url)
        if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", m05_base_url):
            raise TaskError("Expected an explicit loopback M05 port")
        payload = {"spider_id": spider_id, "mode": "selected-nodes", "node_ids": [node_id],
                   "cmd": "python -B -u worker.py", "param":
                   "--batch-id {} --m05-base-url {}".format(batch_id, m05_base_url)}
        try:
            result = self.client.request("POST", "/tasks/run", payload)
            ids = result.get("data")
            if not isinstance(ids, list) or len(ids) != 1:
                raise TaskError("Expected exactly one native task ID")
            return object_id(ids[0])
        except TaskError:
            # Even a 500 or malformed response cannot prove no task was enqueued.
            raise PublishUncertain("Native publication uncertain; keep the batch pending and inspect Crawlab") from None


def publish_reserved_batch(crawlab, m05, *, spider_id, node_id, batch_id, m05_base_url):
    """Publish a reserved batch once, with one publisher responsible for it.

    If binding fails after publication, report the known ID for reconciliation.
    The Worker waits briefly for this binding; it never binds its own identity.
    This precheck is not an atomic lock between multiple publishing processes.
    """
    batch_identifier(batch_id)
    path = "/internal/v1/check-batches/" + batch_id
    batch = m05.request("GET", path)["batch"]
    if batch.get("batch_id") != batch_id or not batch.get("members"):
        raise TaskError("Expected a nonempty reserved batch")
    if batch.get("task_id") is not None:
        raise TaskError("Batch is already bound; do not publish it again")
    task_id = crawlab.run_batch(spider_id, node_id, batch_id, m05_base_url)
    try:
        ack = m05.request("POST", path + "/task", {"task_id": task_id})
        if (ack.get("committed") is not True or ack.get("task_id") != task_id or
                ack.get("batch_id") != batch_id):
            raise TaskError("Binding acknowledgement mismatch")
    except (TaskError, KeyError, TypeError):
        raise TaskError("Native task {} was published; binding uncertain, do not republish".format(task_id)) from None
    return task_id
