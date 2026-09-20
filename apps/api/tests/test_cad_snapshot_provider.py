from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from app.cad_bridge import CadSnapshot
from app.cad_bridge.compiler import _canonical_sha256
from app.cad_bridge.provider import (
    CadSnapshotProviderError,
    apply_cad_snapshot,
    build_dxf_import_from_snapshot,
)
from app.dxf_import.application import ImportApplication
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.geometry.adapters import _unprojectable_physical_layers
from app.geometry.contracts import GeometrySnapshot
from app.projects.adapters import InMemoryProjectRepository
from app.projects.contracts import Project


def snapshot(
    source_sha256: str = "a" * 64,
    *,
    with_dependency: bool = False,
    with_primitives: bool = False,
) -> CadSnapshot:
    identity = {"handle": "A12", "instance_chain": ["10"]}
    payload = {
        "schema": "green-atlas.autocad-snapshot/1",
        "source": {
            "sha256": source_sha256,
            "saved": True,
            "units_code": 6,
            "document_revision": "revision",
        },
        "extraction": {
            "autocad_version": "2027.0.1",
            "plugin_version": "0.1.3",
            "target": "macos-arm64",
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": 0.001,
        },
        "coverage": [
            {
                "identity": identity,
                "entity_type": "AcDbRegion",
                "layer": "BUILDINGS",
                "status": "native",
                "method": "AcBr loop traversal",
                "geometry_ids": ["region/10/A12"],
            }
        ],
        "geometry": [
            {
                "id": "region/10/A12",
                "identity": identity,
                "kind": "region",
                "loops": [
                    {
                        "role": "outer",
                        "closed": True,
                        "coordinates": [
                            [0, 0, 0],
                            [10, 0, 0],
                            [10, 10, 0],
                            [0, 10, 0],
                            [0, 0, 0],
                        ],
                    },
                    {
                        "role": "hole",
                        "closed": True,
                        "coordinates": [
                            [2, 2, 0],
                            [2, 4, 0],
                            [4, 4, 0],
                            [4, 2, 0],
                            [2, 2, 0],
                        ],
                    },
                ],
                "native_area_units2": 96,
                "native_perimeter_units": 48,
                "achieved_tolerance_m": 0.0005,
                "content_sha256": "0" * 64,
            }
        ],
        "summary": {
            "source_instances": 1,
            "native": 1,
            "converted": 0,
            "context": 0,
            "unresolved": 0,
            "payload_sha256": "0" * 64,
            "complete": True,
        },
    }
    if with_dependency:
        payload["dependencies"] = [
            {
                "id": "xref/AA",
                "kind": "xref",
                "path": "references/child.dxf",
                "sha256": "d" * 64,
                "bytes": 128,
                "record_handle": "AA",
                "block_name": "CHILD",
                "stored_path": "references/child.dxf",
            }
        ]
        payload["coverage"].append(
            {
                "identity": {"handle": "20", "instance_chain": []},
                "entity_type": "AcDbBlockReference",
                "layer": "BASE",
                "status": "context",
                "method": "traverse-xref-reference",
                "reason": "resolved XREF traversed for descendant provenance",
                "geometry_ids": [],
                "dependency_ids": ["xref/AA"],
            }
        )
        payload["summary"].update(source_instances=2, context=1)
    if with_primitives:
        payload["coverage"].extend(
            [
                {
                    "identity": {"handle": "C1", "instance_chain": []},
                    "entity_type": "AcDbPolyline",
                    "layer": "BUILDINGS",
                    "status": "native",
                    "method": "autodesk-acdbcurve-adaptive-sampling",
                    "geometry_ids": ["path/C1"],
                },
                {
                    "identity": {"handle": "D1", "instance_chain": []},
                    "entity_type": "AcDbPoint",
                    "layer": "TREES",
                    "status": "native",
                    "method": "autodesk-acdbpoint-wcs",
                    "geometry_ids": ["point/D1"],
                },
            ]
        )
        payload["geometry"].extend(
            [
                {
                    "id": "path/C1",
                    "identity": {"handle": "C1", "instance_chain": []},
                    "kind": "path",
                    "closed": True,
                    "coordinates": [
                        [20, 0, 0],
                        [30, 0, 0],
                        [30, 10, 0],
                        [20, 0, 0],
                    ],
                    "achieved_tolerance_m": 0.0005,
                    "content_sha256": "0" * 64,
                },
                {
                    "id": "point/D1",
                    "identity": {"handle": "D1", "instance_chain": []},
                    "kind": "point",
                    "coordinates": [40, 50, 0],
                    "content_sha256": "0" * 64,
                },
            ]
        )
        payload["summary"].update(
            source_instances=payload["summary"]["source_instances"] + 2,
            native=3,
        )
    normalized = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    for geometry in normalized["geometry"]:
        geometry["content_sha256"] = _canonical_sha256(
            {key: value for key, value in geometry.items() if key != "content_sha256"}
        )
    normalized["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized.items() if key != "summary"}
    )
    return CadSnapshot.model_validate(normalized)


def imported() -> DxfImportResult:
    return DxfImportResult(
        layers=[
            Layer(
                id="buildings",
                source_name="BUILDINGS",
                suggested_kind=LayerKind.BUILDING,
                mapped_kind=LayerKind.BUILDING,
                object_count=1,
                color="#000000",
                entity_types={"REGION": 1},
                geometry_complete=False,
                unsupported_geometry_types={"REGION": 1},
            )
        ],
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        dxf_version="AC1032",
        units="м",
        entity_count=1,
    )


def test_applies_native_region_with_hole_and_preserves_provenance() -> None:
    result = apply_cad_snapshot(imported(), snapshot(), source_sha256="a" * 64)
    feature = result.geometry.feature_collection["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert len(feature["geometry"]["coordinates"]) == 2
    assert feature["properties"]["source_instance_chain"] == ["10"]
    layer = result.layers[0]
    assert layer.geometry_complete is True
    assert layer.unsupported_geometry_types == {}
    assert layer.projected_geometry_types == {"REGION": 1}
    assert result.cad_snapshot_provenance is not None
    assert result.cad_snapshot_provenance.native_geometry == 1


def test_applies_native_curve_and_point_from_autocad_snapshot() -> None:
    portable = imported()
    portable.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "id": "portable-duplicate",
            "properties": {
                "source_handle": "C1",
                "source_instance_chain": [],
                "source_layer": "BUILDINGS",
                "entity_type": "LWPOLYLINE",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[20, 0], [30, 0]],
            },
        }
    ]
    result = apply_cad_snapshot(
        portable, snapshot(with_primitives=True), source_sha256="a" * 64
    )

    features = result.geometry.feature_collection["features"]
    assert [feature["geometry"]["type"] for feature in features] == [
        "Polygon",
        "Polygon",
        "Point",
    ]
    assert features[1]["properties"]["entity_type"] == "LWPOLYLINE"
    assert features[2]["properties"]["entity_type"] == "POINT"
    assert all(feature["id"] != "portable-duplicate" for feature in features)
    buildings = next(
        layer for layer in result.layers if layer.source_name == "BUILDINGS"
    )
    assert buildings.projected_geometry_types == {
        "REGION": 1,
        "LWPOLYLINE": 1,
    }
    assert result.cad_snapshot_provenance is not None
    assert result.cad_snapshot_provenance.native_geometry == 3


def test_snapshot_only_builder_creates_layers_without_portable_reader() -> None:
    result = build_dxf_import_from_snapshot(
        snapshot(with_primitives=True), source_sha256="a" * 64
    )

    assert result.entity_count == 3
    assert result.units == "м"
    assert result.dxf_version == "AutoCAD 2027.0.1"
    assert len(result.geometry.feature_collection["features"]) == 3
    assert {layer.source_name for layer in result.layers} == {
        "BUILDINGS",
        "TREES",
    }
    assert all(layer.geometry_complete for layer in result.layers)


def test_self_intersecting_closed_path_stays_reviewable_linework() -> None:
    payload = snapshot(with_primitives=True).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    path = next(item for item in payload["geometry"] if item["kind"] == "path")
    path["coordinates"] = [
        [20, 0, 0],
        [30, 10, 0],
        [20, 10, 0],
        [30, 0, 0],
        [20, 0, 0],
    ]
    payload = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    path = next(item for item in payload["geometry"] if item["kind"] == "path")
    path["content_sha256"] = _canonical_sha256(
        {key: value for key, value in path.items() if key != "content_sha256"}
    )
    payload["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "summary"}
    )

    result = build_dxf_import_from_snapshot(
        CadSnapshot.model_validate(payload), source_sha256="a" * 64
    )

    feature = next(
        item
        for item in result.geometry.feature_collection["features"]
        if item["properties"].get("source_handle") == "C1"
    )
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["source_closed_path"] is True
    assert feature["properties"]["source_polygon_projection"] is False


def test_unresolved_autocad_instance_never_keeps_fallback_geometry() -> None:
    payload = snapshot().model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["coverage"].append(
        {
            "identity": {"handle": "B12", "instance_chain": []},
            "entity_type": "AcDbArc",
            "layer": "BUILDINGS",
            "status": "unresolved",
            "method": "autodesk-acdbcurve-adaptive-sampling-failed",
            "reason": "native finite curve extraction failed",
            "geometry_ids": [],
        }
    )
    payload["summary"].update(source_instances=2, unresolved=1)
    payload["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "summary"}
    )
    native = CadSnapshot.model_validate(payload)
    portable = imported()
    portable.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "id": "forbidden-fallback",
            "properties": {
                "source_handle": "B12",
                "source_instance_chain": [],
                "source_layer": "BUILDINGS",
                "entity_type": "ARC",
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [[0, 0], [1, 1]],
            },
        }
    ]

    result = apply_cad_snapshot(portable, native, source_sha256="a" * 64)

    assert all(
        feature["id"] != "forbidden-fallback"
        for feature in result.geometry.feature_collection["features"]
    )
    layer = result.layers[0]
    assert layer.geometry_complete is False
    assert layer.unsupported_geometry_types == {"ARC": 1}


def test_snapshot_hash_must_match_exact_source_dxf() -> None:
    with pytest.raises(CadSnapshotProviderError, match="different DXF"):
        apply_cad_snapshot(imported(), snapshot(), source_sha256="d" * 64)


def test_xref_snapshot_requires_exact_verified_package_dependencies() -> None:
    native = snapshot(with_dependency=True)
    with pytest.raises(CadSnapshotProviderError, match="verified XREF package"):
        apply_cad_snapshot(imported(), native, source_sha256="a" * 64)
    with pytest.raises(CadSnapshotProviderError, match="differs"):
        apply_cad_snapshot(
            imported(),
            native,
            source_sha256="a" * 64,
            verified_dependencies={"references/child.dxf": "e" * 64},
        )

    result = apply_cad_snapshot(
        imported(),
        native,
        source_sha256="a" * 64,
        verified_dependencies={"references/child.dxf": "d" * 64},
    )
    assert result.cad_snapshot_provenance is not None
    assert result.cad_snapshot_provenance.dependencies[0].path == (
        "references/child.dxf"
    )


def test_snapshot_geometry_hash_is_verified_again_at_use_time() -> None:
    native = snapshot()
    native.geometry[0].loops[0].coordinates[1] = (11, 0, 0)
    with pytest.raises(CadSnapshotProviderError, match="geometry hash differs"):
        apply_cad_snapshot(imported(), native, source_sha256="a" * 64)


def test_native_count_must_cover_every_region_before_physical_calculation() -> None:
    layer = imported().layers[0]
    project = Project(name="Gate", layers=[layer])
    assert _unprojectable_physical_layers(project, []) == {"BUILDINGS": {"REGION"}}
    layer.projected_geometry_types = {"REGION": 1}
    assert _unprojectable_physical_layers(project, []) == {}


def test_import_application_uses_snapshot_only_when_explicitly_supplied() -> None:
    source = b"DXF"
    native = snapshot(sha256(source).hexdigest())

    class Reader:
        def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult:
            raise AssertionError("portable DXF reader must not run")

    class History:
        def clear(self, project_id: str) -> None:
            pass

    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="Native source"))
    service = ImportApplication(
        repository=repository,
        dxf_reader=Reader(),
        history=History(),
        invalidate_spatial=lambda _project_id: None,
        now=lambda: datetime(2026, 9, 17, tzinfo=UTC),
    )
    saved = service.import_dxf(
        project.id,
        "source.dxf",
        source,
        native.model_dump_json(by_alias=True).encode(),
    )
    assert saved.source_file is not None
    assert saved.source_file.cad_snapshot_provenance is not None
    assert (
        saved.source_file.cad_snapshot_provenance.source_sha256
        == sha256(source).hexdigest()
    )
    assert saved.source_geometry is not None
    assert len(saved.source_geometry.feature_collection["features"]) == 1
