import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from random import Random
from typing import Literal

import pytest
from shapely.geometry import Point, box, mapping
from shapely.geometry.base import BaseGeometry

from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.patterns import building_patterns
from app.planning.patterns import generators as patterns
from app.planning.patterns.building_patterns import (
    generate_building_contour,
    generate_building_screen,
)
from app.planning.patterns.contracts import PlacementMaskRequest
from app.planning.ports import PatternCandidate
from app.planning.search import sampling
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason


def feature(kind: str, geometry: BaseGeometry) -> dict:
    return {
        "type": "Feature",
        "properties": {"kind": kind},
        "geometry": mapping(geometry),
    }


def coordinate_digest(points: list[PatternCandidate]) -> str:
    encoded = json.dumps(
        [asdict(point) for point in points],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize(
    ("side", "count", "digest"),
    [
        (
            "perimeter",
            42,
            "7a3448e4a53c5fe633354b3468e48b5dfc5a88b3b9558de4d8ba67cbd41df462",
        ),
        (
            "roads",
            12,
            "9c6194d55ecd6793009179b65f58d3a5c8525e243c1ed896fe776a8dd4ee5fe5",
        ),
    ],
)
def test_building_groups_preserve_complete_seeded_coordinates_and_identity(
    side: Literal["perimeter", "roads"], count: int, digest: str
) -> None:
    building = box(80, 80, 120, 120)
    points = generate_building_screen(
        PlacementMaskRequest(
            mask_id="building_screen",
            base_plan_version=1,
            zone_ids=["z"],
            seed=47,
            screen_side=side,
            spacing_m=7,
            cluster_size=3,
        ),
        box(0, 0, 200, 200),
        [feature("building", building), feature("road", box(155, 0, 160, 200))],
    )
    assert len(points) == count
    assert coordinate_digest(points) == digest


def test_facade_contour_preserves_complete_order_and_distance() -> None:
    building = box(80, 80, 120, 120)
    points = generate_building_contour(
        PlacementMaskRequest(
            mask_id="building_contour",
            base_plan_version=1,
            zone_ids=["z"],
            seed=47,
            spacing_m=7,
            building_offset_m=10,
        ),
        box(0, 0, 200, 200).difference(building.buffer(8)),
        [feature("building", building)],
    )
    assert len(points) == 31
    assert coordinate_digest(points) == (
        "59325e417bd685966190c220cb1ccd5c851ba0da84c35deccf3e03c527a213b6"
    )
    assert all(
        abs(Point(point.x, point.y).distance(building) - 10) < 0.01 for point in points
    )


@pytest.mark.parametrize("mask_id", ["building_contour", "building_screen"])
def test_facade_proposals_respect_existing_plant_clearance(
    mask_id: Literal["building_contour", "building_screen"],
) -> None:
    building = box(80, 80, 120, 120)
    clearance = Point(135, 100).buffer(15)
    request = PlacementMaskRequest(
        mask_id=mask_id,
        base_plan_version=1,
        zone_ids=["z"],
        seed=47,
        spacing_m=7,
        cluster_size=3,
        building_offset_m=10,
    )
    generate = (
        generate_building_contour
        if mask_id == "building_contour"
        else generate_building_screen
    )
    usable = box(0, 0, 200, 200).difference(building.buffer(8))
    guides = [feature("building", building)]
    initial = generate(request, usable, guides)
    assert any(clearance.contains(Point(point.x, point.y)) for point in initial)
    points = generate(
        request,
        usable,
        [*guides, feature("planned_plant_clearance", clearance)],
    )
    assert points
    assert all(not clearance.contains(Point(point.x, point.y)) for point in points)


def test_generated_coordinate_limit_preserves_boundary_and_error() -> None:
    points = [
        PatternCandidate(float(index), 0) for index in range(MAX_PATTERN_CANDIDATES)
    ]
    assert sampling.bounded_candidates(points) is points
    with pytest.raises(DomainFailure) as error:
        sampling.bounded_candidates([*points, PatternCandidate(0, 1)])
    assert error.value.reason is FailureReason.PATTERN_CANDIDATE_LIMIT
    assert error.value.params == {"max_candidates": MAX_PATTERN_CANDIDATES}
    assert public_failure_message(error.value) == (
        "Рисунок создаёт больше 5000 позиций. Увеличьте шаг или уменьшите участки"
    )


def test_fully_occupied_building_band_does_not_start_random_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_random(seed: str) -> Random:
        raise AssertionError(f"Occupied building band started random sampling: {seed}")

    usable = box(0, 0, 200, 200)
    guides = [
        feature("building", box(80, 80, 120, 120)),
        feature("planned_plant_clearance", usable),
    ]
    request = PlacementMaskRequest(
        mask_id="building_screen",
        base_plan_version=1,
        zone_ids=["z"],
        seed=47,
        cluster_size=3,
    )
    original = usable.wkb
    original_guides = deepcopy(guides)
    original_request = request.model_dump()
    monkeypatch.setattr(sampling, "Random", unexpected_random)
    assert generate_building_screen(request, usable, guides) == []
    assert usable.wkb == original
    assert guides == original_guides
    assert request.model_dump() == original_request


def test_existing_generator_imports_retain_their_owners() -> None:
    assert (
        patterns.generate_building_contour
        is building_patterns.generate_building_contour
    )
    assert (
        patterns.generate_building_screen is building_patterns.generate_building_screen
    )
    assert patterns._bounded is sampling.bounded_candidates
    assert patterns._cluster_offsets is sampling.cluster_offsets
