from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from shapely.geometry import MultiPolygon, Polygon, mapping

from app.cad_bridge.contracts import CadSnapshot, CadSnapshotProvenance, RegionGeometry
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layer_contracts import Layer
from app.dxf_import.layer_suggestions import suggest_layer_kind
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.geojson_size import coordinate_count


class CadSnapshotProviderError(ValueError):
    """An admitted snapshot cannot be combined with this exact DXF import."""


DXF_TYPE_BY_AUTOCAD_CLASS = {
    "AcDbArc": "ARC",
    "AcDbAttribute": "ATTRIB",
    "AcDbAttributeDefinition": "ATTDEF",
    "AcDbBlockReference": "INSERT",
    "AcDbCircle": "CIRCLE",
    "AcDbEllipse": "ELLIPSE",
    "AcDbHatch": "HATCH",
    "AcDbLine": "LINE",
    "AcDbMline": "MLINE",
    "AcDbMText": "MTEXT",
    "AcDbPoint": "POINT",
    "AcDbPolyline": "LWPOLYLINE",
    "AcDb2dPolyline": "POLYLINE",
    "AcDbRegion": "REGION",
    "AcDbText": "TEXT",
}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _verify_integrity(snapshot: CadSnapshot) -> None:
    for geometry in snapshot.geometry:
        payload = geometry.model_dump(mode="json", exclude={"content_sha256"})
        if _canonical_sha256(payload) != geometry.content_sha256:
            raise CadSnapshotProviderError(
                f"CAD snapshot geometry hash differs: {geometry.id}"
            )
    payload = snapshot.model_dump(
        by_alias=True, mode="json", exclude={"summary"}, exclude_none=True
    )
    if _canonical_sha256(payload) != snapshot.summary.payload_sha256:
        raise CadSnapshotProviderError("CAD snapshot payload hash differs")


def _bounds(features: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    coordinates: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            collect(value.get("coordinates"))
            collect(value.get("geometries"))
        elif (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            coordinates.append((float(value[0]), float(value[1])))
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    for feature in features:
        collect(feature.get("geometry"))
    if not coordinates:
        return None
    xs, ys = zip(*coordinates, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


def _region_shape(region: RegionGeometry, factor: float) -> Polygon | MultiPolygon:
    outer_rings: list[list[tuple[float, float]]] = []
    hole_rings: list[list[tuple[float, float]]] = []
    for loop in region.loops:
        ring = [
            (round(point[0] * factor, 6), round(point[1] * factor, 6))
            for point in loop.coordinates
        ]
        (outer_rings if loop.role == "outer" else hole_rings).append(ring)
    if not outer_rings:
        raise CadSnapshotProviderError(f"REGION {region.id} has no outer ring")

    outer_polygons = [Polygon(ring) for ring in outer_rings]
    holes_by_outer: list[list[list[tuple[float, float]]]] = [
        [] for _ in outer_polygons
    ]
    for hole in hole_rings:
        hole_polygon = Polygon(hole)
        point = hole_polygon.representative_point()
        candidates = [
            (outer.area, index)
            for index, outer in enumerate(outer_polygons)
            if outer.covers(point)
        ]
        if not candidates:
            raise CadSnapshotProviderError(
                f"REGION {region.id} has a hole outside every outer ring"
            )
        _, owner = min(candidates)
        holes_by_outer[owner].append(hole)

    polygons = [
        Polygon(outer_rings[index], holes_by_outer[index])
        for index in range(len(outer_rings))
    ]
    if any(polygon.is_empty or not polygon.is_valid or polygon.area <= 0 for polygon in polygons):
        raise CadSnapshotProviderError(f"REGION {region.id} is not a valid planar polygon")
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def apply_cad_snapshot(
    imported: DxfImportResult,
    snapshot: CadSnapshot,
    *,
    source_sha256: str,
    capacity: SourceGeometryCapacity | None = None,
    verified_dependencies: dict[str, str] | None = None,
) -> DxfImportResult:
    """Merge admitted native geometry while preserving the normal DXF result.

    The original reader remains authoritative for every type it supports.  The
    snapshot only fills exact identities that carry admitted native geometry;
    unresolved coverage never becomes empty or inferred ground.
    """

    if snapshot.source.sha256 != source_sha256:
        raise CadSnapshotProviderError("CAD snapshot belongs to a different DXF")
    _verify_integrity(snapshot)
    dependencies = snapshot.dependencies or []
    if dependencies:
        if verified_dependencies is None:
            raise CadSnapshotProviderError(
                "CAD snapshot requires its verified XREF package"
            )
        expected = {item.path: item.sha256 for item in dependencies}
        if verified_dependencies != expected:
            raise CadSnapshotProviderError(
                "verified XREF package differs from CAD snapshot dependencies"
            )
    if snapshot.source.units_code not in DXF_UNIT_FACTORS:
        raise CadSnapshotProviderError("CAD snapshot uses unsupported drawing units")
    units, factor = DXF_UNIT_FACTORS[snapshot.source.units_code]
    if imported.units != units:
        raise CadSnapshotProviderError("CAD snapshot units differ from DXF reader units")

    result = imported.model_copy(deep=True)
    features = result.geometry.feature_collection.setdefault("features", [])
    if not isinstance(features, list):
        raise CadSnapshotProviderError("DXF feature collection is invalid")
    capacity = capacity or SourceGeometryCapacity()
    total_coordinates = sum(
        coordinate_count(feature.get("geometry", {})) for feature in features
    )
    layer_by_name = {layer.source_name: layer for layer in result.layers}
    coverage_by_identity = {
        (record.identity.handle, tuple(record.identity.instance_chain)): record
        for record in snapshot.coverage
    }
    native_counts: Counter[str] = Counter()
    instance_types_by_layer: dict[str, Counter[str]] = {}
    for record in snapshot.coverage:
        entity_type = DXF_TYPE_BY_AUTOCAD_CLASS.get(
            record.entity_type, f"AUTOCAD:{record.entity_type}"
        )
        instance_types_by_layer.setdefault(record.layer, Counter())[entity_type] += 1

    for geometry in snapshot.geometry:
        key = (geometry.identity.handle, tuple(geometry.identity.instance_chain))
        coverage = coverage_by_identity.get(key)
        if coverage is None or geometry.id not in coverage.geometry_ids:
            raise CadSnapshotProviderError(
                f"CAD snapshot geometry lacks matching coverage: {geometry.id}"
            )
        shape = _region_shape(geometry, factor)
        geojson = mapping(shape)
        native_counts[coverage.layer] += 1
        feature = {
            "type": "Feature",
            "id": f"cad-snapshot-{geometry.content_sha256}",
            "properties": {
                "source_layer": coverage.layer,
                "kind": suggest_layer_kind(coverage.layer).value,
                "entity_type": "REGION",
                "source_handle": geometry.identity.handle,
                "source_instance_chain": geometry.identity.instance_chain,
                "source_geometry_provider": "autocad_snapshot_v1",
                "source_native_geometry": True,
                "source_geometry_content_sha256": geometry.content_sha256,
                "source_native_area_units2": geometry.native_area_units2,
                "source_native_perimeter_units": geometry.native_perimeter_units,
                "source_sampling_tolerance_m": geometry.achieved_tolerance_m,
            },
            "geometry": geojson,
        }
        feature_coordinates = coordinate_count(geojson)
        capacity.check(
            features=len(features) + 1,
            coordinates=total_coordinates + feature_coordinates,
            feature_coordinates=feature_coordinates,
            layer=coverage.layer,
        )
        features.append(feature)
        total_coordinates += feature_coordinates
    for layer_name, instance_types in instance_types_by_layer.items():
        count = native_counts[layer_name]
        layer = layer_by_name.get(layer_name)
        may_resolve_region_only = False
        if layer is None:
            kind = suggest_layer_kind(layer_name)
            layer = Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{layer_name}")),
                source_name=layer_name,
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=sum(instance_types.values()),
                color="#64748B",
                entity_types=dict(instance_types),
            )
            result.layers.append(layer)
            layer_by_name[layer_name] = layer
        else:
            may_resolve_region_only = (
                not layer.geometry_complete
                and bool(layer.unsupported_geometry_types)
                and set(layer.unsupported_geometry_types) <= {"REGION"}
                and layer.unreadable_geometry_count == 0
            )
            layer.entity_types = dict(instance_types)
            layer.object_count = sum(instance_types.values())
        if count:
            layer.projected_geometry_types["REGION"] = count
            layer.unsupported_geometry_types.pop("REGION", None)
        if layer.geometry_complete or may_resolve_region_only:
            layer.geometry_complete = (
                not layer.unsupported_geometry_types
                and layer.unreadable_geometry_count == 0
            )
        combined_layer_features = [
            feature
            for feature in features
            if feature.get("properties", {}).get("source_layer") == layer_name
        ]
        layer.bounds = _bounds(combined_layer_features)

    result.warnings = [
        warning
        for warning in result.warnings
        if not warning.startswith("Часть типов доступна только в исходном файле:")
    ]
    remaining_unsupported: Counter[str] = Counter()
    for layer in result.layers:
        remaining_unsupported.update(layer.unsupported_geometry_types)
    if remaining_unsupported:
        labels = ", ".join(
            f"{name}: {count}" for name, count in sorted(remaining_unsupported.items())
        )
        result.warnings.append(
            f"Часть типов доступна только в исходном файле: {labels}. "
            "Если такой слой назначен физическим ограничением, расчёт будет остановлен; "
            "проверьте назначение слоя и подготовьте расчётное представление его физических объектов."
        )

    result.bounds = list(_bounds(features) or ()) or None
    result.cad_snapshot_provenance = CadSnapshotProvenance(
        schema="green-atlas.autocad-snapshot/1",
        source_sha256=snapshot.source.sha256,
        payload_sha256=snapshot.summary.payload_sha256,
        autocad_version=snapshot.extraction.autocad_version,
        plugin_version=snapshot.extraction.plugin_version,
        target=snapshot.extraction.target,
        source_instances=snapshot.summary.source_instances,
        native_geometry=len(snapshot.geometry),
        unresolved_instances=snapshot.summary.unresolved,
        dependencies=[item.model_copy(deep=True) for item in dependencies],
    )
    return result
