from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from app.cad_bridge import CadSnapshotAdmissionError, compile_region_probe
from app.cad_bridge.provider import (
    CadSnapshotProviderError,
    _region_shape,
    build_dxf_import_from_snapshot,
    build_dxf_import_from_snapshot_path,
)
from app.cad_intake.worker import _read_snapshot
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.geometry.adapters import (
    ShapelyGeometryEngine,
    _native_building_paths_without_surface,
)
from app.geometry.domain import PositionChecker
from app.projects.contracts import Project


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


def paired_native_region_probe() -> dict:
    """Contract fixture; real DWG pairing is separately verified by AutoCAD."""
    probe = valid_probe()
    probe["plugin_version"] = "0.1.34"
    probe["coverage"] = [
        {
            "handle": handle,
            "instance_chain": ["10"],
            "entity_type": "AcDbPolyline",
            "source_layer": "Здания",
            "layer": "Здания",
            "status": "native",
            "method": "autodesk-acdbcurve-adaptive-sampling",
            "reason": None,
        }
        for handle in ("ACE2", "E201")
    ]
    probe["paths"] = [
        {
            "handle": "ACE2",
            "instance_chain": ["10"],
            "source_layer": "Здания",
            "layer": "Здания",
            "status": "native",
            "error_status": None,
            "closed": False,
            "sampled_max_deviation_units": 0,
            "coordinates": [[0, 0, 0], [10, 0, 0], [10, 10, 0]],
        },
        {
            "handle": "E201",
            "instance_chain": ["10"],
            "source_layer": "Здания",
            "layer": "Здания",
            "status": "native",
            "error_status": None,
            "closed": False,
            "sampled_max_deviation_units": 0,
            "coordinates": [[10, 10, 0], [0, 10, 0], [0, 0, 0]],
        },
    ]
    probe["regions"] = [
        {
            "handle": "ACE2",
            "instance_chain": ["10"],
            "source_layer": "Здания",
            "layer": "Здания",
            "source_handles": ["ACE2", "E201"],
            "status": "native",
            "error_status": None,
            "native_area_units2": 100,
            "native_perimeter_units": 40,
            "loops": [
                {
                    "role": "outer",
                    "sampled_max_deviation_units": 0,
                    "coordinates": [
                        [0, 0, 0],
                        [10, 0, 0],
                        [10, 10, 0],
                        [0, 10, 0],
                        [0, 0, 0],
                    ],
                }
            ],
        }
    ]
    probe["summary"].update(
        regions=1,
        paths=2,
        points=0,
        resolved=1,
        unresolved=0,
        source_instances=2,
        native=2,
        context=0,
        unresolved_instances=0,
    )
    return probe


def unpaired_native_building_probe() -> dict:
    probe = paired_native_region_probe()
    probe["coverage"].append(
        {
            "handle": "BEEF",
            "instance_chain": ["10"],
            "entity_type": "AcDbPolyline",
            "source_layer": "Здания",
            "layer": "Здания",
            "status": "native",
            "method": "autodesk-acdbcurve-adaptive-sampling",
            "reason": None,
        }
    )
    probe["paths"].append(
        {
            "handle": "BEEF",
            "instance_chain": ["10"],
            "source_layer": "Здания",
            "layer": "Здания",
            "status": "native",
            "error_status": None,
            "closed": False,
            "sampled_max_deviation_units": 0,
            "coordinates": [[12, 0, 0], [14, 0, 0]],
        }
    )
    probe["summary"].update(paths=3, source_instances=3, native=3)
    return probe


def near_closed_building_proposal_probe() -> dict:
    probe = unpaired_native_building_probe()
    probe["plugin_version"] = "0.1.36"
    source_path = next(path for path in probe["paths"] if path["handle"] == "BEEF")
    source_path["coordinates"] = [
        [20, 0, 0], [30, 0, 0], [30, 10, 0], [20, 10, 0], [20, 0.01, 0]
    ]
    probe["area_proposals"] = [{
        "handle": "BEEF",
        "instance_chain": ["10"],
        "source_layer": "Здания",
        "layer": "Здания",
        "proposal_method": "explicit-chord-closure",
        "closure_gap_wcs_xy_units": 0.01,
        "status": "native",
        "error_status": None,
        "native_area_units2": 100,
        "native_perimeter_units": 40,
        "loops": [{
            "role": "outer",
            "sampled_max_deviation_units": 0,
            "coordinates": [
                [20, 0, 0], [30, 0, 0], [30, 10, 0],
                [20, 10, 0], [20, 0.01, 0], [20, 0, 0],
            ],
        }],
    }]
    probe["summary"].update(
        area_proposals=1, area_proposal_candidates=1,
        area_proposal_rejected=0,
    )
    return probe


def test_native_area_proposal_is_signed_but_not_an_active_building(
    tmp_path: Path,
) -> None:
    snapshot = compile(near_closed_building_proposal_probe())
    assert snapshot.area_proposals is not None
    assert len(snapshot.area_proposals) == 1
    proposal = snapshot.area_proposals[0]
    assert proposal.source.handle == "BEEF"
    assert proposal.source.instance_chain == ["10"]
    assert proposal.preview.native_area_units2 == 100
    assert all(item.id != proposal.preview.id for item in snapshot.geometry)
    assert all(
        proposal.preview.id not in item.geometry_ids for item in snapshot.coverage
    )
    imported = build_dxf_import_from_snapshot(snapshot, source_sha256="a" * 64)
    building = next(item for item in imported.layers if item.source_name == "Здания")
    assert not building.geometry_complete
    assert building.unsupported_geometry_types == {"LWPOLYLINE": 1}
    assert len(imported.geometry.feature_collection["features"]) == 4
    snapshot_path = tmp_path / "native-proposals.json"
    snapshot_path.write_text(
        snapshot.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    inventory = _read_snapshot(snapshot_path, tmp_path)
    assert inventory.summary.payload_sha256 == snapshot.summary.payload_sha256

    tampered = snapshot.model_copy(deep=True)
    assert tampered.area_proposals is not None
    tampered.area_proposals[0].preview.loops[0].coordinates[1] = (31, 0, 0)
    with pytest.raises(CadSnapshotProviderError, match="proposal preview hash"):
        build_dxf_import_from_snapshot(tampered, source_sha256="a" * 64)
    snapshot_path.write_text(
        tampered.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="area proposal нарушает hash"):
        _read_snapshot(snapshot_path, tmp_path)


@pytest.mark.parametrize(
    "fault", ["old_producer", "wrong_gap", "wrong_layer", "missing_path", "duplicate"]
)
def test_native_area_proposal_rejects_false_source_evidence(fault: str) -> None:
    probe = near_closed_building_proposal_probe()
    if fault == "old_producer":
        probe["plugin_version"] = "0.1.35"
    elif fault == "wrong_gap":
        probe["area_proposals"][0]["closure_gap_wcs_xy_units"] = 1.0
    elif fault == "wrong_layer":
        probe["area_proposals"][0]["layer"] = "Дороги"
    elif fault == "missing_path":
        probe["paths"].pop()
        probe["coverage"].pop()
        probe["summary"].update(paths=2, source_instances=2, native=2)
    else:
        probe["area_proposals"].append(deepcopy(probe["area_proposals"][0]))
        probe["summary"].update(area_proposals=2, area_proposal_candidates=2)
    with pytest.raises((CadSnapshotAdmissionError, ValueError)):
        compile(probe)


def multi_outer_hatch_probe() -> dict:
    """A typed AutoCAD HATCH can own two disjoint surfaces and one hole."""
    probe = valid_probe()
    probe["plugin_version"] = "0.1.35"
    probe["summary"].update(paths=0, points=0)
    region = probe["regions"][0]
    region["native_area_units2"] = 146
    region["native_perimeter_units"] = 82.142
    region["loops"].extend(
        [
            {
                "role": "outer",
                "sampled_max_deviation_units": 0,
                "coordinates": [
                    [20, 0, 0], [30, 0, 0], [30, 10, 0],
                    [20, 10, 0], [20, 0, 0],
                ],
            },
            {
                "role": "hole",
                "sampled_max_deviation_units": 0,
                "coordinates": [
                    [22, 2, 0], [22, 4, 0], [24, 4, 0],
                    [24, 2, 0], [22, 2, 0],
                ],
            },
        ]
    )
    probe["coverage"][1]["entity_type"] = "AcDbHatch"
    return probe


def test_disjoint_native_hatch_surfaces_survive_both_provider_routes(
    tmp_path: Path,
) -> None:
    admitted = compile(multi_outer_hatch_probe())
    direct = build_dxf_import_from_snapshot(admitted, source_sha256="a" * 64)
    snapshot_path = tmp_path / "native-snapshot.json"
    snapshot_path.write_text(
        admitted.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    streamed = build_dxf_import_from_snapshot_path(
        snapshot_path,
        source=admitted.source,
        extraction=admitted.extraction,
        dependencies=tuple(admitted.dependencies or []),
        summary=admitted.summary,
        source_sha256="a" * 64,
        scratch_root=tmp_path,
    )
    for result in (direct, streamed):
        surface = result.geometry.feature_collection["features"][0]["geometry"]
        assert surface["type"] == "MultiPolygon"
        assert len(surface["coordinates"]) == 2
        assert sum(len(part) - 1 for part in surface["coordinates"]) == 1


def test_small_native_surface_at_large_wcs_offset_keeps_area() -> None:
    probe = valid_probe()
    region = probe["regions"][0]
    region["native_area_units2"] = 1
    region["native_perimeter_units"] = 4
    region["loops"][0]["sampled_max_deviation_units"] = 0
    region["loops"][0]["coordinates"] = [
        [100_000_000, 500_000_000, 0],
        [100_000_001, 500_000_000, 0],
        [100_000_001, 500_000_001, 0],
        [100_000_000, 500_000_001, 0],
        [100_000_000, 500_000_000, 0],
    ]
    admitted = compile(probe)
    assert _region_shape(admitted.geometry[0], 1).area == 1


def test_overlapping_native_hatch_outers_are_not_calculation_surface() -> None:
    probe = multi_outer_hatch_probe()
    region = probe["regions"][0]
    region["loops"].pop()  # no hole: isolate the overlapping outer shells
    region["loops"][1]["coordinates"] = [
        [5, 0, 0], [15, 0, 0], [15, 10, 0], [5, 10, 0], [5, 0, 0]
    ]
    region["native_area_units2"] = 150
    region["native_perimeter_units"] = 74.142
    admitted = compile(probe)
    with pytest.raises(CadSnapshotProviderError, match="overlapping or touching"):
        _region_shape(admitted.geometry[0], 1)


def test_unpaired_building_line_is_visible_but_area_is_partial(
    tmp_path: Path,
) -> None:
    admitted = compile(unpaired_native_building_probe())
    direct = build_dxf_import_from_snapshot(admitted, source_sha256="a" * 64)
    snapshot_path = tmp_path / "native-snapshot.json"
    snapshot_path.write_text(
        admitted.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    streamed = build_dxf_import_from_snapshot_path(
        snapshot_path,
        source=admitted.source,
        extraction=admitted.extraction,
        dependencies=tuple(admitted.dependencies or []),
        summary=admitted.summary,
        source_sha256="a" * 64,
        scratch_root=tmp_path,
    )
    for result in (direct, streamed):
        layer = next(item for item in result.layers if item.source_name == "Здания")
        assert not layer.geometry_complete
        assert layer.unsupported_geometry_types == {"LWPOLYLINE": 1}
        assert any(
            "Линии зданий без подтверждённой площади (1)" in warning
            for warning in result.warnings
        )
        assert len(result.geometry.feature_collection["features"]) == 4


def test_derived_region_keeps_both_paths_and_works_in_both_provider_routes(
    tmp_path: Path,
) -> None:
    admitted = compile(paired_native_region_probe())
    assert len(admitted.geometry) == 3
    assert admitted.coverage[0].geometry_ids == [
        "path/10/ACE2",
        "derived-region/10/ACE2",
    ]
    assert admitted.coverage[1].geometry_ids == ["path/10/E201"]
    direct = build_dxf_import_from_snapshot(admitted, source_sha256="a" * 64)
    snapshot_path = tmp_path / "native-snapshot.json"
    snapshot_path.write_text(
        admitted.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    streamed = build_dxf_import_from_snapshot_path(
        snapshot_path,
        source=admitted.source,
        extraction=admitted.extraction,
        dependencies=tuple(admitted.dependencies or []),
        summary=admitted.summary,
        source_sha256="a" * 64,
        scratch_root=tmp_path,
    )
    for result in (direct, streamed):
        features = result.geometry.feature_collection["features"]
        assert len(features) == 3
        area = next(
            feature for feature in features if feature["geometry"]["type"] == "Polygon"
        )
        assert area["properties"]["source_derived_from"] == [
            {"handle": "ACE2", "instance_chain": ["10"]},
            {"handle": "E201", "instance_chain": ["10"]},
        ]
        assert next(
            layer for layer in result.layers if layer.source_name == "Здания"
        ).projected_geometry_types == {"LWPOLYLINE": 2}

    # This fixture tests the application contract, not the original DWG.
    direct.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "id": "site-fixture",
            "properties": {"source_layer": "SITE", "kind": "site_border"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-20, -20], [30, -20], [30, 30], [-20, 30], [-20, -20]]
                ],
            },
        }
    )
    site_layer = Layer(
        id="site-fixture",
        source_name="SITE",
        suggested_kind=LayerKind.SITE_BORDER,
        mapped_kind=LayerKind.SITE_BORDER,
        object_count=1,
        color="#000000",
    )
    project = Project(
        name="Derived native region contract",
        source_geometry=direct.geometry,
        layers=[*direct.layers, site_layer],
    )
    for layer in project.layers:
        layer.mapping_review_required = False
        layer.mapping_confirmed = True
    project.geometry = ShapelyGeometryEngine().calculate(project)
    checker = PositionChecker(project)
    assert checker.check(5, 5, 0.1, "shrub") is not None
    assert checker.check(25, 25, 0.1, "shrub") is None
    assert not _native_building_paths_without_surface(
        {"Здания": LayerKind.BUILDING},
        project.source_geometry.feature_collection["features"],
    )

    # A new visible path on the mapped building layer has no proven interior.
    # Its presence must not silently upgrade the calculation to "complete".
    project.source_geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "id": "unpaired-building-path",
            "properties": {
                "source_layer": "Здания",
                "source_geometry_provider": "autocad_snapshot_v1",
                "source_handle": "BEEF",
                "source_instance_chain": ["10"],
                "source_closed_path": False,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[12, 0], [14, 0]],
            },
        }
    )
    with pytest.raises(ValueError, match="Карта содержит только часть объектов"):
        ShapelyGeometryEngine().calculate(project)


@pytest.mark.parametrize("fault", ["missing_path", "wrong_layer", "old_producer"])
def test_derived_region_cannot_hide_invalid_provenance(fault: str) -> None:
    probe = paired_native_region_probe()
    if fault == "missing_path":
        probe["paths"].pop()
        probe["coverage"].pop()
        probe["summary"].update(source_instances=1, native=1, paths=1)
    elif fault == "wrong_layer":
        probe["paths"][1]["layer"] = "Дорога"
    else:
        probe["plugin_version"] = "0.1.33"
    with pytest.raises(CadSnapshotAdmissionError):
        compile(probe)


def probe_with_failed_region() -> dict:
    probe = valid_probe()
    probe["regions"].append(
        {
            "handle": "FA1ED",
            "instance_chain": [],
            "status": "unresolved",
            "error_status": 1,
            "loops": [],
        }
    )
    probe["coverage"].append(
        {
            "handle": "FA1ED",
            "instance_chain": [],
            "entity_type": "AcDbHatch",
            "layer": "SITE",
            "status": "unresolved",
            "method": "native-topology-failed",
            "reason": "native HATCH topology extraction failed",
        }
    )
    probe["summary"].update(
        regions=2, unresolved=1, source_instances=4, unresolved_instances=2
    )
    return probe


def test_failed_region_keeps_healthy_geometry_and_explicit_gap() -> None:
    snapshot = compile(probe_with_failed_region())
    assert len(snapshot.geometry) == 1
    assert snapshot.summary.unresolved == 2
    assert snapshot.coverage[-1].status == "unresolved"
    assert snapshot.coverage[-1].geometry_ids == []
    assert snapshot.coverage[-1].reason == "native HATCH topology extraction failed"


@pytest.mark.parametrize(
    "fault", ["missing_coverage", "native_coverage", "missing_reason", "wrong_counts"]
)
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
