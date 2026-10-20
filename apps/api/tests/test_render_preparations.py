"""The browser and Blender consume one frozen visual composition."""

import base64
import hashlib
import json
import math
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import Point, box, mapping

from app.dxf_import.contracts import SourceFile
from app.dxf_import.layers.contracts import Layer
from app.geometry.contracts import GeometrySnapshot
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.rendering.application import (
    LEGACY_RENDERER_SCRIPTS,
    RENDERER_SCRIPTS,
    SceneRendering,
    _preview,
)
from app.rendering.contracts import (
    RenderCamera,
    SceneRenderPreparationRequest,
    SceneRenderRequest,
)
from app.rendering.routes import get_scene_rendering, router


@pytest.fixture
def prepared_service(tmp_path, monkeypatch):
    rows = []
    layers = []
    for name, kind, category, geometry, entity in (
        ("house", "building", "building", box(100, 200, 110, 210), "HATCH"),
        ("path", "road", "footway", box(110, 200, 120, 210), "HATCH"),
        ("grass", "lawn", "lawn", box(120, 200, 130, 210), "HATCH"),
        ("tree", "existing_green", "tree", Point(125, 205).buffer(0.21), "CIRCLE"),
    ):
        layers.append(
            Layer(
                id=name,
                source_name=name,
                suggested_kind=kind,
                mapped_kind=kind,
                category=category,
                mapping_confirmed=True,
                object_count=1,
                color="#000000",
            )
        )
        rows.append(
            {
                "id": name,
                "geometry": mapping(geometry),
                "properties": {
                    "kind": kind,
                    "source_layer": name,
                    "entity_type": entity,
                    "source_handle": name,
                    "source_instance_chain": ["tree-marker"] if name == "tree" else [],
                    "source_closed_path": True,
                },
            }
        )
    project = Project(
        id="site-anywhere",
        name="No address assumptions",
        geometry_version=4,
        layers=layers,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": rows}
        ),
        plan=Plan(objects=[PlanObject(kind="tree", x=125, y=205, radius=2)]),
    )
    service = SceneRendering(Mock(get=Mock(return_value=project)), tmp_path / "renders")
    monkeypatch.setattr(service.executor, "submit", Mock())
    monkeypatch.setattr("app.rendering.application.blender_binary", lambda: "blender")
    monkeypatch.setattr(
        "app.rendering.application.prepare_preview_assets",
        lambda *_args, **_kwargs: {
            "assets": [],
            "materials": [],
            "plants": [],
            "context_plants": [],
        },
    )
    monkeypatch.setattr(
        "app.rendering.application.prepare_appearance",
        lambda *_: {
            "profile": "test",
            "bundle_sha256": "c" * 64,
            "files": [{"path": "/private/secret-model.blend", "sha256": "d" * 64}],
            "context_plants": [
                {
                    "object_id": "existing-tree/tree-marker",
                    "asset_key": "illustrative-tree",
                    "path": "/private/secret-model.blend",
                }
            ],
        },
    )
    snapshot = service.scene.from_project(project, 20)
    request = SceneRenderPreparationRequest(
        request_id=uuid4(),
        scene_sha256=snapshot.content_sha256,
        horizon_year=20,
        camera=RenderCamera(
            position=(0, 10, 0),
            quaternion=(0, 0, 0, 1),
            fov_y_deg=42,
            aspect=16 / 9,
            near=0.08,
            far=20000,
        ),
    )
    yield service, project, request
    service.close()


def test_missing_scene_sha_fails_before_assets_or_worker_with_valid_preview(
    prepared_service, monkeypatch
):
    from app.rendering import application

    service, project, request = prepared_service
    snapshot = service.scene.from_project(project, request.horizon_year)
    snapshot.content_sha256 = None
    monkeypatch.setattr(service.scene, "from_project", Mock(return_value=snapshot))
    # Валидный preview SHA исключает отказ прежней проверки expected_scene вместо guard.
    preview = SimpleNamespace(
        scene={
            "content_sha256": request.scene_sha256,
            "coordinate_origin": snapshot.coordinate_origin,
        },
        environment=SimpleNamespace(provenance={}),
    )
    read_view = Mock(return_value=preview)
    monkeypatch.setattr(service.view, "read", read_view)
    blocked = {}
    for name in (
        "prepare_buildings",
        "prepare_environment",
        "prepare_appearance",
        "prepare_preview_assets",
        "blender_binary",
    ):
        blocked[name] = Mock(side_effect=AssertionError("Missing SHA must fail first"))
        monkeypatch.setattr(application, name, blocked[name])
    launch = Mock(side_effect=AssertionError("Worker must not start without scene SHA"))
    monkeypatch.setattr(application.subprocess, "Popen", launch)
    with pytest.raises(ValueError, match="Сцена изменилась"):
        service.prepare(project.id, request.model_copy(update={"preview_scene": True}))
    read_view.assert_not_called()
    for operation in blocked.values():
        operation.assert_not_called()
    launch.assert_not_called()
    service.executor.submit.assert_not_called()
    root = service._preparation_root(request.request_id)
    assert not root.exists()
    assert not root.with_name(root.name + ".pending").exists()


def test_preparation_matches_frozen_composition_and_never_exposes_asset_paths(
    prepared_service,
):
    service, project, request = prepared_service
    before = project.model_dump_json()
    preparation = service.prepare(project.id, request)
    root = service._preparation_root(preparation.id)
    packet_data = (root / "render-scene.json").read_bytes()
    packet = json.loads(packet_data)
    assert preparation.sha256 == hashlib.sha256(packet_data).hexdigest()
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert set(manifest["renderer_sha256"]) == set(RENDERER_SCRIPTS)
    assert packet["renderer_script_sha256"] == manifest["renderer_sha256"]
    assert all(
        digest == hashlib.sha256((root / "renderer" / name).read_bytes()).hexdigest()
        for name, digest in manifest["renderer_sha256"].items()
    )
    assert preparation.preview.model_dump(mode="json") == _preview(packet)
    assert len(preparation.preview.context_trees) == 1
    tree = preparation.preview.context_trees[0]
    assert (tree.local_x, tree.local_y, tree.base_elevation_m) == (0, 0, 0)
    assert (
        tree.rotation_z
        == int(hashlib.sha256(tree.object_id.encode()).hexdigest()[:8], 16)
        / 0xFFFFFFFF
        * math.tau
    )
    assert tree.asset_key == "illustrative-tree"
    assert preparation.summary.context_tree_count == 1
    assert {row.kind for row in preparation.preview.meshes} == {
        "building",
        "sidewalk",
        "lawn",
        "facade_glass",
        "facade_frame",
        "facade_joint",
        "facade_plinth",
    }
    assert preparation.preview.suppressed_source_ids == [
        "grass",
        "house",
        "path",
        "tree",
    ]
    assert (
        packet["scene"]["data_gaps"]
        == service.scene.from_project(project, 20).data_gaps
    )
    assert "secret-model" not in preparation.model_dump_json()
    assert "texture_root" not in preparation.model_dump_json()
    assert project.model_dump_json() == before
    assert service.prepare(project.id, request) == preparation
    assert service.get_preparation(project.id, preparation.id) == preparation


@pytest.fixture
def preview_asset_preparation(prepared_service, monkeypatch):
    service, project, request = prepared_service
    content = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    digest = hashlib.sha256(content).hexdigest()

    def assets(_packet, root, **_kwargs):
        relative = f"preview-assets/{digest}.png"
        (root / "preview-assets").mkdir()
        (root / relative).write_bytes(content)
        return {
            "assets": [
                {
                    "id": digest,
                    "sha256": digest,
                    "relative_path": relative,
                    "size_bytes": len(content),
                    "mime_type": "image/png",
                    "kind": "texture",
                }
            ],
            "materials": [],
            "plants": [],
            "context_plants": [],
        }

    monkeypatch.setattr("app.rendering.application.prepare_preview_assets", assets)
    preparation = service.prepare(project.id, request)
    return service, project, preparation, digest, content


def test_preview_asset_http_is_exact_and_project_scoped(preview_asset_preparation):
    service, project, preparation, digest, content = preview_asset_preparation
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scene_rendering] = lambda: service
    with TestClient(app) as client:
        path = f"/api/projects/{project.id}/scene-render-preparations/{preparation.id}/assets/{digest}"
        response = client.get(path)
        assert response.status_code == 200
        assert response.content == content
        assert response.headers["etag"] == f'"{digest}"'
        assert response.headers["content-type"] == "image/png"
        assert (
            client.get(path.replace(project.id, "foreign-project")).status_code == 404
        )
        assert client.get(path.replace(digest, "0" * 64)).status_code == 404


@pytest.mark.parametrize("alteration", ["bytes", "path", "preview", "symlink"])
def test_preview_asset_rejects_mutation_or_path_escape(
    preview_asset_preparation, alteration
):
    service, project, preparation, digest, content = preview_asset_preparation
    root = service._preparation_root(preparation.id)
    path = root / "preview-assets" / f"{digest}.png"
    if alteration == "bytes":
        path.write_bytes(content + b"changed")
    elif alteration == "preview":
        (root / "preview.json").write_bytes(b"{}")
    elif alteration == "symlink":
        other = root.parent / "other-image.png"
        other.write_bytes(content)
        path.unlink()
        try:
            path.symlink_to(other)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create file symlinks")
            raise
    else:
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["preview_assets"][0]["relative_path"] = "../../other-image.png"
        (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        service.preview_asset(project.id, preparation.id, digest)


def test_prepared_render_uses_new_camera_and_frozen_composition_without_reenrichment(
    prepared_service, monkeypatch
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    frozen_root = service._preparation_root(preparation.id)
    frozen = (frozen_root / "render-scene.json").read_bytes()
    # A finished preparation represents the currently visible scene. Applying
    # another location may start refresh, but must not invalidate this scene.
    sidecar = service.directory / "georeferences" / f"{project.id}.json"
    sidecar.parent.mkdir()
    sidecar.write_text('{"new_location_after_preparation":true}')
    camera = request.camera.model_copy(
        update={"position": (20, 1.7, -30), "aspect": 1.5}
    )
    render_request = SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=request.scene_sha256,
        horizon_year=request.horizon_year,
        camera=camera,
        preparation_id=preparation.id,
        preparation_sha256=preparation.sha256,
    )
    for name in ("prepare_buildings", "prepare_environment", "prepare_appearance"):
        monkeypatch.setattr(
            f"app.rendering.application.{name}",
            Mock(
                side_effect=AssertionError(
                    "Frozen preparation must not be enriched again"
                )
            ),
        )
    job = service.create(project.id, render_request)
    root = service._root(job.id)
    source = json.loads((root / "scene.json").read_bytes())
    assert source["camera"] == camera.model_dump(mode="json")
    assert (source["width"], source["height"]) == (1280, 853)
    pinned = json.loads(frozen)
    for key in (
        "scene",
        "meshes",
        "building_display_meshes",
        "environment_surfaces",
        "environment_curbs",
        "environment_trees",
        "environment_buildings",
        "appearance",
    ):
        assert source[key] == pinned[key]
    for name in RENDERER_SCRIPTS:
        assert (root / "renderer" / name).read_bytes() == (
            frozen_root / "renderer" / name
        ).read_bytes()
    expected_scripts = json.loads((frozen_root / "manifest.json").read_bytes())[
        "renderer_sha256"
    ]
    assert source["renderer_script_sha256"] == expected_scripts

    def launch(*_args, **_kwargs):
        (root / "image.png").write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"\0" * 8
            + job.width.to_bytes(4, "big")
            + job.height.to_bytes(4, "big")
        )
        (root / "renderer-receipt.json").write_text(
            json.dumps(
                {
                    "packet_sha256": hashlib.sha256(
                        (root / "render-scene.json").read_bytes()
                    ).hexdigest(),
                    "renderer_script_sha256": expected_scripts,
                }
            )
        )
        return Mock(poll=Mock(return_value=0), returncode=0)

    monkeypatch.setattr("app.rendering.application.subprocess.Popen", launch)
    service._run(
        job,
        "blender",
        Event(),
        hashlib.sha256((root / "scene.json").read_bytes()).hexdigest(),
    )
    assert service.get(project.id, job.id).status == "completed"
    receipt = json.loads((root / "receipt.json").read_bytes())
    assert receipt["preparation_id"] == str(preparation.id)
    assert receipt["preparation_sha256"] == preparation.sha256
    assert (
        receipt["preparation_source_packet_sha256"]
        == hashlib.sha256((frozen_root / "scene.json").read_bytes()).hexdigest()
    )
    assert (frozen_root / "render-scene.json").read_bytes() == frozen


@pytest.mark.parametrize(
    "name",
    [
        "render-scene.json",
        "scene.json",
        "preview.json",
        "request.json",
        "renderer/render_style.py",
        "renderer/lawn_instances.py",
        "renderer/lawn_scatter.py",
    ],
)
def test_tampered_preparation_is_rejected_before_job_creation(prepared_service, name):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    path = service._preparation_root(preparation.id) / name
    path.write_bytes(path.read_bytes() + b" ")
    render_request = SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=request.scene_sha256,
        horizon_year=20,
        camera=request.camera,
        preparation_id=preparation.id,
        preparation_sha256=preparation.sha256,
    )
    with pytest.raises(ValueError, match="изменилась"):
        service.create(project.id, render_request)
    assert not service._root(render_request.request_id).exists()


@pytest.mark.parametrize("name", ["lawn_instances.py", "lawn_scatter.py"])
def test_preparation_requires_each_accepted_lawn_helper(prepared_service, name):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    (service._preparation_root(preparation.id) / "renderer" / name).unlink()
    with pytest.raises(ValueError, match="повреждена"):
        service.get_preparation(project.id, preparation.id)


@pytest.mark.parametrize("change", ["partial", "arbitrary", "invalid_digest"])
def test_preparation_renderer_manifest_only_accepts_exact_script_sets(
    prepared_service, change
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    path = service._preparation_root(preparation.id) / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if change == "partial":
        del manifest["renderer_sha256"]["lawn_scatter.py"]
    elif change == "arbitrary":
        manifest["renderer_sha256"]["../outside.py"] = "0" * 64
    else:
        manifest["renderer_sha256"]["lawn_instances.py"] = 123
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Код подготовленного рендера повреждён"):
        service.get_preparation(project.id, preparation.id)


def test_prepared_job_uses_frozen_four_scripts_without_reading_live_helpers(
    prepared_service, monkeypatch, tmp_path
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    frozen = service._preparation_root(preparation.id) / "renderer"
    expected = {name: (frozen / name).read_bytes() for name in RENDERER_SCRIPTS}
    # The application now lives beside an unrelated installation with none of
    # the accepted scripts. A prepared job must use only its frozen renderer.
    monkeypatch.setattr(
        "app.rendering.application.__file__", str(tmp_path / "other.py")
    )
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
            preparation_id=preparation.id,
            preparation_sha256=preparation.sha256,
        ),
    )
    root = service._root(job.id)
    assert {
        name: (root / "renderer" / name).read_bytes() for name in expected
    } == expected
    assert json.loads((root / "scene.json").read_bytes())["renderer_script_sha256"] == {
        name: hashlib.sha256(data).hexdigest() for name, data in expected.items()
    }


def _fake_completed_renderer(root, job, *, scripts=None):
    (root / "image.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\0" * 8
        + job.width.to_bytes(4, "big")
        + job.height.to_bytes(4, "big")
    )
    receipt = {
        "packet_sha256": hashlib.sha256(
            (root / "render-scene.json").read_bytes()
        ).hexdigest()
    }
    if scripts is not None:
        receipt["renderer_script_sha256"] = scripts
    (root / "renderer-receipt.json").write_text(json.dumps(receipt))
    return Mock(poll=Mock(return_value=0), returncode=0)


@pytest.mark.parametrize("previously_accepted_job", [False, True])
def test_legacy_two_script_preparation_runs_its_exact_old_worker_without_helpers(
    prepared_service, monkeypatch, tmp_path, previously_accepted_job
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    frozen_root = service._preparation_root(preparation.id)
    renderer = frozen_root / "renderer"
    # Represent an explicitly accepted old preparation. It must not be silently
    # upgraded to the new four-script code just because the app was updated.
    old_worker = b"import render_style\n# accepted pre-lawn-renderer worker\n"
    (renderer / "blender_worker.py").write_bytes(old_worker)
    for name in ("lawn_instances.py", "lawn_scatter.py"):
        (renderer / name).unlink()
    manifest_path = frozen_root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    scripts = {
        name: hashlib.sha256((renderer / name).read_bytes()).hexdigest()
        for name in LEGACY_RENDERER_SCRIPTS
    }
    # Actual pre-upgrade packets had no renderer map. Construct a complete
    # accepted legacy fixture, including its packet/preview SHA, rather than
    # permitting a new four-script preparation to be edited into the old form.
    packet_path = frozen_root / "render-scene.json"
    packet = json.loads(packet_path.read_bytes())
    del packet["renderer_script_sha256"]
    packet_data = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()
    packet_path.write_bytes(packet_data)
    preparation = preparation.model_copy(
        update={"sha256": hashlib.sha256(packet_data).hexdigest()}
    )
    preview_data = preparation.model_dump_json().encode()
    (frozen_root / "preview.json").write_bytes(preview_data)
    manifest["packet_sha256"] = preparation.sha256
    manifest["preview_sha256"] = hashlib.sha256(preview_data).hexdigest()
    manifest["renderer_sha256"] = scripts
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        "app.rendering.application.__file__", str(tmp_path / "absent.py")
    )
    assert service.get_preparation(project.id, preparation.id) == preparation
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
            preparation_id=preparation.id,
            preparation_sha256=preparation.sha256,
        ),
    )
    root = service._root(job.id)
    assert {path.name for path in (root / "renderer").iterdir()} == set(scripts)
    assert (root / "renderer" / "blender_worker.py").read_bytes() == old_worker
    source = json.loads((root / "scene.json").read_bytes())
    assert source["renderer_script_sha256"] == scripts
    if previously_accepted_job:
        # Jobs accepted by the previous runtime carried the script SHA map in
        # their preparation alone. They must still run that same frozen pair.
        del source["renderer_script_sha256"]
        (root / "scene.json").write_text(json.dumps(source))
    accepted = (root / "scene.json").read_bytes()

    def launch(args, **_kwargs):
        assert args[args.index("--python") + 1] == str(
            root / "renderer" / "blender_worker.py"
        )
        assert (root / "renderer" / "blender_worker.py").read_bytes() == old_worker
        return _fake_completed_renderer(root, job, scripts=scripts)

    monkeypatch.setattr("app.rendering.application.subprocess.Popen", launch)
    service._run(job, "blender", Event(), hashlib.sha256(accepted).hexdigest())
    assert service.get(project.id, job.id).status == "completed"
    assert (
        json.loads((root / "receipt.json").read_bytes())["renderer_script_sha256"]
        == scripts
    )


@pytest.mark.parametrize("change", ["replace_digest", "downgrade"])
def test_new_preparation_cannot_rebind_renderer_without_changing_packet_sha(
    prepared_service, change
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    root = service._preparation_root(preparation.id)
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if change == "replace_digest":
        helper = root / "renderer" / "lawn_scatter.py"
        helper.write_bytes(helper.read_bytes() + b"# changed code")
        manifest["renderer_sha256"]["lawn_scatter.py"] = hashlib.sha256(
            helper.read_bytes()
        ).hexdigest()
    else:
        manifest["renderer_sha256"] = {
            name: manifest["renderer_sha256"][name] for name in LEGACY_RENDERER_SCRIPTS
        }
    path.write_text(json.dumps(manifest))
    with pytest.raises(
        ValueError, match="Код рендера не связан с подготовленной сценой"
    ):
        service.create(
            project.id,
            SceneRenderRequest(
                request_id=uuid4(),
                scene_sha256=request.scene_sha256,
                horizon_year=request.horizon_year,
                camera=request.camera,
                preparation_id=preparation.id,
                preparation_sha256=preparation.sha256,
            ),
        )


@pytest.mark.parametrize("name", ["lawn_instances.py", "lawn_scatter.py"])
@pytest.mark.parametrize("alteration", ["missing", "changed"])
def test_job_rejects_accepted_helper_changes_before_blender(
    prepared_service, monkeypatch, name, alteration
):
    service, project, request = prepared_service
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
        ),
    )
    root = service._root(job.id)
    accepted = (root / "scene.json").read_bytes()
    scripts = json.loads(accepted)["renderer_script_sha256"]
    assert set(scripts) == set(RENDERER_SCRIPTS)
    path = root / "renderer" / name
    if alteration == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"# changed")
    launch = Mock(side_effect=AssertionError("Changed worker must not execute"))
    monkeypatch.setattr("app.rendering.application.subprocess.Popen", launch)
    service._run(job, "blender", Event(), hashlib.sha256(accepted).hexdigest())
    failed = service.get(project.id, job.id)
    assert failed.status == "failed"
    assert failed.message == "Код рендера изменился после принятия"
    launch.assert_not_called()
    assert not (root / "receipt.json").exists()


@pytest.mark.parametrize("alteration", ["missing", "partial", "changed", "extra"])
def test_job_requires_receipt_for_exact_accepted_four_scripts(
    prepared_service, monkeypatch, alteration
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
            preparation_id=preparation.id,
            preparation_sha256=preparation.sha256,
        ),
    )
    root = service._root(job.id)
    accepted = (root / "scene.json").read_bytes()
    scripts = json.loads(accepted)["renderer_script_sha256"]
    if alteration == "missing":
        scripts = None
    elif alteration == "partial":
        del scripts["lawn_scatter.py"]
    elif alteration == "changed":
        scripts["lawn_scatter.py"] = "0" * 64
    else:
        scripts["unexpected.py"] = "0" * 64
    monkeypatch.setattr(
        "app.rendering.application.subprocess.Popen",
        lambda *_args, **_kwargs: _fake_completed_renderer(root, job, scripts=scripts),
    )
    service._run(job, "blender", Event(), hashlib.sha256(accepted).hexdigest())
    failed = service.get(project.id, job.id)
    assert failed.status == "failed"
    assert failed.message == "Изображение собрано другим кодом рендера"
    assert not (root / "receipt.json").exists()


def test_foreign_project_wrong_sha_and_wrong_horizon_cannot_use_preparation(
    prepared_service,
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    with pytest.raises(KeyError):
        service.get_preparation("another", preparation.id)
    with pytest.raises(KeyError):
        service.prepare("another", request.model_copy(update={"request_id": uuid4()}))
    render_request = SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=request.scene_sha256,
        horizon_year=20,
        camera=request.camera,
        preparation_id=preparation.id,
        preparation_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="SHA"):
        service.create(project.id, render_request)
    other_scene = service.scene.from_project(project, 40)
    changed = render_request.model_copy(
        update={
            "request_id": uuid4(),
            "scene_sha256": other_scene.content_sha256,
            "horizon_year": 40,
            "preparation_sha256": preparation.sha256,
        }
    )
    with pytest.raises(ValueError, match="горизонту"):
        service.create(project.id, changed)


@pytest.mark.parametrize("revision", ["state", "geometry", "plan", "coordinates"])
def test_project_revision_changes_require_new_preparation(prepared_service, revision):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    if revision == "state":
        project.state_version += 1
    if revision == "geometry":
        project.geometry_version += 1
    if revision == "plan":
        project.plan.version += 1
    if revision == "coordinates":
        project.geometry.feature_collection["features"][0]["geometry"] = mapping(
            box(200, 200, 210, 210)
        )
    render_request = SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=request.scene_sha256,
        horizon_year=20,
        camera=request.camera,
        preparation_id=preparation.id,
        preparation_sha256=preparation.sha256,
    )
    with pytest.raises(ValueError, match="измени|изменился"):
        service.create(project.id, render_request)


def test_source_identity_change_requires_new_preparation(prepared_service):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    project.source_file = SourceFile(
        name="same-name.dwg",
        content_sha256="a" * 64,
        size=100,
        imported_at="2026-10-12",
        dxf_version="AutoCAD snapshot",
        units="m",
        entity_count=4,
    )
    scene = service.scene.from_project(project, 20)
    render = SceneRenderRequest(
        request_id=uuid4(),
        scene_sha256=scene.content_sha256,
        horizon_year=20,
        camera=request.camera,
        preparation_id=preparation.id,
        preparation_sha256=preparation.sha256,
    )
    with pytest.raises(ValueError, match="изменился"):
        service.create(project.id, render)


@pytest.mark.parametrize("change", ["missing_keys", "wrong_type", "renderer_path"])
def test_corrupt_manifest_is_rejected_with_controlled_http_error(
    prepared_service, change
):
    service, project, request = prepared_service
    preparation = service.prepare(project.id, request)
    path = service._preparation_root(preparation.id) / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if change == "missing_keys":
        manifest.pop("preview_sha256")
    if change == "wrong_type":
        manifest = []
    if change == "renderer_path":
        manifest["renderer_sha256"]["../unexpected"] = "0" * 64
    path.write_text(json.dumps(manifest))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scene_rendering] = lambda: service
    with TestClient(app) as client:
        assert (
            client.get(
                f"/api/projects/{project.id}/scene-render-preparations/{preparation.id}"
            ).status_code
            == 409
        )


def test_preparation_pair_and_camera_validation_and_http_ownership(prepared_service):
    service, project, request = prepared_service
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scene_rendering] = lambda: service
    with TestClient(app) as client:
        base = f"/api/projects/{project.id}/scene-render-preparations"
        response = client.post(base, json=request.model_dump(mode="json"))
        assert response.status_code == 200
        prepared = response.json()
        assert client.get(base + "/" + prepared["id"]).json() == prepared
        assert (
            client.get(
                f"/api/projects/foreign/scene-render-preparations/{prepared['id']}"
            ).status_code
            == 404
        )
        invalid = request.model_dump(mode="json")
        invalid["camera"]["quaternion"] = [0, 0, 0, 0]
        assert client.post(base, json=invalid).status_code == 422
        render = SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=20,
            camera=request.camera,
        ).model_dump(mode="json")
        render["preparation_id"] = prepared["id"]
        assert (
            client.post(
                f"/api/projects/{project.id}/scene-renders", json=render
            ).status_code
            == 422
        )
        render["preparation_sha256"] = prepared["sha256"]
        assert (
            client.post(
                f"/api/projects/{project.id}/scene-renders", json=render
            ).status_code
            == 202
        )


def test_repeated_preparation_id_cannot_silently_replace_camera(prepared_service):
    service, project, request = prepared_service
    prepared = service.prepare(project.id, request)
    with pytest.raises(ValueError, match="идентификатор"):
        service.prepare(
            project.id,
            request.model_copy(
                update={
                    "camera": request.camera.model_copy(
                        update={"position": (0, 1.7, 0)}
                    )
                }
            ),
        )
    assert service.get_preparation(project.id, prepared.id) == prepared


def test_new_preparation_id_resets_composition_without_changing_old_packet(
    prepared_service,
):
    service, project, request = prepared_service
    first = service.prepare(project.id, request)
    frozen = (service._preparation_root(first.id) / "render-scene.json").read_bytes()
    second = service.prepare(
        project.id, request.model_copy(update={"request_id": uuid4()})
    )
    assert second.id != first.id and second.sha256 != first.sha256
    assert second.preview == first.preview
    assert (
        service._preparation_root(first.id) / "render-scene.json"
    ).read_bytes() == frozen


@pytest.mark.parametrize(
    "stage", ["prepare_buildings", "prepare_environment", "prepare_preview_assets"]
)
@pytest.mark.parametrize("change", ["create", "replace", "remove"])
def test_location_change_during_preparation_never_publishes_mixed_scene(
    prepared_service, monkeypatch, stage, change
):
    from app.rendering import application

    service, project, request = prepared_service
    first = service.prepare(project.id, request)
    first_path = service._preparation_root(first.id) / "render-scene.json"
    first_bytes = first_path.read_bytes()
    sidecar = service.directory / "georeferences" / f"{project.id}.json"
    sidecar.parent.mkdir()
    if change != "create":
        sidecar.write_text('{"test_frame":"old"}')
    enrich = getattr(application, stage)

    def changing(*args, **kwargs):
        result = enrich(*args, **kwargs)
        if change == "remove":
            sidecar.unlink()
        else:
            sidecar.write_text('{"test_frame":"new"}')
        return result

    monkeypatch.setattr(application, stage, changing)
    retry = request.model_copy(update={"request_id": uuid4()})
    with pytest.raises(ValueError, match="Привязка к карте изменилась"):
        service.prepare(project.id, retry)
    failed_root = service._preparation_root(retry.request_id)
    assert not failed_root.exists()
    assert not failed_root.with_name(failed_root.name + ".pending").exists()
    assert first_path.read_bytes() == first_bytes
    assert service.get_preparation(project.id, first.id) == first
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
            preparation_id=first.id,
            preparation_sha256=first.sha256,
        ),
    )
    assert job.status == "queued"


def test_legacy_render_location_race_fails_before_packet_or_worker_launch(
    prepared_service, monkeypatch
):
    from app.rendering import application

    service, project, request = prepared_service
    job = service.create(
        project.id,
        SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=request.scene_sha256,
            horizon_year=request.horizon_year,
            camera=request.camera,
        ),
    )
    sidecar = service.directory / "georeferences" / f"{project.id}.json"
    sidecar.parent.mkdir()
    sidecar.write_text('{"test_frame":"old"}')
    enrich = application.prepare_buildings

    def changing(*args, **kwargs):
        result = enrich(*args, **kwargs)
        sidecar.write_text('{"test_frame":"new"}')
        return result

    monkeypatch.setattr(application, "prepare_buildings", changing)
    launch = Mock(
        side_effect=AssertionError("Mixed composition must not launch Blender")
    )
    monkeypatch.setattr(application.subprocess, "Popen", launch)
    root = service._root(job.id)
    digest = hashlib.sha256((root / "scene.json").read_bytes()).hexdigest()
    service._run(job, "blender", Event(), digest)
    failed = service.get(project.id, job.id)
    assert failed.status == "failed"
    assert "Привязка к карте изменилась" in failed.message
    assert not (root / "render-scene.json").exists()
    launch.assert_not_called()


def test_empty_scene_cannot_publish_preparation_and_stale_read_after_restart_fails(
    prepared_service,
):
    service, project, request = prepared_service
    prepared = service.prepare(project.id, request)
    other = SceneRendering(service.scene.repository, service.directory)
    try:
        assert other.get_preparation(project.id, prepared.id) == prepared
        project.state_version += 1
        with pytest.raises(ValueError, match="изменился"):
            other.get_preparation(project.id, prepared.id)
    finally:
        other.close()
    project.geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": []}
    )
    project.plan = None
    scene = service.scene.from_project(project, 20)
    empty = request.model_copy(
        update={"request_id": uuid4(), "scene_sha256": scene.content_sha256}
    )
    with pytest.raises(ValueError, match="нет геометрии"):
        service.prepare(project.id, empty)
    assert not service._preparation_root(empty.request_id).exists()


def test_preview_tree_base_matches_explicit_base_and_confirmed_terrain():
    packet = {
        "scene": {
            "vertical_primitives": [
                {
                    "primitive_type": "surface_mesh",
                    "terrain_mapping_status": "confirmed",
                    "vertices": [[-10, -10, 150], [10, -10, 150], [0, 10, 150]],
                    "faces": [[0, 1, 2]],
                }
            ]
        },
        "appearance": {},
        "meshes": [],
        "environment_trees": [
            {
                "object_id": "tree",
                "local_x": 0,
                "local_y": 0,
                "base_elevation_m": None,
                "height_m": 12,
                "radius_m": 3.5,
            }
        ],
    }
    assert _preview(packet)["context_trees"][0]["base_elevation_m"] == 150
    packet["environment_trees"][0]["base_elevation_m"] = 0
    assert _preview(packet)["context_trees"][0]["base_elevation_m"] == 0
