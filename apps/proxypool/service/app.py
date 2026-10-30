"""Single-process M05 API and static single-page console."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import time
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .auth import install_auth
from .cache import CandidateCache
from .config import Settings, secret
from .database import Database, LimitExceeded, NotFound
from .models import (
    Acquire,
    FeedbackPackage,
    ImportPackage,
    SourceInput,
    SourceResult,
    TargetInput,
)
from .writer import Busy, Writer
from ..crawler.registry import load_entries


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(("/v1/", "/auth/")):
            return await self.app(scope, receive, send)
        parts, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.limit:
                return await JSONResponse(
                    {"error": "request_too_large"}, status_code=413
                )(scope, receive, send)
            parts.append(body)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": b"".join(parts),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay, send)


def create_app(
    settings=None,
    *,
    api_key=None,
    publisher_client=None,
    admin_username=None,
    admin_password_hash=None,
    session_secret=None,
):
    settings = settings or Settings.load()
    api_key = api_key if api_key is not None else secret("PROXYPOOL_API_KEY")
    if len(api_key) < 16:
        raise ValueError("Set PROXYPOOL_API_KEY (at least 16 characters) or its _FILE")
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        app.state.writer = Writer(database)
        app.state.executor = ThreadPoolExecutor(
            max_workers=settings.read_threads, thread_name_prefix="proxypool-read"
        )
        app.state.pool = CandidateCache(settings)
        publisher = None
        try:
            app.state.targets = {
                row["target_id"]: row for row in await read(database.targets)
            }
            app.state.stats_cache = None
            from ..publishers import Publishers

            publisher = Publishers(
                settings,
                database,
                write,
                read,
                app.state.pool.clear,
                client=publisher_client,
            )
            app.state.publishers = publisher
            publisher.start()
            yield
        finally:
            if publisher is not None:
                await publisher.close()
            await asyncio.to_thread(app.state.writer.close)
            app.state.executor.shutdown(wait=True)

    app = FastAPI(
        title="Personal Proxypool",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit, limit=settings.max_request_bytes)
    app.state.settings = settings

    async def read(function, *args):
        return await asyncio.get_running_loop().run_in_executor(
            app.state.executor, function, *args
        )

    async def write(kind, payload, urgent=False):
        future = app.state.writer.submit(kind, payload, urgent=urgent)
        result = await asyncio.shield(asyncio.wrap_future(future))
        app.state.stats_cache = None
        return result

    authorize = install_auth(
        app,
        settings,
        api_key,
        username=admin_username or secret("PROXYPOOL_ADMIN_USERNAME") or "admin",
        password_hash=admin_password_hash
        if admin_password_hash is not None
        else secret("PROXYPOOL_ADMIN_PASSWORD_HASH"),
        session_secret=session_secret
        if session_secret is not None
        else secret("PROXYPOOL_SESSION_SECRET"),
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.exception_handler(Busy)
    async def busy_error(request, error):
        return JSONResponse(
            {"error": "write_queue_busy", "committed": False}, status_code=503
        )

    @app.exception_handler(NotFound)
    async def missing_error(request, error):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(LimitExceeded)
    async def limit_error(request, error):
        return JSONResponse({"error": "configured_limit_reached"}, status_code=409)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, error):
        return JSONResponse(
            {"error": "database_write_failed", "committed": False}, status_code=503
        )

    def size_check(items):
        if len(items) > settings.max_request_records:
            raise HTTPException(status_code=413, detail="too_many_records")

    async def refresh_targets():
        app.state.targets = {
            row["target_id"]: row for row in await read(database.targets)
        }
        app.state.pool.clear()

    protected = [Depends(authorize)]

    @app.get("/healthz")
    async def health():
        healthy = app.state.writer.health()["writer_alive"]
        return JSONResponse({"ok": healthy}, status_code=200 if healthy else 503)

    @app.get("/v1/stats", dependencies=protected)
    async def stats():
        cached = app.state.stats_cache
        if cached is None or time.monotonic() - cached[0] > 5:
            value = await read(database.stats)
            app.state.stats_cache = (time.monotonic(), value)
        else:
            value = cached[1]
        return dict(
            value,
            writer=app.state.writer.health(),
            publishing=app.state.publishers.status(),
            crawlab_ui_url=settings.crawlab_ui_url,
        )

    @app.get("/v1/targets", dependencies=protected)
    async def targets():
        return {"items": list(app.state.targets.values())}

    @app.post("/v1/targets", dependencies=protected)
    async def create_target(payload: TargetInput):
        result = await write("target_save", payload.model_dump(), True)
        await refresh_targets()
        return result

    @app.put("/v1/targets/{target_id}", dependencies=protected)
    async def update_target(target_id: int, payload: TargetInput):
        result = await write(
            "target_save", dict(payload.model_dump(), target_id=target_id), True
        )
        await refresh_targets()
        return result

    @app.delete("/v1/targets/{target_id}", dependencies=protected)
    async def delete_target(target_id: int):
        result = await write("target_delete", target_id, True)
        await refresh_targets()
        app.state.pool.forget_target(target_id)
        return result

    @app.get("/v1/source-entries", dependencies=protected)
    async def entries():
        return {
            "items": [
                {"key": key, "name": value["name"], "example_url": value["example_url"]}
                for key, value in load_entries(settings.source_registry_file).items()
            ]
        }

    @app.get("/v1/sources", dependencies=protected)
    async def sources():
        return {"items": await read(database.sources)}

    async def save_source(payload, source_id=None):
        if payload.spider_key not in load_entries(settings.source_registry_file):
            raise HTTPException(status_code=422, detail="source_not_registered")
        data = payload.model_dump()
        if source_id is not None:
            data["source_id"] = source_id
        return await write("source_save", data, True)

    @app.post("/v1/sources", dependencies=protected)
    async def create_source(payload: SourceInput):
        return await save_source(payload)

    @app.put("/v1/sources/{source_id}", dependencies=protected)
    async def update_source(source_id: int, payload: SourceInput):
        return await save_source(payload, source_id)

    @app.delete("/v1/sources/{source_id}", dependencies=protected)
    async def delete_source(source_id: int):
        return await write("source_delete", source_id, True)

    @app.post("/v1/proxies/import", dependencies=protected)
    async def import_proxies(payload: ImportPackage):
        size_check(payload.proxies)
        return await write("import", [x.model_dump() for x in payload.proxies])

    @app.post("/v1/proxies/feedback", dependencies=protected)
    async def feedback(payload: FeedbackPackage):
        size_check(payload.results)
        return await write("feedback", [x.model_dump() for x in payload.results])

    @app.post("/v1/source-results", dependencies=protected)
    async def source_result(payload: SourceResult):
        size_check(payload.proxies)
        return await write("source_result", payload.model_dump())

    @app.post("/v1/proxies/acquire", dependencies=protected)
    async def acquire(payload: Acquire):
        target = app.state.targets.get(payload.target_id)
        if target is None:
            raise NotFound("Target not found")
        if not target["enabled"]:
            raise HTTPException(status_code=409, detail="target_disabled")
        item = await app.state.pool.acquire(
            target, lambda *args: read(database.candidates, *args)
        )
        if item is None:
            return JSONResponse({"error": "NO_PROXY_AVAILABLE"}, status_code=404)
        item["success_count"] = item.pop("result_bits").bit_count()
        return item

    @app.get("/v1/proxies", dependencies=protected)
    async def proxies(
        target_id: int = Query(gt=0),
        min_score: float = Query(default=0, ge=0, le=100),
        page: int = Query(default=1, ge=1, le=1000000),
        size: int = Query(default=20, ge=1, le=100),
    ):
        return await read(database.quality_page, target_id, min_score, page, size)

    @app.post("/v1/actions/{action}", dependencies=protected)
    async def action(action: str):
        if action not in ("capture", "test"):
            raise NotFound("Action not found")
        publisher = app.state.publishers
        if not publisher.configured:
            raise HTTPException(status_code=409, detail="configure_crawlab_first")
        if publisher.busy:
            raise HTTPException(status_code=409, detail="publication_in_progress")
        publisher.trigger(action)
        return JSONResponse({"accepted": True}, status_code=202)

    web = Path(__file__).resolve().parent.parent / "web"
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-store"})

    return app
