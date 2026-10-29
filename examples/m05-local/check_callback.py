"""Send three synthetic results to a temporary loopback-only receiver, then stop."""

import json
from pathlib import Path
import sys
import threading
import urllib.request

# Allow running this repo example without installing packages or dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.proxypool.service.contracts import Result
from apps.proxypool.service.receiver import make_receiver


def main():
    records = [
        Result("check-one", "proxy-one", "site-demo", 1, 1_793_052_000_000, "response", 200, 80),
        Result("check-two", "proxy-two", "site-demo", 1, 1_793_052_000_000, "response", 503, 120),
        Result("check-three", "proxy-three", "site-demo", 1, 1_793_052_000_000,
               "proxy_error", error_code="connect_timeout"),
    ]
    payload = {"schema_version": 1, "batch_id": "batch-demo", "task_id": "task-demo",
               "records": [record.to_wire() for record in records]}
    received = []
    with make_receiver(on_receive=received.append) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/internal/v1/check-results"
            request = urllib.request.Request(url, json.dumps(payload).encode("utf-8"),
                                             {"Content-Type": "application/json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=3) as response:
                body = json.loads(response.read())
                status = response.status
            if status != 200 or body.get("persisted") is not False or body.get("record_count") != 3 or len(received) != 1:
                raise RuntimeError("Unexpected receive-only acknowledgement")
            print(json.dumps({"check": "PASS", "transport": "local_HTTP", "sent": 3,
                              "received": body["record_count"], "persisted": body["persisted"],
                              "crawlab_used": False}))
        finally:
            server.shutdown()
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("Local receiver did not stop")


if __name__ == "__main__":
    main()
