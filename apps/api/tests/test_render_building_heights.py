"""Render height evidence changes appearance without editing the accepted scene."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import transform

from app.geometry.contracts import CoordinateReference
from app.rendering import building_heights
from app.rendering.georeference import RenderFrame, render_frame
from app.rendering.osm import OsmEvidenceError
from app.rendering.packet import display_meshes, flat_building_surfaces
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def feature(
    identifier: str,
    geometry: Polygon | MultiPolygon | LineString,
    **attributes: Any,
) -> SceneContextFeature:
    return SceneContextFeature(
        feature_id=identifier,
        kind="building",
        geometry=mapping(geometry),
        **attributes,
    )


def scene_packet(
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
            evidence="test explicit local frame",
        )
        if aligned
        else CoordinateReference()
    )
    snapshot = SceneSnapshot(
        project_id="arbitrary-scene",
        geometry_version=7,
        source_sha256="source-identity",
        content_sha256="accepted-scene-identity",
        plan_version=3,
        horizon_year=0,
        coordinate_origin=[100, 200],
        coordinate_reference=reference,
        note="immutable original scene",
        context_features=features,
    )
    meshes = display_meshes(snapshot)
    return {
        "scene": snapshot.model_dump(mode="json"),
        "building_inputs": building_heights.building_inputs(snapshot),
        "meshes": meshes,
        "flat_building_surfaces": flat_building_surfaces(meshes),
        "camera": {"position": [1, 2, 3]},
        "appearance": {"profile": "native_v1"},
    }


def real_frame(packet: dict[str, Any], directory: Path) -> RenderFrame:
    frame = render_frame(SceneSnapshot.model_validate(packet["scene"]), directory)
    assert frame is not None
    return frame


def osm_feature(
    frame: RenderFrame,
    identifier: str,
    local_geometry: Polygon | MultiPolygon,
    **tags: Any,
) -> dict[str, Any]:
    def local_to_wgs84(x: float, y: float, z: float | None = None) -> tuple[float, ...]:
        return frame.cad_to_wgs84(
            x + frame.coordinate_origin[0], y + frame.coordinate_origin[1]
        )

    return {
        "type": "Feature",
        "id": identifier,
        "geometry": mapping(transform(local_to_wgs84, local_geometry)),
        "properties": {"tags": {"building": "yes", **tags}},
    }


def external_result(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "features": features,
        "provenance": {"source_sha256": "external-source", "attribution": "OSM"},
        "rejected": [],
    }


def projected_roof_area(mesh: dict[str, Any]) -> float:
    return sum(
        Polygon([mesh["vertices"][index][:2] for index in face]).area
        for face in mesh["faces"]
        if len(face) == 3
    )


def test_unknown_georeference_never_queries_network_and_labels_visual_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = scene_packet([feature("cad-house", box(0, 0, 10, 10))])
    original = deepcopy(packet)

    def no_network(*args: Any) -> Any:
        pytest.fail("Unknown CAD location must not issue an OSM request")

    monkeypatch.setattr(building_heights, "fetch_buildings", no_network)
    result = building_heights.prepare_buildings(packet, tmp_path)
    evidence = result["building_heights"]
    assert evidence["osm"]["status"] == "unaligned"
    assert evidence["features"][0]["height_source"] == "render_class_proxy"
    assert evidence["features"][0]["height_status"] == "estimated"
    assert evidence["features"][0]["height_m"] == 6
    assert result["building_display_meshes"][0]["height_m"] == 6
    assert packet == original
    assert result["scene"] == original["scene"]
    assert result["meshes"] == original["meshes"]


def test_native_height_has_priority_over_matching_osm_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polygon = box(0, 0, 10, 10)
    packet = scene_packet(
        [
            feature(
                "native-height",
                polygon,
                height_m=18,
                height_status="confirmed",
                height_source="dxf_attribute",
                base_elevation_m=150,
            )
        ],
        aligned=True,
    )
    frame = real_frame(packet, tmp_path)
    monkeypatch.setattr(
        building_heights,
        "fetch_buildings",
        lambda *_: external_result([osm_feature(frame, "way/9", polygon, height="42")]),
    )
    original = deepcopy(packet)
    result = building_heights.prepare_buildings(packet, tmp_path)
    record = result["building_heights"]["features"][0]
    assert record["match_status"] == "source_height"
    assert record["height_m"] == 18 and record["height_source"] == "dxf_attribute"
    assert record["height_status"] == "confirmed"
    mesh = result["building_display_meshes"][0]
    assert mesh["height_m"] == 18
    assert min(point[2] for point in mesh["vertices"]) == 150
    assert max(point[2] for point in mesh["vertices"]) == 168
    assert packet == original


@pytest.mark.parametrize(
    ("tags", "height", "source"),
    [
        ({"height": "24"}, 24, "osm_height"),
        ({"building:levels": "5"}, 15, "osm_levels"),
    ],
)
def test_osm_evidence_extrudes_existing_cad_without_replacing_xy_or_holes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tags: dict[str, str],
    height: float,
    source: str,
) -> None:
    courtyard = Polygon(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        [[(4, 4), (4, 16), (16, 16), (16, 4)]],
    )
    packet = scene_packet([feature("cad-courtyard", courtyard)], aligned=True)
    frame = real_frame(packet, tmp_path)
    requested = []

    def fetch(bbox: tuple[float, ...], directory: Path) -> dict[str, Any]:
        requested.append((bbox, directory))
        return external_result([osm_feature(frame, "relation/17", courtyard, **tags)])

    monkeypatch.setattr(building_heights, "fetch_buildings", fetch)
    original = deepcopy(packet)
    result = building_heights.prepare_buildings(packet, tmp_path)
    evidence = result["building_heights"]
    assert evidence["osm"]["status"] == "loaded"
    assert evidence["osm"]["provenance"]["source_sha256"] == "external-source"
    record = evidence["features"][0]
    assert record["feature_id"] == "cad-courtyard" and record["osm_id"] == "relation/17"
    assert record["height_m"] == height and record["height_source"] == source
    assert record["height_status"] == "estimated"
    mesh = result["building_display_meshes"][0]
    assert len(mesh["footprint_rings"]) == 2
    assert projected_roof_area(mesh) == pytest.approx(courtyard.area)
    assert shape(result["scene"]["context_features"][0]["geometry"]).equals_exact(
        courtyard, 0
    )
    assert packet == original
    for key in (
        "scene",
        "building_inputs",
        "meshes",
        "flat_building_surfaces",
        "camera",
        "appearance",
    ):
        assert result[key] == original[key]
    assert len(requested) == 1 and requested[0][1] == tmp_path / "osm-cache"
    assert requested[0][0][0] == pytest.approx(frame.cad_to_wgs84(100, 200)[0])
    assert requested[0][0][1] == pytest.approx(frame.cad_to_wgs84(100, 200)[1])


def test_matching_osm_without_height_uses_labelled_class_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polygon = box(0, 0, 10, 10)
    packet = scene_packet([feature("cad-shop", polygon)], aligned=True)
    frame = real_frame(packet, tmp_path)
    monkeypatch.setattr(
        building_heights,
        "fetch_buildings",
        lambda *_: external_result(
            [osm_feature(frame, "way/3", polygon, building="retail")]
        ),
    )
    result = building_heights.prepare_buildings(packet, tmp_path)
    record = result["building_heights"]["features"][0]
    assert record["match_status"] == "matched" and record["osm_id"] == "way/3"
    assert record["building_class"] == "retail"
    assert record["height_m"] == 9 and record["height_status"] == "estimated"
    assert record["height_source"] == "render_class_proxy"
    assert result["building_display_meshes"][0]["building_class"] == "retail"


def test_duplicate_cad_footprints_share_match_but_only_one_display_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polygon = box(0, 0, 10, 10)
    packet = scene_packet(
        [feature("cad-outer", polygon), feature("cad-hatch-outline", polygon)],
        aligned=True,
    )
    frame = real_frame(packet, tmp_path)
    monkeypatch.setattr(
        building_heights,
        "fetch_buildings",
        lambda *_: external_result([osm_feature(frame, "way/1", polygon, height="12")]),
    )
    original = deepcopy(packet)
    result = building_heights.prepare_buildings(packet, tmp_path)
    evidence = result["building_heights"]
    assert {record["feature_id"] for record in evidence["features"]} == {
        "cad-outer",
        "cad-hatch-outline",
    }
    assert {record["osm_id"] for record in evidence["features"]} == {"way/1"}
    assert evidence["coalesced_display_envelopes"] == 1
    assert len(result["building_display_meshes"]) == 1
    assert projected_roof_area(result["building_display_meshes"][0]) == 100
    assert packet == original


def test_tiny_hatch_dots_lines_and_narrow_facade_fragments_are_not_extruded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = scene_packet(
        [
            feature("house", box(0, 0, 10, 10)),
            feature("hatch-dot", box(20, 0, 21, 1)),
            feature("facade-line", LineString([(30, 0), (40, 0)])),
            feature("narrow-fragment", box(50, 0, 51, 20)),
        ]
    )
    original = deepcopy(packet)
    monkeypatch.setattr(
        building_heights, "fetch_buildings", lambda *_: pytest.fail("unknown frame")
    )
    result = building_heights.prepare_buildings(packet, tmp_path)
    evidence = result["building_heights"]
    assert evidence["ignored_detail_ids"] == [
        "hatch-dot",
        "facade-line",
        "narrow-fragment",
    ]
    assert [record["feature_id"] for record in evidence["features"]] == ["house"]
    assert len(result["building_display_meshes"]) == 1
    assert packet == original


def test_small_contour_with_explicit_source_height_retains_its_measurement(
    tmp_path: Path,
) -> None:
    packet = scene_packet(
        [
            feature(
                "measured-kiosk",
                box(0, 0, 2, 2),
                height_m=3,
                height_status="confirmed",
                height_source="dxf_extrusion",
            )
        ]
    )
    result = building_heights.prepare_buildings(packet, tmp_path)
    assert result["building_heights"]["ignored_detail_ids"] == []
    assert result["building_heights"]["features"][0]["height_m"] == 3
    assert projected_roof_area(result["building_display_meshes"][0]) == 4


def test_fetch_failure_preserves_scene_and_explicitly_labels_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = scene_packet([feature("cad-house", box(0, 0, 10, 10))], aligned=True)
    original = deepcopy(packet)

    def unavailable(*args: Any) -> Any:
        raise OsmEvidenceError("Overpass unavailable")

    monkeypatch.setattr(building_heights, "fetch_buildings", unavailable)
    result = building_heights.prepare_buildings(packet, tmp_path)
    evidence = result["building_heights"]
    assert evidence["osm"]["status"] == "unavailable"
    assert evidence["osm"]["reason"] == "Overpass unavailable"
    assert evidence["features"][0]["height_m"] == 6
    assert evidence["features"][0]["height_source"] == "render_class_proxy"
    assert evidence["features"][0]["height_status"] == "estimated"
    assert evidence["features"][0]["reason"] == "Overpass unavailable"
    assert packet == original


def test_ambiguous_split_buildings_do_not_borrow_either_osm_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = scene_packet([feature("cad-whole", box(0, 0, 10, 10))], aligned=True)
    frame = real_frame(packet, tmp_path)
    monkeypatch.setattr(
        building_heights,
        "fetch_buildings",
        lambda *_: external_result(
            [
                osm_feature(frame, "way/left", box(0, 0, 5, 10), height="12"),
                osm_feature(frame, "way/right", box(5, 0, 10, 10), height="30"),
            ]
        ),
    )
    result = building_heights.prepare_buildings(packet, tmp_path)
    record = result["building_heights"]["features"][0]
    assert record["match_status"] == "ambiguous" and record["osm_id"] is None
    assert record["height_m"] == 6 and record["height_source"] == "render_class_proxy"
    assert {row["osm_id"] for row in record["candidates"]} == {"way/left", "way/right"}


def test_osm_250_m_height_matches_receipt_evidence_and_uncapped_display_roof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polygon = box(0, 0, 10, 10)
    base = 150.0
    packet = scene_packet(
        [feature("cad-tower", polygon, base_elevation_m=base)], aligned=True
    )
    frame = real_frame(packet, tmp_path)
    monkeypatch.setattr(
        building_heights,
        "fetch_buildings",
        lambda *_: external_result(
            [osm_feature(frame, "way/250", polygon, height="250")]
        ),
    )
    original = deepcopy(packet)
    result = building_heights.prepare_buildings(packet, tmp_path)
    # The renderer includes this evidence object verbatim in its receipt.
    receipt_evidence = result["building_heights"]
    record = receipt_evidence["features"][0]
    assert record["osm_id"] == "way/250"
    assert record["height_m"] == 250
    assert record["height_source"] == "osm_height"
    assert record["height_status"] == "estimated"
    mesh = result["building_display_meshes"][0]
    assert mesh["height_m"] == record["height_m"]
    assert min(point[2] for point in mesh["vertices"]) == base
    roof_vertices = [
        mesh["vertices"][index]
        for face in mesh["faces"]
        if len(face) == 3
        for index in face
    ]
    assert roof_vertices
    assert {point[2] for point in roof_vertices} == {base + 250}
    assert projected_roof_area(mesh) == polygon.area
    assert packet == original
