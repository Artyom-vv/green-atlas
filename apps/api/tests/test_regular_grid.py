import pytest
from regular_grid_baseline import BaselineRegularGrid
from shapely.affinity import rotate
from shapely.geometry import MultiPolygon, Polygon, box, mapping

from app.planning import patterns, regular_grid
from app.planning.pattern_contracts import FillPatternRequest
from app.planning.ports import PatternCandidate
from app.planning.regular_grid import RegularFillGrid
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
def test_complete_existing_count_search_returns_unchanged_candidates(
    monkeypatch, layout, target
):
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
    monkeypatch.setattr(patterns, "RegularFillGrid", BaselineRegularGrid)
    assert actual == patterns.generate_fill(request, zones)


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
