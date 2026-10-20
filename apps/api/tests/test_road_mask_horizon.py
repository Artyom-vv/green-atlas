"""Аллеи: горизонт роста, точность исходника и минимальный шаг общего проверяющего модуля."""

from itertools import combinations
from math import hypot
from typing import cast

import pytest
from shapely.geometry import LineString, Point, box, mapping, shape
from test_hybrid_search import populate
from test_native_live_provider import setup as setup
from test_placement_allocation import application

from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import prepare_snapshot
from app.planning.domain import required_spacing
from app.planning.patterns.contracts import PlacementMaskRequest
from app.planning.patterns.generators import generate_mask
from app.planning.rules import pattern_growth_radii
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.validation.adapters import RuleBasedPlanValidator

TREE = "tilia-cordata@2026-08-28.1"


def test_native_road_now_keeps_places_with_sampling_tolerance(setup) -> None:
    live, _, project = setup
    populate(project)
    assert project.geometry is not None and project.source_file is not None
    project.source_file.native_session = live.session
    road = project.geometry.feature_collection["features"][2]
    road["properties"].update(kind="road", source_sampling_tolerance_m=0.02)
    engine = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    app, _ = application()
    app.geometry = app.evaluation.geometry = engine
    app.validation.validator = RuleBasedPlanValidator(geometry=engine)
    app.repository.create(project)
    request = PlacementMaskRequest(
        base_plan_version=1,
        zone_ids=["work"],
        mask_id="road_edges",
        target_count=40,
        species_revision_id=TREE,
    )
    counts = {}
    try:
        for horizon in (0, 9):
            before = app.get(project.id).model_dump_json()
            result = app.preview_pattern(
                project.id,
                request.model_copy(update={"scenario_horizon_year": horizon}),
            )
            counts[horizon] = result.accepted_count
            assert result.change_set is not None and result.change_set.can_apply
            radii = pattern_growth_radii(request, horizon)
            assert radii is not None
            for item in result.change_set.additions:
                assert (
                    engine.position_violation(
                        project, item.x, item.y, item.radius, item.kind
                    )
                    is None
                )
                advisory = engine.future_growth_advisory_detail(
                    project, item.x, item.y, *radii, item.kind
                )
                assert advisory is None or advisory.code == "SOURCE_GEOMETRY_PARTIAL"
            assert all(
                hypot(a.x - b.x, a.y - b.y) >= required_spacing(a, b, horizon)
                for a, b in combinations(result.change_set.additions, 2)
            )
            assert app.get(project.id).model_dump_json() == before
        assert counts[0] >= counts[9] > 0
    finally:
        cast(SqliteProjectRepository, app.repository).close()


@pytest.mark.parametrize("target", [25, 150])
def test_large_road_row_does_not_reject_neighbours_after_rounding(target: int) -> None:
    app, project = application()
    assert project.geometry is not None and project.plan is not None
    project.planting_zones = [
        project.planting_zones[0].model_copy(
            update={"geometry": mapping(box(0, 0, 700, 200))}
        )
    ]
    project.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "properties": {"kind": "site_border"},
            "geometry": mapping(box(-10, -10, 710, 210)),
        },
        {
            "type": "Feature",
            "properties": {"kind": "road"},
            "geometry": mapping(LineString([(10, 100), (690, 100)])),
        },
    ]
    app.repository.save(project)
    request = PlacementMaskRequest(
        scenario_horizon_year=9,
        base_plan_version=1,
        zone_ids=["west"],
        mask_id="road_edges",
        target_count=target,
        species_revision_id=TREE,
    )
    try:
        before = app.get(project.id).model_dump_json()
        result = app.preview_pattern(project.id, request)
        assert result.accepted_count == target
        assert result.change_set is not None and result.change_set.can_apply
        additions = result.change_set.additions
        assert all(
            hypot(a.x - b.x, a.y - b.y) >= required_spacing(a, b, 9)
            for a, b in combinations(additions, 2)
        )
        for item in additions:
            assert (
                app.geometry.position_violation(
                    project, item.x, item.y, item.radius, item.kind
                )
                is None
            )
        assert app.get(project.id).model_dump_json() == before
    finally:
        cast(SqliteProjectRepository, app.repository).close()


def test_prepared_thin_corridor_keeps_holes_and_work_boundary_offset() -> None:
    work = box(0, 0, 100, 100)
    hole = box(9, 40, 11, 60)
    allowed = box(9.8, 0, 10.2, 100).difference(hole)
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry=mapping(allowed) | {"ga_work_zone": mapping(work)},
    )
    guide = mapping(LineString([(7, 0), (7, 100)]))
    request = PlacementMaskRequest(
        base_plan_version=1,
        zone_ids=["work"],
        mask_id="road_edges",
        target_count=100,
        spacing_m=5,
        road_offset_m=3,
        edge_offset_m=1,
    )
    points = generate_mask(request, [zone], [guide])
    assert len(points) >= 12
    assert all(
        allowed.covers(Point(p.x, p.y))
        and work.buffer(-1).covers(Point(p.x, p.y))
        and not hole.covers(Point(p.x, p.y))
        for p in points
    )
    # Для ручного узкого участка тот же отступ действительно исчерпывает ширину.
    manual = zone.model_copy(update={"geometry": mapping(shape(zone.geometry))})
    assert generate_mask(request, [manual], [guide]) == []
