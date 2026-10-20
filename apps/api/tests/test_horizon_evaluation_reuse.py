"""Reuse is valid only for an immutable position/source after a full base check."""

from itertools import count
from unittest.mock import Mock

import pytest
from shapely.geometry import box, mapping

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.planning.changes.evaluation import CandidateRejected, PlanEvaluation
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate
from app.planning.domain import PlantSpacingIndex
from app.planning.rules import compiled_planting_zones
from app.planning.scenario import scenario_project
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.species.catalog import get_species, growth_forecasts

TREE_SPECIES = ("tilia-cordata@2026-08-28.1", "quercus-robur@2026-08-28.1")


def _project_and_payload(species, situation, partial):
    features = [
        {
            "type": "Feature",
            "id": "site",
            "properties": {"kind": "site_border"},
            "geometry": mapping(box(-100, -100, 200, 200)),
        }
    ]
    x, y = 50, 50
    if situation == "building":
        x = 16
        features.append(
            {
                "type": "Feature",
                "id": "building",
                "properties": {"kind": "building", "source_layer": "Buildings"},
                "geometry": mapping(box(0, 0, 10, 100)),
            }
        )
    if situation == "zone_edge":
        x = 4
    objects = []
    if situation == "saved_plant":
        canopy, roots = growth_forecasts(get_species(species), "standard")
        objects.append(
            PlanObject(
                id="saved-tree",
                kind="tree",
                x=x + 10,
                y=y,
                radius=1.6,
                species_revision_id=species,
                size_class="standard",
                canopy_forecast=canopy,
                root_forecast=roots,
                group_ids=["different-existing-group"],
            )
        )
    project = Project(
        id="isolated-evaluation-reuse",
        name="No repository, CAD or external service",
        geometry_version=3,
        state_version=4,
        geometry=GeometrySnapshot(
            calculation_scope="available_data" if partial else "complete_source",
            feature_collection={"type": "FeatureCollection", "features": features},
        ),
        planting_zones=[
            PlantingZoneAssignment(
                id="work-zone",
                label="Immutable working zone",
                geometry=mapping(box(0, 0, 100, 100)),
            )
        ],
        plan=Plan(version=2, planning_horizon_year=0, objects=objects),
    )
    payload = PlanObjectCreate(
        kind="tree",
        x=x,
        y=y,
        layout_radius_m=1.6,
        species_revision_id=species,
        size_class="standard",
        group_ids=["canonical-request-group"],
        spacing_policy="balanced",
    )
    return project, payload


def _outcome(operation):
    try:
        return "evaluated", operation()
    except CandidateRejected as error:
        return "rejected", error.issue


@pytest.mark.parametrize("species", TREE_SPECIES)
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("situation", ["clear", "building", "saved_plant", "zone_edge"])
def test_reused_horizon_matches_full_checker_in_all_41_years(
    species, partial, situation, monkeypatch
):
    project, payload = _project_and_payload(species, situation, partial)
    source_before = project.model_dump_json()
    payload_before = payload.model_dump_json()
    identifiers = count()
    reused_geometry = ShapelyGeometryEngine()
    static_position = Mock(wraps=reused_geometry.position_violation)
    static_advisory = Mock(wraps=reused_geometry.placement_advisory_detail)
    monkeypatch.setattr(reused_geometry, "position_violation", static_position)
    monkeypatch.setattr(reused_geometry, "placement_advisory_detail", static_advisory)
    reused = PlanEvaluation(reused_geometry, lambda: f"reused-{next(identifiers)}")
    fresh = PlanEvaluation(
        ShapelyGeometryEngine(), lambda: f"fresh-{next(identifiers)}"
    )
    compiled = compiled_planting_zones(project)
    assert project.plan is not None
    cached, base_issue = reused.preview_addition(
        project,
        project.plan,
        payload,
        PlantSpacingIndex(project.plan.objects, planning_horizon_year=0),
        compiled,
    )
    # The coordinator reuses only a base-admitted point. An existing growth
    # conflict at year0 must not be carried forward as a static source issue.
    assert base_issue is None or base_issue.code == "SOURCE_GEOMETRY_PARTIAL"
    cached_before = cached.model_dump_json()
    base_issue_before = base_issue
    observed = set()
    for year in range(41):
        scenario = scenario_project(project, year)
        assert scenario.plan is not None
        spacing = PlantSpacingIndex(scenario.plan.objects, planning_horizon_year=year)
        reused_result = _outcome(
            lambda: reused.preview_horizon(
                scenario,
                scenario.plan,
                cached,
                spacing,
                base_issue=base_issue,
            )
        )
        fresh_result = _outcome(
            lambda: fresh.preview_addition(
                scenario, scenario.plan, payload, spacing, compiled
            )[1]
        )
        assert reused_result == fresh_result, (species, partial, situation, year)
        issue = reused_result[1]
        if issue is not None:
            observed.add(issue.code)
            assert issue.zone_id == "work-zone"
        assert cached.model_dump_json() == cached_before
        assert base_issue == base_issue_before
    assert static_position.call_count == static_advisory.call_count == 1
    assert project.model_dump_json() == source_before
    assert payload.model_dump_json() == payload_before
    if situation == "building":
        assert "CANOPY_BUILDING_REVIEW" in observed
    elif situation == "saved_plant":
        assert "PLANT_SPACING" in observed
    elif situation == "zone_edge":
        assert "GROWTH_PLANTING_ZONE" in observed
    elif partial:
        assert observed == {"SOURCE_GEOMETRY_PARTIAL"}
    else:
        assert observed == set()


def test_generic_plant_without_species_still_rechecks_saved_plant_growth():
    project, payload = _project_and_payload(TREE_SPECIES[0], "saved_plant", False)
    payload.species_revision_id = None
    assert project.plan is not None
    project.plan.objects[0].x = payload.x + 8
    evaluation = PlanEvaluation(ShapelyGeometryEngine(), lambda: "generic-candidate")
    assert project.plan is not None
    cached, base_issue = evaluation.preview_addition(project, project.plan, payload)
    assert base_issue is None
    observed = set()
    for year in range(41):
        scenario = scenario_project(project, year)
        assert scenario.plan is not None
        # Also cover the fallback path that constructs its own spacing index.
        reused = _outcome(
            lambda: evaluation.preview_horizon(
                scenario, scenario.plan, cached, base_issue=base_issue
            )
        )
        fresh = _outcome(
            lambda: evaluation.preview_addition(scenario, scenario.plan, payload)[1]
        )
        assert reused == fresh
        if reused[1] is not None:
            observed.add(reused[1].code)
    assert observed == {"PLANT_SPACING"}
