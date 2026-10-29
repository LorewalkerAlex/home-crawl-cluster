"""Source-derived contract checks; not recorded deployment responses."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/m05-native-task"
sys.path.insert(0, str(EXAMPLE))
spec = importlib.util.spec_from_file_location("contract_inspection", EXAMPLE / "inspect_platform.py")
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)


def node(**fields):
    # Names/types from models/models/node.go; enum from constants/node.go.
    result = {"_id": "a" * 24, "name": "home-worker-01", "is_master": False,
              "enabled": True, "status": "on", "available_runners": 1, "max_runners": 1}
    result.update(fields)
    return result


class Api:
    def __init__(self, nodes=None, total=None):
        self.nodes = nodes if nodes is not None else [node()]
        self.total = len(self.nodes) if total is None else total
        self.calls = []
        self.token = None
        self.token_observed = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path))
        self.token_observed.append(self.token)
        if (method, path) == ("POST", "/login"):
            return {"data": "session-only-test-token"}
        if (method, path) == ("GET", "/version"):
            return {"data": "v0.6.2"}
        if (method, path) == ("GET", "/nodes?page=1&size=100"):
            return {"data": self.nodes, "total": self.total}
        raise AssertionError("Unexpected mutation or unsupported request")


class CrawlabInspectionTests(unittest.TestCase):
    def test_https_requires_explicit_selection_and_remote_plaintext_is_rejected(self):
        with self.assertRaises(inspection.TaskError):
            inspection.JsonClient("https://example.invalid/api")
        for url in ("http://example.invalid/api", "https://user:secret@example.invalid/api"):
            with self.subTest(url=url), self.assertRaises(inspection.TaskError):
                inspection.JsonClient(url, allow_https=True)

    def test_https_uses_verified_context_and_sends_raw_token_only_to_selected_origin(self):
        response = Mock(status=200)
        response.read.return_value = b'{"data":"v0.6.2"}'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("native_task.http.client.HTTPSConnection", return_value=connection) as constructor:
            api = inspection.JsonClient("https://example.invalid:4129/api", token="test-only-token", allow_https=True)
            self.assertEqual(api.request("GET", "/version")["data"], "v0.6.2")
        args, kwargs = constructor.call_args
        self.assertEqual(args, ("example.invalid", 4129))
        self.assertTrue(kwargs["context"].check_hostname)
        import ssl
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"], "test-only-token")
        connection.close.assert_called_once()

    def test_https_redirect_does_not_forward_credentials_or_retry(self):
        response = Mock(status=302)
        response.read.return_value = b'{"location":"https://other.invalid"}'
        connection = Mock()
        connection.getresponse.return_value = response
        with patch("native_task.http.client.HTTPSConnection", return_value=connection) as constructor:
            api = inspection.JsonClient("https://example.invalid/api", token="test-only-token", allow_https=True)
            with self.assertRaises(inspection.TaskError) as failure:
                api.request("GET", "/nodes?page=1&size=100")
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(connection.request.call_count, 1)
        self.assertNotIn("test-only-token", str(failure.exception))
        self.assertNotIn("other.invalid", str(failure.exception))

    def test_cli_checks_connection_before_asking_for_credentials(self):
        api = Mock()
        api.request.side_effect = inspection.TaskError("HTTPS unavailable")
        output = io.StringIO()
        with patch.object(inspection.sys.stdin, "isatty", return_value=True), \
                patch.object(inspection, "JsonClient", return_value=api), \
                patch.object(inspection, "session") as session, redirect_stdout(output):
            self.assertEqual(inspection.main(["--api-base-url", "https://example.invalid/api"]), 1)
        api.request.assert_called_once_with("GET", "/version")
        session.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["phase"], "connection")

    def test_pinned_api_version_is_observed_without_false_rejection_of_the_deployment(self):
        api = Api()
        report = inspection.inspect_platform(api)
        self.assertEqual(report["api_version_observed"], "v0.6.2")
        self.assertFalse(report["version_used_as_gate"])
        self.assertEqual(report["readiness"], "ready")
        self.assertEqual(api.calls, [("GET", "/version"), ("GET", "/nodes?page=1&size=100")])

    def test_dynamic_busy_state_is_distinct_from_protocol_incompatibility(self):
        api = Api([node(available_runners=0)])
        report = inspection.inspect_nodes(api)
        self.assertEqual(report["readiness"], "busy")
        self.assertEqual(report["selected_node"], "a" * 24)
        api.nodes[0]["available_runners"] = 1
        self.assertEqual(inspection.inspect_nodes(api)["readiness"], "ready")
        self.assertTrue(all(method == "GET" for method, _ in api.calls))

    def test_offline_or_disabled_workers_report_observed_values(self):
        for change in ({"status": "off"}, {"enabled": False}):
            with self.subTest(change=change):
                report = inspection.inspect_nodes(Api([node(**change)]))
                self.assertEqual(report["readiness"], "unavailable")
                self.assertEqual(report["reason"], "worker_disabled_or_not_online")
                for key, value in change.items():
                    self.assertEqual(report["nodes"][0][key], value)

    def test_ui_label_is_reported_as_unknown_contract_value(self):
        report = inspection.inspect_nodes(Api([node(status="online")]))
        self.assertEqual(report["readiness"], "incompatible")
        self.assertEqual(report["nodes"][0]["status"], "online")

    def test_safe_snapshot_excludes_keys_addresses_and_unrequested_metadata(self):
        report = inspection.inspect_nodes(Api([node(key="private-node-key", ip="private-address",
                                                     token="private-token", config={"password": "private-password"})]))
        text = json.dumps(report)
        for private in ("private-node-key", "private-address", "private-token", "private-password"):
            self.assertNotIn(private, text)
        self.assertEqual(set(report["nodes"][0]), set(inspection.SAFE_FIELDS))

    def test_incomplete_duplicate_and_invalid_capacity_are_not_silently_selected(self):
        for api, state in ((Api(total=101), "incompatible"),
                           (Api([node(), node(_id="b" * 24)]), "unavailable"),
                           (Api([node(available_runners=True)]), "incompatible"),
                           (Api([node(available_runners=2)]), "incompatible")):
            with self.subTest(state=state):
                self.assertEqual(inspection.inspect_nodes(api)["readiness"], state)

    def test_session_reuses_one_login_and_never_accepts_a_run_command(self):
        api = Api()
        answers = iter(("admin", "run", "check", "quit"))
        passwords = []
        output = []
        def password(_prompt):
            passwords.append(True)
            return "hidden-test-password"
        inspection.session(api, read=lambda prompt: next(answers), read_password=password, output=output.append)
        self.assertEqual(passwords, [True])
        self.assertEqual(sum(method == "POST" for method, _ in api.calls), 1)
        self.assertEqual(sum(path == "/nodes?page=1&size=100" for _, path in api.calls), 2)
        self.assertTrue(all(value == "session-only-test-token" for value in api.token_observed[1:]))
        self.assertIsNone(api.token)
        self.assertNotIn("hidden-test-password", "".join(output))
        self.assertNotIn("session-only-test-token", "".join(output))

    def test_session_clears_token_when_input_is_interrupted(self):
        api = Api()
        answers = iter(("admin",))
        def read(_prompt):
            try:
                return next(answers)
            except StopIteration:
                raise KeyboardInterrupt()
        inspection.session(api, read=read, read_password=lambda prompt: "hidden", output=lambda text: None)
        self.assertIsNone(api.token)


if __name__ == "__main__":
    unittest.main()
