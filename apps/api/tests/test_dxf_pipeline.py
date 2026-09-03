from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter

import ezdxf
import pytest
from shapely.geometry import shape

import app.dxf_import.adapters as dxf_adapters
from app.contracts import GeometrySnapshot, LayerKind, Plan, PlanObject, Project
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.projects.adapters import SqliteProjectRepository


SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"
LARGE_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "vdnkh-large.dxf"
KITAY_GOROD_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "kitay-gorod" / "kitay-gorod-large.dxf"


def imported_project(source: bytes | None = None) -> Project:
    imported = EzdxfReader().read("site.dxf", source or SITE_DXF.read_bytes())
    return Project(name="DXF", layers=imported.layers, source_geometry=imported.geometry)


def test_reader_accepts_binary_dxf_and_keeps_boundary_kind() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.modelspace().add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    stream = BytesIO()
    document.write(stream, fmt="bin")

    # The HTTP collector hands the parser one mutable buffer. Binary DXF must
    # retain the same no-text-decode path as an ordinary immutable upload.
    imported = EzdxfReader().read("binary-site.dxf", bytearray(stream.getvalue()))

    assert imported.entity_count == 1
    assert imported.bounds == [0.0, 0.0, 20.0, 20.0]
    assert imported.layers[0].suggested_kind == LayerKind.SITE_BORDER


def test_round_trip_keeps_a_binary_dxf_binary() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SOURCE_CONTEXT", color=3)
    document.modelspace().add_line((0, 0), (20, 0), dxfattribs={"layer": "SOURCE_CONTEXT"})
    stream = BytesIO()
    document.write(stream, fmt="bin")
    source = stream.getvalue()
    project = Project(name="Бинарный DXF", plan=Plan(objects=[PlanObject(kind="tree", x=10, y=10, radius=1.6)]))

    _, output = DxfRoundTripWriter().create(project, source)
    reopened = EzdxfReader().read("binary-plan.dxf", output)

    assert output.startswith(b"AutoCAD Binary DXF")
    assert any(feature["properties"].get("source_layer") == "SOURCE_CONTEXT" for feature in reopened.geometry.feature_collection["features"])
    assert any(layer.source_name == "GREEN_ATLAS_TREES" for layer in reopened.layers)


def test_nonfinite_dxf_coordinates_never_reach_the_map_or_calculation() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BROKEN_SURVEY", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    # DXF permits these IEEE values even though neither a map renderer nor a
    # spatial index can safely consume them.
    modelspace.add_line((float("nan"), 20), (40, 20), dxfattribs={"layer": "BROKEN_SURVEY"})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()

    imported = EzdxfReader().read("nonfinite.dxf", source)
    project = Project(name="Повреждённая съёмка", layers=imported.layers, source_geometry=imported.geometry)

    assert not any(feature["properties"].get("source_layer") == "BROKEN_SURVEY" for feature in imported.geometry.feature_collection["features"])
    assert imported.bounds == [0.0, 0.0, 100.0, 100.0]
    assert any("BROKEN_SURVEY" in warning and "некорректными координатами" in warning for warning in imported.warnings)
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000

    project.plan = Plan(objects=[PlanObject(kind="tree", x=50, y=50, radius=1.6)])
    _, exported = DxfRoundTripWriter().create(project, source)
    assert b"nan" in exported.lower()
    reopened = EzdxfReader().read("nonfinite-export.dxf", exported)
    assert not any(feature["properties"].get("source_layer") == "BROKEN_SURVEY" for feature in reopened.geometry.feature_collection["features"])


def test_import_cap_blocks_truncated_physical_layer_from_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dense DXF may have a preview, but never an incomplete safe zone."""
    monkeypatch.setattr(dxf_adapters, "MAX_NORMALIZED_DXF_FEATURES", 3)
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("DENSE_BUILDINGS", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    for index in range(5):
        x = 10 + index * 12
        modelspace.add_lwpolyline([(x, 10), (x + 8, 10), (x + 8, 22), (x, 22)], close=True, dxfattribs={"layer": "DENSE_BUILDINGS"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("dense.dxf", stream.getvalue().encode())
    buildings = next(layer for layer in imported.layers if layer.source_name == "DENSE_BUILDINGS")
    project = Project(name="Плотная застройка", layers=imported.layers, source_geometry=imported.geometry)

    assert len(imported.geometry.feature_collection["features"]) == 3
    assert buildings.geometry_complete is False
    assert any("DENSE_BUILDINGS" in warning and "Неполный слой" in warning for warning in imported.warnings)
    with pytest.raises(ValueError, match="DENSE_BUILDINGS"):
        ShapelyGeometryEngine().calculate(project)

    buildings.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_coordinate_cap_blocks_an_overdetailed_physical_curve_from_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """One enormous polyline is also an incomplete constraint, not a safe gap."""
    monkeypatch.setattr(dxf_adapters, "MAX_NORMALIZED_COORDINATES_PER_FEATURE", 5)
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("DENSE_ROAD", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_lwpolyline([(index * 10, 50) for index in range(8)], dxfattribs={"layer": "DENSE_ROAD"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("detailed-road.dxf", stream.getvalue().encode())
    road = next(layer for layer in imported.layers if layer.source_name == "DENSE_ROAD")
    project = Project(name="Детальная дорога", layers=imported.layers, source_geometry=imported.geometry)

    assert road.geometry_complete is False
    assert not any(feature["properties"].get("source_layer") == "DENSE_ROAD" for feature in imported.geometry.feature_collection["features"])
    assert any("DENSE_ROAD" in warning and "Слишком детальная" in warning for warning in imported.warnings)
    with pytest.raises(ValueError, match="DENSE_ROAD"):
        ShapelyGeometryEngine().calculate(project)

    road.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_remote_cad_annotations_do_not_make_the_site_open_as_an_empty_dot() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("ANNOTATION", color=7)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_text("Сводный план", dxfattribs={"layer": "ANNOTATION", "insert": (1_000_000, 1_000_000)})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("site-with-remote-title.dxf", stream.getvalue().encode())

    assert imported.bounds == [0.0, 0.0, 100.0, 100.0]
    assert any(feature["properties"].get("source_text") == "Сводный план" for feature in imported.geometry.feature_collection["features"])


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
        document.modelspace().add_line((0, 0), (20 / metres_per_unit, 0), dxfattribs={"layer": "SOURCE_CONTEXT"})
        stream = StringIO()
        document.write(stream)
        source = stream.getvalue().encode()

        imported = EzdxfReader().read(f"unit-{unit_code}.dxf", source)
        project = Project(name=f"Единицы {unit_code}", plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]))
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
    document.modelspace().add_text("Главная аллея", dxfattribs={"layer": "ОЗЕЛЕНЕНИЕ", "insert": (10, 10)})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode("cp1251")
    project = Project(name="Кодовая страница", plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]))

    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode("cp1251")))

    assert b"\xc3\xeb\xe0\xe2\xed\xe0\xff \xe0\xeb\xeb\xe5\xff" in output
    assert reopened.encoding == "cp1251"
    assert reopened.header["$DWGCODEPAGE"] == "ANSI_1251"
    assert reopened.modelspace().query('TEXT[layer=="ОЗЕЛЕНЕНИЕ"]')[0].dxf.text == "Главная аллея"


def test_round_trip_respects_declared_cp1252_before_a_cyrillic_fallback() -> None:
    document = ezdxf.new("R2000", setup=True)
    document.encoding = "cp1252"
    document.header["$DWGCODEPAGE"] = "ANSI_1252"
    document.layers.add("NOTES", color=3)
    document.modelspace().add_text("Café", dxfattribs={"layer": "NOTES", "insert": (10, 10)})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode("cp1252")
    project = Project(name="Latin code page", plan=Plan(objects=[PlanObject(kind="tree", x=20, y=30, radius=1.6)]))

    imported = EzdxfReader().read("cp1252.dxf", source)
    _, output = DxfRoundTripWriter().create(project, source)
    reopened = ezdxf.read(StringIO(output.decode("cp1252")))

    assert any(feature["properties"].get("source_text") == "Café" for feature in imported.geometry.feature_collection["features"])
    assert b"Caf\xe9" in output
    assert reopened.encoding == "cp1252"
    assert reopened.header["$DWGCODEPAGE"] == "ANSI_1252"
    assert reopened.modelspace().query('TEXT[layer=="NOTES"]')[0].dxf.text == "Café"


def test_round_trip_never_merges_planting_into_a_source_layer_with_the_same_name() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.layers.add("GREEN_ATLAS_TREES", color=7)
    document.layers.add("GREEN_ATLAS_SHRUBS", color=8)
    document.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "GREEN_ATLAS_TREES"})
    stream = StringIO()
    document.write(stream)
    project = Project(name="Коллизия слоёв", plan=Plan(objects=[
        PlanObject(kind="tree", x=20, y=20, radius=1.6),
        PlanObject(kind="shrub", x=30, y=20, radius=0.65),
    ]))

    _, output = DxfRoundTripWriter().create(project, stream.getvalue().encode())
    reopened = ezdxf.read(StringIO(output.decode()))

    assert len(reopened.modelspace().query('LINE[layer=="GREEN_ATLAS_TREES"]')) == 1
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')) == 0
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES_2"]')) == 1
    assert len(reopened.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_SHRUBS_2"]')) == 1


def test_hatch_hole_is_preserved_for_real_site_geometry() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_HATCH", color=1)
    hatch = document.modelspace().add_hatch(dxfattribs={"layer": "SITE_HATCH"})
    hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(5, 5), (15, 5), (15, 15), (5, 15)], is_closed=True, flags=0)
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("site-with-hole.dxf", stream.getvalue().encode())
    project = Project(name="Участок", layers=imported.layers, source_geometry=imported.geometry)

    assert shape(imported.geometry.feature_collection["features"][0]["geometry"]).area == 300
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 300


def test_hatch_with_many_disconnected_islands_stays_fast_and_keeps_every_area() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_HATCH", color=1)
    hatch = document.modelspace().add_hatch(dxfattribs={"layer": "SITE_HATCH"})
    island_count = 900
    for index in range(island_count):
        x, y = (index % 45) * 3, (index // 45) * 3
        hatch.paths.add_polyline_path([(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)], is_closed=True, flags=1)
    stream = StringIO()
    document.write(stream)

    started = perf_counter()
    imported = EzdxfReader().read("many-islands.dxf", stream.getvalue().encode())
    geometry = shape(imported.geometry.feature_collection["features"][0]["geometry"])
    project = Project(name="Много участков HATCH", layers=imported.layers, source_geometry=imported.geometry)
    calculated = ShapelyGeometryEngine().calculate(project)

    assert geometry.geom_type == "MultiPolygon"
    assert len(geometry.geoms) == island_count
    assert geometry.area == island_count
    assert calculated.site_area_m2 == island_count
    assert perf_counter() - started < 5


def test_planar_cad_faces_remain_visible_and_can_be_mapped_as_buildings() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_FACE", color=2)
    document.layers.add("BUILDING_SOLID", color=3)
    document.layers.add("BUILDING_TRACE", color=4)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_3dface([(20, 20, 4), (30, 20, 4), (30, 30, 5), (20, 30, 5)], dxfattribs={"layer": "BUILDING_FACE"})
    modelspace.add_solid([(40, 20, 2), (50, 20, 2), (40, 30, 3), (50, 30, 3)], dxfattribs={"layer": "BUILDING_SOLID"})
    modelspace.add_trace([(60, 20, 0), (70, 20, 0), (60, 30, 1), (70, 30, 1)], dxfattribs={"layer": "BUILDING_TRACE"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("planar-cad.dxf", stream.getvalue().encode())
    for layer in imported.layers:
        layer.mapped_kind = layer.suggested_kind
    project = Project(name="Плоские CAD-примитивы", layers=imported.layers, source_geometry=imported.geometry)
    engine = ShapelyGeometryEngine()
    project.geometry = engine.calculate(project)

    entity_types = {feature["properties"].get("entity_type") for feature in imported.geometry.feature_collection["features"]}
    assert {"3DFACE", "SOLID", "TRACE"}.issubset(entity_types)
    for x in (25, 45, 65):
        with __import__("pytest").raises(ValueError, match="наружной стены"):
            engine.validate_position(project, x, 25, 1.6, "tree")


def test_text_only_block_on_a_building_named_layer_never_invents_a_building_setback() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_LABEL", color=2)
    document.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    label_block = document.blocks.new("BUILDING_TITLE")
    label_block.add_text("Корпус 3")
    document.modelspace().add_blockref("BUILDING_TITLE", (50, 50), dxfattribs={"layer": "BUILDING_LABEL"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("building-label-block.dxf", stream.getvalue().encode())
    project = Project(name="Подписанный план", layers=imported.layers, source_geometry=imported.geometry)
    engine = ShapelyGeometryEngine()
    project.geometry = engine.calculate(project)

    label = next(feature for feature in imported.geometry.feature_collection["features"] if feature["properties"].get("source_block") == "BUILDING_TITLE")
    assert label["properties"]["source_context_only"] is True
    engine.validate_position(project, 50, 50, 1.6, "tree")


def test_unprojectable_physical_cad_entity_blocks_a_false_safe_calculation() -> None:
    """A real CAD solid must not disappear when its layer means a building."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_VOLUME", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    solid = modelspace.add_3dsolid(dxfattribs={"layer": "BUILDING_VOLUME"})
    # ezdxf skips an ACIS container without payload when serialising. The
    # bytes are deliberately opaque here: this test exercises the safe path
    # for a valid-but-not-projectable real CAD entity.
    solid.sab = b"ACIS"
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("building-volume.dxf", stream.getvalue().encode())
    project = Project(name="Объём здания", layers=imported.layers, source_geometry=imported.geometry)
    engine = ShapelyGeometryEngine()

    building_layer = next(layer for layer in imported.layers if layer.source_name == "BUILDING_VOLUME")
    assert "3DSOLID" in building_layer.entity_types
    with pytest.raises(ValueError, match="BUILDING_VOLUME .*3DSOLID"):
        engine.calculate(project)

    # An explicit decision that this volume is non-physical drafting context is
    # still allowed. The service must not silently make that decision itself.
    building_layer.mapped_kind = LayerKind.IGNORE
    assert engine.calculate(project).site_area_m2 == 10_000


def test_unknown_cad_entity_cannot_disappear_from_a_physical_constraint_layer() -> None:
    """An unsupported CAD type is incomplete terrain, never free ground."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_SYMBOL", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_shape("BUILDING_MARK", (50, 50), dxfattribs={"layer": "BUILDING_SYMBOL"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("unknown-building-symbol.dxf", stream.getvalue().encode())
    project = Project(name="Неизвестный символ здания", layers=imported.layers, source_geometry=imported.geometry)
    layer = next(item for item in imported.layers if item.source_name == "BUILDING_SYMBOL")

    assert layer.entity_types == {"SHAPE": 1}
    assert layer.geometry_complete is False
    assert any("SHAPE" in warning and "физическим ограничением" in warning for warning in imported.warnings)
    with pytest.raises(ValueError, match="BUILDING_SYMBOL"):
        ShapelyGeometryEngine().calculate(project)

    layer.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_unknown_cad_component_inside_a_block_cannot_disappear_from_its_layer() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BLOCK_ANNOTATION", color=2)
    document.layers.add("BUILDING_SYMBOL", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    block = document.blocks.new("UNKNOWN_BUILDING_COMPONENT")
    block.add_shape("BUILDING_MARK", (0, 0), dxfattribs={"layer": "BUILDING_SYMBOL"})
    modelspace.add_blockref("UNKNOWN_BUILDING_COMPONENT", (50, 50), dxfattribs={"layer": "BLOCK_ANNOTATION"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("unknown-block-component.dxf", stream.getvalue().encode())
    project = Project(name="Неизвестный компонент блока", layers=imported.layers, source_geometry=imported.geometry)
    layer = next(item for item in imported.layers if item.source_name == "BUILDING_SYMBOL")

    assert layer.entity_types == {"SHAPE": 1}
    assert layer.geometry_complete is False
    with pytest.raises(ValueError, match="BUILDING_SYMBOL"):
        ShapelyGeometryEngine().calculate(project)

    layer.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_failed_normalization_of_a_supported_physical_entity_blocks_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A parser failure is an unknown obstacle, not a zero-sized one."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_FOOTPRINT", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_circle((50, 50), 10, dxfattribs={"layer": "BUILDING_FOOTPRINT"})
    stream = StringIO()
    document.write(stream)

    original = dxf_adapters._entity_geometry

    def reject_circle(entity: object, factor: float):
        return None if getattr(entity, "dxftype")() == "CIRCLE" else original(entity, factor)

    monkeypatch.setattr(dxf_adapters, "_entity_geometry", reject_circle)
    imported = EzdxfReader().read("normalization-failure.dxf", stream.getvalue().encode())
    project = Project(name="Непрочитанный контур", layers=imported.layers, source_geometry=imported.geometry)
    layer = next(item for item in imported.layers if item.source_name == "BUILDING_FOOTPRINT")

    assert layer.geometry_complete is False
    assert any("BUILDING_FOOTPRINT" in warning and "не удалось прочитать" in warning for warning in imported.warnings)
    with pytest.raises(ValueError, match="BUILDING_FOOTPRINT"):
        ShapelyGeometryEngine().calculate(project)

    layer.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_unreadable_block_with_a_physical_component_keeps_the_component_layer_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BLOCK_ANNOTATION", color=2)
    document.layers.add("BUILDING_EMBEDDED", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    block = document.blocks.new("FAILED_BUILDING_COMPONENT")
    block.add_circle((0, 0), 8, dxfattribs={"layer": "BUILDING_EMBEDDED"})
    modelspace.add_blockref("FAILED_BUILDING_COMPONENT", (50, 50), dxfattribs={"layer": "BLOCK_ANNOTATION"})
    stream = StringIO()
    document.write(stream)

    monkeypatch.setattr(dxf_adapters, "_flatten_insert_components", lambda *_args, **_kwargs: [])
    imported = EzdxfReader().read("unreadable-building-block.dxf", stream.getvalue().encode())
    project = Project(name="Непрочитанный блок здания", layers=imported.layers, source_geometry=imported.geometry)
    layer = next(item for item in imported.layers if item.source_name == "BUILDING_EMBEDDED")

    assert layer.entity_types == {"CIRCLE": 1}
    assert layer.geometry_complete is False
    with pytest.raises(ValueError, match="BUILDING_EMBEDDED"):
        ShapelyGeometryEngine().calculate(project)

    layer.mapped_kind = LayerKind.IGNORE
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == 10_000


def test_repeated_block_reuses_its_definition_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_SYMBOL", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (200, 0), (200, 200), (0, 200)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    block = document.blocks.new("REPEATED_BUILDING")
    block.add_circle((0, 0), 2, dxfattribs={"layer": "BUILDING_SYMBOL"})
    for index in range(24):
        modelspace.add_blockref("REPEATED_BUILDING", (10 + index * 7, 30), dxfattribs={"layer": "SITE_BORDER"})
    stream = StringIO()
    document.write(stream)

    original = dxf_adapters._block_definition_components
    calls = 0

    def count_definition(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(dxf_adapters, "_block_definition_components", count_definition)
    imported = EzdxfReader().read("repeated-blocks.dxf", stream.getvalue().encode())

    assert calls == 1
    assert sum(feature["properties"].get("source_layer") == "BUILDING_SYMBOL" for feature in imported.geometry.feature_collection["features"]) == 24


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
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER", **ocs})
    modelspace.add_circle((40, 50, 0), 4, dxfattribs={"layer": "BUILDING_CIRCLE", **ocs})
    modelspace.add_solid([(55, 40, 0), (65, 40, 0), (55, 50, 0), (65, 50, 0)], dxfattribs={"layer": "BUILDING_SOLID", **ocs})
    modelspace.add_polyline2d([(10, 70), (90, 70)], dxfattribs={"layer": "ROAD_2D", **ocs})
    modelspace.add_arc((20, 20, 0), 5, 0, 90, dxfattribs={"layer": "SOURCE_ARC", **ocs})
    modelspace.add_text("граница", dxfattribs={"layer": "ANNOTATION", "insert": (12, 22, 0), **ocs})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("ocs-plan.dxf", stream.getvalue().encode())
    by_layer = {feature["properties"]["source_layer"]: feature for feature in imported.geometry.feature_collection["features"]}

    assert shape(by_layer["SITE_BORDER"]["geometry"]).bounds == (-100.0, 0.0, 0.0, 100.0)
    assert shape(by_layer["BUILDING_CIRCLE"]["geometry"]).centroid.x == pytest.approx(-40.0, abs=0.01)
    assert shape(by_layer["BUILDING_SOLID"]["geometry"]).centroid.coords[0] == pytest.approx((-60.0, 45.0), abs=0.01)
    assert by_layer["ROAD_2D"]["geometry"]["coordinates"] == [[-10.0, 70.0], [-90.0, 70.0]]
    assert by_layer["SOURCE_ARC"]["geometry"]["coordinates"][0] == [-25.0, 20.0]
    assert by_layer["ANNOTATION"]["geometry"]["coordinates"] == [-12.0, 22.0]

    project = Project(name="OCS план", layers=imported.layers, source_geometry=imported.geometry)
    project.geometry = ShapelyGeometryEngine().calculate(project)
    with pytest.raises(ValueError, match="наружной стены"):
        ShapelyGeometryEngine().validate_position(project, -40, 50, 1.6, "tree")


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
        feature for feature in imported.geometry.feature_collection["features"]
        if feature["properties"]["source_layer"] == "BUILDING"
    ]
    by_handle = {feature["properties"]["source_handle"]: feature["properties"] for feature in buildings}

    assert by_handle[str(with_height.dxf.handle)]["source_extrusion_height_m"] == 12
    assert by_handle[str(with_height.dxf.handle)]["source_base_elevation_m"] == 1.5
    assert "source_extrusion_height_m" not in next(
        properties for handle, properties in by_handle.items() if handle != str(with_height.dxf.handle)
    )


def test_invalid_mapped_polygon_blocks_calculation_instead_of_crashing_geos() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_BAD", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_lwpolyline([(20, 20), (40, 40), (20, 40), (40, 20)], close=True, dxfattribs={"layer": "BUILDING_BAD"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("bad-building.dxf", stream.getvalue().encode())
    for layer in imported.layers:
        layer.mapped_kind = layer.suggested_kind
    project = Project(name="Некорректный контур", layers=imported.layers, source_geometry=imported.geometry)

    with __import__("pytest").raises(ValueError, match="BUILDING_BAD"):
        ShapelyGeometryEngine().calculate(project)
    assert any(feature["properties"].get("source_layer") == "BUILDING_BAD" for feature in project.source_geometry.feature_collection["features"])


def test_bulged_polyline_is_flattened_without_a_false_straight_boundary() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.modelspace().add_lwpolyline([(0, 0, 1), (20, 0, 0), (20, 20, 0), (0, 20, 0)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("bulged.dxf", stream.getvalue().encode())
    ring = imported.geometry.feature_collection["features"][0]["geometry"]["coordinates"][0]

    assert len(ring) > 5
    assert any(point[1] < 0 for point in ring)


def test_closed_ellipse_can_be_a_boundary_but_open_arc_cannot() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_ELLIPSE", color=1)
    document.layers.add("SITE_ARC", color=2)
    document.modelspace().add_ellipse((30, 30), (20, 0), ratio=0.5, dxfattribs={"layer": "SITE_ELLIPSE"})
    document.modelspace().add_arc((70, 30), 20, 0, 180, dxfattribs={"layer": "SITE_ARC"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("curves.dxf", stream.getvalue().encode())
    by_layer = {feature["properties"]["source_layer"]: feature for feature in imported.geometry.feature_collection["features"]}

    assert by_layer["SITE_ELLIPSE"]["geometry"]["type"] == "Polygon"
    assert by_layer["SITE_ARC"]["geometry"]["type"] == "LineString"
    assert next(layer for layer in imported.layers if layer.source_name == "SITE_ELLIPSE").suggested_kind == LayerKind.SITE_BORDER
    assert next(layer for layer in imported.layers if layer.source_name == "SITE_ARC").suggested_kind == LayerKind.IGNORE


def test_wide_road_polyline_uses_its_ground_footprint_for_clearance() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("ROAD", color=2)
    document.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    document.modelspace().add_lwpolyline([(10, 50), (90, 50)], dxfattribs={"layer": "ROAD", "const_width": 8})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("wide-road.dxf", stream.getvalue().encode())
    project = Project(name="Широкая дорога", layers=imported.layers, source_geometry=imported.geometry)
    project.geometry = ShapelyGeometryEngine().calculate(project)

    with __import__("pytest").raises(ValueError, match="проезжей части"):
        ShapelyGeometryEngine().validate_position(project, 50, 55.5, 1.6, "tree")


def test_stricter_tree_map_cue_does_not_false_block_a_shrub() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_lwpolyline([(20, 20), (30, 20), (30, 30), (20, 30)], close=True, dxfattribs={"layer": "BUILDING"})
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read("plant-kind-clearance.dxf", stream.getvalue().encode())
    project = Project(name="Разные отступы", layers=imported.layers, source_geometry=imported.geometry)
    engine = ShapelyGeometryEngine()
    project.geometry = engine.calculate(project)

    # The point is on the 5 m tree threshold and outside the 1.5 m shrub
    # threshold. It must not inherit the tree prohibition merely because the
    # map has one conservative cue. PP-743 measures both values to the axis.
    engine.validate_position(project, 35, 25, 0.65, "shrub")
    engine.validate_position(project, 35, 25, 1.6, "tree")
    with __import__("pytest").raises(ValueError, match="наружной стены"):
        engine.validate_position(project, 34.99, 25, 1.6, "tree")


def test_empty_common_tree_cue_does_not_block_a_shrub_only_site() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (10, 0), (10, 20), (0, 20)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_lwpolyline([(4.5, 0), (5.5, 0), (5.5, 20), (4.5, 20)], close=True, dxfattribs={"layer": "BUILDING"})
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read("shrub-only-site.dxf", stream.getvalue().encode())
    project = Project(name="Узкий участок", layers=imported.layers, source_geometry=imported.geometry)
    engine = ShapelyGeometryEngine()

    project.geometry = engine.calculate(project)

    assert project.geometry.allowed_area_m2 == 0
    assert not any(feature["properties"].get("kind") == "allowed" for feature in project.geometry.feature_collection["features"])
    engine.validate_position(project, 1.5, 10, 0.65, "shrub")


def test_block_array_expands_explicit_constraint_components() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    block = document.blocks.new("BUILDING_MODULE")
    block.add_lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)], close=True, dxfattribs={"layer": "BUILDING"})
    insert = document.modelspace().add_blockref("BUILDING_MODULE", (20, 20))
    insert.dxf.row_count = 2
    insert.dxf.column_count = 2
    insert.dxf.row_spacing = 20
    insert.dxf.column_spacing = 20
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("array.dxf", stream.getvalue().encode())
    buildings = [item for item in imported.geometry.feature_collection["features"] if item["properties"].get("source_layer") == "BUILDING"]

    assert len(buildings) == 4
    assert {item["properties"].get("source_block_instance") for item in buildings} == {1, 2, 3, 4}


def test_nested_block_keeps_an_explicit_constraint_layer_after_transform() -> None:
    """A building inside a reusable symbol must not inherit its legend layer."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("SYMBOLS", color=3)
    document.layers.add("BUILDING", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    building = document.blocks.new("BUILDING_CORE")
    building.add_lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)], close=True, dxfattribs={"layer": "BUILDING"})
    compound = document.blocks.new("COMPOUND_SYMBOL")
    compound.add_blockref("BUILDING_CORE", (10, 0), dxfattribs={"layer": "0"})
    modelspace.add_blockref("COMPOUND_SYMBOL", (20, 20), dxfattribs={"layer": "SYMBOLS"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("nested-building.dxf", stream.getvalue().encode())
    project = Project(name="Вложенный символ", layers=imported.layers, source_geometry=imported.geometry)
    buildings = [
        feature for feature in imported.geometry.feature_collection["features"]
        if feature["properties"].get("source_layer") == "BUILDING"
    ]

    assert len(buildings) == 1
    assert buildings[0]["properties"]["source_block"] == "COMPOUND_SYMBOL"
    project.geometry = ShapelyGeometryEngine().calculate(project)
    with __import__("pytest").raises(ValueError, match="наружной стены"):
        ShapelyGeometryEngine().validate_position(project, 32, 22, 1.6, "tree")


def test_recursive_block_is_preserved_but_blocks_calculation_without_expansion() -> None:
    """A corrupt cyclic block graph must never become a false empty site."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    first = document.blocks.new("LOOP_A")
    second = document.blocks.new("LOOP_B")
    first.add_blockref("LOOP_B", (0, 0), dxfattribs={"layer": "0"})
    second.add_blockref("LOOP_A", (0, 0), dxfattribs={"layer": "BUILDING"})
    document.modelspace().add_blockref("LOOP_A", (30, 30), dxfattribs={"layer": "BUILDING"})
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("recursive-block.dxf", stream.getvalue().encode())
    project = Project(name="Циклический блок", layers=imported.layers, source_geometry=imported.geometry)

    assert any(feature["properties"].get("source_analysis_blocking") for feature in imported.geometry.feature_collection["features"])
    assert any("Крупных блоковых массивов" in warning for warning in imported.warnings)
    with __import__("pytest").raises(ValueError, match="нельзя безопасно развернуть"):
        ShapelyGeometryEngine().calculate(project)


def test_oversized_block_array_blocks_calculation_instead_of_claiming_safe_space() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING", color=2)
    document.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    block = document.blocks.new("DENSE_MODULE")
    block.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True, dxfattribs={"layer": "BUILDING"})
    insert = document.modelspace().add_blockref("DENSE_MODULE", (10, 10))
    insert.dxf.row_count = 150
    insert.dxf.column_count = 150
    insert.dxf.row_spacing = 2
    insert.dxf.column_spacing = 2
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("too-dense.dxf", stream.getvalue().encode())
    project = Project(name="Слишком большой массив", layers=imported.layers, source_geometry=imported.geometry)

    assert any("Крупных блоковых массивов" in warning for warning in imported.warnings)
    assert any(item["properties"].get("source_analysis_blocking") for item in imported.geometry.feature_collection["features"])
    with __import__("pytest").raises(ValueError, match="нельзя безопасно развернуть"):
        ShapelyGeometryEngine().calculate(project)


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
    project = Project(name="Большой DXF", layers=imported.layers, source_geometry=imported.geometry, geometry_version=1)
    page = IndexedGeometryQuery().query(project, tuple(imported.bounds or [0, 0, 1, 1]), 3)

    assert imported.entity_count == 4859
    assert imported.bounds is not None and imported.bounds[2] - imported.bounds[0] > 4000
    assert any(feature["properties"].get("source_layer") == "OSM_ROAD_LOCAL" for feature in page.feature_collection["features"])


def test_dense_moscow_dxf_keeps_pedestrian_technical_and_existing_green_context() -> None:
    imported = EzdxfReader().read(KITAY_GOROD_DXF.name, KITAY_GOROD_DXF.read_bytes())
    project = Project(name="Китай-город", layers=imported.layers, source_geometry=imported.geometry, geometry_version=1)
    page = IndexedGeometryQuery().query(project, tuple(imported.bounds or [0, 0, 1, 1]), 2)
    source_layers = {feature["properties"].get("source_layer") for feature in page.feature_collection["features"]}

    assert imported.entity_count == 9020
    assert imported.bounds is not None and imported.bounds[2] - imported.bounds[0] > 1_500
    assert {
        "OSM_BUILDING",
        "OSM_PATH",
        "OSM_GREEN_EXISTING",
        "OSM_BARRIER",
        "OSM_ROAD_LOCAL",
    } <= source_layers


def test_viewport_index_is_reused_after_non_geometry_project_changes() -> None:
    imported = EzdxfReader().read(LARGE_DXF.name, LARGE_DXF.read_bytes())
    project = Project(name="Кэш карты", layers=imported.layers, source_geometry=imported.geometry, geometry_version=1)
    query = IndexedGeometryQuery()
    extent = tuple(imported.bounds or [0, 0, 1, 1])

    query.query(project, extent, 1)
    initial_index = query._indexes[project.id]
    project.state_version += 1
    query.query(project, extent, 1)

    assert query._indexes[project.id] is initial_index


def test_viewport_index_can_be_released_when_a_project_leaves_the_session() -> None:
    imported = EzdxfReader().read(LARGE_DXF.name, LARGE_DXF.read_bytes())
    project = Project(name="Освобождаем карту", layers=imported.layers, source_geometry=imported.geometry, geometry_version=1)
    query = IndexedGeometryQuery()

    query.query(project, tuple(imported.bounds or [0, 0, 1, 1]), 1)
    assert query.cached_project_count == 1
    assert project.id in query._indexes

    query.discard(project.id)

    assert query.cached_project_count == 0
    assert project.id not in query._indexes


def test_viewport_simplification_uses_a_precise_zoom_key_and_a_bounded_lru() -> None:
    # A coarse overview must never leak into a closer zoom merely because both
    # resolutions were formerly called "mid". At the same time, pan/zoom over
    # an arbitrary CAD file must not retain unbounded simplified GeoJSON.
    features = [{
        "type": "Feature",
        "id": "curved-road",
        "properties": {"kind": "road", "source_layer": "ROAD"},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0.5], [2, 0], [3, 0.5], [4, 0]]},
    }]
    features.extend({
        "type": "Feature",
        "id": f"survey-{index}",
        "properties": {"kind": "source", "source_layer": "SURVEY"},
        "geometry": {"type": "Point", "coordinates": [10 + index, 0]},
    } for index in range(32))
    project = Project(
        name="Кэш масштаба",
        source_geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": features}),
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
    features.extend(point(f"survey-{index}", "source", 10 + index % 80, 10 + index // 80) for index in range(10_000))
    project = Project(
        name="Плотная топосъёмка",
        source_geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": features}),
        geometry_version=1,
    )

    page = IndexedGeometryQuery(max_features=5).query(project, (0, 0, 150, 150), 1)
    returned = page.feature_collection["features"]
    metadata = page.feature_collection["metadata"]

    assert [feature["id"] for feature in returned] == ["border", "building", "road", "utility", "green"]
    assert metadata["truncated"] is True
    assert metadata["total_matches"] == 10_005
    assert metadata["omitted_by_kind"] == {"source": 10_000}


def test_dense_arbitrary_dxf_is_bounded_through_import_viewport_storage_and_export(tmp_path: Path) -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("TOPOGRAPHY_2026", color=8)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (1200, 0), (1200, 400), (0, 400)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    for index in range(20_000):
        modelspace.add_point(((index % 250) * 4.5 + 20, (index // 250) * 4.5 + 20), dxfattribs={"layer": "TOPOGRAPHY_2026"})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()

    started = perf_counter()
    imported = EzdxfReader().read("dense-survey.dxf", source)
    project = Project(name="Плотная топосъёмка", layers=imported.layers, source_geometry=imported.geometry, geometry_version=1)
    project.geometry = ShapelyGeometryEngine().calculate(project)
    page = IndexedGeometryQuery(max_features=512).query(project, tuple(imported.bounds or [0, 0, 1, 1]), 1)
    repository = SqliteProjectRepository(tmp_path / "dense.sqlite3")
    stored = repository.create(project)
    repository.save(stored, source=source)
    restored = repository.get(stored.id)
    restored.plan = Plan(objects=[PlanObject(kind="tree", x=20, y=20, radius=1.6)])
    _, output = DxfRoundTripWriter().create(restored, repository.get_source(stored.id) or b"")

    assert page.feature_collection["metadata"]["returned_features"] == 512
    assert page.feature_collection["metadata"]["truncated"] is True
    assert len(ezdxf.read(StringIO(output.decode())).modelspace().query("POINT")) == 20_000
    assert perf_counter() - started < 30


def test_large_constraint_union_reports_completed_batches_then_an_honest_atomic_stage() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_GRID", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (1200, 0), (1200, 400), (0, 400)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    for index in range(650):
        x, y = (index % 65) * 16 + 20, (index // 65) * 25 + 20
        modelspace.add_lwpolyline([(x, y), (x + 5, y), (x + 5, y + 5), (x, y + 5)], close=True, dxfattribs={"layer": "BUILDING_GRID"})
    stream = StringIO()
    document.write(stream)
    imported = EzdxfReader().read("large-constraints.dxf", stream.getvalue().encode())
    project = Project(name="Большой набор ограничений", layers=imported.layers, source_geometry=imported.geometry)
    updates = []

    ShapelyGeometryEngine().calculate(project, updates.append)

    union_updates = [item for item in updates if item.stage.startswith("Объединяем объекты:")]
    final_union = next(item for item in updates if item.stage.startswith("Сводим контуры:"))
    numeric_progress = [item.fraction for item in updates if item.fraction is not None]
    assert [item.processed for item in union_updates] == [320, 640, 650]
    assert final_union.fraction is None
    assert final_union.processed is None
    assert numeric_progress == sorted(numeric_progress)
