"""Same-origin API and built UI for the desktop shell, with a private session.

The native parent seeds an HttpOnly cookie in its own web view. No login page,
URL credential, fixed port or public trust-proxy identity is needed here.
This is an outer boundary: the normal web API keeps its existing behaviour.
"""

from __future__ import annotations

import hmac
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_COOKIE = "green_atlas_local_session"
SESSION_HEADER = "x-green-atlas-local-session"


class DesktopSession:
    """Reject foreign sites, forged hosts and requests outside this launch."""

    def __init__(self, app: ASGIApp, *, origin: str, secret: str):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or not 1024 <= parsed.port <= 65535
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"http://127.0.0.1:{parsed.port}"
        ):
            raise ValueError("Desktop must use its own explicit loopback port")
        if (
            len(secret) < 43
            or not secret.isascii()
            or not all(char.isalnum() or char in "-_" for char in secret)
        ):
            raise ValueError("Desktop requires a random URL-safe launch secret")
        self.app, self.origin, self.host, self.secret = (
            app,
            origin,
            parsed.netloc,
            secret,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        native = headers.get(SESSION_HEADER, "")
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except CookieError:
            cookie = SimpleCookie()
        morsel = cookie.get(SESSION_COOKIE)
        supplied = native or (morsel.value if morsel else "")
        origin = headers.get("origin")
        valid = (
            (scope.get("client") or ("", 0))[0] in {"127.0.0.1", "::1"}
            and headers.getlist("host") == [self.host]
            and len(headers.getlist(SESSION_HEADER)) <= 1
            and hmac.compare_digest(supplied.encode(), self.secret.encode())
            and (origin is None or origin == self.origin)
            and headers.get("sec-fetch-site", "none") in {"none", "same-origin"}
            and (
                scope.get("method") in {"GET", "HEAD", "OPTIONS"}
                or origin == self.origin
                or bool(native)
            )
        )
        if not valid:
            response = JSONResponse(
                {
                    "code": "LOCAL_SESSION_REQUIRED",
                    "message": "Откройте Green Atlas из приложения.",
                },
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        scope = {**scope, "green_atlas.native": bool(native)}
        await self.app(scope, receive, send)


class DesktopWeb:
    """Dispatch without stripping /api; bundle assets never come from a CDN."""

    def __init__(self, api: ASGIApp, web_root: Path):
        self.api = api
        self.root = web_root.resolve(strict=True)
        if not (self.root / "index.html").is_file():
            raise ValueError("The desktop package is missing its built web interface")
        self.files = StaticFiles(directory=self.root, follow_symlink=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        if scope["type"] == "lifespan" or path == "/api" or path.startswith("/api/"):
            async def send_uncached(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() != b"cache-control"
                    ]
                    headers.append((b"cache-control", b"no-store"))
                    message = {**message, "headers": headers}
                await send(message)

            return await self.api(scope, receive, send_uncached)
        if path == "/_desktop/health" and scope.get("method") == "GET":
            return await JSONResponse(
                {"service": "green-atlas-desktop", "protocol": 1}
            )(scope, receive, send)
        if scope.get("method") not in {"GET", "HEAD"}:
            return await JSONResponse({"code": "NOT_FOUND"}, status_code=404)(
                scope, receive, send
            )
        # Only actual SPA routes get the document; missing assets keep their 404.
        if path == "/" or path == "/projects" or path.startswith("/projects/"):
            return await FileResponse(
                self.root / "index.html", headers={"Cache-Control": "no-store"}
            )(scope, receive, send)
        try:
            await self.files(scope, receive, send)
        except HTTPException as error:
            await JSONResponse({"code": "NOT_FOUND"}, status_code=error.status_code)(
                scope, receive, send
            )


def create_desktop_web(
    api: ASGIApp, web_root: Path, *, origin: str, secret: str, handoff=None
):
    from app.desktop.control import DesktopControl

    web = DesktopWeb(api, web_root)
    return DesktopSession(
        DesktopControl(web, handoff) if handoff else web, origin=origin, secret=secret
    )
