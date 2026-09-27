from io import StringIO

import ezdxf
import pytest
from ezdxf.entities import Insert
from shapely.geometry import shape

import app.dxf_import.adapters as dxf_adapters
from app.contracts import LayerKind, Project
from app.dxf_import.adapters import EzdxfReader
from app.geometry.adapters import ShapelyGeometryEngine


def source_bytes(document: ezdxf.document.Drawing) -> bytes:
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode(document.output_encoding)


def test_missing_layer_keeps_source_geometry_and_explicit_style() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    source_layer = "BUILDING_Отсутствующий"
    polygon = document.modelspace().add_lwpolyline(
        [(0, 0), (12, 0), (12, 8), (0, 8)],
        close=True,
        dxfattribs={"layer": source_layer, "color": 1, "lineweight": 0},
    )
    polygon.rgb = (12, 34, 56)
    content = source_bytes(document)

    imported = EzdxfReader().read("missing-layer.dxf", content)
    feature = imported.geometry.feature_collection["features"][0]
    layer = next(item for item in imported.layers if item.source_name == source_layer)

    assert shape(feature["geometry"]).area == 96
    assert feature["properties"]["source_layer"] == source_layer
    assert feature["properties"]["source_handle"] == polygon.dxf.handle
    assert feature["properties"]["source_layer_definition_missing"] is True
    assert feature["properties"]["source_color"] == "#0C2238"
    assert feature["properties"]["source_lineweight_mm"] == 0
    assert layer.geometry_complete
    assert layer.linetype == "CONTINUOUS"
    assert sum("нет определений слоёв" in warning for warning in imported.warnings) == 1
    assert source_layer in imported.warnings[0]
    assert source_layer not in ezdxf.read(StringIO(content.decode())).layers


def test_nested_missing_layer_preserves_transforms_and_inherited_definition() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("SYMBOL", color=3)
    child = document.blocks.new("CHILD")
    child.add_line((0, 0), (10, 0), dxfattribs={"layer": "MISSING_NETWORK"})
    parent = document.blocks.new("PARENT")
    parent.add_blockref("CHILD", (5, 0))
    document.modelspace().add_blockref(
        "PARENT", (100, 50), dxfattribs={"layer": "SYMBOL"}
    )

    imported = EzdxfReader().read("nested-missing.dxf", source_bytes(document))
    feature = imported.geometry.feature_collection["features"][0]

    assert feature["geometry"]["coordinates"] == [[105, 50], [115, 50]]
    assert feature["properties"]["source_layer"] == "MISSING_NETWORK"
    assert feature["properties"]["source_block_parent_layer"] == "SYMBOL"
    assert feature["properties"]["source_layer_definition_missing"] is True
    assert {layer.source_name for layer in imported.layers} == {
        "SYMBOL",
        "MISSING_NETWORK",
    }


def test_layer_and_entity_share_defined_style_without_false_missing_diagnosis() -> None:
    document = ezdxf.new("R2013")
    layer = document.layers.add("ROAD", color=3)
    layer.dxf.lineweight = 30
    layer.rgb = (24, 48, 96)
    document.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "ROAD"})

    imported = EzdxfReader().read("defined-layer.dxf", source_bytes(document))
    properties = imported.geometry.feature_collection["features"][0]["properties"]

    assert properties["source_color"] == imported.layers[0].color == "#183060"
    assert properties["source_lineweight_mm"] == imported.layers[0].lineweight_mm == 0.3
    assert "source_layer_definition_missing" not in properties
    assert not any("нет определений слоёв" in warning for warning in imported.warnings)


def test_mpolygon_keeps_holes_islands_ocs_offset_and_units() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.MM
    document.layers.add("SITE_BORDER")
    polygon = document.modelspace().add_mpolygon(
        dxfattribs={
            "layer": "SITE_BORDER",
            "extrusion": (0, 0, -1),
            "offset_vector": (5000, 7000),
        }
    )
    for ring in (
        [(0, 0), (20000, 0), (20000, 20000), (0, 20000)],
        [(5000, 5000), (15000, 5000), (15000, 15000), (5000, 15000)],
        [(30000, 0), (35000, 0), (35000, 5000), (30000, 5000)],
    ):
        # MPOLYGON nesting is geometric; even identical flags must retain holes.
        polygon.paths.add_polyline_path(ring, is_closed=True, flags=1)

    imported = EzdxfReader().read("mpolygon.dxf", source_bytes(document))
    feature = imported.geometry.feature_collection["features"][0]
    geometry = shape(feature["geometry"])

    assert geometry.geom_type == "MultiPolygon"
    assert geometry.area == pytest.approx(325)
    assert geometry.bounds == pytest.approx((-40, 7, -5, 27))
    assert sum(len(part.interiors) for part in geometry.geoms) == 1
    assert feature["properties"]["entity_type"] == "MPOLYGON"
    assert feature["properties"]["source_handle"] == polygon.dxf.handle
    assert imported.layers[0].geometry_complete
    project = Project(
        name="MPOLYGON", layers=imported.layers, source_geometry=imported.geometry
    )
    assert ShapelyGeometryEngine().calculate(project).site_area_m2 == pytest.approx(325)


def test_mpolygon_with_invalid_second_ring_cannot_become_a_partial_safe_site() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER")
    polygon = document.modelspace().add_mpolygon(dxfattribs={"layer": "SITE_BORDER"})
    polygon.paths.add_polyline_path(
        [(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True
    )
    polygon.paths.add_polyline_path(
        [(5, 5), (15, 15), (5, 15), (15, 5)], is_closed=True
    )

    imported = EzdxfReader().read("invalid-mpolygon.dxf", source_bytes(document))

    assert imported.geometry.feature_collection["features"] == []
    assert not imported.layers[0].geometry_complete
    assert any("не удалось прочитать" in warning for warning in imported.warnings)
    # A later explicit physical mapping must retain the incomplete-source gate.
    imported.layers[0].mapped_kind = LayerKind.SITE_BORDER
    project = Project(
        name="Invalid area", layers=imported.layers, source_geometry=imported.geometry
    )
    with pytest.raises(ValueError, match="часть объектов"):
        ShapelyGeometryEngine().calculate(project)


def test_insert_reuse_keeps_distinct_instances_and_avoids_second_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING", color=1)
    block = document.blocks.new("FOOTPRINT")
    block.add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True)
    document.modelspace().add_blockref(
        "FOOTPRINT", (10, 20), dxfattribs={"layer": "BUILDING"}
    )
    document.modelspace().add_blockref(
        "FOOTPRINT",
        (100, 200),
        dxfattribs={"layer": "BUILDING", "xscale": 2, "yscale": 3},
    )
    original = Insert.virtual_entities
    expansions = 0

    def counted(self: Insert, *args: object, **kwargs: object):
        nonlocal expansions
        expansions += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Insert, "virtual_entities", counted)
    imported = EzdxfReader().read("two-instances.dxf", source_bytes(document))
    geometries = [
        shape(feature["geometry"])
        for feature in imported.geometry.feature_collection["features"]
    ]

    assert expansions == 2
    assert [geometry.bounds for geometry in geometries] == [
        (10, 20, 15, 24),
        (100, 200, 110, 212),
    ]
    assert [geometry.area for geometry in geometries] == [20, 120]


def test_failed_block_expansion_keeps_anchor_and_incomplete_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING")
    block = document.blocks.new("UNREADABLE")
    block.add_circle((0, 0), 5)
    document.modelspace().add_blockref(
        "UNREADABLE", (20, 30), dxfattribs={"layer": "BUILDING"}
    )
    monkeypatch.setattr(dxf_adapters, "_insert_instance_components", lambda *_: [])

    imported = EzdxfReader().read("failed-transform.dxf", source_bytes(document))

    feature = imported.geometry.feature_collection["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [20, 30]}
    assert feature["properties"]["block_rendered"] is False
    assert not imported.layers[0].geometry_complete
    project = Project(
        name="Unreadable block",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    with pytest.raises(ValueError, match="часть объектов"):
        ShapelyGeometryEngine().calculate(project)


def test_mpolygon_bulge_and_array_instances_preserve_curved_area() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING")
    block = document.blocks.new("ROUND_FOOTPRINT")
    polygon = block.add_mpolygon()
    polygon.paths.add_polyline_path([(0, 0, 1), (10, 0, 1)], is_closed=True)
    reference = document.modelspace().add_blockref(
        "ROUND_FOOTPRINT", (10, 20), dxfattribs={"layer": "BUILDING"}
    )
    reference.grid(size=(1, 2), spacing=(0, 30))

    imported = EzdxfReader().read("curved-mpolygon-array.dxf", source_bytes(document))
    geometries = [
        shape(feature["geometry"])
        for feature in imported.geometry.feature_collection["features"]
    ]

    assert len(geometries) == 2
    assert [geometry.bounds for geometry in geometries] == [
        (10, 15, 20, 25),
        (40, 15, 50, 25),
    ]
    assert all(
        geometry.area == pytest.approx(78.54, abs=0.2) for geometry in geometries
    )
    assert imported.layers[0].geometry_complete
