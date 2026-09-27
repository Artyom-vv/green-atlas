from hashlib import sha256
from io import StringIO

import ezdxf
import pytest

from app.dxf_import.encoding import decode_text_dxf
from app.exporting.adapters import DxfRoundTripWriter
from app.planning.contracts import Plan
from app.projects.contracts import Project


def _bytes(document) -> bytes:
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode(document.output_encoding)


def _document(*, units: int = ezdxf.units.M):
    document = ezdxf.new("R2013", setup=True)
    document.units = units
    return document


def test_writer_merges_independent_dxf_with_layouts_resources_and_provenance():
    primary = _document()
    primary.layers.add("SHARED", color=1)
    primary.modelspace().add_line(
        (0, 0), (10, 0), dxfattribs={"layer": "SHARED"}
    )
    primary_content = _bytes(primary)

    child = _document()
    child.layers.add("SHARED", color=3)
    child.modelspace().add_line(
        (0, 5), (10, 5), dxfattribs={"layer": "SHARED"}
    )
    symbol = child.blocks.new("SYMBOL")
    symbol.add_circle((0, 0), 2, dxfattribs={"layer": "SHARED"})
    child.modelspace().add_blockref("SYMBOL", (20, 5))
    child.layout("Layout1").add_text("Auxiliary sheet")
    child_content = _bytes(child)
    child_digest = sha256(child_content).hexdigest()

    _artifact, output = DxfRoundTripWriter().create(
        Project(name="Multi", plan=Plan(objects=[])),
        primary_content,
        {"networks/base.dxf": child_content},
    )

    merged = ezdxf.read(StringIO(decode_text_dxf(output)))
    assert len(merged.modelspace().query("LINE")) == 2
    assert "SHARED" in merged.layers
    assert any(
        layer.dxf.name.endswith("$0$SHARED") for layer in merged.layers
    )
    assert "Layout1 (2)" in merged.layouts
    assert len(merged.layout("Layout1 (2)").query("TEXT")) == 1
    marker = f"GREEN_ATLAS_SOURCE_{child_digest[:12].upper()}"
    marked = [
        entity
        for entity in merged.entitydb.values()
        if entity.is_alive and entity.has_xdata(marker)
    ]
    assert len(marked) == 4
    assert {
        tuple(str(tag.value) for tag in entity.get_xdata(marker)[:2])
        for entity in marked
    } == {("networks/base.dxf", child_digest)}
    assert not any(
        entity.is_alive and entity.has_xdata(marker)
        for entity in child.entitydb.values()
    )
    assert not any(
        layer.dxf.name.endswith("$0$SHARED") for layer in primary.layers
    )
def test_writer_rejects_auxiliary_groups_instead_of_silently_dropping_them():
    primary = _document()
    child = _document()
    line = child.modelspace().add_line((0, 0), (1, 1))
    child.groups.new("linked").extend([line])

    with pytest.raises(ValueError, match="GROUPS"):
        DxfRoundTripWriter().create(
            Project(name="Grouped", plan=Plan(objects=[])),
            _bytes(primary),
            {"groups.dxf": _bytes(child)},
        )


def test_writer_rejects_mixed_units_before_merging():
    with pytest.raises(ValueError, match="единицы"):
        DxfRoundTripWriter().create(
            Project(name="Units", plan=Plan(objects=[])),
            _bytes(_document(units=ezdxf.units.M)),
            {"millimeters.dxf": _bytes(_document(units=ezdxf.units.MM))},
        )


def test_writer_preserves_auxiliary_acis_payload_in_initialized_section():
    primary = _document()
    child = _document()
    child.modelspace().add_region().sab = b"verified-auxiliary-sab"

    _artifact, output = DxfRoundTripWriter().create(
        Project(name="ACIS", plan=Plan(objects=[])),
        _bytes(primary),
        {"terrain.dxf": _bytes(child)},
    )

    reopened = ezdxf.read(StringIO(decode_text_dxf(output)))
    regions = reopened.modelspace().query("REGION")
    assert len(regions) == 1
    assert regions[0].sab == b"verified-auxiliary-sab"
