"""Shared display geometry has one visible owner and honest facade assumptions."""

from copy import deepcopy
from typing import Any

import pytest
from shapely import union_all
from shapely.geometry import LineString, Point, Polygon, box, mapping

from app.rendering.curb_geometry import curb_envelopes
from app.rendering.facade_geometry import prepare_facades
from app.rendering.packet import display_meshes
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def curb(identifier: str, a: list[float], b: list[float]) -> dict[str, Any]:
    return {
        "source_handle": identifier,
        "start_scene_xyz_m": a,
        "end_scene_xyz_m": b,
        "width_m": 0.2,
        "height_m": 0.18,
    }


def top_triangles(row: dict[str, Any], height: float) -> list[Polygon]:
    return [
        Polygon([row["vertices"][i][:2] for i in face])
        for face in row["faces"]
        if len(face) == 3
        and all(row["vertices"][i][2] == pytest.approx(height) for i in face)
    ]


def test_partial_crossing_and_equal_curb_chains_have_one_top_face_owner() -> None:
    rows = [
        curb("main", [0, 0, 0], [10, 0, 0]),
        curb("partial", [5, 0.03, 0], [15, 0.03, 0]),
        curb("cross", [7, -3, 0], [7, 3, 0]),
        curb("duplicate", [10, 0, 0], [0, 0, 0]),
    ]
    original = deepcopy(rows)
    meshes, tops, evidence = curb_envelopes(rows)
    assert len(meshes) == len(tops) == 1
    expected = union_all(
        [
            LineString([r["start_scene_xyz_m"][:2], r["end_scene_xyz_m"][:2]]).buffer(
                0.1, cap_style="flat"
            )
            for r in rows
        ]
    )
    triangles = top_triangles(meshes[0], 0.18)
    assert union_all(triangles).symmetric_difference(expected).area < 1e-10
    assert sum(p.area for p in triangles) == pytest.approx(expected.area)
    assert set(meshes[0]["source_ids"]) == {r["source_handle"] for r in rows}
    assert evidence["removed_duplicate_strip_area_m2"] > 2
    assert rows == original


def test_closed_curb_loop_retains_courtyard_and_outward_face_normals() -> None:
    points = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]]
    meshes, _, _ = curb_envelopes(
        [curb(str(i), a, b) for i, (a, b) in enumerate(zip(points, points[1:]))]
    )
    row = meshes[0]
    tops = union_all(top_triangles(row, 0.18))
    assert not tops.covers(Point(5, 5))
    assert tops.covers(Point(5, 0))
    for face in row["faces"]:
        if len(face) != 3:
            continue
        a, b, c = [row["vertices"][i] for i in face]
        winding = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        assert winding > 0 if a[2] == pytest.approx(0.18) else winding < 0


def test_curb_envelopes_preserve_grade_and_keep_different_levels_separate() -> None:
    rows = [curb("graded", [0, 0, 2], [10, 0, 3]), curb("upper", [0, 0, 7], [10, 0, 7])]
    meshes, flat, _ = curb_envelopes(rows)
    assert len(meshes) == 2 and len(flat) == 1
    graded = next(r for r in meshes if r["source_ids"] == ["graded"])
    for x, y, z in graded["vertices"]:
        assert z == pytest.approx(2 + x * 0.1) or z == pytest.approx(2.18 + x * 0.1)
    assert min(v[2] for v in graded["vertices"]) == 2
    assert max(v[2] for v in graded["vertices"]) == pytest.approx(3.18)


def test_one_source_curb_has_continuous_outer_corner_and_retains_open_end() -> None:
    meshes, _, _ = curb_envelopes(
        [
            curb("continuous", [0, 0, 0], [5, 0, 0]),
            curb("continuous", [5, 0, 0], [5, 5, 0]),
        ]
    )
    top = union_all(top_triangles(meshes[0], 0.18))
    assert top.covers(Point(5.08, -0.08))
    assert not top.covers(Point(-0.08, 0))
    assert not top.covers(Point(4.8, 0.2))


def building(
    building_class: str, height: float, geometry: Polygon | None = None
) -> dict[str, Any]:
    polygon = geometry if geometry is not None else box(0, 0, 18, 12)
    scene = SceneSnapshot(
        plan_version=1,
        horizon_year=0,
        coordinate_origin=[0, 0],
        note="source unchanged",
        context_features=[
            SceneContextFeature(
                feature_id="building-source",
                kind="building",
                geometry=mapping(polygon),
                height_m=height,
                height_status="estimated",
                base_elevation_m=150,
            )
        ],
    )
    row = display_meshes(scene)[0]
    row["building_class"] = building_class
    return row


@pytest.mark.parametrize(
    ("building_class", "height"),
    [
        ("building", 6),
        ("yes", 3),
        ("kiosk", 6),
        ("service", 4.2),
        ("school", 12),
        ("kindergarten", 6),
        ("retail", 6),
        ("apartments", 51),
    ],
)
def test_all_building_classes_have_shared_recessed_glass_without_changing_evidence(
    building_class: str, height: float
) -> None:
    row = building(building_class, height)
    original = deepcopy(row)
    prepared, facades, evidence = prepare_facades([row], "cad_building")
    assert row == original
    assert prepared[0]["building_class"] == building_class
    assert prepared[0]["height_m"] == height
    assert prepared[0]["height_status"] == "estimated"
    assert prepared[0]["footprint_rings"] == original["footprint_rings"]
    assert evidence[0]["windows"] > 0 and evidence[0]["entries"] == 1
    assert evidence[0]["facade_joints"] > 0
    assert {r["kind"] for r in facades} == {
        "facade_glass",
        "facade_frame",
        "facade_joint",
        "facade_plinth",
    }
    visible_vertices = [v for mesh in [prepared[0], *facades] for v in mesh["vertices"]]
    assert min(v[2] for v in visible_vertices) == 150
    assert max(v[2] for v in visible_vertices) == 150 + height
    footprint = box(0, 0, 18, 12)
    for detail in facades:
        assert detail["source_building_id"] == row["id"]
        assert detail["source"] == "cad_building"
        for x, y, z in detail["vertices"]:
            assert footprint.covers(Point(x, y))
            assert 150 <= z <= 150 + height
    glass = next(r for r in facades if r["kind"] == "facade_glass")
    assert all(footprint.contains(Point(v[:2])) for v in glass["vertices"])


def test_facade_cutouts_and_flush_details_do_not_share_coplanar_wall_area() -> None:
    row = building("building", 6)
    prepared, facades, _ = prepare_facades([row], "cad_building")
    wall = prepared[0]
    for axis, boundary in [(0, 0), (0, 18), (1, 0), (1, 12)]:
        other = 1 - axis
        polygons = []
        for face in wall["faces"]:
            points = [wall["vertices"][i] for i in face]
            if all(p[axis] == pytest.approx(boundary) for p in points):
                polygons.append(Polygon([(p[other], p[2]) for p in points]))
        wall_area = union_all(polygons)
        for detail in facades:
            for face in detail["faces"]:
                points = [detail["vertices"][i] for i in face]
                if all(p[axis] == pytest.approx(boundary) for p in points):
                    projected = Polygon([(p[other], p[2]) for p in points])
                    assert wall_area.intersection(projected).area < 1e-9


def test_courtyard_and_reversed_ring_winding_keep_glass_inside_occupied_footprint() -> (
    None
):
    polygon = Polygon(
        [(0, 0), (30, 0), (30, 30), (0, 30)], [[(8, 8), (8, 22), (22, 22), (22, 8)]]
    )
    row = building("school", 12, polygon)
    row["footprint_rings"] = [list(reversed(r)) for r in row["footprint_rings"]]
    before = deepcopy(row)
    prepared, facades, _ = prepare_facades([row], "map_building")
    assert row == before
    assert prepared[0]["footprint_rings"] == before["footprint_rings"]
    glass = next(r for r in facades if r["kind"] == "facade_glass")
    assert all(polygon.contains(Point(v[:2])) for v in glass["vertices"])
    assert not any(Point(v[:2]).within(box(8, 8, 22, 22)) for v in glass["vertices"])
    assert all(r["source"] == "map_building" for r in facades)


def test_base_course_and_divided_openings_partition_the_existing_wall_without_gaps() -> (
    None
):
    row = building("building", 6)
    prepared, facades, evidence = prepare_facades([row], "cad_building")
    # Project front-facing wall, frames, glass and plinth onto the accepted wall.
    # The finish changes must leave neither uncovered strips nor overlapping
    # front-facing areas, including the doorway cut out of the base course.
    projected: list[Polygon] = []
    for mesh in [*prepared, *facades]:
        for face in mesh["faces"]:
            points = [mesh["vertices"][i] for i in face]
            if all(abs(p[1]) <= 0.046 for p in points):
                polygon = Polygon([(p[0], p[2]) for p in points])
                if polygon.area > 1e-10:
                    projected.append(polygon)
    covered = union_all(projected)
    assert covered.symmetric_difference(box(0, 150, 18, 156)).area < 1e-9
    assert sum(p.area for p in projected) == pytest.approx(covered.area)
    glass = next(mesh for mesh in facades if mesh["kind"] == "facade_glass")
    assert len(glass["faces"]) == 2 * evidence[0]["windows"] + evidence[0]["entries"]
    plinth = next(mesh for mesh in facades if mesh["kind"] == "facade_plinth")
    assert min(v[2] for v in plinth["vertices"]) == 150
    assert max(v[2] for v in plinth["vertices"]) == pytest.approx(150.36)


@pytest.mark.parametrize(
    ("height", "expected_rows"), [(3, 1), (6, 2), (9, 3), (4.2, 1)]
)
def test_facade_fills_the_available_height_without_inventing_extra_storey(
    height: float, expected_rows: int
) -> None:
    row = building("building", height)
    original = deepcopy(row)
    _, facades, _ = prepare_facades([row], "cad_building")
    glass = next(mesh for mesh in facades if mesh["kind"] == "facade_glass")
    # The short wall has no entry, so all glazing belongs to ordinary windows.
    bands = {
        round(min(glass["vertices"][i][2] for i in face), 5)
        for face in glass["faces"]
        if all(abs(glass["vertices"][i][0] - 0.045) < 1e-8 for i in face)
    }
    assert len(bands) == expected_rows
    assert max(v[2] for v in glass["vertices"]) < 150 + height
    assert row == original
