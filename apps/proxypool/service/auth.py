"""Single administrator login using framework sessions; Bearer remains for programs."""

import asyncio
import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pwdlib import PasswordHash
from starlette.middleware.sessions import SessionMiddleware


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=512)


def install_auth(app, settings, api_key, *, username, password_hash, session_secret):
    if password_hash and len(session_secret) < 32:
        raise ValueError(
            "Configure PROXYPOOL_SESSION_SECRET before enabling browser login"
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret or secrets.token_urlsafe(32),
        session_cookie="proxypool_session",
        max_age=settings.login_session_seconds,
        same_site="strict",
        https_only=settings.session_https_only,
    )
    hasher = PasswordHash.recommended()
    password_gate = asyncio.Semaphore(1)

    def logged_in(request):
        return bool(password_hash) and request.session.get("user") == username

    def same_origin(request):
        origin = urlsplit(request.headers.get("origin", ""))
        schemes = ("https",) if settings.session_https_only else ("http", "https")
        if (
            origin.scheme not in schemes
            or origin.netloc.lower() != request.headers.get("host", "").lower()
            or origin.username
            or origin.password
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
        ):
            raise HTTPException(status_code=403, detail="origin_rejected")

    def authorize(request: Request, authorization: str = Header(default="")):
        if authorization:
            if hmac.compare_digest(authorization, "Bearer " + api_key):
                return
            raise HTTPException(status_code=401, detail="unauthorized")
        if not logged_in(request):
            raise HTTPException(status_code=401, detail="unauthorized")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            same_origin(request)

    @app.get("/auth/session")
    async def session(request: Request):
        active = logged_in(request)
        return {
            "authenticated": active,
            "username": username if active else None,
            "login_configured": bool(password_hash),
        }

    @app.post("/auth/login")
    async def login(data: LoginInput, request: Request):
        same_origin(request)
        if not password_hash:
            raise HTTPException(status_code=503, detail="login_not_configured")
        async with password_gate:
            try:
                valid = await asyncio.to_thread(
                    hasher.verify, data.password.get_secret_value(), password_hash
                )
            except Exception:
                raise HTTPException(
                    status_code=503, detail="login_unavailable"
                ) from None
        if not valid or data.username != username:
            raise HTTPException(status_code=401, detail="invalid_credentials")
        request.session.clear()
        request.session["user"] = username
        return {"authenticated": True, "username": username}

    @app.post("/auth/logout")
    async def logout(request: Request):
        same_origin(request)
        request.session.clear()
        return {"authenticated": False}

    return authorize
