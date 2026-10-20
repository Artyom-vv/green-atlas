"""Преобразование проверенного снимка AutoCAD в локальный проект и GeoJSON; исходник не
перечитывается CAD-парсером.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from shapely import get_coordinates, get_num_coordinates, total_bounds
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from app.cad_bridge.contracts import (
    CadGeometry,
    CadSnapshot,
    CadSnapshotDependency,
    CadSnapshotProvenance,
    ExtractionEvidence,
    LiveCadReference,
    LiveDocumentCapture,
    PathGeometry,
    PointGeometry,
    RegionGeometry,
    SnapshotSource,
    SnapshotSummary,
)
from app.cad_bridge.integrity import canonical_record_sha256 as _canonical_sha256
from app.cad_bridge.integrity import snapshot_payload_sha256
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import (
    DxfImportResult,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.layers.contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    Layer,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layers.suggestions import (
    assess_layer_suggestion,
    is_boundary_candidate_name,
    suggest_layer_kind,
)
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.geometry.geojson_size import coordinate_count
from app.projects.contracts import Project, ProjectStatus


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

_NativeShape = Point | LineString | Polygon | MultiPolygon
_Bounds = tuple[float, float, float, float]
_AREA_DECIMALS = 3
_BOUNDARY_INSET_M = 1.5
_MIN_BOUNDARY_WORKING_AREA_M2 = 24.0
_WARNING_LAYER_LIMIT = 3
_PROJECTION_BOUNDS_BATCH_SIZE = 256


class _LayerProjectionFields(TypedDict, total=False):
    projected_geometry_types: dict[str, int]


class _LiveProvenanceFields(TypedDict, total=False):
    live_capture: LiveDocumentCapture | None
    live_references: list[LiveCadReference] | None


def verify_cad_snapshot_integrity(snapshot: CadSnapshot) -> None:
    """Проверять хеш каждой native-геометрии и полный payload снимка."""

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
    if snapshot_payload_sha256(snapshot) != snapshot.summary.payload_sha256:
        raise CadSnapshotProviderError("CAD snapshot payload hash differs")


def native_geometry_shape(geometry: CadGeometry, factor: float) -> _NativeShape:
    """Проецирует проверенную native-геометрию для чтения снимка и просмотра областей."""
    if isinstance(geometry, RegionGeometry):
        return _region_shape(geometry, factor)
    if isinstance(geometry, PointGeometry):
        return Point(
            geometry.coordinates[0] * factor,
            geometry.coordinates[1] * factor,
        )
    if isinstance(geometry, PathGeometry):
        # GEOS получает плотный массив XY без промежуточных Python-кортежей.
        coordinates = np.asarray(geometry.coordinates, dtype=np.float64)[:, :2] * factor
        if geometry.closed:
            polygon = Polygon(coordinates)
            if not polygon.is_empty and polygon.is_valid and polygon.area > 0:
                return polygon
            # Замкнутый CAD-контур может касаться себя или повторять сегменты. Сохраняем
            # авторские линии для просмотра, не восстанавливая вымышленную расчётную поверхность.
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


def build_project_from_snapshot(
    project: Project,
    snapshot: CadSnapshot,
    *,
    filename: str,
    imported_at: str,
    source_sha256: str,
    source_size: int,
    capacity: SourceGeometryCapacity | None = None,
    consume_owned: bool = False,
) -> Project:
    """Проверить снимок и построить проект по свидетельствам AutoCAD.

    Файловый импортер может передать исключительное владение снимком:
    тогда обработанные модели освобождаются по мере построения GeoJSON.
    Общий вызов сохраняет снимок вызывающей стороны без изменений.
    """
    if snapshot.source.units_code not in DXF_UNIT_FACTORS:
        raise CadSnapshotProviderError("CAD snapshot uses unsupported drawing units")
    _validated_snapshot_units(snapshot, source_sha256, None)
    return _build_project_from_admitted_snapshot(
        project,
        snapshot,
        filename=filename,
        imported_at=imported_at,
        source_sha256=source_sha256,
        source_size=source_size,
        capacity=capacity,
        consume_owned=consume_owned,
    )


def _build_project_from_admitted_snapshot(
    project: Project,
    snapshot: CadSnapshot,
    *,
    filename: str,
    imported_at: str,
    source_sha256: str,
    source_size: int,
    capacity: SourceGeometryCapacity | None = None,
    consume_owned: bool = False,
) -> Project:
    """Внутренний этап после допуска, без внешнего входа с готовым снимком.

    Общий вход проверяет подписи перед этим вызовом. Файловый импортер
    компилирует и потребляет свой снимок в одном закрытом вызове, не передавая
    изменяемый DTO вызывающей стороне между допуском и проекцией.
    """
    units, factor = _snapshot_source_units(snapshot, source_sha256, None)
    layers: list[Layer] = []
    features: list[dict[str, Any]] = []
    bounds, warnings, provenance = _project_native_geometry(
        snapshot,
        factor=factor,
        layers=layers,
        features=features,
        capacity=capacity,
        consume_owned=consume_owned,
    )
    source = SourceFile(
        name=filename,
        content_sha256=source_sha256,
        size=source_size,
        imported_at=imported_at,
        dxf_version="AutoCAD live snapshot",
        units=units,
        entity_count=snapshot.summary.source_instances,
        bounds=bounds,
        warnings=warnings,
        cad_snapshot_provenance=provenance,
    )
    return project.model_copy(
        update={
            "source_file": source,
            "source_review": None,
            "import_status": ImportStatus(
                mode=ImportMode.AUTOCAD_LIVE,
                message="Открыта геометрия текущего документа AutoCAD.",
            ),
            "layers": layers,
            "source_geometry": GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": features}
            ),
            "coordinate_reference": CoordinateReference(
                status="unknown",
                source="none",
                evidence=(
                    "AutoCAD подтвердил WCS и единицы; геодезическая система "
                    "координат в snapshot не объявлена"
                ),
            ),
            "geometry": None,
            "map_ready": False,
            "geometry_version": project.geometry_version + 1,
            "planting_zones": [],
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
            "plan": None,
            "status": ProjectStatus.IMPORTED,
        }
    )


def apply_cad_snapshot(
    imported: DxfImportResult,
    snapshot: CadSnapshot,
    *,
    source_sha256: str,
    capacity: SourceGeometryCapacity | None = None,
    verified_dependencies: dict[str, str] | None = None,
) -> DxfImportResult:
    """Совместимое представление сохранённых DXF-результатов, вне нового native-импорта."""
    units, factor = _validated_snapshot_units(
        snapshot, source_sha256, verified_dependencies
    )
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

    # Полный журнал AutoCAD определяет покрытие. Совместимый граф прежнего reader
    # не даёт права сохранить геометрию объекта, который AutoCAD отметил как контекст
    # или нерешённый источник.
    features[:] = [
        feature
        for feature in features
        if source_identity(feature) not in covered_identity_keys
    ]
    result.bounds, warnings, result.cad_snapshot_provenance = _project_native_geometry(
        snapshot,
        factor=factor,
        layers=result.layers,
        features=features,
        capacity=capacity,
    )
    result.warnings = [
        warning
        for warning in result.warnings
        if not warning.startswith("Часть типов доступна только в исходном файле:")
    ] + warnings
    return result


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


def _combined_bounds(
    shapes: Sequence[BaseGeometry],
    existing: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    """Использовать габариты уже проверенных GEOS-объектов, без обхода их GeoJSON."""
    if not shapes:
        return existing
    x0, y0, x1, y1 = (float(value) for value in total_bounds(shapes))
    if existing is None:
        return x0, y0, x1, y1
    return (
        min(x0, existing[0]),
        min(y0, existing[1]),
        max(x1, existing[2]),
        max(y1, existing[3]),
    )


def _region_shape(region: RegionGeometry, factor: float) -> Polygon | MultiPolygon:
    outer_rings: list[list[tuple[float, float]]] = []
    hole_rings: list[list[tuple[float, float]]] = []
    for loop in region.loops:
        # Native-точность сохраняется: округление может замкнуть узкий разрыв кольца.
        ring = [
            (
                point[0] * factor,
                point[1] * factor,
            )
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
        raise CadSnapshotProviderError(f"REGION {region.id} has an invalid outer ring")
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


def _projection_issue_warning(failures: list[tuple[str, str]]) -> str | None:
    """Сохранять локальные ошибки плоской геометрии без избыточных замечаний при
    открытии.
    """

    if not failures:
        return None
    counts = Counter(layer for layer, _ in failures)
    examples = {layer: handle for layer, handle in failures}
    details = "; ".join(
        f"{layer}: {count} (например, handle {examples[layer]})"
        for layer, count in list(counts.items())[:_WARNING_LAYER_LIMIT]
    )
    if len(counts) > _WARNING_LAYER_LIMIT:
        details += f"; ещё {len(counts) - _WARNING_LAYER_LIMIT} слоёв"
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
    details = "; ".join(
        f"{layer}: {count}" for layer, count in gaps.most_common(_WARNING_LAYER_LIMIT)
    )
    if len(gaps) > _WARNING_LAYER_LIMIT:
        details += f"; ещё {len(gaps) - _WARNING_LAYER_LIMIT} слоёв"
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


def _boundary_candidate(shapes: Sequence[BaseGeometry]) -> BoundaryCandidate:
    polygons = [part for shape in shapes for part in _polygon_parts(shape)]
    lines = [shape for shape in shapes if shape.geom_type == "LineString"]
    basis = "source_surface"
    surfaces: Sequence[BaseGeometry] = polygons
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
    # Корректный малый полигон не обязательно охватывает весь проект. Если на том же
    # слое протяжённый незамкнутый контур превосходит принятую поверхность по размеру
    # и непокрытой длине, не выбираем малый полигон молча. Это замечание допуска,
    # а не восстановление замыкания CAD.
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
            area_m2=round(float(surface.area), _AREA_DECIMALS),
            component_count=len(_polygon_parts(surface)),
            issue=(
                "На слое есть более протяжённый незамкнутый контур; "
                "замкнутая часть не подтверждена как вся территория"
            ),
        )
    inset = surface.buffer(-_BOUNDARY_INSET_M)
    area = float(surface.area)
    inset_area = float(inset.area)
    status = (
        BoundaryCandidateStatus.USABLE
        if inset_area >= _MIN_BOUNDARY_WORKING_AREA_M2
        else BoundaryCandidateStatus.THIN
    )
    return BoundaryCandidate(
        status=status,
        basis=basis,
        area_m2=round(area, _AREA_DECIMALS),
        inset_1_5m_area_m2=round(inset_area, _AREA_DECIMALS),
        component_count=len(_polygon_parts(surface)),
        issue=(
            None
            if status == BoundaryCandidateStatus.USABLE
            else "После внутреннего отступа 1,5 м не остаётся рабочей площади"
        ),
    )


def _native_geojson(geometry: CadGeometry, shape: _NativeShape) -> dict[str, Any]:
    """Перенести координаты примитива из GEOS без Python-обхода каждой точки.

    У пути только один контур, даже после перехода в полигон. Для REGION
    библиотечный mapping сохраняет принадлежность отверстий и частей области.
    """
    if isinstance(geometry, RegionGeometry):
        return mapping(shape)
    coordinates = tuple(map(tuple, get_coordinates(shape).tolist()))
    kind = shape.geom_type
    if kind == "Point":
        return {"type": kind, "coordinates": coordinates[0]}
    if kind == "Polygon":
        return {"type": kind, "coordinates": (coordinates,)}
    return {"type": kind, "coordinates": coordinates}


def _native_feature(
    geometry: CadGeometry,
    shape: _NativeShape,
    layer_name: str,
    entity_type: str,
    kind: LayerKind,
) -> dict[str, Any]:
    geojson = _native_geojson(geometry, shape)
    properties: dict[str, Any] = {
        "source_layer": layer_name,
        "kind": kind.value,
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
    return {
        "type": "Feature",
        "id": f"cad-snapshot-{geometry.content_sha256}",
        "properties": properties,
        "geometry": geojson,
    }


def _new_native_layer(
    layer_name: str,
    layer_index: int,
    instance_types: Counter[str],
    native_types: Counter[str] | None = None,
) -> Layer:
    projection: _LayerProjectionFields = {}
    if native_types is not None:
        projection["projected_geometry_types"] = dict(native_types)
    kind = suggest_layer_kind(layer_name)
    return Layer(
        id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{layer_name}")),
        source_name=layer_name,
        suggested_kind=kind,
        mapped_kind=kind,
        object_count=sum(instance_types.values()),
        color=_LAYER_COLORS[layer_index % len(_LAYER_COLORS)],
        entity_types=dict(instance_types),
        **projection,
    )


def _update_native_layer(
    layer: Layer,
    instance_types: Counter[str],
    native_types: Counter[str],
    unresolved_types: Counter[str],
    context_types: Counter[str],
    *,
    bounds: _Bounds | None,
    shapes: Sequence[BaseGeometry],
    has_polygon: bool,
) -> None:
    layer.entity_types = dict(instance_types)
    layer.object_count = sum(instance_types.values())
    layer.unsupported_geometry_types = dict(unresolved_types)
    layer.unreadable_geometry_count = 0
    layer.projected_geometry_types.update(native_types)
    layer.context_entity_types = dict(context_types)
    layer.geometry_complete = not unresolved_types
    layer.bounds = bounds
    candidate = (
        _boundary_candidate(shapes)
        if is_boundary_candidate_name(layer.source_name)
        else None
    )
    kind = suggest_layer_kind(layer.source_name)
    if kind == LayerKind.SITE_BORDER and (
        candidate is None or candidate.status != BoundaryCandidateStatus.USABLE
    ):
        kind = LayerKind.IGNORE
    confidence, reasons, _ = assess_layer_suggestion(
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


def _finish_native_projection(
    layers: list[Layer],
    features: list[dict[str, Any]],
    projection_failures: list[tuple[str, str]],
    projection_type_counts: Counter[str],
    building_area_gaps: Counter[str],
    bounds: tuple[float, float, float, float] | None,
) -> tuple[list[float] | None, list[str]]:
    warnings: list[str] = []
    usable_boundaries = [
        layer
        for layer in layers
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

    kind_by_layer = {layer.source_name: layer.suggested_kind.value for layer in layers}
    for feature in features:
        source_layer = feature.get("properties", {}).get("source_layer")
        if source_layer in kind_by_layer:
            feature["properties"]["kind"] = kind_by_layer[source_layer]
    remaining_unsupported: Counter[str] = Counter()
    for layer in layers:
        remaining_unsupported.update(layer.unsupported_geometry_types)
    remaining_unsupported.subtract(projection_type_counts)
    remaining_unsupported = +remaining_unsupported
    if remaining_unsupported:
        labels = ", ".join(
            f"{name}: {count}" for name, count in sorted(remaining_unsupported.items())
        )
        warnings.append(
            f"Не извлечены для расчёта: {labels}. "
            "Исходный чертёж сохранён; эти объекты пока не участвуют в проверках."
        )
    projection_warning = _projection_issue_warning(projection_failures)
    if projection_warning is not None:
        warnings.append(projection_warning)
    area_warning = _building_area_warning(building_area_gaps)
    if area_warning is not None:
        warnings.append(area_warning)
    return list(bounds) if bounds is not None else None, warnings


def _snapshot_provenance(
    source: SnapshotSource,
    extraction: ExtractionEvidence,
    summary: SnapshotSummary,
    dependencies: Sequence[CadSnapshotDependency],
    geometry_count: int,
    *,
    live_references: list[LiveCadReference] | None = None,
    include_live_capture: bool = False,
) -> CadSnapshotProvenance:
    live_fields: _LiveProvenanceFields = {}
    if include_live_capture:
        live_fields = {
            "live_capture": source.live_capture,
            "live_references": live_references,
        }
    return CadSnapshotProvenance(
        schema_="green-atlas.autocad-snapshot/1",
        source_sha256=source.sha256,
        payload_sha256=summary.payload_sha256,
        autocad_version=extraction.autocad_version,
        plugin_version=extraction.plugin_version,
        target=extraction.target,
        source_instances=summary.source_instances,
        native_geometry=geometry_count,
        unresolved_instances=summary.unresolved,
        dependencies=[item.model_copy(deep=True) for item in dependencies],
        **live_fields,
    )


def _validated_snapshot_units(
    snapshot: CadSnapshot,
    source_sha256: str,
    verified_dependencies: dict[str, str] | None,
) -> tuple[str, float]:
    if snapshot.source.sha256 != source_sha256:
        raise CadSnapshotProviderError("CAD snapshot belongs to a different DXF")
    verify_cad_snapshot_integrity(snapshot)
    return _snapshot_source_units(snapshot, source_sha256, verified_dependencies)


def _snapshot_source_units(
    snapshot: CadSnapshot,
    source_sha256: str,
    verified_dependencies: dict[str, str] | None,
) -> tuple[str, float]:
    if snapshot.source.sha256 != source_sha256:
        raise CadSnapshotProviderError("CAD snapshot belongs to a different DXF")
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
    return units, factor


def _project_native_geometry(
    snapshot: CadSnapshot,
    *,
    factor: float,
    layers: list[Layer],
    features: list[dict[str, Any]],
    capacity: SourceGeometryCapacity | None,
    consume_owned: bool = False,
) -> tuple[list[float] | None, list[str], CadSnapshotProvenance]:
    """Однократно проецировать в исходник вызывающего кода, сохраняя происхождение и
    пропуски.
    """
    capacity = capacity or SourceGeometryCapacity()
    total_coordinates = sum(
        coordinate_count(feature.get("geometry", {})) for feature in features
    )
    existing_bounds = _bounds(features)
    existing_features_by_layer: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        name = feature.get("properties", {}).get("source_layer")
        if isinstance(name, str):
            existing_features_by_layer.setdefault(name, []).append(feature)
    layer_by_name = {layer.source_name: layer for layer in layers}
    coverage_by_identity = {
        (record.identity.handle, tuple(record.identity.instance_chain)): record
        for record in snapshot.coverage
    }
    native_counts: Counter[str] = Counter()
    native_types_by_layer: dict[str, Counter[str]] = {}
    instance_types_by_layer: dict[str, Counter[str]] = {}
    unresolved_types_by_layer: dict[str, Counter[str]] = {}
    context_types_by_layer: dict[str, Counter[str]] = {}
    native_shapes_by_layer: dict[str, list[_NativeShape]] = {}
    native_bounds_by_layer: dict[str, _Bounds | None] = {}
    polygon_layers: set[str] = set()
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
        if record.status == "context":
            context_types_by_layer.setdefault(record.layer, Counter())[entity_type] += 1
        if record.status == "unresolved":
            unresolved_types_by_layer.setdefault(record.layer, Counter())[
                entity_type
            ] += 1

    # Признак принадлежит слою. Тысячи объектов одного слоя используют ту же
    # подсказку; окончательная роль по геометрии всё равно назначается ниже.
    native_kinds = {name: suggest_layer_kind(name) for name in instance_types_by_layer}
    boundary_layers = {
        name for name in instance_types_by_layer if is_boundary_candidate_name(name)
    }
    for geometry in _projection_records(snapshot, consume_owned=consume_owned):
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
            shape = native_geometry_shape(geometry, factor)
        except (CadSnapshotProviderError, GEOSException):
            unresolved_types_by_layer.setdefault(coverage.layer, Counter())[
                entity_type
            ] += 1
            projection_failures.append((coverage.layer, geometry.identity.handle))
            projection_type_counts[entity_type] += 1
            continue
        shapes = native_shapes_by_layer.setdefault(coverage.layer, [])
        shapes.append(shape)
        if shape.geom_type in {"Polygon", "MultiPolygon"}:
            polygon_layers.add(coverage.layer)
        if (
            coverage.layer not in boundary_layers
            and len(shapes) >= _PROJECTION_BOUNDS_BATCH_SIZE
        ):
            # После GeoJSON для обычного слоя нужны только габариты. Полный
            # набор GEOS сохраняется лишь там, где проверяем кандидата границы.
            native_bounds_by_layer[coverage.layer] = _combined_bounds(
                shapes, native_bounds_by_layer.get(coverage.layer)
            )
            shapes.clear()
        if (
            isinstance(geometry, PathGeometry)
            and shape.geom_type == "LineString"
            and native_kinds[coverage.layer] == LayerKind.BUILDING
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
        feature = _native_feature(
            geometry, shape, coverage.layer, entity_type, native_kinds[coverage.layer]
        )
        feature_coordinates = int(get_num_coordinates(shape))
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
        layer = layer_by_name.get(layer_name)
        if layer is None:
            layer = _new_native_layer(layer_name, layer_index, instance_types)
            layers.append(layer)
            layer_by_name[layer_name] = layer
        shapes = native_shapes_by_layer.get(layer_name, [])
        _update_native_layer(
            layer,
            instance_types,
            native_types_by_layer.get(layer_name, Counter()),
            unresolved_types_by_layer.get(layer_name, Counter()),
            context_types_by_layer.get(layer_name, Counter()),
            bounds=_combined_bounds(
                shapes,
                _merge_bounds(
                    native_bounds_by_layer.get(layer_name),
                    _bounds(existing_features_by_layer.get(layer_name, [])),
                ),
            ),
            shapes=shapes,
            has_polygon=layer_name in polygon_layers,
        )
        if layer.bounds is not None:
            x0, y0, x1, y1 = layer.bounds
            existing_bounds = _merge_bounds(existing_bounds, (x0, y0, x1, y1))
        shapes.clear()

    bounds, warnings = _finish_native_projection(
        layers,
        features,
        projection_failures,
        projection_type_counts,
        building_area_gaps,
        existing_bounds,
    )
    provenance = _snapshot_provenance(
        snapshot.source,
        snapshot.extraction,
        snapshot.summary,
        snapshot.dependencies or [],
        sum(native_counts.values()),
        live_references=snapshot.live_references,
        include_live_capture=True,
    )
    return bounds, warnings, provenance


def _merge_bounds(left: _Bounds | None, right: _Bounds | None) -> _Bounds | None:
    if left is None:
        return right
    if right is None:
        return left
    return (
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    )


def _projection_records(
    snapshot: CadSnapshot, *, consume_owned: bool
) -> Iterator[CadGeometry]:
    """Освобождать допущенные модели только при исключительном владении снимком.

    Проверка всех подписей и связей завершена до начала проекции. Обход
    сохраняет исходный порядок; предложения областей остаются в снимке
    для компактного индекса последующей проверки.
    """
    if not consume_owned:
        yield from snapshot.geometry
        return
    records = snapshot.geometry
    snapshot.geometry = []
    records.reverse()
    while records:
        yield records.pop()
