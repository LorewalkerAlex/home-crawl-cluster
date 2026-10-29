from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("server_poc", ROOT / "examples/m05-server-poc/server_poc.py")
poc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poc)
build_spec = importlib.util.spec_from_file_location("build_poc", ROOT / "examples/m05-server-poc/build_bundle.py")
builder = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(builder)


class PocPreparationTests(unittest.TestCase):
    def test_callback_failure_still_cleans_the_exact_upload_and_container(self):
        with tempfile.TemporaryDirectory(prefix="poc-finally-") as directory:
            task = Path(directory) / "task"
            task.mkdir()
            source = task / "m05_poc.pyz"
            source.write_bytes(b"synthetic reviewed bundle")
            output = io.StringIO()
            with patch.object(poc, "UPLOAD", source), patch.object(poc.sys, "argv", [str(source)]), \
                    patch.object(poc.os, "geteuid", return_value=0, create=True), \
                    patch.object(poc, "docker", return_value="server-version"), \
                    patch.object(poc, "run_trial", side_effect=poc.PocError("synthetic failure")), \
                    patch.object(poc, "cleanup_sender") as sender, \
                    patch.object(poc, "cleanup_container") as container, redirect_stdout(output):
                self.assertEqual(poc.host(), 1)
            sender.assert_called_once()
            container.assert_called_once()
            self.assertFalse(task.exists())
            lines = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([(line["phase"], line["check"]) for line in lines],
                             [("callback", "FAIL"), ("cleanup", "PASS")])

    def test_cleanup_failure_is_visible_and_preserves_the_recovery_bundle(self):
        with tempfile.TemporaryDirectory(prefix="poc-fail-") as directory:
            source = Path(directory) / "m05_poc.pyz"
            source.write_bytes(b"synthetic reviewed bundle")
            output = io.StringIO()
            with patch.object(poc, "UPLOAD", source), patch.object(poc.sys, "argv", [str(source)]), \
                    patch.object(poc.os, "geteuid", return_value=0, create=True), \
                    patch.object(poc, "docker", return_value="server-version"), \
                    patch.object(poc, "cleanup_sender"), \
                    patch.object(poc, "cleanup_container", side_effect=poc.PocError("synthetic Docker failure")), \
                    redirect_stdout(output):
                self.assertEqual(poc.host(cleanup_only=True), 1)
            self.assertTrue(source.exists())
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["check"], "FAIL")

    def test_optional_inspection_does_not_hide_daemon_or_permission_failures(self):
        missing = SimpleNamespace(returncode=1, stdout="", stderr="Error: No such container: this-poc")
        denied = SimpleNamespace(returncode=1, stdout="", stderr="permission denied")
        with patch.object(poc.subprocess, "run", return_value=missing):
            self.assertIsNone(poc.docker(["inspect"], optional=True))
        with patch.object(poc.subprocess, "run", return_value=denied), self.assertRaises(poc.PocError):
            poc.docker(["inspect"], optional=True)

    def test_cleanup_never_removes_a_foreign_container(self):
        calls = []
        def fake(args, **_kwargs):
            calls.append(args)
            return "container-id" if args[3] == "{{.Id}}" else json.dumps({poc.OWNER_LABEL: "other"})
        with patch.object(poc, "docker", side_effect=fake), self.assertRaises(poc.PocError):
            poc.cleanup_container("test-sha")
        self.assertFalse(any("rm" in args for args in calls))

    def test_cleanup_removes_only_the_verified_id_and_checks_absence(self):
        calls = []
        responses = iter(["verified-id", json.dumps({poc.OWNER_LABEL: poc.TASK, poc.HASH_LABEL: "test-sha"}),
                          "verified-id", None])
        def fake(args, **_kwargs):
            calls.append(args)
            return next(responses)
        with patch.object(poc, "docker", side_effect=fake):
            poc.cleanup_container("test-sha")
        self.assertEqual(calls[2], ["container", "rm", "--force", "--volumes", "verified-id"])
        self.assertEqual(calls[3][-1], "verified-id")

    def test_missing_container_is_not_an_error(self):
        with patch.object(poc, "inspect", return_value=None), patch.object(poc, "docker") as command:
            poc.cleanup_container("unused")
        command.assert_not_called()

    def test_volume_overrides_are_tmpfs_and_refuse_sensitive_or_invalid_paths(self):
        flags = poc.temporary_mounts({"/data": {}, "/root/.crawlab": {}})
        self.assertIn("/data:rw,nosuid,nodev,noexec,size=4m,mode=1777", flags)
        self.assertIn("/tmp:rw,nosuid,nodev,noexec,size=4m,mode=1777", flags)
        for path in ["/", "/usr/local", "/opt/conda", "/poc", "/data:bad", "relative"]:
            with self.subTest(path=path), self.assertRaises(poc.PocError):
                poc.temporary_mounts({path: {}})

    def test_file_cleanup_is_exact_and_never_recursive(self):
        with tempfile.TemporaryDirectory(prefix="poc-clean-") as directory:
            task = Path(directory) / "task"
            task.mkdir()
            source = task / "m05_poc.pyz"
            source.write_bytes(b"synthetic")
            extra = task / "unrelated.txt"
            extra.write_text("keep", encoding="utf-8")
            with patch.object(poc, "UPLOAD", source), self.assertRaises(OSError):
                poc.cleanup_upload(source)
            self.assertFalse(source.exists())
            self.assertTrue(extra.exists())
            extra.unlink()
            with patch.object(poc, "UPLOAD", source):
                poc.cleanup_upload(source)
            self.assertFalse(task.exists())

    def test_file_cleanup_refuses_another_path(self):
        with self.assertRaises(poc.PocError):
            poc.cleanup_upload(Path("not-this-task.pyz"))

    def test_bundle_uses_actual_receiver_sources_and_has_no_database_or_secrets(self):
        with tempfile.TemporaryDirectory(prefix="poc-build-") as directory:
            output = Path(directory) / "m05_poc.pyz"
            digest = builder.build(ROOT, output)
            self.assertEqual(len(digest), 64)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(len(archive.namelist()), 7)
                self.assertEqual(archive.read("apps/proxypool/service/receiver.py"),
                                 (ROOT / "apps/proxypool/service/receiver.py").read_bytes())
                self.assertFalse(any("storage" in name or "secret" in name for name in archive.namelist()))
            with self.assertRaises(FileExistsError):
                builder.build(ROOT, output)


if __name__ == "__main__":
    unittest.main()
