"""Exercise the SDK failure reproduced by the official XREF package."""

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

import ezdxf
import pytest
from ezdxf import xref

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.package_contracts import ReferenceOverride
from app.exporting.adapters import DxfRoundTripWriter
from app.planning.contracts import Plan
from app.projects.contracts import Project


def _bind_into_headerless_root():
    root = ezdxf.new("R2018")
    stream = StringIO()
    root.write(stream)
    root = ezdxf.read(StringIO(stream.getvalue()))
    assert not root.acdsdata.is_valid
    child = ezdxf.new("R2018")
    # Opaque SDK-preserved bytes; no assertion of ACIS geometric validity.
    child.modelspace().add_region().sab = b"round-trip-payload"
    loader = xref.Loader(child, root)
    loader.load_modelspace()
    loader.execute()
    return root


def _bytes(document):
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode("utf8")


def test_writer_rejects_missing_acis_instead_of_publishing_lossy_dxf():
    content = _bytes(_bind_into_headerless_root())
    document = ezdxf.read(StringIO(content.decode()))
    assert len(document.modelspace().query("REGION")) == 1
    assert document.modelspace().query("REGION")[0].sab == b""
    with pytest.raises(ValueError, match="SAT/SAB.*REGION: 1"):
        DxfRoundTripWriter().create(Project(name="QA", plan=Plan(objects=[])), content)


def test_sdk_section_initialization_preserves_bound_payload_and_writer_roundtrip():
    path = Path(__file__).parents[3] / "scripts/cad-lab/assemble_dxf_fixture.py"
    spec = spec_from_file_location("assembly_integrity_probe", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    document = _bind_into_headerless_root()
    before = module.acis_snapshot(document)
    module.ensure_acis_section(document)
    prepared = _bytes(document)
    _artifact, output = DxfRoundTripWriter().create(
        Project(name="QA", plan=Plan(objects=[])), prepared
    )
    assert module.acis_snapshot(ezdxf.read(StringIO(output.decode()))) == before


@pytest.mark.parametrize("changed_source", [False, True])
def test_offline_binding_uses_fingerprinted_profile_and_preserves_insert(
    tmp_path, changed_source
):
    path = Path(__file__).parents[3] / "scripts/cad-lab/assemble_dxf_fixture.py"
    spec = spec_from_file_location("assembly_profile_probe", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    root, child = ezdxf.new("R2018"), ezdxf.new("R2018")
    root.add_xref_def(filename="moved.dwg", name="networks")
    root.modelspace().add_blockref("networks", (100, 200), dxfattribs={"rotation": 30})
    child.modelspace().add_line((10, 20), (30, 40))
    root.saveas(tmp_path / "main.dxf")
    child.saveas(tmp_path / "actual.dxf")
    before = (tmp_path / "main.dxf").read_bytes()
    override = ReferenceOverride(
        owner="main.dxf",
        block="networks",
        target="actual.dxf",
        expected_sha256=file_sha256(tmp_path / "actual.dxf"),
        reason="Verified relocated source",
    )
    if changed_source:
        child.modelspace().add_circle((0, 0), 5)
        child.saveas(tmp_path / "actual.dxf")
        with pytest.raises(CadConversionError, match="изменилось"):
            module.assemble(
                tmp_path, tmp_path / "main.dxf", tmp_path / "full.dxf", [override]
            )
        assert not (tmp_path / "full.dxf").exists()
    else:
        receipt = module.assemble(
            tmp_path, tmp_path / "main.dxf", tmp_path / "full.dxf", [override]
        )
        output = ezdxf.readfile(tmp_path / "full.dxf")
        insert = output.modelspace().query("INSERT")[0]
        assert tuple(insert.dxf.insert) == (100, 200, 0)
        assert insert.dxf.rotation == 30
        assert len(output.blocks["networks"].query("LINE")) == 1
        assert receipt["all_active_references_resolved"]
        assert receipt["bindings"][0]["resolution"]["resolution"] == "explicit_override"
    assert (tmp_path / "main.dxf").read_bytes() == before
