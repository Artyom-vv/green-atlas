from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from app.cad_bridge import CadSnapshotAdmissionError, compile_region_probe


def valid_probe() -> dict:
    region_identity = {"handle": "A12", "instance_chain": ["10", "2F"]}
    return {
        "schema": "green-atlas.autocad-region-topology-probe/1",
        "complete": False,
        "plugin_version": "0.1.4",
        "capture_mode": "side_database_dxf",
        "requested_tolerance_m": 0.001,
        "source": {
            "path": "/tmp/source.dxf",
            "sha256": "a" * 64,
            "units_code": 6,
            "metres_per_unit": 1,
            "document_revision": "revision",
            "database_modified_flags": 0,
            "live_database_matches_disk": True,
        },
        "coverage": [
            {
                "handle": "10",
                "instance_chain": [],
                "entity_type": "AcDbBlockReference",
                "layer": "SITE",
                "status": "context",
                "method": "traverse-block-reference",
                "reason": "container instance traversed for descendant provenance",
            },
            {
                **region_identity,
                "entity_type": "AcDbRegion",
                "layer": "SITE",
                "status": "native",
                "method": "AcBr loop traversal",
                "reason": None,
            },
            {
                "handle": "B1",
                "instance_chain": ["10"],
                "entity_type": "AcDbLine",
                "layer": "SITE",
                "status": "unresolved",
                "method": "not-emitted-by-region-probe",
                "reason": "geometry is not emitted by this probe",
            },
        ],
        "regions": [
            {
                **region_identity,
                "status": "native",
                "error_status": None,
                "native_area_units2": 50,
                "native_perimeter_units": 34.142,
                "loops": [
                    {
                        "role": "outer",
                        "sampled_max_deviation_units": 0.0005,
                        "coordinates": [
                            [0, 0, 0],
                            [10, 0, 0],
                            [10, 10, 0],
                            [0, 0, 0],
                        ],
                    }
                ],
            }
        ],
        "summary": {
            "regions": 1,
            "resolved": 1,
            "unresolved": 0,
            "source_instances": 3,
            "native": 1,
            "context": 1,
            "unresolved_instances": 1,
            "cyclic_block_references": 0,
            "unloaded_xref_block_references": 0,
            "unresolved_xref_block_references": 0,
            "xref_block_references": 0,
            "unexpanded_minsert_blocks": 0,
            "unreadable_block_records": 0,
            "unreadable_entities": 0,
        },
    }


def compile(probe: dict, package_root: Path | None = None):
    return compile_region_probe(
        probe,
        autocad_version="2027.0.1",
        target="macos-arm64",
        package_root=package_root,
    )


def probe_with_xref(package_root: Path) -> dict:
    child = package_root / "references" / "child.dxf"
    child.parent.mkdir()
    child.write_bytes(b"exact child DXF bytes")
    probe = valid_probe()
    probe["plugin_version"] = "0.1.6"
    probe["coverage"].append(
        {
            "handle": "20",
            "instance_chain": [],
            "entity_type": "AcDbBlockReference",
            "layer": "BASE",
            "status": "context",
            "method": "traverse-xref-reference",
            "reason": "resolved XREF traversed for descendant provenance",
            "xref_dependency_id": "xref/AA",
        }
    )
    probe["xref_dependencies"] = [
        {
            "record_handle": "AA",
            "block_name": "CHILD",
            "stored_path": "references/child.dxf",
            "resolved_path": str(child),
            "sha256": sha256(child.read_bytes()).hexdigest(),
            "bytes": child.stat().st_size,
            "status": "resolved",
        }
    ]
    probe["summary"].update(
        source_instances=4,
        context=2,
        xref_block_references=1,
        xref_dependency_records=1,
    )
    return probe


def test_compiles_complete_ledger_without_claiming_unsupported_geometry() -> None:
    snapshot = compile(valid_probe())
    assert snapshot.summary.source_instances == 3
    assert snapshot.summary.native == 1
    assert snapshot.summary.context == 1
    assert snapshot.summary.unresolved == 1
    assert snapshot.summary.complete is True
    assert snapshot.geometry[0].id == "region/10/2F/A12"
    assert snapshot.geometry[0].achieved_tolerance_m == 0.0005
    assert snapshot.coverage[2].geometry_ids == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(capture_mode="live_document"), "side-database"),
        (
            lambda value: value.update(plugin_version="0.1.3"),
            "unsupported native probe plugin version",
        ),
        (
            lambda value: value["summary"].pop(
                "unresolved_xref_block_references"
            ),
            "misses traversal diagnostics",
        ),
        (
            lambda value: value["summary"].update(unreadable_entities=1),
            "traversal is incomplete",
        ),
        (
            lambda value: value["summary"].update(
                unresolved_xref_block_references=1
            ),
            "traversal is incomplete",
        ),
        (
            lambda value: value["regions"][0]["loops"][0].update(
                sampled_max_deviation_units=0.002
            ),
            "exceeds requested",
        ),
        (
            lambda value: value["regions"][0]["loops"][0]["coordinates"][1].__setitem__(
                2, 0.1
            ),
            "wcs-xy-planar",
        ),
        (
            lambda value: value["coverage"].pop(),
            "source instance count differs",
        ),
        (
            lambda value: value["regions"][0].update(native_area_units2=75),
            "area disagrees with emitted loops",
        ),
        (
            lambda value: value["regions"][0].update(native_perimeter_units=50),
            "perimeter disagrees with emitted loops",
        ),
    ],
)
def test_rejects_probe_that_cannot_prove_snapshot(mutation, message: str) -> None:
    probe = deepcopy(valid_probe())
    mutation(probe)
    with pytest.raises(CadSnapshotAdmissionError, match=message):
        compile(probe)


def test_payload_and_geometry_hashes_are_deterministic() -> None:
    first = compile(valid_probe())
    second = compile(valid_probe())
    assert first.summary.payload_sha256 == second.summary.payload_sha256
    assert first.geometry[0].content_sha256 == second.geometry[0].content_sha256


def test_compiles_xref_only_when_exact_package_dependency_is_verified(
    tmp_path: Path,
) -> None:
    probe = probe_with_xref(tmp_path)
    snapshot = compile(probe, tmp_path)

    assert snapshot.dependencies is not None
    assert snapshot.dependencies[0].path == "references/child.dxf"
    assert snapshot.coverage[-1].dependency_ids == ["xref/AA"]

    with pytest.raises(CadSnapshotAdmissionError, match="package root is required"):
        compile(probe)

    (tmp_path / "references" / "child.dxf").write_bytes(b"changed")
    with pytest.raises(CadSnapshotAdmissionError, match="differs from the file"):
        compile(probe, tmp_path)


def test_rejects_xref_probe_without_complete_dependency_coverage(
    tmp_path: Path,
) -> None:
    probe = probe_with_xref(tmp_path)
    probe["coverage"][-1].pop("xref_dependency_id")

    with pytest.raises(CadSnapshotAdmissionError, match="reference count differs"):
        compile(probe, tmp_path)
