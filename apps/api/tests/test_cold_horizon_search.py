"""Public previews start from independent repositories, without earlier-year witnesses.

Synthetic geometry demonstrates the algorithmic invariant, not CAD completeness
or maximum continuous packing capacity.
"""

from collections import Counter
from collections.abc import Callable
from itertools import combinations
from math import hypot
from typing import cast

import pytest
from shapely.geometry import MultiPolygon, Point, box, mapping
from test_geometric_search_acceptance import _application

from app.application import ProjectApplication
from app.contracts import PlantingZoneAssignment, Project
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.planning.domain import required_spacing
from app.planning.patterns.contracts import FillPatternRequest, PatternPreview
from app.planning.rules import planting_zone_at
from app.planning.scenario import scenario_project
from app.projects.adapters import SqliteProjectRepository
from app.species.catalog import get_species, growth_forecasts

SPRUCE = "picea-pungens@2026-09-17.1"
LINDEN = "tilia-cordata@2026-08-28.1"
SHRUB = "spiraea-japonica@2026-08-28.1"
Setup = Callable[[], tuple[ProjectApplication, Project]]


def _request(year, *, mode="count", species=SPRUCE, **changes):
    return FillPatternRequest(
        base_plan_version=1,
        zone_ids=["outer"],
        layout="natural",
        placement_mode=mode,
        species_revision_id=species,
        target_count=23,
        edge_offset_m=0,
        seed=911,
        scenario_horizon_year=year,
    ).model_copy(update=changes)


def _assert_current_checker(
    app: ProjectApplication,
    project: Project,
    request: FillPatternRequest,
    preview: PatternPreview,
) -> None:
    assert preview.planning_horizon_year == request.scenario_horizon_year
    if preview.accepted_count == 0:
        assert preview.change_set is None
        return
    assert preview.change_set is not None and preview.change_set.can_apply
    assert len(preview.change_set.additions) == preview.accepted_count
    current = scenario_project(project, request.scenario_horizon_year)
    assert current.plan is not None
    working = current.plan.model_copy(deep=True)
    for addition in preview.change_set.additions:
        zone = planting_zone_at(
            current, addition.x, addition.y, addition.layout_radius_m or addition.radius
        )
        assert zone is not None and zone.id in request.zone_ids
        assert addition.planting_zone_id == zone.id
        payload = PlanObjectCreate.model_validate(
            addition.model_dump(exclude={"id", "status", "planting_zone_id"})
        )
        checked, issue = app.evaluation.preview_addition(current, working, payload)
        assert issue is None
        for saved in working.objects:
            assert hypot(checked.x - saved.x, checked.y - saved.y) >= required_spacing(
                checked, saved, request.scenario_horizon_year
            )
        working.objects.append(checked)
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= preview.effective_spacing_m
        for first, second in combinations(preview.change_set.additions, 2)
    )


def _signature(preview: PatternPreview):
    return (
        tuple(
            (
                plant.x,
                plant.y,
                plant.kind,
                plant.species_revision_id,
                plant.planting_zone_id,
            )
            for plant in preview.change_set.additions
        )
        if preview.change_set
        else ()
    )


def _cold(request: FillPatternRequest, setup: Setup | None = None):
    app, project = setup() if setup else _application(box(0, 0, 30, 15))
    try:
        before = app.repository.get(project.id).model_dump(mode="json")
        assert not app.changes._previews
        preview = app.preview_pattern(project.id, request)
        _assert_current_checker(app, project, request, preview)
        assert app.repository.get(project.id).model_dump(mode="json") == before
        return preview
    finally:
        cast(SqliteProjectRepository, app.repository).close()


@pytest.fixture(scope="module", params=["count", "spacing"])
def cold_spruce_series(request):
    # Every single year receives a new application, DB, geometry engine and cache.
    return request.param, {
        year: _cold(_request(year, mode=request.param)) for year in range(41)
    }


def test_all_41_public_cold_years_preserve_count_and_saved_project(cold_spruce_series):
    mode, previews = cold_spruce_series
    counts = [previews[year].accepted_count for year in range(41)]
    assert counts[0] > 0
    assert all(younger >= older for younger, older in zip(counts, counts[1:])), (
        mode,
        counts,
    )
    assert previews[4].accepted_count >= previews[7].accepted_count
    assert previews[4].pattern_id == previews[7].pattern_id
    if mode == "count":
        assert max(counts) <= 23


def test_reversed_cold_requests_match_forward_coordinates(cold_spruce_series):
    mode, previews = cold_spruce_series
    for year in (40, 20, 7, 4, 0):
        reverse = _cold(_request(year, mode=mode))
        assert reverse.accepted_count == previews[year].accepted_count
        assert _signature(reverse) == _signature(previews[year])


def _obstacles():
    app, project = _application(box(0, 0, 60, 30))
    assert project.geometry is not None
    project.geometry.feature_collection["features"].extend(
        [
            {
                "type": "Feature",
                "properties": {"kind": "water"},
                "geometry": mapping(box(24, 0, 28, 30)),
            },
            {
                "type": "Feature",
                "properties": {"kind": "building"},
                "geometry": mapping(box(42, 10, 50, 20)),
            },
        ]
    )
    app.repository.save(project)
    return app, project


@pytest.mark.parametrize("species", [SPRUCE, LINDEN])
def test_two_species_cold_results_respect_water_buildings_and_current_growth(species):
    previews = [
        _cold(_request(year, species=species, target_count=12), _obstacles)
        for year in (0, 4, 7, 20, 40)
    ]
    counts = [preview.accepted_count for preview in previews]
    assert counts[0] > 0
    assert all(younger >= older for younger, older in zip(counts, counts[1:])), counts
    for preview in previews:
        if preview.change_set:
            for plant in preview.change_set.additions:
                assert not box(24, 0, 28, 30).intersects(
                    Point(plant.x, plant.y).buffer(plant.radius)
                )
                assert not box(42, 10, 50, 20).intersects(
                    Point(plant.x, plant.y).buffer(plant.radius)
                )


def _two_zones_saved():
    west, east = box(0, 0, 30, 15), box(100, 0, 130, 15)
    canopy, roots = growth_forecasts(get_species(LINDEN), "standard")
    saved = PlanObject(
        id="saved-tree",
        kind="tree",
        x=15,
        y=7.5,
        radius=1.6,
        species_revision_id=LINDEN,
        canopy_forecast=canopy,
        root_forecast=roots,
        planting_zone_id="west",
    )
    app, project = _application(MultiPolygon([west, east]), saved=[saved])
    project.planting_zones = [
        PlantingZoneAssignment(id="west", label="West", geometry=mapping(west)),
        PlantingZoneAssignment(id="east", label="East", geometry=mapping(east)),
    ]
    app.repository.save(project)
    return app, project


def test_equal_zone_quotas_and_saved_growth_are_cold_monotone():
    previews = [
        _cold(
            _request(
                year,
                target_count=6,
                zone_ids=["east", "west"],
                zone_distribution="equal",
            ),
            _two_zones_saved,
        )
        for year in (0, 4, 7, 40)
    ]
    counts = [preview.accepted_count for preview in previews]
    assert counts[0] > 0
    assert all(younger >= older for younger, older in zip(counts, counts[1:])), counts
    for preview in previews:
        assert [
            (zone.zone_id, zone.requested_count) for zone in preview.zone_allocations
        ] == [("west", 3), ("east", 3)]
        assert all(zone.accepted_count <= 3 for zone in preview.zone_allocations)


def test_mixed_assignment_is_fixed_by_canonical_pool_before_year_selection():
    mappings = []
    counts = []
    for year in (7, 4):
        app, project = _application(box(0, 0, 30, 15))
        observed = {}
        evaluate = app.evaluation.preview_addition

        def record(project_, plan, payload, *args, **kwargs):
            observed[payload.x, payload.y] = (payload.kind, payload.species_revision_id)
            return evaluate(project_, plan, payload, *args, **kwargs)

        app.evaluation.preview_addition = record
        request = _request(
            year,
            target_count=10,
            composition="mixed",
            species_revision_id=None,
            tree_species_revision_id=SPRUCE,
            shrub_species_revision_id=SHRUB,
            tree_share=0.5,
        )
        try:
            before = app.repository.get(project.id).model_dump(mode="json")
            result = app.preview_pattern(project.id, request)
            _assert_current_checker(app, project, request, result)
            assert app.repository.get(project.id).model_dump(mode="json") == before
            mappings.append(observed)
            counts.append(result.accepted_count)
            if result.change_set:
                assert all(
                    plant.species_revision_id
                    == (SPRUCE if plant.kind == "tree" else SHRUB)
                    for plant in result.change_set.additions
                )
        finally:
            cast(SqliteProjectRepository, app.repository).close()
    assert counts[1] >= counts[0] > 0
    assert mappings[0] == mappings[1]
    assert Counter(kind for kind, _ in mappings[0].values())["tree"] > 0
    assert Counter(kind for kind, _ in mappings[0].values())["shrub"] > 0
