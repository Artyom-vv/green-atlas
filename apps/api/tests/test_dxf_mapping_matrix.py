import sys
from io import StringIO
from pathlib import Path

import ezdxf

SCRIPTS = Path(__file__).parents[3] / "scripts" / "cad-lab"
sys.path.insert(0, str(SCRIPTS))

from audit_dxf_mapping_matrix import audit_source  # noqa: E402


def test_mapping_audit_separates_readability_from_semantic_confirmation(tmp_path):
    document = ezdxf.new("R2010")
    document.units = 6
    document.layers.new("Граница работ")
    document.layers.new("СУЩ_СЕТИ")
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "Граница работ"},
    )
    document.modelspace().add_line(
        (10, 0),
        (10, 100),
        dxfattribs={"layer": "СУЩ_СЕТИ"},
    )
    stream = StringIO()
    document.write(stream)
    source = tmp_path / "source.dxf"
    source.write_bytes(stream.getvalue().encode())

    result = audit_source(source)

    assert result["status"] == "readable"
    assert result["features"] == 2
    assert result["calculation_gate"] == "confirm_mapping"
    assert result["unconfirmed_active_layer_count"] == 2
    assert {row["mapped_kind"] for row in result["review_layers"]} == {
        "site_border",
        "utility",
    }
    assert all(row["reasons"] for row in result["review_layers"])
