"""Бордюры быстрого 3D сохраняют нативную геометрию и семантику полного рендера."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from shapely import constrained_delaunay_triangles, union_all
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.geometry.polygon import orient
from shapely.ops import substring

from app.dxf_import.contracts import ImportMode, SourceFile
from app.dxf_import.layers.contracts import Layer
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.rendering import curb_geometry, environment
from app.rendering.environment_inputs import environment_inputs
from app.rendering.packet import display_meshes
from app.rendering.scene_view import SceneViewReader
from app.scene.domain import build_scene
from app.scene.plants import scene_origin
from app.species.catalog import get_species


def feature(identifier: str, geometry: Any, **properties: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "geometry": mapping(geometry),
        "properties": {
            "kind": "road",
            "source_layer": "curbs",
            "entity_type": "LINE",
            "source_handle": identifier,
            "source_closed_path": False,
            "source_native_geometry": True,
            "source_geometry_provider": "autocad_snapshot_v1",
            **properties,
        },
    }


def site(
    features: list[dict[str, Any]],
    *,
    confirmed: bool = True,
    category: str | None = "curb",
    origin: tuple[float, float] = (0, 0),
) -> Project:
    layers = [
        Layer(
            id="curbs",
            source_name="curbs",
            suggested_kind="road",
            mapped_kind="road",
            category=category,
            mapping_confirmed=confirmed,
            object_count=len(features),
            color="#000000",
        )
    ]
    if any(row["properties"]["source_layer"] == "grass" for row in features):
        layers.append(
            Layer(
                id="grass",
                source_name="grass",
                suggested_kind="lawn",
                mapped_kind="lawn",
                category="lawn",
                mapping_confirmed=True,
                object_count=1,
                color="#000000",
            )
        )
    x, y = origin
    return Project(
        id="native-curb-scene",
        name="Native curb scene",
        source_file=SourceFile(
            name="accepted-capture.dwg",
            content_sha256="a" * 64,
            size=1,
            imported_at="2026-10-14T00:00:00Z",
            dxf_version="AC1032",
            units="m",
            entity_count=len(features),
            bounds=[x - 20, y - 20, x + 20, y + 20],
        ),
        geometry_version=1,
        layers=layers,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        plan=Plan(objects=[]),
    )


def full_environment(
    project: Project, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    snapshot = build_scene(
        project,
        0,
        species=get_species,
        origin_xy=scene_origin(project.model_copy(update={"plan": None})),
    )
    monkeypatch.setattr(
        environment,
        "_neighbours",
        lambda *_args, **_kwargs: ([], {"status": "unaligned"}),
    )
    packet = {
        "scene": snapshot.model_dump(mode="json", exclude={"context_features"}),
        "meshes": display_meshes(snapshot),
        "building_inputs": [],
        "building_display_meshes": [],
        "camera": {"position": [0, 500, 0], "fov_y_deg": 42, "aspect": 1},
        "environment_inputs": environment_inputs(project, snapshot),
    }
    before = deepcopy(packet)
    result = environment.prepare_environment(packet, directory)
    assert packet == before
    return result


def fast_preview(
    project: Project, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    repository = Mock(get=Mock(return_value=project))
    del repository.get_geometry_view
    monkeypatch.setattr(
        "app.rendering.osm.fetch_buildings",
        Mock(side_effect=AssertionError("Сеть не нужна для просмотра бордюров")),
    )
    before = project.model_dump(mode="json")
    view = SceneViewReader(repository, directory).read(project.id, 0)
    assert project.model_dump(mode="json") == before
    assert view.scene["coordinate_origin"] == list(scene_origin(project))
    return view.environment.preview.model_dump(mode="json")


@pytest.mark.parametrize("case", ["closed-hole", "curved", "graded", "multiple-planes"])
def test_bulk_triangles_are_exactly_equal_to_previous_geos_member_projection(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Guard the performance-only replacement against the prior GEOS path."""
    points = {
        "closed-hole": [(0, 0, 2), (12, 0, 2), (12, 8, 2), (0, 8, 2), (0, 0, 2)],
        "curved": [(0, 0, 0), (2.5, 0.75, 0), (4.5, 2.1, 0), (7, 5, 0)],
        "graded": [(0, 0, 2.5), (5, 0, 3), (10, 0, 3.5)],
        "multiple-planes": [(0, 0, 0), (5, 0, 0), (5, 5, 1), (10, 5, 1)],
    }[case]
    rows = [
        {
            "start_scene_xyz_m": a,
            "end_scene_xyz_m": b,
            "source_handle": "native-axis",
            "width_m": 0.15,
            "height_m": 0.18,
        }
        for a, b in zip(points, points[1:])
    ]
    if case == "multiple-planes":
        rows.append(
            {
                "start_scene_xyz_m": [0, 0, 2],
                "end_scene_xyz_m": [10, 0, 2],
                "source_handle": "separate-level",
                "width_m": 0.15,
                "height_m": 0.18,
            }
        )
    current = curb_geometry.curb_envelopes(rows)

    def previous_triangle_projection(polygon):
        for member in constrained_delaunay_triangles(polygon).geoms:
            yield list(orient(member, sign=1.0).exterior.coords)[:3]

    monkeypatch.setattr(
        curb_geometry, "_oriented_triangle_points", previous_triangle_projection
    )
    previous = curb_geometry.curb_envelopes(rows)
    assert current[0] == previous[0]
    assert json.dumps(current[0], sort_keys=True) == json.dumps(
        previous[0], sort_keys=True
    )
    assert current[2] == previous[2]
    assert len(current[1]) == len(previous[1])
    for (current_z, current_top), (previous_z, previous_top) in zip(
        current[1], previous[1]
    ):
        assert current_z == previous_z
        assert current_top.equals_exact(previous_top, tolerance=0)


def curb_meshes(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in preview["meshes"] if row["kind"] == "curb"]


def mesh_footprint(meshes: list[dict[str, Any]]) -> Any:
    polygons = []
    for mesh in meshes:
        if mesh.get("surface_geometry"):
            polygons.append(shape(mesh["surface_geometry"]))
        for face in mesh["faces"]:
            polygon = Polygon([mesh["vertices"][i][:2] for i in face])
            if polygon.is_valid and polygon.area > 0:
                polygons.append(polygon)
    return union_all(polygons)


def assert_envelope_parity(
    preview: dict[str, Any], full: dict[str, Any]
) -> list[dict[str, Any]]:
    meshes = curb_meshes(preview)
    assert meshes
    assert (
        mesh_footprint(meshes)
        .symmetric_difference(mesh_footprint(full["environment_curb_meshes"]))
        .area
        < 1e-8
    )
    assert {tuple(point) for mesh in meshes for point in mesh["vertices"]} == {
        tuple(point)
        for mesh in full["environment_curb_meshes"]
        for point in mesh["vertices"]
    }
    return meshes


@pytest.mark.parametrize("base", [0, 2.5])
def test_confirmed_native_line_keeps_world_origin_and_cad_elevation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: float
) -> None:
    project = site(
        [
            feature(
                "kerb-axis",
                LineString([(1000, 2000), (1010, 2000), (1010, 2005)]),
                source_base_elevation_m=base,
            )
        ],
        origin=(1000, 2000),
    )
    full = full_environment(project, tmp_path / "full", monkeypatch)
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    meshes = assert_envelope_parity(preview, full)
    heights = {round(p[2], 8) for row in meshes for p in row["vertices"]}
    assert heights == {base, base + 0.18}
    footprint = mesh_footprint(meshes)
    assert footprint.covers(Point(5, 0))
    assert footprint.covers(Point(10, 2.5))
    assert footprint.bounds[0] == pytest.approx(0)
    assert footprint.bounds[2] == pytest.approx(10.075)
    assert not footprint.covers(Point(5, 0.2))


def test_native_xyz_curb_preserves_source_grade_instead_of_flattening() -> None:
    from app.rendering.scene_curbs import native_scene_curbs

    features = [
        feature(
            "graded-native-axis",
            LineString([(1000, 2000, 2.5), (1010, 2000, 3.5)]),
            source_base_elevation_m=2.5,
        )
    ]
    project = site(features, origin=(1000, 2000))
    before = project.model_dump(mode="json")
    result = native_scene_curbs(project, features, (1000, 2000))
    meshes = result["meshes"]
    assert meshes
    points = [point for mesh in meshes for point in mesh["vertices"]]
    assert any(point[2] == pytest.approx(3.68) for point in points)
    assert any(point[2] == pytest.approx(3.5) for point in points)
    for x, _y, z in points:
        source_base = 2.5 + x / 10
        assert z == pytest.approx(source_base) or z == pytest.approx(source_base + 0.18)
    footprint = mesh_footprint(meshes)
    assert footprint.bounds == pytest.approx((0, -0.075, 10, 0.075))
    assert project.model_dump(mode="json") == before


def test_native_closed_projection_is_perimeter_without_lawn_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = site(
        [
            feature(
                "island-rim",
                box(0, 0, 10, 10),
                entity_type="LWPOLYLINE",
                source_closed_path=True,
                source_polygon_projection=True,
                source_native_area=None,
            ),
            feature(
                "grass-island",
                box(0.1, 0.1, 9.9, 9.9),
                kind="lawn",
                source_layer="grass",
                entity_type="HATCH",
            ),
        ]
    )
    full = full_environment(project, tmp_path / "full", monkeypatch)
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    meshes = assert_envelope_parity(preview, full)
    assert not mesh_footprint(meshes).intersects(box(0.2, 0.2, 9.8, 9.8))
    assert mesh_footprint(meshes).covers(Point(5, 0))
    lawns = [row for row in preview["meshes"] if row["kind"] == "lawn"]
    assert (
        mesh_footprint(lawns).symmetric_difference(box(0.1, 0.1, 9.9, 9.9)).area < 1e-8
    )


@pytest.mark.parametrize(
    ("geometry", "evidence"),
    [
        (box(0, 0, 8, 0.15), {}),
        (
            Polygon(
                [(0, 0), (4, 0), (4, 4), (0, 4)],
                [[(0.15, 0.15), (0.15, 3.85), (3.85, 3.85), (3.85, 0.15)]],
            ),
            {},
        ),
        (box(0, 0, 4, 4), {"representation": "footprint"}),
        (box(0, 0, 4, 4), {"source_native_area": 16}),
    ],
    ids=[
        "narrow-footprint",
        "footprint-with-hole",
        "explicit-footprint",
        "native-area",
    ],
)
def test_actual_curb_footprint_retains_holes_without_duplicate_line_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    geometry: Any,
    evidence: dict[str, Any],
) -> None:
    project = site(
        [
            feature(
                "physical-footprint",
                geometry,
                entity_type="LWPOLYLINE",
                source_closed_path=True,
                source_polygon_projection=True,
                **evidence,
            )
        ]
    )
    full = full_environment(project, tmp_path / "full", monkeypatch)
    assert full["environment_curb_meshes"] == []
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    meshes = curb_meshes(preview)
    assert len(meshes) == 1
    assert not meshes[0]["vertices"]
    assert meshes[0]["surface_elevation_m"] == pytest.approx(0.18)
    assert mesh_footprint(meshes).symmetric_difference(geometry).area < 1e-8
    assert (
        mesh_footprint(meshes)
        .symmetric_difference(mesh_footprint(full["environment_surfaces"]))
        .area
        < 1e-8
    )
    assert "physical-footprint" not in preview["suppressed_source_ids"]


def native_graphic_features() -> list[dict[str, Any]]:
    curve = LineString([(0, 0), (4.75, 0), (4.75, 10)])
    intervals = [(0, 0.5), *[(1 + 1.5 * i, 2 + 1.5 * i) for i in range(8)], (13, 13.5)]
    result = []
    for index, (start, end) in enumerate(intervals):
        line = substring(curve, start, end)
        result.append(
            feature(
                f"native-unit-{index}",
                line,
                entity_type="LWPOLYLINE" if len(line.coords) > 2 else "LINE",
                source_instance_chain=["capture", "single-native-block"],
            )
        )
    return result


def test_whole_native_block_repeat_stays_2d_with_addressed_suppression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = native_graphic_features()
    project = site(features)
    full = full_environment(project, tmp_path / "full", monkeypatch)
    assert full["environment_curb_meshes"] == []
    assert (
        full["environment"]["curb_composition"]["native_graphic_interpretation"][
            "qualified_source_count"
        ]
        == 10
    )
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    assert curb_meshes(preview) == []
    assert {row["id"] for row in features} <= set(preview["suppressed_source_ids"])


@pytest.mark.parametrize("protection", ["explicit-physical", "separate-blocks"])
def test_native_repeat_without_whole_graphic_proof_keeps_physical_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protection: str
) -> None:
    features = native_graphic_features()
    if protection == "explicit-physical":
        features[0]["properties"]["representation"] = "footprint"
    else:
        for row in features:
            row["properties"]["source_instance_chain"] = ["capture", row["id"]]
    project = site(features)
    full = full_environment(project, tmp_path / "full", monkeypatch)
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    assert_envelope_parity(preview, full)
    assert not {row["id"] for row in features}.intersection(
        preview["suppressed_source_ids"]
    )


@pytest.mark.parametrize(
    ("confirmed", "category"), [(False, "curb"), (True, None), (True, "carriageway")]
)
def test_unconfirmed_or_noncurb_role_does_not_create_curb_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmed: bool,
    category: str | None,
) -> None:
    project = site(
        [feature("unadmitted-line", LineString([(0, 0), (10, 0)]))],
        confirmed=confirmed,
        category=category,
    )
    full = full_environment(project, tmp_path / "full", monkeypatch)
    assert full["environment_curb_meshes"] == []
    preview = fast_preview(project, tmp_path / "fast", monkeypatch)
    assert curb_meshes(preview) == []


@pytest.mark.parametrize("compact", [False, True])
def test_warm_2d_index_reuses_confirmed_curb_paths_without_reloading_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compact: bool
) -> None:
    project = site(
        [
            feature("native-axis", LineString([(0, 0), (10, 0)])),
            feature(
                "unconfirmed-axis", LineString([(0, 5), (10, 5)]), source_layer="other"
            ),
        ]
    )
    project.layers.append(
        Layer(
            id="other",
            source_name="other",
            suggested_kind="road",
            mapped_kind="road",
            category="curb",
            mapping_confirmed=False,
            object_count=1,
            color="#000000",
        )
    )
    project.import_status.mode = ImportMode.AUTOCAD_LIVE
    # The retained native collection is JSON, including nested coordinate lists.
    project.geometry.feature_collection = json.loads(
        json.dumps(project.geometry.feature_collection)
    )
    query = IndexedGeometryQuery(max_cached_features=1 if compact else 100)
    query._index(project)
    cached = query.cached_scene_features(project)
    assert cached is not None
    assert [row["id"] for row in cached] == ["native-axis"]
    assert cached[0] == project.geometry.feature_collection["features"][0]
    assert query._indexes[project.id].features.encoded_count == (2 if compact else 0)
    before = project.model_dump(mode="json")
    repository = Mock(
        get=Mock(return_value=project),
        get_geometry_view=Mock(
            side_effect=AssertionError("source geometry reread after 2D")
        ),
    )
    reader = SceneViewReader(
        repository, tmp_path, display_features=query.cached_scene_features
    )
    monkeypatch.setattr(
        "app.rendering.osm.fetch_buildings",
        Mock(side_effect=AssertionError("network in fast viewer")),
    )
    result = reader.read(project.id, 0)
    meshes = curb_meshes(result.environment.preview.model_dump(mode="json"))
    assert mesh_footprint(meshes).bounds == pytest.approx((0, -0.075, 10, 0.075))
    assert result.environment.provenance["curb_volume_source_ids"] == ["native-axis"]
    repository.get_geometry_view.assert_not_called()
    assert project.model_dump(mode="json") == before
