import importlib.util
import json
from pathlib import Path

import ezdxf

MODULE_PATH = Path(__file__).with_name("verify_autocad_entity_instance_coverage.py")
SPEC = importlib.util.spec_from_file_location("entity_instance_verifier", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_source(path: Path, *, minsert: bool = False) -> Path:
    document = ezdxf.new("R2018")
    block = document.blocks.new("TREE")
    block.add_line((0, 0), (1, 0))
    block.add_attdef("NAME", insert=(0, 0))
    insert = document.modelspace().add_blockref(
        "TREE",
        (10, 20),
        dxfattribs={
            "row_count": 2 if minsert else 1,
            "column_count": 1,
            "row_spacing": 5,
        },
    )
    insert.add_attrib("NAME", "one")
    document.saveas(path)
    return path


def make_probe(source: Path, path: Path) -> Path:
    records, _ = MODULE.inventory_source_instances(source)
    import hashlib

    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    path.write_text(
        json.dumps(
            {
                "source": {"sha256": digest},
                "coverage": records,
                "summary": {"source_instances": len(records)},
            }
        )
    )
    return path


def test_accepts_nested_definition_and_attached_attribute(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source.dxf")
    probe = make_probe(source, tmp_path / "probe.json")
    report = MODULE.verify(source, probe)
    assert report["passed"] is True
    assert report["counts"]["expected_types"] == {
        "AcDbAttribute": 1,
        "AcDbAttributeDefinition": 1,
        "AcDbBlockReference": 1,
        "AcDbLine": 1,
    }


def test_rejects_missing_attached_attribute(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source.dxf")
    probe = make_probe(source, tmp_path / "probe.json")
    document = json.loads(probe.read_text())
    document["coverage"] = [
        record
        for record in document["coverage"]
        if record["entity_type"] != "AcDbAttribute"
    ]
    document["summary"]["source_instances"] -= 1
    probe.write_text(json.dumps(document))
    report = MODULE.verify(source, probe)
    assert report["passed"] is False
    assert report["failures"][0]["reason"] == "native coverage misses source instances"


def test_minsert_expands_each_cell_with_stable_identity(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source.dxf", minsert=True)
    probe = make_probe(source, tmp_path / "probe.json")
    report = MODULE.verify(source, probe)
    assert report["passed"] is True
    assert report["counts"]["expected_types"] == {
        "AcDbAttribute": 2,
        "AcDbAttributeDefinition": 2,
        "AcDbLine": 2,
        "AcDbMInsertBlock": 1,
    }
    records, blockers = MODULE.inventory_source_instances(source)
    assert blockers == []
    chains = {
        tuple(record["instance_chain"])
        for record in records
        if record["entity_type"] == "AcDbLine"
    }
    assert chains == {
        (f"MINSERT:{records[0]['handle']}:R0:C0",),
        (f"MINSERT:{records[0]['handle']}:R1:C0",),
    }
