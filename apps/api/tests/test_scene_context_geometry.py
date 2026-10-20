"""The scene must retain CAD coverage before preview/render triangulation."""

from copy import deepcopy
from math import cos, pi, sin
from typing import Any

import pytest
from shapely import union_all
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry

from app.geometry.contracts import GeometrySnapshot
from app.projects.contracts import Project
from app.rendering.packet import display_meshes
from app.scene.config import CONTEXT_KINDS, CONTEXT_SIMPLIFY_TOLERANCE_M
from app.scene.context import scene_context
from app.scene.contracts import SceneSnapshot


def feature(geometry: BaseGeometry, kind: str, identifier: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": identifier,
        "geometry": mapping(geometry),
        "properties": {
            "kind": kind,
            "source_handle": identifier,
            "source_layer": f"accepted-{kind}",
            "entity_type": "HATCH"
            if geometry.geom_type.endswith("Polygon")
            else "POLYLINE",
            "source_base_elevation_m": 12.5,
        },
    }


def project_with(features: list[dict[str, Any]]) -> Project:
    return Project(
        name="Synthetic shared CAD boundaries",
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
    )


def scene(project: Project, origin: tuple[float, float] = (0, 0)) -> SceneSnapshot:
    context = scene_context(project, *origin)
    return SceneSnapshot(
        plan_version=1,
        horizon_year=0,
        coordinate_origin=list(origin),
        geometry_source=context.geometry_source,
        context_features=context.features,
        vertical_primitives=context.primitives,
        note="Synthetic accepted snapshot; no CAD parsing or external data",
    )


def footprint(mesh: dict[str, Any]) -> BaseGeometry:
    # Side walls have four vertices. Only horizontal surface triangles own XY area.
    return union_all(
        [
            Polygon([mesh["vertices"][index][:2] for index in face])
            for face in mesh["faces"]
            if len(face) == 3
        ]
    )


def adjacent_curved_polygons(
    start: int, reverse: bool, densify: bool
) -> tuple[Polygon, Polygon]:
    arc = [(10 * cos(i * pi / 128), 10 * sin(i * pi / 128)) for i in range(65)]
    left = [(0.0, 0.0), *arc]
    left = left[start:] + left[:start]
    right = [*arc, (0.0, 15.0), (15.0, 15.0), (15.0, 0.0)]
    if densify:
        # Same exact shared polyline, with additional collinear vertices in one ring.
        right = [
            point
            for a, b in zip(right, right[1:] + right[:1])
            for point in [a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)]
        ]
    if reverse:
        right.reverse()
    return Polygon(left), Polygon(right)


@pytest.mark.parametrize("kinds", [("road", "road"), ("lawn", "road")])
@pytest.mark.parametrize(
    ("start", "reverse", "densify"),
    [(3, False, False), (9, True, False), (22, False, True), (41, True, True)],
)
def test_shared_curved_boundaries_have_no_new_gaps_or_overlap_in_render_meshes(
    kinds: tuple[str, str], start: int, reverse: bool, densify: bool
) -> None:
    polygons = adjacent_curved_polygons(start, reverse, densify)
    expected = union_all(polygons)
    assert polygons[0].intersection(polygons[1]).area < 1e-12

    # This fixture reproduces the former defect: preserve_topology applies to
    # each polygon, not the shared boundary of the two accepted surfaces.
    old_coverage = union_all(
        [
            polygon.simplify(CONTEXT_SIMPLIFY_TOLERANCE_M, preserve_topology=True)
            for polygon in polygons
        ]
    )
    assert expected.difference(old_coverage).area > 0.1

    project = project_with(
        [
            feature(polygon, kind, str(i))
            for i, (polygon, kind) in enumerate(zip(polygons, kinds))
        ]
    )
    original = deepcopy(project.model_dump())
    snapshot = scene(project)
    prepared = [footprint(mesh) for mesh in display_meshes(snapshot)]

    # Test each owner's coverage as well as the combined area; merely covering
    # the seams with another surface would not satisfy this regression.
    for before, after in zip(polygons, prepared):
        assert before.symmetric_difference(after).area < 1e-10
    assert expected.symmetric_difference(union_all(prepared)).area < 1e-10
    assert prepared[0].intersection(prepared[1]).area < 1e-10
    assert project.model_dump() == original


def test_multipart_surfaces_keep_real_holes_and_narrow_intentional_gaps() -> None:
    curved_hole = Point(4, 4).buffer(1, quad_segs=16)
    first = Polygon(box(0, 0, 8, 8).exterior.coords, [curved_hole.exterior.coords])
    second = box(8.03, 0, 12, 8)
    source = MultiPolygon([first, second])
    snapshot = scene(project_with([feature(source, "road", "multipart-road")]))
    meshes = display_meshes(snapshot)
    actual = footprint(meshes[0])

    assert actual.symmetric_difference(source).area < 1e-10
    assert not actual.covers(Point(4, 4))
    assert not actual.covers(Point(8.015, 4))
    assert actual.covers(Point(7.99, 4))
    assert actual.covers(Point(8.04, 4))
    translated = shape(snapshot.context_features[0].geometry)
    assert translated.geom_type == "MultiPolygon"
    assert len(translated.geoms) == 2
    assert len(translated.geoms[0].interiors) == 1


@pytest.mark.parametrize("kind", ["road", "lawn", "water", "building"])
def test_physical_polygon_context_preserves_coverage(kind: str) -> None:
    polygon, _ = adjacent_curved_polygons(9, False, False)
    snapshot = scene(project_with([feature(polygon, kind, "curved-area")]))
    actual = shape(snapshot.context_features[0].geometry)
    assert actual.symmetric_difference(polygon).area < 1e-10


@pytest.mark.parametrize(
    "kind", sorted(CONTEXT_KINDS - {"road", "lawn", "water", "building"})
)
def test_independent_nonphysical_polygon_outlines_keep_bounded_simplification(
    kind: str,
) -> None:
    polygon, _ = adjacent_curved_polygons(9, False, False)
    snapshot = scene(project_with([feature(polygon, kind, "independent-outline")]))
    actual = shape(snapshot.context_features[0].geometry)
    assert len(actual.exterior.coords) < len(polygon.exterior.coords)
    assert actual.hausdorff_distance(polygon) <= CONTEXT_SIMPLIFY_TOLERANCE_M
    assert actual.is_valid
    meshes = display_meshes(snapshot)
    assert all(mesh["faces"] == [] for mesh in meshes)


@pytest.mark.parametrize("kind", ["road", "lawn", "water", "building"])
@pytest.mark.parametrize("multipart", [False, True])
def test_linear_surface_and_curb_boundaries_keep_the_accepted_curve(
    kind: str, multipart: bool
) -> None:
    arc = [(10 * cos(i * pi / 128), 10 * sin(i * pi / 128)) for i in range(65)]
    geometry = MultiLineString([arc[:33], arc[32:]]) if multipart else LineString(arc)
    row = feature(geometry, kind, "physical-boundary")
    row["properties"]["source_layer"] = "physical-curb" if kind == "road" else kind
    snapshot = scene(project_with([row]))
    mesh = display_meshes(snapshot)[0]
    output = MultiLineString([[point[:2] for point in line] for line in mesh["lines"]])

    assert output.hausdorff_distance(geometry) < 1e-12
    assert output.length == pytest.approx(geometry.length)
    assert mesh["faces"] == []
    assert mesh["vertices"] == []


def test_utility_linework_keeps_bounded_preview_simplification() -> None:
    source = LineString([(0, 0), (1, 0.03), (2, -0.04), (3, 0.02), (4, 0)])
    snapshot = scene(project_with([feature(source, "utility", "utility-line")]))
    actual = shape(snapshot.context_features[0].geometry)
    assert len(actual.coords) < len(source.coords)
    assert list(actual.coords)[0] == list(source.coords)[0]
    assert list(actual.coords)[-1] == list(source.coords)[-1]
    assert actual.hausdorff_distance(source) <= CONTEXT_SIMPLIFY_TOLERANCE_M


@pytest.mark.parametrize("prepared", [False, True])
def test_selected_snapshot_xyz_is_only_translated_and_source_stays_immutable(
    prepared: bool,
) -> None:
    origin = (400000.25, 2500000.5)
    ring = [
        (origin[0] + x, origin[1] + y, z)
        for x, y, z in [
            (0, 0, 100),
            (1, 0.03125, 101),
            (2, 0, 102),
            (2, 2, 103),
            (0, 2, 104),
        ]
    ]
    polygon = Polygon(ring)
    project = project_with([feature(polygon, "road", "selected")])
    if prepared:
        project.source_geometry = GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [feature(box(0, 0, 1, 1), "road", "unselected")],
            }
        )
    else:
        project.source_geometry = project.geometry
        project.geometry = None
    original = deepcopy(project.model_dump())
    snapshot = scene(project, origin)
    actual = snapshot.context_features[0]

    assert actual.feature_id == "selected"
    assert actual.geometry["coordinates"][0] == [
        [x - origin[0], y - origin[1], z] for x, y, z in polygon.exterior.coords
    ]
    assert snapshot.geometry_source == (
        "prepared_geometry" if prepared else "source_geometry"
    )
    assert project.model_dump() == original


def test_invalid_accepted_polygon_is_not_repaired_or_filled_during_scene_projection() -> (
    None
):
    source = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    snapshot = scene(project_with([feature(source, "road", "invalid-boundary")]))
    assert len(snapshot.context_features) == 1
    assert not shape(snapshot.context_features[0].geometry).is_valid
    with pytest.raises(ValueError, match="invalid-boundary"):
        display_meshes(snapshot)
