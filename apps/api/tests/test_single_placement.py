from __future__ import annotations

import pytest

from app.composition import get_application
application = get_application()
from test_manual_flow import area, boundaryless_content, prepare_map, select_areas
from test_project_concurrency import client, create_manual_project


def test_species_and_size_check_match_authoritative_add_without_writing() -> None:
    project = create_manual_project("Единая проверка одиночной посадки")
    project_id = project["id"]
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    before = application.get(project_id).model_dump(mode="json")
    history_before = application.history.state(project_id).model_dump(mode="json")
    previews_before = list(application.changes._previews)
    candidate = {"kind": "tree", "x": 26, "y": 20}
    generic = client.post(f"/api/projects/{project_id}/plan/placement-check", json=candidate)
    assert generic.status_code == 200 and generic.json()["allowed"]

    typed = {**candidate, "species_revision_id": "tilia-cordata@2026-08-28.1", "size_class": "standard"}
    checked = client.post(f"/api/projects/{project_id}/plan/placement-check", json=typed)
    assert checked.status_code == 200 and not checked.json()["allowed"]
    assert checked.json()["code"] == "PLANT_SPACING"
    # No preview token, durable project edit, history entry or candidate in the cached index.
    assert list(application.changes._previews) == previews_before
    assert application.get(project_id).model_dump(mode="json") == before
    assert application.history.state(project_id).model_dump(mode="json") == history_before
    index = application.spatial._spacing_indexes[project_id][1]
    assert sum(len(items) for items in index.cells.values()) == 1
    refused = client.post(f"/api/projects/{project_id}/plan/objects", json=typed)
    assert refused.status_code == 400 and refused.json()["message"] == checked.json()["reason"]

    # Size affects the same crown forecast used by Add, not just the marker.
    oak = {"kind": "tree", "x": 34.5, "y": 20, "species_revision_id": "quercus-robur@2026-08-28.1"}
    sapling = client.post(f"/api/projects/{project_id}/plan/placement-check", json={**oak, "size_class": "sapling"})
    large = client.post(f"/api/projects/{project_id}/plan/placement-check", json={**oak, "size_class": "large"})
    assert sapling.json()["allowed"] is True
    assert large.json()["allowed"] is False and large.json()["code"] == "PLANT_SPACING"
    added = client.post(f"/api/projects/{project_id}/plan/objects", json={**oak, "size_class": "sapling"}, headers={"If-Match": str(before["state_version"])})
    assert added.status_code == 200 and len(added.json()["objects"]) == 2


@pytest.mark.parametrize("version_field", ["base_plan_version", "geometry_version", "state_version"])
def test_check_refuses_stale_basis_and_returns_actual_versions(version_field: str) -> None:
    project = create_manual_project("Версия проверки")
    request = {
        "kind": "tree", "x": 20, "y": 20,
        "base_plan_version": project["plan"]["version"],
        "geometry_version": project["geometry_version"], "state_version": project["state_version"],
    }
    request[version_field] += 1
    response = client.post(f"/api/projects/{project['id']}/plan/placement-check", json=request)
    assert response.status_code == 200
    check = response.json()
    assert check["allowed"] is False and check["code"] == "STALE_PLACEMENT_BASIS"
    assert check["plan_version"] == project["plan"]["version"]
    assert check["geometry_version"] == project["geometry_version"]
    assert check["state_version"] == project["state_version"]
    assert application.get(project["id"]).state_version == project["state_version"]


def test_check_exposes_actual_geometry_source_and_distance() -> None:
    project_id = client.post("/api/projects", json={"name": "Источник ограничения"}).json()["id"]
    imported = client.post(f"/api/projects/{project_id}/source-dxf", files={"file": ("fragment.dxf", boundaryless_content(), "application/dxf")}).json()
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)
    select_areas(project_id, [area("manual", "Контур", [[5, 5], [80, 5], [80, 80], [5, 80]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    checked = client.post(f"/api/projects/{project_id}/plan/placement-check", json={"kind": "tree", "x": 33.01, "y": 43})
    assert checked.status_code == 200
    result = checked.json()
    assert result["allowed"] is False
    assert result["source_layer"] == "BUILDING" and result["source_feature_ids"]
    assert result["actual_distance_m"] == pytest.approx(4.99)
    assert result["required_distance_m"] == 5
    assert result["rule_id"] and result["suggested_action"]
