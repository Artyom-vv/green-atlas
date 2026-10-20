from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from shapely.geometry import LineString, Polygon

from app.cad_bridge import CadSnapshot
from app.cad_bridge.contracts import PathGeometry, PointGeometry, RegionGeometry
from app.cad_bridge.integrity import canonical_record_sha256 as _canonical_sha256
from app.cad_bridge.provider import (
    CadSnapshotProviderError,
    _boundary_candidate,
    apply_cad_snapshot,
    build_project_from_snapshot,
    native_geometry_shape,
)
from app.dxf_import.capacity import SourceCapacityExceeded, SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import BoundaryCandidateStatus, Layer, LayerKind
from app.geometry.checking.adapters import _unprojectable_physical_layers
from app.geometry.contracts import GeometrySnapshot
from app.layer_recognition.review_policy import local_layer_proposal
from app.native_query.live.live_inventory import QueryObject
from app.native_query.search.hybrid_geometry import HybridGeometry, uses_linear_geometry
from app.projects.contracts import Project


def native_project(snapshot: CadSnapshot, *, source_sha256: str) -> Project:
    return build_project_from_snapshot(
        Project(name="Native projection"),
        snapshot,
        filename="Drawing.autocad.json",
        imported_at="2026-10-08T12:00:00+03:00",
        source_sha256=source_sha256,
        source_size=1,
    )


def test_native_site_candidate_does_not_hide_dominant_open_boundary() -> None:
    small_surface = Polygon([(0, 0), (12, 0), (12, 10), (0, 10)])
    long_open_outline = LineString(
        [(30, 0), (300, 0), (300, 200), (30, 200), (30, 0.2)]
    )
    candidate = _boundary_candidate([small_surface, long_open_outline])
    assert candidate.status == BoundaryCandidateStatus.INVALID
    assert candidate.area_m2 == small_surface.area
    assert candidate.issue is not None
    assert "незамкнутый" in candidate.issue


def test_native_site_candidate_keeps_small_local_open_annotation() -> None:
    surface = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    local_line = LineString([(1, 1), (5, 1), (5, 5)])
    candidate = _boundary_candidate([surface, local_line])
    assert candidate.status == BoundaryCandidateStatus.USABLE


def test_imported_name_confidence_does_not_confirm_a_role() -> None:
    result = native_project(snapshot(), source_sha256="a" * 64)
    assert result.layers
    assert all(layer.mapping_review_required for layer in result.layers)
    assert all(layer.mapping_confirmed is False for layer in result.layers)


def snapshot(
    source_sha256: str = "a" * 64,
    *,
    with_dependency: bool = False,
    with_primitives: bool = False,
) -> CadSnapshot:
    identity = {"handle": "A12", "instance_chain": ["10"]}
    payload: dict[str, Any] = {
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


def test_casing_context_counters_come_only_from_verified_coverage() -> None:
    payload = snapshot().model_dump(by_alias=True, mode="json", exclude_none=True)
    for row in payload["coverage"]:
        row["layer"] = "ЭС_Футляры"
    for index, entity_type in enumerate(
        [
            "AcDbBlockReference",
            "AcDbAttribute",
            "AcDbAttributeDefinition",
        ]
    ):
        payload["coverage"].append(
            {
                "identity": {"handle": f"C{index}", "instance_chain": ["10"]},
                "entity_type": entity_type,
                "layer": "ЭС_Футляры",
                "status": "context",
                "method": "native-context",
                "reason": "Контейнер или атрибут без физической геометрии",
                "geometry_ids": [],
            }
        )
    payload["summary"].update(source_instances=4, context=3)
    payload["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "summary"}
    )
    native = CadSnapshot.model_validate(payload)
    source_before = native.model_dump()
    project = native_project(native, source_sha256="a" * 64)
    layer = project.layers[0]
    assert layer.context_entity_types == {"INSERT": 1, "ATTRIB": 1, "ATTDEF": 1}
    assert layer.entity_types == {"REGION": 1, "INSERT": 1, "ATTRIB": 1, "ATTDEF": 1}
    assert layer.projected_geometry_types == {"REGION": 1}
    assert layer.object_count == 4 and layer.geometry_complete
    assert local_layer_proposal(layer).category == LayerCategory.PROTECTIVE_CASING
    assert project.source_geometry is not None
    area = project.source_geometry.feature_collection["features"][0]
    assert area["geometry"]["type"] == "Polygon"
    assert len(area["geometry"]["coordinates"]) == 2
    assert native.model_dump() == source_before


@pytest.mark.parametrize(
    "role, category, expected_closed_path_type",
    [
        (LayerKind.RESTRICTED, LayerCategory.PROTECTIVE_CASING, "LineString"),
        (LayerKind.RESTRICTED, LayerCategory.EQUIPMENT, "Polygon"),
        (LayerKind.RESTRICTED, None, "Polygon"),
        (LayerKind.UTILITY, None, "LineString"),
    ],
)
def test_casing_uses_measured_region_but_not_filled_native_closed_path(
    role: LayerKind,
    category: LayerCategory | None,
    expected_closed_path_type: str,
) -> None:
    project = native_project(snapshot(with_primitives=True), source_sha256="a" * 64)
    assert project.source_geometry is not None
    layer = next(item for item in project.layers if item.source_name == "BUILDINGS")
    layer.mapped_kind, layer.category, layer.mapping_confirmed = role, category, True
    features = project.source_geometry.feature_collection["features"]
    closed_path = next(f for f in features if f["properties"]["source_handle"] == "C1")
    assert closed_path["geometry"]["type"] == "Polygon"
    assert closed_path["properties"]["source_closed_path"] is True
    assert closed_path["properties"]["source_polygon_projection"] is True
    assert "source_native_area_units2" not in closed_path["properties"]
    engine = SimpleNamespace(factor=1.0, _faces=())
    projection = HybridGeometry(engine, project, require_accuracy=True)
    item = QueryObject(
        routes=("C1",),
        layer=layer.source_name,
        bounds=(20, 0, 30, 10),
        curve=True,
        error="",
    )
    projected = projection.get(
        item, linear=uses_linear_geometry(item, layer, frozenset())
    )
    assert not projected.reason
    assert projected.geometries[0].geom_type == expected_closed_path_type
    if role == LayerKind.RESTRICTED:
        measured = projection.get(
            QueryObject(
                routes=("10/A12",),
                layer=layer.source_name,
                bounds=(0, 0, 10, 10),
                curve=False,
                error="",
            )
        )
        assert not measured.reason
        assert isinstance(measured.geometries[0], Polygon)
        assert measured.geometries[0].area == 96
        assert len(measured.geometries[0].interiors) == 1


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
    result = native_project(snapshot(with_primitives=True), source_sha256="a" * 64)
    assert result.source_file is not None
    assert result.source_geometry is not None

    assert result.source_file.entity_count == 3
    assert result.source_file.units == "м"
    # Application version is not a DXF ACxxxx header. A snapshot without an
    # actual DXF must not invent that header from the AutoCAD product version.
    assert result.source_file.dxf_version == "AutoCAD live snapshot"
    assert result.source_file.cad_snapshot_provenance is not None
    assert result.source_file.cad_snapshot_provenance.autocad_version == "2027.0.1"
    assert len(result.source_geometry.feature_collection["features"]) == 3
    assert {layer.source_name for layer in result.layers} == {
        "BUILDINGS",
        "TREES",
    }
    assert all(layer.geometry_complete for layer in result.layers)


@pytest.mark.parametrize(
    "capacity,allowed",
    [
        (SourceGeometryCapacity(max_coordinates=15, max_feature_coordinates=10), True),
        (SourceGeometryCapacity(max_coordinates=14), False),
        (SourceGeometryCapacity(max_feature_coordinates=9), False),
    ],
)
def test_projected_coordinate_budget_counts_holes_closure_and_points(
    capacity: SourceGeometryCapacity, allowed: bool
) -> None:
    def build() -> Project:
        return build_project_from_snapshot(
            Project(name="Budget"),
            snapshot(with_primitives=True),
            filename="Drawing.autocad.json",
            imported_at="2026-10-09T12:00:00+03:00",
            source_sha256="a" * 64,
            source_size=1,
            capacity=capacity,
        )

    if allowed:
        result = build()
        assert result.source_file is not None
        assert result.source_file.bounds == [0, 0, 40, 50]
        assert {layer.source_name: layer.bounds for layer in result.layers} == {
            "BUILDINGS": (0, 0, 30, 10),
            "TREES": (40, 50, 40, 50),
        }
    else:
        with pytest.raises(SourceCapacityExceeded):
            build()


def test_compatible_existing_features_still_expand_native_layer_and_project_bounds() -> (
    None
):
    portable = imported()
    portable.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "properties": {"source_handle": "F1", "source_layer": "BUILDINGS"},
            "geometry": {"type": "Point", "coordinates": [-50, 60]},
        }
    ]
    result = apply_cad_snapshot(portable, snapshot(), source_sha256="a" * 64)
    assert result.bounds == [-50, 0, 10, 60]
    assert result.layers[0].bounds == (-50, 0, 10, 60)


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

    result = native_project(CadSnapshot.model_validate(payload), source_sha256="a" * 64)
    assert result.source_geometry is not None

    feature = next(
        item
        for item in result.source_geometry.feature_collection["features"]
        if item["properties"].get("source_handle") == "C1"
    )
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["source_closed_path"] is True
    assert feature["properties"]["source_polygon_projection"] is False


def test_invalid_native_surface_does_not_hide_other_admitted_objects() -> None:
    payload = snapshot(with_primitives=True).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    region = next(item for item in payload["geometry"] if item["kind"] == "region")
    region["loops"][0]["coordinates"] = [
        [0, 0, 0],
        [10, 10, 0],
        [10, 0, 0],
        [0, 10, 0],
        [0, 0, 0],
    ]
    payload = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    region = next(item for item in payload["geometry"] if item["kind"] == "region")
    region["content_sha256"] = _canonical_sha256(
        {key: value for key, value in region.items() if key != "content_sha256"}
    )
    payload["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "summary"}
    )
    native = CadSnapshot.model_validate(payload)
    result = native_project(native, source_sha256=native.source.sha256)
    assert result.source_file is not None
    assert result.source_geometry is not None
    assert len(result.source_geometry.feature_collection["features"]) == 2
    buildings = next(
        layer for layer in result.layers if layer.source_name == "BUILDINGS"
    )
    assert buildings.geometry_complete is False
    assert buildings.unsupported_geometry_types == {"REGION": 1}
    assert buildings.projected_geometry_types == {"LWPOLYLINE": 1}
    assert result.source_file.cad_snapshot_provenance is not None
    assert result.source_file.cad_snapshot_provenance.native_geometry == 2
    assert len(result.source_file.warnings) == 1
    assert "handle A12" in result.source_file.warnings[0]


def test_native_surface_projection_preserves_close_distinct_vertices() -> None:
    # Сокращённый контур HATCH 1457EDD: округление схлопывало два разных прохода.
    coordinates = [
        [17842.257116217268, 14253.971833003123, 0],
        [17842.21111122393, 14256.392211087776, 0],
        [17842.47376544471, 14253.191573770322, 0],
        [17843.86509534638, 14250.584101964558, 0],
        [17846.249067765788, 14248.773268750598, 0],
        [17849.212572708147, 14248.132488140036, 0],
        [17849.442350840385, 14248.13784722404, 0],
        [17849.384924872607, 14248.13579555709, 0],
        [17849.327483913752, 14248.134218421064, 0],
        [17849.270031884964, 14248.133115923627, 0],
        [17849.212572708147, 14248.132488140034, 0],
        [17846.57734448795, 14248.351261279111, 0],
        [17844.386915071107, 14249.525508223009, 0],
        [17842.844242483297, 14251.519816271384, 0],
        [17842.257116217268, 14253.971833003123, 0],
    ]
    original = Polygon(coordinates)
    assert original.is_valid
    assert not Polygon([(round(p[0], 6), round(p[1], 6)) for p in coordinates]).is_valid
    payload = snapshot().model_dump(by_alias=True, mode="json", exclude_none=True)
    region = payload["geometry"][0]
    region["loops"] = [{"role": "outer", "closed": True, "coordinates": coordinates}]
    region["native_area_units2"] = original.area
    region["native_perimeter_units"] = original.length
    payload = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    region = payload["geometry"][0]
    region["content_sha256"] = _canonical_sha256(
        {key: value for key, value in region.items() if key != "content_sha256"}
    )
    payload["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "summary"}
    )

    result = native_project(CadSnapshot.model_validate(payload), source_sha256="a" * 64)

    assert result.source_geometry is not None
    features = result.source_geometry.feature_collection["features"]
    assert len(features) == 1
    assert features[0]["geometry"]["coordinates"][0] == tuple(
        (point[0], point[1]) for point in coordinates
    )
    assert result.layers[0].geometry_complete
    assert result.layers[0].unsupported_geometry_types == {}
    assert result.source_file is not None
    assert result.source_file.warnings == []


@pytest.mark.parametrize("factor", [1.0, 0.001])
def test_native_projection_preserves_short_lines_and_point_position(
    factor: float,
) -> None:
    coordinates = [(1.00000004, 2.00000004, 0.0), (1.00000005, 2.00000004, 0.0)]
    identity = {"handle": "A12", "instance_chain": []}
    line = PathGeometry.model_validate(
        {
            "id": "path/A12",
            "identity": identity,
            "kind": "path",
            "closed": False,
            "coordinates": coordinates,
            "achieved_tolerance_m": 0.0005,
            "content_sha256": "0" * 64,
        }
    )
    point = PointGeometry.model_validate(
        {
            "id": "point/A12",
            "identity": identity,
            "kind": "point",
            "coordinates": coordinates[0],
            "content_sha256": "0" * 64,
        }
    )
    expected = [(x * factor, y * factor) for x, y, _ in coordinates]

    assert list(native_geometry_shape(line, factor).coords) == expected
    assert list(native_geometry_shape(point, factor).coords) == [expected[0]]


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
    assert isinstance(native.geometry[0], RegionGeometry)
    native.geometry[0].loops[0].coordinates[1] = (11, 0, 0)
    with pytest.raises(CadSnapshotProviderError, match="geometry hash differs"):
        apply_cad_snapshot(imported(), native, source_sha256="a" * 64)


def test_native_count_must_cover_every_region_before_physical_calculation() -> None:
    layer = imported().layers[0]
    project = Project(name="Gate", layers=[layer])
    assert _unprojectable_physical_layers(project, []) == {"BUILDINGS": {"REGION"}}
    layer.projected_geometry_types = {"REGION": 1}
    assert _unprojectable_physical_layers(project, []) == {}
