"""Render surroundings preserve accepted CAD and label separate map evidence."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from shapely import union_all
from shapely.geometry import (
    LineString,
    MultiLineString,
    Point,
    Polygon,
    box,
    mapping,
    shape,
)
from shapely.ops import transform

from app.geometry.contracts import CoordinateReference
from app.rendering import building_heights, environment
from app.rendering.georeference import RenderFrame, render_frame
from app.rendering.osm import OsmEvidenceError
from app.rendering.packet import display_meshes
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def context(
    identifier: str, kind: str, geometry: Any, **attributes: Any
) -> SceneContextFeature:
    return SceneContextFeature(
        feature_id=identifier, kind=kind, geometry=mapping(geometry), **attributes
    )


def packet_for(
    features: list[SceneContextFeature], *, aligned: bool = False
) -> dict[str, Any]:
    reference = (
        CoordinateReference(
            status="declared",
            source="dxf_custom_georeference",
            axis_order="xy",
            origin_wgs84=[55.0, 37.0],
            local_projection="local_equirectangular_wgs84",
            earth_radius_m=6_378_137,
            evidence="explicit test frame",
        )
        if aligned
        else CoordinateReference()
    )
    scene = SceneSnapshot(
        project_id="unrelated-kit",
        geometry_version=7,
        source_sha256="immutable-source",
        content_sha256="immutable-scene",
        plan_version=3,
        horizon_year=20,
        coordinate_origin=[100, 200],
        coordinate_reference=reference,
        context_features=features,
        note="unchanged accepted scene",
    )
    return {
        "scene": scene.model_dump(mode="json"),
        "meshes": display_meshes(scene),
        "building_inputs": building_heights.building_inputs(scene),
        "camera": {"position": [0, 1.7, 0], "fov_y_deg": 42, "aspect": 16 / 9},
        "appearance": {"profile": "native_v1"},
        "environment_inputs": {
            "roles": {},
            "role_evidence": [],
            "omissions": {"counts": {}},
        },
    }


def roles(packet: dict[str, Any], assignments: dict[str, str]) -> None:
    features = {r["feature_id"]: r for r in packet["scene"]["context_features"]}
    packet["environment_inputs"]["roles"] = assignments
    packet["environment_inputs"]["role_evidence"] = [
        {
            "feature_id": identifier,
            "role": role,
            "mapping_confirmed": True,
            "geometry_type": features[identifier]["geometry"]["type"],
        }
        for identifier, role in assignments.items()
    ]


def mesh_footprint(mesh: dict[str, Any]) -> Any:
    return union_all(
        [
            Polygon([mesh["vertices"][index][:2] for index in face])
            for face in mesh["faces"]
            if len(face) == 3
        ]
    )


def frame_for(packet: dict[str, Any], directory: Path) -> RenderFrame:
    frame = render_frame(SceneSnapshot.model_validate(packet["scene"]), directory)
    assert frame is not None
    return frame


def map_feature(
    frame: RenderFrame, identifier: str, geometry: Any, **tags: str
) -> dict[str, Any]:
    def coordinate(x: float, y: float, *_: float) -> tuple[float, float]:
        return frame.cad_to_wgs84(
            x + frame.coordinate_origin[0], y + frame.coordinate_origin[1]
        )

    return {
        "id": identifier,
        "geometry": mapping(transform(coordinate, geometry)),
        "properties": {"tags": {"building": "yes", **tags}},
    }


def map_result(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "features": features,
        "provenance": {"source_sha256": "external-identity", "attribution": "OSM"},
        "rejected": [],
    }


def test_surfaces_union_duplicate_faces_preserve_holes_and_accepted_source(
    tmp_path: Path,
) -> None:
    courtyard = Polygon(
        [(0, 20), (30, 20), (30, 50), (0, 50)],
        [[(10, 30), (10, 40), (20, 40), (20, 30)]],
    )
    packet = packet_for(
        [
            context("road-a", "road", box(0, 0, 20, 10)),
            context("road-b", "road", box(10, 0, 30, 10)),
            context("walk-a", "road", courtyard),
            context("walk-b", "road", courtyard),
            context("grass", "lawn", box(0, 60, 10, 70)),
        ]
    )
    roles(
        packet,
        {
            "road-a": "road",
            "road-b": "road",
            "walk-a": "sidewalk",
            "walk-b": "sidewalk",
            "grass": "lawn",
        },
    )
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    surfaces = {row["kind"]: row for row in result["environment_surfaces"]}
    assert set(surfaces) == {"road", "sidewalk", "lawn"}
    assert (
        mesh_footprint(surfaces["road"]).symmetric_difference(box(0, 0, 30, 10)).area
        < 1e-8
    )
    assert (
        mesh_footprint(surfaces["sidewalk"]).symmetric_difference(courtyard).area < 1e-8
    )
    assert mesh_footprint(surfaces["lawn"]).area == pytest.approx(100)
    assert all(
        point[2] == pytest.approx(0.08) for point in surfaces["sidewalk"]["vertices"]
    )
    assert result["environment"]["surface_area_m2"] == pytest.approx(
        {"road": 300, "sidewalk": 800, "lawn": 100}
    )
    assert set(result["environment_replaced_ids"]) == {
        "road-a",
        "road-b",
        "walk-a",
        "walk-b",
        "grass",
    }
    assert packet == original
    for key in (
        "scene",
        "meshes",
        "camera",
        "appearance",
        "building_inputs",
        "environment_inputs",
    ):
        assert result[key] == original[key]


def test_unknown_surface_role_keeps_original_road_and_addressed_omission(
    tmp_path: Path,
) -> None:
    packet = packet_for([context("unknown", "road", box(0, 0, 10, 10))])
    packet["environment_inputs"]["omissions"] = {
        "counts": {"unknown_environment_category": 1},
        "feature_ids": {"unknown_environment_category": ["unknown"]},
    }

    result = environment.prepare_environment(packet, tmp_path)

    assert result["environment_surfaces"][0]["kind"] == "road"
    assert mesh_footprint(result["environment_surfaces"][0]).area == 100
    assert (
        result["environment"]["omissions"] == packet["environment_inputs"]["omissions"]
    )
    assert result["environment"]["layer_evidence"] == []


@pytest.mark.parametrize("camera_x", [0, -199])
def test_native_dashed_graphic_is_not_volume_even_without_surface_or_nearby_rim(
    tmp_path: Path, camera_x: float
) -> None:
    features = [
        context(
            f"glyph-{i}",
            "road",
            LineString([(0.2 + i * 1.2, 0), (0.9 + i * 1.2, 0)]),
        )
        for i in range(6)
    ]
    features.append(context("physical-rim", "road", LineString([(0, 10), (12, 10)])))
    packet = packet_for(features)
    roles(packet, {row.feature_id: "curb" for row in features})
    packet["scene"].pop("context_features")  # The real frozen packet contract.
    meshes = {row["id"]: row for row in packet["meshes"]}
    for row in packet["environment_inputs"]["role_evidence"]:
        row.update(
            category="curb",
            source_layer="arbitrary native curb layer",
            source_entity_type="LINE",
            source_closed_path=False,
            source_native_geometry=True,
            source_geometry_provider="autocad_snapshot_v1",
            source_instance_chain=["drawing", "repeated-graphic"],
            source_native_curve_scene_xyz_m=[
                [point[0], point[1], 0.0]
                for point in meshes[row["feature_id"]]["lines"][0]
            ],
        )
        if row["feature_id"] == "physical-rim":
            row["source_instance_chain"] = ["drawing", "physical-path"]
            row["representation"] = "centerline"
    # A simplified display chord must not corrupt the whole native unit proof.
    meshes["glyph-0"]["lines"][0][-1][0] -= 0.1
    packet["camera"]["position"][0] = camera_x
    before = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    assert packet == before
    assert result["meshes"] == before["meshes"]
    assert {row["source_handle"] for row in result["environment_curbs"]} == {
        "physical-rim"
    }
    [graphic] = result["environment"]["curb_graphic_interpretations"]
    assert set(graphic["source_ids"]) == {f"glyph-{i}" for i in range(6)}
    assert len(graphic["visible_source_ids"]) == (6 if camera_x == 0 else 1)
    assert result["environment_surfaces"] == []
    assert result["environment"]["map_buildings"]["status"] == "unaligned"


def test_missing_native_path_is_not_replaced_by_display_mesh_for_symbol_proof(
    tmp_path: Path,
) -> None:
    packet = packet_for(
        [
            context(
                f"glyph-{i}", "road", LineString([(i * 1.2, 0), (i * 1.2 + 0.7, 0)])
            )
            for i in range(6)
        ]
    )
    roles(packet, {row["id"]: "curb" for row in packet["meshes"]})
    for detail in packet["environment_inputs"]["role_evidence"]:
        detail.update(
            category="curb",
            source_layer="native curb",
            source_native_geometry=True,
            source_geometry_provider="autocad_snapshot_v1",
            source_entity_type="LINE",
            source_closed_path=False,
            source_instance_chain=["drawing", "symbol"],
        )
    before = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    assert packet == before
    assert len(result["environment_curbs"]) == 6
    assert result["environment"]["curb_graphic_interpretations"] == []
    assert (
        result["environment"]["curb_composition"]["native_graphic_interpretation"][
            "unresolved_display_counts"
        ]["whole_native_curb_stroke_unavailable"]
        == 6
    )


def test_native_closed_curb_line_keeps_lawn_island_open_in_both_display_outputs(
    tmp_path: Path,
) -> None:
    packet = packet_for(
        [
            context("rim", "road", box(0, 0, 10, 10)),
            context("grass", "lawn", box(0.1, 0.1, 9.9, 9.9)),
            context("walk", "road", box(-5, -5, 15, 15)),
        ]
    )
    roles(packet, {"rim": "curb", "grass": "lawn", "walk": "sidewalk"})
    detail = next(
        row
        for row in packet["environment_inputs"]["role_evidence"]
        if row["feature_id"] == "rim"
    )
    detail.update(
        source_entity_type="LWPOLYLINE",
        source_closed_path=True,
        source_polygon_projection=True,
        source_native_area=None,
    )
    # Application._packet omits context_features in the actual frozen packet.
    packet["scene"].pop("context_features")
    original = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    surfaces = {row["kind"]: row for row in result["environment_surfaces"]}
    assert "curb" not in surfaces
    assert (
        mesh_footprint(surfaces["lawn"])
        .symmetric_difference(box(0.1, 0.1, 9.9, 9.9))
        .area
        < 1e-9
    )
    assert not mesh_footprint(surfaces["sidewalk"]).covers(Point(5, 5))
    top = union_all(
        [
            Polygon([row["vertices"][i][:2] for i in face])
            for row in result["environment_curb_meshes"]
            for face in row["faces"]
            if len(face) == 3
            and all(row["vertices"][i][2] == pytest.approx(0.18) for i in face)
        ]
    )
    assert not top.intersects(box(0.2, 0.2, 9.8, 9.8))
    assert top.covers(Point(5, 0))
    assert (
        result["environment"]["curb_composition"]["projected_closed_line_perimeters"][
            0
        ]["source_handle"]
        == "rim"
    )
    assert packet == original
    for key in ("scene", "meshes", "environment_inputs"):
        assert result[key] == original[key]


@pytest.mark.parametrize(
    "footprint",
    [
        box(0, 0, 8, 0.15),
        Polygon(
            [(0, 0), (4, 0), (4, 4), (0, 4)],
            [[(0.15, 0.15), (0.15, 3.85), (3.85, 3.85), (3.85, 0.15)]],
        ),
    ],
)
def test_actual_frozen_packet_retains_narrow_and_holed_curb_footprints(
    tmp_path: Path, footprint: Polygon
) -> None:
    packet = packet_for([context("curb", "road", footprint)])
    roles(packet, {"curb": "curb"})
    packet["environment_inputs"]["role_evidence"][0].update(
        source_entity_type="LWPOLYLINE",
        source_closed_path=True,
        source_polygon_projection=True,
        source_native_area=None,
    )
    packet["scene"].pop("context_features")
    original = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    assert result["environment_curbs"] == []
    assert result["environment_curb_meshes"] == []
    assert (
        mesh_footprint(result["environment_surfaces"][0])
        .symmetric_difference(footprint)
        .area
        < 1e-9
    )
    assert packet == original


@pytest.mark.parametrize("other_base", [150, 151])
def test_lawn_excludes_hard_cover_only_at_its_original_cad_base(
    tmp_path: Path,
    other_base: float,
) -> None:
    packet = packet_for(
        [
            context("grass", "lawn", box(0, 0, 10, 10), base_elevation_m=150),
            context("road", "road", box(-2, -2, 12, 12), base_elevation_m=other_base),
            context("walk", "road", box(-1, -1, 11, 11), base_elevation_m=other_base),
        ]
    )
    roles(packet, {"grass": "lawn", "road": "road", "walk": "sidewalk"})
    original = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    surfaces = {row["kind"]: row for row in result["environment_surfaces"]}
    assert mesh_footprint(surfaces["lawn"]).area == 100
    for kind, expected in (("road", 196), ("sidewalk", 144)):
        footprint = mesh_footprint(surfaces[kind])
        assert footprint.area == pytest.approx(
            expected - (100 if other_base == 150 else 0)
        )
        assert footprint.covers(Point(5, 5)) == (other_base != 150)
    assert packet == original


def test_curbs_keep_exact_xy_source_base_and_explicit_culling_without_duplicates(
    tmp_path: Path,
) -> None:
    packet = packet_for(
        [
            context(
                "curb-a", "road", LineString([(0, 0), (10, 0)]), base_elevation_m=150
            ),
            context(
                "curb-b", "road", LineString([(10, 0), (0, 0)]), base_elevation_m=150
            ),
            context(
                "curb-far",
                "road",
                LineString([(1000, 0), (1010, 0)]),
                base_elevation_m=151,
            ),
            context("curb-outline", "road", box(20, 0, 30, 1)),
        ]
    )
    roles(packet, {row["id"]: "curb" for row in packet["meshes"]})
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    assert len(result["environment_curbs"]) == 1
    row = result["environment_curbs"][0]
    assert row["start_scene_xyz_m"] == [0, 0, 150]
    assert row["end_scene_xyz_m"] == [10, 0, 150]
    assert row["width_m"] == 0.15 and row["height_m"] == 0.18
    assert result["environment"]["view_culling"]["curb_segments_outside_view"] == 1
    assert result["environment"]["curb_proxy_dimensions_m"]["status"] == "illustrative"
    surface = result["environment_surfaces"][0]
    assert surface["kind"] == "curb"
    assert mesh_footprint(surface).symmetric_difference(box(20, 0, 30, 1)).area < 1e-8
    assert all(point[2] == pytest.approx(0.18) for point in surface["vertices"])
    assert packet == original


def test_covered_short_curb_glyph_has_explicit_alias_without_changing_source(
    tmp_path: Path,
) -> None:
    packet = packet_for(
        [
            context(
                "whole-curb",
                "road",
                LineString([(0, 0), (17, 0)]),
                base_elevation_m=150,
            ),
            context(
                "short-glyph",
                "road",
                LineString([(3, 0.01), (3.3, 0.01), (3.708, 0.01)]),
                base_elevation_m=150,
            ),
        ]
    )
    roles(packet, {"whole-curb": "curb", "short-glyph": "curb"})
    packet["scene"]["data_gaps"] = ["Missing XREF retained in source"]
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    assert [row["source_handle"] for row in result["environment_curbs"]] == [
        "whole-curb"
    ]
    assert result["environment_curbs"][0]["start_scene_xyz_m"] == [0, 0, 150]
    assert result["environment_curbs"][0]["end_scene_xyz_m"] == [17, 0, 150]
    alias = result["environment"]["curb_display_aliases"][0]
    assert alias["source_handle"] == "short-glyph"
    assert alias["retained_source_handle"] == "whole-curb"
    assert alias["status"] == "appearance_only_source_geometry_preserved"
    assert alias["source_length_m"] == pytest.approx(0.708)
    assert alias["centerline_tolerance_m"] == pytest.approx(0.0375)
    assert result["environment"]["curb_segments_before_display_priority"] == 3
    assert result["environment"]["curb_segments"] == 1
    assert packet == original
    assert result["meshes"] == original["meshes"]
    assert result["scene"] == original["scene"]
    assert result["environment_inputs"] == original["environment_inputs"]


@pytest.mark.parametrize(
    ("main_points", "short_points", "short_base"),
    [
        ([(0, 0), (17, 0)], [(16.8, 0.01), (17.5, 0.01)], 150),
        ([(0, 0), (17, 0)], [(3, 0.01), (3.708, 0.01)], 151),
        ([(0, 0), (17, 0)], [(3, 0.05), (3.708, 0.05)], 150),
        ([(0, 0), (0.708, 0)], [(0, 0.01), (0.708, 0.01)], 150),
        ([(0, 0), (17, 0)], [(3, 0.01), (5, 0.01)], 150),
    ],
    ids=[
        "partial-overlap",
        "different-elevation",
        "outside-width-tolerance",
        "equal-length",
        "whole-short-feature-over-one-metre",
    ],
)
def test_distinct_curb_chains_keep_exact_source_geometry(
    tmp_path: Path,
    main_points: list[tuple[float, float]],
    short_points: list[tuple[float, float]],
    short_base: float,
) -> None:
    packet = packet_for(
        [
            context("main", "road", LineString(main_points), base_elevation_m=150),
            context(
                "other", "road", LineString(short_points), base_elevation_m=short_base
            ),
        ]
    )
    roles(packet, {"main": "curb", "other": "curb"})
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    assert result["environment"]["curb_display_aliases"] == []
    assert {row["source_handle"] for row in result["environment_curbs"]} == {
        "main",
        "other",
    }
    assert result["environment"]["curb_segments_before_display_priority"] == 2
    assert result["environment"]["curb_segments"] == 2
    assert packet == original and result["meshes"] == original["meshes"]


def test_curb_display_priority_preserves_sloping_source_chain_and_other_dimensions() -> (
    None
):
    def curb(
        identifier: str, start: list[float], end: list[float], **extra: Any
    ) -> dict[str, Any]:
        return {
            "source_handle": identifier,
            "start_scene_xyz_m": start,
            "end_scene_xyz_m": end,
            "width_m": 0.15,
            "height_m": 0.18,
            "curb_class": "source-curb",
            **extra,
        }

    rows = [
        curb("main", [0, 0, 150], [17, 0, 150]),
        curb("sloping", [3, 0.01, 150], [3.708, 0.01, 150.2]),
        curb("wider", [4, 0.01, 150], [4.708, 0.01, 150], width_m=0.3),
        curb("taller", [5, 0.01, 150], [5.708, 0.01, 150], height_m=0.3),
        curb(
            "another-class",
            [6, 0.01, 150],
            [6.708, 0.01, 150],
            curb_class="different-curb",
        ),
    ]
    original = deepcopy(rows)

    retained, aliases = environment._curb_aliases(rows)

    assert retained == original and aliases == []
    assert rows == original


def test_coplanar_polygon_curb_and_line_curb_share_one_visible_top(
    tmp_path: Path,
) -> None:
    packet = packet_for(
        [
            context("polygon-curb", "road", box(0, -0.1, 10, 0.1)),
            context("line-curb", "road", LineString([(0, 0), (10, 0)])),
            context("crossing-curb", "road", LineString([(5, -2), (5, 2)])),
        ]
    )
    roles(packet, {row["id"]: "curb" for row in packet["meshes"]})
    original = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    meshes = result["environment_curb_meshes"]
    assert len(meshes) == 1
    curb = meshes[0]
    top = union_all(
        [
            Polygon([curb["vertices"][i][:2] for i in face])
            for face in curb["faces"]
            if len(face) == 3
            and all(curb["vertices"][i][2] == pytest.approx(0.18) for i in face)
        ]
    )
    visible_surface = union_all(
        [
            mesh_footprint(row)
            for row in result["environment_surfaces"]
            if row["kind"] == "curb"
        ]
    )
    assert top.intersection(visible_surface).area < 1e-9
    assert (
        result["environment"]["curb_composition"]["removed_coplanar_polygon_area_m2"]
        > 1
    )
    assert len(result["environment_curbs"]) == 2
    assert packet == original


@pytest.mark.parametrize(
    "outside_length",
    [9.7, 0.4],
    ids=["whole-feature-ten-metres", "whole-short-feature-partly-uncovered"],
)
def test_curbs_use_complete_source_geometry_before_view_culling_for_priority(
    tmp_path: Path, outside_length: float
) -> None:
    packet = packet_for(
        [
            context(
                "retained-main",
                "road",
                LineString([(0, 0), (17, 0)]),
                base_elevation_m=150,
            ),
            context(
                "disconnected-source",
                "road",
                MultiLineString(
                    [
                        [(3, 0.01), (3.3, 0.01)],
                        [(500, 10), (500 + outside_length, 10)],
                    ]
                ),
                base_elevation_m=150,
            ),
        ]
    )
    roles(packet, {"retained-main": "curb", "disconnected-source": "curb"})
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    assert result["environment"]["view_culling"]["curb_segments_outside_view"] == 1
    assert result["environment"]["curb_display_aliases"] == []
    visible = {row["source_handle"]: row for row in result["environment_curbs"]}
    assert set(visible) == {"retained-main", "disconnected-source"}
    assert visible["disconnected-source"]["start_scene_xyz_m"] == [3, 0.01, 150]
    assert visible["disconnected-source"]["end_scene_xyz_m"] == [3.3, 0.01, 150]
    assert result["environment"]["curb_segments_before_display_priority"] == 2
    assert result["environment"]["curb_segments"] == 2
    assert packet == original and result["meshes"] == original["meshes"]


def test_marker_culling_does_not_change_tree_input_or_claim_measured_size(
    tmp_path: Path,
) -> None:
    packet = packet_for([])
    markers = [
        {
            "object_id": identifier,
            "local_x": x,
            "local_y": 0,
            "radius_m": 3.5,
            "height_m": 12,
            "evidence": {
                "height_status": "estimated",
                "species_status": "unknown",
                "size_source": "illustrative_render_proxy",
            },
        }
        for identifier, x in [("near", 10), ("far", 500)]
    ]
    packet["environment_inputs"]["existing_tree_markers"] = markers
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    assert [r["object_id"] for r in result["environment_trees"]] == ["near"]
    assert result["environment_trees"][0]["evidence"]["height_status"] == "estimated"
    assert result["environment"]["view_culling"]["tree_markers_outside_view"] == 1
    assert (
        packet == original
        and result["scene"]["objects"] == original["scene"]["objects"]
    )


def test_unknown_frame_never_queries_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = packet_for([context("cad", "building", box(0, 0, 10, 10))])

    def no_network(*args: Any) -> Any:
        pytest.fail("An unknown CAD location cannot authorize a map request")

    monkeypatch.setattr(environment, "fetch_buildings", no_network)
    result = environment.prepare_environment(packet, tmp_path)
    assert result["environment_buildings"] == []
    assert result["environment"]["map_buildings"]["status"] == "unaligned"


def test_aligned_neighbours_are_whole_estimated_houses_outside_cad_with_holes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = context(
        "cad",
        "building",
        box(0, 0, 10, 10),
        height_m=18,
        height_status="confirmed",
        height_source="dxf_attribute",
    )
    packet = packet_for(
        [native, context("road", "road", box(20, 0, 30, 10))], aligned=True
    )
    frame = frame_for(packet, tmp_path)
    neighbour = Polygon(
        [(40, 0), (70, 0), (70, 30), (40, 30)],
        [[(50, 10), (50, 20), (60, 20), (60, 10)]],
    )
    monkeypatch.setattr(
        environment,
        "fetch_buildings",
        lambda *_: map_result(
            [
                map_feature(frame, "relation/whole", neighbour, height="24"),
                map_feature(frame, "way/on-cad", box(5, 0, 15, 10), height="90"),
                map_feature(frame, "way/on-road", box(25, 0, 35, 10), height="30"),
                map_feature(
                    frame,
                    "way/part",
                    box(90, 0, 100, 10),
                    **{"building:part": "yes", "height": "100"},
                ),
            ]
        ),
    )
    original = deepcopy(packet)

    result = environment.prepare_environment(packet, tmp_path)

    evidence = result["environment"]["map_buildings"]
    assert (
        evidence["status"] == "loaded"
        and evidence["provenance"]["source_sha256"] == "external-identity"
    )
    assert [r["osm_id"] for r in evidence["features"]] == ["relation/whole"]
    assert evidence["features"][0]["height_m"] == 24
    assert evidence["features"][0]["height_status"] == "estimated"
    assert evidence["features"][0]["height_source"] == "osm_height"
    assert {r["id"] for r in evidence["excluded"]} == {"way/on-cad", "way/on-road"}
    mesh = result["environment_buildings"][0]
    assert mesh["height_m"] == 24 and len(mesh["footprint_rings"]) == 2
    assert mesh_footprint(mesh).symmetric_difference(neighbour).area < 1e-6
    assert (
        shape(evidence["features"][0]["geometry"]).symmetric_difference(neighbour).area
        < 1e-6
    )
    assert packet == original and result["building_inputs"][0]["height_m"] == 18


def test_duplicate_external_footprint_does_not_make_duplicate_render_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = packet_for([], aligned=True)
    frame = frame_for(packet, tmp_path)
    neighbour = box(40, 0, 50, 10)
    monkeypatch.setattr(
        environment,
        "fetch_buildings",
        lambda *_: map_result(
            [
                map_feature(
                    frame, "relation/whole", neighbour, **{"building:levels": "4"}
                ),
                map_feature(
                    frame, "way/duplicate", neighbour, **{"building:levels": "4"}
                ),
            ]
        ),
    )

    result = environment.prepare_environment(packet, tmp_path)

    assert len(result["environment_buildings"]) == 1
    assert len(result["environment"]["map_buildings"]["features"]) == 1
    assert result["environment_buildings"][0]["height_m"] == 12


def test_overlapping_map_parents_remain_ambiguous_instead_of_partial_houses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = packet_for([], aligned=True)
    frame = frame_for(packet, tmp_path)
    monkeypatch.setattr(
        environment,
        "fetch_buildings",
        lambda *_: map_result(
            [
                map_feature(frame, "way/a", box(40, 0, 50, 10), height="12"),
                map_feature(frame, "way/b", box(45, 0, 55, 10), height="15"),
            ]
        ),
    )

    result = environment.prepare_environment(packet, tmp_path)

    assert result["environment_buildings"] == []
    excluded = result["environment"]["map_buildings"]["excluded"]
    assert {row["id"] for row in excluded} == {"way/a", "way/b"}
    assert {row["reason"] for row in excluded} == {"ambiguous_map_overlap"}


def test_map_object_crossing_transform_extent_is_rejected_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = packet_for([], aligned=True)
    frame = frame_for(packet, tmp_path)
    # Build WGS84 data in the unrestricted frame first, then apply its explicit
    # CAD scope. Cutting the footprint at this boundary would invent a house.
    data = map_result(
        [
            map_feature(frame, "way/in", box(10, 10, 20, 20), height="9"),
            map_feature(frame, "way/crosses", box(45, 10, 55, 20), height="9"),
        ]
    )
    bounded = replace(frame, valid_extent_cad_xy=(50, 150, 150, 250))
    monkeypatch.setattr(environment, "render_frame", lambda *_: bounded)
    monkeypatch.setattr(environment, "fetch_buildings", lambda *_: data)

    result = environment.prepare_environment(packet, tmp_path)

    evidence = result["environment"]["map_buildings"]
    assert evidence["outside_transform_extent"] == ["way/crosses"]
    assert [row["osm_id"] for row in evidence["features"]] == ["way/in"]
    assert len(result["environment_buildings"]) == 1


def test_optional_map_failure_keeps_cad_environment_and_addressed_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = packet_for([context("road", "road", box(0, 0, 10, 10))], aligned=True)

    def unavailable(*args: Any) -> Any:
        raise OsmEvidenceError("OSM connection unavailable")

    monkeypatch.setattr(environment, "fetch_buildings", unavailable)
    original = deepcopy(packet)
    result = environment.prepare_environment(packet, tmp_path)
    assert result["environment"]["map_buildings"]["status"] == "unavailable"
    assert "connection unavailable" in result["environment"]["map_buildings"]["reason"]
    assert len(result["environment_surfaces"]) == 1
    assert result["environment_buildings"] == []
    assert packet == original
