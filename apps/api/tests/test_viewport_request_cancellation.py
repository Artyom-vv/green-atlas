"""Отмена HTTP карты не отменяет общий индекс и освобождает старые запросы."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event, RLock
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException

from app.api import router
from app.composition import get_application
from app.dxf_import.contracts import ImportMode, ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.cancellation import (
    ViewportRequestCancelled,
    check_viewport_cancelled,
    viewport_cancellation,
)
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.geometry.viewport.source_overview import SourceOverviewCache, SourcePathCache
from app.operations.progress import (
    OperationCancelled,
    cancellable_lock,
    operation_cancellation,
)
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project


def make_project(count=2000):
    return Project(
        name="Viewport cancellation",
        geometry_version=1,
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": str(i),
                        "properties": {"source_layer": f"Road-{i % 5}", "kind": "road"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[i, 0], [i + 0.8, 0], [i + 0.8, 1], [i, 1], [i, 0]]
                            ],
                        },
                    }
                    for i in range(count)
                ],
            }
        ),
    )


def make_api(application):
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application

    @api.middleware("http")
    async def version_header(request, call_next):
        # Тот же BaseHTTPMiddleware, через который проходит production GET.
        response = await call_next(request)
        response.headers["X-Project-Version"] = "1"
        return response

    return api


class Request:
    def __init__(self, api, project_id, resolution=1.0, query=None):
        self.frames = []
        self.queue = asyncio.Queue()
        self.queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        path = f"/api/projects/{project_id}/map-features"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "headers": [],
            "query_string": (
                query or f"min_x=-1&min_y=-1&max_x=2100&max_y=2&resolution={resolution}"
            ).encode(),
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50001),
        }

        async def send(frame):
            self.frames.append(frame)

        self.task = asyncio.create_task(api(scope, self.queue.get, send))

    def disconnect(self):
        self.queue.put_nowait({"type": "http.disconnect"})

    async def finish(self):
        await asyncio.wait_for(self.task, 3)

    def status(self):
        return next(
            frame["status"]
            for frame in self.frames
            if frame["type"] == "http.response.start"
        )

    def body(self):
        return json.loads(b"".join(frame.get("body", b"") for frame in self.frames))


async def until(predicate):
    deadline = monotonic() + 2
    while not predicate():
        assert monotonic() < deadline, "event was not observed"
        await asyncio.sleep(0.001)


def assert_no_watchers():
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "viewport-disconnect" and not task.done()
    ]


def test_warm_burst_discards_five_old_requests_and_serves_latest(
    monkeypatch, record_property, caplog
):
    project = make_project()
    query = IndexedGeometryQuery(max_features=16)
    query._index(project)
    repository = Mock(spec=["get", "get_geometry_view"])
    repository.get.return_value = project
    spatial = SpatialQueries(repository, query, Mock())
    stages = []
    blocked = Event()
    original_paths = SourcePathCache.paths

    def paths(cache, geometry, resolution):
        if resolution < 1.5:
            blocked.set()
            # Удерживаем настоящую render-lock, пока ASGI не отменит старый запрос.
            # Это контролируемая задержка, не измерение скорости реального чертежа.
            deadline = monotonic() + 2
            while monotonic() < deadline:
                check_viewport_cancelled()
                sleep(0.001)
            pytest.fail("disconnected owner kept computing")
        return original_paths(cache, geometry, resolution)

    monkeypatch.setattr(SourcePathCache, "paths", paths)

    class Application:
        def query_geometry(self, project_id, extent, resolution):
            stages.append((resolution, "start", monotonic()))
            try:
                result = spatial.query_geometry(project_id, extent, resolution)
            except ViewportRequestCancelled:
                stages.append((resolution, "cancel", monotonic()))
                raise
            stages.append((resolution, "complete", monotonic()))
            return result

    async def scenario():
        api = make_api(Application())
        old = [Request(api, project.id)]
        await until(blocked.is_set)
        old.extend(Request(api, project.id, 1 + i / 10) for i in range(1, 5))
        latest = Request(api, project.id, 1.5)
        await until(
            lambda: len([stage for stage in stages if stage[1] == "start"]) == 6
        )
        disconnected_at = monotonic()
        for request in old:
            request.disconnect()
        await asyncio.gather(*(request.finish() for request in [*old, latest]))
        assert latest.status() == 200
        assert (
            latest.body()["feature_collection"]["source_overview"]["source_features"]
            == 2000
        )
        assert {resolution for resolution, stage, _ in stages if stage == "cancel"} == {
            1,
            1.1,
            1.2,
            1.3,
            1.4,
        }
        assert [
            resolution for resolution, stage, _ in stages if stage == "complete"
        ] == [1.5]
        assert (
            max(at for _, stage, at in stages if stage == "cancel") - disconnected_at
            < 1
        )
        assert_no_watchers()
        record_property(
            "stages_ms",
            json.dumps(
                [
                    {
                        "resolution": resolution,
                        "stage": stage,
                        "after_disconnect_ms": round((at - disconnected_at) * 1000, 3),
                    }
                    for resolution, stage, at in stages
                ]
            ),
        )

    asyncio.run(scenario())
    assert query.cached_project_count == 1
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


@pytest.mark.parametrize("persistent", [False, True])
def test_disconnected_cold_owner_finishes_index_and_waiter_exits(
    tmp_path, monkeypatch, record_property, persistent
):
    repository = SqliteProjectRepository(tmp_path / "viewport.sqlite3")
    if not persistent:
        monkeypatch.setattr(
            repository, "open_display_geometry", lambda _, **kwargs: nullcontext(None)
        )
    project = repository.create(make_project(200))
    query = IndexedGeometryQuery(max_features=16)
    spatial = SpatialQueries(repository, query, Mock())
    entered, release = Event(), Event()
    calls = 0
    stages = []
    target = repository._geometry_store if persistent else repository
    method = "consume_features" if persistent else "consume_geometry_view"
    consume = getattr(target, method)

    def paused_consume(*args, **kwargs):
        nonlocal calls
        calls += 1
        stages.append(("index_start", monotonic()))
        entered.set()
        assert release.wait(2)
        result = consume(*args, **kwargs)
        stages.append(("index_validated", monotonic()))
        return result

    monkeypatch.setattr(target, method, paused_consume)

    async def scenario():
        api = make_api(spatial)
        owner = Request(api, project.id)
        await until(entered.is_set)
        waiters = [Request(api, project.id, 1 + i / 10) for i in range(1, 6)]
        await asyncio.sleep(0.02)
        owner.disconnect()
        disconnected_at = monotonic()
        stages.append(("disconnect_owner_and_waiters", disconnected_at))
        for waiter in waiters:
            waiter.disconnect()
        await asyncio.gather(*(waiter.finish() for waiter in waiters))
        stages.append(("all_waiters_finished", monotonic()))
        assert not owner.task.done()
        assert calls == 1
        release.set()
        await owner.finish()
        stages.append(("owner_finished", monotonic()))
        # Разрыв не отменяет проверенную общую подготовку. Дисковый ресурс
        # уже опубликован; кеш окна отменённому владельцу создавать не нужно.
        assert query.cached_project_count == (0 if persistent else 1)
        if persistent:
            assert (
                repository._connection.execute(
                    "SELECT count(*) FROM project_display_resources"
                ).fetchone()[0]
                == 1
            )
        latest = Request(api, project.id, 1.5)
        await latest.finish()
        stages.append(("latest_finished", monotonic()))
        assert latest.status() == 200
        assert calls == 1
        assert len(query._indexes[project.id].features) == 200
        assert_no_watchers()
        record_property(
            "stages_ms",
            json.dumps(
                [
                    {
                        "stage": stage,
                        "after_disconnect_ms": round((at - disconnected_at) * 1000, 3),
                    }
                    for stage, at in stages
                ]
            ),
        )

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        repository.close()


def test_disconnected_viewport_waiting_for_cold_scene_owner_exits_before_build(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "scene-owner.sqlite3")
    project = repository.create(make_project(20))
    query = IndexedGeometryQuery(max_features=16, display_repository=repository)
    spatial = SpatialQueries(repository, query, Mock())
    entered, release, viewport_waiting = Event(), Event(), Event()
    consume = repository._geometry_store.consume_features
    open_resource = repository.open_display_geometry
    calls = 0

    def paused_consume(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2)
        return consume(*args, **kwargs)

    @contextmanager
    def observed_open(*args, **kwargs):
        if kwargs.get("wait_check") is not None:
            viewport_waiting.set()
        with open_resource(*args, **kwargs) as opened:
            yield opened

    monkeypatch.setattr(repository._geometry_store, "consume_features", paused_consume)
    monkeypatch.setattr(repository, "open_display_geometry", observed_open)

    async def scenario(owner):
        api = make_api(spatial)
        waiter = Request(api, project.id)
        await until(viewport_waiting.is_set)
        waiter.disconnect()
        await asyncio.wait_for(waiter.finish(), 1)
        assert not owner.done() and not release.is_set()
        assert calls == 1
        release.set()
        await until(owner.done)
        latest = Request(api, project.id, 1.5)
        await latest.finish()
        assert latest.status() == 200 and calls == 1
        assert_no_watchers()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            owner = executor.submit(query.cached_scene_features, project)
            try:
                assert entered.wait(2)
                asyncio.run(scenario(owner))
            finally:
                release.set()
            assert len(owner.result(2)) == 20
    finally:
        repository.close()


@pytest.mark.parametrize("outcome", ["normal", "error", "internal", "invalid"])
def test_route_keeps_response_validation_errors_and_cleans_context(outcome):
    snapshot = make_project(1).source_geometry

    class Application:
        def query_geometry(self, *_):
            check_viewport_cancelled()
            if outcome == "error":
                raise HTTPException(409, "conflict")
            if outcome == "internal":
                raise RuntimeError("unexpected failure")
            return snapshot

    async def scenario():
        request = Request(
            make_api(Application()),
            "test",
            query="resolution=-1" if outcome == "invalid" else None,
        )
        await request.finish()
        assert (
            request.status()
            == {"normal": 200, "error": 409, "internal": 500, "invalid": 422}[outcome]
        )
        if outcome == "normal":
            assert request.body() == snapshot.model_dump(mode="json")
            assert (b"x-project-version", b"1") in request.frames[0]["headers"]
        assert_no_watchers()
        check_viewport_cancelled()

    asyncio.run(scenario())


def test_viewport_cancel_waiter_does_not_release_foreign_lock():
    lock, acquired, release, stop = RLock(), Event(), Event(), Event()

    def owner():
        with lock:
            acquired.set()
            assert release.wait(2)

    def check():
        if stop.is_set():
            raise ViewportRequestCancelled()

    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(owner)
        assert acquired.wait(1)
        stop.set()
        try:
            with viewport_cancellation(check), pytest.raises(ViewportRequestCancelled):
                with cancellable_lock(lock, check=check_viewport_cancelled):
                    pytest.fail("cancelled waiter acquired lock")
            assert not lock.acquire(blocking=False)
        finally:
            release.set()
            task.result()
    check_viewport_cancelled()


def test_cancelled_svg_never_publishes_partial_drawing(monkeypatch):
    from shapely.geometry import box

    cache = SourceOverviewCache()
    geometry = box(0, 0, 1, 1)
    stopped = False
    original = cache._paths.paths

    def paths(*args):
        nonlocal stopped
        result = original(*args)
        stopped = True
        return result

    def check():
        if stopped:
            raise ViewportRequestCancelled()

    monkeypatch.setattr(cache._paths, "paths", paths)
    candidates = [({"properties": {"source_layer": "Road"}}, geometry)]
    with viewport_cancellation(check), pytest.raises(ViewportRequestCancelled):
        cache.render(candidates, (0, 0, 1, 1), 1, b"one")
    assert not cache._drawings
    assert cache._bytes == 0
    assert len(cache._paths.entries) == 1  # Полностью готовый путь остаётся пригодным.
    assert cache.render(candidates, (0, 0, 1, 1), 1, b"one")["svg"]


def test_cancellation_inside_path_is_not_hidden_by_exact_geometry_fallback():
    from shapely.geometry import LineString

    from app.geometry.viewport.source_overview import _display_paths

    def check():
        raise OperationCancelled()

    with operation_cancellation(check), pytest.raises(OperationCancelled):
        _display_paths(LineString([(0, 0), (1, 1)]), 1)
