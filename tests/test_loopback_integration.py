"""Real HTTP, real proxy forwarding/CONNECT and real Worker subprocesses; loopback only."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import ssl
import subprocess
import sys
import threading
import tempfile
from integrations.crawlab.program import program_files
import time
from urllib.parse import urlsplit
import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from apps.proxypool.service.app import create_app
from apps.proxypool.service.config import Settings
from integrations.crawlab.client import encode_job
from pwdlib import PasswordHash

KEY = "loopback-only-acceptance-key"
LOGIN_PASSWORD = "loopback-only-admin-password"
LOGIN_HASH = PasswordHash.recommended().hash(LOGIN_PASSWORD)
ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextmanager
def http_server(handler, context=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if context:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@contextmanager
def api_server(tmp_path):
    config = replace(
        Settings(),
        database=str(tmp_path / "integration.sqlite"),
        port=13004,
        write_flush_seconds=0.02,
        candidate_min_score=0,
        session_https_only=False,
    )
    app = create_app(
        config,
        api_key=KEY,
        admin_password_hash=LOGIN_HASH,
        session_secret="loopback-only-session-secret-32-characters",
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("Local API did not start")
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + KEY},
            trust_env=False,
            timeout=10,
        ) as client:
            yield client, app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()


def worker(job, base_url, extra_env=None, workdir=None):
    environment = dict(
        os.environ,
        PROXYPOOL_M05_URL=base_url,
        PROXYPOOL_API_KEY=KEY,
        CRAWLAB_TASK_ID="1234567890abcdef12345678",
        PYTHONUTF8="1",
    )
    if extra_env:
        environment.update(extra_env)
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="delivered-program-") as directory:
        taskdir = Path(workdir or directory)
        for name, content in program_files().items():
            path = taskdir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(taskdir / "proxypool_task.py"),
                "--job",
                encode_job(job),
            ],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",  # Preserve diagnostic bytes from native Windows libraries.
            timeout=30,
        )


def common_job():
    return {
        "request_timeout_seconds": 1.0,
        "retry_attempts": 2,
        "retry_window_seconds": 10,
        "max_request_records": 100,
    }


def proxy_handler(mode="normal"):
    class Proxy(QuietHandler):
        def do_GET(self):
            if mode == "error":
                return self.reply(503, "not available")
            if mode == "slow":
                time.sleep(0.5)
            parsed = urlsplit(self.path)
            assert parsed.hostname == "127.0.0.1", "This fixture may only forward to loopback"
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
            try:
                connection.request("GET", parsed.path or "/")
                response = connection.getresponse()
                body = response.read()
                self.reply(response.status, body)
            finally:
                connection.close()

        def do_CONNECT(self):
            host, port = self.path.rsplit(":", 1)
            assert host == "127.0.0.1"
            upstream = socket.create_connection((host, int(port)), timeout=2)
            self.send_response(200)
            self.end_headers()
            try:
                while True:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 2)
                    if not ready:
                        break
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
            except (OSError, ConnectionResetError):
                pass
            finally:
                upstream.close()

    return Proxy


def test_actual_worker_http_proxy_results(tmp_path):
    class Target(QuietHandler):
        def do_GET(self):
            self.reply(200, "target ok")

    with (
        api_server(tmp_path) as (client, app, url),
        http_server(Target) as target,
        http_server(proxy_handler()) as good,
        http_server(proxy_handler("error")) as bad,
        http_server(proxy_handler("slow")) as slow,
    ):
        target_row = client.post(
            "/v1/targets",
            json={
                "name": "loopback",
                "test_url": f"http://127.0.0.1:{target.server_port}/?access_token=synthetic-query-secret",
                "enabled": True,
            },
        ).json()
        endpoints = [
            {"scheme": "http", "host": "127.0.0.1", "port": x.server_port}
            for x in (good, bad, slow)
        ]
        assert (
            client.post("/v1/proxies/import", json={"proxies": endpoints}).json()["inserted"] == 3
        )
        job = dict(
            common_job(),
            kind="test",
            concurrency=3,
            target=target_row,
            request_timeout_seconds=0.2,
            proxies=[
                {
                    "proxy_id": i + 1,
                    "proxy_url": f"http://127.0.0.1:{server.server_port}",
                }
                for i, server in enumerate((good, bad, slow))
            ],
        )
        options = tmp_path / "settings.json"
        options.write_text(
            json.dumps({"capture_proxy_url": "http://127.0.0.1:1", "max_concurrency": 2})
        )
        result = worker(job, url, {"PROXYPOOL_TASK_SETTINGS": str(options)})
        assert result.returncode == 0, (result.stdout, result.stderr)
        output = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        events = {row["event"]: row for row in output if "event" in row}
        assert events["test_start"]["concurrency"] == 2
        assert events["test_progress"]["completed"] == 3
        summary = events["test_summary"]
        assert (summary["tested"], summary["success_count"], summary["failure_count"]) == (3, 1, 2)
        assert summary["http_status_counts"] == {"200": 1, "503": 1}
        assert summary["error_counts"] == {"timeout": 1}
        assert sum(summary["exception_types"].values()) == 1
        assert {row["proxy_id"] for row in summary["failure_examples"]} == {2, 3}
        assert events["feedback_committed"]["accepted"] == 3
        assert events["feedback_committed"]["ignored"] == 0
        assert output[-1] == {"kind": "test", "reported": 3, "committed": True}
        assert "synthetic-query-secret" not in result.stdout and KEY not in result.stdout
        assert all(member["proxy_url"] not in result.stdout for member in job["proxies"])
        with app.state.writer.database.reader() as db:
            rows = db.execute("SELECT * FROM proxy_quality ORDER BY proxy_id").fetchall()
        assert [row["result_bits"] for row in rows] == [1, 0, 0]
        assert [row["feedback_count"] for row in rows] == [1, 1, 1]
        assert rows[0]["successful_latency_ewma_ms"] is not None
        assert rows[1]["successful_latency_ewma_ms"] is None
        chosen = client.post("/v1/proxies/acquire", json={"target_id": target_row["target_id"]})
        assert chosen.status_code == 200


def tls_files(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Loopback acceptance only")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, True, True, False, False),
            True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "local-cert.pem"
    key_file = tmp_path / "local-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


def test_https_target_via_actual_connect_proxy(tmp_path):
    cert_file, key_file = tls_files(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)

    class Target(QuietHandler):
        def do_GET(self):
            self.reply(200, "secure target ok")

    with (
        api_server(tmp_path) as (client, app, url),
        http_server(Target, context) as target,
        http_server(proxy_handler()) as proxy,
    ):
        target_row = client.post(
            "/v1/targets",
            json={
                "name": "TLS loopback",
                "test_url": f"https://127.0.0.1:{target.server_port}/",
                "enabled": True,
            },
        ).json()
        client.post(
            "/v1/proxies/import",
            json={"proxies": [{"host": "127.0.0.1", "port": proxy.server_port}]},
        )
        options = tmp_path / "worker-settings.json"
        options.write_text(json.dumps({"ca_file": str(cert_file)}))
        job = dict(
            common_job(),
            kind="test",
            concurrency=1,
            target=target_row,
            proxies=[{"proxy_id": 1, "proxy_url": f"http://127.0.0.1:{proxy.server_port}"}],
        )
        result = worker(job, url, {"PROXYPOOL_TASK_SETTINGS": str(options)})
        assert result.returncode == 0, (result.stdout, result.stderr)
        with app.state.writer.database.reader() as db:
            row = db.execute("SELECT * FROM proxy_quality").fetchone()
        assert row["result_bits"] == 1


@pytest.mark.parametrize(
    "key,expected", [("ip89", 2), ("ipscdn", 1), ("ipfreeonly", 1), ("ip3366", 0)]
)
def test_legacy_source_worker_commits_results_and_blocked_source_health(tmp_path, key, expected):
    class Source(QuietHandler):
        def do_GET(self):
            if key == "ip3366":
                return self.reply(521, "<script>challenge()</script>")
            if key == "ip89":
                return self.reply(200, "192.0.2.1:8080<br>192.0.2.2:3128")
            if key == "ipscdn":
                return self.reply(
                    200, json.dumps({"code": 200, "data": {"proxies": ["192.0.2.1:8080"]}})
                )
            country = "CN" if "page=2" in self.path else "US"
            return self.reply(
                200,
                json.dumps(
                    {
                        "items": [
                            {
                                "ip": "192.0.2.1",
                                "port": "8080",
                                "country": {"countryCode": country},
                                "protocols": ["http"],
                            }
                        ],
                        "totalItems": 2,
                    }
                ),
            )

    with api_server(tmp_path) as (client, _app, url), http_server(Source) as source_server:
        source = client.post(
            "/v1/sources",
            json={
                "name": "legacy fixture",
                "spider_key": key,
                "url": f"http://127.0.0.1:{source_server.server_port}/?page=1",
                "enabled": True,
            },
        ).json()
        job = dict(
            common_job(),
            kind="capture",
            source={x: source[x] for x in ("source_id", "spider_key", "url")},
            source_max_records=100,
            source_max_pages=10,
            source_timeout_seconds=5,
        )
        result = worker(job, url)
        assert result.returncode == (1 if key == "ip3366" else 0), (result.stdout, result.stderr)
        assert client.get("/v1/stats").json()["proxy_count"] == expected
        state = client.get("/v1/sources").json()["items"][0]
        assert state["outcome"] == ("failed" if key == "ip3366" else "success")
        assert state["inserted_count"] == expected


def test_actual_scrapy_pagination_and_source_commit(tmp_path):
    class Pages(QuietHandler):
        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/one")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/one":
                self.reply(
                    200,
                    '<table id="proxies"><tr><td>192.0.2.1</td><td>8080</td></tr></table><a class="next" href="/two">next</a>',
                )
            else:
                self.reply(
                    200,
                    '<table id="proxies"><tr><td>192.0.2.2</td><td>8081</td></tr></table>',
                )

    with api_server(tmp_path) as (client, app, url), http_server(Pages) as pages:
        source = client.post(
            "/v1/sources",
            json={
                "name": "offline pages",
                "spider_key": "example_table",
                "url": f"http://127.0.0.1:{pages.server_port}/start",
                "enabled": True,
            },
        ).json()
        job = dict(
            common_job(),
            kind="capture",
            source={x: source[x] for x in ("source_id", "spider_key", "url")},
            source_max_records=100,
            source_max_pages=10,
            source_timeout_seconds=5,
        )
        result = worker(job, url)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert client.get("/v1/stats").json()["proxy_count"] == 2
        state = client.get("/v1/sources").json()["items"][0]
        assert (
            state["outcome"] == "success" and state["output_count"] == 2 and state["pages_ok"] == 2
        )
        assert state["inserted_count"] == 2 and state["pages_failed"] == 0
        again = worker(job, url)
        assert again.returncode == 0, (again.stdout, again.stderr)
        assert client.get("/v1/stats").json()["proxy_count"] == 2


def test_capture_proxy_forwards_pagination_without_proxying_m05(tmp_path):
    calls = []
    forwarding = proxy_handler()

    class Proxy(forwarding):
        def do_GET(self):
            calls.append(self.path)
            super().do_GET()

    class Pages(QuietHandler):
        def do_GET(self):
            following = '<a class="next" href="/two">next</a>' if self.path == "/one" else ""
            port = 8080 if self.path == "/one" else 8081
            self.reply(
                200,
                f'<table id="proxies"><tr><td>192.0.2.1</td><td>{port}</td></tr></table>{following}',
            )

    with (
        api_server(tmp_path) as (client, app, url),
        http_server(Pages) as pages,
        http_server(Proxy) as proxy,
    ):
        source = client.post(
            "/v1/sources",
            json={
                "name": "proxied fixture",
                "spider_key": "example_table",
                "url": f"http://127.0.0.1:{pages.server_port}/one",
                "enabled": True,
            },
        ).json()
        options = tmp_path / "settings.json"
        options.write_text(
            json.dumps({"capture_proxy_url": f"http://127.0.0.1:{proxy.server_port}"})
        )
        job = dict(
            common_job(),
            kind="capture",
            source={x: source[x] for x in ("source_id", "spider_key", "url")},
            source_max_records=100,
            source_max_pages=10,
            source_timeout_seconds=5,
        )
        result = worker(job, url, {"PROXYPOOL_TASK_SETTINGS": str(options)})
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert calls == [source["url"], f"http://127.0.0.1:{pages.server_port}/two"]
        assert client.get("/v1/stats").json()["proxy_count"] == 2
        state = client.get("/v1/sources").json()["items"][0]
        assert state["outcome"] == "success" and state["inserted_count"] == 2
        # This proxy implements GET only: M05's authenticated POST must stay direct.
        assert "REMOTE_CONTROL" not in result.stderr


def test_result_retry_does_not_repeat_network_test(tmp_path):
    calls = {"target": 0, "callback": 0}

    class Target(QuietHandler):
        def do_GET(self):
            calls["target"] += 1
            self.reply(200, "ok")

    with (
        api_server(tmp_path) as (client, app, url),
        http_server(Target) as target,
        http_server(proxy_handler()) as proxy,
    ):
        target_row = client.post(
            "/v1/targets",
            json={
                "name": "retry test",
                "test_url": f"http://127.0.0.1:{target.server_port}/",
                "enabled": True,
            },
        ).json()
        client.post(
            "/v1/proxies/import",
            json={"proxies": [{"host": "127.0.0.1", "port": proxy.server_port}]},
        )

        class Callback(QuietHandler):
            def do_POST(self):
                calls["callback"] += 1
                body = self.rfile.read(int(self.headers["Content-Length"]))
                # Commit both attempts in real M05, but lose the first confirmation.
                response = client.post(
                    self.path,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
                self.reply(
                    503 if calls["callback"] == 1 else response.status_code,
                    response.content,
                )

        with http_server(Callback) as callback:
            job = dict(
                common_job(),
                kind="test",
                concurrency=1,
                target=target_row,
                proxies=[
                    {
                        "proxy_id": 1,
                        "proxy_url": f"http://127.0.0.1:{proxy.server_port}",
                    }
                ],
            )
            execution = worker(job, f"http://127.0.0.1:{callback.server_port}")
            assert execution.returncode == 0, (execution.stdout, execution.stderr)
        assert calls == {"target": 1, "callback": 2}
        events = [
            json.loads(line) for line in execution.stdout.splitlines() if line.startswith("{")
        ]
        retry = next(row for row in events if row.get("event") == "callback_retry")
        assert retry["http_status"] == 503 and retry["next_attempt"] == 2
        assert sum(row.get("event") == "test_summary" for row in events) == 1
        assert sum(row.get("event") == "feedback_committed" for row in events) == 1
        with app.state.writer.database.reader() as db:
            assert db.execute("SELECT feedback_count FROM proxy_quality").fetchone()[0] == 1


def test_connect_failure_logs_proxy_status_without_inventing_target_response(tmp_path):
    class DeniedProxy(QuietHandler):
        def do_CONNECT(self):
            self.reply(407, "proxy authentication required")

    with api_server(tmp_path) as (client, app, url), http_server(DeniedProxy) as proxy:
        target = client.post(
            "/v1/targets",
            json={
                "name": "blocked CONNECT",
                "test_url": "https://example.invalid/",
                "enabled": True,
            },
        ).json()
        client.post(
            "/v1/proxies/import",
            json={"proxies": [{"host": "127.0.0.1", "port": proxy.server_port}]},
        )
        job = dict(
            common_job(),
            kind="test",
            concurrency=1,
            target=target,
            proxies=[{"proxy_id": 1, "proxy_url": f"http://127.0.0.1:{proxy.server_port}"}],
        )
        result = worker(job, url)
        assert result.returncode == 0, (result.stdout, result.stderr)
        summary = next(
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith('{"event": "test_summary"')
        )
        assert summary["success_count"] == 0 and summary["failure_count"] == 1
        assert summary["http_status_counts"] == {}
        assert summary["proxy_http_status_counts"] == {"407": 1}
        assert summary["exception_types"] == {"ClientHttpProxyError": 1}
        with app.state.writer.database.reader() as db:
            row = db.execute("SELECT * FROM proxy_quality").fetchone()
        assert row["sample_count"] == 1 and row["result_bits"] == 0


def test_callback_failure_does_not_log_a_successful_commit(tmp_path):
    calls = {"target": 0, "callback": 0}

    class Target(QuietHandler):
        def do_GET(self):
            calls["target"] += 1
            self.reply(200, "ok")

    class Callback(QuietHandler):
        def do_POST(self):
            calls["callback"] += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.reply(503, "unavailable")

    with (
        http_server(Target) as target,
        http_server(proxy_handler()) as proxy,
        http_server(Callback) as callback,
    ):
        job = dict(
            common_job(),
            kind="test",
            concurrency=1,
            target={
                "target_id": 1,
                "revision": 1,
                "test_url": f"http://127.0.0.1:{target.server_port}/",
            },
            proxies=[{"proxy_id": 1, "proxy_url": f"http://127.0.0.1:{proxy.server_port}"}],
        )
        result = worker(job, f"http://127.0.0.1:{callback.server_port}")
        assert result.returncode == 1
        assert calls == {"target": 1, "callback": 2}
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        assert any(
            row.get("event") == "test_summary" and row["success_count"] == 1 for row in events
        )
        assert any(row.get("event") == "callback_failed" for row in events)
        assert not any(row.get("event") == "feedback_committed" for row in events)
        assert events[-1]["committed"] is False


def test_failed_source_updates_health_without_proxy_failure_samples(tmp_path):
    class FailedPage(QuietHandler):
        def do_GET(self):
            self.reply(503, "source unavailable")

    with api_server(tmp_path) as (client, app, url), http_server(FailedPage) as page:
        source = client.post(
            "/v1/sources",
            json={
                "name": "failed fixture",
                "spider_key": "proxyscrape",
                "url": f"http://127.0.0.1:{page.server_port}/",
                "enabled": True,
            },
        ).json()
        job = dict(
            common_job(),
            kind="capture",
            source={x: source[x] for x in ("source_id", "spider_key", "url")},
            source_max_records=100,
            source_max_pages=10,
            source_timeout_seconds=5,
        )
        execution = worker(job, url)
        assert execution.returncode == 1
        diagnostics = [
            json.loads(line)["capture_diagnostics"]
            for line in execution.stdout.splitlines()
            if line.startswith('{"capture_diagnostics":')
        ][0]
        assert diagnostics["http_status_counts"].get("503", 0) >= 1
        assert diagnostics["captured_count"] == 0
        assert "503" in execution.stderr, execution.stderr
        assert "install_root_handler parameter is deprecated" not in execution.stderr
        state = client.get("/v1/sources").json()["items"][0]
        assert state["outcome"] == "failed" and state["output_count"] == 0
        assert client.get("/v1/stats").json()["proxy_count"] == 0


def test_registered_workspace_script_runs_without_image_rebuild(tmp_path):
    class Text(QuietHandler):
        def do_GET(self):
            self.reply(200, "192.0.2.1:8080\n192.0.2.2:8081\n")

    taskdir = tmp_path / "task-workspace"
    taskdir.mkdir()
    (taskdir / "sources").mkdir()
    (taskdir / "sources/new_reviewed_site.py").write_bytes(
        (ROOT / "plugins/source_template.py").read_bytes()
    )
    with api_server(tmp_path) as (client, app, url), http_server(Text) as page:
        source = client.post(
            "/v1/sources",
            json={
                "name": "workspace source",
                "spider_key": "proxyscrape",
                "url": f"http://127.0.0.1:{page.server_port}/",
                "enabled": True,
            },
        ).json()
        job = dict(
            common_job(),
            kind="capture",
            workspace_spider=True,
            source={
                "source_id": source["source_id"],
                "spider_key": "new_reviewed_site",
                "url": source["url"],
            },
            source_max_records=100,
            source_max_pages=10,
            source_timeout_seconds=5,
        )
        execution = worker(job, url, workdir=taskdir)
        assert execution.returncode == 0, (execution.stdout, execution.stderr)
        assert client.get("/v1/stats").json()["proxy_count"] == 2
