import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter
from typing import Any

import ezdxf
import pytest
from legacy_geometry_fixture import calculate_confirmed_fixture
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape

import app.dxf_import.legacy.entities.block_diagnostics as block_diagnostics
import app.dxf_import.legacy.reader as dxf_adapters
from app.contracts import GeometrySnapshot, LayerKind, Plan, PlanObject, Project
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult, SourceFile
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.legacy.reader import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import DxfVerticalPrimitive
from app.geometry.ports import PositionRejected
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.operations.progress import OperationCancelled, WorkProgress
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason


def source_extent(imported: DxfImportResult) -> tuple[float, float, float, float]:
    bounds = imported.bounds or [0, 0, 1, 1]
    return bounds[0], bounds[1], bounds[2], bounds[3]


SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"
LARGE_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "vdnkh-large.dxf"
VDNKH_3D_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "vdnkh-3d.dxf"
KITAY_GOROD_DXF = (
    Path(__file__).parents[3]
    / "fixtures"
    / "large-map"
    / "kitay-gorod"
    / "kitay-gorod-large.dxf"
)
KITAY_GOROD_3D_DXF = (
    Path(__file__).parents[3]
    / "fixtures"
    / "large-map"
    / "kitay-gorod"
    / "kitay-gorod-3d.dxf"
)


def imported_project(source: bytes | None = None) -> Project:
    imported = EzdxfReader().read("site.dxf", source or SITE_DXF.read_bytes())
    return Project(
        name="DXF", layers=imported.layers, source_geometry=imported.geometry
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("encoding", ["utf-8", "cp1251"])
def test_text_dxf_line_endings_preserve_source_geometry_and_text_on_round_trip(
    newline: str, encoding: str
) -> None:
    document = ezdxf.new("R2013" if encoding == "utf-8" else "R2000")
    document.units = ezdxf.units.M
    document.encoding = encoding
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("ANNOTATION", color=7)
    document.modelspace().add_lwpolyline(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    document.modelspace().add_text(
        "Главная аллея", dxfattribs={"layer": "ANNOTATION", "insert": (5, 5)}
    )
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().replace("\n", newline).encode(encoding)

    # The HTTP upload path supplies a mutable buffer with its original line endings.
    imported = EzdxfReader().read("windows-site.dxf", bytearray(source))
    assert imported.entity_count == 2
    assert imported.bounds == [0.0, 0.0, 20.0, 20.0]
    assert (
        next(
            layer for layer in imported.layers if layer.source_name == "SITE_BORDER"
        ).suggested_kind
        == LayerKind.SITE_BORDER
    )
    assert any(
        feature["properties"].get("source_text") == "Главная аллея"
        for feature in imported.geometry.feature_collection["features"]
    )

    project = Project(
        name="Windows DXF",
        plan=Plan(objects=[PlanObject(kind="tree", x=10, y=10, radius=1.6)]),
    )
    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode(encoding), newline=None))
    assert len(reopened.modelspace().query('LWPOLYLINE[layer=="SITE_BORDER"]')) == 1
    assert (
        reopened.modelspace().query('TEXT[layer=="ANNOTATION"]')[0].dxf.text
        == "Главная аллея"
    )
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')) == 1
    assert len(reopened.modelspace()) == 3


def test_reader_accepts_binary_dxf_and_keeps_boundary_kind() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.modelspace().add_lwpolyline(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    stream = BytesIO()
    document.write(stream, fmt="bin")

    # The HTTP collector hands the parser one mutable buffer. Binary DXF must
    # retain the same no-text-decode path as an ordinary immutable upload.
    imported = EzdxfReader().read("binary-site.dxf", bytearray(stream.getvalue()))

    assert imported.entity_count == 1
    assert imported.bounds == [0.0, 0.0, 20.0, 20.0]
    assert imported.layers[0].suggested_kind == LayerKind.SITE_BORDER


def _dxf_with_custom_georeference(properties: dict[str, str]) -> bytes:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.modelspace().add_line((0, 0), (1, 1))
    for tag, value in properties.items():
        document.header.custom_vars.append(tag, value)
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode()


def test_reader_accepts_complete_local_wgs84_declaration_without_claiming_epsg() -> (
    None
):
    imported = EzdxfReader().read(
        "local.dxf",
        _dxf_with_custom_georeference(
            {
                "GREEN_ATLAS_HORIZONTAL_SOURCE": "OpenStreetMap snapshot: fixture.osm.json",
                "GREEN_ATLAS_ORIGIN_WGS84": "55.7522500,37.6235000",
                "GREEN_ATLAS_LOCAL_PROJECTION": "local_equirectangular_wgs84",
                "GREEN_ATLAS_EARTH_RADIUS_M": "6378137.000",
            }
        ),
    )

    reference = imported.coordinate_reference
    assert reference.status == "declared"
    assert reference.source == "dxf_custom_georeference"
    assert reference.crs_id is None
    assert reference.name == "Локальная эквиректангулярная аппроксимация WGS84"
    assert reference.axis_order == "xy"
    assert reference.origin_wgs84 == [55.75225, 37.6235]
    assert reference.local_projection == "local_equirectangular_wgs84"
    assert reference.earth_radius_m == 6_378_137
    assert reference.horizontal_source == "OpenStreetMap snapshot: fixture.osm.json"


def test_reader_rejects_incomplete_local_wgs84_declaration_atomically() -> None:
    imported = EzdxfReader().read(
        "incomplete-local.dxf",
        _dxf_with_custom_georeference(
            {
                "GREEN_ATLAS_HORIZONTAL_SOURCE": "OpenStreetMap snapshot: fixture.osm.json",
                "GREEN_ATLAS_ORIGIN_WGS84": "55.7522500,37.6235000",
            }
        ),
    )

    reference = imported.coordinate_reference
    assert reference.status == "unknown"
    assert reference.source == "none"
    assert reference.origin_wgs84 is None
    assert "неполна" in reference.evidence


def test_reader_rejects_malformed_local_wgs84_declaration_atomically() -> None:
    imported = EzdxfReader().read(
        "broken-local.dxf",
        _dxf_with_custom_georeference(
            {
                "GREEN_ATLAS_HORIZONTAL_SOURCE": "OpenStreetMap snapshot: fixture.osm.json",
                "GREEN_ATLAS_ORIGIN_WGS84": "north,far-away",
                "GREEN_ATLAS_LOCAL_PROJECTION": "EPSG:3857",
                "GREEN_ATLAS_EARTH_RADIUS_M": "not-a-radius",
            }
        ),
    )

    reference = imported.coordinate_reference
    assert reference.status == "unknown"
    assert reference.source == "none"
    assert reference.origin_wgs84 is None
    assert reference.crs_id is None
    assert "повреждена" in reference.evidence


def test_enriched_source_layers_are_not_mistaken_for_exported_plan_layers() -> None:
    imported = EzdxfReader().read("kitay-gorod-3d.dxf", KITAY_GOROD_3D_DXF.read_bytes())
    layer_names = {layer.source_name for layer in imported.layers}

    assert "GREEN_ATLAS_TERRAIN_COP90" in layer_names
    assert "GREEN_ATLAS_BUILDING_OSM_LEVELS" in layer_names
    assert not ({"GREEN_ATLAS_TREES", "GREEN_ATLAS_SHRUBS"} & layer_names)


def test_round_trip_keeps_a_binary_dxf_binary() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SOURCE_CONTEXT", color=3)
    document.modelspace().add_line(
        (0, 0), (20, 0), dxfattribs={"layer": "SOURCE_CONTEXT"}
    )
    stream = BytesIO()
    document.write(stream, fmt="bin")
    source = stream.getvalue()
    project = Project(
        name="Бинарный DXF",
        plan=Plan(objects=[PlanObject(kind="tree", x=10, y=10, radius=1.6)]),
    )

    _, output = DxfRoundTripWriter().create(project, source)
    reopened = EzdxfReader().read("binary-plan.dxf", output)

    assert output.startswith(b"AutoCAD Binary DXF")
    assert any(
        feature["properties"].get("source_layer") == "SOURCE_CONTEXT"
        for feature in reopened.geometry.feature_collection["features"]
    )
    assert any(layer.source_name == "GREEN_ATLAS_TREES" for layer in reopened.layers)


def test_nonfinite_dxf_coordinates_never_reach_the_map_or_calculation() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BROKEN_SURVEY", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    # DXF permits these IEEE values even though neither a map renderer nor a
    # spatial index can safely consume them.
    modelspace.add_line(
        (float("nan"), 20), (40, 20), dxfattribs={"layer": "BROKEN_SURVEY"}
    )
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()

    imported = EzdxfReader().read("nonfinite.dxf", source)
    project = Project(
        name="Повреждённая съёмка",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )

    assert not any(
        feature["properties"].get("source_layer") == "BROKEN_SURVEY"
        for feature in imported.geometry.feature_collection["features"]
    )
    assert imported.bounds == [0.0, 0.0, 100.0, 100.0]
    assert any(
        "BROKEN_SURVEY" in warning and "некорректными координатами" in warning
        for warning in imported.warnings
    )
    assert calculate_confirmed_fixture(project).site_area_m2 == 10_000

    project.plan = Plan(objects=[PlanObject(kind="tree", x=50, y=50, radius=1.6)])
    _, exported = DxfRoundTripWriter().create(project, source)
    assert b"nan" in exported.lower()
    reopened = EzdxfReader().read("nonfinite-export.dxf", exported)
    assert not any(
        feature["properties"].get("source_layer") == "BROKEN_SURVEY"
        for feature in reopened.geometry.feature_collection["features"]
    )


def test_source_capacity_rejects_a_partial_physical_snapshot() -> None:
    """A display budget must never truncate the authoritative source."""
    reader = EzdxfReader(capacity=SourceGeometryCapacity(max_features=3))
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("DENSE_BUILDINGS", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    for index in range(5):
        x = 10 + index * 12
        modelspace.add_lwpolyline(
            [(x, 10), (x + 8, 10), (x + 8, 22), (x, 22)],
            close=True,
            dxfattribs={"layer": "DENSE_BUILDINGS"},
        )
    stream = StringIO()
    document.write(stream)

    with pytest.raises(SourceCapacityExceeded, match="DENSE_BUILDINGS"):
        reader.read("dense.dxf", stream.getvalue().encode())


def test_coordinate_capacity_rejects_an_overdetailed_physical_curve() -> None:
    """An enormous curve cannot silently disappear from the source."""
    reader = EzdxfReader(capacity=SourceGeometryCapacity(max_feature_coordinates=5))
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("DENSE_ROAD", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_lwpolyline(
        [(index * 10, 50) for index in range(8)], dxfattribs={"layer": "DENSE_ROAD"}
    )
    stream = StringIO()
    document.write(stream)

    with pytest.raises(SourceCapacityExceeded, match="DENSE_ROAD"):
        reader.read("detailed-road.dxf", stream.getvalue().encode())


def test_remote_cad_annotations_do_not_make_the_site_open_as_an_empty_dot() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("ANNOTATION", color=7)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_text(
        "Сводный план",
        dxfattribs={"layer": "ANNOTATION", "insert": (1_000_000, 1_000_000)},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read(
        "site-with-remote-title.dxf", stream.getvalue().encode()
    )

    assert imported.bounds == [0.0, 0.0, 100.0, 100.0]
    assert any(
        feature["properties"].get("source_text") == "Сводный план"
        for feature in imported.geometry.feature_collection["features"]
    )


def test_round_trip_returns_manual_metres_to_the_original_dxf_scale() -> None:
    # These cover every common engineering/survey scale plus the two values
    # that used to fall through to an incorrect metre export (miles and km).
    for unit_code, metres_per_unit in {
        1: 0.0254,  # inches
        2: 0.3048,  # feet
        3: 1609.344,  # miles
        4: 0.001,  # millimetres
        5: 0.01,  # centimetres
        6: 1.0,  # metres
        7: 1_000.0,  # kilometres
        10: 0.9144,  # yards
        14: 0.1,  # decimetres
    }.items():
        document = ezdxf.new("R2013", setup=True)
        document.units = unit_code
        document.layers.add("SOURCE_CONTEXT", color=3)
        document.modelspace().add_line(
            (0, 0), (20 / metres_per_unit, 0), dxfattribs={"layer": "SOURCE_CONTEXT"}
        )
        stream = StringIO()
        document.write(stream)
        source = stream.getvalue().encode()

        imported = EzdxfReader().read(f"unit-{unit_code}.dxf", source)
        project = Project(
            name=f"Единицы {unit_code}",
            plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]),
        )
        _, output = DxfRoundTripWriter().create(project, source)
        reopened = ezdxf.read(StringIO(output.decode()))
        planting = reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')[0]

        assert imported.units_assumed is False
        assert imported.bounds is not None and abs(imported.bounds[2] - 20) < 0.000001
        assert abs(planting.dxf.center.x - 20 / metres_per_unit) < 0.000001
        assert abs(planting.dxf.center.y - 30 / metres_per_unit) < 0.000001
        assert abs(planting.dxf.radius - 1.6 / metres_per_unit) < 0.000001


def test_round_trip_keeps_legacy_dxf_codepage_and_cyrillic_source_text() -> None:
    document = ezdxf.new("R2000", setup=True)
    document.encoding = "cp1251"
    document.header["$DWGCODEPAGE"] = "ANSI_1251"
    document.layers.add("ОЗЕЛЕНЕНИЕ", color=3)
    document.modelspace().add_text(
        "Главная аллея", dxfattribs={"layer": "ОЗЕЛЕНЕНИЕ", "insert": (10, 10)}
    )
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode("cp1251")
    project = Project(
        name="Кодовая страница",
        plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]),
    )

    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode("cp1251")))

    assert b"\xc3\xeb\xe0\xe2\xed\xe0\xff \xe0\xeb\xeb\xe5\xff" in output
    assert reopened.encoding == "cp1251"
    assert reopened.header["$DWGCODEPAGE"] == "ANSI_1251"
    assert (
        reopened.modelspace().query('TEXT[layer=="ОЗЕЛЕНЕНИЕ"]')[0].dxf.text
        == "Главная аллея"
    )


def test_round_trip_respects_declared_cp1252_before_a_cyrillic_fallback() -> None:
    document = ezdxf.new("R2000", setup=True)
    document.encoding = "cp1252"
    document.header["$DWGCODEPAGE"] = "ANSI_1252"
    document.layers.add("NOTES", color=3)
    document.modelspace().add_text(
        "Café", dxfattribs={"layer": "NOTES", "insert": (10, 10)}
    )
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode("cp1252")
    project = Project(
        name="Latin code page",
        plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]),
    )

    imported = EzdxfReader().read("cp1252.dxf", source)
    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode("cp1252")))

    assert any(
        feature["properties"].get("source_text") == "Café"
        for feature in imported.geometry.feature_collection["features"]
    )
    assert b"Caf\xe9" in output
    assert reopened.encoding == "cp1252"
    assert reopened.header["$DWGCODEPAGE"] == "ANSI_1252"
    assert reopened.modelspace().query('TEXT[layer=="NOTES"]')[0].dxf.text == "Café"


def test_round_trip_never_merges_planting_into_a_source_layer_with_the_same_name() -> (
    None
):
    document = ezdxf.new("R2013", setup=True)
    document.layers.add("GREEN_ATLAS_TREES", color=7)
    document.layers.add("GREEN_ATLAS_SHRUBS", color=8)
    document.modelspace().add_line(
        (0, 0), (10, 0), dxfattribs={"layer": "GREEN_ATLAS_TREES"}
    )
    stream = StringIO()
    document.write(stream)
    project = Project(
        name="Коллизия слоёв",
        plan=Plan(
            objects=[
                PlanObject(kind="tree", x=20, y=20, radius=1.6),
                PlanObject(kind="shrub", x=30, y=20, radius=0.65),
            ]
        ),
    )

    _, output = DxfRoundTripWriter().create(project, stream.getvalue().encode())
    reopened = ezdxf.read(StringIO(output.decode()))

    assert len(reopened.modelspace().query('LINE[layer=="GREEN_ATLAS_TREES"]')) == 1
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')) == 0
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES_2"]')) == 1
    assert (
        len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_SHRUBS_2"]')) == 1
    )


def test_hatch_hole_is_preserved_for_real_site_geometry() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_HATCH", color=1)
    hatch = document.modelspace().add_hatch(dxfattribs={"layer": "SITE_HATCH"})
    hatch.paths.add_polyline_path(
        [(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True, flags=1
    )
    hatch.paths.add_polyline_path(
        [(5, 5), (15, 5), (15, 15), (5, 15)], is_closed=True, flags=0
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("site-with-hole.dxf", stream.getvalue().encode())
    project = Project(
        name="Участок", layers=imported.layers, source_geometry=imported.geometry
    )

    assert (
        shape(imported.geometry.feature_collection["features"][0]["geometry"]).area
        == 300
    )
    assert calculate_confirmed_fixture(project).site_area_m2 == 300


def test_hatch_with_many_disconnected_islands_stays_fast_and_keeps_every_area() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_HATCH", color=1)
    hatch = document.modelspace().add_hatch(dxfattribs={"layer": "SITE_HATCH"})
    island_count = 900
    for index in range(island_count):
        x, y = (index % 45) * 3, (index // 45) * 3
        hatch.paths.add_polyline_path(
            [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)], is_closed=True, flags=1
        )
    stream = StringIO()
    document.write(stream)

    started = perf_counter()
    imported = EzdxfReader().read("many-islands.dxf", stream.getvalue().encode())
    imported.layers[0].mapped_kind = LayerKind.SITE_BORDER
    geometry = shape(imported.geometry.feature_collection["features"][0]["geometry"])
    project = Project(
        name="Много участков HATCH",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    calculated = calculate_confirmed_fixture(project)
    allowed_fragments = [
        feature
        for feature in calculated.feature_collection["features"]
        if feature["properties"].get("kind") == "allowed"
    ]

    assert isinstance(geometry, MultiPolygon)
    assert len(geometry.geoms) == island_count
    assert geometry.area == island_count
    assert calculated.site_area_m2 == island_count
    assert len(allowed_fragments) == island_count
    assert all(
        feature["geometry"]["type"] == "Polygon" for feature in allowed_fragments
    )
    assert (
        sum(feature["properties"]["area_m2"] for feature in allowed_fragments)
        == island_count
    )
    assert perf_counter() - started < 5


def test_disconnected_setbacks_are_published_as_local_map_fragments() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    for x in (15, 70):
        modelspace.add_lwpolyline(
            [(x, 20), (x + 5, 20), (x + 5, 25), (x, 25)],
            close=True,
            dxfattribs={"layer": "BUILDING"},
        )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("separate-buildings.dxf", stream.getvalue().encode())
    for layer in imported.layers:
        layer.mapped_kind = layer.suggested_kind
    calculated = calculate_confirmed_fixture(
        Project(
            name="Раздельные ограничения",
            layers=imported.layers,
            source_geometry=imported.geometry,
        )
    )
    setback_fragments = [
        feature
        for feature in calculated.feature_collection["features"]
        if feature["properties"].get("rule_id") == "pp743-3.6.3-building"
    ]

    assert len(setback_fragments) == 2
    assert all(
        feature["geometry"]["type"] == "Polygon" for feature in setback_fragments
    )
    assert {
        feature["properties"]["fragment_index"] for feature in setback_fragments
    } == {1, 2}
    assert {
        feature["properties"]["fragment_count"] for feature in setback_fragments
    } == {2}


def test_planar_cad_faces_remain_visible_and_can_be_mapped_as_buildings() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_FACE", color=2)
    document.layers.add("BUILDING_SOLID", color=3)
    document.layers.add("BUILDING_TRACE", color=4)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_3dface(
        [(20, 20, 4), (30, 20, 4), (30, 30, 5), (20, 30, 5)],
        dxfattribs={"layer": "BUILDING_FACE"},
    )
    modelspace.add_solid(
        [(40, 20, 2), (50, 20, 2), (40, 30, 3), (50, 30, 3)],
        dxfattribs={"layer": "BUILDING_SOLID"},
    )
    modelspace.add_trace(
        [(60, 20, 0), (70, 20, 0), (60, 30, 1), (70, 30, 1)],
        dxfattribs={"layer": "BUILDING_TRACE"},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("planar-cad.dxf", stream.getvalue().encode())
    for layer in imported.layers:
        layer.mapped_kind = layer.suggested_kind
    project = Project(
        name="Плоские CAD-примитивы",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    engine = ShapelyGeometryEngine()
    project.geometry = calculate_confirmed_fixture(project, engine=engine)

    entity_types = {
        feature["properties"].get("entity_type")
        for feature in imported.geometry.feature_collection["features"]
    }
    assert {"3DFACE", "SOLID", "TRACE"}.issubset(entity_types)
    for x in (25, 45, 65):
        with pytest.raises(PositionRejected) as failure:
            engine.validate_position(project, x, 25, 1.6, "tree")
        assert failure.value.violation.code == "PP743-3.6.3-BUILDING_CLEARANCE"
        assert failure.value.violation.rule_id == "pp743-3.6.3-building"
        assert failure.value.violation.actual == 0.0
        assert failure.value.violation.required == 5.0
        assert "наружной стены" in failure.value.violation.description


def test_text_only_block_on_a_building_named_layer_never_invents_a_building_setback() -> (
    None
):
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_LABEL", color=2)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    label_block = document.blocks.new("BUILDING_TITLE")
    label_block.add_text("Корпус 3")
    document.modelspace().add_blockref(
        "BUILDING_TITLE", (50, 50), dxfattribs={"layer": "BUILDING_LABEL"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read(
        "building-label-block.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Подписанный план",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    engine = ShapelyGeometryEngine()
    project.geometry = calculate_confirmed_fixture(project, engine=engine)

    label = next(
        feature
        for feature in imported.geometry.feature_collection["features"]
        if feature["properties"].get("source_block") == "BUILDING_TITLE"
    )
    assert label["properties"]["source_context_only"] is True
    engine.validate_position(project, 50, 50, 1.6, "tree")


def test_unprojectable_physical_cad_entity_blocks_a_false_safe_calculation() -> None:
    """A real CAD solid must not disappear when its layer means a building."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_VOLUME", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    solid = modelspace.add_3dsolid(dxfattribs={"layer": "BUILDING_VOLUME"})
    # ezdxf skips an ACIS container without payload when serialising. The
    # bytes are deliberately opaque here: this test exercises the safe path
    # for a valid-but-not-projectable real CAD entity.
    solid.sab = b"ACIS"
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("building-volume.dxf", stream.getvalue().encode())
    project = Project(
        name="Объём здания", layers=imported.layers, source_geometry=imported.geometry
    )
    engine = ShapelyGeometryEngine()

    building_layer = next(
        layer for layer in imported.layers if layer.source_name == "BUILDING_VOLUME"
    )
    assert "3DSOLID" in building_layer.entity_types
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project, engine=engine)
    assert failure.value.reason is FailureReason.GEOMETRY_UNPROJECTABLE_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_VOLUME (3DSOLID)"]}
    assert re.search("BUILDING_VOLUME .*3DSOLID", public_failure_message(failure.value))

    # An explicit decision that this volume is non-physical drafting context is
    # still allowed. The service must not silently make that decision itself.
    building_layer.mapped_kind = LayerKind.IGNORE
    assert calculate_confirmed_fixture(project, engine=engine).site_area_m2 == 10_000


def test_unknown_cad_entity_cannot_disappear_from_a_physical_constraint_layer() -> None:
    """An unsupported CAD type is incomplete terrain, never free ground."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_SYMBOL", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_shape(
        "BUILDING_MARK", (50, 50), dxfattribs={"layer": "BUILDING_SYMBOL"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read(
        "unknown-building-symbol.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Неизвестный символ здания",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    layer = next(
        item for item in imported.layers if item.source_name == "BUILDING_SYMBOL"
    )

    assert layer.entity_types == {"SHAPE": 1}
    assert layer.geometry_complete is False
    assert any(
        "SHAPE" in warning and "физическим ограничением" in warning
        for warning in imported.warnings
    )
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INCOMPLETE_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_SYMBOL"]}
    assert re.search("BUILDING_SYMBOL", public_failure_message(failure.value))

    layer.mapped_kind = LayerKind.IGNORE
    assert calculate_confirmed_fixture(project).site_area_m2 == 10_000


def test_unknown_cad_component_inside_a_block_cannot_disappear_from_its_layer() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BLOCK_ANNOTATION", color=2)
    document.layers.add("BUILDING_SYMBOL", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    block = document.blocks.new("UNKNOWN_BUILDING_COMPONENT")
    block.add_shape("BUILDING_MARK", (0, 0), dxfattribs={"layer": "BUILDING_SYMBOL"})
    modelspace.add_blockref(
        "UNKNOWN_BUILDING_COMPONENT", (50, 50), dxfattribs={"layer": "BLOCK_ANNOTATION"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read(
        "unknown-block-component.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Неизвестный компонент блока",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    layer = next(
        item for item in imported.layers if item.source_name == "BUILDING_SYMBOL"
    )

    assert layer.entity_types == {"SHAPE": 1}
    assert layer.geometry_complete is False
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INCOMPLETE_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_SYMBOL"]}
    assert re.search("BUILDING_SYMBOL", public_failure_message(failure.value))

    layer.mapped_kind = LayerKind.IGNORE
    assert calculate_confirmed_fixture(project).site_area_m2 == 10_000


def test_failed_normalization_of_a_supported_physical_entity_blocks_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser failure is an unknown obstacle, not a zero-sized one."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_FOOTPRINT", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_circle((50, 50), 10, dxfattribs={"layer": "BUILDING_FOOTPRINT"})
    stream = StringIO()
    document.write(stream)

    original = dxf_adapters._entity_geometry

    def reject_circle(entity: object, factor: float) -> dict[str, Any] | None:
        return (
            None
            if getattr(entity, "dxftype")() == "CIRCLE"
            else original(entity, factor)
        )

    monkeypatch.setattr(dxf_adapters, "_entity_geometry", reject_circle)
    imported = EzdxfReader().read(
        "normalization-failure.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Непрочитанный контур",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    layer = next(
        item for item in imported.layers if item.source_name == "BUILDING_FOOTPRINT"
    )

    assert layer.geometry_complete is False
    assert any(
        "BUILDING_FOOTPRINT" in warning and "не удалось прочитать" in warning
        for warning in imported.warnings
    )
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INCOMPLETE_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_FOOTPRINT"]}
    assert re.search("BUILDING_FOOTPRINT", public_failure_message(failure.value))

    layer.mapped_kind = LayerKind.IGNORE
    assert calculate_confirmed_fixture(project).site_area_m2 == 10_000


def test_unreadable_block_with_a_physical_component_keeps_the_component_layer_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BLOCK_ANNOTATION", color=2)
    document.layers.add("BUILDING_EMBEDDED", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    block = document.blocks.new("FAILED_BUILDING_COMPONENT")
    block.add_circle((0, 0), 8, dxfattribs={"layer": "BUILDING_EMBEDDED"})
    modelspace.add_blockref(
        "FAILED_BUILDING_COMPONENT", (50, 50), dxfattribs={"layer": "BLOCK_ANNOTATION"}
    )
    stream = StringIO()
    document.write(stream)

    monkeypatch.setattr(
        block_diagnostics, "_flatten_insert_components", lambda *_args, **_kwargs: []
    )
    imported = EzdxfReader().read(
        "unreadable-building-block.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Непрочитанный блок здания",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    layer = next(
        item for item in imported.layers if item.source_name == "BUILDING_EMBEDDED"
    )

    assert layer.entity_types == {"CIRCLE": 1}
    assert layer.geometry_complete is False
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INCOMPLETE_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_EMBEDDED"]}
    assert re.search("BUILDING_EMBEDDED", public_failure_message(failure.value))

    layer.mapped_kind = LayerKind.IGNORE
    assert calculate_confirmed_fixture(project).site_area_m2 == 10_000


def test_repeated_block_reuses_its_definition_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_SYMBOL", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (200, 0), (200, 200), (0, 200)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    block = document.blocks.new("REPEATED_BUILDING")
    block.add_circle((0, 0), 2, dxfattribs={"layer": "BUILDING_SYMBOL"})
    for index in range(24):
        modelspace.add_blockref(
            "REPEATED_BUILDING",
            (10 + index * 7, 30),
            dxfattribs={"layer": "SITE_BORDER"},
        )
    stream = StringIO()
    document.write(stream)

    original = block_diagnostics._block_definition_components
    calls = 0

    def count_definition(
        reference: Any, inherited_layer: str, depth: int = 0
    ) -> list[tuple[str, str]] | None:
        nonlocal calls
        calls += 1
        return original(reference, inherited_layer, depth)

    monkeypatch.setattr(
        block_diagnostics, "_block_definition_components", count_definition
    )
    imported = EzdxfReader().read("repeated-blocks.dxf", stream.getvalue().encode())

    assert calls == 1
    assert (
        sum(
            feature["properties"].get("source_layer") == "BUILDING_SYMBOL"
            for feature in imported.geometry.feature_collection["features"]
        )
        == 24
    )


def test_planar_ocs_entities_are_normalized_before_mapping_or_calculation() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_CIRCLE", color=2)
    document.layers.add("BUILDING_SOLID", color=3)
    document.layers.add("ROAD_2D", color=4)
    document.layers.add("SOURCE_ARC", color=5)
    document.layers.add("ANNOTATION", color=6)
    modelspace = document.modelspace()
    ocs = {"extrusion": (0, 0, -1)}
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER", **ocs},
    )
    modelspace.add_circle(
        (40, 50, 0), 4, dxfattribs={"layer": "BUILDING_CIRCLE", **ocs}
    )
    modelspace.add_solid(
        [(55, 40, 0), (65, 40, 0), (55, 50, 0), (65, 50, 0)],
        dxfattribs={"layer": "BUILDING_SOLID", **ocs},
    )
    modelspace.add_polyline2d(
        [(10, 70), (90, 70)], dxfattribs={"layer": "ROAD_2D", **ocs}
    )
    modelspace.add_arc((20, 20, 0), 5, 0, 90, dxfattribs={"layer": "SOURCE_ARC", **ocs})
    modelspace.add_text(
        "граница", dxfattribs={"layer": "ANNOTATION", "insert": (12, 22, 0), **ocs}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("ocs-plan.dxf", stream.getvalue().encode())
    by_layer = {
        feature["properties"]["source_layer"]: feature
        for feature in imported.geometry.feature_collection["features"]
    }

    assert shape(by_layer["SITE_BORDER"]["geometry"]).bounds == (
        -100.0,
        0.0,
        0.0,
        100.0,
    )
    assert shape(by_layer["BUILDING_CIRCLE"]["geometry"]).centroid.x == pytest.approx(
        -40.0, abs=0.01
    )
    assert shape(by_layer["BUILDING_SOLID"]["geometry"]).centroid.coords[
        0
    ] == pytest.approx((-60.0, 45.0), abs=0.01)
    assert by_layer["ROAD_2D"]["geometry"]["coordinates"] == [
        [-10.0, 70.0],
        [-90.0, 70.0],
    ]
    assert by_layer["SOURCE_ARC"]["geometry"]["coordinates"][0] == [-25.0, 20.0]
    assert by_layer["ANNOTATION"]["geometry"]["coordinates"] == [-12.0, 22.0]

    project = Project(
        name="OCS план", layers=imported.layers, source_geometry=imported.geometry
    )
    project.geometry = calculate_confirmed_fixture(project)
    with pytest.raises(PositionRejected) as failure:
        ShapelyGeometryEngine().validate_position(project, -40, 50, 1.6, "tree")
    assert failure.value.violation.code == "PP743-3.6.3-BUILDING_CLEARANCE"
    assert failure.value.violation.rule_id == "pp743-3.6.3-building"
    assert failure.value.violation.actual == 0.0
    assert failure.value.violation.required == 5.0
    assert "наружной стены" in failure.value.violation.description


def test_reader_preserves_only_explicit_vertical_building_evidence() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    with_height = modelspace.add_lwpolyline(
        [(0, 0), (8, 0), (8, 6), (0, 6)],
        close=True,
        dxfattribs={"layer": "BUILDING", "elevation": 1.5, "thickness": 12},
    )
    modelspace.add_lwpolyline(
        [(12, 0), (20, 0), (20, 6), (12, 6)],
        close=True,
        dxfattribs={"layer": "BUILDING"},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("explicit-height.dxf", stream.getvalue().encode())
    buildings = [
        feature
        for feature in imported.geometry.feature_collection["features"]
        if feature["properties"]["source_layer"] == "BUILDING"
    ]
    by_handle = {
        feature["properties"]["source_handle"]: feature["properties"]
        for feature in buildings
    }

    assert by_handle[str(with_height.dxf.handle)]["source_extrusion_height_m"] == 12
    assert by_handle[str(with_height.dxf.handle)]["source_base_elevation_m"] == 1.5
    assert "source_extrusion_height_m" not in next(
        properties
        for handle, properties in by_handle.items()
        if handle != str(with_height.dxf.handle)
    )


def test_reader_preserves_metric_xyz_primitives_without_promoting_them_to_terrain() -> (
    None
):
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.MM
    document.layers.add("SURVEY_3D", color=3)
    modelspace = document.modelspace()
    modelspace.add_3dface(
        [
            (1000, 2000, 3500),
            (2000, 2000, 3600),
            (2000, 3000, 3700),
            (1000, 3000, 3550),
        ],
        dxfattribs={"layer": "SURVEY_3D"},
    )
    polyline3d = modelspace.add_polyline3d(
        [(4000, 5000, 6100), (4500, 5500, 6400)],
        dxfattribs={"layer": "SURVEY_3D"},
    )
    modelspace.add_line(
        (7000, 8000, 9000), (7100, 8200, 9300), dxfattribs={"layer": "SURVEY_3D"}
    )
    modelspace.add_point((11000, 12000, 13000), dxfattribs={"layer": "SURVEY_3D"})
    mesh = modelspace.add_mesh(dxfattribs={"layer": "SURVEY_3D"})
    with mesh.edit_data() as data:
        data.vertices = [
            (14000, 15000, 16000),
            (15000, 15000, 16100),
            (14000, 16000, 16200),
        ]
        data.faces = [(0, 1, 2)]
    polyface = modelspace.add_polyface(dxfattribs={"layer": "SURVEY_3D"})
    polyface.append_faces(
        [[(17000, 18000, 19000), (18000, 18000, 19100), (17000, 19000, 19200)]]
    )
    polygon_mesh = modelspace.add_polymesh((2, 2), dxfattribs={"layer": "SURVEY_3D"})
    polygon_mesh.set_mesh_vertex((0, 0), (20000, 21000, 22000))
    polygon_mesh.set_mesh_vertex((0, 1), (21000, 21000, 22100))
    polygon_mesh.set_mesh_vertex((1, 0), (20000, 22000, 22200))
    polygon_mesh.set_mesh_vertex((1, 1), (21000, 22000, 22300))
    elevated = modelspace.add_lwpolyline(
        [(23000, 24000), (24000, 24000)],
        dxfattribs={"layer": "SURVEY_3D", "elevation": 25000, "thickness": -500},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("survey-mm.dxf", stream.getvalue().encode())
    primitives = imported.geometry.vertical_primitives
    by_type = {
        item.source_entity_type: item
        for item in primitives
        if item.source_entity_type != "POLYLINE"
    }
    by_handle = {item.source_handle: item for item in primitives}

    assert by_type["3DFACE"].vertices_m[0] == pytest.approx([1.0, 2.0, 3.5], abs=0.001)
    assert by_type["3DFACE"].faces == [[0, 1, 2, 3]]
    assert by_handle[str(polyline3d.dxf.handle)].vertices_m[-1] == pytest.approx(
        [4.5, 5.5, 6.4], abs=0.001
    )
    assert by_type["LINE"].vertices_m[-1] == pytest.approx([7.1, 8.2, 9.3], abs=0.001)
    assert by_type["POINT"].vertices_m[0] == pytest.approx(
        [11.0, 12.0, 13.0], abs=0.001
    )
    assert by_type["MESH"].vertices_m[-1] == pytest.approx(
        [14.0, 16.0, 16.2], abs=0.001
    )
    assert by_type["MESH"].faces == [[0, 1, 2]]
    assert by_handle[str(polyface.dxf.handle)].primitive_type == "surface_mesh"
    assert by_handle[str(polyface.dxf.handle)].vertices_m[-1] == pytest.approx(
        [17.0, 19.0, 19.2], abs=0.001
    )
    assert by_handle[str(polygon_mesh.dxf.handle)].faces == [[0, 2, 3, 1]]
    assert by_handle[str(polygon_mesh.dxf.handle)].vertices_m[-1] == pytest.approx(
        [21.0, 22.0, 22.3], abs=0.001
    )
    assert by_handle[str(elevated.dxf.handle)].vertices_m[0] == pytest.approx(
        [23.0, 24.0, 25.0], abs=0.001
    )
    assert by_handle[str(elevated.dxf.handle)].vertical_evidence == "explicit_extrusion"
    assert by_handle[str(elevated.dxf.handle)].extrusion_vector_m == pytest.approx(
        [0.0, 0.0, -0.5], abs=0.001
    )
    assert all(item.unit_scale_to_m == 0.001 for item in primitives)
    assert all(item.source_file_units == "мм" for item in primitives)
    assert all(item.terrain_mapping_status == "unmapped" for item in primitives)

    project = Project(
        name="XYZ round-trip", layers=imported.layers, source_geometry=imported.geometry
    )
    project.geometry = calculate_confirmed_fixture(project)
    assert project.geometry.vertical_primitives == primitives


def test_invalid_mapped_polygon_blocks_calculation_instead_of_crashing_geos() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_BAD", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_lwpolyline(
        [(20, 20), (40, 40), (20, 40), (40, 20)],
        close=True,
        dxfattribs={"layer": "BUILDING_BAD"},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("bad-building.dxf", stream.getvalue().encode())
    for layer in imported.layers:
        layer.mapped_kind = layer.suggested_kind
    project = Project(
        name="Некорректный контур",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )

    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INVALID_LAYERS
    assert failure.value.params == {"layer_names": ["BUILDING_BAD"]}
    assert re.search("BUILDING_BAD", public_failure_message(failure.value))
    assert project.source_geometry is not None
    assert any(
        feature["properties"].get("source_layer") == "BUILDING_BAD"
        for feature in project.source_geometry.feature_collection["features"]
    )


def test_bulged_polyline_is_flattened_without_a_false_straight_boundary() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.modelspace().add_lwpolyline(
        [(0, 0, 1), (20, 0, 0), (20, 20, 0), (0, 20, 0)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("bulged.dxf", stream.getvalue().encode())
    ring = imported.geometry.feature_collection["features"][0]["geometry"][
        "coordinates"
    ][0]

    assert len(ring) > 5
    assert any(point[1] < 0 for point in ring)


def test_closed_ellipse_can_be_a_boundary_but_open_arc_cannot() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_ELLIPSE", color=1)
    document.layers.add("SITE_ARC", color=2)
    document.modelspace().add_ellipse(
        (30, 30), (20, 0), ratio=0.5, dxfattribs={"layer": "SITE_ELLIPSE"}
    )
    document.modelspace().add_arc(
        (70, 30), 20, 0, 180, dxfattribs={"layer": "SITE_ARC"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("curves.dxf", stream.getvalue().encode())
    by_layer = {
        feature["properties"]["source_layer"]: feature
        for feature in imported.geometry.feature_collection["features"]
    }

    assert by_layer["SITE_ELLIPSE"]["geometry"]["type"] == "Polygon"
    assert by_layer["SITE_ARC"]["geometry"]["type"] == "LineString"
    assert (
        next(
            layer for layer in imported.layers if layer.source_name == "SITE_ELLIPSE"
        ).suggested_kind
        == LayerKind.SITE_BORDER
    )
    assert (
        next(
            layer for layer in imported.layers if layer.source_name == "SITE_ARC"
        ).suggested_kind
        == LayerKind.IGNORE
    )


def test_wide_road_polyline_uses_its_ground_footprint_for_clearance() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("ROAD", color=2)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    document.modelspace().add_lwpolyline(
        [(10, 50), (90, 50)], dxfattribs={"layer": "ROAD", "const_width": 8}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("wide-road.dxf", stream.getvalue().encode())
    project = Project(
        name="Широкая дорога", layers=imported.layers, source_geometry=imported.geometry
    )
    project.geometry = calculate_confirmed_fixture(project)

    with pytest.raises(PositionRejected) as failure:
        ShapelyGeometryEngine().validate_position(project, 50, 55.5, 1.6, "tree")
    assert failure.value.violation.code == "PP743-3.6.3-ROAD-EDGE_CLEARANCE"
    assert failure.value.violation.rule_id == "pp743-3.6.3-road-edge"
    assert failure.value.violation.actual == 1.5
    assert failure.value.violation.required == 2.0
    assert "проезжей части" in failure.value.violation.description


def test_stricter_tree_map_cue_does_not_false_block_a_shrub() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_lwpolyline(
        [(20, 20), (30, 20), (30, 30), (20, 30)],
        close=True,
        dxfattribs={"layer": "BUILDING"},
    )
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read(
        "plant-kind-clearance.dxf", stream.getvalue().encode()
    )
    project = Project(
        name="Разные отступы", layers=imported.layers, source_geometry=imported.geometry
    )
    engine = ShapelyGeometryEngine()
    project.geometry = calculate_confirmed_fixture(project, engine=engine)

    # The point is on the 5 m tree threshold and outside the 1.5 m shrub
    # threshold. It must not inherit the tree prohibition merely because the
    # map has one conservative cue. PP-743 measures both values to the axis.
    engine.validate_position(project, 35, 25, 0.65, "shrub")
    engine.validate_position(project, 35, 25, 1.6, "tree")
    with pytest.raises(PositionRejected) as failure:
        engine.validate_position(project, 34.99, 25, 1.6, "tree")
    assert failure.value.violation.code == "PP743-3.6.3-BUILDING_CLEARANCE"
    assert failure.value.violation.rule_id == "pp743-3.6.3-building"
    assert failure.value.violation.actual == 4.99
    assert failure.value.violation.required == 5.0
    assert "наружной стены" in failure.value.violation.description


def test_empty_common_tree_cue_does_not_block_a_shrub_only_site() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (10, 0), (10, 20), (0, 20)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    modelspace.add_lwpolyline(
        [(4.5, 0), (5.5, 0), (5.5, 20), (4.5, 20)],
        close=True,
        dxfattribs={"layer": "BUILDING"},
    )
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read("shrub-only-site.dxf", stream.getvalue().encode())
    project = Project(
        name="Узкий участок", layers=imported.layers, source_geometry=imported.geometry
    )
    engine = ShapelyGeometryEngine()

    project.geometry = calculate_confirmed_fixture(project, engine=engine)

    assert project.geometry.allowed_area_m2 == 0
    assert not any(
        feature["properties"].get("kind") == "allowed"
        for feature in project.geometry.feature_collection["features"]
    )
    engine.validate_position(project, 1.5, 10, 0.65, "shrub")


def test_block_array_expands_explicit_constraint_components() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    block = document.blocks.new("BUILDING_MODULE")
    block.add_lwpolyline(
        [(0, 0), (4, 0), (4, 4), (0, 4)], close=True, dxfattribs={"layer": "BUILDING"}
    )
    insert = document.modelspace().add_blockref("BUILDING_MODULE", (20, 20))
    insert.dxf.row_count = 2
    insert.dxf.column_count = 2
    insert.dxf.row_spacing = 20
    insert.dxf.column_spacing = 20
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("array.dxf", stream.getvalue().encode())
    buildings = [
        item
        for item in imported.geometry.feature_collection["features"]
        if item["properties"].get("source_layer") == "BUILDING"
    ]

    assert len(buildings) == 4
    assert {item["properties"].get("source_block_instance") for item in buildings} == {
        1,
        2,
        3,
        4,
    }


def test_nested_block_keeps_an_explicit_constraint_layer_after_transform() -> None:
    """A building inside a reusable symbol must not inherit its legend layer."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("SYMBOLS", color=3)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    building = document.blocks.new("BUILDING_CORE")
    building.add_lwpolyline(
        [(0, 0), (4, 0), (4, 4), (0, 4)], close=True, dxfattribs={"layer": "BUILDING"}
    )
    compound = document.blocks.new("COMPOUND_SYMBOL")
    compound.add_blockref("BUILDING_CORE", (10, 0), dxfattribs={"layer": "0"})
    modelspace.add_blockref(
        "COMPOUND_SYMBOL", (20, 20), dxfattribs={"layer": "SYMBOLS"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("nested-building.dxf", stream.getvalue().encode())
    project = Project(
        name="Вложенный символ",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    buildings = [
        feature
        for feature in imported.geometry.feature_collection["features"]
        if feature["properties"].get("source_layer") == "BUILDING"
    ]

    assert len(buildings) == 1
    assert buildings[0]["properties"]["source_block"] == "COMPOUND_SYMBOL"
    project.geometry = calculate_confirmed_fixture(project)
    with pytest.raises(PositionRejected) as failure:
        ShapelyGeometryEngine().validate_position(project, 32, 22, 1.6, "tree")
    assert failure.value.violation.code == "PP743-3.6.3-BUILDING_CLEARANCE"
    assert failure.value.violation.rule_id == "pp743-3.6.3-building"
    assert failure.value.violation.actual == 0.0
    assert failure.value.violation.required == 5.0
    assert "наружной стены" in failure.value.violation.description


def test_recursive_block_is_preserved_but_blocks_calculation_without_expansion() -> (
    None
):
    """A corrupt cyclic block graph must never become a false empty site."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    first = document.blocks.new("LOOP_A")
    second = document.blocks.new("LOOP_B")
    first.add_blockref("LOOP_B", (0, 0), dxfattribs={"layer": "0"})
    second.add_blockref("LOOP_A", (0, 0), dxfattribs={"layer": "BUILDING"})
    document.modelspace().add_blockref(
        "LOOP_A", (30, 30), dxfattribs={"layer": "BUILDING"}
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("recursive-block.dxf", stream.getvalue().encode())
    project = Project(
        name="Циклический блок",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )

    assert any(
        feature["properties"].get("source_analysis_blocking")
        for feature in imported.geometry.feature_collection["features"]
    )
    assert any("Крупных блоковых массивов" in warning for warning in imported.warnings)
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_BLOCK_ARRAY_UNSUPPORTED
    assert failure.value.params == {
        "component_count": block_diagnostics.MAX_EXPANDED_MINSERT_INSTANCES + 1
    }
    assert re.search(
        "нельзя безопасно развернуть", public_failure_message(failure.value)
    )


def test_oversized_block_array_blocks_calculation_instead_of_claiming_safe_space() -> (
    None
):
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    block = document.blocks.new("DENSE_MODULE")
    block.add_lwpolyline(
        [(0, 0), (1, 0), (1, 1), (0, 1)], close=True, dxfattribs={"layer": "BUILDING"}
    )
    insert = document.modelspace().add_blockref("DENSE_MODULE", (10, 10))
    insert.dxf.row_count = 150
    insert.dxf.column_count = 150
    insert.dxf.row_spacing = 2
    insert.dxf.column_spacing = 2
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("too-dense.dxf", stream.getvalue().encode())
    project = Project(
        name="Слишком большой массив",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )

    assert any("Крупных блоковых массивов" in warning for warning in imported.warnings)
    assert any(
        item["properties"].get("source_analysis_blocking")
        for item in imported.geometry.feature_collection["features"]
    )
    with pytest.raises(DomainFailure) as failure:
        calculate_confirmed_fixture(project)
    assert failure.value.reason is FailureReason.GEOMETRY_BLOCK_ARRAY_UNSUPPORTED
    assert failure.value.params == {"component_count": 22500}
    assert re.search(
        "нельзя безопасно развернуть", public_failure_message(failure.value)
    )


def test_round_trip_preserves_source_entities_and_adds_only_planting_layer() -> None:
    document = ezdxf.new("R2018", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SOURCE_CONTEXT", color=3)
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (20, 0), dxfattribs={"layer": "SOURCE_CONTEXT"})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()
    project = imported_project()
    project.plan = Plan(objects=[PlanObject(kind="tree", x=20, y=20, radius=1.6)])

    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode()))

    assert len(reopened.modelspace().query('LINE[layer=="SOURCE_CONTEXT"]')) == 1
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')) == 1


def test_large_real_dxf_keeps_extent_and_local_context() -> None:
    imported = EzdxfReader().read(LARGE_DXF.name, LARGE_DXF.read_bytes())
    project = Project(
        name="Большой DXF",
        layers=imported.layers,
        source_geometry=imported.geometry,
        geometry_version=1,
    )
    page = IndexedGeometryQuery().query(project, source_extent(imported), 3)

    assert imported.entity_count == 4859
    assert (
        imported.bounds is not None and imported.bounds[2] - imported.bounds[0] > 4000
    )
    assert any(
        feature["properties"].get("source_layer") == "OSM_ROAD_LOCAL"
        for feature in page.feature_collection["features"]
    )


def test_dense_moscow_dxf_keeps_pedestrian_technical_and_existing_green_context() -> (
    None
):
    imported = EzdxfReader().read(KITAY_GOROD_DXF.name, KITAY_GOROD_DXF.read_bytes())
    project = Project(
        name="Китай-город",
        layers=imported.layers,
        source_geometry=imported.geometry,
        geometry_version=1,
    )
    page = IndexedGeometryQuery().query(project, source_extent(imported), 2)
    source_layers = {
        feature["properties"].get("source_layer")
        for feature in page.feature_collection["features"]
    }

    assert imported.entity_count == 9020
    assert (
        imported.bounds is not None and imported.bounds[2] - imported.bounds[0] > 1_500
    )
    assert {
        "OSM_BUILDING",
        "OSM_PATH",
        "OSM_GREEN_EXISTING",
        "OSM_BARRIER",
        "OSM_ROAD_LOCAL",
    } <= source_layers


def test_dense_moscow_3d_fixture_preserves_dem_and_auditable_building_extrusions() -> (
    None
):
    imported = EzdxfReader().read(
        KITAY_GOROD_3D_DXF.name, KITAY_GOROD_3D_DXF.read_bytes()
    )
    terrain = [
        item
        for item in imported.geometry.vertical_primitives
        if item.terrain_mapping_status == "confirmed"
    ]
    extrusions = [
        item
        for item in imported.geometry.vertical_primitives
        if item.vertical_evidence == "explicit_extrusion"
    ]
    source_layers = {item.source_layer for item in extrusions}

    assert imported.entity_count == 9_340
    assert imported.coordinate_reference.status == "declared"
    assert imported.coordinate_reference.source == "dxf_custom_georeference"
    assert imported.coordinate_reference.crs_id is None
    assert imported.coordinate_reference.origin_wgs84 == [55.75225, 37.6235]
    assert (
        imported.coordinate_reference.local_projection == "local_equirectangular_wgs84"
    )
    assert len(terrain) == 320
    assert all(item.primitive_type == "surface_mesh" for item in terrain)
    assert all(
        item.terrain_mapping_basis == "dxf_document_metadata" for item in terrain
    )
    assert {item.source_dataset for item in terrain} == {
        "Copernicus DEM 2021 GLO-90 via Open-Meteo Elevation API"
    }
    assert {item.source_url for item in terrain} == {
        "https://open-meteo.com/en/docs/elevation-api"
    }
    assert {item.terrain_confidence for item in terrain} == {"estimated"}
    assert all(
        "Copernicus WorldDEM-90" in (item.source_attribution or "") for item in terrain
    )
    assert {item.vertical_datum for item in terrain} == {"local_zero_at_dem_minimum"}
    assert {item.vertical_datum_offset_m for item in terrain} == {120.0}
    assert {
        "GREEN_ATLAS_BUILDING_OSM_HEIGHT",
        "GREEN_ATLAS_BUILDING_OSM_LEVELS",
    } <= source_layers
    assert (
        max(vertex[2] for item in terrain for vertex in item.vertices_m)
        - min(vertex[2] for item in terrain for vertex in item.vertices_m)
        >= 40
    )


def test_vdnkh_3d_fixture_has_strict_dem_provenance_and_split_building_heights() -> (
    None
):
    imported = EzdxfReader().read(VDNKH_3D_DXF.name, VDNKH_3D_DXF.read_bytes())
    terrain = [
        item
        for item in imported.geometry.vertical_primitives
        if item.terrain_mapping_status == "confirmed"
    ]
    extrusions = [
        item
        for item in imported.geometry.vertical_primitives
        if item.vertical_evidence == "explicit_extrusion"
    ]

    assert imported.entity_count == 5_190
    assert imported.coordinate_reference.status == "declared"
    assert imported.coordinate_reference.source == "dxf_custom_georeference"
    assert imported.coordinate_reference.crs_id is None
    assert imported.coordinate_reference.origin_wgs84 == [55.8295, 37.6355]
    assert (
        imported.coordinate_reference.local_projection == "local_equirectangular_wgs84"
    )
    assert imported.bounds == pytest.approx(
        [-906.59, -946.216, 906.59, 946.216], abs=0.001
    )
    assert len(terrain) == 320
    assert {item.terrain_mapping_basis for item in terrain} == {"dxf_document_metadata"}
    assert {item.source_dataset for item in terrain} == {
        "Copernicus DEM 2021 GLO-90 via Open-Meteo Elevation API"
    }
    assert {item.source_url for item in terrain} == {
        "https://open-meteo.com/en/docs/elevation-api"
    }
    assert {item.terrain_confidence for item in terrain} == {"estimated"}
    assert all(
        "Copernicus WorldDEM-90" in (item.source_attribution or "") for item in terrain
    )
    assert {item.vertical_datum for item in terrain} == {"local_zero_at_dem_minimum"}
    assert {item.vertical_datum_offset_m for item in terrain} == {134.0}
    assert max(vertex[2] for item in terrain for vertex in item.vertices_m) == 37.0
    by_layer = Counter(item.source_layer for item in extrusions)
    assert by_layer["GREEN_ATLAS_BUILDING_OSM_HEIGHT"] == 23
    assert by_layer["GREEN_ATLAS_BUILDING_OSM_LEVELS"] == 409


def test_terrain_layer_name_without_document_evidence_cannot_confirm_terrain() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("GREEN_ATLAS_TERRAIN_COP90", color=3)
    document.modelspace().add_3dface(
        [(0, 0, 1), (10, 0, 2), (10, 10, 3), (0, 10, 2)],
        dxfattribs={"layer": "GREEN_ATLAS_TERRAIN_COP90"},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read(
        "spoofed-terrain-layer.dxf", stream.getvalue().encode()
    )
    primitive = imported.geometry.vertical_primitives[0]

    assert primitive.primitive_type == "surface_mesh"
    assert primitive.terrain_mapping_status == "unmapped"
    assert primitive.terrain_mapping_basis is None
    assert primitive.terrain_confidence is None
    assert primitive.source_dataset is None


def test_viewport_index_is_reused_after_non_geometry_project_changes() -> None:
    imported = EzdxfReader().read(LARGE_DXF.name, LARGE_DXF.read_bytes())
    project = Project(
        name="Кэш карты",
        layers=imported.layers,
        source_geometry=imported.geometry,
        geometry_version=1,
    )
    query = IndexedGeometryQuery()
    extent = source_extent(imported)

    query.query(project, extent, 1)
    initial_index = query._indexes[project.id]
    project.state_version += 1
    query.query(project, extent, 1)

    assert query._indexes[project.id] is initial_index


def test_viewport_index_can_be_released_when_a_project_leaves_the_session() -> None:
    imported = EzdxfReader().read(LARGE_DXF.name, LARGE_DXF.read_bytes())
    project = Project(
        name="Освобождаем карту",
        layers=imported.layers,
        source_geometry=imported.geometry,
        geometry_version=1,
    )
    query = IndexedGeometryQuery()

    query.query(project, source_extent(imported), 1)
    assert query.cached_project_count == 1
    assert project.id in query._indexes

    query.discard(project.id)

    assert query.cached_project_count == 0
    assert project.id not in query._indexes


def test_viewport_simplification_uses_a_precise_zoom_key_and_a_bounded_lru() -> None:
    # A coarse overview must never leak into a closer zoom merely because both
    # resolutions were formerly called "mid". At the same time, pan/zoom over
    # an arbitrary CAD file must not retain unbounded simplified GeoJSON.
    features = [
        {
            "type": "Feature",
            "id": "curved-road",
            "properties": {"kind": "road", "source_layer": "ROAD"},
            "geometry": {
                "type": "LineString",
                "coordinates": [[0, 0], [1, 0.5], [2, 0], [3, 0.5], [4, 0]],
            },
        }
    ]
    features.extend(
        {
            "type": "Feature",
            "id": f"survey-{index}",
            "properties": {"kind": "source", "source_layer": "SURVEY"},
            "geometry": {"type": "Point", "coordinates": [10 + index, 0]},
        }
        for index in range(32)
    )
    project = Project(
        name="Кэш масштаба",
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        geometry_version=1,
    )
    query = IndexedGeometryQuery(max_features=4)

    close = query.query(project, (0, -2, 5, 2), 1)
    coarse = query.query(project, (0, -2, 5, 2), 2)

    close_line = close.feature_collection["features"][0]["geometry"]["coordinates"]
    coarse_line = coarse.feature_collection["features"][0]["geometry"]["coordinates"]
    assert len(close_line) > len(coarse_line)

    # Visit more source geometry and several wheel-zoom values. The index can
    # retain at most one viewport's worth of derived paths.
    for start in range(10, 40, 4):
        query.query(project, (start, -2, start + 4, 2), 1.0 + start / 100)
    index = query._indexes[project.id]
    assert len(index.simplified_geometries) <= query.max_features


def test_budgeted_viewport_keeps_cad_context_before_dense_survey_detail() -> None:
    # A dense topographic layer must not crowd out the few objects that make
    # a manual planting decision understandable. This also protects the
    # bounded selection path from regressing to a full Python-sized response.
    def point(feature_id: str, kind: str, x: float, y: float) -> dict:
        return {
            "type": "Feature",
            "id": feature_id,
            "properties": {"kind": kind, "source_layer": kind.upper()},
            "geometry": {"type": "Point", "coordinates": [x, y]},
        }

    features = [
        point("border", "site_border", 1, 1),
        point("building", "building", 2, 2),
        point("road", "road", 3, 3),
        point("utility", "utility", 4, 4),
        point("green", "existing_green", 5, 5),
    ]
    features.extend(
        point(f"survey-{index}", "source", 10 + index % 80, 10 + index // 80)
        for index in range(10_000)
    )
    project = Project(
        name="Плотная топосъёмка",
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        geometry_version=1,
    )

    page = IndexedGeometryQuery(max_features=5).query(project, (0, 0, 150, 150), 1)
    returned = page.feature_collection["features"]
    metadata = page.feature_collection["metadata"]

    assert [feature["id"] for feature in returned] == [
        "border",
        "building",
        "road",
        "utility",
        "green",
    ]
    assert metadata["truncated"] is True
    assert metadata["total_matches"] == 10_005
    assert metadata["omitted_by_kind"] == {"source": 10_000}


def test_dense_arbitrary_dxf_is_bounded_through_import_viewport_storage_and_export(
    tmp_path: Path,
) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("TOPOGRAPHY_2026", color=8)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (1200, 0), (1200, 400), (0, 400)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    for index in range(20_000):
        modelspace.add_point(
            ((index % 250) * 4.5 + 20, (index // 250) * 4.5 + 20),
            dxfattribs={"layer": "TOPOGRAPHY_2026"},
        )
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()

    started = perf_counter()
    imported = EzdxfReader().read("dense-survey.dxf", source)
    project = Project(
        name="Плотная топосъёмка",
        layers=imported.layers,
        source_geometry=imported.geometry,
        geometry_version=1,
    )
    project.geometry = calculate_confirmed_fixture(project)
    page = IndexedGeometryQuery(max_features=512).query(
        project, source_extent(imported), 1
    )
    repository = SqliteProjectRepository(tmp_path / "dense.sqlite3")
    stored = repository.create(project)
    repository.save(stored, source=source)
    restored = repository.get(stored.id)
    restored.plan = Plan(objects=[PlanObject(kind="tree", x=20, y=20, radius=1.6)])
    _, output = DxfRoundTripWriter().create(
        restored, repository.get_source(stored.id) or b""
    )

    assert page.feature_collection["metadata"]["returned_features"] == 512
    assert page.feature_collection["metadata"]["truncated"] is True
    assert (
        len(ezdxf.read(StringIO(output.decode())).modelspace().query("POINT")) == 20_000
    )
    assert perf_counter() - started < 30


def test_large_constraint_union_reports_completed_batches_then_an_honest_atomic_stage() -> (
    None
):
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_GRID", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (1200, 0), (1200, 400), (0, 400)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    for index in range(650):
        x, y = (index % 65) * 16 + 20, (index // 65) * 25 + 20
        modelspace.add_lwpolyline(
            [(x, y), (x + 5, y), (x + 5, y + 5), (x, y + 5)],
            close=True,
            dxfattribs={"layer": "BUILDING_GRID"},
        )
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read("large-constraints.dxf", stream.getvalue().encode())
    project = Project(
        name="Большой набор ограничений",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    updates: list[WorkProgress] = []

    calculate_confirmed_fixture(project, updates.append)

    union_updates = [
        item for item in updates if item.stage.startswith("Объединяем объекты:")
    ]
    final_union = next(
        item for item in updates if item.stage.startswith("Сводим контуры:")
    )
    numeric_progress = [item.fraction for item in updates if item.fraction is not None]
    assert [item.processed for item in union_updates] == [320, 640, 650]
    assert final_union.fraction is None
    assert final_union.processed is None
    assert numeric_progress == sorted(numeric_progress)


def calculation_control(*, site: bool = True, partial: bool = False) -> Project:
    """Фиксированная локальная геометрия проверяет расчёт без чтения CAD."""
    site_shape = MultiPolygon(
        [
            Polygon(
                [(0, 0), (90, 0), (90, 80), (0, 80), (0, 0)],
                [[(30, 30), (35, 30), (35, 35), (30, 35), (30, 30)]],
            ),
            box(100, 0, 110, 10),
        ]
    )
    inputs = [
        (LayerKind.BUILDING, MultiPolygon([box(10, 10, 15, 15), box(60, 10, 65, 15)])),
        (LayerKind.ROAD, LineString([(0, 45), (90, 45)])),
        (LayerKind.EXISTING_GREEN, box(40, 55, 46, 61)),
        (LayerKind.WATER, box(20, 55, 28, 63)),
        (LayerKind.RESTRICTED, box(55, 55, 61, 61)),
        (LayerKind.IGNORE, box(0, 0, 1, 1)),
        (LayerKind.UTILITY, box(2, 2, 3, 3)),
    ]
    if site:
        inputs.insert(0, (LayerKind.SITE_BORDER, box(-1, -1, 111, 81)))
    features: list[dict[str, Any]] = []
    layers = []
    for kind, geometry in inputs:
        properties: dict[str, Any] = {"source_layer": kind.value}
        if kind == LayerKind.SITE_BORDER:
            properties["source_polyline_center_geometry"] = mapping(site_shape)
        if kind == LayerKind.UTILITY:
            properties.update(source_context_only=True, utility_context={"stale": True})
        features.append(
            {
                "type": "Feature",
                "id": kind.value,
                "properties": properties,
                "geometry": mapping(geometry)
                if kind != LayerKind.IGNORE
                else {"bad": True},
            }
        )
        layers.append(
            Layer(
                id=kind.value,
                source_name=kind.value,
                suggested_kind=kind,
                mapped_kind=kind,
                mapping_confirmed=True,
                object_count=1,
                color="#475569",
                geometry_complete=not (partial and kind == LayerKind.BUILDING),
            )
        )
    features.append(
        {
            "type": "Feature",
            "id": "distant-building",
            "properties": {"source_layer": "building"},
            "geometry": mapping(box(300, 300, 305, 305)),
        }
    )
    return Project(
        id="calculation-control",
        name="Проверка расчёта",
        created_at="2026-10-08T00:00:00+00:00",
        updated_at="2026-10-08T00:00:00+00:00",
        source_file=SourceFile(
            name="control.dxf",
            size=1,
            imported_at="2026-10-08T00:00:00+00:00",
            dxf_version="test",
            units="m",
            entity_count=len(features),
            accept_partial_geometry=partial,
        ),
        layers=layers,
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features},
            vertical_primitives=[
                DxfVerticalPrimitive(
                    primitive_id="height-control",
                    primitive_type="polyline",
                    vertices_m=[[5, 5, 2], [6, 6, 3]],
                    source_layer="ignore",
                    source_entity_type="POLYLINE",
                    source_file_units="m",
                    unit_scale_to_m=1,
                    source_space="dxf_wcs",
                    vertical_evidence="explicit_xyz",
                )
            ],
        ),
        planting_zones=[
            PlantingZoneAssignment(
                id="selected", label="Участок", geometry=mapping(box(5, 5, 85, 70))
            )
        ],
    )


@pytest.mark.parametrize(
    "site,partial,snapshot_sha,windows_snapshot_sha,progress_sha",
    [
        (
            True,
            False,
            "c1b70153ec895565d1bd9ddc1e8c2f624f49a446d120157da7084bcf996fe3b2",
            "1b9b5dc1df867c4edaab9f243a269a8e4c21fea3aa64fe3296b3779074550299",
            "bc17e759a2ba68e2bde6375515d76d112f7eef1d6d01e9236121064278ed4b48",
        ),
        (
            True,
            True,
            "0b22532f46cac57190c5e62339fe64e3462ccf2962d117d2a81eccbbb0a8ecd9",
            "09b7de343431649982bda799236219c39f5117a633f35bd1442a944c1d4289ea",
            "bc17e759a2ba68e2bde6375515d76d112f7eef1d6d01e9236121064278ed4b48",
        ),
        (
            False,
            False,
            "5e290e53024c4c0786830fa0786e30510c3a08782b9079be1dafa011af6d69d4",
            "0b95ac2d5291e2adc2213d3df0bd8827d2e9fb8076c1053661b534f331d6b379",
            "37511a553ac1f5a8b86574f021d9f6358c99b3883a94b8ec9911347fc7d8a4a2",
        ),
    ],
)
def test_calculation_preserves_complete_snapshot_and_callback_sequence(
    site: bool,
    partial: bool,
    snapshot_sha: str,
    windows_snapshot_sha: str,
    progress_sha: str,
) -> None:
    project = calculation_control(site=site, partial=partial)
    before = project.model_dump(mode="json")
    updates: list[WorkProgress] = []
    result = ShapelyGeometryEngine().calculate(project, updates.append)
    # Контрольные SHA сняты до рефакторинга и покрывают координаты, порядок и свойства.
    encoded = json.dumps(result.model_dump(mode="json"), sort_keys=True).encode()
    callbacks = json.dumps([asdict(item) for item in updates], sort_keys=True).encode()
    # Полные синтетические снимки сверены между Windows x64 и Mac ARM64:
    # отличаются лишь 4/4/6 координат buffer-колец в 1 ULP, максимум 5.69e-14 м.
    # Свойства, порядок, области и callbacks совпадают. Точные платформенные SHA
    # сохраняют контроль всех значений без округления производственных данных.
    expected_snapshot_sha = (
        windows_snapshot_sha if sys.platform == "win32" else snapshot_sha
    )
    assert sha256(encoded).hexdigest() == expected_snapshot_sha
    assert sha256(callbacks).hexdigest() == progress_sha
    assert project.model_dump(mode="json") == before
    assert result.calculation_scope == (
        "available_data" if partial else "complete_source"
    )
    assert result.site_area_m2 == (7275 if site else None)
    assert project.source_geometry is not None
    assert result.vertical_primitives == project.source_geometry.vertical_primitives
    features = result.feature_collection["features"]
    assert features[-1]["id"] == "planting-area-selected"
    assert (
        next(item for item in features if item["id"] == "utility")["properties"]["kind"]
        == "ignore"
    )


@pytest.mark.parametrize(
    "stage",
    [
        "Читаем геометрию слоёв",
        "Собираем границу проектирования",
        "Объединяем объекты: отступ от наружной стены здания",
        "Вычисляем итоговую допустимую область",
        "Фиксируем выбранные рабочие области",
    ],
)
def test_calculation_callback_cancellation_preserves_source(stage: str) -> None:
    project = calculation_control()
    before = project.model_dump(mode="json")
    reached: list[str] = []

    def cancel(update: WorkProgress) -> None:
        reached.append(update.stage)
        if update.stage == stage:
            raise OperationCancelled(stage)

    with pytest.raises(OperationCancelled, match=stage):
        ShapelyGeometryEngine().calculate(project, cancel)
    assert reached[-1] == stage
    assert project.model_dump(mode="json") == before


@pytest.mark.parametrize("invalid", ["shape", "topology", "authored_boundary"])
def test_calculation_invalid_feature_preserves_refusal_and_callback_order(
    invalid: str,
) -> None:
    project = calculation_control()
    assert project.source_geometry is not None
    feature = project.source_geometry.feature_collection["features"][0]
    if invalid == "shape":
        feature["geometry"] = {"bad": True}
    elif invalid == "topology":
        feature["geometry"] = mapping(
            Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)])
        )
    else:
        feature["properties"]["source_polyline_center_geometry"] = {"type": "Polygon"}
    before = project.model_dump(mode="json")
    updates: list[WorkProgress] = []
    with pytest.raises(DomainFailure) as failure:
        ShapelyGeometryEngine().calculate(project, updates.append)
    assert failure.value.reason is FailureReason.GEOMETRY_INVALID_LAYERS
    assert failure.value.params == {"layer_names": ["site_border"]}
    assert [item.processed for item in updates] == [
        0,
        *([1] if invalid == "topology" else []),
        *range(2, 10),
    ]
    assert all(item.stage == "Читаем геометрию слоёв" for item in updates)
    assert project.model_dump(mode="json") == before
