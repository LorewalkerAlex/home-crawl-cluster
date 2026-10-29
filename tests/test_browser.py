"""One-page browser acceptance with installed Edge; no browser download or Docker."""

import os
from pathlib import Path
import uuid
import pytest
from playwright.sync_api import sync_playwright, expect
from test_loopback_integration import api_server, KEY


def test_single_page_browser_workflow(tmp_path):
    browser_path = os.getenv("PROXYPOOL_BROWSER")
    if not browser_path:
        candidates = [
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        ]
        browser_path = next((str(x) for x in candidates if x.exists()), None)
    if not browser_path:
        pytest.skip("Set PROXYPOOL_BROWSER to an installed Chromium browser")
    with api_server(tmp_path) as (client, app, url), sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=browser_path,
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--no-first-run",
            ],
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.url.startswith(url)
                    else route.abort()
                ),
            )
            responses = []
            page.on(
                "response",
                lambda response: responses.append((response.status, response.url)),
            )
            page.goto(url)
            page.locator("#apiKey").fill(KEY)
            page.get_by_role("button", name="连接", exact=True).click()
            try:
                expect(page.locator("#poolCount")).to_have_text("0 / 100,000")
            except AssertionError:
                raise AssertionError(
                    {
                        "errors": errors,
                        "notice": page.locator("#notice").inner_text(),
                        "responses": responses,
                    }
                ) from None
            expect(page.locator("#test")).to_be_disabled()
            page.get_by_role("button", name="添加网址", exact=True).click()
            dialog = page.locator("#targetDialog")
            dialog.locator("[name=name]").fill("Local target <script>")
            dialog.locator("[name=test_url]").fill("https://example.invalid/check")
            dialog.get_by_role("button", name="保存", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.locator("#targetsBody")).to_contain_text(
                "Local target <script>"
            )
            client.post(
                "/v1/proxies/import",
                json={
                    "proxies": [{"scheme": "http", "host": "192.0.2.1", "port": 8080}]
                },
            )
            records = [
                {
                    "result_id": uuid.uuid4().hex,
                    "proxy_id": 1,
                    "target_id": 1,
                    "target_revision": 1,
                    "outcome": "response",
                    "http_status": 200,
                    "latency_ms": 42.0,
                }
                for _ in range(63)
            ]
            assert (
                client.post(
                    "/v1/proxies/feedback", json={"results": records}
                ).status_code
                == 200
            )
            page.locator("#refresh").click()
            expect(page.locator("#poolCount")).to_have_text("1 / 100,000")
            page.get_by_role("button", name="高分代理", exact=True).click()
            expect(page.locator("#proxyDetail")).to_contain_text("192.0.2.1:8080")
            expect(page.locator("#proxyDetail")).to_contain_text("94.25")
            assert page.url == url + "/"
            toggle = page.locator("#targetsBody input[type=checkbox]")
            toggle.uncheck()
            expect(page.locator("#targetCount")).to_have_text("0")
            page.get_by_role("button", name="添加来源", exact=True).click()
            source = page.locator("#sourceDialog")
            source.locator("[name=name]").fill("Offline source")
            source.locator("[name=url]").fill("http://127.0.0.1/list")
            source.get_by_role("button", name="保存", exact=True).click()
            expect(source).not_to_be_visible()
            expect(page.locator("#sourcesBody")).to_contain_text("Offline source")
            assert not errors, errors
            artifact = os.getenv("PROXYPOOL_SCREENSHOT")
            if artifact:
                path = Path(artifact)
                path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(path), full_page=True)
        finally:
            browser.close()
