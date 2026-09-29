from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import ijson
from pydantic import TypeAdapter
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping
from shapely.ops import polygonize, unary_union

from app.cad_bridge.contracts import (
    CadGeometry,
    CadSnapshot,
    CadSnapshotDependency,
    CadSnapshotProvenance,
    CoverageRecord,
    ExtractionEvidence,
    PathGeometry,
    PointGeometry,
    RegionGeometry,
    SnapshotSource,
    SnapshotSummary,
)
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layer_contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    Layer,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layer_suggestions import (
    assess_layer_suggestion,
    is_boundary_candidate_name,
    suggest_layer_kind,
)
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.geometry.geojson_size import coordinate_count


class CadSnapshotProviderError(ValueError):
    """An admitted snapshot cannot authorize this exact source geometry."""


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
    "AcDb3dPolyline": "POLYLINE",
    "AcDbRegion": "REGION",
    "AcDbSolid": "SOLID",
    "AcDbSpline": "SPLINE",
    "AcDbText": "TEXT",
}

_LAYER_COLORS = (
    "#475569",
    "#2563EB",
    "#0F766E",
    "#B45309",
    "#7C3AED",
    "#BE123C",
    "#0369A1",
    "#4D7C0F",
)

_CAD_GEOMETRY_ADAPTER = TypeAdapter(CadGeometry)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def verify_cad_snapshot_integrity(snapshot: CadSnapshot) -> None:
    """Verify every native geometry hash and the complete snapshot payload."""

    for geometry in snapshot.geometry:
        payload = geometry.model_dump(
            mode="json", exclude={"content_sha256"}, exclude_none=True
        )
        if _canonical_sha256(payload) != geometry.content_sha256:
            raise CadSnapshotProviderError(
                f"CAD snapshot geometry hash differs: {geometry.id}"
            )
    for proposal in snapshot.area_proposals or []:
        preview_payload = proposal.preview.model_dump(
            mode="json", exclude={"content_sha256"}, exclude_none=True
        )
        if _canonical_sha256(preview_payload) != proposal.preview.content_sha256:
            raise CadSnapshotProviderError(
                f"CAD area proposal preview hash differs: {proposal.id}"
            )
        proposal_payload = proposal.model_dump(
            mode="json", exclude={"content_sha256"}, exclude_none=True
        )
        if _canonical_sha256(proposal_payload) != proposal.content_sha256:
            raise CadSnapshotProviderError(
                f"CAD area proposal hash differs: {proposal.id}"
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
    if any(
        outer.is_empty or not outer.is_valid or outer.area <= 0
        for outer in outer_polygons
    ):
        raise CadSnapshotProviderError(
            f"REGION {region.id} has an invalid outer ring"
        )
    holes_by_outer: list[list[list[tuple[float, float]]]] = [[] for _ in outer_polygons]
    for hole in hole_rings:
        hole_polygon = Polygon(hole)
        if hole_polygon.is_empty or not hole_polygon.is_valid or hole_polygon.area <= 0:
            raise CadSnapshotProviderError(
                f"REGION {region.id} has an invalid hole ring"
            )
        candidates = [
            (outer.area, index)
            for index, outer in enumerate(outer_polygons)
            if outer.covers(hole_polygon)
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
    if any(
        polygon.is_empty or not polygon.is_valid or polygon.area <= 0
        for polygon in polygons
    ):
        raise CadSnapshotProviderError(
            f"REGION {region.id} is not a valid planar polygon"
        )
    result = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    if not result.is_valid:
        raise CadSnapshotProviderError(
            f"REGION {region.id} has overlapping or touching outer rings"
        )
    return result


def _native_shape(
    geometry: CadGeometry, factor: float
) -> Point | LineString | Polygon | MultiPolygon:
    if isinstance(geometry, RegionGeometry):
        return _region_shape(geometry, factor)
    if isinstance(geometry, PointGeometry):
        return Point(
            round(geometry.coordinates[0] * factor, 6),
            round(geometry.coordinates[1] * factor, 6),
        )
    if isinstance(geometry, PathGeometry):
        coordinates = [
            (round(point[0] * factor, 6), round(point[1] * factor, 6))
            for point in geometry.coordinates
        ]
        if geometry.closed:
            polygon = Polygon(coordinates)
            if not polygon.is_empty and polygon.is_valid and polygon.area > 0:
                return polygon
            # A closed CAD path may self-touch or contain repeated segments.
            # Preserve the exact authored linework for display/review, but do
            # not repair it into an invented calculation surface.
            line = LineString(coordinates)
            if not line.is_empty and line.is_valid and line.length > 0:
                return line
            raise CadSnapshotProviderError(
                f"native path {geometry.id} is not valid planar linework"
            )
        line = LineString(coordinates)
        if line.is_empty or not line.is_valid or line.length <= 0:
            raise CadSnapshotProviderError(
                f"native path {geometry.id} is not a valid planar line"
            )
        return line
    raise CadSnapshotProviderError(f"unsupported native geometry: {geometry.id}")


def _projection_issue_warning(failures: list[tuple[str, str]]) -> str | None:
    """Keep local planar failures visible without flooding the opening review."""

    if not failures:
        return None
    counts = Counter(layer for layer, _ in failures)
    examples = {layer: handle for layer, handle in failures}
    details = "; ".join(
        f"{layer}: {count} (например, handle {examples[layer]})"
        for layer, count in list(counts.items())[:3]
    )
    if len(counts) > 3:
        details += f"; ещё {len(counts) - 3} слоёв"
    return (
        f"AutoCAD прочитал, но расчётное представление не получено "
        f"для отдельных объектов ({len(failures)}): {details}. "
        "Остальная геометрия доступна; эти объекты не считаются проверенными "
        "ограничениями до уточнения."
    )


def _building_area_warning(gaps: Counter[str]) -> str | None:
    if not gaps:
        return None
    total = sum(gaps.values())
    details = "; ".join(f"{layer}: {count}" for layer, count in gaps.most_common(3))
    if len(gaps) > 3:
        details += f"; ещё {len(gaps) - 3} слоёв"
    return (
        f"Линии зданий без подтверждённой площади ({total}): {details}. "
        "Они видны на карте, но не доказывают свободное место внутри контура. "
        "Остальную геометрию можно рассчитать после подтверждения неполных данных."
    )


def _polygon_parts(geometry: Any) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def _boundary_candidate(shapes: list[Any]) -> BoundaryCandidate:
    polygons = [part for shape in shapes for part in _polygon_parts(shape)]
    lines = [shape for shape in shapes if shape.geom_type == "LineString"]
    basis = "source_surface"
    surfaces: list[Any] = polygons
    if not surfaces and lines:
        surfaces = list(polygonize(lines))
        basis = "polygonized_linework"
    if not surfaces:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.UNAVAILABLE,
            basis=basis,
            issue="Слой не образует замкнутую поверхность",
        )
    try:
        surface = unary_union(surfaces)
    except GEOSException:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            issue="Контуры слоя конфликтуют при объединении",
        )
    if surface.is_empty or not surface.is_valid:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            issue="Объединённая поверхность слоя некорректна",
        )
    # A valid small polygon is not necessarily the whole project boundary.
    # Real drawings can contain a long, nearly closed AutoCAD polyline on the
    # same layer. If its extent and uncovered length dominate the admitted
    # surface, do not silently select the small polygon as the entire site.
    # This is an admission warning, not inferred closure or CAD repair.
    try:
        dominant_open_boundary = any(
            line.convex_hull.area > surface.area
            and line.difference(surface).length > surface.boundary.length
            for line in lines
        )
    except GEOSException:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            issue="Не удалось проверить протяжённые линии границы",
        )
    if dominant_open_boundary:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            area_m2=round(float(surface.area), 3),
            component_count=len(_polygon_parts(surface)),
            issue=(
                "На слое есть более протяжённый незамкнутый контур; "
                "замкнутая часть не подтверждена как вся территория"
            ),
        )
    inset = surface.buffer(-1.5)
    area = float(surface.area)
    inset_area = float(inset.area)
    status = (
        BoundaryCandidateStatus.USABLE
        if inset_area >= 24.0
        else BoundaryCandidateStatus.THIN
    )
    return BoundaryCandidate(
        status=status,
        basis=basis,
        area_m2=round(area, 3),
        inset_1_5m_area_m2=round(inset_area, 3),
        component_count=len(_polygon_parts(surface)),
        issue=(
            None
            if status == BoundaryCandidateStatus.USABLE
            else "После внутреннего отступа 1,5 м не остаётся рабочей площади"
        ),
    )


def build_dxf_import_from_snapshot(
    snapshot: CadSnapshot,
    *,
    source_sha256: str,
    dxf_version: str = "unknown",
    capacity: SourceGeometryCapacity | None = None,
    verified_dependencies: dict[str, str] | None = None,
) -> DxfImportResult:
    """Build the normalized project source from AutoCAD evidence alone.

    No portable DXF parser participates in this path.  The complete coverage
    ledger is authoritative: native geometry is projected, presentation
    context remains inventory-only, and unresolved calculation geometry is
    exposed on its exact layer for operator review.
    """

    units_entry = DXF_UNIT_FACTORS.get(snapshot.source.units_code)
    if units_entry is None:
        raise CadSnapshotProviderError("CAD snapshot uses unsupported drawing units")
    units, _ = units_entry
    imported = DxfImportResult(
        layers=[],
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        dxf_version=dxf_version,
        units=units,
        entity_count=snapshot.summary.source_instances,
        coordinate_reference=CoordinateReference(
            status="unknown",
            source="none",
            evidence=(
                "AutoCAD подтвердил WCS и единицы; геодезическая система "
                "координат в snapshot не объявлена"
            ),
        ),
    )
    return apply_cad_snapshot(
        imported,
        snapshot,
        source_sha256=source_sha256,
        capacity=capacity,
        verified_dependencies=verified_dependencies,
    )


def build_dxf_import_from_snapshot_path(
    path: Path,
    *,
    source: SnapshotSource,
    extraction: ExtractionEvidence,
    dependencies: tuple[CadSnapshotDependency, ...],
    summary: SnapshotSummary,
    source_sha256: str,
    dxf_version: str = "unknown",
    scratch_root: Path,
    capacity: SourceGeometryCapacity | None = None,
    verified_dependencies: dict[str, str] | None = None,
) -> DxfImportResult:
    """Build the project graph without materialising the complete snapshot.

    The caller must first run the streaming snapshot integrity admission. This
    second bounded pass keeps only the publishable GeoJSON graph in memory;
    coverage-to-geometry joins live in a disposable SQLite ledger.
    """

    if source.sha256 != source_sha256:
        raise CadSnapshotProviderError("CAD snapshot belongs to a different DXF")
    expected_dependencies = {item.path: item.sha256 for item in dependencies}
    if expected_dependencies and verified_dependencies != expected_dependencies:
        raise CadSnapshotProviderError(
            "verified XREF package differs from CAD snapshot dependencies"
        )
    units_entry = DXF_UNIT_FACTORS.get(source.units_code)
    if units_entry is None:
        raise CadSnapshotProviderError("CAD snapshot uses unsupported drawing units")
    units, factor = units_entry
    result = DxfImportResult(
        layers=[],
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        dxf_version=dxf_version,
        units=units,
        entity_count=summary.source_instances,
        coordinate_reference=CoordinateReference(
            status="unknown",
            source="none",
            evidence=(
                "AutoCAD подтвердил WCS и единицы; геодезическая система "
                "координат в snapshot не объявлена"
            ),
        ),
    )
    features = result.geometry.feature_collection["features"]
    assert isinstance(features, list)
    capacity = capacity or SourceGeometryCapacity()
    total_coordinates = 0
    instance_types_by_layer: dict[str, Counter[str]] = {}
    unresolved_types_by_layer: dict[str, Counter[str]] = {}
    native_types_by_layer: dict[str, Counter[str]] = {}
    native_shapes_by_layer: dict[str, list[Any]] = {}
    has_polygon_by_layer: dict[str, bool] = {}
    bounds_by_layer: dict[str, tuple[float, float, float, float]] = {}
    geometry_count = 0
    projection_failures: list[tuple[str, str]] = []
    projection_type_counts: Counter[str] = Counter()
    building_area_gaps: Counter[str] = Counter()

    scratch_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="snapshot-project-", dir=scratch_root) as temporary:
        ledger = sqlite3.connect(Path(temporary) / "coverage.sqlite3")
        ledger.execute("PRAGMA journal_mode=OFF")
        ledger.execute("PRAGMA synchronous=OFF")
        ledger.execute("PRAGMA temp_store=FILE")
        ledger.execute(
            "CREATE TABLE geometry_ref ("
            "id TEXT PRIMARY KEY, identity TEXT NOT NULL, layer TEXT NOT NULL, "
            "entity_type TEXT NOT NULL) WITHOUT ROWID"
        )
        ledger.execute(
            "CREATE TABLE source_ref ("
            "identity TEXT PRIMARY KEY, layer TEXT NOT NULL, status TEXT NOT NULL, "
            "path_seen INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID"
        )
        ledger.execute(
            "CREATE TABLE derived_member ("
            "identity TEXT PRIMARY KEY, layer TEXT NOT NULL) WITHOUT ROWID"
        )
        ledger.execute(
            "CREATE TABLE open_building_path ("
            "identity TEXT PRIMARY KEY, layer TEXT NOT NULL, "
            "entity_type TEXT NOT NULL) WITHOUT ROWID"
        )
        try:
            with path.open("rb") as stream:
                for raw in ijson.items(stream, "coverage.item", use_float=True):
                    record = CoverageRecord.model_validate(raw)
                    entity_type = DXF_TYPE_BY_AUTOCAD_CLASS.get(
                        record.entity_type, f"AUTOCAD:{record.entity_type}"
                    )
                    instance_types_by_layer.setdefault(record.layer, Counter())[
                        entity_type
                    ] += 1
                    if record.status == "unresolved":
                        unresolved_types_by_layer.setdefault(record.layer, Counter())[
                            entity_type
                        ] += 1
                    identity = _canonical_bytes(
                        record.identity.model_dump(mode="json")
                    ).decode("utf-8")
                    try:
                        ledger.execute(
                            "INSERT INTO source_ref (identity, layer, status) "
                            "VALUES (?, ?, ?)",
                            (identity, record.layer, record.status),
                        )
                        ledger.executemany(
                            "INSERT INTO geometry_ref"
                            "(id, identity, layer, entity_type) VALUES (?, ?, ?, ?)",
                            (
                                (
                                    geometry_id,
                                    identity,
                                    record.layer,
                                    entity_type,
                                )
                                for geometry_id in record.geometry_ids
                            ),
                        )
                    except sqlite3.IntegrityError as error:
                        raise CadSnapshotProviderError(
                            "CAD snapshot repeats a geometry reference"
                        ) from error

            with path.open("rb") as stream:
                for raw in ijson.items(stream, "geometry.item", use_float=True):
                    geometry = _CAD_GEOMETRY_ADAPTER.validate_python(raw)
                    normalized = geometry.model_dump(mode="json", exclude_none=True)
                    content = {
                        key: value
                        for key, value in normalized.items()
                        if key != "content_sha256"
                    }
                    if _canonical_sha256(content) != geometry.content_sha256:
                        raise CadSnapshotProviderError(
                            f"CAD snapshot geometry hash differs: {geometry.id}"
                        )
                    row = ledger.execute(
                        "SELECT identity, layer, entity_type FROM geometry_ref "
                        "WHERE id = ?",
                        (geometry.id,),
                    ).fetchone()
                    if row is None:
                        raise CadSnapshotProviderError(
                            f"CAD snapshot geometry lacks coverage: {geometry.id}"
                        )
                    identity = _canonical_bytes(
                        geometry.identity.model_dump(mode="json")
                    ).decode("utf-8")
                    if row[0] != identity:
                        raise CadSnapshotProviderError(
                            f"CAD snapshot geometry provenance differs: {geometry.id}"
                        )
                    layer_name, entity_type = str(row[1]), str(row[2])
                    ledger.execute(
                        "DELETE FROM geometry_ref WHERE id = ?", (geometry.id,)
                    )
                    if isinstance(geometry, PathGeometry):
                        ledger.execute(
                            "UPDATE source_ref SET path_seen = 1 WHERE identity = ?",
                            (identity,),
                        )
                    if isinstance(geometry, RegionGeometry) and geometry.derived_from:
                        try:
                            ledger.executemany(
                                "INSERT INTO derived_member (identity, layer) "
                                "VALUES (?, ?)",
                                (
                                    (
                                        _canonical_bytes(
                                            member.model_dump(mode="json")
                                        ).decode("utf-8"),
                                        layer_name,
                                    )
                                    for member in geometry.derived_from
                                ),
                            )
                        except sqlite3.IntegrityError as error:
                            raise CadSnapshotProviderError(
                                "CAD source path contributes to multiple native regions"
                            ) from error

                    try:
                        shape = _native_shape(geometry, factor)
                    except (CadSnapshotProviderError, GEOSException):
                        # The admitted native object is real, but this planar
                        # projection is not. Keep the rest of the street and
                        # mark this exact layer incomplete for calculations.
                        unresolved_types_by_layer.setdefault(layer_name, Counter())[
                            entity_type
                        ] += 1
                        projection_failures.append(
                            (layer_name, geometry.identity.handle)
                        )
                        projection_type_counts[entity_type] += 1
                        continue
                    geojson = mapping(shape)
                    if (
                        isinstance(geometry, PathGeometry)
                        and shape.geom_type == "LineString"
                        and suggest_layer_kind(layer_name) == LayerKind.BUILDING
                    ):
                        ledger.execute(
                            "INSERT INTO open_building_path "
                            "(identity, layer, entity_type) VALUES (?, ?, ?)",
                            (identity, layer_name, entity_type),
                        )
                    if not (
                        isinstance(geometry, RegionGeometry) and geometry.derived_from
                    ):
                        native_types_by_layer.setdefault(layer_name, Counter())[
                            entity_type
                        ] += 1
                    has_polygon = shape.geom_type in {"Polygon", "MultiPolygon"}
                    has_polygon_by_layer[layer_name] = (
                        has_polygon_by_layer.get(layer_name, False) or has_polygon
                    )
                    if is_boundary_candidate_name(layer_name):
                        native_shapes_by_layer.setdefault(layer_name, []).append(shape)
                    shape_bounds = tuple(float(value) for value in shape.bounds)
                    previous = bounds_by_layer.get(layer_name)
                    if previous is None:
                        bounds_by_layer[layer_name] = shape_bounds
                    else:
                        bounds_by_layer[layer_name] = (
                            min(previous[0], shape_bounds[0]),
                            min(previous[1], shape_bounds[1]),
                            max(previous[2], shape_bounds[2]),
                            max(previous[3], shape_bounds[3]),
                        )
                    properties = {
                        "source_layer": layer_name,
                        "kind": suggest_layer_kind(layer_name).value,
                        "entity_type": entity_type,
                        "source_handle": geometry.identity.handle,
                        "source_instance_chain": geometry.identity.instance_chain,
                        "source_geometry_provider": "autocad_snapshot_v1",
                        "source_native_geometry": True,
                        "source_geometry_content_sha256": geometry.content_sha256,
                    }
                    if isinstance(geometry, (RegionGeometry, PathGeometry)):
                        properties["source_sampling_tolerance_m"] = (
                            geometry.achieved_tolerance_m
                        )
                    if isinstance(geometry, PathGeometry):
                        properties["source_closed_path"] = geometry.closed
                        properties["source_polygon_projection"] = has_polygon
                    if isinstance(geometry, RegionGeometry):
                        properties.update(
                            {
                                "source_native_area_units2": (
                                    geometry.native_area_units2
                                ),
                                "source_native_perimeter_units": (
                                    geometry.native_perimeter_units
                                ),
                            }
                        )
                        if geometry.derived_from:
                            properties["source_derived_from"] = [
                                member.model_dump(mode="json")
                                for member in geometry.derived_from
                            ]
                    feature = {
                        "type": "Feature",
                        "id": f"cad-snapshot-{geometry.content_sha256}",
                        "properties": properties,
                        "geometry": geojson,
                    }
                    feature_coordinates = coordinate_count(geojson)
                    capacity.check(
                        features=len(features) + 1,
                        coordinates=total_coordinates + feature_coordinates,
                        feature_coordinates=feature_coordinates,
                        layer=layer_name,
                    )
                    features.append(feature)
                    total_coordinates += feature_coordinates
                    geometry_count += 1
            if ledger.execute("SELECT 1 FROM geometry_ref LIMIT 1").fetchone():
                raise CadSnapshotProviderError(
                    "CAD snapshot misses referenced native geometry"
                )
            if ledger.execute(
                "SELECT 1 FROM derived_member AS member "
                "LEFT JOIN source_ref AS source ON source.identity = member.identity "
                "WHERE source.identity IS NULL OR source.status != 'native' "
                "OR source.layer != member.layer OR source.path_seen != 1 LIMIT 1"
            ).fetchone():
                raise CadSnapshotProviderError(
                    "CAD derived region source is not a same-layer native path"
                )
            for layer_name, entity_type, count in ledger.execute(
                "SELECT path.layer, path.entity_type, COUNT(*) "
                "FROM open_building_path AS path "
                "LEFT JOIN derived_member AS member ON member.identity = path.identity "
                "WHERE member.identity IS NULL "
                "GROUP BY path.layer, path.entity_type"
            ):
                unresolved_types_by_layer.setdefault(layer_name, Counter())[
                    entity_type
                ] += count
                building_area_gaps[layer_name] += count
                # The path was projected for display; do not duplicate it in
                # the generic "not extracted" warning below.
                projection_type_counts[entity_type] += count
        finally:
            ledger.close()

    for layer_index, (layer_name, instance_types) in enumerate(
        instance_types_by_layer.items()
    ):
        kind = suggest_layer_kind(layer_name)
        unresolved_types = unresolved_types_by_layer.get(layer_name, Counter())
        candidate = (
            _boundary_candidate(native_shapes_by_layer.get(layer_name, []))
            if is_boundary_candidate_name(layer_name)
            else None
        )
        if kind == LayerKind.SITE_BORDER and (
            candidate is None or candidate.status != BoundaryCandidateStatus.USABLE
        ):
            kind = LayerKind.IGNORE
        confidence, reasons, review_required = assess_layer_suggestion(
            kind,
            entity_types=dict(instance_types),
            has_polygon=has_polygon_by_layer.get(layer_name, False),
            geometry_complete=not unresolved_types,
            boundary_candidate=candidate,
        )
        result.layers.append(
            Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{layer_name}")),
                source_name=layer_name,
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=sum(instance_types.values()),
                color=_LAYER_COLORS[layer_index % len(_LAYER_COLORS)],
                entity_types=dict(instance_types),
                projected_geometry_types=dict(
                    native_types_by_layer.get(layer_name, Counter())
                ),
                unsupported_geometry_types=dict(unresolved_types),
                unreadable_geometry_count=0,
                geometry_complete=not unresolved_types,
                bounds=list(bounds_by_layer[layer_name])
                if layer_name in bounds_by_layer
                else None,
                suggestion_confidence=confidence,
                suggestion_reasons=reasons,
                # Confidence in a name is not an operator's confirmation.
                mapping_review_required=True,
                mapping_confirmed=False,
                boundary_candidate=candidate,
                required=kind == LayerKind.SITE_BORDER,
            )
        )

    usable_boundaries = [
        layer
        for layer in result.layers
        if layer.boundary_candidate is not None
        and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
    ]
    if len(usable_boundaries) > 1:
        for layer in usable_boundaries:
            if layer.suggested_kind != LayerKind.SITE_BORDER:
                continue
            layer.suggested_kind = LayerKind.IGNORE
            layer.mapped_kind = LayerKind.IGNORE
            layer.suggestion_confidence = LayerSuggestionConfidence.LOW
            layer.suggestion_reasons = [
                "Найдено несколько подходящих контуров территории"
            ]
            layer.mapping_review_required = True
            layer.mapping_confirmed = False
            layer.required = False

    kind_by_layer = {
        layer.source_name: layer.suggested_kind.value for layer in result.layers
    }
    for feature in features:
        source_layer = feature.get("properties", {}).get("source_layer")
        if source_layer in kind_by_layer:
            feature["properties"]["kind"] = kind_by_layer[source_layer]
    remaining_unsupported: Counter[str] = Counter()
    for layer in result.layers:
        remaining_unsupported.update(layer.unsupported_geometry_types)
    remaining_unsupported.subtract(projection_type_counts)
    remaining_unsupported = +remaining_unsupported
    if remaining_unsupported:
        labels = ", ".join(
            f"{name}: {count}" for name, count in sorted(remaining_unsupported.items())
        )
        result.warnings.append(
            f"Не извлечены для расчёта: {labels}. "
            "Исходный чертёж сохранён; эти объекты пока не участвуют в проверках."
        )
    projection_warning = _projection_issue_warning(projection_failures)
    if projection_warning is not None:
        result.warnings.append(projection_warning)
    area_warning = _building_area_warning(building_area_gaps)
    if area_warning is not None:
        result.warnings.append(area_warning)
    result.bounds = list(_bounds(features) or ()) or None
    result.cad_snapshot_provenance = CadSnapshotProvenance(
        schema="green-atlas.autocad-snapshot/1",
        source_sha256=source.sha256,
        payload_sha256=summary.payload_sha256,
        autocad_version=extraction.autocad_version,
        plugin_version=extraction.plugin_version,
        target=extraction.target,
        source_instances=summary.source_instances,
        native_geometry=geometry_count,
        unresolved_instances=summary.unresolved,
        dependencies=[item.model_copy(deep=True) for item in dependencies],
    )
    return result


def apply_cad_snapshot(
    imported: DxfImportResult,
    snapshot: CadSnapshot,
    *,
    source_sha256: str,
    capacity: SourceGeometryCapacity | None = None,
    verified_dependencies: dict[str, str] | None = None,
) -> DxfImportResult:
    """Project admitted native geometry into the temporary project result.

    This compatibility entry point still receives an existing result while the
    snapshot-only project builder is being connected. Native geometry always
    replaces the corresponding source type; unresolved coverage never becomes
    empty or inferred ground and must not fall back to another parser.
    """

    if snapshot.source.sha256 != source_sha256:
        raise CadSnapshotProviderError("CAD snapshot belongs to a different DXF")
    verify_cad_snapshot_integrity(snapshot)
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
        raise CadSnapshotProviderError(
            "CAD snapshot units differ from DXF reader units"
        )

    result = imported.model_copy(deep=True)
    features = result.geometry.feature_collection.setdefault("features", [])
    if not isinstance(features, list):
        raise CadSnapshotProviderError("DXF feature collection is invalid")
    covered_identity_keys = {
        (record.identity.handle, tuple(record.identity.instance_chain))
        for record in snapshot.coverage
    }

    def source_identity(feature: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
        properties = feature.get("properties", {})
        chain = properties.get("source_instance_chain", [])
        return str(properties.get("source_handle", "")), tuple(chain or [])

    # The complete AutoCAD ledger is authoritative.  A compatibility caller
    # may still carry a portable-reader graph, but no covered source instance
    # may survive as fallback geometry when AutoCAD classified it as context
    # or unresolved.
    features[:] = [
        feature
        for feature in features
        if source_identity(feature) not in covered_identity_keys
    ]
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
    native_types_by_layer: dict[str, Counter[str]] = {}
    instance_types_by_layer: dict[str, Counter[str]] = {}
    unresolved_types_by_layer: dict[str, Counter[str]] = {}
    native_shapes_by_layer: dict[str, list[Any]] = {}
    projection_failures: list[tuple[str, str]] = []
    projection_type_counts: Counter[str] = Counter()
    building_area_gaps: Counter[str] = Counter()
    derived_members = {
        (member.handle, tuple(member.instance_chain))
        for item in snapshot.geometry
        if isinstance(item, RegionGeometry) and item.derived_from
        for member in item.derived_from
    }
    for record in snapshot.coverage:
        entity_type = DXF_TYPE_BY_AUTOCAD_CLASS.get(
            record.entity_type, f"AUTOCAD:{record.entity_type}"
        )
        instance_types_by_layer.setdefault(record.layer, Counter())[entity_type] += 1
        if record.status == "unresolved":
            unresolved_types_by_layer.setdefault(record.layer, Counter())[
                entity_type
            ] += 1

    for geometry in snapshot.geometry:
        key = (geometry.identity.handle, tuple(geometry.identity.instance_chain))
        coverage = coverage_by_identity.get(key)
        if coverage is None or geometry.id not in coverage.geometry_ids:
            raise CadSnapshotProviderError(
                f"CAD snapshot geometry lacks matching coverage: {geometry.id}"
            )
        entity_type = DXF_TYPE_BY_AUTOCAD_CLASS.get(
            coverage.entity_type, f"AUTOCAD:{coverage.entity_type}"
        )
        try:
            shape = _native_shape(geometry, factor)
        except (CadSnapshotProviderError, GEOSException):
            unresolved_types_by_layer.setdefault(coverage.layer, Counter())[
                entity_type
            ] += 1
            projection_failures.append((coverage.layer, geometry.identity.handle))
            projection_type_counts[entity_type] += 1
            continue
        native_shapes_by_layer.setdefault(coverage.layer, []).append(shape)
        geojson = mapping(shape)
        if (
            isinstance(geometry, PathGeometry)
            and shape.geom_type == "LineString"
            and suggest_layer_kind(coverage.layer) == LayerKind.BUILDING
            and key not in derived_members
        ):
            unresolved_types_by_layer.setdefault(coverage.layer, Counter())[
                entity_type
            ] += 1
            building_area_gaps[coverage.layer] += 1
            projection_type_counts[entity_type] += 1
        native_counts[coverage.layer] += 1
        if not (isinstance(geometry, RegionGeometry) and geometry.derived_from):
            native_types_by_layer.setdefault(coverage.layer, Counter())[
                entity_type
            ] += 1
        properties = {
            "source_layer": coverage.layer,
            "kind": suggest_layer_kind(coverage.layer).value,
            "entity_type": entity_type,
            "source_handle": geometry.identity.handle,
            "source_instance_chain": geometry.identity.instance_chain,
            "source_geometry_provider": "autocad_snapshot_v1",
            "source_native_geometry": True,
            "source_geometry_content_sha256": geometry.content_sha256,
        }
        if isinstance(geometry, (RegionGeometry, PathGeometry)):
            properties["source_sampling_tolerance_m"] = geometry.achieved_tolerance_m
        if isinstance(geometry, PathGeometry):
            properties["source_closed_path"] = geometry.closed
            properties["source_polygon_projection"] = shape.geom_type in {
                "Polygon",
                "MultiPolygon",
            }
        if isinstance(geometry, RegionGeometry):
            properties.update(
                {
                    "source_native_area_units2": geometry.native_area_units2,
                    "source_native_perimeter_units": (geometry.native_perimeter_units),
                }
            )
            if geometry.derived_from:
                properties["source_derived_from"] = [
                    member.model_dump(mode="json") for member in geometry.derived_from
                ]
        feature = {
            "type": "Feature",
            "id": f"cad-snapshot-{geometry.content_sha256}",
            "properties": properties,
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
    for layer_index, (layer_name, instance_types) in enumerate(
        instance_types_by_layer.items()
    ):
        count = native_counts[layer_name]
        layer = layer_by_name.get(layer_name)
        if layer is None:
            kind = suggest_layer_kind(layer_name)
            layer = Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{layer_name}")),
                source_name=layer_name,
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=sum(instance_types.values()),
                color=_LAYER_COLORS[layer_index % len(_LAYER_COLORS)],
                entity_types=dict(instance_types),
            )
            result.layers.append(layer)
            layer_by_name[layer_name] = layer
        layer.entity_types = dict(instance_types)
        layer.object_count = sum(instance_types.values())
        unresolved_types = unresolved_types_by_layer.get(layer_name, Counter())
        layer.unsupported_geometry_types = dict(unresolved_types)
        layer.unreadable_geometry_count = 0
        if count:
            for entity_type, entity_count in native_types_by_layer.get(
                layer_name, Counter()
            ).items():
                layer.projected_geometry_types[entity_type] = entity_count
        layer.geometry_complete = not unresolved_types
        combined_layer_features = [
            feature
            for feature in features
            if feature.get("properties", {}).get("source_layer") == layer_name
        ]
        layer.bounds = _bounds(combined_layer_features)
        shapes = native_shapes_by_layer.get(layer_name, [])
        has_polygon = any(
            shape.geom_type in {"Polygon", "MultiPolygon"} for shape in shapes
        )
        candidate = (
            _boundary_candidate(shapes)
            if is_boundary_candidate_name(layer_name)
            else None
        )
        kind = suggest_layer_kind(layer_name)
        if kind == LayerKind.SITE_BORDER and (
            candidate is None or candidate.status != BoundaryCandidateStatus.USABLE
        ):
            kind = LayerKind.IGNORE
        confidence, reasons, review_required = assess_layer_suggestion(
            kind,
            entity_types=dict(instance_types),
            has_polygon=has_polygon,
            geometry_complete=layer.geometry_complete,
            boundary_candidate=candidate,
        )
        layer.suggested_kind = kind
        layer.mapped_kind = kind
        layer.suggestion_confidence = confidence
        layer.suggestion_reasons = reasons
        layer.mapping_review_required = True
        layer.mapping_confirmed = False
        layer.boundary_candidate = candidate
        layer.required = kind == LayerKind.SITE_BORDER

    usable_boundaries = [
        layer
        for layer in result.layers
        if layer.boundary_candidate is not None
        and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
    ]
    if len(usable_boundaries) > 1:
        for layer in usable_boundaries:
            if layer.suggested_kind != LayerKind.SITE_BORDER:
                continue
            layer.suggested_kind = LayerKind.IGNORE
            layer.mapped_kind = LayerKind.IGNORE
            layer.suggestion_confidence = LayerSuggestionConfidence.LOW
            layer.suggestion_reasons = [
                "Найдено несколько подходящих контуров территории"
            ]
            layer.mapping_review_required = True
            layer.mapping_confirmed = False
            layer.required = False

    kind_by_layer = {
        layer.source_name: layer.suggested_kind.value for layer in result.layers
    }
    for feature in features:
        source_layer = feature.get("properties", {}).get("source_layer")
        if source_layer in kind_by_layer:
            feature["properties"]["kind"] = kind_by_layer[source_layer]

    result.warnings = [
        warning
        for warning in result.warnings
        if not warning.startswith("Часть типов доступна только в исходном файле:")
    ]
    remaining_unsupported: Counter[str] = Counter()
    for layer in result.layers:
        remaining_unsupported.update(layer.unsupported_geometry_types)
    remaining_unsupported.subtract(projection_type_counts)
    remaining_unsupported = +remaining_unsupported
    if remaining_unsupported:
        labels = ", ".join(
            f"{name}: {count}" for name, count in sorted(remaining_unsupported.items())
        )
        result.warnings.append(
            f"Не извлечены для расчёта: {labels}. "
            "Исходный чертёж сохранён; эти объекты пока не участвуют в проверках."
        )
    projection_warning = _projection_issue_warning(projection_failures)
    if projection_warning is not None:
        result.warnings.append(projection_warning)
    area_warning = _building_area_warning(building_area_gaps)
    if area_warning is not None:
        result.warnings.append(area_warning)

    result.bounds = list(_bounds(features) or ()) or None
    result.cad_snapshot_provenance = CadSnapshotProvenance(
        schema="green-atlas.autocad-snapshot/1",
        source_sha256=snapshot.source.sha256,
        payload_sha256=snapshot.summary.payload_sha256,
        autocad_version=snapshot.extraction.autocad_version,
        plugin_version=snapshot.extraction.plugin_version,
        target=snapshot.extraction.target,
        source_instances=snapshot.summary.source_instances,
        native_geometry=sum(native_counts.values()),
        unresolved_instances=snapshot.summary.unresolved,
        dependencies=[item.model_copy(deep=True) for item in dependencies],
        live_capture=snapshot.source.live_capture,
        live_references=snapshot.live_references,
    )
    return result
