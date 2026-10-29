"""Deployment files and native requests checked without Docker or a live Master."""

import asyncio
import ast
from pathlib import Path
import json
import httpx
import pytest
from tools.prepare_proxypool import prepare
from tools.register_crawlab import register
from integrations.crawlab.client import CrawlabClient

ROOT = Path(__file__).resolve().parents[1]


def test_prepare_is_local_idempotent_and_keeps_secrets_out_of_output(tmp_path):
    preview = prepare(tmp_path)
    assert preview["prepared"] is False and not (tmp_path / "srv").exists()
    first = prepare(tmp_path, apply=True)
    private = tmp_path / "srv/persistent/secrets/proxypool"
    app_key = (private / "app.env").read_text().splitlines()[0]
    worker_key = (private / "worker.env").read_text().splitlines()[0]
    assert app_key == worker_key
    assert app_key.split("=", 1)[1] not in json.dumps(first)
    again = prepare(tmp_path, apply=True)
    assert again["created"] == []
    assert (private / "app.env").read_text().splitlines()[0] == app_key
    config = json.loads((tmp_path / "srv/apps/proxypool/config.json").read_text())
    assert config["publishing_enabled"] is False and config["window_size"] == 63


def test_crawlab_native_run_uses_one_worker_and_never_retries_ambiguous_response():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": ["a" * 24]})

    async def exercise():
        client = CrawlabClient(
            "http://127.0.0.1/api",
            "synthetic-native-token",
            ["1" * 24, "2" * 24],
            transport=httpx.MockTransport(respond),
        )
        try:
            for _ in range(2):
                await client.run_job("3" * 24, {"kind": "test"})
        finally:
            await client.close()

    asyncio.run(exercise())
    assert [json.loads(x.content)["node_ids"] for x in calls] == [["1" * 24], ["2" * 24]]
    assert calls[0].url.path == "/api/tasks/run"
    assert calls[0].headers["Authorization"] == "synthetic-native-token"
    assert json.loads(calls[0].content)["param"].startswith("--job ")
    assert all("token" not in x.content.decode() for x in calls)


def test_program_registration_reuses_ids_and_verifies_bootstrap():
    programs = []
    files = {}
    mutations = []

    def respond(request):
        path = request.url.path
        if request.method == "GET" and path == "/api/spiders":
            data = programs
        elif request.method == "POST" and path == "/api/spiders":
            value = json.loads(request.content)
            value["_id"] = str(len(programs) + 1) * 24
            programs.append(value)
            mutations.append(path)
            data = value
        elif path.endswith("/files/save"):
            files[path.split("/")[3]] = json.loads(request.content)["data"]
            data = None
        elif path.endswith("/files/get"):
            data = files[path.split("/")[3]]
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"data": data})

    transport = httpx.MockTransport(respond)
    first = register("http://127.0.0.1/api", "synthetic-token", transport=transport)
    second = register("http://127.0.0.1/api", "synthetic-token", transport=transport)
    assert first == second and len(mutations) == 2
    assert len(files) == 2 and all("os.execv" in x for x in files.values())


def test_worker_source_syntax_compatible_with_python310():
    for base in ("apps", "integrations", "tools"):
        for path in (ROOT / base).rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))


def test_invalid_publisher_config_closes_writer_and_read_pool(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from apps.proxypool.service.app import create_app
    from apps.proxypool.service.config import Settings
    import threading

    before = {x.ident for x in threading.enumerate() if x.name.startswith("proxypool-")}
    app = create_app(
        replace(Settings(), database=str(tmp_path / "invalid.sqlite"), publishing_enabled=True),
        api_key="test-only-api-key",
    )
    with pytest.raises(ValueError, match="not configured"):
        with TestClient(app):
            pass
    after = {x.ident for x in threading.enumerate() if x.name.startswith("proxypool-")}
    assert after == before


def test_source_registration_becomes_available_without_api_restart(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from apps.proxypool.service.app import create_app
    from apps.proxypool.service.config import Settings
    from tools.register_source import register_source

    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")

    class Native:
        def __init__(self):
            self.jobs = []

        async def run_job(self, spider, job):
            self.jobs.append((spider, job))
            return "a" * 24

        async def close(self):
            pass

    native = Native()
    saved = {}

    def call(method, path, payload=None):
        if path == "spiders":
            return {"_id": "b" * 24}
        if path.endswith("/save"):
            saved[payload["path"]] = payload["data"]
            return None
        return saved["/" + path.split("path=/")[1]]

    config = replace(
        Settings(),
        database=str(tmp_path / "hot.sqlite"),
        source_registry_file=str(registry),
        write_flush_seconds=0.01,
    )
    app = create_app(config, api_key="local-hot-source-key", publisher_client=native)
    with TestClient(app, headers={"Authorization": "Bearer local-hot-source-key"}) as client:
        payload = {
            "name": "Reviewed site",
            "spider_key": "reviewed_site",
            "url": "http://127.0.0.1/list",
            "enabled": True,
        }
        assert client.post("/v1/sources", json=payload).status_code == 422
        registered = register_source(
            call,
            registry,
            ROOT / "plugins/source_template.py",
            key="reviewed_site",
            name="Reviewed site",
            url=payload["url"],
        )
        assert registered["restart_required"] is False
        assert any(
            x["key"] == "reviewed_site" for x in client.get("/v1/source-entries").json()["items"]
        )
        assert client.post("/v1/sources", json=payload).status_code == 200
        assert client.post("/v1/actions/capture").status_code == 202
        import time

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if native.jobs:
                break
            time.sleep(0.01)
        assert native.jobs[0][0] == "b" * 24 and native.jobs[0][1]["workspace_spider"] is True
