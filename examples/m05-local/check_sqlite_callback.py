"""Local HTTP -> SQLite -> readback demo using disposable synthetic data."""

import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.proxypool.service.contracts import Result
from apps.proxypool.service.http_api import make_sqlite_api
from apps.proxypool.service.quality import wilson_preview
from apps.proxypool.service.storage import LocalStore


def main():
    now_ms = int(time.time() * 1000)
    summary = None
    with tempfile.TemporaryDirectory(prefix="m05-http-demo-") as directory:
        with LocalStore(Path(directory) / "synthetic.sqlite3", score_fn=wilson_preview) as store:
            store.register_target("site-demo")
            for index in range(3):
                store.register_proxy(f"proxy-{index}", f"192.0.2.{index+1}", 8080)
            with make_sqlite_api(store, clock=lambda: now_ms) as server:
                thread = threading.Thread(target=server.serve_forever,
                                          kwargs={"poll_interval": 0.01}, daemon=True)
                thread.start()
                def request(method, path, payload=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        body = json.dumps(payload).encode("utf-8") if payload is not None else None
                        connection.request(method, path, body, {"Content-Type": "application/json"})
                        response = connection.getresponse()
                        result = json.loads(response.read())
                        if response.status != 200:
                            raise RuntimeError("Local integration request failed")
                        return result
                    finally:
                        connection.close()
                try:
                    batch = request("POST", "/internal/v1/check-batches",
                                    {"request_id": "demo-request", "target_id": "site-demo", "limit": 3})["batch"]
                    request("POST", f"/internal/v1/check-batches/{batch['batch_id']}/task", {"task_id": "synthetic-task"})
                    records = [Result(member["check_id"], member["proxy_id"], "site-demo", 1,
                                      now_ms, "response", status, 80).to_wire()
                               for member, status in zip(batch["members"], (200, 503, 200))]
                    payload = {"schema_version": 1, "batch_id": batch["batch_id"],
                               "task_id": "synthetic-task", "records": records}
                    ack = request("POST", "/internal/v1/check-results", payload)
                    replay = request("POST", "/internal/v1/check-results", payload)
                    quality = request("GET", "/internal/v1/quality?proxy_id=proxy-0&target_id=site-demo")["quality"]
                    if (ack.get("committed") is not True or ack.get("persisted") is not True or
                            ack.get("accepted") != 3 or replay.get("duplicates") != 3 or
                            quality["test_status"] != "available" or quality["sample_count"] != 1):
                        raise RuntimeError("Unexpected committed state or replay result")
                    summary = {"check": "PASS", "transport": "local_HTTP_to_SQLite", "sent": 3,
                               "persisted": True, "committed": True, "duplicate_replay": 3,
                               "score_policy": "explicit_Wilson_preview", "crawlab_used": False}
                finally:
                    server.shutdown()
                    thread.join(timeout=2)
                    if thread.is_alive():
                        raise RuntimeError("Local HTTP server did not stop")
    print(json.dumps({**summary, "temporary_resources_removed": True}))


if __name__ == "__main__":
    main()
