from io import BytesIO, StringIO

import ezdxf
import pytest
from ezdxf.entities.mleader import LeaderData
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.math import Vec2, Vec3
from ezdxf.render.mleader import ConnectionSide

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.mleader_compat import prepare_multileader_transforms


def annotation_block(*, dogleg_length=0.0):
    doc = ezdxf.new("R2018")
    doc.units = 6
    block = doc.blocks.new("AnnotatedSurvey")
    block.add_line((0, 0), (1, 0))
    builder = block.add_multileader_mtext("Standard")
    builder.set_content("Кабель", char_height=0.25)
    builder.add_leader_line(ConnectionSide.left, [Vec2(1, 1), Vec2(2, 2)])
    builder.build(insert=Vec2(4, 4))
    annotation = block.query("MULTILEADER")[0]
    annotation.dxf.has_dogleg = int(dogleg_length != 0)
    for data in annotation.context.leaders:
        data.dogleg_length = dogleg_length
        data.breaks = [Vec3(2, 3), Vec3(3, 4)]
        data.lines[0].breaks = [0, Vec3(1, 1), Vec3(2, 2)]
    block.add_line((0, 1), (1, 1))
    return doc, block, annotation


@pytest.mark.parametrize("scale", [1, 2, -2])
@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("null_direction", [False, True])
def test_zero_dogleg_keeps_annotation_and_adjacent_geometry(scale, rotation, null_direction):
    doc, block, annotation = annotation_block()
    source = annotation.context.leaders[0]
    if null_direction:
        source.dogleg_vector = Vec3()
    source_tags = TagCollector.dxftags(annotation)
    insert = doc.modelspace().add_blockref(
        block.name, (10, 20),
        dxfattribs={"xscale": scale, "yscale": abs(scale), "zscale": abs(scale),
                    "rotation": rotation},
    )
    matrix = insert.matrix44()
    sdk_transform = LeaderData.transform

    assert prepare_multileader_transforms(doc) == 1
    assert prepare_multileader_transforms(doc) == 0
    entities = list(insert.virtual_entities())

    assert [e.dxftype() for e in entities] == ["LINE", "MULTILEADER", "LINE"]
    assert entities[0].dxf.start.isclose(matrix.transform(Vec3(0, 0)))
    assert entities[-1].dxf.end.isclose(matrix.transform(Vec3(1, 1)))
    transformed = entities[1].context.leaders[0]
    assert transformed.dogleg_length == 0
    assert entities[1].dxf.has_dogleg == 0
    assert transformed.last_leader_point.isclose(matrix.transform(source.last_leader_point))
    direction = Vec3(1, 0) if null_direction else source.dogleg_vector
    assert transformed.dogleg_vector.isclose(matrix.transform_direction(direction).normalize())
    for actual, original in zip(transformed.breaks, source.breaks, strict=True):
        assert actual.isclose(matrix.transform(original))
    for actual, original in zip(transformed.lines[0].vertices, source.lines[0].vertices, strict=True):
        assert actual.isclose(matrix.transform(original))
    assert transformed.lines[0].breaks[0] == 0
    assert transformed.lines[0].breaks[1].isclose(matrix.transform(source.lines[0].breaks[1]))
    assert TagCollector.dxftags(annotation) == source_tags
    assert LeaderData.transform is sdk_transform  # No process-wide SDK replacement.


@pytest.mark.parametrize("binary", [False, True])
def test_reader_handles_nested_zero_dogleg_without_losing_whole_insert(binary):
    doc, block, _ = annotation_block()
    parent = doc.blocks.new("SurveyReference")
    parent.add_blockref(block.name, (5, 7), dxfattribs={"rotation": 90})
    doc.modelspace().add_blockref(parent.name, (10, 20))
    stream = BytesIO() if binary else StringIO()
    doc.write(stream, fmt="bin" if binary else "asc")
    payload = stream.getvalue() if binary else stream.getvalue().encode("utf8")
    result = EzdxfReader().read("nested.dxf", payload)
    features = result.geometry.feature_collection["features"]
    assert features
    assert all(layer.geometry_complete for layer in result.layers)
    assert not any(f["properties"].get("source_analysis_blocking") for f in features)
    geometries = [g for f in features for g in f["geometry"].get("geometries", [])]
    assert {"type": "LineString", "coordinates": [[15, 27], [15, 28]]} in geometries
    assert {"type": "LineString", "coordinates": [[14, 27], [14, 28]]} in geometries
    assert any(g["type"] == "GeometryCollection" for g in geometries)


def test_nonzero_dogleg_uses_unmodified_sdk_record():
    doc, block, annotation = annotation_block(dogleg_length=2)
    data = annotation.context.leaders[0]
    assert prepare_multileader_transforms(doc) == 0
    assert annotation.context.leaders[0] is data
    insert = doc.modelspace().add_blockref(
        block.name, (10, 20), dxfattribs={"xscale": 3, "yscale": 3, "zscale": 3},
    )
    transformed = list(insert.virtual_entities())[1]
    assert transformed.context.leaders[0].dogleg_length == pytest.approx(6)
