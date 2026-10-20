"""Native-завершение runtime не доступно странице через сессионную cookie."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.desktop.web import SESSION_COOKIE, SESSION_HEADER, create_desktop_web

ORIGIN = "http://127.0.0.1:18273"
SECRET = "x" * 43


@pytest.fixture
def shutdown_client(tmp_path: Path):
    (tmp_path / "index.html").write_text("<title>Desktop</title>")
    events: list[str] = []
    app = create_desktop_web(
        FastAPI(),
        tmp_path,
        origin=ORIGIN,
        secret=SECRET,
        shutdown=lambda: events.append("shutdown"),
    )
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 32123)) as client:
        yield client, events


def test_native_shutdown_without_handoff_returns_receipt(shutdown_client) -> None:
    client, events = shutdown_client
    response = client.post(
        "/_desktop/shutdown", json={}, headers={SESSION_HEADER: SECRET}
    )
    assert response.status_code == 202
    assert response.json() == {"state": "stopping"}
    assert response.headers["cache-control"] == "no-store"
    assert events == ["shutdown"]


@pytest.mark.parametrize("body", [b"", b"null", b"[]", b"0", b'{"force":true}', b" " * 65])
def test_shutdown_rejects_invalid_payload(shutdown_client, body: bytes) -> None:
    client, events = shutdown_client
    response = client.post(
        "/_desktop/shutdown", content=body, headers={SESSION_HEADER: SECRET}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "DESKTOP_CONTROL_INVALID"
    assert events == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Cookie": f"{SESSION_COOKIE}={SECRET}", "Origin": ORIGIN},
        {SESSION_HEADER: "y" * 43},
        {SESSION_HEADER: SECRET, "Origin": "https://foreign.example"},
    ],
)
def test_page_or_other_launch_cannot_stop_runtime(shutdown_client, headers) -> None:
    client, events = shutdown_client
    assert client.post("/_desktop/shutdown", json={}, headers=headers).status_code == 403
    assert events == []


def test_shutdown_requires_post(shutdown_client) -> None:
    client, events = shutdown_client
    assert client.get(
        "/_desktop/shutdown", headers={SESSION_HEADER: SECRET}
    ).status_code == 404
    assert events == []
