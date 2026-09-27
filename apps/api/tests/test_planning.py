from math import hypot

from shapely.geometry import Point, shape

from app.contracts import BrushPreviewRequest, FillPatternRequest, GeometrySnapshot, GrowthEnvelopeForecast, PlacementMaskRequest, Plan, PlanObject, PlantingZoneAssignment, Project
from app.geometry.domain import PositionChecker
from app.planning.domain import PlantSpacingIndex
from app.planning.patterns import generate_brush, generate_fill, generate_mask
from app.species.catalog import get_species, growth_forecasts
from app.validation.adapters import RuleBasedPlanValidator


def test_spacing_index_never_misses_large_crowns_across_several_grid_cells() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=25)
    candidate = PlanObject(kind="tree", x=40, y=0, radius=15)
    index = PlantSpacingIndex([first])

    assert index.respects(candidate) is False


def test_spacing_index_keeps_dense_manual_checks_local() -> None:
    objects = [
        PlanObject(kind="tree", x=(index % 100) * 8, y=(index // 100) * 8, radius=1.6)
        for index in range(10_000)
    ]
    index = PlantSpacingIndex(objects)

    nearby = index.nearby(PlanObject(kind="tree", x=400, y=400, radius=1.6))

    assert len(nearby) < 20


def forecast(radius: float) -> list[GrowthEnvelopeForecast]:
    return [GrowthEnvelopeForecast(horizon_year=20, radius_min_m=radius - 1, radius_max_m=radius, confidence="low", basis="test")]


def test_spacing_uses_mature_crowns_when_species_is_known() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=1.6, canopy_forecast=forecast(6))
    index = PlantSpacingIndex([first])

    assert index.respects(PlanObject(kind="tree", x=12, y=0, radius=1.6, canopy_forecast=forecast(6))) is False
    assert index.respects(PlanObject(kind="tree", x=14, y=0, radius=1.6, canopy_forecast=forecast(6))) is True


def test_dense_group_can_close_canopies_without_disabling_external_spacing() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["grove"], spacing_policy="canopy")
    index = PlantSpacingIndex([first])

    assert index.respects(PlanObject(kind="tree", x=7, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["grove"], spacing_policy="canopy")) is True
    assert index.respects(PlanObject(kind="tree", x=7, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["other"], spacing_policy="canopy")) is False


def test_growth_envelope_keeps_automatic_layout_inside_the_selected_area() -> None:
    area = {"type": "Polygon", "coordinates": [[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]]]}
    project = Project(
        name="Прогноз границы",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"kind": "site_border"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
            }],
        }),
        planting_zones=[PlantingZoneAssignment(label="Участок", geometry=area)],
    )

    advisory = PositionChecker(project).growth_advisory(14, 50, canopy_radius=6, root_radius=7)

    assert advisory is not None
    assert advisory.code == "GROWTH_PLANTING_ZONE"


def test_automatic_safe_area_uses_the_same_growth_envelope_as_validation() -> None:
    area = {"type": "Polygon", "coordinates": [[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]]]}
    project = Project(
        name="Будущая безопасная область",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"kind": "site_border"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
            }],
        }),
        planting_zones=[PlantingZoneAssignment(id="work", label="Участок", geometry=area)],
    )
    checker = PositionChecker(project)
    safe = checker.automatic_safe_area(shape(area), radius=1.6, plant_kind="tree", growth_canopy_radius=6, growth_root_radius=7)

    assert safe.covers(Point(50, 50))
    assert not safe.covers(Point(14, 50))
    assert checker.growth_advisory(50, 50, canopy_radius=6, root_radius=7) is None


def test_wide_crown_is_not_a_validation_issue_without_a_real_conflict() -> None:
    area = {"type": "Polygon", "coordinates": [[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]]]}
    project = Project(
        name="Широкая крона без конфликта",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"kind": "site_border"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
            }],
        }),
        planting_zones=[PlantingZoneAssignment(id="work", label="Участок", geometry=area)],
    )
    revision = get_species("tilia-cordata@2026-08-28.1")
    canopy, roots = growth_forecasts(revision, "standard")
    plan = Plan(objects=[PlanObject(
        kind="tree",
        x=50,
        y=50,
        radius=1.6,
        species_revision_id=revision.id,
        canopy_forecast=canopy,
        root_forecast=roots,
    )])

    issues = RuleBasedPlanValidator().validate_plan(project, plan)

    # Missing engineering data limits verification independently of crown size.
    assert plan.objects[0].status == "warning"
    assert any(issue.code == "NO_NETWORK_FEATURES" for issue in issues)
    assert all(issue.code != "CROWN_SETBACK_REVIEW" for issue in issues)


def test_hard_safe_area_removes_boundaries_infrastructure_and_existing_green() -> None:
    area = {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]}
    project = Project(
        name="Безопасная область",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"kind": "site_border"}, "geometry": area},
                {"type": "Feature", "properties": {"kind": "building"}, "geometry": {"type": "Polygon", "coordinates": [[[40, 40], [60, 40], [60, 60], [40, 60], [40, 40]]]}},
                {"type": "Feature", "properties": {"kind": "road"}, "geometry": {"type": "LineString", "coordinates": [[0, 20], [100, 20]]}},
                {"type": "Feature", "properties": {"kind": "existing_green"}, "geometry": {"type": "Point", "coordinates": [80, 80]}},
            ],
        }),
    )

    safe = PositionChecker(project).hard_safe_area(shape(area), radius=1.6, plant_kind="tree")

    assert safe.covers(Point(10, 10))
    assert not safe.covers(Point(0.5, 50))
    assert not safe.covers(Point(50, 50))
    assert not safe.covers(Point(50, 21))
    assert not safe.covers(Point(80, 80))


def test_natural_fill_is_deterministic_blue_noise_without_coordinate_rails() -> None:
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [120, 0], [120, 80], [0, 80], [0, 0]]]},
    )
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["work"],
        placement_mode="count",
        target_count=80,
        layout="natural",
        spacing_m=6,
        edge_offset_m=2,
        seed=47,
    )

    first = generate_fill(request, [zone])
    second = generate_fill(request, [zone])

    assert first == second
    assert len(first) == 80
    assert len({item.x for item in first}) == len(first)
    assert len({item.y for item in first}) == len(first)
    assert all(
        hypot(first[index].x - first[other].x, first[index].y - first[other].y) >= 6 - 1e-6
        for index in range(len(first))
        for other in range(index)
    )


def test_brush_uses_stable_blue_noise_and_density_changes_capacity() -> None:
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [120, 0], [120, 80], [0, 80], [0, 0]]]},
    )
    common = {
        "base_plan_version": 1,
        "zone_ids": ["work"],
        "strokes": [{"mode": "add", "geometry": {"type": "LineString", "coordinates": [[10, 40], [110, 40]]}}],
        "width_m": 40,
        "spacing_m": 5,
        "composition": "trees",
        "seed": 19,
        "max_sites": 500,
    }

    sparse = generate_brush(BrushPreviewRequest(**common, density="sparse"), [zone])
    dense = generate_brush(BrushPreviewRequest(**common, density="dense"), [zone])
    repeated = generate_brush(BrushPreviewRequest(**common, density="dense"), [zone])

    assert dense == repeated
    assert len(dense) > len(sparse)
    assert len({item.x for item in dense}) == len(dense)
    assert len({item.y for item in dense}) == len(dense)
    assert all(
        hypot(dense[index].x - dense[other].x, dense[index].y - dense[other].y) >= 5 - 1e-6
        for index in range(len(dense))
        for other in range(index)
    )


def test_regular_grid_mask_is_deterministic_and_keeps_a_strict_lattice() -> None:
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]]]},
    )
    request = PlacementMaskRequest(
        mask_id="regular_grid",
        base_plan_version=1,
        zone_ids=["work"],
        placement_mode="spacing",
        spacing_m=8,
        edge_offset_m=4,
        angle_deg=0,
    )

    first = generate_mask(request, [zone])
    second = generate_mask(request, [zone])

    assert first == second
    assert len(first) > 20
    assert {round(item.x % 8, 6) for item in first} == {0}
    assert {round(item.y % 8, 6) for item in first} == {0}


def test_cluster_groves_mask_creates_separated_compact_groups() -> None:
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [140, 0], [140, 100], [0, 100], [0, 0]]]},
    )
    request = PlacementMaskRequest(
        mask_id="cluster_groves",
        base_plan_version=1,
        zone_ids=["work"],
        placement_mode="count",
        target_count=28,
        spacing_m=5,
        edge_offset_m=8,
        cluster_gap_m=28,
        cluster_size=7,
        seed=29,
    )

    first = generate_mask(request, [zone])
    second = generate_mask(request, [zone])

    assert first == second
    assert len(first) >= 21
    assert all(
        hypot(first[index].x - first[other].x, first[index].y - first[other].y) >= 5 - 1e-6
        for index in range(len(first))
        for other in range(index)
    )
    assert sum(
        any(4.9 <= hypot(item.x - other.x, item.y - other.y) <= 5.1 for other in first if other != item)
        for item in first
    ) >= len(first) // 2


def test_road_edge_mask_follows_all_guides_instead_of_filling_the_zone() -> None:
    zone = PlantingZoneAssignment(
        id="work",
        label="Участок",
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [120, 0], [120, 120], [0, 120], [0, 0]]]},
    )
    guides = [
        {"type": "LineString", "coordinates": [[10, 25], [110, 25]]},
        {"type": "LineString", "coordinates": [[10, 90], [110, 90]]},
    ]
    request = PlacementMaskRequest(
        mask_id="road_edges",
        base_plan_version=1,
        zone_ids=["work"],
        placement_mode="count",
        target_count=30,
        spacing_m=7,
        edge_offset_m=2,
        road_offset_m=4,
    )

    candidates = generate_mask(request, [zone], guides)
    guide_lines = [shape(value) for value in guides]

    assert len(candidates) == 30
    # GEOS approximates round buffer caps, hence the centimetre tolerance.
    assert all(abs(min(Point(item.x, item.y).distance(line) for line in guide_lines) - 4) < 0.01 for item in candidates)
    assert any(abs(item.y - 25) < 6 for item in candidates)
    assert any(abs(item.y - 90) < 6 for item in candidates)
