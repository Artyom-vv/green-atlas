from __future__ import annotations

import json
import zipfile
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import create_autospec

import ezdxf
import pytest
from application_factory import save_legacy_dxf_fixture
from fastapi.testclient import TestClient

from app.cad_intake.prepare_contracts import (
    PreparedDrawingProvenance,
    PreparedSourceProvenance,
)
from app.composition import get_application, get_runtime
from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.legacy.encoding import decode_text_dxf
from app.exporting.cad_contracts import (
    CadPlant,
    CadRelease,
    CadWriteReceipt,
    CadWrittenPlant,
    encode_plantings,
    validate_receipt,
)
from app.exporting.contracts import RegulatoryReleaseBasis, ReleaseCreateRequest
from app.exporting.evidence import UnavailableReleaseEvidence
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import GeometryRuleTracePort
from app.main import app
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project, ProjectStatus
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot
from app.species.catalog import get_species
from app.validation.contracts import ValidationIssue

client = TestClient(app)
SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"


def _area() -> dict:
    coordinates = [[12, 12], [60, 12], [60, 35], [12, 35]]
    return {
        "id": "roundtrip-area",
        "label": "Участок ревизии",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[*coordinates, coordinates[0]]],
        },
    }


def _auxiliary_dxf() -> bytes:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("AUX_NETWORK")
    document.modelspace().add_lwpolyline(
        [(5, 5), (65, 5)], dxfattribs={"layer": "AUX_NETWORK"}
    )
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode()


def _prepared_project() -> tuple[str, list[str], bytes]:
    source = SITE_DXF.read_bytes()
    created = client.post("/api/projects", json={"name": "Исходная ревизия"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    imported = save_legacy_dxf_fixture(
        get_application(), project_id, "site.dxf", source
    ).model_dump(mode="json")
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported["layers"]
    ]
    assert (
        client.put(
            f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}
        ).status_code
        == 200
    )
    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202
    operation = client.get(
        f"/api/projects/{project_id}/operations/{started.json()['id']}"
    )
    assert operation.json()["status"] == "completed", operation.json()
    assert (
        client.put(
            f"/api/projects/{project_id}/planting-zones", json={"zones": [_area()]}
        ).status_code
        == 200
    )
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    plan = client.post(
        f"/api/projects/{project_id}/plan/objects",
        json={
            "kind": "tree",
            "x": 20,
            "y": 20,
            "species_revision_id": "tilia-cordata@2026-08-28.1",
            "size_class": "standard",
            "group_ids": ["field-group-1"],
            "pattern_id": "manual-group-1",
            "spacing_policy": "canopy",
            "locked": True,
        },
    )
    assert plan.status_code == 200, plan.json()
    second = client.post(
        f"/api/projects/{project_id}/plan/objects",
        json={
            "kind": "shrub",
            "x": 48,
            "y": 26,
            "species_revision_id": "cornus-alba@2026-08-28.1",
            "size_class": "sapling",
            "group_ids": ["field-group-1", "understorey-1"],
            "pattern_id": "manual-group-1",
            "spacing_policy": "balanced",
        },
    )
    assert second.status_code == 200, second.json()
    return project_id, [item["id"] for item in second.json()["objects"]], source


def test_release_contains_plan_semantics_exact_source_and_editable_saved_project() -> (
    None
):
    project_id, object_ids, source = _prepared_project()
    source_plan = client.get(f"/api/projects/{project_id}").json()["plan"]
    release = client.post(
        f"/api/projects/{project_id}/releases",
        json={"mode": "draft", "scene_horizon": 20},
    )
    assert release.status_code == 200, release.json()
    assert (
        "Основания ПП-616 и ПП-1160 в этом черновике не указаны."
        in release.json()["warnings"]
    )
    bundle_artifact = next(
        item for item in release.json()["artifacts"] if item["kind"] == "bundle"
    )
    bundle = client.get(bundle_artifact["download_url"]).content

    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(
            archive.read(
                next(
                    name
                    for name in archive.namelist()
                    if name.endswith("manifest.json")
                )
            )
        )
    assert manifest["schema"] == "green-atlas-release:2"
    assert {item["id"] for item in manifest["plan"]["objects"]} == set(object_ids)
    assert manifest["source"]["embedded"] is True
    assert manifest["source"]["path"] == "source/site.dxf"
    assert "content_base64" not in manifest["source"]

    target = project_id
    payload = client.get(f"/api/projects/{target}").json()
    assert payload["status"] == "editing"
    assert payload["map_ready"] is True
    semantic_fields = {
        "id",
        "kind",
        "x",
        "y",
        "radius",
        "layout_radius_m",
        "size_class",
        "species_revision_id",
        "pattern_id",
        "group_ids",
        "spacing_policy",
        "planting_zone_id",
        "locked",
        "status",
    }
    source_semantics = {
        item["id"]: {key: item.get(key) for key in semantic_fields}
        for item in source_plan["objects"]
    }
    restored_semantics = {
        item["id"]: {key: item.get(key) for key in semantic_fields}
        for item in manifest["plan"]["objects"]
    }
    assert restored_semantics == source_semantics
    assert {layer["source_name"] for layer in payload["layers"]} == {
        "SITE_BORDER",
        "BUILDING",
        "ROAD",
        "UTIL_WATER",
        "UTIL_HEAT",
        "GREEN_EXISTING",
    }
    assert client.get(f"/api/projects/{target}/source-dxf/download").content == source

    object_id = object_ids[1]
    edited = client.patch(
        f"/api/projects/{target}/plan/objects/{object_id}", json={"x": 46}
    )
    assert edited.status_code == 200, edited.json()
    assert any(item["id"] == object_id for item in edited.json()["objects"])
    assert (
        next(item for item in edited.json()["objects"] if item["id"] == object_id)["x"]
        == 46
    )
    exported = client.post(f"/api/projects/{target}/exports")
    assert exported.status_code == 200, exported.json()
    document = ezdxf.read(
        StringIO(client.get(exported.json()["download_url"]).content.decode("utf-8"))
    )
    planting = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')
    assert len(planting) == 1
    assert any(
        tag.code == 1000 and tag.value == f"object_id={object_ids[0]}"
        for tag in planting[0].get_xdata("GREEN_ATLAS")
    )
    shrubs = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_SHRUBS"]')
    assert len(shrubs) == 1
    assert any(
        tag.code == 1000 and tag.value == f"object_id={object_id}"
        for tag in shrubs[0].get_xdata("GREEN_ATLAS")
    )


def test_release_preserves_saved_independent_dxf_set_and_rejects_corrupt_sources() -> (
    None
):
    project_id, _object_ids, primary = _prepared_project()
    auxiliary = _auxiliary_dxf()
    primary_path = "site.dxf"
    auxiliary_path = "networks/base.dxf"
    runtime = get_runtime()
    project = runtime.project_repository.get(project_id)
    assert project.source_file is not None
    project.source_file.prepared_provenance = PreparedSourceProvenance(
        intake_operation_id="roundtrip-multi-intake",
        manifest_sha256="a" * 64,
        entry=primary_path,
        source_sha256=sha256(primary).hexdigest(),
        drawings=[
            PreparedDrawingProvenance(
                path=path,
                source_sha256=sha256(content).hexdigest(),
                source_bytes=len(content),
            )
            for path, content in ((primary_path, primary), (auxiliary_path, auxiliary))
        ],
    )
    runtime.project_repository.save(
        project,
        source=primary,
        source_components={auxiliary_path: auxiliary},
    )

    release = client.post(
        f"/api/projects/{project_id}/releases",
        json={"mode": "draft", "scene_horizon": 20},
    )
    assert release.status_code == 200, release.json()
    bundle_artifact = next(
        item for item in release.json()["artifacts"] if item["kind"] == "bundle"
    )
    bundle = client.get(bundle_artifact["download_url"]).content
    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        assert archive.read(f"source/{primary_path}") == primary
        assert archive.read(f"source/{auxiliary_path}") == auxiliary
        derived_name = next(
            name for name in archive.namelist() if name.endswith("planting-plan.dxf")
        )
        derived = ezdxf.read(StringIO(decode_text_dxf(archive.read(derived_name))))
        manifest = json.loads(
            archive.read(
                next(
                    name
                    for name in archive.namelist()
                    if name.endswith("manifest.json")
                )
            )
        )
    assert {item["path"] for item in manifest["source"]["drawings"]} == {
        primary_path,
        auxiliary_path,
    }
    assert "geometry" in manifest
    assert "geometry" not in manifest["project"]
    assert any(layer.dxf.name.endswith("$0$AUX_NETWORK") for layer in derived.layers)
    auxiliary_marker = (
        f"GREEN_ATLAS_SOURCE_{sha256(auxiliary).hexdigest()[:12].upper()}"
    )
    assert any(
        entity.is_alive and entity.has_xdata(auxiliary_marker)
        for entity in derived.entitydb.values()
    )

    before = runtime.project_repository.get(project_id).model_dump_json()
    runtime.project_repository.save(
        runtime.project_repository.get(project_id),
        source_components={auxiliary_path: auxiliary + b"\nchanged"},
    )
    rejected = client.post(
        f"/api/projects/{project_id}/releases",
        json={"mode": "draft", "scene_horizon": 21},
    )
    assert rejected.status_code == 400, rejected.text
    assert "не совпадают" in rejected.text
    assert runtime.project_repository.get_source(project_id) == primary
    saved_plan = runtime.project_repository.get(project_id).plan
    assert saved_plan is not None
    assert saved_plan.model_dump(mode="json") == json.loads(before)["plan"]


def test_plain_exported_dxf_is_explicitly_read_only_fallback() -> None:
    project_id, _object_ids, _source = _prepared_project()
    exported = client.post(f"/api/projects/{project_id}/exports")
    assert exported.status_code == 200
    target = client.post("/api/projects", json={"name": "DXF без manifest"}).json()[
        "id"
    ]
    imported = save_legacy_dxf_fixture(
        get_application(),
        target,
        "plain-plan.dxf",
        client.get(exported.json()["download_url"]).content,
    ).model_dump(mode="json")
    status = imported["import_status"]
    assert status["mode"] == "plain_dxf_fallback"
    assert status["editability"] == "read_only"
    assert "полный ZIP-пакет" in status["message"]


def test_provenance_layers_keep_an_enriched_source_editable() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    for layer in (
        "SITE_BORDER",
        "GREEN_ATLAS_TERRAIN_COP90",
        "GREEN_ATLAS_BUILDING_OSM_LEVELS",
    ):
        document.layers.add(layer)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (40, 0), (40, 40), (0, 40)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_3dface(
        [(0, 0, 0), (40, 0, 1), (40, 40, 2), (0, 40, 1)],
        dxfattribs={"layer": "GREEN_ATLAS_TERRAIN_COP90"},
    )
    modelspace.add_lwpolyline(
        [(10, 10), (20, 10), (20, 20), (10, 20)],
        close=True,
        dxfattribs={
            "layer": "GREEN_ATLAS_BUILDING_OSM_LEVELS",
            "elevation": 0.5,
            "thickness": 12,
        },
    )
    stream = StringIO()
    document.write(stream)

    target = client.post("/api/projects", json={"name": "Обогащённый исходник"}).json()[
        "id"
    ]
    imported = save_legacy_dxf_fixture(
        get_application(), target, "enriched-source.dxf", stream.getvalue().encode()
    ).model_dump(mode="json")

    assert imported["import_status"]["mode"] == "source_dxf"
    assert imported["import_status"]["editability"] == "editable"


def _release_golden_inputs(
    native: bool,
) -> tuple[Project, ReleaseCreateRequest, SceneSnapshot, bytes, CadRelease | None]:
    species = get_species("tilia-cordata@2026-08-28.1")
    source = (
        b'{"schema":"autocad_snapshot_v1","capture":"fixed"}\n'
        if native
        else SITE_DXF.read_bytes()
    )
    plan = Plan(
        id="fixed-plan",
        version=4,
        planning_horizon_year=20,
        objects=[
            PlanObject(
                id="bb22",
                kind="shrub",
                x=6,
                y=7,
                radius=0.65,
                group_ids=["group-b", "group-a"],
            ),
            PlanObject(
                id="aa11",
                kind="tree",
                x=3,
                y=4,
                radius=1.25,
                species_revision_id=species.id,
                size_class="standard",
                canopy_forecast=species.canopy_forecast,
                root_forecast=species.root_forecast,
                group_ids=["group-a"],
                locked=True,
            ),
        ],
        issues=[
            ValidationIssue(
                id="fixed-error",
                severity="error",
                code="FIXED",
                title="Замечание",
                description="Описание",
                object_id="bb22",
                rule_id="SP82-BASE-BUILDING-TREE",
            )
        ],
    )
    project = Project(
        id="fixed-project",
        name='Участок <A> & "B"',
        status=ProjectStatus.EDITING,
        created_at="2026-08-28T00:00:00+00:00",
        updated_at="2026-08-28T00:00:00+00:00",
        source_file=SourceFile(
            name="site.dwg" if native else "site.dxf",
            size=len(source),
            content_sha256=sha256(source).hexdigest(),
            imported_at="2026-08-28T00:00:00+00:00",
            dxf_version="AC1027",
            units="m",
            entity_count=3,
        ),
        import_status=ImportStatus(
            mode=ImportMode.AUTOCAD_LIVE if native else ImportMode.SOURCE_DXF
        ),
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "building-1",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                        },
                        "properties": {"kind": "building", "source_layer": "BUILDING"},
                    },
                ],
            }
        ),
        layers=[
            Layer(
                id=f"layer-{index}",
                source_name=kind.value,
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=1,
                color="#667085",
                geometry_complete=index != 1,
                mapping_review_required=index == 0,
            )
            for index, kind in enumerate(
                (LayerKind.BUILDING, LayerKind.UTILITY, LayerKind.IGNORE)
            )
        ],
        plan=plan,
        geometry_version=7,
        state_version=9,
    )
    request = ReleaseCreateRequest(
        scene_horizon=23,
        regulatory_basis=RegulatoryReleaseBasis(
            pp616_status="documented",
            pp616_reference="616/fixed",
            pp1160_status="documented",
            pp1160_reference="1160/fixed",
            confirmed_by="Оператор",
        )
        if native
        else None,
    )
    scene = SceneSnapshot(
        plan_version=4,
        horizon_year=23,
        coordinate_origin=[1, 2],
        note="Фиксированная сцена",
        data_gaps=["Нет высот"],
    )
    cad = None
    if native:
        plants = tuple(
            CadPlant(id=item.id, kind=item.kind, x=item.x, y=item.y, radius=item.radius)
            for item in plan.objects
        )
        dwg, xref, source_dwg = b"DWG-result-fixed", b"XREF-fixed", b"DWG-source-fixed"
        request_sha, source_sha, result_sha = (
            sha256(content).hexdigest()
            for content in (encode_plantings(plants, 6), source_dwg, dwg)
        )
        receipt = CadWriteReceipt.model_validate(
            {
                "schema": "green-atlas.cad-release/1",
                "request_sha256": request_sha,
                "source_sha256": source_sha,
                "result_sha256": result_sha,
                "source_instances": 3,
                "result_instances": 5,
                "units_code": 6,
                "reopened": True,
                "plantings": tuple(
                    CadWrittenPlant(**item.model_dump(), handle=f"A{index}")
                    for index, item in enumerate(plants)
                ),
            }
        )
        validate_receipt(receipt, plants, 6, request_sha, source_sha, result_sha)
        cad = CadRelease(
            files={"site.dwg": dwg, "xref/network.dwg": xref},
            entry="site.dwg",
            receipt=receipt,
        )
    return project, request, scene, source, cad


RELEASE_GOLDEN_HASHES: dict[str, dict[str, str]] = {
    "compatibility": {
        "schedule": "4cfe454dea36a7ec69b7114db690ff92c9313cd5d10bc46eef27b482ff94b042",
        "manifest": "eceb26bb764cd2f5f0879f7072c97dec6eac2572f4bdf17063d33ad431cd4c59",
        "scene": "31a6734e6ff73afeef0c51c667e556ffcb9d2d339c6b5cfd4afc27deecd9c155",
        "dendroplan": "514a9fd59eadbf28e477f5475d5af74d21fad5295976a1962dd5fa6c6f6d4b65",
        "bundle": "54403c7ef70a6d8faff310ca86144f39895f4f228d61d3df88fc241bc71a8c3f",
        "dxf": "51a937ffce63675f9907e0ccc052d2f589a54a709d6d7d46e3608ce695ede1b9",
        "package": "be30b661e8f54ab1b8a5bea8065e2c03204125b1d1a0e5396b6476c9a56db11e",
    },
    "native": {
        "schedule": "4cfe454dea36a7ec69b7114db690ff92c9313cd5d10bc46eef27b482ff94b042",
        "manifest": "4e2552bdecbef26560e2436587ebb694093f54c3615f4dc0bda2839180d53c27",
        "scene": "31a6734e6ff73afeef0c51c667e556ffcb9d2d339c6b5cfd4afc27deecd9c155",
        "dendroplan": "514a9fd59eadbf28e477f5475d5af74d21fad5295976a1962dd5fa6c6f6d4b65",
        "bundle": "5e55040b0aa3a8c9c00698ed62998bbca43b2e4950fbcb63748fe44020b129d7",
        "cad": "82613cdc3a444dee9b93f0cbb57811ad3291a0d1dfb31616be115a0cdd91c589",
        "package": "bb02f7db9769ae1eeced9bc21d7604121d3785123c117216a0cadadc9a107ad0",
    },
    "unavailable": {
        "schedule": "4cfe454dea36a7ec69b7114db690ff92c9313cd5d10bc46eef27b482ff94b042",
        "manifest": "1e6246328116a59972f2b73c01ea36c593213fd1cd26e9d1f2f0bfbfdece56e1",
        "scene": "31a6734e6ff73afeef0c51c667e556ffcb9d2d339c6b5cfd4afc27deecd9c155",
        "dendroplan": "514a9fd59eadbf28e477f5475d5af74d21fad5295976a1962dd5fa6c6f6d4b65",
        "bundle": "41402b5e5ae67bb0d91b0dd6ac9f46d0d4eba369400e2f1b9742088c08966fee",
        "cad": "82613cdc3a444dee9b93f0cbb57811ad3291a0d1dfb31616be115a0cdd91c589",
        "package": "ec9c80b38fb3b110f79213f15977dc4fe9068bb33fc365baa8f2af43b3b97b0d",
    },
}


@pytest.mark.parametrize("case", ["compatibility", "native", "unavailable"])
def test_release_artifact_bytes_match_complete_baseline(case: str) -> None:
    project, request, scene, source, cad = _release_golden_inputs(
        case != "compatibility"
    )
    before = project.model_dump(mode="json")
    package, contents = build_release(
        project,
        request,
        release_identity(project, request),
        b"derived-DXF-fixed",
        scene,
        source,
        geometry=UnavailableReleaseEvidence()
        if case == "unavailable"
        else ShapelyGeometryEngine(),
        cad=cad,
    )
    actual: dict[str, str] = {
        artifact.kind: sha256(contents[artifact.id]).hexdigest()
        for artifact in package.artifacts
    }
    # Только время создания зависит от запуска; все остальные поля входят в эталон.
    package_json = json.dumps(
        package.model_dump(mode="json", exclude={"created_at"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    actual["package"] = sha256(package_json).hexdigest()
    assert actual == RELEASE_GOLDEN_HASHES[case]
    manifest = json.loads(
        contents[next(item.id for item in package.artifacts if item.kind == "manifest")]
    )
    # Выпускаем текущий план; выбранный прогноз остаётся параметром изображения.
    assert manifest["project"]["planning_horizon_year"] == 0
    assert manifest["plan"]["planning_horizon_year"] == 0
    assert manifest["data_passport"]["planning_horizon_year"] == 0
    assert package.planning_horizon_year == 0 and package.scene_horizon == 23
    assert project.model_dump(mode="json") == before


def test_release_identity_ignores_legacy_growth_horizon_and_keeps_visual_scenario():
    project, request, scene, source, cad = _release_golden_inputs(True)
    assert project.plan is not None
    before = project.model_dump_json()
    identity = release_identity(project, request)
    baseline = project.model_copy(
        update={"plan": project.plan.model_copy(update={"planning_horizon_year": 0})}
    )
    assert release_identity(baseline, request) == identity
    other_scene = request.model_copy(update={"scene_horizon": 5})
    assert release_identity(project, other_scene) != identity
    package, contents = build_release(
        project, request, identity, b"derived-DXF-fixed", scene, source, cad=cad
    )
    assert package.planning_horizon_year == 0
    assert package.scene_horizon == request.scene_horizon == 23
    manifest_artifact = next(
        item for item in package.artifacts if item.kind == "manifest"
    )
    manifest = json.loads(contents[manifest_artifact.id])
    assert manifest["project"]["planning_horizon_year"] == 0
    assert manifest["plan"]["planning_horizon_year"] == 0
    assert manifest["views"]["scene_horizon"] == 23
    assert project.model_dump_json() == before


@pytest.mark.parametrize(
    "path",
    [
        "../escape.dwg",
        "/absolute.dwg",
        "nested/./site.dwg",
        "nested/../site.dwg",
        "bad\\site.dwg",
        "bad\x00site.dwg",
    ],
)
def test_release_rejects_cad_traversal_before_source_checks_and_callbacks(
    path: str,
) -> None:
    project, request, scene, source, cad = _release_golden_inputs(True)
    assert cad is not None
    invalid = CadRelease(files={path: b"invalid"}, entry=cad.entry, receipt=cad.receipt)
    geometry = create_autospec(GeometryRuleTracePort, instance=True)
    before = project.model_dump(mode="json")

    with pytest.raises(ValueError, match="^Недопустимый путь CAD-результата$"):
        build_release(
            project,
            request,
            release_identity(project, request),
            b"",
            scene,
            source,
            {"autocad-snapshot.json": source},
            geometry=geometry,
            cad=invalid,
        )

    geometry.position_rule_trace.assert_not_called()
    assert project.model_dump(mode="json") == before


def test_release_checks_sources_before_rule_callbacks_and_preserves_callback_order() -> (
    None
):
    project, request, scene, source, cad = _release_golden_inputs(False)
    geometry = create_autospec(GeometryRuleTracePort, instance=True)
    geometry.position_rule_trace.side_effect = (
        ShapelyGeometryEngine().position_rule_trace
    )

    with pytest.raises(
        ValueError, match="^Дополнительный DXF дублирует основной исходник$"
    ):
        build_release(
            project,
            request,
            release_identity(project, request),
            b"",
            scene,
            source,
            {"site.dxf": source},
            geometry=geometry,
            cad=cad,
        )
    geometry.position_rule_trace.assert_not_called()

    build_release(
        project,
        request,
        release_identity(project, request),
        b"",
        scene,
        source,
        geometry=geometry,
        cad=cad,
    )
    assert [
        (call.args[1], call.args[2], call.args[3])
        for call in geometry.position_rule_trace.call_args_list
    ] == [(6.0, 7.0, "shrub"), (3.0, 4.0, "tree")]
