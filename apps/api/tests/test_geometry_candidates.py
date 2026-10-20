"""Контракты адаптивных проб: покрытие, расстояния и честные пределы поиска."""

from math import hypot
from typing import TypedDict

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from app.planning.ports import PatternCandidate
from app.planning.search.geometry_candidates import (
    GeometryCandidateSearch,
    GeometryStopReason,
)


def test_seeded_search_and_acceptance_are_reproducible_without_mutating_region() -> (
    None
):
    region = Polygon([(0, 0), (20, 0), (20, 10), (11, 10), (11, 3), (0, 3)])
    original = region.wkb

    def accepted_sequence() -> tuple[
        list[tuple[float, float]], GeometryStopReason | None
    ]:
        search = GeometryCandidateSearch(region, 2, 8, 19)
        accepted: list[tuple[float, float]] = []
        while search.stop_reason is None:
            batch = search.next_batch(1)
            if batch:
                point = batch[0]
                accepted.append((point.x, point.y))
                search.accept(point.x, point.y)
        return accepted, search.stop_reason

    points, reason = accepted_sequence()
    assert (points, reason) == accepted_sequence()
    assert reason == "target_reached" and len(points) == 8
    assert all(region.covers(Point(p)) for p in points)
    assert all(
        hypot(a[0] - b[0], a[1] - b[1]) >= 2
        for i, a in enumerate(points)
        for b in points[i + 1 :]
    )
    assert region.wkb == original


def test_rejected_point_does_not_reserve_its_neighbourhood() -> None:
    search = GeometryCandidateSearch(box(0, 0, 4, 1), 10, 1, 7)
    assert search.metrics["refinement_enabled"] == 1
    first = search.next_batch(1)[0]
    following = search.next_batch(1)[0]
    assert first != following
    assert hypot(first.x - following.x, first.y - following.y) < 10
    assert search.accepted_count == 0


def test_seed_changes_equal_bound_order_without_changing_the_region() -> None:
    region = box(0, 0, 10, 10)
    first = GeometryCandidateSearch(region, 2, 20, 1).next_batch(12)
    second = GeometryCandidateSearch(region, 2, 20, 2).next_batch(12)
    assert first != second
    assert all(region.covers(Point(p.x, p.y)) for p in first + second)


def test_accepted_points_revalidate_the_heap_and_leave_no_forbidden_proposals() -> None:
    search = GeometryCandidateSearch(box(0, 0, 12, 8), 3, 8, 3)
    accepted: list[tuple[float, float]] = []
    for _ in range(8):
        batch = search.next_batch(1)
        assert batch
        point = batch[0]
        assert all(hypot(point.x - x, point.y - y) >= 3 for x, y in accepted)
        search.accept(point.x, point.y)
        accepted.append((point.x, point.y))
    assert search.stop_reason == "target_reached"
    assert search.metrics["cells_revalidated"] > 0


def test_each_disconnected_component_gets_a_probe_even_when_very_small() -> None:
    large = box(0, 0, 100, 100)
    tiny = box(200, 1, 200.000001, 1.000001)
    search = GeometryCandidateSearch(MultiPolygon([tiny, large]), 2, 20, 47)
    points = search.next_batch(2)
    assert len(points) == 2
    assert any(tiny.covers(Point(p.x, p.y)) for p in points)
    assert any(large.covers(Point(p.x, p.y)) for p in points)


def test_holes_and_thin_bent_corridors_are_not_replaced_by_their_bounds() -> None:
    ring = Polygon(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        holes=[[(1, 1), (19, 1), (19, 19), (1, 19)]],
    )
    corridor = LineString([(30, 0), (30, 20), (40, 20), (40, 3)]).buffer(0.0001)
    region = MultiPolygon([ring, corridor])
    search = GeometryCandidateSearch(
        region, 0.2, 100, 1, max_cells=3_000, resolution_m=0.05
    )
    points = search.next_batch(60)
    assert len(points) >= 10
    assert any(corridor.covers(Point(p.x, p.y)) for p in points)
    assert all(region.covers(Point(p.x, p.y)) for p in points)
    assert not any(
        box(1.001, 1.001, 18.999, 18.999).covers(Point(p.x, p.y)) for p in points
    )


def test_initial_points_precede_geometry_anchors_and_are_checked_for_duplicates() -> (
    None
):
    initial = [
        PatternCandidate(-1, 0),
        PatternCandidate(0, 0),
        PatternCandidate(0, 0),
        PatternCandidate(2, 0),
    ]
    search = GeometryCandidateSearch(box(0, 0, 3, 1), 2, 2, 5, initial_points=initial)
    first = search.next_batch(1)[0]
    assert first == PatternCandidate(0, 0)
    search.accept(first.x, first.y)
    second = search.next_batch(1)[0]
    assert second == PatternCandidate(2, 0)
    search.accept(second.x, second.y)
    assert search.stop_reason == "target_reached"


def test_alternative_start_can_escape_the_single_greedy_corridor_trap() -> None:
    region = box(0, 0, 7, 0.2)
    centre = GeometryCandidateSearch(region, 6, 2, 1, resolution_m=0.1, max_cells=2_000)
    point = centre.next_batch(1)[0]
    assert point.x == 3.5
    centre.accept(point.x, point.y)
    assert centre.next_batch(1) == []
    assert centre.stop_reason == "resolution_exhausted"
    assert centre.accepted_count == 1
    # Предыдущий жадный результат не объявляется максимумом: другое начало даёт два.
    alternate = GeometryCandidateSearch(
        region,
        6,
        2,
        1,
        initial_points=[PatternCandidate(0, 0.1), PatternCandidate(7, 0.1)],
    )
    for _ in range(2):
        candidate = alternate.next_batch(1)[0]
        alternate.accept(candidate.x, candidate.y)
    assert alternate.accepted_count == 2
    assert alternate.stop_reason == "target_reached"


def test_closed_boundary_and_exact_spacing_equality_are_preserved() -> None:
    search = GeometryCandidateSearch(
        box(0, 0, 4, 1),
        4,
        2,
        0,
        initial_points=[PatternCandidate(0, 0), PatternCandidate(4, 0)],
    )
    for expected in [PatternCandidate(0, 0), PatternCandidate(4, 0)]:
        assert search.next_batch(1) == [expected]
        search.accept(expected.x, expected.y)
    assert search.stop_reason == "target_reached"


def test_sub_resolution_component_is_not_rounded_away() -> None:
    region = box(1000, 1000, 1000.0000001, 1000.0000002)
    search = GeometryCandidateSearch(region, 1, 1, 0, resolution_m=0.1)
    point = search.next_batch(1)[0]
    assert region.covers(Point(point.x, point.y))
    search.accept(point.x, point.y)
    assert search.stop_reason == "target_reached"


def test_cell_budget_is_not_reported_as_exhausted_capacity() -> None:
    search = GeometryCandidateSearch(box(0, 0, 100, 100), 1, 20, 0, max_cells=3)
    search.next_batch(100)
    assert search.stop_reason == "cell_limit"
    assert search.metrics["cells_evaluated"] <= 3


def test_frontier_memory_budget_is_explicit() -> None:
    search = GeometryCandidateSearch(box(0, 0, 100, 100), 1, 20, 0, max_frontier=2)
    search.next_batch(100)
    assert search.stop_reason == "frontier_limit"
    assert search.metrics["frontier_peak"] <= 2


def test_time_budget_has_its_own_reason() -> None:
    search = GeometryCandidateSearch(box(0, 0, 10, 10), 1, 20, 0, time_limit_s=0)
    assert search.next_batch(1) == []
    assert search.stop_reason == "time_limit"


def test_empty_region_and_resolution_are_not_claims_about_original_site_capacity() -> (
    None
):
    search = GeometryCandidateSearch(Polygon(), 2, 3, 0)
    assert search.next_batch(10) == []
    assert search.stop_reason == "resolution_exhausted"
    assert search.accepted_count == 0


def test_cancellation_reaches_the_caller() -> None:
    cancelled = False

    def check() -> None:
        if cancelled:
            raise RuntimeError("Отмена")

    search = GeometryCandidateSearch(box(0, 0, 10, 10), 1, 10, 0, check_cancelled=check)
    cancelled = True
    with pytest.raises(RuntimeError, match="Отмена"):
        search.next_batch(10)


class _InvalidOptions(TypedDict, total=False):
    spacing_m: float
    resolution_m: float
    target_count: int
    max_cells: int
    time_limit_s: float


@pytest.mark.parametrize(
    "kwargs",
    [
        {"spacing_m": 0},
        {"spacing_m": float("nan")},
        {"resolution_m": 0},
        {"target_count": 0},
        {"max_cells": 0},
        {"time_limit_s": -1},
    ],
)
def test_invalid_search_parameters_are_rejected(kwargs: _InvalidOptions) -> None:
    with pytest.raises(ValueError):
        GeometryCandidateSearch(
            box(0, 0, 1, 1),
            spacing_m=kwargs.get("spacing_m", 1),
            target_count=kwargs.get("target_count", 2),
            seed=0,
            resolution_m=kwargs.get("resolution_m", 0.05),
            max_cells=kwargs.get("max_cells", 20_000),
            time_limit_s=kwargs.get("time_limit_s", 5),
        )


def test_invalid_polygon_is_not_silently_repaired() -> None:
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0)])
    with pytest.raises(ValueError, match="корректным"):
        GeometryCandidateSearch(invalid, 1, 2, 0)


def test_saturated_component_discards_its_layout_and_heap_but_keeps_other_region() -> (
    None
):
    region = MultiPolygon([box(0, 0, 8, 2), box(20, 0, 28, 2)])
    original = region.wkb
    search = GeometryCandidateSearch(
        region,
        2,
        3,
        0,
        initial_points=[
            PatternCandidate(1, 1),
            PatternCandidate(4, 1),
            PatternCandidate(21, 1),
            PatternCandidate(24, 1),
        ],
    )
    first = search.next_batch(1)[0]
    search.accept(first.x, first.y)
    evaluated = search.metrics["cells_evaluated"]
    assert search.discard_regions(lambda part: part.bounds[0] < 10) == 1
    assert search.accepted_count == 1 and search.target_count == 3
    assert search.metrics["active_components"] == 1
    assert search.metrics["discarded_components"] == 1
    assert search.metrics["discarded_cells"] == 1
    assert search.metrics["discarded_pending_anchors"] == 2
    assert search.metrics["cells_evaluated"] == evaluated
    for expected in [PatternCandidate(21, 1), PatternCandidate(24, 1)]:
        assert search.next_batch(1) == [expected]
        search.accept(expected.x, expected.y)
    assert search.stop_reason == "target_reached"
    assert region.wkb == original


def test_future_adaptive_probes_do_not_return_to_disabled_components() -> None:
    other = box(20, 0, 28, 2)
    search = GeometryCandidateSearch(MultiPolygon([box(0, 0, 8, 2), other]), 1, 20, 0)
    first = search.next_batch(1)[0]
    search.accept(first.x, first.y)
    assert search.discard_regions(lambda part: part.bounds[0] < 10) == 1
    later = search.next_batch(30)
    assert len(later) == 30
    assert all(other.covers(Point(point.x, point.y)) for point in later)
    # Повторное доказательство не сбрасывает ни принятые места, ни счётчики.
    assert search.discard_regions(lambda part: part.bounds[0] < 10) == 0
    assert search.metrics["discarded_components"] == 1
    with pytest.raises(ValueError, match="область"):
        search.accept(0, 0)


def test_pending_boundary_point_survives_when_an_active_component_covers_it() -> None:
    search = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 2, 2), box(2, 2, 4, 4)]),
        1,
        3,
        0,
        initial_points=[
            PatternCandidate(0, 0),
            PatternCandidate(2, 2),
            PatternCandidate(3, 3),
        ],
    )
    first = search.next_batch(1)[0]
    search.accept(first.x, first.y)
    assert search.discard_regions(lambda part: part.bounds[0] == 0) == 1
    assert search.next_batch(1) == [PatternCandidate(2, 2)]
    search.accept(2, 2)
    assert search.next_batch(1) == [PatternCandidate(3, 3)]
    search.accept(3, 3)
    assert search.stop_reason == "target_reached"


def test_disabled_component_keeps_accepted_spacing_for_remaining_component() -> None:
    search = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 1, 1), box(1.1, 0, 10, 1)]),
        3,
        2,
        0,
        initial_points=[
            PatternCandidate(1, 0.5),
            PatternCandidate(1.1, 0.5),
            PatternCandidate(4, 0.5),
        ],
    )
    first = search.next_batch(1)[0]
    search.accept(first.x, first.y)
    assert search.discard_regions(lambda part: part.bounds[0] == 0) == 1
    with pytest.raises(ValueError, match="расстояние"):
        search.accept(1.1, 0.5)
    assert search.next_batch(1) == [PatternCandidate(4, 0.5)]
    search.accept(4, 0.5)
    assert search.stop_reason == "target_reached"


def test_discarding_all_regions_is_resolution_exhaustion_not_target_reached() -> None:
    search = GeometryCandidateSearch(box(0, 0, 8, 2), 1, 3, 0)
    first = search.next_batch(1)[0]
    search.accept(first.x, first.y)
    assert search.discard_regions(lambda _part: True) == 1
    assert search.next_batch(10) == []
    assert search.stop_reason == "resolution_exhausted"
    assert search.accepted_count == 1 and search.target_count == 3
    assert search.metrics["active_components"] == 0
    assert search.discard_regions(lambda _part: True) == 0


def test_discard_preserves_existing_target_and_resource_limit_reasons() -> None:
    complete = GeometryCandidateSearch(box(0, 0, 8, 2), 1, 1, 0)
    first = complete.next_batch(1)[0]
    complete.accept(first.x, first.y)
    assert complete.discard_regions(lambda _part: True) == 1
    assert complete.stop_reason == "target_reached"
    limited = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 8, 2), box(20, 0, 28, 2)]), 1, 3, 0, max_cells=1
    )
    assert limited.stop_reason == "cell_limit"
    assert limited.discard_regions(lambda _part: True) == 2
    assert limited.stop_reason == "cell_limit"
    assert limited.metrics["cells_evaluated"] == 1
    frontier_limited = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 8, 2), box(20, 0, 28, 2)]),
        1,
        3,
        0,
        max_frontier=2,
    )
    frontier_limited.next_batch(3)
    assert frontier_limited.stop_reason == "frontier_limit"
    assert frontier_limited.discard_regions(lambda _part: True) == 2
    assert frontier_limited.stop_reason == "frontier_limit"
    assert frontier_limited.metrics["frontier_peak"] == 2


def test_discard_respects_time_limit_without_resetting_frontier() -> None:
    search = GeometryCandidateSearch(box(0, 0, 8, 2), 1, 3, 0, time_limit_s=0)
    called: list[Polygon] = []

    def predicate(part: Polygon) -> bool:
        called.append(part)
        return True

    assert search.discard_regions(predicate) == 0
    assert search.stop_reason == "time_limit"
    assert called == []
    assert search.metrics["active_components"] == 1
    assert search.metrics["frontier_cells"] == 1


def test_cancellation_during_discard_does_not_partially_disable_regions() -> None:
    cancelled = False

    def check() -> None:
        if cancelled:
            raise RuntimeError("Отмена")

    search = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 8, 2), box(20, 0, 28, 2)]),
        1,
        3,
        0,
        check_cancelled=check,
    )

    def predicate(_part: Polygon) -> bool:
        nonlocal cancelled
        cancelled = True
        return True

    with pytest.raises(RuntimeError, match="Отмена"):
        search.discard_regions(predicate)
    assert search.metrics["active_components"] == 2
    assert search.metrics["discarded_components"] == 0
    assert search.metrics["frontier_cells"] == 2
    assert search.metrics["pending_anchors"] == 2
    cancelled = False
    assert search.discard_regions(lambda part: part.bounds[0] < 10) == 1


def test_initial_only_variant_exhausts_explicit_points_without_adaptive_work() -> None:
    region = box(0, 0, 20, 20)
    search = GeometryCandidateSearch(
        region,
        2,
        5,
        0,
        initial_points=[
            PatternCandidate(-1, 0),
            PatternCandidate(1, 1),
            PatternCandidate(1, 1),
            PatternCandidate(4, 1),
        ],
        refine=False,
        max_cells=1,
    )
    assert search.metrics["pending_anchors"] == 4
    assert search.metrics["refinement_enabled"] == 0
    assert search.next_batch(10) == [PatternCandidate(1, 1), PatternCandidate(4, 1)]
    assert search.next_batch(1) == []
    assert search.stop_reason == "resolution_exhausted"
    assert search.accepted_count == 0
    assert search.metrics["cells_evaluated"] == 0
    assert search.metrics["frontier_peak"] == 0
    assert search.metrics["active_components"] == 1


def test_initial_only_rejection_keeps_neighbours_and_retirement_keeps_active_points() -> (
    None
):
    search = GeometryCandidateSearch(
        MultiPolygon([box(0, 0, 4, 2), box(10, 0, 14, 2)]),
        2,
        3,
        0,
        initial_points=[
            PatternCandidate(1, 1),
            PatternCandidate(1.1, 1),
            PatternCandidate(11, 1),
            PatternCandidate(11.1, 1),
            PatternCandidate(14, 1),
        ],
        refine=False,
    )
    assert search.next_batch(1) == [PatternCandidate(1, 1)]
    # Отклонённый первый центр ничего не резервирует; сосед всё ещё предлагается.
    assert search.next_batch(1) == [PatternCandidate(1.1, 1)]
    search.accept(1.1, 1)
    assert search.discard_regions(lambda part: part.bounds[0] < 10) == 1
    assert search.next_batch(1) == [PatternCandidate(11, 1)]
    assert search.next_batch(1) == [PatternCandidate(11.1, 1)]
    search.accept(11.1, 1)
    assert search.next_batch(1) == [PatternCandidate(14, 1)]
    search.accept(14, 1)
    assert search.stop_reason == "target_reached"
    assert search.accepted_count == 3
    assert search.metrics["cells_evaluated"] == 0
    assert search.metrics["discarded_components"] == 1
