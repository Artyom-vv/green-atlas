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


def probe_with_failed_region() -> dict:
    probe = valid_probe()
    probe["regions"].append({
        "handle": "FA1ED", "instance_chain": [], "status": "unresolved",
        "error_status": 1, "loops": [],
    })
    probe["coverage"].append({
        "handle": "FA1ED", "instance_chain": [], "entity_type": "AcDbHatch",
        "layer": "SITE", "status": "unresolved", "method": "native-topology-failed",
        "reason": "native HATCH topology extraction failed",
    })
    probe["summary"].update(regions=2, unresolved=1, source_instances=4, unresolved_instances=2)
    return probe


def test_failed_region_keeps_healthy_geometry_and_explicit_gap() -> None:
    snapshot = compile(probe_with_failed_region())
    assert len(snapshot.geometry) == 1
    assert snapshot.summary.unresolved == 2
    assert snapshot.coverage[-1].status == "unresolved"
    assert snapshot.coverage[-1].geometry_ids == []
    assert snapshot.coverage[-1].reason == "native HATCH topology extraction failed"


@pytest.mark.parametrize("fault", ["missing_coverage", "native_coverage", "missing_reason", "wrong_counts"])
def test_failed_region_cannot_hide_inconsistent_extraction(fault: str) -> None:
    probe = probe_with_failed_region()
    if fault == "missing_coverage":
        probe["coverage"].pop()
        probe["summary"]["source_instances"] -= 1
    elif fault == "native_coverage":
        probe["coverage"][-1]["status"] = "native"
    elif fault == "missing_reason":
        probe["coverage"][-1]["reason"] = None
    else:
        probe["summary"]["unresolved"] = 0
    with pytest.raises(CadSnapshotAdmissionError):
        compile(probe)


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


def probe_with_missing_xref() -> dict:
    probe = valid_probe()
    probe["plugin_version"] = "0.1.21"
    probe["coverage"].append(
        {
            "handle": "20",
            "instance_chain": [],
            "entity_type": "AcDbBlockReference",
            "layer": "BASE",
            "status": "unresolved",
            "method": "xref-not-resolved",
            "reason": "external reference status is not resolved",
            "xref_dependency_id": "xref/AA",
        }
    )
    probe["xref_dependencies"] = [
        {
            "record_handle": "AA",
            "block_name": "MISSING",
            "stored_path": "references/missing.dxf",
            "resolved_path": "",
            "sha256": "",
            "bytes": 0,
            "status": "unresolved",
        }
    ]
    probe["summary"].update(
        paths=0,
        points=0,
        source_instances=4,
        unresolved_instances=2,
        xref_block_references=1,
        unresolved_xref_block_references=1,
        xref_dependency_records=1,
    )
    return probe


def probe_with_native_primitives() -> dict:
    probe = valid_probe()
    probe["plugin_version"] = "0.1.7"
    probe["coverage"].extend(
        [
            {
                "handle": "C1",
                "instance_chain": [],
                "entity_type": "AcDbPolyline",
                "layer": "ROAD",
                "status": "native",
                "method": "autodesk-acdbcurve-adaptive-sampling",
                "reason": None,
            },
            {
                "handle": "D1",
                "instance_chain": ["10"],
                "entity_type": "AcDbPoint",
                "layer": "TREE",
                "status": "native",
                "method": "autodesk-acdbpoint-wcs",
                "reason": None,
            },
        ]
    )
    probe["paths"] = [
        {
            "handle": "C1",
            "instance_chain": [],
            "status": "native",
            "error_status": None,
            "closed": True,
            "sampled_max_deviation_units": 0.00025,
            "coordinates": [
                [0, 0, 150],
                [20, 0, 150.1],
                [20, 5, 150.2],
                [0, 0, 150],
            ],
        }
    ]
    probe["points"] = [
        {
            "handle": "D1",
            "instance_chain": ["10"],
            "status": "native",
            "error_status": None,
            "coordinates": [4, 3, 151.25],
        }
    ]
    probe["summary"].update(source_instances=5, native=3, paths=1, points=1)
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


def test_compiles_native_curve_and_point_without_portable_reader() -> None:
    snapshot = compile(probe_with_native_primitives())

    assert [geometry.kind for geometry in snapshot.geometry] == [
        "region",
        "path",
        "point",
    ]
    path = snapshot.geometry[1]
    assert path.id == "path/C1"
    assert path.closed is True
    assert path.achieved_tolerance_m == 0.00025
    assert snapshot.geometry[2].id == "point/10/D1"
    assert snapshot.summary.native == 3


def test_quarantines_zero_area_closed_path_without_rejecting_snapshot() -> None:
    probe = probe_with_native_primitives()
    probe["plugin_version"] = "0.1.28"
    probe["paths"][0]["coordinates"] = [
        [0, 0, 150],
        [20, 0, 150.1],
        [10, 0, 150.2],
        [0, 0, 150],
    ]

    snapshot = compile(probe)

    assert [geometry.kind for geometry in snapshot.geometry] == ["region", "point"]
    path_coverage = next(
        record for record in snapshot.coverage if record.identity.handle == "C1"
    )
    assert path_coverage.status == "unresolved"
    assert path_coverage.method == "autocad-wcs-xy-quarantined"
    assert path_coverage.geometry_ids == []
    assert "no usable WCS XY area" in (path_coverage.reason or "")
    assert snapshot.summary.native == 2
    assert snapshot.summary.unresolved == 2
    assert snapshot.summary.complete is True


def test_compiles_latest_autocad_bridge_contract() -> None:
    probe = probe_with_native_primitives()
    probe["plugin_version"] = "0.1.20"

    snapshot = compile(probe)

    assert snapshot.extraction.plugin_version == "0.1.20"
    assert [geometry.kind for geometry in snapshot.geometry] == [
        "region",
        "path",
        "point",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(capture_mode="live_document"), "side-database"),
        (
            lambda value: value.update(plugin_version="0.1.3"),
            "unsupported native probe plugin version",
        ),
        (
            lambda value: value.update(plugin_version="0.1.18"),
            "unsupported native probe plugin version",
        ),
        (
            lambda value: value["summary"].pop("unresolved_xref_block_references"),
            "misses traversal diagnostics",
        ),
        (
            lambda value: value["summary"].update(unreadable_entities=1),
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
                0, float("nan")
            ),
            "invalid coordinates",
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


def test_missing_xref_is_admitted_as_explicit_unresolved_coverage() -> None:
    snapshot = compile(probe_with_missing_xref())

    assert snapshot.dependencies is None
    assert snapshot.coverage[-1].status == "unresolved"
    assert snapshot.coverage[-1].dependency_ids is None
    assert snapshot.summary.unresolved == 2
    assert snapshot.summary.complete is True


def test_missing_xref_cannot_masquerade_as_traversed_context() -> None:
    probe = probe_with_missing_xref()
    probe["coverage"][-1]["status"] = "context"

    with pytest.raises(CadSnapshotAdmissionError, match="must remain unresolved"):
        compile(probe)


def test_rejects_xref_probe_without_complete_dependency_coverage(
    tmp_path: Path,
) -> None:
    probe = probe_with_xref(tmp_path)
    probe["coverage"][-1].pop("xref_dependency_id")

    with pytest.raises(CadSnapshotAdmissionError, match="reference count differs"):
        compile(probe, tmp_path)
