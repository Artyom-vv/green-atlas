from io import StringIO

import ezdxf
import pytest

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.layer_contracts import LayerKind
from app.dxf_import.layer_suggestions import suggest_layer_kind


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ГЕО+Границы работ$0$ТОПО_Здания", LayerKind.BUILDING),
        ("Границы$12$подоснова$0$СУЩ_СЕТИ_Водопровод", LayerKind.UTILITY),
        ("SITE_BORDER|survey|BUILDINGS", LayerKind.BUILDING),
        ("BUILDINGS|SITE_BORDER", LayerKind.SITE_BORDER),
        ("Границы$0$Леса и газоны", LayerKind.EXISTING_GREEN),
        ("Леса и газоны", LayerKind.EXISTING_GREEN),
        ("Газопровод", LayerKind.UTILITY),
        ("ГАЗ низкого давления", LayerKind.UTILITY),
        ("UTILITY_GAS", LayerKind.UTILITY),
        ("GAS_PIPE", LayerKind.UTILITY),
        ("Граница$0$0", LayerKind.IGNORE),
        ("GREEN_ATLAS_TERRAIN_COP90", LayerKind.IGNORE),
    ],
)
def test_suggestions_do_not_inherit_drawing_names(name, expected):
    assert suggest_layer_kind(name) == expected


def test_reader_retains_full_source_names_and_coordinates():
    document = ezdxf.new("R2010")
    document.units = 6
    names = ["Границы$0$ТОПО_Здания", "Границы$0$СУЩ_СЕТИ_Газопровод"]
    for name in names:
        document.layers.new(name)
        document.modelspace().add_line((10, 20), (30, 40), dxfattribs={"layer": name})
    stream = StringIO()
    document.write(stream)
    content = stream.getvalue().encode("utf8")
    imported = EzdxfReader().read("source.dxf", content)
    layers = {layer.source_name: layer for layer in imported.layers}
    assert layers[names[0]].suggested_kind == LayerKind.BUILDING
    assert layers[names[1]].suggested_kind == LayerKind.UTILITY
    assert layers[names[0]].bounds == (10, 20, 30, 40)
    assert layers[names[1]].bounds == (10, 20, 30, 40)
    features = imported.geometry.feature_collection["features"]
    assert {feature["properties"]["source_layer"] for feature in features} == set(names)
    assert all(feature["geometry"]["coordinates"] == [[10, 20], [30, 40]] for feature in features)
