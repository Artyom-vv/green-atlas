import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rendering import lawn_scatter


@pytest.fixture
def lawn(monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "mathutils", SimpleNamespace(Vector=tuple, Quaternion=tuple)
    )
    monkeypatch.setitem(sys.modules, "render_style", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "lawn_scatter", lawn_scatter)
    path = Path(__file__).parents[1] / "app/rendering/lawn_instances.py"
    spec = importlib.util.spec_from_file_location("lawn_instances_boundary_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rectangle(x0, y0, x1, y1):
    return {
        "id": "lawn",
        "kind": "lawn",
        "vertices": [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)],
        "faces": [(0, 1, 2), (0, 2, 3)],
    }


def sample(x, y):
    return lawn_scatter.LawnSample((x, y, 0), "lawn", 1, 1)


def test_shared_diagonal_does_not_shrink_the_centre_patch(lawn):
    row = rectangle(0, 0, 2, 2)
    segments = list(lawn._boundary_segments([row]))
    assert len(segments) == 4
    index = lawn._boundary_index([row], [sample(1, 1)], 2)
    assert lawn._clearance((1, 1, 0), index, 2) == pytest.approx(1)


def test_duplicate_faces_keep_the_outer_border(lawn):
    row = rectangle(0, 0, 2, 2)
    row["faces"] = row["faces"] * 2
    index = lawn._boundary_index([row, row], [sample(0.1, 1)], 2)
    assert len(list(lawn._boundary_segments([row, row]))) == 4
    assert lawn._clearance((0.1, 1, 0), index, 2) == pytest.approx(0.1)


def test_adjacent_lawns_do_not_leave_a_seam(lawn):
    rows = [rectangle(0, 0, 2, 2), rectangle(2, 0, 4, 2)]
    index = lawn._boundary_index(rows, [sample(2, 1)], 2)
    assert lawn._clearance((2, 1, 0), index, 2) == pytest.approx(1)


def test_hole_border_limits_rotated_patch_footprint(lawn):
    rows = [
        rectangle(0, 0, 4, 10),
        rectangle(6, 0, 10, 10),
        rectangle(4, 0, 6, 4),
        rectangle(4, 6, 6, 10),
    ]
    index = lawn._boundary_index(rows, [sample(3.8, 5)], 2)
    clearance = lawn._clearance((3.8, 5, 0), index, 2)
    assert clearance == pytest.approx(0.2)
    radius = math.hypot(0.4, 0.4)
    horizontal = max(0, clearance - 1e-4) / radius
    assert radius * horizontal < clearance


def test_winding_and_face_order_preserve_border(lawn):
    row = rectangle(0, 0, 2, 2)
    baseline = set(lawn._boundary_segments([row]))
    row["faces"] = [tuple(reversed(face)) for face in reversed(row["faces"])]
    assert set(lawn._boundary_segments([row])) == baseline


def test_far_segment_index_is_clipped_to_relevant_samples(lawn):
    rows = [rectangle(-100000, 0, 100000, 2)]
    index = lawn._boundary_index(rows, [sample(0, 1)], 2)
    assert len(index) < 40
    assert lawn._clearance((0, 1, 0), index, 2) == pytest.approx(1)


def test_distant_detail_blends_into_turf_without_tall_enlarged_clumps(
    lawn, monkeypatch
):
    class Identity:
        def __matmul__(self, point):
            return SimpleNamespace(x=point[0], y=point[1])

    class Instance(dict):
        def __init__(self):
            self.rotation_euler = SimpleNamespace(z=0)

    collection = SimpleNamespace(
        objects=[
            SimpleNamespace(
                type="MESH",
                matrix_world=Identity(),
                bound_box=[(-0.3, -0.3, 0), (0.3, 0.3, 0.09)],
            )
        ]
    )
    linked = []
    monkeypatch.setattr(
        lawn.style,
        "load_collection_asset",
        lambda *_: (collection, 0.09),
        raising=False,
    )
    monkeypatch.setattr(
        lawn.style, "tune_botaniq_lawn_material", lambda _: [], raising=False
    )
    monkeypatch.setattr(
        lawn.bpy,
        "data",
        SimpleNamespace(objects=SimpleNamespace(new=lambda *_: Instance())),
        raising=False,
    )
    monkeypatch.setattr(
        lawn.bpy,
        "context",
        SimpleNamespace(
            collection=SimpleNamespace(objects=SimpleNamespace(link=linked.append))
        ),
        raising=False,
    )
    samples = [
        lawn_scatter.LawnSample((0, 0, 0), "lawn", 42, coverage) for coverage in (1, 6)
    ]
    monkeypatch.setattr(
        lawn,
        "scatter_lawn",
        lambda *_a, **_k: SimpleNamespace(
            samples=samples,
            raster_candidate_count=2,
            candidate_count=2,
            visible_tile_count=1,
            auxiliary_bytes=0,
            spacing_m=0.3,
        ),
    )
    count, *_ = lawn.add_lawn_instances(
        [rectangle(-20, -20, 20, 20)],
        ["grass.blend"],
        camera_position=(0, -5, 2),
        camera_target=(0, 50, 0),
        camera_up=(0, 0, 1),
        vertical_fov_radians=math.radians(42),
        aspect=1.5,
        near_m=0.04,
        far_m=2000,
    )
    near, far = linked
    assert count == 2
    assert far.scale[2] < near.scale[2] / 2
    assert far.scale[0] < near.scale[0] * 3
    assert near.location[2] == far.location[2]


def test_production_aggregate_without_id_keeps_address_and_shared_arrays(
    lawn, monkeypatch
):
    monkeypatch.setitem(sys.modules, "lawn_instances", lawn)
    path = Path(__file__).parents[1] / "app/rendering/blender_worker.py"
    spec = importlib.util.spec_from_file_location("lawn_production_worker_test", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    faces = [(0, 1, 2)]
    source = {"kind": "lawn", "vertices": vertices, "faces": faces}
    packet = {"meshes": [], "environment_surfaces": [{"kind": "road"}, source]}
    rows = worker.lawn_surface_rows(packet)
    assert rows[0]["id"] == "environment:lawn:1"
    assert rows[0]["vertices"] is vertices
    assert rows[0]["faces"] is faces
    assert "id" not in source


def test_production_source_lawn_id_is_retained(lawn, monkeypatch):
    monkeypatch.setitem(sys.modules, "lawn_instances", lawn)
    path = Path(__file__).parents[1] / "app/rendering/blender_worker.py"
    spec = importlib.util.spec_from_file_location("lawn_address_worker_test", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    packet = {"meshes": [{**rectangle(0, 0, 2, 2), "id": "native:lawn:42"}]}
    assert worker.lawn_surface_rows(packet)[0]["id"] == "native:lawn:42"
