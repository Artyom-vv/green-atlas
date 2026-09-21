"""Automatic composition must use the same zone and geometry checks as editing."""

import json
from pathlib import Path

import pytest
from shapely.geometry import Point, box, mapping, shape
from test_zone_assortment_policy import context, ready

from app.dxf_import.review_contracts import SourceReview
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.pattern_application import PatternApplication
from app.planning.patterns import MAX_PATTERN_CANDIDATES
from app.planning.recommendation_contracts import RecommendationRequest
from app.projects.contracts import Project
from app.species.placement_policy import plant_eligibility


def small_project(*, roads=False):
    app, project = ready()
    for zone, offset in zip(project.planting_zones, [0, 60], strict=True):
        zone.geometry = mapping(box(offset, 0, offset + 30, 24))
    if roads:
        project.planting_zones = project.planting_zones[:1]
        project.planting_zones[0].territory = context("major_road")
        project.geometry.feature_collection["features"].extend(
            [
                {
                    "type": "Feature",
                    "id": "road",
                    "properties": {"kind": "road", "layer": "road"},
                    "geometry": mapping(box(-10, -3, 40, 0)),
                },
                {
                    "type": "Feature",
                    "id": "water",
                    "properties": {"kind": "water", "layer": "water"},
                    "geometry": mapping(box(12, 0, 18, 20)),
                },
            ]
        )
    return app, app.repository.save(project)


def request(project, **values):
    return RecommendationRequest(
        base_plan_version=project.plan.version,
        zone_ids=[z.id for z in project.planting_zones],
        selection_mode="automatic",
        **values,
    )


def canonical(result):
    return [
        (p.kind, p.species_revision_id, p.x, p.y, p.planting_zone_id, p.canopy_forecast)
        for p in result.change_set.additions
    ]


def test_automatic_capacity_composition_is_repeatable_qualified_and_atomic():
    app, project = small_project()
    before = project.model_dump()
    first = app.preview_recommendation(project.id, request(project, max_sites=1))
    assert first.change_set and first.change_set.can_apply
    assert len(app.changes._previews) == 1
    assert len(first.change_set.additions) > 1
    assert {p.kind for p in first.change_set.additions} == {"tree", "shrub"}
    assert all(z.requested_count is None for z in first.zone_results)
    for plant in first.change_set.additions:
        zone = next(z for z in project.planting_zones if z.id == plant.planting_zone_id)
        assert plant_eligibility(plant.species_revision_id, zone.territory).allowed
        assert shape(zone.geometry).covers(Point(plant.x, plant.y))
    assert all(c.status == "allowed" for c in first.change_set.candidate_results)
    assert {e.object_id for e in first.explanations} == {
        p.id for p in first.change_set.additions
    }
    assert app.get(project.id).model_dump() == before
    second = app.preview_recommendation(project.id, request(project, max_sites=500))
    assert canonical(first) == canonical(second)
    # Apply remains the ordinary atomic command; preview alone does not edit.
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=first.change_set.id,
            digest=first.change_set.digest,
            base_plan_version=1,
        ),
    )
    saved = app.get(project.id)
    assert len(saved.plan.objects) == len(first.change_set.additions)
    assert saved.plan.version == 2


def test_road_mode_follows_imported_guides_and_checks_obstacles():
    app, project = small_project(roads=True)
    result = app.preview_recommendation(
        project.id, request(project, arrangement="road_edges")
    )
    assert result.arrangement == "road_edges"
    assert result.change_set and result.change_set.can_apply
    water = box(12, 0, 18, 20)
    assert result.change_set.additions
    for plant in result.change_set.additions:
        assert water.distance(Point(plant.x, plant.y)) > 0
        assert (
            0 < plant.y < 12
        )  # Candidates follow the road, not the distant end of the zone.
        assert plant_eligibility(
            plant.species_revision_id, context("major_road")
        ).allowed
    assert all(c.status == "allowed" for c in result.change_set.candidate_results)
    assert app.get(project.id).plan.objects == []


def test_missing_road_does_not_fall_back_to_unrelated_area_placement():
    app, project = small_project()
    with pytest.raises(ValueError, match="дороги"):
        app.preview_recommendation(
            project.id, request(project, arrangement="road_edges")
        )
    assert not app.changes._previews
    assert app.get(project.id).plan.objects == []


def test_automatic_mode_does_not_infer_missing_zone_category():
    app, project = small_project()
    project.planting_zones[1].territory = None
    app.repository.save(project)
    with pytest.raises(ValueError, match="категорию"):
        app.preview_recommendation(project.id, request(project))
    assert not app.changes._previews


def test_configured_request_cannot_mislabel_area_as_road_proposal():
    with pytest.raises(ValueError, match="автоматический"):
        RecommendationRequest(
            base_plan_version=1, zone_ids=["west"], arrangement="road_edges"
        )


@pytest.mark.parametrize("network_type", ["gas", "heat"])
def test_automatic_composition_preserves_network_rules_and_trace(network_type):
    app, project = small_project()
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/network-crossing.json"
    )
    source = Project.model_validate(json.loads(path.read_bytes())["project"])
    project.geometry, project.layers = source.geometry, source.layers
    project.planting_zones = project.planting_zones[:1]
    project.planting_zones[0].geometry = mapping(box(35, 5, 65, 25))
    properties = project.geometry.feature_collection["features"][1]["properties"]
    properties["utility_context"]["network_type"] = network_type
    reference = "channel_wall" if network_type == "heat" else "outer_surface"
    properties["utility_context"]["geometry_reference"] = reference
    project.layers[0].utility_context.network_type = network_type
    project.layers[0].utility_context.geometry_reference = reference
    app.repository.save(project)
    result = app.preview_recommendation(project.id, request(project))
    assert result.change_set and result.change_set.can_apply
    assert {p.kind for p in result.change_set.additions} == {"tree", "shrub"}
    for candidate in result.change_set.candidate_results:
        trace = candidate.rule_trace
        network = [entry for entry in trace.entries if entry.obstacle_kind == "utility"]
        assert network
        for entry in network:
            assert entry.status in {"passed", "not_checked"}
            assert entry.document_code and entry.clause
            if entry.required_distance_m is not None:
                assert entry.actual_distance_m >= entry.required_distance_m
    assert app.get(project.id).plan.objects == []


def test_candidate_budget_does_not_publish_a_partial_proposal(monkeypatch):
    app, project = small_project()
    original = PatternApplication.preview_on_snapshot

    def saturated(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.model_copy(update={"generated_count": MAX_PATTERN_CANDIDATES})

    monkeypatch.setattr(PatternApplication, "preview_on_snapshot", saturated)
    with pytest.raises(ValueError, match="предела"):
        app.preview_recommendation(project.id, request(project))
    assert not app.changes._previews
    assert app.get(project.id).plan.objects == []


def test_automatic_mode_requires_computed_constraints():
    app, project = small_project()
    project.source_review = SourceReview()
    app.repository.save(project)
    with pytest.raises(ValueError, match="расчёта ограничений"):
        app.preview_recommendation(project.id, request(project))
    assert not app.changes._previews
