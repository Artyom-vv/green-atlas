"""Допуск захвата AutoCAD: полные хеши, точность, учёт объектов и границы XREF."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast
from weakref import ReferenceType, ref

import pytest
from pydantic import ValidationError

from app.cad_bridge import CadSnapshotAdmissionError, compile_region_probe, compiler
from app.cad_bridge.contracts import (
    CadSnapshot,
    CoverageRecord,
    NativeAreaProposal,
    PathGeometry,
    PointGeometry,
    RegionGeometry,
)
from app.cad_bridge.integrity import snapshot_payload_sha256
from app.cad_bridge.provider import (
    CadSnapshotProviderError,
    _region_shape,
    build_project_from_snapshot,
)
from app.cad_intake.inspection.worker import _read_snapshot
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.geometry.checking.adapters import (
    ShapelyGeometryEngine,
    _native_building_paths_without_surface,
)
from app.geometry.checking.domain import PositionChecker
from app.projects.contracts import Project
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason


def native_project(snapshot: CadSnapshot, *, source_sha256: str) -> Project:
    return build_project_from_snapshot(
        Project(name="Native projection"),
        snapshot,
        filename="Drawing.autocad.json",
        imported_at="2026-10-08T12:00:00+03:00",
        source_sha256=source_sha256,
        source_size=1,
    )


@pytest.mark.parametrize("with_proposal", [False, True])
def test_payload_hash_preserves_canonical_identity_without_full_dump(
    monkeypatch: pytest.MonkeyPatch,
    with_proposal: bool,
) -> None:
    probe = near_closed_building_proposal_probe() if with_proposal else valid_probe()
    snapshot = compile_region_probe(
        probe,
        autocad_version="2027",
        target="macos-arm64",
    )
    payload = snapshot.model_dump(
        by_alias=True,
        mode="json",
        exclude={"summary"},
        exclude_none=True,
    )
    expected = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    def forbidden_dump(*args: object, **kwargs: object) -> None:
        raise AssertionError("Do not materialize the full snapshot payload")

    monkeypatch.setattr(CadSnapshot, "model_dump", forbidden_dump)
    assert snapshot_payload_sha256(snapshot) == expected
    snapshot.coverage[0].layer = "Изменённый слой"
    assert snapshot_payload_sha256(snapshot) != expected


@pytest.mark.parametrize("with_proposal", [False, True])
def test_live_admission_releases_native_graph_without_changing_snapshot(
    monkeypatch: pytest.MonkeyPatch, with_proposal: bool
) -> None:
    probe = near_closed_building_proposal_probe() if with_proposal else valid_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    expected = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )

    class NativeGraph(dict[str, Any]):
        pass

    captured: list[ReferenceType[NativeGraph]] = []
    original_loads = json.loads
    original_validate = CadSnapshot.model_validate

    def load_native(value: bytes | bytearray) -> NativeGraph:
        graph = NativeGraph(original_loads(value))
        captured.append(ref(graph))
        return graph

    def admit(cls: type[CadSnapshot], payload: object, **kwargs: Any) -> CadSnapshot:
        assert captured and captured[-1]() is None
        return original_validate(payload, **kwargs)

    monkeypatch.setattr(compiler.json, "loads", load_native)
    monkeypatch.setattr(CadSnapshot, "model_validate", classmethod(admit))
    actual = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    assert actual == expected
    assert snapshot_payload_sha256(actual) == expected.summary.payload_sha256


@pytest.mark.parametrize("with_proposal", [False, True])
def test_live_file_preserves_bytes_snapshot_and_hash(
    tmp_path: Path, with_proposal: bool
) -> None:
    probe = near_closed_building_proposal_probe() if with_proposal else valid_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe, ensure_ascii=False).encode() + b"\n \t"
    expected = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    payload, tolerance = compiler._normalize_probe_payload(
        probe,
        autocad_version="2027",
        target="macos-arm64",
        live_source_sha256=sha256(content).hexdigest(),
    )
    from_dict = compiler._admit_probe_payload(payload, tolerance)
    assert from_dict == expected
    path = tmp_path / "capture.json"
    path.write_bytes(content)
    actual, source_size = compiler.compile_live_document_file(
        path,
        expected_source_sha256=sha256(content).hexdigest(),
        autocad_version="2027",
        target="macos-arm64",
    )
    assert actual == expected
    assert source_size == len(content)
    assert snapshot_payload_sha256(actual) == expected.summary.payload_sha256


@pytest.mark.parametrize("with_proposal", [False, True])
def test_admission_reuses_private_lists_and_releases_normalized_records(
    monkeypatch: pytest.MonkeyPatch, with_proposal: bool
) -> None:
    probe = near_closed_building_proposal_probe() if with_proposal else valid_probe()
    original_probe = deepcopy(probe)
    normalize = compiler._normalize_probe_payload
    validate = CadSnapshot.model_validate
    lists: dict[str, list[object]] = {}

    class NormalizedRecord(dict[str, Any]):
        pass

    records: list[ReferenceType[NormalizedRecord]] = []

    def tracked_normalize(
        *args: Any, **kwargs: Any
    ) -> tuple[compiler._NormalizedProbePayload, float]:
        payload, tolerance = normalize(*args, **kwargs)
        for field in ("coverage", "geometry", "area_proposals"):
            if field in payload:
                values = cast(list[Any], payload[field])
                for index in range(len(values)):
                    values[index] = NormalizedRecord(values[index])
                    records.append(ref(values[index]))
                lists[field] = values
        return payload, tolerance

    def typed_validate(
        cls: type[CadSnapshot], payload: Any, **kwargs: Any
    ) -> CadSnapshot:
        assert all(record() is None for record in records)
        for field, values in lists.items():
            assert payload[field] is values
            assert all(
                isinstance(
                    item,
                    (
                        CoverageRecord,
                        PathGeometry,
                        RegionGeometry,
                        PointGeometry,
                        NativeAreaProposal,
                    ),
                )
                for item in values
            )
        result = validate(payload, **kwargs)
        for field, values in lists.items():
            assert all(
                actual is original
                for actual, original in zip(getattr(result, field), values, strict=True)
            )
        return result

    monkeypatch.setattr(compiler, "_normalize_probe_payload", tracked_normalize)
    monkeypatch.setattr(CadSnapshot, "model_validate", classmethod(typed_validate))
    snapshot = compile(probe)
    assert probe == original_probe
    assert snapshot_payload_sha256(snapshot) == snapshot.summary.payload_sha256


@pytest.mark.parametrize(
    "fields",
    [
        ("coverage",),
        ("geometry",),
        ("area_proposals",),
        ("coverage", "geometry", "area_proposals"),
    ],
)
def test_record_admission_preserves_pydantic_error_locations(
    fields: tuple[str, ...],
) -> None:
    payload, tolerance = compiler._normalize_probe_payload(
        near_closed_building_proposal_probe(),
        autocad_version="2027",
        target="macos-arm64",
    )
    if "coverage" in fields:
        payload["coverage"][0]["identity"]["handle"] = "invalid"
    if "geometry" in fields:
        region = payload["geometry"][0]
        assert isinstance(region, dict)
        region["loops"][0]["coordinates"][0][0] = "invalid"
    if "area_proposals" in fields:
        payload["area_proposals"][0]["preview"]["native_area_units2"] = -1
    with pytest.raises(ValidationError) as original:
        CadSnapshot.model_validate(payload)
    with pytest.raises(ValidationError) as incremental:
        compiler._admit_probe_payload(payload, tolerance)
    assert incremental.value.errors(include_url=False) == original.value.errors(
        include_url=False
    )


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("coverage", "duplicate source instance in coverage"),
        ("geometry", "duplicate geometry id"),
        ("reference", "geometry referenced by multiple source instances"),
        ("missing", "coverage references missing geometry"),
        ("unreferenced", "unreferenced geometry in snapshot"),
        ("provenance", "geometry provenance mismatch"),
        ("reference_before_missing", "coverage references missing geometry"),
        ("coverage_before_geometry", "duplicate source instance in coverage"),
    ],
)
def test_incremental_admission_preserves_global_ledger_failures(
    fault: str, message: str
) -> None:
    payload, tolerance = compiler._normalize_probe_payload(
        valid_probe(), autocad_version="2027", target="macos-arm64"
    )
    resolved = payload["coverage"][1]
    if fault in {"coverage", "coverage_before_geometry"}:
        payload["coverage"].append(deepcopy(resolved))
    if fault in {"geometry", "coverage_before_geometry"}:
        payload["geometry"].append(deepcopy(payload["geometry"][0]))
    elif fault in {"reference", "reference_before_missing"}:
        resolved["geometry_ids"].append(resolved["geometry_ids"][0])
    if fault in {"missing", "reference_before_missing"}:
        resolved["geometry_ids"].append("region/missing")
    elif fault == "unreferenced":
        unused = deepcopy(payload["geometry"][0])
        assert isinstance(unused, dict)
        unused["id"] = "region/unused"
        payload["geometry"].append(unused)
    elif fault == "provenance":
        region = payload["geometry"][0]
        assert isinstance(region, dict)
        region["identity"]["handle"] = "FFFF"
    with pytest.raises(ValidationError, match=message) as original:
        CadSnapshot.model_validate(payload)
    with pytest.raises(ValidationError, match=message) as incremental:
        compiler._admit_probe_payload(payload, tolerance)
    assert [
        (error["type"], error["loc"], error["msg"])
        for error in incremental.value.errors()
    ] == [
        (error["type"], error["loc"], error["msg"]) for error in original.value.errors()
    ]


@pytest.mark.parametrize("with_proposal", [False, True])
@pytest.mark.parametrize("with_quarantine", [False, True])
def test_file_normalization_releases_owned_records_before_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_proposal: bool,
    with_quarantine: bool,
) -> None:
    probe = (
        near_closed_building_proposal_probe()
        if with_proposal
        else probe_with_native_primitives()
    )
    if with_quarantine:
        path = deepcopy(probe["paths"][0])
        coverage = deepcopy(
            next(item for item in probe["coverage"] if item["handle"] == path["handle"])
        )
        path.update(
            handle="FADE",
            closed=True,
            coordinates=[[0, 0, 0], [2, 0, 0], [1, 0, 0], [0, 0, 0]],
        )
        coverage["handle"] = "FADE"
        probe["paths"].append(path)
        probe["coverage"].append(coverage)
        for field in ("paths", "source_instances", "native"):
            probe["summary"][field] += 1
    probe["capture_mode"] = "live_document"
    original_probe = deepcopy(probe)
    content = json.dumps(probe, ensure_ascii=False).encode()
    digest = sha256(content).hexdigest()
    expected = compiler.compile_live_document(
        content, autocad_version="2027", target="macos-arm64"
    )
    payload, tolerance = compiler._normalize_probe_payload(
        probe,
        autocad_version="2027",
        target="macos-arm64",
        live_source_sha256=digest,
    )
    assert compiler._admit_probe_payload(payload, tolerance) == expected
    assert probe == original_probe
    source = tmp_path / "native.json"
    source.write_bytes(content)

    class OwnedRecord(dict[str, Any]):
        pass

    captured: dict[str, list[ReferenceType[OwnedRecord]]] = {}
    read_probe = compiler._read_live_probe
    normalize_paths = compiler._normalize_paths
    normalize_coverage = compiler._normalize_coverage

    def tracked_read(path: Path, expected_sha256: str) -> tuple[dict[str, Any], int]:
        graph, size = read_probe(path, expected_sha256)
        for field in ("coverage", "regions", "paths", "points", "area_proposals"):
            captured[field] = []
            values = graph.get(field, [])
            for index in range(len(values)):
                values[index] = OwnedRecord(values[index])
                captured[field].append(ref(values[index]))
        return graph, size

    def paths_without_native_graph(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["consume_owned"] is True
        result = normalize_paths(*args, **kwargs)
        assert all(record() is None for record in captured["paths"])
        assert all(set(path) == {"handle", "instance_chain"} for path in result[0])
        return result

    def coverage_without_native_geometry(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["consume_owned"] is True
        assert all(
            record() is None
            for field, records in captured.items()
            if field != "coverage"
            for record in records
        )
        assert all(record() is not None for record in captured["coverage"])
        result = normalize_coverage(*args, **kwargs)
        assert all(record() is None for record in captured["coverage"])
        return result

    monkeypatch.setattr(compiler, "_read_live_probe", tracked_read)
    monkeypatch.setattr(compiler, "_normalize_paths", paths_without_native_graph)
    monkeypatch.setattr(
        compiler, "_normalize_coverage", coverage_without_native_geometry
    )
    actual, size = compiler.compile_live_document_file(
        source,
        expected_source_sha256=digest,
        autocad_version="2027",
        target="macos-arm64",
    )
    assert actual == expected
    assert size == len(content)
    assert actual.summary.payload_sha256 == snapshot_payload_sha256(expected)
    assert source.read_bytes() == content
    if with_quarantine:
        quarantined = next(
            record for record in actual.coverage if record.identity.handle == "FADE"
        )
        assert quarantined.status == "unresolved"
        assert quarantined.geometry_ids == []


@pytest.mark.parametrize("fault", ["path", "duplicate_path", "coverage"])
def test_file_owned_admission_keeps_failure_priority_and_source_bytes(
    tmp_path: Path, fault: str
) -> None:
    probe = probe_with_native_primitives()
    probe["capture_mode"] = "live_document"
    if fault == "path":
        probe["paths"][0]["sampled_max_deviation_units"] = -1
        probe["coverage"][0].pop("method")
    elif fault == "duplicate_path":
        probe["paths"].append(deepcopy(probe["paths"][0]))
        probe["coverage"][0].pop("method")
    else:
        probe["coverage"][0]["reason"] = None
    original_probe = deepcopy(probe)
    content = json.dumps(probe).encode()
    source = tmp_path / "native.json"
    source.write_bytes(content)
    with pytest.raises((CadSnapshotAdmissionError, ValidationError)) as original:
        compiler.compile_live_document(
            content, autocad_version="2027", target="macos-arm64"
        )
    with pytest.raises(type(original.value)) as consumed:
        compiler.compile_live_document_file(
            source,
            expected_source_sha256=sha256(content).hexdigest(),
            autocad_version="2027",
            target="macos-arm64",
        )
    assert str(consumed.value) == str(original.value)
    assert source.read_bytes() == content
    assert probe == original_probe


def test_path_normalization_reuses_owned_coordinates_until_snapshot_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_paths: list[ReferenceType[PathGeometry]] = []
    original_path_validation = PathGeometry.model_validate
    original_snapshot_validation = CadSnapshot.model_validate

    def normalize(
        cls: type[PathGeometry], payload: object, **kwargs: Any
    ) -> PathGeometry:
        path = original_path_validation(payload, **kwargs)
        normalized_paths.append(ref(path))
        return path

    def admit(cls: type[CadSnapshot], payload: object, **kwargs: Any) -> CadSnapshot:
        assert normalized_paths and all(path() is not None for path in normalized_paths)
        return original_snapshot_validation(payload, **kwargs)

    monkeypatch.setattr(PathGeometry, "model_validate", classmethod(normalize))
    monkeypatch.setattr(CadSnapshot, "model_validate", classmethod(admit))
    probe = probe_with_native_primitives()
    probe["paths"].append({**deepcopy(probe["paths"][0]), "handle": "C2"})
    path_coverage = next(item for item in probe["coverage"] if item["handle"] == "C1")
    probe["coverage"].append({**deepcopy(path_coverage), "handle": "C2"})
    probe["summary"].update(source_instances=6, native=4, paths=2)
    snapshot = compile(probe)
    assert len(normalized_paths) == 2
    assert sum(isinstance(item, PathGeometry) for item in snapshot.geometry) == 2
    assert all(
        path() is geometry
        for path, geometry in zip(normalized_paths, snapshot.geometry[1:3], strict=True)
    )
    probe["paths"][0]["coordinates"][1][0] = 999
    retained = normalized_paths[0]()
    assert retained is not None
    assert retained.coordinates[1][0] == 20
    del snapshot, retained
    assert all(path() is None for path in normalized_paths)


@pytest.mark.parametrize("fault", ["trailing", "malformed", "hash", "geometry"])
def test_live_file_rejects_untrusted_input(tmp_path: Path, fault: str) -> None:
    probe = valid_probe()
    probe["capture_mode"] = "live_document"
    if fault == "geometry":
        probe["regions"][0]["loops"][0]["coordinates"][0][0] = "invalid"
    content = json.dumps(probe).encode()
    if fault == "trailing":
        content += b"{}"
    elif fault == "malformed":
        content = content[:-1]
    path = tmp_path / "capture.json"
    path.write_bytes(content)
    expected_hash = "0" * 64 if fault == "hash" else sha256(content).hexdigest()
    with pytest.raises((CadSnapshotAdmissionError, ValueError)):
        compiler.compile_live_document_file(
            path,
            expected_source_sha256=expected_hash,
            autocad_version="2027",
            target="macos-arm64",
        )


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


def compile(probe: dict, package_root: Path | None = None) -> CadSnapshot:
    return compile_region_probe(
        probe,
        autocad_version="2027.0.1",
        target="macos-arm64",
        package_root=package_root,
    )


@pytest.mark.parametrize("version", ["0.1.42", "0.1.47", "0.1.48"])
@pytest.mark.parametrize("live", [False, True])
def test_current_producers_keep_native_capture_contract(
    version: str, live: bool
) -> None:
    probe = valid_probe()
    probe["plugin_version"] = version
    probe["summary"].update(
        paths=0,
        points=0,
        area_proposals=0,
        area_proposal_candidates=0,
        area_proposal_rejected=0,
    )
    target: Literal["windows-x86_64", "macos-arm64"] = (
        "windows-x86_64" if version == "0.1.42" else "macos-arm64"
    )
    if live:
        probe["capture_mode"] = "live_document"
        snapshot = compiler.compile_live_document(
            json.dumps(probe).encode(), autocad_version="2027", target=target
        )
    else:
        snapshot = compile_region_probe(probe, autocad_version="2027", target=target)
    assert snapshot.extraction.plugin_version == version
    assert snapshot.extraction.target == target
    assert snapshot.coverage[1].geometry_ids == ["region/10/2F/A12"]
    assert snapshot.summary.payload_sha256 == snapshot_payload_sha256(snapshot)


@pytest.mark.parametrize(
    "version", ["0.1.18", "0.1.29", "0.1.31", "0.1.32", "0.1.39", "0.1.49"]
)
def test_unqualified_producer_gaps_remain_rejected(version: str) -> None:
    probe = valid_probe()
    probe["plugin_version"] = version
    with pytest.raises(
        CadSnapshotAdmissionError, match="unsupported native probe plugin version"
    ):
        compile(probe)


@pytest.mark.parametrize("field", ["paths", "points"])
@pytest.mark.parametrize("version", ["0.1.6", "0.1.7", "0.1.48"])
def test_producer_capability_requires_primitive_summary_counts(
    version: str, field: str
) -> None:
    probe = valid_probe()
    probe["plugin_version"] = version
    probe["summary"].update(
        paths=0,
        points=0,
        area_proposals=0,
        area_proposal_candidates=0,
        area_proposal_rejected=0,
    )
    probe["summary"][field] = 1
    if version == "0.1.6":
        assert len(compile(probe).geometry) == 1
    else:
        with pytest.raises(
            CadSnapshotAdmissionError,
            match=f"native {field[:-1]} count differs from geometry",
        ):
            compile(probe)


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
        [20, 0, 0],
        [30, 0, 0],
        [30, 10, 0],
        [20, 10, 0],
        [20, 0.01, 0],
    ]
    probe["area_proposals"] = [
        {
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
            "loops": [
                {
                    "role": "outer",
                    "sampled_max_deviation_units": 0,
                    "coordinates": [
                        [20, 0, 0],
                        [30, 0, 0],
                        [30, 10, 0],
                        [20, 10, 0],
                        [20, 0.01, 0],
                        [20, 0, 0],
                    ],
                }
            ],
        }
    ]
    probe["summary"].update(
        area_proposals=1,
        area_proposal_candidates=1,
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
    imported = native_project(snapshot, source_sha256="a" * 64)
    assert imported.source_geometry is not None
    building = next(item for item in imported.layers if item.source_name == "Здания")
    assert not building.geometry_complete
    assert building.unsupported_geometry_types == {"LWPOLYLINE": 1}
    assert len(imported.source_geometry.feature_collection["features"]) == 4
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
        native_project(tampered, source_sha256="a" * 64)
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
                    [20, 0, 0],
                    [30, 0, 0],
                    [30, 10, 0],
                    [20, 10, 0],
                    [20, 0, 0],
                ],
            },
            {
                "role": "hole",
                "sampled_max_deviation_units": 0,
                "coordinates": [
                    [22, 2, 0],
                    [22, 4, 0],
                    [24, 4, 0],
                    [24, 2, 0],
                    [22, 2, 0],
                ],
            },
        ]
    )
    probe["coverage"][1]["entity_type"] = "AcDbHatch"
    return probe


def test_disjoint_native_hatch_surfaces_survive_native_projection() -> None:
    admitted = compile(multi_outer_hatch_probe())
    direct = native_project(admitted, source_sha256="a" * 64)
    result = direct
    assert result.source_geometry is not None
    surface = result.source_geometry.feature_collection["features"][0]["geometry"]
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
    surface = admitted.geometry[0]
    assert isinstance(surface, RegionGeometry)
    assert _region_shape(surface, 1).area == 1


def test_overlapping_native_hatch_outers_are_not_calculation_surface() -> None:
    probe = multi_outer_hatch_probe()
    region = probe["regions"][0]
    region["loops"].pop()  # no hole: isolate the overlapping outer shells
    region["loops"][1]["coordinates"] = [
        [5, 0, 0],
        [15, 0, 0],
        [15, 10, 0],
        [5, 10, 0],
        [5, 0, 0],
    ]
    region["native_area_units2"] = 150
    region["native_perimeter_units"] = 74.142
    admitted = compile(probe)
    surface = admitted.geometry[0]
    assert isinstance(surface, RegionGeometry)
    with pytest.raises(CadSnapshotProviderError, match="overlapping or touching"):
        _region_shape(surface, 1)


def test_unpaired_building_line_is_visible_but_area_is_partial() -> None:
    admitted = compile(unpaired_native_building_probe())
    direct = native_project(admitted, source_sha256="a" * 64)
    result = direct
    assert result.source_file is not None
    assert result.source_geometry is not None
    layer = next(item for item in result.layers if item.source_name == "Здания")
    assert not layer.geometry_complete
    assert layer.unsupported_geometry_types == {"LWPOLYLINE": 1}
    assert any(
        "Линии зданий без подтверждённой площади (1)" in warning
        for warning in result.source_file.warnings
    )
    assert len(result.source_geometry.feature_collection["features"]) == 4


def test_derived_region_keeps_both_paths_and_works_in_native_projection() -> None:
    admitted = compile(paired_native_region_probe())
    assert len(admitted.geometry) == 3
    assert admitted.coverage[0].geometry_ids == [
        "path/10/ACE2",
        "derived-region/10/ACE2",
    ]
    assert admitted.coverage[1].geometry_ids == ["path/10/E201"]
    direct = native_project(admitted, source_sha256="a" * 64)
    result = direct
    assert result.source_geometry is not None
    features = result.source_geometry.feature_collection["features"]
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
    features.append(
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
        source_geometry=result.source_geometry,
        layers=[*direct.layers, site_layer],
    )
    for layer in project.layers:
        layer.mapping_review_required = False
        layer.mapping_confirmed = True
    project.geometry = ShapelyGeometryEngine().calculate(project)
    checker = PositionChecker(project)
    assert checker.check(5, 5, 0.1, "shrub") is not None
    assert checker.check(25, 25, 0.1, "shrub") is None
    assert project.source_geometry is not None
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
    with pytest.raises(DomainFailure) as failure:
        ShapelyGeometryEngine().calculate(project)
    assert failure.value.reason is FailureReason.GEOMETRY_INCOMPLETE_LAYERS
    assert failure.value.params == {"layer_names": ["Здания"]}
    assert "Карта содержит только часть объектов" in public_failure_message(
        failure.value
    )


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
    assert isinstance(snapshot.geometry[0], RegionGeometry)
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
    assert isinstance(path, PathGeometry)
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
def test_rejects_probe_that_cannot_prove_snapshot(
    mutation: Callable[[dict], object], message: str
) -> None:
    probe = deepcopy(valid_probe())
    mutation(probe)
    with pytest.raises(CadSnapshotAdmissionError, match=message):
        compile(probe)


def test_payload_and_geometry_hashes_are_deterministic() -> None:
    first = compile(valid_probe())
    second = compile(valid_probe())
    assert first.summary.payload_sha256 == second.summary.payload_sha256
    assert first.geometry[0].content_sha256 == second.geometry[0].content_sha256


@pytest.mark.parametrize(
    ("factory", "expected_hash"),
    [
        (
            valid_probe,
            "47806fee8b7ef96b213257c9010914c1a9488d5507db53602f4615913fd9117f",
        ),
        (
            probe_with_native_primitives,
            "c6ab954be84cbeb2a48b600d6ec8b24aa45ff611203ae3b7268936501f594569",
        ),
        (
            near_closed_building_proposal_probe,
            "ea967ea26862f2ceaef3f510c66857a55c1a0b3552e73823221422ca8fe9cb2e",
        ),
    ],
)
def test_snapshot_hash_preserves_qualified_record_normalization(
    factory: Callable[[], dict[str, Any]], expected_hash: str
) -> None:
    # Fixed before extracting path admission, not recomputed from its output.
    snapshot = compile_region_probe(
        factory(), autocad_version="2027", target="macos-arm64"
    )
    assert snapshot.summary.payload_sha256 == expected_hash


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


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize(
    "field", ["record_handle", "block_name", "stored_path", "status"]
)
@pytest.mark.parametrize("value", [None, "", 1, True, [], {}])
def test_xref_metadata_rejects_invalid_types_without_coercion(
    tmp_path: Path, live: bool, field: str, value: object
) -> None:
    probe = probe_with_xref(tmp_path)
    probe["xref_dependencies"][0][field] = value
    before = deepcopy(probe)
    with pytest.raises(CadSnapshotAdmissionError, match="metadata is incomplete"):
        if live:
            probe["capture_mode"] = "live_document"
            compiler.compile_live_document(
                json.dumps(probe).encode(),
                autocad_version="2027",
                target="macos-arm64",
            )
        else:
            compile(probe, tmp_path)
    if live:
        before["capture_mode"] = "live_document"
    assert probe == before


@pytest.mark.parametrize("value", [b"resolved", bytearray(b"resolved")])
def test_xref_status_keeps_native_string_contract(
    tmp_path: Path, value: object
) -> None:
    probe = probe_with_xref(tmp_path)
    probe["xref_dependencies"][0]["status"] = value
    with pytest.raises(CadSnapshotAdmissionError, match="metadata is incomplete"):
        compile(probe, tmp_path)


@pytest.mark.parametrize("field", ["resolved_path", "sha256", "bytes"])
@pytest.mark.parametrize("value", [None, "", "1", 1.5, [], {}])
def test_live_and_unresolved_xrefs_skip_file_claim_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    from app.cad_bridge.probe_contracts import NativeXrefFileClaim

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "Файловый допуск не относится к live или отсутствующему XREF"
        )

    live = probe_with_xref(tmp_path)
    live["capture_mode"] = "live_document"
    live["xref_dependencies"][0][field] = value
    missing = probe_with_missing_xref()
    missing["xref_dependencies"][0][field] = value
    monkeypatch.setattr(NativeXrefFileClaim, "model_validate", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    snapshot = compiler.compile_live_document(
        json.dumps(live).encode(), autocad_version="2027", target="macos-arm64"
    )
    assert snapshot.dependencies is None
    assert snapshot.live_references is not None
    assert snapshot.live_references[0].id == "xref/AA"
    unresolved = compile(missing)
    assert unresolved.dependencies is None
    assert unresolved.coverage[-1].status == "unresolved"


@pytest.mark.parametrize("bytes_value", [True, 1])
def test_file_claim_preserves_integer_contract_after_physical_verification(
    tmp_path: Path, bytes_value: int | bool
) -> None:
    probe = probe_with_xref(tmp_path)
    child = tmp_path / "references" / "child.dxf"
    child.write_bytes(b"x")
    probe["xref_dependencies"][0].update(
        bytes=bytes_value, sha256=sha256(b"x").hexdigest()
    )
    probe["summary"]["xref_block_references"] = True
    snapshot = compile(probe, tmp_path)
    assert snapshot.dependencies is not None
    assert snapshot.dependencies[0].bytes == 1
    assert snapshot.dependencies[0].sha256 == sha256(b"x").hexdigest()


@pytest.mark.parametrize("field", ["resolved_path", "bytes", "sha256"])
def test_incomplete_xref_claim_precedes_missing_package_root(
    tmp_path: Path, field: str
) -> None:
    probe = probe_with_xref(tmp_path)
    probe["xref_dependencies"][0][field] = None
    with pytest.raises(CadSnapshotAdmissionError, match="metadata is incomplete"):
        compile(probe)


def test_duplicate_xref_precedes_invalid_second_file_claim(tmp_path: Path) -> None:
    probe = probe_with_xref(tmp_path)
    duplicate = deepcopy(probe["xref_dependencies"][0])
    duplicate.update(resolved_path=None, sha256=None, bytes=None)
    probe["xref_dependencies"].append(duplicate)
    probe["summary"]["xref_dependency_records"] = 2
    with pytest.raises(CadSnapshotAdmissionError, match="duplicate native XREF"):
        compile(probe, tmp_path)


def test_xref_diagnostic_catalog_is_complete() -> None:
    from app.cad_bridge.config import XREF_DIAGNOSTICS
    from app.cad_bridge.probe_contracts import XrefDiagnostic

    assert set(XREF_DIAGNOSTICS) == set(XrefDiagnostic)
    assert all(XREF_DIAGNOSTICS.values())
