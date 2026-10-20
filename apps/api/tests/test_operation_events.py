"""Устойчивые уведомления журнала, terminal-ответы и освобождение SSE без новой подготовки."""

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from app.api import router
from app.composition import Runtime, create_runtime, get_application
from app.desktop.web import SESSION_COOKIE, create_desktop_web
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.operations.adapters import SqliteOperationRepository
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    owner = create_runtime(
        tmp_path / "events.sqlite3", geometry_engine=ShapelyGeometryEngine()
    )
    try:
        yield owner
    finally:
        owner.close()


def operation(
    runtime: Runtime, status: OperationStatus = OperationStatus.RUNNING
) -> ProjectOperation:
    project = runtime.application.create_project("Наблюдение подготовки")
    return runtime.operation_repository.create(
        ProjectOperation(
            project_id=project.id,
            kind=OperationKind.CALCULATE_GEOMETRY,
            status=status,
            project_state_version=project.state_version,
        )
    )


def test_heartbeat_does_not_read_journal_and_failed_cas_does_not_notify(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = operation(runtime)
    journal = runtime.operation_repository
    calls: list[str] = []
    get = journal.get

    def record_get(identity: str) -> ProjectOperation:
        calls.append(identity)
        return get(identity)

    monkeypatch.setattr(journal, "get", record_get)
    stop = Event()
    stream = journal.watch(current.id, stop=stop, keepalive_seconds=0.001)
    assert next(stream) == current
    assert next(stream) is None
    assert calls == [current.id]
    wrong = current.model_copy(update={"progress": 10})
    assert journal.compare_and_save(wrong, wrong) is None
    assert next(stream) is None
    assert calls == [current.id]
    journal.cancel_watch(stop)


@pytest.mark.parametrize("write", ["save", "cas", "recover"])
def test_watch_publishes_only_committed_terminal_changes(
    runtime: Runtime, write: str
) -> None:
    current = operation(runtime)
    journal = runtime.operation_repository
    reader = SqliteOperationRepository(runtime.database_path, recover=False)
    stop = Event()
    stream = journal.watch(current.id, stop=stop)
    try:
        assert next(stream) == current
        terminal = current.model_copy(update={"status": OperationStatus.COMPLETED})
        if write == "save":
            journal.save(terminal)
        elif write == "cas":
            assert journal.compare_and_save(current, terminal) == terminal
        else:
            terminal = journal.recover_incomplete()[0]
        observed = next(stream)
        assert observed == terminal == reader.get(current.id)
        with pytest.raises(StopIteration):
            next(stream)
    finally:
        journal.cancel_watch(stop)
        reader.close()


@pytest.mark.parametrize("shutdown", [False, True])
def test_cancel_subscription_or_close_releases_waiting_thread(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    shutdown: bool,
) -> None:
    current = operation(runtime)
    journal = runtime.operation_repository
    waiting = Event()
    wait = journal._changes.wait_for

    def mark_wait(predicate: Callable[[], bool], timeout: float | None = None) -> bool:
        waiting.set()
        return wait(predicate, timeout)

    monkeypatch.setattr(journal._changes, "wait_for", mark_wait)
    stop = Event()
    stream = journal.watch(current.id, stop=stop)
    assert next(stream) == current
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lambda: next(stream, "finished"))
        assert waiting.wait(1)
        if shutdown:
            journal.close()
        else:
            journal.cancel_watch(stop)
        assert pending.result(timeout=1) == "finished"
    if not shutdown:
        assert journal.get(current.id) == current


def test_watch_reads_a_write_between_initial_admission_and_iteration(
    runtime: Runtime,
) -> None:
    current = operation(runtime)
    journal = runtime.operation_repository
    stream = journal.watch(current.id, stop=Event())
    terminal = current.model_copy(update={"status": OperationStatus.CANCELLED})
    journal.save(terminal)
    assert next(stream) == terminal
    with pytest.raises(StopIteration):
        next(stream)


@pytest.mark.parametrize(
    "status",
    [
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.INTERRUPTED,
    ],
)
def test_events_emit_terminal_snapshot_once_and_keep_desktop_security(
    runtime: Runtime,
    tmp_path: Path,
    status: OperationStatus,
) -> None:
    current = operation(runtime, status)
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: runtime.application
    (tmp_path / "index.html").write_text("<title>Проверка подготовки</title>")
    origin, secret = "http://127.0.0.1:18327", "a" * 43
    desktop = create_desktop_web(api, tmp_path, origin=origin, secret=secret)
    path = f"/api/projects/{current.project_id}/operations/{current.id}/events"
    with TestClient(desktop, base_url=origin, client=("127.0.0.1", 50001)) as client:
        assert client.get(path).status_code == 403
        client.cookies.set(SESSION_COOKIE, secret)
        assert (
            client.get(path, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        response = client.get(path, headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.text.count("event: operation\n") == 1
    assert f'"status":"{status.value}"' in response.text
    assert runtime.application.get_operation(current.project_id, current.id) == current


@pytest.mark.parametrize("wrong", ["project", "operation"])
def test_events_reject_wrong_identity_before_stream_headers(
    runtime: Runtime, wrong: str
) -> None:
    current = operation(runtime)
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: runtime.application
    project_id = "missing-project" if wrong == "project" else current.project_id
    operation_id = "missing-operation" if wrong == "operation" else current.id
    with TestClient(api) as client:
        response = client.get(
            f"/api/projects/{project_id}/operations/{operation_id}/events"
        )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_real_disconnect_releases_wait_without_cancelling_operation(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = operation(runtime)
    journal = runtime.operation_repository
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: runtime.application
    cancelled: list[Event] = []
    cancel = journal.cancel_watch

    def record_cancel(stop: Event) -> None:
        cancelled.append(stop)
        cancel(stop)

    monkeypatch.setattr(journal, "cancel_watch", record_cancel)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        initial, waiting, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        wait = journal._changes.wait_for
        watch = journal._watch
        queue: asyncio.Queue[Message] = asyncio.Queue()
        frames: list[Message] = []

        def record_wait(
            predicate: Callable[[], bool], timeout: float | None = None
        ) -> bool:
            loop.call_soon_threadsafe(waiting.set)
            return wait(predicate, timeout)

        def record_watch(
            item: ProjectOperation, revision: int, stop: Event, keepalive_seconds: float
        ) -> Iterator[ProjectOperation | None]:
            try:
                yield from watch(item, revision, stop, keepalive_seconds)
            finally:
                loop.call_soon_threadsafe(finished.set)

        monkeypatch.setattr(journal._changes, "wait_for", record_wait)
        monkeypatch.setattr(journal, "_watch", record_watch)

        async def receive() -> Message:
            return await queue.get()

        async def send(frame: Message) -> None:
            frames.append(frame)
            if (
                frame["type"] == "http.response.body"
                and b"event: operation" in frame["body"]
            ):
                initial.set()

        path = f"/api/projects/{current.project_id}/operations/{current.id}/events"
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50001),
        }
        await queue.put({"type": "http.request", "body": b"", "more_body": False})
        request = asyncio.create_task(api(scope, receive, send))
        try:
            await asyncio.wait_for(initial.wait(), 2)
            await asyncio.wait_for(waiting.wait(), 2)
            await queue.put({"type": "http.disconnect"})
            await asyncio.wait_for(request, 2)
            await asyncio.wait_for(finished.wait(), 2)
            assert len(cancelled) == 1 and cancelled[0].is_set()
            assert frames[0]["status"] == 200
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    asyncio.run(scenario())
    assert journal.get(current.id) == current
