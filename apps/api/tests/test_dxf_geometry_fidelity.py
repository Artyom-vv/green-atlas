"""CAD semantics that must survive conversion into calculation geometry."""

from io import StringIO

import ezdxf
import pytest
from shapely.geometry import shape

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.contracts import DxfImportResult
from app.geometry.adapters import ShapelyGeometryEngine
from app.projects.contracts import Project


def import_document(document: ezdxf.document.Drawing) -> DxfImportResult:
    stream = StringIO()
    document.write(stream)
    return EzdxfReader().read("geometry.dxf", stream.getvalue().encode())


@pytest.mark.parametrize("style,area,holes", [(0, 93, 1), (1, 89, 1), (2, 125, 0)])
def test_hatch_fill_styles_preserve_holes_and_disconnected_exteriors(
    style: int, area: float, holes: int,
) -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    hatch = document.modelspace().add_hatch(dxfattribs={"hatch_style": style})
    # Exterior, hole, island inside the hole, then another independent exterior.
    for low, high, flags in [(0, 10, 1), (2, 8, 16), (4, 6, 0), (20, 25, 1)]:
        hatch.paths.add_polyline_path(
            [(low, low), (high, low), (high, high), (low, high)],
            is_closed=True, flags=flags,
        )

    imported = import_document(document)
    geometry = shape(imported.geometry.feature_collection["features"][0]["geometry"])

    assert geometry.is_valid
    assert geometry.area == area
    assert sum(len(part.interiors) for part in geometry.geoms) == holes
    assert geometry.bounds == (0, 0, 25, 25)
    assert all(layer.geometry_complete for layer in imported.layers)
    assert hatch.dxf.hatch_style == style
    assert len(hatch.paths) == 4  # Source is not rewritten or stripped.


def test_invalid_hatch_ring_cannot_silently_become_a_partial_footprint() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    hatch = document.modelspace().add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], flags=1)
    hatch.paths.add_polyline_path([(5, 5), (15, 15), (5, 15), (15, 5)], flags=0)

    imported = import_document(document)

    assert not imported.geometry.feature_collection["features"]
    assert not imported.layers[0].geometry_complete
    assert any("не удалось прочитать" in warning for warning in imported.warnings)


@pytest.mark.parametrize("explicit_layer", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_partial_insert_skip_is_addressed_and_marks_effective_layer_incomplete(
    explicit_layer: bool, nested: bool,
) -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING")
    document.layers.add("CABLE")
    block = document.blocks.new("MIXED")
    block.add_line((0, 0), (10, 0))
    circle = block.add_circle(
        (5, 5), 0, dxfattribs={"layer": "CABLE" if explicit_layer else "0"},
    )
    if nested:
        parent = document.blocks.new("PARENT")
        parent.add_blockref("MIXED", (5, 5))
    insert = document.modelspace().add_blockref(
        "PARENT" if nested else "MIXED", (10, 20),
        dxfattribs={"layer": "BUILDING", "xscale": 2, "yscale": 1},
    )
    imported = import_document(document)
    layers = {layer.source_name: layer for layer in imported.layers}
    failed_layer = "CABLE" if explicit_layer else "BUILDING"

    assert not layers[failed_layer].geometry_complete
    assert imported.geometry.feature_collection["features"]  # Surviving line stays.
    assert any(
        f"#{circle.dxf.handle}" in warning
        and insert.dxf.handle in warning
        and failed_layer in warning
        and "Invalid radius" in warning
        for warning in imported.warnings
    )
    if explicit_layer:
        assert layers["BUILDING"].geometry_complete
        assert layers["CABLE"].object_count == 1
    # This test exercises the later completeness gate, not operator review.
    # Confirm the suggested roles explicitly instead of bypassing the product
    # contract inside the geometry engine.
    for layer in imported.layers:
        layer.mapping_confirmed = True
        layer.mapping_review_required = False
    project = Project(name="Incomplete", layers=imported.layers, source_geometry=imported.geometry)
    with pytest.raises(ValueError, match="часть объектов"):
        ShapelyGeometryEngine().calculate(project)


def test_failed_normalization_inside_partly_read_block_is_also_reported() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING")
    block = document.blocks.new("PARTIAL")
    block.add_line((0, 0), (10, 0))
    hatch = block.add_hatch()
    hatch.paths.add_polyline_path([(0, 0), (10, 10), (0, 10), (10, 0)], flags=1)
    document.modelspace().add_blockref("PARTIAL", (0, 0), dxfattribs={"layer": "BUILDING"})

    imported = import_document(document)

    assert not imported.layers[0].geometry_complete
    assert any(f"#{hatch.dxf.handle}" in warning for warning in imported.warnings)


def test_array_skip_addresses_each_instance_without_hiding_other_layers() -> None:
    document = ezdxf.new("R2013")
    document.units = ezdxf.units.M
    document.layers.add("BUILDING")
    document.layers.add("CABLE")
    block = document.blocks.new("MIXED")
    block.add_line((0, 0), (10, 0))
    circle = block.add_circle((5, 5), 0, dxfattribs={"layer": "CABLE"})
    insert = document.modelspace().add_blockref(
        "MIXED", (0, 0),
        dxfattribs={"layer": "BUILDING", "xscale": 2, "yscale": 1},
    )
    insert.grid(size=(1, 2), spacing=(0, 30))

    imported = import_document(document)
    layers = {layer.source_name: layer for layer in imported.layers}
    failures = [warning for warning in imported.warnings if f"#{circle.dxf.handle}" in warning]

    assert len(failures) == 2
    assert len(set(failures)) == 2
    assert layers["CABLE"].object_count == 2
    assert not layers["CABLE"].geometry_complete
    assert layers["BUILDING"].geometry_complete
    assert len(imported.geometry.feature_collection["features"]) == 2


@pytest.mark.parametrize("units,scale", [(ezdxf.units.M, 1), (ezdxf.units.MM, 1000)])
def test_hatch_style_survives_units_and_nested_insert_transform(units: int, scale: int) -> None:
    document = ezdxf.new("R2013")
    document.units = units
    block = document.blocks.new("FILLED")
    hatch = block.add_hatch(dxfattribs={"hatch_style": 2})
    for low, high, flags in [(0, 10, 1), (3, 7, 0)]:
        hatch.paths.add_polyline_path(
            [(low * scale, low * scale), (high * scale, low * scale),
             (high * scale, high * scale), (low * scale, high * scale)],
            flags=flags,
        )
    parent = document.blocks.new("NESTED")
    parent.add_blockref("FILLED", (0, 0), dxfattribs={"xscale": 2, "yscale": 3})
    document.modelspace().add_blockref(
        "NESTED", (100 * scale, 200 * scale), dxfattribs={"rotation": 90},
    )

    imported = import_document(document)
    geometry = shape(imported.geometry.feature_collection["features"][0]["geometry"])

    assert geometry.is_valid
    assert geometry.area == pytest.approx(600)
    assert geometry.bounds == pytest.approx((70, 200, 100, 220))
    assert all(layer.geometry_complete for layer in imported.layers)
