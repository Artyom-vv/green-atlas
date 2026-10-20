"""Чтение и поток статуса не создают и не возобновляют запросы модели."""

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from app.api import router
from app.application import ProjectApplication
from app.composition import get_application, get_layer_recognition
from app.desktop.web import SESSION_COOKIE, create_desktop_web
from app.dxf_import.contracts import SourceFile
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.layers.recognition import (
    LayerRecognition,
    LayerRoleProposal,
    recognize_layers,
)
from app.dxf_import.layers.semantics import LayerSemanticEvidence
from app.layer_recognition.checkpoints import (
    RecognitionCheckpointStore,
    publish_recognition_result,
)
from app.layer_recognition.service import LayerRecognitionService
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason, ProjectNotFound

SOURCE_SHA = "a" * 64


class NeverRequestedProvider:
    name = "test/status-only"

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, layers: list[Layer]) -> list[LayerRoleProposal]:
        self.calls += 1
        raise AssertionError("A status read must never request model proposals")

    def resolve_conflicts(
        self, layers: list[Layer], proposals: list[LayerRoleProposal]
    ) -> list[LayerRoleProposal]:
        self.calls += 1
        raise AssertionError("A status read must never resume refinement")


def sample_layers() -> list[Layer]:
    return [
        Layer(
            id=str(index),
            source_name=f"Здания {index}",
            object_count=2,
            color="#000000",
            suggested_kind=LayerKind.BUILDING,
            entity_types={"LINE": 2},
        )
        for index in range(2)
    ]


def result_for(
    layers: list[Layer], provider: NeverRequestedProvider
) -> LayerRecognition:
    result = recognize_layers(layers, SOURCE_SHA)
    result.provider = provider.name
    result.total_count = len(layers)
    result.processed_count = len(layers)
    return result


@pytest.fixture
def observation(
    tmp_path: Path,
) -> Iterator[tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]]]:
    provider = NeverRequestedProvider()
    service = LayerRecognitionService(tmp_path / "recognition", provider)
    try:
        yield service, provider, sample_layers()
    finally:
        service.close()


def test_absent_status_does_not_create_job_checkpoint_or_model_request(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
) -> None:
    service, provider, layers = observation
    assert service.observe(layers, SOURCE_SHA) is None
    assert service.observe(layers, SOURCE_SHA) is None
    assert service._jobs == {}
    assert not service.cache.exists()
    assert provider.calls == 0


def test_observe_returns_detached_existing_running_job(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    original = result_for(layers, provider)
    original.status = "running"
    original.processed_count = 1
    service._jobs[key] = original
    observed = service.observe(layers, SOURCE_SHA)
    assert observed == original and observed is not original
    observed.proposals.clear()
    assert len(original.proposals) == len(layers)
    assert service.review(layers, SOURCE_SHA) == original
    assert provider.calls == 0


def test_cold_completed_result_is_read_without_republishing_or_refining(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    result = result_for(layers, provider)
    path = service.cache / f"{key}.json"
    publish_recognition_result(path, result.model_dump_json().encode())
    before = path.read_bytes()
    observed = service.observe(layers, SOURCE_SHA)
    assert observed is not None
    assert observed.status == "completed" and observed.processed_count == 2
    assert observed.proposals == result.proposals
    assert service.review(layers, SOURCE_SHA) == observed
    assert path.read_bytes() == before
    assert service._jobs == {}
    assert provider.calls == 0


@pytest.mark.parametrize("processed", [0, 1, 2])
def test_cold_checkpoint_requires_explicit_resume_and_retains_answers(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    processed: int,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    saved = result_for(layers, provider)
    saved.status = "running"
    saved.processed_count = processed
    RecognitionCheckpointStore(service.cache).save(key, layers, saved)
    path = service.cache / f"{key}.progress.json"
    before = path.read_bytes()
    observed = service.observe(layers, SOURCE_SHA)
    assert observed is not None and observed.status == "failed"
    assert observed.processed_count == processed
    assert observed.message == "Распознавание прервано"
    assert observed.proposals == saved.proposals
    assert service.observe(layers, SOURCE_SHA) == observed
    assert service.review(layers, SOURCE_SHA) == observed
    assert path.read_bytes() == before
    assert provider.calls == 0


@pytest.mark.parametrize(
    "version",
    [
        "layer-classifier-2026-09-28.4",
        "layer-classifier-2026-09-28.5",
        "layer-classifier-2026-09-28.6",
    ],
)
def test_previous_prompt_result_does_not_start_implicit_refinement(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    version: str,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers, version=version)
    path = service.cache / f"{key}.json"
    publish_recognition_result(
        path, result_for(layers, provider).model_dump_json().encode()
    )
    before = path.read_bytes()
    assert service.observe(layers, SOURCE_SHA) is None
    assert path.read_bytes() == before
    assert service._jobs == {}
    assert provider.calls == 0


@pytest.mark.parametrize("change", ["source", "name", "count", "provider"])
def test_distinct_input_does_not_adopt_an_existing_result_or_start_work(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    change: str,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    path = service.cache / f"{key}.json"
    publish_recognition_result(
        path, result_for(layers, provider).model_dump_json().encode()
    )
    before = path.read_bytes()
    source_sha = SOURCE_SHA
    if change == "source":
        source_sha = "c" * 64
    elif change == "name":
        layers[0].source_name = "Газон"
    elif change == "count":
        layers[0].object_count = 3
    else:
        provider.name = "test/another-provider"
    assert service.observe(layers, source_sha) is None
    assert path.read_bytes() == before
    assert provider.calls == 0


@pytest.mark.parametrize("corruption", ["source", "provider", "ids", "count"])
def test_invalid_exact_cache_is_not_returned_or_replaced(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    corruption: str,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    result = result_for(layers, provider)
    if corruption == "source":
        result.source_sha256 = "d" * 64
    elif corruption == "provider":
        result.provider = "test/another"
    elif corruption == "ids":
        result.proposals[0].layer_id = "another-layer"
    else:
        result.processed_count = 1
    path = service.cache / f"{key}.json"
    publish_recognition_result(path, result.model_dump_json().encode())
    before = path.read_bytes()
    assert service.observe(layers, SOURCE_SHA) is None
    assert path.read_bytes() == before
    assert provider.calls == 0


@pytest.mark.parametrize("invalid_field", ["source", "completed_ids"])
def test_corrupt_checkpoint_is_visible_without_replaying_work(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    invalid_field: str,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    result = result_for(layers, provider)
    result.status = "running"
    RecognitionCheckpointStore(service.cache).save(key, layers, result)
    path = service.cache / f"{key}.progress.json"
    data = json.loads(path.read_bytes())
    if invalid_field == "source":
        data["result"]["source_sha256"] = "e" * 64
    else:
        data["result"]["completed_layer_ids"] = [layers[1].id]
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    observed = service.observe(layers, SOURCE_SHA)
    assert observed is not None and observed.status == "failed"
    assert observed.processed_count == 0
    assert observed.completed_layer_ids == []
    assert observed.message and "заново" in observed.message
    assert path.read_bytes() == before
    assert provider.calls == 0


def test_observe_rejects_cross_source_semantic_evidence(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
) -> None:
    service, provider, layers = observation
    layers[0].semantic_evidence = LayerSemanticEvidence(
        source_sha256="b" * 64,
        evidence_sha256="c" * 64,
        complete=True,
        eligible_count=0,
        captured_count=0,
        omitted_count=0,
        default_count=0,
        observations=(),
    )
    with pytest.raises(DomainFailure) as caught:
        service.observe(layers, SOURCE_SHA)
    assert caught.value.reason is FailureReason.RECOGNITION_SOURCE_MISMATCH
    assert service._jobs == {} and provider.calls == 0


@pytest.mark.parametrize(
    "endpoint,state",
    [
        ("status", "absent"),
        ("status", "running"),
        ("status", "completed"),
        ("status", "checkpoint"),
        ("events", "absent"),
        ("events", "completed"),
        ("events", "checkpoint"),
    ],
)
def test_public_status_endpoint_reads_summary_only_and_never_starts_model_work(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    endpoint: str,
    state: str,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    result = result_for(layers, provider)
    if state == "running":
        result.status = "running"
        result.processed_count = 1
        service._jobs[key] = result
    elif state == "completed":
        publish_recognition_result(
            service.cache / f"{key}.json", result.model_dump_json().encode()
        )
    elif state == "checkpoint":
        result.status = "running"
        result.processed_count = 1
        RecognitionCheckpointStore(service.cache).save(
            key, layers, result, completed_layer_ids=[layers[1].id]
        )
    project = Project(
        name="Status qualification",
        layers=layers,
        source_file=SourceFile(
            name="Saved capture.dwg",
            content_sha256=SOURCE_SHA,
            size=12,
            imported_at="2026-10-07T00:00:00Z",
            dxf_version="AutoCAD live",
            units="m",
            entity_count=4,
        ),
    )
    application = Mock(spec=ProjectApplication)
    application.get.return_value = project
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    api.dependency_overrides[get_layer_recognition] = lambda: service
    with TestClient(api) as client:
        response = client.get(
            f"/api/projects/{project.id}/source-layer-review/{endpoint}"
        )
    assert response.status_code == 200
    if endpoint == "events":
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.text.count("event: recognition\n") == 1
        data = json.loads(response.text.partition("data: ")[2].splitlines()[0])
    else:
        data = response.json()
    if state == "absent":
        assert data is None and service._jobs == {}
    else:
        observed = LayerRecognition.model_validate(data)
        assert observed.status == ("failed" if state == "checkpoint" else state)
        assert observed.source_sha256 == SOURCE_SHA
        assert len(observed.proposals) == len(layers)
        if state == "checkpoint":
            assert observed.completed_layer_ids == [layers[1].id]
    application.get.assert_called_once_with(project.id, lightweight=True)
    assert provider.calls == 0


@pytest.mark.parametrize("failure", ["missing_project", "semantic_source"])
@pytest.mark.parametrize("endpoint", ["status", "events"])
def test_public_status_rejects_wrong_project_or_semantic_source_without_side_effects(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    failure: str,
    endpoint: str,
) -> None:
    service, provider, layers = observation
    layers[0].semantic_evidence = LayerSemanticEvidence(
        source_sha256="b" * 64,
        evidence_sha256="c" * 64,
        complete=True,
        eligible_count=0,
        captured_count=0,
        omitted_count=0,
        default_count=0,
        observations=(),
    )
    project = Project(name="Wrong source qualification", layers=layers)
    application = Mock(spec=ProjectApplication)
    if failure == "missing_project":
        application.get.side_effect = ProjectNotFound(project.id)
    else:
        application.get.return_value = project
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    api.dependency_overrides[get_layer_recognition] = lambda: service
    with TestClient(api) as client:
        response = client.get(
            f"/api/projects/{project.id}/source-layer-review/{endpoint}"
        )
    assert response.status_code == (404 if failure == "missing_project" else 400)
    if failure == "semantic_source":
        assert (
            response.json()["detail"]["message"]
            == "Семантические сведения принадлежат другому снимку"
        )
    assert service._jobs == {} and not service.cache.exists()
    assert provider.calls == 0


def test_event_stream_uses_desktop_cookie_and_rejects_foreign_origin(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    tmp_path: Path,
) -> None:
    service, provider, layers = observation
    project = Project(name="Проверка доступа к потоку", layers=layers)
    application = Mock(spec=ProjectApplication)
    application.get.return_value = project
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    api.dependency_overrides[get_layer_recognition] = lambda: service
    (tmp_path / "index.html").write_text("<title>Проверка SSE</title>")
    origin, secret = "http://127.0.0.1:18327", "a" * 43
    desktop = create_desktop_web(api, tmp_path, origin=origin, secret=secret)
    path = f"/api/projects/{project.id}/source-layer-review/events"
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
    assert "data: null\n" in response.text
    assert secret not in response.text
    application.get.assert_called_once_with(project.id, lightweight=True)
    assert provider.calls == 0


def test_running_event_stream_disconnect_unblocks_watch_without_model_or_source_change(
    observation: tuple[LayerRecognitionService, NeverRequestedProvider, list[Layer]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, provider, layers = observation
    key = service.cache_key(provider.name, SOURCE_SHA, layers)
    running = result_for(layers, provider)
    running.status = "running"
    running.processed_count = 1
    service._jobs[key] = running
    project = Project(
        name="Отключение потока распознавания",
        layers=layers,
        source_file=SourceFile(
            name="Saved capture.dwg",
            content_sha256=SOURCE_SHA,
            size=12,
            imported_at="2026-10-07T00:00:00Z",
            dxf_version="AutoCAD live",
            units="m",
            entity_count=4,
        ),
    )
    source_before = project.model_dump_json()
    job_before = running.model_dump_json()
    application = Mock(spec=ProjectApplication)
    application.get.return_value = project
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    api.dependency_overrides[get_layer_recognition] = lambda: service
    cancelled: list[Event] = []
    cancel_watch = service.cancel_watch

    def record_cancel(stop: Event) -> None:
        cancelled.append(stop)
        cancel_watch(stop)

    monkeypatch.setattr(service, "cancel_watch", record_cancel)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        initial, waiting, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        queue: asyncio.Queue[Message] = asyncio.Queue()
        frames: list[Message] = []
        wait_for = service._changes.wait_for
        watch = service._watch

        def track_wait(
            predicate: Callable[[], bool], timeout: float | None = None
        ) -> bool:
            loop.call_soon_threadsafe(waiting.set)
            return wait_for(predicate, timeout)

        def track_watch(
            key: str,
            initial: LayerRecognition | None,
            stop: Event,
            keepalive_seconds: float,
        ) -> Iterator[LayerRecognition | None]:
            try:
                yield from watch(key, initial, stop, keepalive_seconds)
            finally:
                loop.call_soon_threadsafe(finished.set)

        monkeypatch.setattr(service._changes, "wait_for", track_wait)
        monkeypatch.setattr(service, "_watch", track_watch)

        async def receive() -> Message:
            return await queue.get()

        async def send(frame: Message) -> None:
            frames.append(frame)
            if (
                frame["type"] == "http.response.body"
                and b"event: recognition" in frame["body"]
            ):
                initial.set()

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/api/projects/{project.id}/source-layer-review/events",
            "raw_path": f"/api/projects/{project.id}/source-layer-review/events".encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50001),
        }
        await queue.put({"type": "http.request", "body": b"", "more_body": False})
        request = asyncio.create_task(api(scope, receive, send))
        try:
            await asyncio.wait_for(initial.wait(), timeout=2)
            # Отключаемся именно после входа реального наблюдателя в Condition.
            await asyncio.wait_for(waiting.wait(), timeout=2)
            assert not finished.is_set() and not cancelled
            await queue.put({"type": "http.disconnect"})
            await asyncio.wait_for(request, timeout=2)
            await asyncio.wait_for(finished.wait(), timeout=2)
            assert len(cancelled) == 1 and cancelled[0].is_set()
            assert frames[0]["status"] == 200
            bodies = b"".join(
                frame.get("body", b"")
                for frame in frames
                if frame["type"] == "http.response.body"
            )
            assert bodies.count(b"event: recognition\n") == 1
            assert b'"status":"running"' in bodies
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    asyncio.run(scenario())
    application.get.assert_called_once_with(project.id, lightweight=True)
    assert project.model_dump_json() == source_before
    assert service._jobs[key].model_dump_json() == job_before
    assert provider.calls == 0 and not service.cache.exists()
