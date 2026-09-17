from io import StringIO

import ezdxf
import pytest
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity


def content(doc) -> bytes:
    stream = StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf8")


def test_large_survey_insert_preserves_every_line_and_transform():
    doc = ezdxf.new("R2018")
    doc.units = 6
    block = doc.blocks.new("Survey")
    for index in range(20_001):
        block.add_line((index, 0), (index, 1))
    doc.modelspace().add_blockref("Survey", (3, 5), dxfattribs={"xscale": 2})

    result = EzdxfReader().read("survey.dxf", content(doc))

    features = result.geometry.feature_collection["features"]
    assert len(features) == 1
    feature = features[0]
    assert not feature["properties"].get("source_analysis_blocking")
    geometries = feature["geometry"]["geometries"]
    assert len(geometries) == 20_001
    assert geometries[0]["coordinates"] == [[3, 5], [3, 6]]
    assert geometries[-1]["coordinates"] == [[40_003, 5], [40_003, 6]]
    assert all(layer.geometry_complete for layer in result.layers)


def test_ordinary_block_exceeding_source_capacity_refuses_partial_import():
    doc = ezdxf.new("R2018")
    doc.units = 6
    block = doc.blocks.new("Survey")
    for index in range(11):
        block.add_line((index, 0), (index, 1))
    doc.modelspace().add_blockref("Survey", (0, 0))
    reader = EzdxfReader(capacity=SourceGeometryCapacity(max_features=10))
    with pytest.raises(SourceCapacityExceeded, match="11 > 10"):
        reader.read("survey.dxf", content(doc))


def test_nested_array_product_is_rejected_before_virtual_expansion():
    doc = ezdxf.new("R2018")
    doc.units = 6
    leaf = doc.blocks.new("Leaf")
    leaf.add_line((0, 0), (1, 0))
    parent = doc.blocks.new("Parent")
    parent.add_blockref("Leaf", (0, 0), dxfattribs={"row_count": 150, "row_spacing": 2})
    doc.modelspace().add_blockref(
        "Parent", (0, 0), dxfattribs={"column_count": 150, "column_spacing": 2}
    )
    result = EzdxfReader().read("array.dxf", content(doc))
    assert any(
        f["properties"].get("source_analysis_blocking")
        for f in result.geometry.feature_collection["features"]
    )


def test_real_multileader_is_rendered_as_annotation_without_incomplete_layer():
    doc = ezdxf.new("R2018")
    doc.units = 6
    leader = doc.modelspace().add_multileader_mtext("Standard")
    leader.set_content("Кабель", char_height=0.25)
    leader.add_leader_line(ConnectionSide.left, [Vec2(1, 1), Vec2(2, 2)])
    leader.build(insert=Vec2(4, 4))
    result = EzdxfReader().read("annotation.dxf", content(doc))
    feature = result.geometry.feature_collection["features"][0]
    assert feature["properties"]["entity_type"] == "MULTILEADER"
    assert feature["properties"]["source_context_only"] is True
    assert feature["geometry"]["geometries"]
    assert all(layer.geometry_complete for layer in result.layers)
    assert not any("MULTILEADER:" in warning for warning in result.warnings)


def test_attribute_definition_does_not_make_symbol_physical_geometry_incomplete():
    doc = ezdxf.new("R2018")
    doc.units = 6
    block = doc.blocks.new("Symbol")
    block.add_circle((0, 0), 1)
    block.add_attdef("LABEL", (2, 0), text="Колодец")
    doc.modelspace().add_blockref("Symbol", (10, 20))
    result = EzdxfReader().read("attributes.dxf", content(doc))
    assert all(layer.geometry_complete for layer in result.layers)
    assert not any("ATTDEF:" in warning for warning in result.warnings)
    assert result.geometry.feature_collection["features"]
