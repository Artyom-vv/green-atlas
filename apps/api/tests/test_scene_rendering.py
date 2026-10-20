import hashlib
import json
from contextlib import contextmanager
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from app.geometry.contracts import GeometrySnapshot
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.rendering import blender_runtime
from app.rendering.application import SceneRendering
from app.rendering.contracts import RenderCamera, SceneRenderRequest
from app.rendering.packet import display_meshes
from app.rendering.routes import get_scene_rendering, router


@pytest.fixture
def rendering(tmp_path, monkeypatch):
    project = Project(
        name="Second arbitrary kit",
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "id": "unmeasured-building",
                        "properties": {"kind": "building"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [100, 200],
                                    [110, 200],
                                    [110, 210],
                                    [100, 210],
                                    [100, 200],
                                ],
                                [
                                    [102, 202],
                                    [102, 204],
                                    [104, 204],
                                    [104, 202],
                                    [102, 202],
                                ],
                            ],
                        },
                    }
                ],
            }
        ),
        plan=Plan(
            objects=[
                PlanObject(kind="tree", x=105, y=205, radius=2),
                PlanObject(kind="shrub", x=107, y=208, radius=1),
            ]
        ),
    )
    service = SceneRendering(Mock(get=Mock(return_value=project)), tmp_path / "renders")
    monkeypatch.setattr("app.rendering.application.blender_binary", lambda: "blender")
    monkeypatch.setattr(service.executor, "submit", Mock())
    monkeypatch.setattr(
        "app.rendering.application.prepare_appearance", lambda *_: {"profile": "test"}
    )
    yield service, project
    service.close()


def request_for(service, project):
    scene = service.scene.from_project(project, 20)
    return SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=scene.content_sha256,
        horizon_year=20,
        camera=RenderCamera(
            position=(10, 20, 30),
            quaternion=(0, 0, 0, 1),
            fov_y_deg=42,
            aspect=1.5,
            near=0.08,
            far=20000,
        ),
    )


def test_packet_preserves_camera_all_plants_and_polygon_holes_without_mutating_project(
    rendering,
):
    service, project = rendering
    before = project.model_dump_json()
    request = request_for(service, project)
    job = service.create(project.id, request)
    packet = json.loads(
        (service._root(job.id) / "scene.json").read_text(encoding="utf-8")
    )
    assert packet["camera"] == request.camera.model_dump(mode="json")
    assert packet["width"] == 1280 and packet["height"] == 853
    assert {p["object_id"] for p in packet["scene"]["objects"]} == {
        p.id for p in project.plan.objects
    }
    assert {p["kind"] for p in packet["scene"]["objects"]} == {"tree", "shrub"}
    row = packet["meshes"][0]
    assert row["height_status"] == "missing"
    assert max(v[2] for v in row["vertices"]) == 0.012
    area = sum(
        Polygon([row["vertices"][i][:2] for i in face]).area for face in row["faces"]
    )
    assert area == pytest.approx(96)
    merged = packet["flat_building_surfaces"][0]
    merged_area = sum(
        Polygon([merged["vertices"][i][:2] for i in face]).area
        for face in merged["faces"]
    )
    assert merged_area == pytest.approx(96)
    for face in row["faces"]:
        a, b, c = [row["vertices"][i] for i in face]
        assert (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) > 0
    assert row["height_m"] == 0 and row["footprint_rings"] == []
    assert project.model_dump_json() == before


def test_stale_scene_rejected_before_job_is_created(rendering):
    service, project = rendering
    request = request_for(service, project)
    project.plan.objects[0].x += 1
    with pytest.raises(ValueError, match="Сцена изменилась"):
        service.create(project.id, request)
    assert not service._root(request.request_id).exists()


def test_accepted_source_packet_tamper_fails_before_blender_or_project_access(
    rendering, monkeypatch
):
    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    root = service._root(job.id)
    source_path = root / "scene.json"
    accepted_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    packet = json.loads(source_path.read_text(encoding="utf-8"))
    packet["scene"]["objects"] = []
    source_path.write_text(json.dumps(packet))
    before = project.model_dump_json()
    repository_calls = list(service.scene.repository.method_calls)
    launch = Mock()
    enrich = Mock()
    monkeypatch.setattr("app.rendering.application.subprocess.Popen", launch)
    monkeypatch.setattr("app.rendering.application.prepare_buildings", enrich)

    service._run(job, "blender", Event(), accepted_sha)

    result = service.get(project.id, job.id)
    assert result.status == "failed"
    assert "Исходный пакет рендера изменился" in result.message
    launch.assert_not_called()
    enrich.assert_not_called()
    assert project.model_dump_json() == before
    assert service.scene.repository.method_calls == repository_calls
    assert not (root / "render-scene.json").exists()
    assert not (root / "image.png").exists()
    assert not (root / "receipt.json").exists()


def test_idempotence_project_ownership_and_cancel(rendering):
    service, project = rendering
    request = request_for(service, project)
    job = service.create(project.id, request)
    assert service.create(project.id, request) == job
    service.executor.submit.assert_called_once()
    with pytest.raises(KeyError):
        service.get("another-project", job.id)
    with pytest.raises(ValueError, match="другого ракурса"):
        service.create(project.id, request.model_copy(update={"resolution": 512}))
    with pytest.raises(ValueError, match="ещё не готов"):
        service.file(project.id, job.id, "image.png")
    assert service.cancel(project.id, job.id).status == "cancelled"
    assert service.cancellations[str(job.id)].is_set()
    service._run(job, "blender", service.cancellations[str(job.id)], "unused")
    assert service.get(project.id, job.id).status == "cancelled"


@pytest.mark.parametrize(
    "invalid", ["corrupt_image", "wrong_receipt", "process_failure", None]
)
def test_worker_receipt_and_failure_isolation(rendering, monkeypatch, invalid):
    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    root = service._root(job.id)
    packet_sha = hashlib.sha256((root / "scene.json").read_bytes()).hexdigest()

    def run_process(*args, **kwargs):
        image = (
            b"\x89PNG\r\n\x1a\n"
            + b"\0" * 8
            + job.width.to_bytes(4, "big")
            + job.height.to_bytes(4, "big")
        )
        (root / "image.png").write_bytes(
            b"broken" if invalid == "corrupt_image" else image
        )
        (root / "renderer-receipt.json").write_text(
            json.dumps(
                {
                    "packet_sha256": "wrong"
                    if invalid == "wrong_receipt"
                    else hashlib.sha256(
                        (root / "render-scene.json").read_bytes()
                    ).hexdigest(),
                    "renderer_script_sha256": json.loads(
                        (root / "scene.json").read_bytes()
                    )["renderer_script_sha256"],
                }
            )
        )
        return Mock(
            poll=Mock(return_value=1 if invalid == "process_failure" else 0),
            returncode=1 if invalid == "process_failure" else 0,
        )

    monkeypatch.setattr("app.rendering.application.subprocess.Popen", run_process)
    before = project.model_dump_json()
    service._run(job, "blender", Event(), packet_sha)
    result = service.get(project.id, job.id)
    assert result.status == ("failed" if invalid else "completed")
    assert project.model_dump_json() == before
    if invalid is None:
        receipt = json.loads(
            service.file(project.id, job.id, "receipt.json").read_text(encoding="utf-8")
        )
        assert receipt["project_id"] == project.id
        assert receipt["scene_sha256"] == job.scene_sha256
        assert (
            receipt["image_sha256"]
            == hashlib.sha256((root / "image.png").read_bytes()).hexdigest()
        )


def test_http_contract_rejects_invalid_camera_and_foreign_result(rendering):
    service, project = rendering
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scene_rendering] = lambda: service
    with TestClient(app) as client:
        request = request_for(service, project).model_dump(mode="json")
        base = f"/api/projects/{project.id}/scene-renders"
        response = client.post(base, json=request)
        assert response.status_code == 202
        job_id = response.json()["id"]
        assert (
            client.get(f"/api/projects/other/scene-renders/{job_id}").status_code == 404
        )
        request["camera"]["quaternion"] = [0, 0, 0, 0]
        assert client.post(base, json=request).status_code == 422
        assert client.post(f"{base}/{job_id}/cancel").json()["status"] == "cancelled"


def test_dll_restore_failure_stops_blender_and_preserves_the_project(
    rendering, monkeypatch
):
    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    child = Mock(poll=Mock(return_value=None))

    @contextmanager
    def boundary(*, external):
        assert external
        yield
        raise OSError("DLL restore failed")

    monkeypatch.setattr(blender_runtime, "child_dll_search", boundary)
    monkeypatch.setattr(blender_runtime.subprocess, "Popen", Mock(return_value=child))
    before = project.model_dump_json()
    service._run(
        job,
        "blender",
        Event(),
        hashlib.sha256((service._root(job.id) / "scene.json").read_bytes()).hexdigest(),
    )
    child.terminate.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)
    result = service.get(project.id, job.id)
    assert result.status == "failed"
    assert result.message == "DLL restore failed"
    assert project.model_dump_json() == before


def test_cancel_terminates_the_running_blender_process(rendering, monkeypatch):
    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    event = service.cancellations[str(job.id)]
    process = Mock(poll=Mock(return_value=None))

    def launch(*args, **kwargs):
        service.cancel(project.id, job.id)
        return process

    monkeypatch.setattr("app.rendering.application.subprocess.Popen", launch)
    service._run(
        job,
        "blender",
        event,
        hashlib.sha256((service._root(job.id) / "scene.json").read_bytes()).hexdigest(),
    )
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    assert service.get(project.id, job.id).status == "cancelled"


def test_surface_elevation_uses_confirmed_terrain_and_preserves_explicit_bases(
    rendering,
):
    from app.scene.contracts import SceneVerticalPrimitive

    service, project = rendering
    scene = service.scene.from_project(project, 20)
    scene.vertical_primitives = [
        SceneVerticalPrimitive(
            primitive_id="terrain",
            primitive_type="surface_mesh",
            vertices=[[-100, -100, 150], [100, -100, 150], [0, 100, 150]],
            faces=[[0, 1, 2]],
            source_layer="survey",
            source_entity_type="MESH",
            source_file_units="m",
            unit_scale_to_m=1,
            vertical_evidence="explicit_xyz",
            terrain_mapping_status="confirmed",
        )
    ]
    assert min(v[2] for v in display_meshes(scene)[0]["vertices"]) == pytest.approx(
        150.012
    )
    scene.context_features[0].base_elevation_m = 170
    assert min(v[2] for v in display_meshes(scene)[0]["vertices"]) == pytest.approx(
        170.012
    )


def test_restart_preserves_completed_results_and_marks_abandoned_jobs(rendering):
    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    other = SceneRendering(service.scene.repository, service.directory)
    try:
        assert other.get(project.id, job.id).status == "failed"
    finally:
        other.close()


def test_status_polling_does_not_reload_or_advance_the_project(rendering, monkeypatch):
    from app.main import app

    service, project = rendering
    job = service.create(project.id, request_for(service, project))
    metadata = Mock(
        side_effect=AssertionError("Render polling must not load project metadata")
    )
    monkeypatch.setattr("app.main.get_application", metadata)
    app.dependency_overrides[get_scene_rendering] = lambda: service
    try:
        with TestClient(app) as client:
            base = f"/api/projects/{project.id}/scene-renders/{job.id}"
            response = client.get(base)
            assert response.status_code == 200
            assert "X-Project-State-Version" not in response.headers
            assert client.post(base + "/cancel").status_code == 200
        metadata.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_scene_rendering, None)
