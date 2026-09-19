from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("verify_autocad_region_topology_probe.py")
SPEC = importlib.util.spec_from_file_location("region_probe_verifier", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def write_probe(tmp_path: Path) -> Path:
    source_path = tmp_path / "source.dxf"
    source_path.write_bytes(b"DXF fixture")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    outer = [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [0, 0, 0]]
    hole = [[4, 4, 0], [6, 4, 0], [6, 6, 0], [4, 6, 0], [4, 4, 0]]
    document = {
        "schema": "green-atlas.autocad-region-topology-probe/1",
        "complete": False,
        "requested_tolerance_m": 0.0001,
        "source": {
            "path": str(source_path),
            "sha256": source_hash,
            "units_code": 6,
            "metres_per_unit": 1,
            "database_modified_flags": 0,
        },
        "regions": [
            {
                "handle": "1A",
                "layer": "TEST",
                "status": "native",
                "error_status": None,
                "native_area_units2": 96,
                "native_perimeter_units": 48,
                "loops": [
                    {"role": "outer", "sampled_max_deviation_units": 0, "coordinates": outer},
                    {"role": "hole", "sampled_max_deviation_units": 0, "coordinates": hole},
                ],
            }
        ],
        "summary": {"regions": 1, "resolved": 1, "unresolved": 0, "loops": 2, "points": 10},
        "limitations": [],
    }
    probe_path = tmp_path / "probe.json"
    probe_path.write_text(json.dumps(document))
    return probe_path


def test_verifies_exterior_and_hole(tmp_path: Path) -> None:
    report = VERIFIER.verify(write_probe(tmp_path))
    assert report["passed"] is True
    assert report["counts"] == {
        "regions": 1,
        "resolved": 1,
        "unresolved": 0,
        "loops": 2,
        "points": 10,
        "failures": 0,
    }


def test_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    probe_path = write_probe(tmp_path)
    document = json.loads(probe_path.read_text())
    Path(document["source"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        VERIFIER.verify(probe_path)
