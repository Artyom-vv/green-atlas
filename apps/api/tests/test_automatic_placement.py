from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from test_placement_allocation import application

from app.automatic_placement import (
    AutomaticPlacementRequest,
    preview_automatic_placement,
)
from app.composition import get_application
from app.history.adapters import InMemoryProjectHistory
from app.main import app as http_app
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.domain import PlanVersionConflict


def test_automatic_building_placement_chooses_species_and_requires_apply():
    service, project = application()
    project.geometry.feature_collection["features"].append({
        "type": "Feature", "properties": {"kind": "building"},
        "geometry": mapping(box(75, 75, 125, 125)),
    })
    service.repository.save(project)
    request = AutomaticPlacementRequest(
        base_plan_version=project.plan.version, zone_id="west",
        near="building", plant_kind="auto", target_count=8,
    )
    result = preview_automatic_placement(service, project.id, request)
    assert result.selected_kind == "shrub"
    assert result.found == 8
    assert result.species_revision_ids
    assert result.change_set and result.change_set.can_apply
    assert service.get(project.id).plan.objects == []

    change = result.change_set
    service.history_application.history = InMemoryProjectHistory()
    service.apply_change_set(project.id, PlanChangeSetApplyRequest(
        preview_id=change.id, digest=change.digest,
        base_plan_version=change.base_plan_version,
    ))
    assert len(service.get(project.id).plan.objects) == 8


def test_automatic_preview_rejects_stale_plan_and_missing_zone():
    service, project = application()
    with pytest.raises(PlanVersionConflict):
        preview_automatic_placement(service, project.id, AutomaticPlacementRequest(
            base_plan_version=project.plan.version + 1, zone_id="west",
        ))
    with pytest.raises(ValueError, match="участок"):
        preview_automatic_placement(service, project.id, AutomaticPlacementRequest(
            base_plan_version=project.plan.version, zone_id="missing",
        ))


def test_http_auto_preview_uses_the_same_service_without_saving():
    service, project = application()
    http_app.dependency_overrides[get_application] = lambda: service
    try:
        response = TestClient(http_app).post(
            f"/api/projects/{project.id}/plan/automatic/preview",
            json={"base_plan_version": project.plan.version, "zone_id": "west",
                  "near": "area", "plant_kind": "auto", "target_count": 3},
        )
    finally:
        http_app.dependency_overrides.pop(get_application, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] == 3
    assert body["selected_kind"] == "tree"
    assert body["change_set"]["can_apply"] is True
    assert service.get(project.id).plan.objects == []
