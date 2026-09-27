from math import hypot
from random import Random

import pytest
from shapely.affinity import rotate
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from app.planning.patterns import _poisson_candidates
from app.planning.sampling import sparse_area_sampler


@pytest.mark.parametrize("sparse", [False, True])
def test_sdk_preparation_preserves_entire_seeded_result(monkeypatch, sparse):
    from shapely import from_wkb

    from app.planning import patterns

    polygon = Polygon(
        Point(0, 0).buffer(100, quad_segs=256).exterior.coords,
        [Point(0, 0).buffer(30, quad_segs=64).exterior.coords],
    )
    geometry = (
        MultiPolygon([polygon, box(1000000, 0, 1000100, 100)]) if sparse else polygon
    )
    original = geometry.wkb
    with monkeypatch.context() as patch:
        patch.setattr(patterns, "prepare", lambda _: None)
        expected = patterns._poisson_candidates(from_wkb(original), 8, 96, 47)
    assert patterns._poisson_candidates(geometry, 8, 96, 47) == expected
    assert geometry.wkb == original


@pytest.mark.parametrize(
    "geometry",
    [
        rotate(box(0, 0, 20000, 20), 45),
        MultiPolygon([box(0, 0, 100, 100), box(1000000, 1000000, 1000100, 1000100)]),
    ],
)
def test_sparse_geometry_produces_requested_reproducible_spaced_candidates(
    geometry: BaseGeometry,
) -> None:
    candidates = _poisson_candidates(geometry, spacing=8, target=96, seed=47)
    assert len(candidates) == 96
    assert candidates == _poisson_candidates(geometry, spacing=8, target=96, seed=47)
    assert all(geometry.covers(Point(item.x, item.y)) for item in candidates)
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= 8 - 1e-6
        for index, first in enumerate(candidates)
        for second in candidates[index + 1 :]
    )


def test_compact_geometry_keeps_previous_seed_coordinates() -> None:
    geometry = box(0, 0, 100, 100)
    assert sparse_area_sampler(geometry, target=96, attempt_budget=11520) is None
    candidates = _poisson_candidates(geometry, spacing=8, target=96, seed=47)
    assert len(candidates) == 96
    assert [(item.x, item.y) for item in candidates[:5]] == [
        (9.473624, 89.146526),
        (4.850302, 5.099577),
        (73.154031, 21.459794),
        (39.081862, 20.384534),
        (93.73296, 85.303757),
    ]


def test_sampler_switch_depends_on_expected_hits_in_attempt_budget() -> None:
    geometry = MultiPolygon([box(0, 0, 100, 100), box(2000, 2000, 2100, 2100)])
    # The same geometry has enough expected hits with a larger attempt budget.
    assert sparse_area_sampler(geometry, target=96, attempt_budget=11520) is not None
    assert sparse_area_sampler(geometry, target=96, attempt_budget=100000) is None


def test_sparse_sampling_keeps_holes_out_and_weights_usable_area() -> None:
    first = Polygon(
        box(0, 0, 100, 100).exterior.coords, [box(10, 10, 90, 90).exterior.coords]
    )
    second = box(1000000, 0, 1000100, 100)
    sampler = sparse_area_sampler(MultiPolygon([first, second]), 96, 11520)
    assert sampler is not None
    # Shapely multipart iteration creates geometry wrappers, so compare by area.
    random = Random(47)
    accepted = [0, 0]
    for _ in range(8000):
        component, x, y = sampler.draw(random)
        if component.covers(Point(x, y)):
            accepted[0 if component.area == first.area else 1] += 1
    expected_fraction = first.area / (first.area + second.area)
    assert accepted[0] / sum(accepted) == pytest.approx(expected_fraction, abs=0.025)
    candidates = _poisson_candidates(MultiPolygon([first, second]), 8, 96, 47)
    assert len(candidates) == 96
    assert all(
        not box(10, 10, 90, 90).contains(Point(item.x, item.y)) for item in candidates
    )
