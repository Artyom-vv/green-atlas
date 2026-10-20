"""HTTP abort cancels a read-only preview without racing POST body consumption."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_change_set_geometry_scope import scoped_application
from test_geometry_preparation import addition, draft, project_with

from app import main
from app.api import router as api_router
from app.composition import get_application
from app.operations.progress import (
    OperationCancelled,
    _cancellation_check,
    check_operation_cancelled,
    operation_cancellation,
)
from app.planning.changes.http_cancellation import preview_connection


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakePreview:
    def __init__(self, disconnect):
        self.disconnect = disconnect
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.cancelled = threading.Event()
        self.completed = False
        self.callback_present = False
        self.payload = None

    def get(self, project_id, **kwargs):
        return SimpleNamespace(state_version=1)

    def _preview(self, payload):
        self.callback_present = _cancellation_check.get() is not None
        self.payload = payload
        self.started.set()
        try:
            if self.disconnect:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    check_operation_cancelled()
                    time.sleep(0.005)
                raise AssertionError("HTTP disconnect did not cancel preview")
            check_operation_cancelled()
            self.completed = True
        except OperationCancelled:
            self.cancelled.set()
            raise
        finally:
            self.stopped.set()

    def preview_pattern(self, project_id, payload):
        self._preview(payload)
        return dict(pattern_id="fake", type="fill", requested_count=1, accepted_count=0)

    def preview_change_set(self, project_id, payload):
        self._preview(payload)
        return dict(
            id="fake",
            digest="a" * 64,
            base_plan_version=1,
            source="manual",
            label="Preview",
            can_apply=True,
            expires_at="2026-10-19T23:59:59Z",
        )


async def wait_event(event):
    deadline = time.monotonic() + 3
    while not event.is_set():
        if time.monotonic() > deadline:
            raise TimeoutError("Fake endpoint did not start")
        await asyncio.sleep(0.005)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["patterns", "change-sets"])
@pytest.mark.parametrize("disconnect", [False, True])
async def test_real_post_route_parses_complete_body_before_watching_disconnect(
    monkeypatch, caplog, kind, disconnect
):
    fake = FakePreview(disconnect)
    monkeypatch.setitem(main.app.dependency_overrides, get_application, lambda: fake)
    monkeypatch.setattr(main, "get_application", lambda: fake)
    payload = (
        dict(type="fill", base_plan_version=1, zone_ids=["fake"], target_count=1)
        if kind == "patterns"
        else dict(
            base_plan_version=1,
            label="Preview",
            operations=[dict(type="add", object=dict(kind="tree", x=1, y=2, radius=1))],
        )
    )
    body = json.dumps(payload).encode()
    queue = asyncio.Queue()
    halfway = len(body) // 2
    await queue.put(dict(type="http.request", body=body[:halfway], more_body=True))
    await queue.put(dict(type="http.request", body=body[halfway:], more_body=False))
    messages = []
    consumed = []

    async def receive():
        message = await queue.get()
        consumed.append(message["type"])
        return message

    async def send(message):
        messages.append(message)

    path = f"/api/projects/fake/plan/{kind}/preview"
    scope = dict(
        type="http",
        asgi=dict(version="3.0", spec_version="2.3"),
        http_version="1.1",
        method="POST",
        scheme="http",
        path=path,
        raw_path=path.encode(),
        query_string=b"",
        root_path="",
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        client=("127.0.0.1", 12345),
        server=("127.0.0.1", 8000),
    )
    task = asyncio.create_task(main.app(scope, receive, send))
    await wait_event(fake.started)
    assert consumed[:2] == ["http.request", "http.request"]
    assert fake.callback_present
    if disconnect:
        await queue.put(dict(type="http.disconnect"))
    await asyncio.wait_for(task, timeout=3)
    assert fake.stopped.is_set()
    assert fake.completed is not disconnect
    assert fake.cancelled.is_set() is disconnect
    assert "Unhandled request failure" not in caplog.text
    status = next(
        item["status"] for item in messages if item["type"] == "http.response.start"
    )
    assert status == (499 if disconnect else 200)
    if disconnect:
        assert "http.disconnect" in consumed
    else:
        assert "http.disconnect" not in consumed
    assert _cancellation_check.get() is None
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "preview-disconnect" and not task.done()
    ]


def test_all_read_only_preview_routes_have_disconnect_dependency():
    names = {
        "get_plan_growth_forecast",
        "check_plan_placement",
        "preview_plan_change_set",
        "preview_automatic_plan",
        "preview_plan_pattern",
        "preview_building_screen",
        "preview_plan_recommendation",
        "preview_plan_brush",
    }
    found = set()
    for route in api_router.routes:
        if route.name in names:
            found.add(route.name)
            assert preview_connection in [
                item.call for item in route.dependant.dependencies
            ]
        elif route.name in {
            "apply_plan_change_set",
            "add_plan_object",
            "save_planting_zones",
        }:
            assert preview_connection not in [
                item.call for item in route.dependant.dependencies
            ]
    assert found == names


@pytest.mark.parametrize("cache_preview", [False, True])
def test_disconnect_at_scope_exit_never_publishes_or_caches_preview(
    monkeypatch, cache_preview
):
    project = project_with()
    before = project.model_dump_json()
    changes, provider = scoped_application(project)
    stopped = threading.Event()
    from contextlib import contextmanager

    # The shared helper discovers class-level scopes; make the outer scope close
    # normally, then simulate the request cancellation immediately afterwards.
    real_scope = type(provider).calculation_scope

    @contextmanager
    def closed_scope(self, current):
        with real_scope(self, current):
            yield
        stopped.set()

    monkeypatch.setattr(type(provider), "calculation_scope", closed_scope)
    new_id = Mock(wraps=changes.new_id)
    monkeypatch.setattr(changes, "new_id", new_id)

    def check():
        if stopped.is_set():
            raise OperationCancelled("HTTP disconnect")

    with operation_cancellation(check), pytest.raises(OperationCancelled):
        changes.preview_on_snapshot(
            project, draft(project, [addition(30)]), cache_preview=cache_preview
        )
    assert project.model_dump_json() == before
    assert not changes._previews and not changes._applied
    new_id.assert_not_called()
