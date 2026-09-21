from __future__ import annotations

import json
import zipfile
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient

from app.cad_intake.composition import ImportedDrawing, compose_dxf_imports
from app.cad_intake.prepare_contracts import (
    PreparedDrawingProvenance,
    PreparedSourceProvenance,
)
from app.composition import get_runtime
from app.dxf_import.encoding import decode_text_dxf
from app.main import app

client = TestClient(app)
SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"


def _area() -> dict:
    coordinates = [[12, 12], [60, 12], [60, 35], [12, 35]]
    return {
        "id": "roundtrip-area",
        "label": "Участок ревизии",
        "geometry": {"type": "Polygon", "coordinates": [[*coordinates, coordinates[0]]]},
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


def _replace_bundle_entry(bundle: bytes, name: str, content: bytes) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(bundle)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for entry in source.infolist():
            target.writestr(entry.filename, content if entry.filename == name else source.read(entry))
    return output.getvalue()


def _prepared_project() -> tuple[str, list[str], bytes]:
    source = SITE_DXF.read_bytes()
    created = client.post("/api/projects", json={"name": "Исходная ревизия"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("site.dxf", source, "application/dxf")},
    )
    assert imported.status_code == 200, imported.json()
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported.json()["layers"]
    ]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202
    operation = client.get(f"/api/projects/{project_id}/operations/{started.json()['id']}")
    assert operation.json()["status"] == "completed", operation.json()
    assert client.put(f"/api/projects/{project_id}/planting-zones", json={"zones": [_area()]}).status_code == 200
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


def test_release_bundle_restores_editable_plan_and_source_layers() -> None:
    project_id, object_ids, source = _prepared_project()
    source_plan = client.get(f"/api/projects/{project_id}").json()["plan"]
    release = client.post(f"/api/projects/{project_id}/releases", json={"mode": "draft", "scene_horizon": 20})
    assert release.status_code == 200, release.json()
    assert "Основания ПП-616 и ПП-1160 в этом черновике не указаны." in release.json()["warnings"]
    bundle_artifact = next(item for item in release.json()["artifacts"] if item["kind"] == "bundle")
    bundle = client.get(bundle_artifact["download_url"]).content

    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith("manifest.json"))))
    assert manifest["schema"] == "green-atlas-release:2"
    assert {item["id"] for item in manifest["plan"]["objects"]} == set(object_ids)
    assert manifest["source"]["embedded"] is True
    assert manifest["source"]["path"] == "source/site.dxf"
    assert "content_base64" not in manifest["source"]

    target = client.post("/api/projects", json={"name": "Новая ревизия"}).json()["id"]
    imported = client.post(
        f"/api/projects/{target}/release-bundle",
        files={"file": ("release.zip", bundle, "application/zip")},
    )
    assert imported.status_code == 200, imported.json()
    payload = imported.json()
    assert payload["status"] == "editing"
    assert payload["map_ready"] is True
    assert payload["import_status"] == {
        "mode": "release_bundle",
        "editability": "editable",
        "release_id": release.json()["id"],
        "message": "Ревизия восстановлена из полного ZIP-пакета и доступна для редактирования.",
    }
    semantic_fields = {
        "id", "kind", "x", "y", "radius", "layout_radius_m", "size_class",
        "species_revision_id", "pattern_id", "group_ids", "spacing_policy",
        "planting_zone_id", "locked", "status",
    }
    source_semantics = {
        item["id"]: {key: item.get(key) for key in semantic_fields}
        for item in source_plan["objects"]
    }
    restored_semantics = {
        item["id"]: {key: item.get(key) for key in semantic_fields}
        for item in payload["plan"]["objects"]
    }
    assert restored_semantics == source_semantics
    assert {layer["source_name"] for layer in payload["layers"]} == {
        "SITE_BORDER", "BUILDING", "ROAD", "UTIL_WATER", "UTIL_HEAT", "GREEN_EXISTING",
    }
    assert client.get(f"/api/projects/{target}/source-dxf/download").content == source

    history_boundary = client.get(f"/api/projects/{target}/plan/history")
    assert history_boundary.status_code == 200
    assert history_boundary.json()["can_undo"] is False
    assert history_boundary.json()["entries"] == []

    object_id = object_ids[1]
    edited = client.patch(f"/api/projects/{target}/plan/objects/{object_id}", json={"x": 46})
    assert edited.status_code == 200, edited.json()
    assert any(item["id"] == object_id for item in edited.json()["objects"])
    assert next(item for item in edited.json()["objects"] if item["id"] == object_id)["x"] == 46
    exported = client.post(f"/api/projects/{target}/exports")
    assert exported.status_code == 200, exported.json()
    document = ezdxf.read(StringIO(client.get(exported.json()["download_url"]).content.decode("utf-8")))
    planting = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')
    assert len(planting) == 1
    assert any(tag.code == 1000 and tag.value == f"object_id={object_ids[0]}" for tag in planting[0].get_xdata("GREEN_ATLAS"))
    shrubs = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_SHRUBS"]')
    assert len(shrubs) == 1
    assert any(tag.code == 1000 and tag.value == f"object_id={object_id}" for tag in shrubs[0].get_xdata("GREEN_ATLAS"))


def test_release_bundle_preserves_and_restores_independent_dxf_set() -> None:
    project_id, _object_ids, primary = _prepared_project()
    auxiliary = _auxiliary_dxf()
    primary_path = "site.dxf"
    auxiliary_path = "networks/base.dxf"
    runtime = get_runtime()
    composed = compose_dxf_imports(
        [
            ImportedDrawing(
                path=primary_path,
                source=primary,
                imported=runtime.application.dxf_reader.read(primary_path, primary),
            ),
            ImportedDrawing(
                path=auxiliary_path,
                source=auxiliary,
                imported=runtime.application.dxf_reader.read(
                    auxiliary_path, auxiliary
                ),
            ),
        ]
    )
    project = runtime.project_repository.get(project_id)
    assert project.source_file is not None
    project.layers = composed.imported.layers
    project.geometry = composed.imported.geometry
    project.coordinate_reference = composed.imported.coordinate_reference
    project.source_file.dxf_version = composed.imported.dxf_version
    project.source_file.entity_count = composed.imported.entity_count
    project.source_file.bounds = composed.imported.bounds
    project.source_file.prepared_provenance = PreparedSourceProvenance(
        intake_operation_id="roundtrip-multi-intake",
        manifest_sha256="a" * 64,
        entry=primary_path,
        source_sha256=sha256(primary).hexdigest(),
        drawings=[
            PreparedDrawingProvenance(
                path=item.path,
                source_sha256=item.source_sha256,
                source_bytes=item.source_bytes,
                cad_snapshot=item.cad_snapshot,
            )
            for item in composed.drawings
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
        derived = ezdxf.read(
            StringIO(decode_text_dxf(archive.read(derived_name)))
        )
        manifest = json.loads(
            archive.read(
                next(name for name in archive.namelist() if name.endswith("manifest.json"))
            )
        )
    assert {item["path"] for item in manifest["source"]["drawings"]} == {
        primary_path,
        auxiliary_path,
    }
    assert "geometry" in manifest
    assert "geometry" not in manifest["project"]
    assert any(
        layer.dxf.name.endswith("$0$AUX_NETWORK")
        for layer in derived.layers
    )
    auxiliary_marker = (
        f"GREEN_ATLAS_SOURCE_{sha256(auxiliary).hexdigest()[:12].upper()}"
    )
    assert any(
        entity.is_alive and entity.has_xdata(auxiliary_marker)
        for entity in derived.entitydb.values()
    )

    target = client.post("/api/projects", json={"name": "Комплект DXF"}).json()["id"]
    restored = client.post(
        f"/api/projects/{target}/release-bundle",
        files={"file": ("release.zip", bundle, "application/zip")},
    )
    assert restored.status_code == 200, restored.json()
    assert runtime.project_repository.get_source_components(target) == {
        auxiliary_path: auxiliary
    }
    assert {layer["source_name"] for layer in restored.json()["layers"]} >= {
        f"[{primary_path}] SITE_BORDER",
        f"[{auxiliary_path}] AUX_NETWORK",
    }
    restored_project = runtime.project_repository.get(target)
    assert restored_project.geometry is not None
    source_paths = {
        feature["properties"].get("source_drawing_path")
        for feature in restored_project.geometry.feature_collection["features"]
    }
    assert source_paths == {primary_path, auxiliary_path}

    rejected_target = client.post(
        "/api/projects", json={"name": "Повреждённый комплект"}
    ).json()["id"]
    tampered = _replace_bundle_entry(
        bundle, f"source/{auxiliary_path}", auxiliary + b"\nchanged"
    )
    rejected = client.post(
        f"/api/projects/{rejected_target}/release-bundle",
        files={"file": ("release.zip", tampered, "application/zip")},
    )
    assert rejected.status_code == 400, rejected.json()
    assert runtime.project_repository.get_source(rejected_target) is None
    assert runtime.project_repository.get_source_components(rejected_target) == {}


def test_plain_exported_dxf_is_explicitly_read_only_fallback() -> None:
    project_id, _object_ids, _source = _prepared_project()
    exported = client.post(f"/api/projects/{project_id}/exports")
    assert exported.status_code == 200
    target = client.post("/api/projects", json={"name": "DXF без manifest"}).json()["id"]
    imported = client.post(
        f"/api/projects/{target}/source-dxf",
        files={"file": ("plain-plan.dxf", client.get(exported.json()["download_url"]).content, "application/dxf")},
    )
    assert imported.status_code == 200, imported.json()
    status = imported.json()["import_status"]
    assert status["mode"] == "plain_dxf_fallback"
    assert status["editability"] == "read_only"
    assert "полный ZIP-пакет" in status["message"]


def test_provenance_layers_keep_an_enriched_source_editable() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    for layer in ("SITE_BORDER", "GREEN_ATLAS_TERRAIN_COP90", "GREEN_ATLAS_BUILDING_OSM_LEVELS"):
        document.layers.add(layer)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 40), (0, 40)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_3dface([(0, 0, 0), (40, 0, 1), (40, 40, 2), (0, 40, 1)], dxfattribs={"layer": "GREEN_ATLAS_TERRAIN_COP90"})
    modelspace.add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True, dxfattribs={"layer": "GREEN_ATLAS_BUILDING_OSM_LEVELS", "elevation": 0.5, "thickness": 12})
    stream = StringIO()
    document.write(stream)

    target = client.post("/api/projects", json={"name": "Обогащённый исходник"}).json()["id"]
    imported = client.post(
        f"/api/projects/{target}/source-dxf",
        files={"file": ("enriched-source.dxf", stream.getvalue().encode(), "application/dxf")},
    )

    assert imported.status_code == 200, imported.json()
    assert imported.json()["import_status"]["mode"] == "source_dxf"
    assert imported.json()["import_status"]["editability"] == "editable"
