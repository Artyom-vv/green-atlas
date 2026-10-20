from __future__ import annotations

import math
import tracemalloc
from collections.abc import Sequence

import pytest

from app.rendering.lawn_scatter import (
    MAX_LAWN_INSTANCES,
    LawnSample,
    scatter_lawn,
)


def rectangle(
    name: str, x0: float, x1: float, y0: float, y1: float, *, other_diagonal=False
):
    return {
        "id": name,
        "kind": "lawn",
        "vertices": [[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]],
        "faces": [[0, 1, 3], [1, 2, 3]] if other_diagonal else [[0, 1, 2], [0, 2, 3]],
    }


def sample(rows, **options):
    return scatter_lawn(
        rows,
        (0.0, -5.0, 3.0),
        (0.0, 40.0, 0.0),
        math.radians(42),
        1.3,
        **options,
    )


def positions(samples: Sequence[LawnSample]):
    return [item.position for item in samples]


def test_every_visible_patch_gets_detail_including_beyond_old_radius():
    rows = [
        rectangle("foreground", -3, 3, 1, 8),
        rectangle("background", -16, 16, 95, 110),
        rectangle("behind", -2, 2, -20, -10),
        rectangle("outside", 200, 210, 10, 20),
        rectangle("past-far-clip", -16, 16, 150, 170),
    ]
    result = sample(rows, seed=42)
    assert {point.surface_id for point in result.samples} == {
        "foreground",
        "background",
    }
    assert max(point.position[1] for point in result.samples) > 100
    assert result.visible_tile_count <= len(result.samples) <= MAX_LAWN_INSTANCES


def test_geometry_order_and_diagonal_do_not_decide_who_gets_budget():
    rows = [rectangle("a", -90, 0, 1, 110), rectangle("b", 0, 90, 1, 110)]
    reordered = [
        rectangle("b", 0, 90, 1, 110, other_diagonal=True),
        rectangle("a", -90, 0, 1, 110, other_diagonal=True),
    ]
    result = sample(rows, max_instances=300, seed=19)
    other = sample(reordered, max_instances=300, seed=19)
    assert result == other
    assert len(result.samples) == 300
    assert {point.surface_id for point in result.samples} == {"a", "b"}


def test_identical_triangles_are_not_counted_twice():
    row = rectangle("a", -10, 10, 1, 70)
    single = sample([row], max_instances=400)
    assert sample([row, row], max_instances=400) == single
    row["faces"] = list(reversed(row["faces"]))
    assert sample([row], max_instances=400) == single


def test_holes_remain_empty_and_points_stay_on_admitted_geometry():
    rows = [
        rectangle("lower", -10, 10, 1, 10),
        rectangle("upper", -10, 10, 20, 50),
        rectangle("left", -10, -3, 10, 20),
        rectangle("right", 3, 10, 10, 20),
    ]
    result = sample(rows, max_instances=800)
    assert len(result.samples) == 800
    for point in result.samples:
        x, y, z = point.position
        assert not (-3 + 1e-7 < x < 3 - 1e-7 and 10 + 1e-7 < y < 20 - 1e-7)
        assert -10 - 1e-7 <= x <= 10 + 1e-7
        assert 1 - 1e-7 <= y <= 50 + 1e-7
        assert z == pytest.approx(0, abs=1e-7)


def test_very_thin_visible_strip_does_not_disappear_between_grid_anchors():
    row = rectangle("thin", 0.37, 0.39, 4, 100)
    result = sample([row], max_instances=500)
    assert result.samples
    assert min(point.position[1] for point in result.samples) < 8
    assert max(point.position[1] for point in result.samples) > 90
    assert all(
        0.37 - 1e-7 <= point.position[0] <= 0.39 + 1e-7 for point in result.samples
    )


def test_height_is_interpolated_from_each_triangle_and_not_assumed_flat():
    row = rectangle("slope", -20, 20, 1, 70)
    for point in row["vertices"]:
        point[2] = 0.02 * point[0] + 0.03 * point[1]
    result = sample([row], max_instances=600)
    assert result.samples
    for point in result.samples:
        x, y, z = point.position
        assert z == pytest.approx(0.02 * x + 0.03 * y, abs=1e-7)


def test_foreground_is_denser_per_square_metre_with_bounded_distant_scale():
    rows = [rectangle("near", -3, 3, 1, 8), rectangle("far", -16, 16, 65, 110)]
    result = sample(rows, max_instances=2000)
    near = sum(point.surface_id == "near" for point in result.samples)
    far = sum(point.surface_id == "far" for point in result.samples)
    assert near > 0 and far > 0
    assert near / 42 > far / 1440
    assert all(1 <= point.coverage_scale <= 8 for point in result.samples)
    near_scales = [
        point.coverage_scale for point in result.samples if point.surface_id == "near"
    ]
    far_scales = [
        point.coverage_scale for point in result.samples if point.surface_id == "far"
    ]
    assert sum(near_scales) / len(near_scales) < sum(far_scales) / len(far_scales)


def test_foreground_dedup_prevents_many_samples_on_one_tiny_patch():
    row = rectangle("near", -0.5, 0.5, 1, 2)
    result = sample([row])
    assert 0 < len(result.samples) <= 9
    world_cells = {
        (math.floor(point.position[0] / 0.56), math.floor(point.position[1] / 0.56))
        for point in result.samples
    }
    assert len(world_cells) == len(result.samples)


def test_input_indexed_mesh_and_direct_triangle_rows_are_equivalent():
    indexed = rectangle("same", -20, 20, 1, 80)
    direct = {
        "id": "same",
        "semantic": "lawn",
        "triangles_scene_xyz_m": [
            [indexed["vertices"][i] for i in face] for face in indexed["faces"]
        ],
    }
    assert sample([indexed], max_instances=500) == sample([direct], max_instances=500)


def test_camera_roll_is_respected_and_degenerate_triangles_are_ignored():
    row = rectangle("flat", -10, 10, -10, 10)
    row["faces"].append([0, 0, 0])
    result = scatter_lawn(
        [row],
        (0, 0, 20),
        (0, 0, 0),
        math.radians(45),
        1.5,
        camera_up=(0, 1, 0),
        max_instances=500,
    )
    assert result.samples
    assert all(
        abs(point.position[0]) <= 10 + 1e-7
        and abs(point.position[1]) <= 10 + 1e-7
        and abs(point.position[2]) < 1e-7
        for point in result.samples
    )


def test_memory_and_instance_count_do_not_grow_with_lawn_extent():
    row = rectangle("huge", -100000, 100000, 1, 100000)
    tracemalloc.start()
    try:
        result = sample([row], far_m=100000, max_instances=500)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(result.samples) <= 500
    assert result.auxiliary_bytes < 8 * 500 * 180
    assert peak < 4_000_000


def test_output_is_deterministic_and_requested_cap_cannot_exceed_hard_budget():
    row = rectangle("large", -90, 90, 1, 110)
    result = sample([row], max_instances=MAX_LAWN_INSTANCES + 1, seed=183)
    assert len(result.samples) <= MAX_LAWN_INSTANCES
    assert result == sample([row], max_instances=MAX_LAWN_INSTANCES + 1, seed=183)
    assert sample([row], max_instances=0).samples == ()


@pytest.mark.parametrize("aspect", [0.000001, 1000000.0])
def test_extreme_camera_aspect_does_not_expand_the_candidate_buffer(aspect):
    result = scatter_lawn(
        [rectangle("lawn", -100, 100, 1, 110)],
        (0, -5, 3),
        (0, 40, 0),
        math.radians(42),
        aspect,
        max_instances=100,
    )
    assert result.raster_candidate_count <= 800
    assert result.auxiliary_bytes <= 800 * 180
    assert len(result.samples) <= 100


@pytest.mark.parametrize(
    "options", [{"aspect": 0}, {"near_m": 120}, {"spacing_m": 0}, {"far_m": math.inf}]
)
def test_invalid_camera_settings_fail_explicitly(options):
    settings = {"aspect": 1.3, "near_m": 0.06, "far_m": 120, "spacing_m": 0.56}
    settings.update(options)
    with pytest.raises(ValueError):
        scatter_lawn([], (0, -5, 3), (0, 40, 0), math.radians(42), **settings)
