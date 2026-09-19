from io import StringIO

import ezdxf

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.component_provenance import component_definition_provenance
from app.dxf_import.layer_contracts import Layer, LayerKind


def test_nested_annotation_preserves_leaf_identity_without_reclassifying():
    doc = ezdxf.new("R2013")
    doc.units = ezdxf.units.M
    doc.layers.new("Кабели")
    leaf = doc.blocks.new("DIMTXT_84")
    line = leaf.add_line((0, 0), (23, 0), dxfattribs={"layer": "Кабели"})
    wrapper = doc.blocks.new("NETWORK")
    wrapper.add_blockref(leaf.name, (10, 0))
    root = doc.modelspace().add_blockref(wrapper.name, (100, 0))
    stream = StringIO()
    doc.write(stream)
    result = EzdxfReader().read("nested.dxf", stream.getvalue().encode())
    features = result.geometry.feature_collection["features"]
    feature = next(f for f in features if f["properties"]["source_layer"] == "Кабели")
    props = feature["properties"]
    assert props["source_handle"] == root.dxf.handle
    assert props["source_block"] == "NETWORK"
    assert props["source_component_handle"] == line.dxf.handle
    assert props["source_component_block"] == "DIMTXT_84"
    assert feature["geometry"]["coordinates"] == [[110.0, 0.0], [133.0, 0.0]]
    assert not props.get("source_context_only")  # No name-based classification.


def test_entity_without_copy_origin_does_not_invent_provenance():
    doc = ezdxf.new()
    assert component_definition_provenance(doc.modelspace().add_line((0, 0), (1, 0))) == {}


def test_legacy_incomplete_layer_is_not_cleared_by_empty_diagnostics():
    layer = Layer(id="legacy", source_name="legacy", object_count=1,
                  suggested_kind=LayerKind.UTILITY, color="#000000",
                  geometry_complete=False)
    restored = Layer.model_validate_json(layer.model_dump_json())
    assert not restored.geometry_complete
    assert restored.unsupported_geometry_types == {}
    assert restored.unreadable_geometry_count == 0


def test_region_omission_is_recorded_without_marking_layer_complete():
    doc = ezdxf.new("R2013")
    doc.layers.new("PHYSICAL")
    doc.modelspace().new_entity("REGION", {"layer": "PHYSICAL"})
    # Empty ACIS payloads are omitted by the SDK writer. Exercise the
    # normalizer directly here; real nonempty REGION is covered by the lab.
    result = EzdxfReader()._normalize_document(doc)
    layer = next(l for l in result.layers if l.source_name == "PHYSICAL")
    assert not layer.geometry_complete
    assert layer.unsupported_geometry_types == {"REGION": 1}
