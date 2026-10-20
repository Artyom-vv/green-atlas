"""The same bounded raw proposals support every requested growth horizon."""

from math import hypot

import pytest
from shapely.affinity import rotate, translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from app.operations.progress import OperationCancelled, operation_cancellation
from app.planning.ports import PatternCandidate
from app.planning.search import horizon_candidates
from app.planning.search.horizon_candidates import (
    HorizonCandidateLimit,
    generate_horizon_candidates,
)


def _generate(geometry=None, **changes):
    values = dict(minimum_spacing0=3, maximum_spacing40=8, target_count=8, seed=47)
    values.update(changes)
    return generate_horizon_candidates(
        box(0, 0, 30, 20) if geometry is None else geometry, **values
    )


def _coordinates(points):
    return [(point.x, point.y) for point in points]


def test_repeated_cold_calls_return_exactly_the_same_ordered_pool():
    assert _generate() == _generate()
    assert 8 <= len(_generate()) <= 5000


def test_raw_alternatives_depend_on_seed_but_are_repeatable():
    assert _generate(seed=1) == _generate(seed=1)
    assert _generate(seed=1) != _generate(seed=2)


def test_count_layouts_use_both_fixed_spacing_endpoints(monkeypatch):
    calls = []

    def propose(geometry, spacing, target, **kwargs):
        calls.append((spacing, target, kwargs["max_layouts"], kwargs["max_segments"]))
        return [[PatternCandidate(spacing, 1)]]

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", propose)
    result = _generate()

    assert calls == [(8, 8, 4, 512), (3, 8, 4, 512)]
    assert _coordinates(result)[:2] == [(8, 1), (3, 1)]


def test_equal_spacing_endpoints_do_not_repeat_layout_generation(monkeypatch):
    calls = []

    def propose(geometry, spacing, target, **kwargs):
        calls.append(spacing)
        return []

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", propose)
    _generate(minimum_spacing0=3, maximum_spacing40=3)
    assert calls == [3]


def test_spacing_mode_uses_raw_irregular_positions_without_n_layouts(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Spacing mode must not build a target-dependent layout")

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", unexpected)
    result = _generate(target_count=None)

    assert len(result) > 5
    assert any(
        point.x != round(point.x) and point.y != round(point.y) for point in result
    )


def test_raw_proposals_do_not_reserve_the_neighborhood_of_prior_proposals():
    result = _generate(
        box(0, 0, 1, 1), minimum_spacing0=3, maximum_spacing40=8, target_count=None
    )

    assert len(result) == 128
    assert hypot(result[0].x - result[-1].x, result[0].y - result[-1].y) < 3


def test_small_region_uses_adaptive_pool_instead_of_always_5000_checks():
    assert len(_generate(box(0, 0, 1, 1), target_count=None)) == 128
    assert (
        len(_generate(box(0, 0, 100, 100), target_count=None, max_candidates=500))
        == 500
    )


def test_large_region_respects_the_production_candidate_cap():
    assert len(_generate(box(0, 0, 1000, 1000), target_count=None)) == 5000


def test_small_count_on_huge_site_bounds_work_without_losing_target_layouts_or_islands(
    monkeypatch,
):
    mainland = box(0, 0, 10000, 10000)
    tiny = box(20000, 20000, 20000.00001, 20000.00001)
    proposal_sizes = []
    original = horizon_candidates.propose_target_layouts

    def record(*args, **kwargs):
        layouts = original(*args, **kwargs)
        proposal_sizes.append(sum(map(len, layouts)))
        return layouts

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", record)
    result = _generate(MultiPolygon([mainland, tiny]), target_count=24)

    assert 24 <= len(result) <= 24 * 16
    assert len(proposal_sizes) == 2
    assert all(size <= 4 * 24 for size in proposal_sizes)
    assert any(mainland.covers(Point(point.x, point.y)) for point in result)
    assert any(tiny.covers(Point(point.x, point.y)) for point in result)


def test_holes_and_concave_boundaries_cover_every_rounded_candidate():
    outer = [(0, 0), (20, 0), (20, 4), (6, 4), (6, 20), (0, 20), (0, 0)]
    hole = [(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]
    geometry = Polygon(outer, [hole])
    result = _generate(geometry)

    assert result
    assert all(geometry.covers(Point(point.x, point.y)) for point in result)
    assert all(
        (point.x, point.y) == (round(point.x, 6), round(point.y, 6)) for point in result
    )
    assert len(set(_coordinates(result))) == len(result)


def test_sparse_rotated_corridor_samples_its_local_window():
    geometry = translate(rotate(box(0, 0, 100, 0.01), 37), 1000, -2000)
    result = _generate(geometry, target_count=None)

    assert len(result) >= 100
    assert all(geometry.covers(Point(point.x, point.y)) for point in result)


@pytest.mark.parametrize("target_count", [None, 8])
def test_tiny_remote_islands_are_not_lost_to_area_weighted_sampling(target_count):
    mainland = box(0, 0, 100, 100)
    tiny = box(1000, 1000, 1000.00001, 1000.00001)
    geometry = MultiPolygon([mainland, tiny])
    result = _generate(geometry, target_count=target_count, max_candidates=128)

    assert any(mainland.covers(Point(point.x, point.y)) for point in result)
    assert any(tiny.covers(Point(point.x, point.y)) for point in result)


def test_component_coverage_is_reserved_even_if_target_layouts_fill_the_budget(
    monkeypatch,
):
    mainland, tiny = box(0, 0, 20, 20), box(1000, 1000, 1000.00001, 1000.00001)

    def propose(*args, **kwargs):
        return [
            [PatternCandidate(2, 2), PatternCandidate(3, 3), PatternCandidate(4, 4)]
        ]

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", propose)
    result = _generate(MultiPolygon([mainland, tiny]), max_candidates=2)

    assert len(result) == 2
    assert any(tiny.covers(Point(point.x, point.y)) for point in result)


def test_component_input_order_does_not_change_canonical_candidate_order():
    parts = [box(0, 0, 10, 10), box(100, 20, 110, 30), box(-20, 2, -19, 3)]
    assert _generate(MultiPolygon(parts)) == _generate(MultiPolygon(parts[::-1]))


def test_distinct_layouts_and_rounding_do_not_create_duplicate_candidates(monkeypatch):
    def propose(*args, **kwargs):
        return [[PatternCandidate(1.00000001, 1), PatternCandidate(1.00000002, 1)]] * 4

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", propose)
    result = _generate(max_candidates=16)

    assert _coordinates(result)[0] == (1, 1)
    assert len(result) == len(set(_coordinates(result))) == 16


def test_rounding_a_proposed_position_into_a_hole_does_not_publish_it(monkeypatch):
    geometry = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]]
    )

    def propose(*args, **kwargs):
        return [[PatternCandidate(5, 5), PatternCandidate(2, 2)]]

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", propose)
    result = _generate(geometry)

    assert (5, 5) not in _coordinates(result)
    assert (2, 2) in _coordinates(result)


def test_too_many_components_fail_explicitly_instead_of_silently_dropping_islands():
    geometry = MultiPolygon([box(x * 10, 0, x * 10 + 1, 1) for x in range(3)])
    with pytest.raises(HorizonCandidateLimit, match="компонентов"):
        _generate(geometry, max_candidates=2)


def test_unrepresentable_submicron_island_is_not_silently_omitted():
    geometry = box(0.0000001, 0.0000001, 0.0000002, 0.0000002)
    result = _generate(geometry, target_count=None)

    assert len(result) == 1
    assert geometry.covers(Point(result[0].x, result[0].y))
    assert result[0].x != round(result[0].x, 6)


def test_cancellation_aborts_generation_without_returning_a_clock_dependent_prefix():
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise OperationCancelled("isolated cancellation")

    with operation_cancellation(cancel):
        with pytest.raises(OperationCancelled, match="isolated"):
            _generate(target_count=None)
    assert checks == 2


def test_layout_generation_failure_does_not_turn_into_a_partial_pool(monkeypatch):
    def failed(*args, **kwargs):
        raise TimeoutError("deterministic outer operation deadline")

    monkeypatch.setattr(horizon_candidates, "propose_target_layouts", failed)
    with pytest.raises(TimeoutError):
        _generate()


def test_empty_polygon_has_no_candidates():
    assert _generate(Polygon()) == []


@pytest.mark.parametrize(
    "geometry",
    [LineString([(0, 0), (1, 1)]), Polygon([(0, 0), (1, 1), (0, 1), (1, 0)])],
)
def test_invalid_or_nonpolygonal_region_is_rejected(geometry):
    with pytest.raises(ValueError, match="полигоном"):
        _generate(geometry)


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum_spacing0": 0},
        {"minimum_spacing0": -1},
        {"minimum_spacing0": float("nan")},
        {"maximum_spacing40": float("inf")},
        {"maximum_spacing40": 1},
        {"minimum_spacing0": True},
        {"target_count": 0},
        {"target_count": 5001},
        {"target_count": True},
        {"target_count": 2.5},
        {"max_candidates": 0},
        {"max_candidates": 5001},
        {"max_candidates": True},
        {"seed": True},
        {"seed": "47"},
    ],
)
def test_invalid_parameters_fail_before_producing_any_positions(changes):
    with pytest.raises(ValueError):
        _generate(**changes)
