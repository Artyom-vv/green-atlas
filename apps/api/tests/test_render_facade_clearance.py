"""Illustrative details must not cover the back of a numerically coincident wall."""

from copy import deepcopy
from math import hypot
from typing import Any

import pytest
from shapely import union_all
from shapely.geometry import Polygon, box, mapping

from app.rendering import facade_geometry
from app.rendering.packet import display_meshes
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def building(polygon: Polygon) -> dict[str, Any]:
    snapshot = SceneSnapshot(
        plan_version=1,
        horizon_year=0,
        coordinate_origin=[0, 0],
        note="Accepted synthetic CAD building",
        context_features=[
            SceneContextFeature(
                feature_id="synthetic-building",
                kind="building",
                geometry=mapping(polygon),
                height_m=6,
                height_status="confirmed",
            )
        ],
    )
    return display_meshes(snapshot)[0]


def maximum_overlay(row: dict[str, Any], details: list[dict[str, Any]]) -> float:
    """Independent wall-plane check, including opposite/back-facing triangles."""
    maximum = 0.0
    for ring in row["footprint_rings"]:
        for a, b in zip(ring, ring[1:] + ring[:1]):
            length = hypot(b[0] - a[0], b[1] - a[1])
            if length <= 1e-8:
                continue
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length

            def faces(mesh: dict[str, Any]) -> list[Polygon]:
                polygons = []
                for face in mesh["faces"]:
                    points = [mesh["vertices"][index] for index in face]
                    if not all(
                        abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux) <= 1e-7
                        for p in points
                    ):
                        continue
                    coordinates = [
                        ((p[0] - a[0]) * ux + (p[1] - a[1]) * uy, p[2]) for p in points
                    ]
                    if (
                        min(s for s, _ in coordinates) >= -1e-7
                        and max(s for s, _ in coordinates) <= length + 1e-7
                    ):
                        polygon = Polygon(coordinates)
                        if polygon.area > 1e-10:
                            polygons.append(polygon)
                return polygons

            overlap = union_all(faces(row)).intersection(
                union_all([polygon for detail in details for polygon in faces(detail)])
            )
            maximum = max(maximum, overlap.area)
    return maximum


def test_native_thin_return_keeps_walls_but_omits_conflicting_illustrative_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polygon = Polygon(
        [(0, 0), (20, 0), (20, 20), (10 + 1e-12, 20), (10, 14), (10, 20), (0, 20)]
    )
    assert polygon.is_valid
    source = building(polygon)
    original = deepcopy(source)
    with monkeypatch.context() as previous:
        previous.setattr(facade_geometry, "_opposite_detail_edges", lambda *_: {})
        old, old_details, _ = facade_geometry.prepare_facades([source], "cad_building")
    assert maximum_overlay(old[0], old_details) > 0.01

    prepared, details, evidence = facade_geometry.prepare_facades(
        [source], "cad_building"
    )
    omitted = evidence[0]["omitted_detail_edges"]
    assert {(row["ring_index"], row["edge_index"]) for row in omitted} == {
        (0, 3),
        (0, 4),
    }
    assert all(
        row["reason"] == "opposite_boundary_below_display_separation" for row in omitted
    )
    assert all(row["opposite_edges"] for row in omitted)
    assert maximum_overlay(prepared[0], details) < 1e-8
    assert prepared[0]["footprint_rings"] == original["footprint_rings"]
    assert prepared[0]["height_m"] == original["height_m"]
    assert prepared[0]["height_status"] == original["height_status"]
    roof = union_all(
        [
            Polygon([prepared[0]["vertices"][i][:2] for i in face])
            for face in prepared[0]["faces"]
            if all(prepared[0]["vertices"][i][2] == 6 for i in face)
        ]
    )
    assert roof.symmetric_difference(polygon).area < 1e-10
    assert source == original
    assert evidence[0]["windows"] > 0


def test_accessible_outer_facades_and_real_courtyard_keep_their_details() -> None:
    polygon = Polygon(
        box(0, 0, 20, 20).exterior.coords, [box(4, 4, 16, 16).exterior.coords]
    )
    source = building(polygon)
    prepared, details, evidence = facade_geometry.prepare_facades(
        [source], "cad_building"
    )
    assert evidence[0]["omitted_detail_edges"] == []
    assert evidence[0]["facade_joints"] == 8
    glasses = next(row for row in details if row["kind"] == "facade_glass")
    for ring in prepared[0]["footprint_rings"]:
        for a, b in zip(ring, ring[1:] + ring[:1]):
            length = hypot(b[0] - a[0], b[1] - a[1])
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            assert any(
                all(
                    abs(
                        (glasses["vertices"][i][0] - a[0]) * uy
                        - (glasses["vertices"][i][1] - a[1]) * ux
                        + 0.045
                    )
                    < 1e-8
                    and 0
                    < (glasses["vertices"][i][0] - a[0]) * ux
                    + (glasses["vertices"][i][1] - a[1]) * uy
                    < length
                    for i in face
                )
                for face in glasses["faces"]
            ), "Every outer and courtyard edge retains its inward recessed windows"
    assert maximum_overlay(prepared[0], details) < 1e-8


def test_distinct_opposite_edges_are_not_suppressed_by_physical_width_assumptions() -> (
    None
):
    # A measurable 0.1-mm separation is still above the display coplanarity
    # tolerance. This guard is not a centimetre-scale wall/clearance rule.
    polygon = Polygon(
        [
            (0, 0),
            (20, 0),
            (20, 20),
            (10.0001, 20),
            (10.0001, 14),
            (10, 14),
            (10, 20),
            (0, 20),
        ]
    )
    _, _, evidence = facade_geometry.prepare_facades(
        [building(polygon)], "cad_building"
    )
    assert evidence[0]["omitted_detail_edges"] == []
