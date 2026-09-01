from __future__ import annotations

from io import BytesIO, StringIO
import json
from pathlib import Path
import zipfile

import ezdxf
from fastapi.testclient import TestClient

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
    bundle_artifact = next(item for item in release.json()["artifacts"] if item["kind"] == "bundle")
    bundle = client.get(bundle_artifact["download_url"]).content

    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith("manifest.json"))))
    assert manifest["schema"] == "green-atlas-release:2"
    assert {item["id"] for item in manifest["plan"]["objects"]} == set(object_ids)
    assert manifest["source"]["embedded"] is True

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
