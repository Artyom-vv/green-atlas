from itertools import combinations
from math import cos, hypot, radians, sin, sqrt

import pytest
from regular_grid_baseline import BaselineRegularGrid
from shapely.affinity import rotate
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping

from app.planning.patterns import generators as patterns
from app.planning.patterns.contracts import FillPatternRequest
from app.planning.ports import PatternCandidate
from app.planning.search import regular_grid
from app.planning.search.allocation import spread_indices
from app.planning.search.regular_grid import RegularFillGrid
from app.planting_zones.contracts import PlantingZoneAssignment

GEOMETRIES = [
    box(-30, -20, 50, 40),
    Polygon(
        [(-40, -20), (60, -20), (60, 40), (-40, 40)],
        [[(0, 0), (15, 0), (15, 15), (0, 15)]],
    ),
    MultiPolygon([box(-45, -10, -20, 10), box(15, 30, 40, 55)]),
    rotate(box(-30, -2, 60, 2), 37, origin=(0, 0)),
]


@pytest.mark.parametrize("geometry", GEOMETRIES)
@pytest.mark.parametrize("layout", ["regular", "staggered"])
@pytest.mark.parametrize("angle", [0, 27, -90, 180])
def test_exact_points_order_phase_and_count_match_previous_loop(
    geometry, layout, angle
):
    current = RegularFillGrid(geometry, angle, layout)
    baseline = BaselineRegularGrid(geometry, angle, layout)
    for spacing in (3.1, 9.5):
        expected = baseline.candidates(spacing)
        assert current.candidates(spacing) == expected
        for limit in (1, 5, 5000):
            assert current.count(spacing, limit) == min(len(expected), limit)


@pytest.mark.parametrize("layout", ["regular", "staggered"])
@pytest.mark.parametrize("target", [1, 12, 60])
def test_count_keeps_one_contained_lattice_and_bounds_the_reserve(
    monkeypatch, layout, target
):
    variants = []
    original_variant = RegularFillGrid._count_variant

    def record_variant(self, parts, spacing, *args):
        result = original_variant(self, parts, spacing, *args)
        variants.append((result[0], spacing))
        return result

    monkeypatch.setattr(RegularFillGrid, "_count_variant", record_variant)
    zones = [
        PlantingZoneAssignment(
            id="zone", label="fixture", geometry=mapping(GEOMETRIES[1])
        )
    ]
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["zone"],
        layout=layout,
        placement_mode="count",
        target_count=target,
        spacing_m=3,
        angle_deg=-33,
    )
    actual = patterns.generate_fill(request, zones)
    assert len(actual) == target
    assert actual == patterns.generate_fill(request, zones)
    usable = patterns.usable_zone_geometry(zones[0], request.edge_offset_m)
    assert all(usable.covers(Point(point.x, point.y)) for point in actual)
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= request.spacing_m
        for first, second in combinations(actual, 2)
    )
    # Выбран один рисунок, а не смесь узлов нескольких фаз или шагов.
    spacing = next(spacing for points, spacing in variants if points is actual)
    assert spacing >= request.spacing_m
    first = actual[0]
    angle = radians(request.angle_deg)
    row_step = spacing * (sqrt(3) / 2 if layout == "staggered" else 1)
    for point in actual:
        dx, dy = point.x - first.x, point.y - first.y
        x = dx * cos(angle) + dy * sin(angle)
        row = (-dx * sin(angle) + dy * cos(angle)) / row_step
        assert row == pytest.approx(round(row), abs=2e-6)
        column = x / spacing
        if layout == "staggered" and round(row) % 2:
            column -= 0.5
        assert column == pytest.approx(round(column), abs=2e-6)


def test_early_count_reserve_and_final_six_cover_both_ends_of_the_zone():
    zone = PlantingZoneAssignment(
        id="zone", label="fixture", geometry=mapping(box(0, 0, 100, 100))
    )
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["zone"],
        layout="regular",
        placement_mode="count",
        target_count=48,
        spacing_m=6.73,
        edge_offset_m=0,
    )
    reserve = patterns.generate_fill(request, [zone])
    assert len(reserve) == 48
    actual = [reserve[index] for index in spread_indices(list(range(48)), 6)]
    assert min(point.y for point in actual) < 25
    assert max(point.y for point in actual) > 75
    assert min(point.x for point in actual) < 25
    assert max(point.x for point in actual) > 75


@pytest.mark.parametrize("angle", [44, 51])
def test_count_rounding_does_not_undercut_the_strict_minimum_spacing(angle):
    from shapely.affinity import translate

    geometry = translate(
        rotate(box(-5.336, -1.811, 5.336, 1.811), angle, origin=(0, 0)),
        100,
        100,
    )
    points = RegularFillGrid(geometry, angle, "regular").count_candidates(3.5, 3)
    assert len(points) == 3
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= 3.5
        for first, second in combinations(points, 2)
    )
    assert all(geometry.covers(Point(point.x, point.y)) for point in points)


def test_raw_minimum_recovers_exact_boundary_positions_without_weakening_spacing():
    geometry = box(-3.5, -0.05, 3.5, 0.05)
    points = RegularFillGrid(geometry, 0, "regular").count_candidates(3.5, 3)
    assert {(point.x, point.y) for point in points} == {
        (-3.5, 0),
        (0, 0),
        (3.5, 0),
    }
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= 3.5
        for first, second in combinations(points, 2)
    )


@pytest.mark.parametrize("layout", ["regular", "staggered"])
def test_lower_horizon_keeps_the_feasible_count_when_reserve_is_unreachable(layout):
    # Области центров одной липы после обычной подготовки на 7 и 5 лет.
    # Пользователь просит 6 позиций, внутренний резерв составляет 48.
    rows = []
    for bounds, spacing in (
        ((83.264, 94.064, 84.336, 131.436), 7.65),
        ((82.7, 93.5, 84.9, 132.0), 6.73),
    ):
        zone = PlantingZoneAssignment(
            id="zone", label="fixture", geometry=mapping(box(*bounds))
        )
        rows.append(
            patterns.generate_fill(
                FillPatternRequest(
                    base_plan_version=1,
                    zone_ids=["zone"],
                    layout=layout,
                    placement_mode="count",
                    target_count=48,
                    spacing_m=spacing,
                    edge_offset_m=0,
                ),
                [zone],
            )
        )
    assert 0 < len(rows[0]) < 48
    assert len(rows[1]) >= len(rows[0])
    if layout == "regular":
        assert len(rows[1]) >= 6


@pytest.mark.parametrize("layout", ["regular", "staggered"])
@pytest.mark.parametrize("angle", [0, 31, -90])
def test_count_is_equivariant_under_world_translation(layout, angle):
    from shapely.affinity import translate

    geometry = GEOMETRIES[1]
    offset = 12_345.25, -8_765.5
    original = RegularFillGrid(geometry, angle, layout).count_candidates(7, 20)
    shifted = RegularFillGrid(
        translate(geometry, *offset), angle, layout
    ).count_candidates(7, 20)
    assert len(original) == len(shifted) == 20
    for before, after in zip(original, shifted, strict=True):
        assert after.x == pytest.approx(before.x + offset[0], abs=2e-6)
        assert after.y == pytest.approx(before.y + offset[1], abs=2e-6)


def test_count_probe_budget_bounds_huge_sparse_lattices(monkeypatch):
    monkeypatch.setattr(regular_grid, "COUNT_GRID_MAX_PROBES", 25)
    monkeypatch.setattr(regular_grid, "COUNT_GRID_VARIANT_PROBES", 10)
    checks = []
    original_prep = regular_grid.prep

    class Coverage:
        def __init__(self, geometry):
            self.real = original_prep(geometry)

        def covers(self, point):
            checks.append(point)
            return self.real.covers(point)

    monkeypatch.setattr(regular_grid, "prep", Coverage)
    grid = RegularFillGrid(
        MultiPolygon([box(-1e9, 0, -1e9 + 100, 100), box(1e9, 0, 1e9 + 100, 100)]),
        0,
        "regular",
    )
    points = grid.count_candidates(0.5, 50)
    assert 0 < len(points) < 50
    assert len(checks) <= 25


def test_count_propagates_cancellation(monkeypatch):
    from app.operations.progress import OperationCancelled

    def cancel():
        raise OperationCancelled()

    monkeypatch.setattr(regular_grid, "check_operation_cancelled", cancel)
    with pytest.raises(OperationCancelled):
        RegularFillGrid(GEOMETRIES[0], 0, "regular").count_candidates(6, 20)


def test_count_probes_rotate_source_once_and_do_not_construct_candidates(monkeypatch):
    source_rotations = []
    constructed = []
    original_rotate = regular_grid.rotate

    def record_rotation(geometry, *args, **kwargs):
        if geometry.geom_type != "Point":
            source_rotations.append(geometry)
        return original_rotate(geometry, *args, **kwargs)

    def construct(*args):
        constructed.append(args)
        return PatternCandidate(*args)

    monkeypatch.setattr(regular_grid, "rotate", record_rotation)
    monkeypatch.setattr(regular_grid, "PatternCandidate", construct)
    grid = RegularFillGrid(GEOMETRIES[0], 27, "staggered")
    for _ in range(16):
        assert grid.count(5, 12) == 12
    assert len(source_rotations) == 1 and constructed == []
    candidates = grid.candidates(5)
    assert len(constructed) == len(candidates)


def test_boundary_inclusion_and_staggered_parity_are_explicit():
    assert RegularFillGrid(box(0, 0, 2, 2), 0, "regular").candidates(2) == [
        PatternCandidate(0, 0),
        PatternCandidate(2, 0),
        PatternCandidate(0, 2),
        PatternCandidate(2, 2),
    ]
    assert RegularFillGrid(box(0, 0, 2, 2), 0, "staggered").candidates(2) == [
        PatternCandidate(0, 0),
        PatternCandidate(2, 0),
        PatternCandidate(1, 1.732051),
    ]


def test_rounded_dedup_is_preserved_even_for_sub_resolution_fixture():
    geometry = box(0, 0, 1e-6, 1e-6)
    baseline = BaselineRegularGrid(geometry, 27, "regular")
    current = RegularFillGrid(geometry, 27, "regular")
    expected = baseline.candidates(2e-7)
    assert current.candidates(2e-7) == expected
    assert current.count(2e-7, 5000) == len(expected)
