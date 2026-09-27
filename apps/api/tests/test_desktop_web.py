"""Desktop boundary tests, not a packaged app/AutoCAD end-to-end claim."""

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.desktop.web import (
    SESSION_COOKIE,
    SESSION_HEADER,
    DesktopSession,
    create_desktop_web,
)

ORIGIN = "http://127.0.0.1:18327"
SECRET = "a" * 43


@pytest.fixture
def desktop(tmp_path: Path):
    (tmp_path / "index.html").write_text("<title>Green Atlas desktop fixture</title>")
    (tmp_path / "asset.js").write_text("fixture")
    api = FastAPI()

    @api.api_route("/api/echo", methods=["GET", "POST"])
    def echo(request: Request):
        return {"path": request.url.path}

    app = create_desktop_web(api, tmp_path, origin=ORIGIN, secret=SECRET)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50001)) as client:
        yield client


def authenticate(client):
    client.cookies.set(SESSION_COOKIE, SECRET)


def test_no_files_or_api_without_desktop_session(desktop):
    for path in ("/projects", "/api/echo", "/asset.js", "/_desktop/health"):
        response = desktop.get(path)
        assert response.status_code == 403
        assert SECRET not in response.text


def test_native_health_and_browser_same_origin(desktop):
    response = desktop.get("/_desktop/health", headers={SESSION_HEADER: SECRET})
    assert response.json() == {"service": "green-atlas-desktop", "protocol": 1}
    authenticate(desktop)
    assert desktop.get("/projects/example/import").status_code == 200
    api_response = desktop.get("/api/echo")
    assert api_response.json() == {"path": "/api/echo"}
    assert api_response.headers["cache-control"] == "no-store"
    assert desktop.get("/asset.js").text == "fixture"
    assert desktop.get("/missing.js").status_code == 404
    assert desktop.get("/api/missing").status_code == 404


def test_desktop_api_replaces_upstream_cache_policy(desktop):
    authenticate(desktop)
    response = desktop.get("/api/echo")
    assert response.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Host": "evil.example"},
        {"Host": "localhost:18327"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {SESSION_HEADER: "wrong"},
    ],
)
def test_foreign_site_and_rebinding_rejected(desktop, headers):
    authenticate(desktop)
    assert desktop.get("/api/echo", headers=headers).status_code == 403


def test_cookie_writes_need_exact_origin_but_native_header_can_write(desktop):
    authenticate(desktop)
    assert desktop.post("/api/echo").status_code == 403
    assert desktop.post("/api/echo", headers={"Origin": ORIGIN}).status_code == 200
    assert (
        desktop.post("/api/echo", headers={SESSION_HEADER: SECRET}).status_code == 200
    )
    assert (
        desktop.post(
            "/api/echo", headers={SESSION_HEADER: SECRET, "Origin": "null"}
        ).status_code
        == 403
    )


def test_secret_never_accepted_from_url_or_proxy_headers(desktop):
    assert desktop.get("/api/echo?token=" + SECRET).status_code == 403
    assert (
        desktop.get(
            "/api/echo",
            headers={"X-Green-Atlas-Proxy-Key": SECRET, "X-Green-Atlas-User": "root"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://0.0.0.0:18327",
        "https://service.example",
        "http://127.0.0.1",
        ORIGIN + "/",
        ORIGIN + "?token=x",
    ],
)
def test_no_external_or_ambiguous_origin(origin):
    with pytest.raises(ValueError):
        DesktopSession(FastAPI(), origin=origin, secret=SECRET)


def test_missing_built_ui_is_explicit(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        create_desktop_web(FastAPI(), tmp_path, origin=ORIGIN, secret=SECRET)
