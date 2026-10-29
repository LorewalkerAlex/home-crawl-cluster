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
import time
from urllib.parse import urlsplit
import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from apps.proxypool.service.app import create_app
from apps.proxypool.service.config import Settings
from integrations.crawlab.client import encode_job

KEY = "loopback-only-acceptance-key"
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
    )
    app = create_app(config, api_key=KEY)
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
        PYTHONPATH=str(ROOT),
    )
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        [sys.executable, "-B", "-m", "apps.proxypool.worker", "--job", encode_job(job)],
        cwd=workdir or ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
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
                "test_url": f"http://127.0.0.1:{target.server_port}/",
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
                {"proxy_id": i + 1, "proxy_url": f"http://127.0.0.1:{server.server_port}"}
                for i, server in enumerate((good, bad, slow))
            ],
        )
        result = worker(job, url)
        assert result.returncode == 0, (result.stdout, result.stderr)
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
            x509.KeyUsage(True, False, True, False, False, True, True, False, False), True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
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
        result = worker(job, url, {"PROXYPOOL_WORKER_SETTINGS": str(options)})
        assert result.returncode == 0, (result.stdout, result.stderr)
        with app.state.writer.database.reader() as db:
            row = db.execute("SELECT * FROM proxy_quality").fetchone()
        assert row["result_bits"] == 1


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
                    200, '<table id="proxies"><tr><td>192.0.2.2</td><td>8081</td></tr></table>'
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
                    self.path, content=body, headers={"Content-Type": "application/json"}
                )
                self.reply(
                    503 if calls["callback"] == 1 else response.status_code, response.content
                )

        with http_server(Callback) as callback:
            job = dict(
                common_job(),
                kind="test",
                concurrency=1,
                target=target_row,
                proxies=[{"proxy_id": 1, "proxy_url": f"http://127.0.0.1:{proxy.server_port}"}],
            )
            execution = worker(job, f"http://127.0.0.1:{callback.server_port}")
            assert execution.returncode == 0, (execution.stdout, execution.stderr)
        assert calls == {"target": 1, "callback": 2}
        with app.state.writer.database.reader() as db:
            assert db.execute("SELECT feedback_count FROM proxy_quality").fetchone()[0] == 1


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
        state = client.get("/v1/sources").json()["items"][0]
        assert state["outcome"] == "failed" and state["output_count"] == 0
        assert client.get("/v1/stats").json()["proxy_count"] == 0


def test_registered_workspace_script_runs_without_image_rebuild(tmp_path):
    class Text(QuietHandler):
        def do_GET(self):
            self.reply(200, "192.0.2.1:8080\n192.0.2.2:8081\n")

    taskdir = tmp_path / "task-workspace"
    taskdir.mkdir()
    (taskdir / "source_spider.py").write_bytes((ROOT / "plugins/source_template.py").read_bytes())
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
