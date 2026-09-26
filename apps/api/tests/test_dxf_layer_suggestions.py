from io import StringIO

import ezdxf
import pytest
from shapely.geometry import box, mapping

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.layer_contracts import (
    BoundaryCandidateStatus,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layer_suggestions import suggest_layer_kind
from app.geometry.adapters import ShapelyGeometryEngine
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import validate_planting_zones
from app.projects.contracts import Project


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
        ("Граница улицы", LayerKind.IGNORE),
        ("Граница растительности и грунта", LayerKind.IGNORE),
        ("Границы$0$Граница растительности и грунта", LayerKind.IGNORE),
        ("ROAD_BOUNDARY", LayerKind.IGNORE),
        ("!!!_1. ГРАНИЦА РАБОТ", LayerKind.SITE_BORDER),
        ("ДВ_ГП_П_Граница работ", LayerKind.SITE_BORDER),
        ("Границы_проектирования", LayerKind.SITE_BORDER),
        ("Граница участка", LayerKind.SITE_BORDER),
        ("ДВ_ГП_П_Столбики металлические парковочные", LayerKind.RESTRICTED),
        ("PARKING_BOLLARDS", LayerKind.RESTRICTED),
        ("PARKING", LayerKind.IGNORE),
        ("Парковка", LayerKind.IGNORE),
        ("Парк", LayerKind.EXISTING_GREEN),
        ("PARK", LayerKind.EXISTING_GREEN),
        ("Проект|Тип7_Тротуар АБ менее 2м за Газон", LayerKind.ROAD),
        ("Проект|Тип7_Тротуар_за_Газон", LayerKind.ROAD),
        ("Проект|Тип4_ПЧ_за_Тротуар", LayerKind.ROAD),
        ("Проект|Газон_за_Тротуар", LayerKind.EXISTING_GREEN),
        ("Проект|Отмостка_без_борта", LayerKind.ROAD),
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
    assert layers[names[0]].suggestion_confidence == LayerSuggestionConfidence.LOW
    assert layers[names[0]].mapping_review_required
    assert layers[names[1]].suggestion_confidence == LayerSuggestionConfidence.MEDIUM
    assert layers[names[1]].mapping_review_required
    assert layers[names[0]].bounds == (10, 20, 30, 40)
    assert layers[names[1]].bounds == (10, 20, 30, 40)
    features = imported.geometry.feature_collection["features"]
    assert {feature["properties"]["source_layer"] for feature in features} == set(names)
    assert all(feature["geometry"]["coordinates"] == [[10, 20], [30, 40]] for feature in features)


def test_wide_closed_boundary_uses_authored_center_ring_for_calculation():
    document = ezdxf.new("R2010")
    document.units = 6
    layer = "Граница работ"
    document.layers.new(layer)
    boundary = document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": layer, "const_width": 1.0},
    )
    assert boundary.closed
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("wide-boundary.dxf", stream.getvalue().encode("utf8"))
    feature = imported.geometry.feature_collection["features"][0]
    imported_layer = imported.layers[0]

    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"]["source_polyline_width_m"] == 1.0
    assert feature["properties"]["source_polyline_center_geometry"] == {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0], [0.0, 0.0]]],
    }
    assert imported_layer.suggested_kind == LayerKind.SITE_BORDER
    assert imported_layer.suggestion_confidence == LayerSuggestionConfidence.MEDIUM
    assert imported_layer.mapping_review_required
    assert not imported_layer.mapping_confirmed
    assert imported_layer.boundary_candidate is not None
    assert imported_layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
    assert imported_layer.boundary_candidate.area_m2 == 10_000.0
    imported_layer.mapping_confirmed = True
    project = Project(
        name="Wide boundary",
        layers=imported.layers,
        source_geometry=imported.geometry,
    )
    calculated = ShapelyGeometryEngine().calculate(project)
    assert calculated.site_area_m2 == 10_000.0
    assert calculated.allowed_area_m2 == 10_000.0
    assert any(
        feature["properties"].get("kind") == "site_surface"
        for feature in calculated.feature_collection["features"]
    )
    project.geometry = calculated
    validate_planting_zones(
        project,
        [PlantingZoneAssignment(label="Inside", geometry=mapping(box(10, 10, 30, 30)))],
    )


def test_thin_named_boundary_is_not_automatically_mapped_as_the_site():
    document = ezdxf.new("R2010")
    document.units = 6
    layer = "Граница работ"
    document.layers.new(layer)
    document.modelspace().add_lwpolyline(
        [(0, 0), (20, 0), (20, 1), (0, 1)],
        close=True,
        dxfattribs={"layer": layer, "const_width": 0.5},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("thin-boundary.dxf", stream.getvalue().encode("utf8"))
    imported_layer = imported.layers[0]

    assert imported_layer.suggested_kind == LayerKind.IGNORE
    assert imported_layer.required is False
    assert imported_layer.boundary_candidate is not None
    assert imported_layer.boundary_candidate.status == BoundaryCandidateStatus.THIN
    assert imported_layer.boundary_candidate.inset_1_5m_area_m2 == 0


def test_closed_order_boundary_is_exposed_as_a_usable_manual_candidate():
    document = ezdxf.new("R2010")
    document.units = 6
    layer = "Граница заказа"
    document.layers.new(layer)
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
        dxfattribs={"layer": layer},
    )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("order-boundary.dxf", stream.getvalue().encode("utf8"))
    imported_layer = imported.layers[0]

    assert imported_layer.suggested_kind == LayerKind.IGNORE
    assert imported_layer.boundary_candidate is not None
    assert imported_layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
    assert imported_layer.boundary_candidate.basis == "polygonized_linework"
    assert imported_layer.boundary_candidate.area_m2 == 10_000.0


def test_multiple_usable_boundaries_require_an_explicit_choice():
    document = ezdxf.new("R2010")
    document.units = 6
    for layer, size in (("Граница работ", 100), ("Граница заказа", 200)):
        document.layers.new(layer)
        document.modelspace().add_lwpolyline(
            [(0, 0), (size, 0), (size, size), (0, size), (0, 0)],
            dxfattribs={"layer": layer},
        )
    stream = StringIO()
    document.write(stream)

    imported = EzdxfReader().read("ambiguous-boundaries.dxf", stream.getvalue().encode("utf8"))

    assert all(
        layer.boundary_candidate is not None
        and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
        for layer in imported.layers
    )
    assert all(layer.suggested_kind == LayerKind.IGNORE for layer in imported.layers)
    assert all(not layer.mapping_confirmed for layer in imported.layers)
    assert all(layer.required is False for layer in imported.layers)
