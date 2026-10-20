import hashlib
import json
from dataclasses import asdict
from math import hypot
from random import Random

import pytest
from shapely.affinity import rotate
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from app.planning.patterns.generators import _poisson_candidates
from app.planning.search import sampling
from app.planning.search.sampling import poisson_candidates, sparse_area_sampler


@pytest.mark.parametrize("geometry", [Point(), Polygon()])
@pytest.mark.parametrize("alternatives", [False, True])
def test_empty_area_returns_without_starting_random_sampling(
    monkeypatch: pytest.MonkeyPatch,
    geometry: BaseGeometry,
    alternatives: bool,
) -> None:
    def unexpected_random(seed: str) -> Random:
        raise AssertionError(f"Empty area started random sampling: {seed}")

    original = geometry.wkb
    monkeypatch.setattr(sampling, "Random", unexpected_random)
    assert poisson_candidates(geometry, 8, 16, 47, alternatives=alternatives) == []
    assert geometry.wkb == original


@pytest.mark.parametrize("sparse", [False, True])
def test_sdk_preparation_preserves_entire_seeded_result(
    monkeypatch: pytest.MonkeyPatch, sparse: bool
) -> None:
    from shapely import from_wkb

    polygon = Polygon(
        Point(0, 0).buffer(100, quad_segs=256).exterior.coords,
        [Point(0, 0).buffer(30, quad_segs=64).exterior.coords],
    )
    geometry = (
        MultiPolygon([polygon, box(1000000, 0, 1000100, 100)]) if sparse else polygon
    )
    original = geometry.wkb
    with monkeypatch.context() as patch:
        patch.setattr(sampling, "prepare", lambda _: None)
        expected = poisson_candidates(from_wkb(original), 8, 96, 47)
    assert poisson_candidates(geometry, 8, 96, 47) == expected
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
    # The refactor must preserve the entire ordered result, including points
    # after the prefix where spacing rejections affect later random draws.
    encoded = json.dumps(
        [asdict(candidate) for candidate in candidates],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == (
        "b964892a178b01a62a4e15d862f6316cc7f77b13e583e6a70722492bf06f1a23"
    )


def test_alternative_probes_cover_pockets_without_reserving_space() -> None:
    large = box(1000, 1000, 1100, 1100)
    small = box(0, 0, 10, 10)
    geometry = MultiPolygon([small, large])
    probes = poisson_candidates(geometry, 1000, 16, 47, alternatives=True)
    assert len(probes) == 16
    assert [(point.x, point.y) for point in probes[:2]] == [
        (large.representative_point().x, large.representative_point().y),
        (small.representative_point().x, small.representative_point().y),
    ]
    assert all(geometry.covers(Point(point.x, point.y)) for point in probes)
    assert any(
        hypot(first.x - second.x, first.y - second.y) < 1000
        for index, first in enumerate(probes)
        for second in probes[index + 1 :]
    )
    assert _poisson_candidates is poisson_candidates


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
