from copy import deepcopy
from dataclasses import replace
import http.client
import json
import threading
import unittest

from apps.proxypool.service.contracts import MAX_BODY_BYTES, MAX_RECORDS, Result, ValidationError, parse_callback
from apps.proxypool.service.receiver import make_receiver


def synthetic_callback():
    records = [
        Result("check-one", "proxy-one", "site-demo", 1, 1_793_052_000_000, "response", 200, 80),
        Result("check-two", "proxy-two", "site-demo", 1, 1_793_052_000_000, "response", 503, 120),
        Result("check-three", "proxy-three", "site-demo", 1, 1_793_052_000_000,
               "proxy_error", error_code="connect_timeout"),
    ]
    return {"schema_version": 1, "batch_id": "batch-demo", "task_id": "task-demo",
            "records": [record.to_wire() for record in records]}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.server = make_receiver(on_receive=self.received.append)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def post(self, payload=None, *, raw=None, path="/internal/v1/check-results", content_type="application/json"):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request("POST", path, body, {"Content-Type": content_type})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_real_loopback_post_only_acknowledges_reception(self):
        status, body = self.post(synthetic_callback())
        self.assertEqual(status, 200)
        self.assertTrue(body["received"])
        self.assertFalse(body["persisted"])
        self.assertNotIn("committed", body)
        self.assertEqual(body["record_count"], 3)
        self.assertEqual(self.received, [{"batch_id": "batch-demo", "task_id": "task-demo", "record_count": 3}])

    def test_malformed_packages_are_rejected_before_receipt(self):
        cases = []
        invalid = synthetic_callback()
        invalid["schema_version"] = True
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"][1]["http_status"] = True
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"][1]["check_id"] = "check-one"
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"][0]["observed_at"] = "2026-10-27 10:00:00"
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"][0]["score"] = 100
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"] = []
        cases.append(invalid)
        invalid = synthetic_callback()
        invalid["records"] = [deepcopy(invalid["records"][0]) for _ in range(MAX_RECORDS+1)]
        cases.append(invalid)
        for payload in cases:
            with self.subTest(payload=payload):
                status, body = self.post(payload)
                self.assertEqual(status, 422)
                self.assertEqual(body, {"error": "invalid_callback"})
        self.assertEqual(self.received, [])

    def test_invalid_json_content_type_path_and_size(self):
        self.assertEqual(self.post(raw=b'{broken')[0], 422)
        self.assertEqual(self.post(raw=b'\xff')[0], 422)
        self.assertEqual(self.post(synthetic_callback(), content_type="text/plain")[0], 415)
        self.assertEqual(self.post(synthetic_callback(), path="/wrong")[0], 404)
        self.assertEqual(self.received, [])

    def test_unbounded_or_ambiguous_content_lengths_are_rejected(self):
        # Oversized uploads are rejected from headers, before sending a body.
        # Sending an unwanted body while the server closes can race into a TCP reset.
        for headers, expected in [([], 411), ([("Content-Length", "-1")], 411),
                                  ([("Content-Length", str(MAX_BODY_BYTES+1))], 413),
                                  ([("Content-Length", "9"*5000)], 413),
                                  ([("Content-Length", "2"), ("Content-Length", "2")], 411),
                                  ([("Transfer-Encoding", "chunked")], 400)]:
            with self.subTest(headers=headers):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
                try:
                    connection.putrequest("POST", "/internal/v1/check-results")
                    connection.putheader("Content-Type", "application/json")
                    for key, value in headers:
                        connection.putheader(key, value)
                    connection.endheaders()
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                finally:
                    connection.close()
        self.assertEqual(self.received, [])

    def test_contract_roundtrip_and_diagnostic_outcomes(self):
        parsed = parse_callback(synthetic_callback())
        self.assertEqual([record.success for record in parsed], [True, False, False])
        self.assertEqual(parsed[0].to_wire(), synthetic_callback()["records"][0])
        with self.assertRaises(ValidationError):
            Result("one", "proxy", "target", 1, 0, "proxy_error", error_code="full-secret-trace")

    def test_millisecond_timestamps_roundtrip_without_float_rounding(self):
        payload = synthetic_callback()
        original = parse_callback(payload)[0]
        for offset in (1, 2, 10, 337, 999):
            with self.subTest(offset=offset):
                record = replace(original, observed_at_ms=original.observed_at_ms+offset)
                payload["records"] = [record.to_wire()]
                self.assertEqual(parse_callback(payload)[0], record)


if __name__ == "__main__":
    unittest.main()
