"""Real password/session flow, program keys and cookie write boundaries."""

from dataclasses import replace
import json

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
import pytest

from apps.proxypool.service.app import create_app
from apps.proxypool.service.config import Settings
from tools.configure_admin import configure

KEY = "machine-only-test-api-key"
PASSWORD = "a-test-only-dashboard-password"
HASH = PasswordHash.recommended().hash(PASSWORD)


@pytest.fixture
def dashboard(tmp_path):
    app = create_app(
        replace(
            Settings(), database=str(tmp_path / "auth.sqlite"), write_flush_seconds=0.01
        ),
        api_key=KEY,
        admin_password_hash=HASH,
        session_secret="test-only-session-secret-long-enough",
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def login(client, password=PASSWORD):
    return client.post(
        "/auth/login",
        json={"username": "admin", "password": password},
        headers={"Origin": "https://testserver"},
    )


def test_password_login_cookie_reload_and_logout(dashboard):
    c = dashboard
    assert c.get("/v1/stats").status_code == 401
    assert login(c, "incorrect-password").status_code == 401
    response = login(c)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert PASSWORD not in cookie and KEY not in cookie
    assert c.get("/auth/session").json()["authenticated"] is True
    assert c.get("/v1/stats").status_code == 200
    assert (
        c.post("/auth/logout", headers={"Origin": "https://testserver"}).status_code
        == 200
    )
    assert c.get("/v1/stats").status_code == 401


def test_cookie_writes_require_same_origin_and_machine_key_stays_independent(dashboard):
    c = dashboard
    assert login(c).status_code == 200
    target = {"name": "test", "test_url": "https://example.invalid/", "enabled": True}
    assert (
        c.post(
            "/v1/targets", json=target, headers={"Origin": "https://other.testserver"}
        ).status_code
        == 403
    )
    assert c.post("/v1/targets", json=target).status_code == 403
    assert (
        c.post(
            "/v1/targets", json=target, headers={"Origin": "https://testserver"}
        ).status_code
        == 200
    )
    assert (
        c.get("/v1/stats", headers={"Authorization": "Bearer wrong-key"}).status_code
        == 401
    )
    assert (
        c.post("/auth/logout", headers={"Origin": "https://testserver"}).status_code
        == 200
    )
    assert (
        c.get("/v1/stats", headers={"Authorization": "Bearer " + KEY}).status_code
        == 200
    )
    assert login(c).status_code == 200
    c.cookies.set(
        "proxypool_session", "forged-session", domain="testserver.local", path="/"
    )
    # Explicitly override the complete Cookie header to test a signature failure.
    assert (
        c.get(
            "/v1/stats", headers={"Cookie": "proxypool_session=forged-session"}
        ).status_code
        == 401
    )


def test_login_size_and_unconfigured_dashboard_preserve_api(tmp_path, dashboard):
    c = dashboard
    assert c.post("/auth/login", content=b"x" * 262145).status_code == 413
    assert (
        c.post(
            "/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": "https://other.invalid"},
        ).status_code
        == 403
    )
    app = create_app(
        replace(Settings(), database=str(tmp_path / "api-only.sqlite")), api_key=KEY
    )
    with TestClient(app, base_url="https://testserver") as api:
        assert api.get("/auth/session").json()["login_configured"] is False
        assert login(api).status_code == 503
        assert (
            api.get("/v1/stats", headers={"Authorization": "Bearer " + KEY}).status_code
            == 200
        )


def test_private_password_setup_preserves_machine_credentials_and_rotates_session(
    tmp_path,
):
    path = tmp_path / "app.env"
    original = (
        "PROXYPOOL_API_KEY=" + KEY + "\nCRAWLAB_API_TOKEN=synthetic-platform-token\n"
    )
    path.write_text(original)
    inode = path.stat().st_ino
    result = configure(path, "admin", PASSWORD)
    text = path.read_text()
    assert (
        text.startswith(original)
        and PASSWORD not in text
        and path.stat().st_ino == inode
    )
    assert PASSWORD not in json.dumps(result) and KEY not in json.dumps(result)
    values = dict(line.split("=", 1) for line in text.splitlines())
    hashed = values["PROXYPOOL_ADMIN_PASSWORD_HASH"]
    assert hashed.startswith("'$argon2id$") and hashed.endswith("'")
    assert PasswordHash.recommended().verify(PASSWORD, hashed[1:-1])
    old_secret = values["PROXYPOOL_SESSION_SECRET"]
    configure(path, "admin", PASSWORD)
    current = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert current["PROXYPOOL_SESSION_SECRET"] != old_secret
    with pytest.raises(ValueError):
        configure(path, "../invalid", "short")
