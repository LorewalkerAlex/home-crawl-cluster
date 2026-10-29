"""Loopback-only, receive-only HTTP fixture. This does not acknowledge a DB commit."""

import argparse
from http.server import HTTPServer
import json

from .contracts import parse_callback
from .http_transport import HttpError, JsonHandler


def make_receiver(port=0, on_receive=None, *, bind_host="127.0.0.1"):
    if bind_host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Unsupported bind address")
    class Handler(JsonHandler):
        def do_GET(self):
            self.respond(405, {"error": "method_not_allowed"})

        def do_POST(self):
            self.run_json(self.receive)

        def receive(self):
            if self.path != "/internal/v1/check-results":
                raise HttpError(404, "not_found")
            payload = self.read_json()
            records = parse_callback(payload)
            summary = {"batch_id": payload["batch_id"], "task_id": payload["task_id"],
                       "record_count": len(records)}
            if on_receive is not None:
                try:
                    on_receive(summary)
                except Exception:
                    raise HttpError(500, "receiver_error") from None
            return 200, {"received": True, "persisted": False, **summary}

    return HTTPServer((bind_host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses a free port")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    with make_receiver(args.port, lambda item: print(json.dumps(item), flush=True)) as server:
        print(f"Receive-only URL: http://127.0.0.1:{server.server_port}/internal/v1/check-results", flush=True)
        print("No SQLite writes or score updates. Stop with Ctrl+C.", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
