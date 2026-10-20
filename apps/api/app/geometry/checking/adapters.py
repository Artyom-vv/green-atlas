"""Геометрический адаптер Shapely для общей проверки посадок и покрытия; расчётные
данные отделены от карты.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any, Literal, cast

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.layers.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.dxf_import.source.admission import (
    partial_geometry_accepted,
    require_confirmed_layer_mapping,
    require_usable_site_boundary,
)
from app.geometry.checking.checker_basis import (
    DEFAULT_CHECKER_CACHE_PROJECTS,
    checker_basis,
    checker_project,
)
from app.geometry.checking.config import (
    CALCULATION_PROGRESS_COMPLETE,
    CALCULATION_PROGRESS_START,
    CONSTRAINT_PROGRESS_SPAN,
    CONSTRAINT_UNION_PROGRESS_SHARE,
    GEOMETRY_UNION_BATCH_SIZE,
    OCCUPIED_PROGRESS_END,
    OCCUPIED_PROGRESS_START,
    SITE_PROGRESS_END,
    SOURCE_READ_PROGRESS_END,
    SOURCE_READ_PROGRESS_STEPS,
    WORK_AREAS_PROGRESS_START,
)
from app.geometry.checking.domain import (
    CONSTRAINT_KINDS,
    OCCUPIED_KINDS,
    PositionAdvisory,
    PositionChecker,
    PositionViolation,
    envelope_distance,
)
from app.geometry.checking.rule_trace import position_rule_trace
from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import PositionRejected
from app.operations.progress import ProgressReporter, WorkProgress
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace
from app.shared.error_messages import message
from app.shared.failures import DomainFailure, FailureReason

# Эти сущности могут задавать объём здания, подпорной стены или сети, но модуль
# чтения не получает из них проверяемый плоский контур. Отсутствие контура нельзя
# считать свободной землёй. Оператор может явно выбрать ``ignore`` для слоя,
# если подтвердил, что он содержит только справочную графику.
UNPROJECTABLE_PHYSICAL_ENTITY_TYPES = frozenset(
    {
        "3DSOLID",
        "BODY",
        "REGION",
        "SURFACE",
        "EXTRUDEDSURFACE",
        "LOFTEDSURFACE",
        "PLANESURFACE",
        "REVOLVEDSURFACE",
        "SWEPTSURFACE",
        "NURBSSURFACE",
    }
)
PHYSICAL_LAYER_KINDS = frozenset(
    LayerKind(kind)
    for kind in (*CONSTRAINT_KINDS, *OCCUPIED_KINDS, "site_border", "utility")
)


def _unprojectable_physical_layers(
    project: Project, source_features: list[dict]
) -> dict[str, set[str]]:
    """Находим назначенные физические слои с недоступной расчётной геометрией.

    ``Layer.entity_types`` учитывает сущности без объектов на карте. Объекты
    MESH/POLYLINE/PROXY проверяем отдельно: они могут сохраняться для отображения,
    но не задавать расчётных ограничений.
    """
    issues: dict[str, set[str]] = {}
    mapped_layers = {
        layer.source_name: layer
        for layer in project.layers
        if layer.mapped_kind in PHYSICAL_LAYER_KINDS
    }
    for source_name, layer in mapped_layers.items():
        unsupported = set(layer.entity_types).intersection(
            UNPROJECTABLE_PHYSICAL_ENTITY_TYPES
        )
        unsupported = {
            entity_type
            for entity_type in unsupported
            if layer.projected_geometry_types.get(entity_type, 0)
            < layer.entity_types.get(entity_type, 0)
        }
        if unsupported:
            issues[source_name] = unsupported
    for feature in source_features:
        properties = feature.get("properties", {})
        source_name = str(properties.get("source_layer", ""))
        if source_name not in mapped_layers or not properties.get(
            "source_context_only"
        ):
            continue
        entity_type = str(properties.get("entity_type", ""))
        if entity_type == "ACAD_PROXY_ENTITY" or properties.get("source_mesh_context"):
            issues.setdefault(source_name, set()).add(entity_type or "MESH")
    return issues


def _native_building_paths_without_surface(
    mapping_by_layer: dict[str, LayerKind | None], source_features: list[dict]
) -> set[str]:
    """Линия на карте не доказывает, что внутри здания посадка запрещена.

    Используем подтверждённое оператором назначение слоя. Исходный путь задаёт
    контур здания, если он проецируется в полигон либо снимок содержит поверхность
    из той же сущности и того же экземпляра блока. Остальные открытые пути здания
    остаются видимыми, но расчёт явно помечается как частичный.
    """

    derived_members: set[tuple[str, tuple[str, ...]]] = set()
    for feature in source_features:
        properties = feature.get("properties", {})
        if (feature.get("geometry") or {}).get("type") not in {
            "Polygon",
            "MultiPolygon",
        }:
            continue
        for member in properties.get("source_derived_from", []):
            if isinstance(member, dict):
                derived_members.add(
                    (
                        str(member.get("handle", "")),
                        tuple(member.get("instance_chain", [])),
                    )
                )

    incomplete: set[str] = set()
    for feature in source_features:
        properties = feature.get("properties", {})
        source_layer = str(properties.get("source_layer", ""))
        if (
            mapping_by_layer.get(source_layer) != LayerKind.BUILDING
            or properties.get("source_geometry_provider") != "autocad_snapshot_v1"
            or "source_closed_path" not in properties
            or (feature.get("geometry") or {}).get("type")
            in {"Polygon", "MultiPolygon"}
        ):
            continue
        identity = (
            str(properties.get("source_handle", "")),
            tuple(properties.get("source_instance_chain", [])),
        )
        if identity not in derived_members:
            incomplete.add(source_layer)
    return incomplete


def _polygons(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [polygon for part in geometry.geoms for polygon in _polygons(part)]
    return []


def _stable_polygon_parts(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Разделяем несвязанные объекты карты вместо общего MultiPolygon.

    Расчёт ограничений использует целую геометрию. Разделяем только отображение,
    чтобы наведение на один отступ не подсвечивало все несвязанные объекты,
    подпадающие под тот же норматив.
    """
    return sorted(
        (polygon for polygon in _polygons(geometry) if not polygon.is_empty),
        key=lambda polygon: (
            *(round(value, 6) for value in polygon.bounds),
            round(polygon.area, 6),
        ),
    )


def _fragment_features(
    geometry: BaseGeometry,
    *,
    feature_id: str,
    properties: dict,
) -> list[dict]:
    parts = _stable_polygon_parts(geometry)
    count = len(parts)
    return [
        {
            "type": "Feature",
            "id": feature_id if count == 1 else f"{feature_id}-part-{index}",
            "properties": {
                **properties,
                "calculation_fragment": True,
                "fragment_index": index,
                "fragment_count": count,
                "area_m2": round(part.area, 2),
            },
            "geometry": mapping(part),
        }
        for index, part in enumerate(parts, start=1)
    ]


def _union_in_batches(
    geometries: list[BaseGeometry],
    *,
    progress: ProgressReporter | None,
    stage: str,
    fraction_start: float,
    fraction_end: float,
    batch_size: int = GEOMETRY_UNION_BATCH_SIZE,
) -> BaseGeometry:
    """Объединяем порции, сохраняя точный результат и отчёт о завершённой работе.

    Внутри атомарного шага GEOS нет измеримого прогресса. После порций сообщаем
    неопределённую длительность итогового объединения и не обещаем проценты.
    """
    if not geometries:
        return GeometryCollection()
    if len(geometries) <= batch_size:
        _report(progress, stage)
        return unary_union(geometries)

    partials: list[BaseGeometry] = []
    total = len(geometries)
    for offset in range(0, total, batch_size):
        partials.append(unary_union(geometries[offset : offset + batch_size]))
        processed = min(total, offset + batch_size)
        _report(
            progress,
            stage=stage,
            fraction=fraction_start
            + (fraction_end - fraction_start) * processed / total,
            processed=processed,
            total=total,
            unit=message("geometry.progress.geometry_unit"),
        )
    _report(
        progress,
        message(
            "geometry.progress.consolidate",
            {"stage": stage.removeprefix(message("geometry.progress.union_prefix"))},
        ),
    )
    return unary_union(partials)


def _report(
    progress: ProgressReporter | None,
    stage: str,
    fraction: float | None = None,
    processed: int | None = None,
    total: int | None = None,
    unit: str | None = None,
) -> None:
    """Сообщаем о завершённой работе; исключение обработчика сразу прерывает расчёт."""
    if progress:
        progress(WorkProgress(stage, fraction, processed, total, unit))


def _require_physical_geometry(
    project: Project,
    source_features: list[dict[str, Any]],
    mapping_by_layer: dict[str, LayerKind | None],
    allow_partial: bool,
) -> list[str]:
    """Проверяем пропуски до проекции; частичный допуск не скрывает ошибки объектов."""
    unprojectable_layers = _unprojectable_physical_layers(project, source_features)
    if unprojectable_layers and not allow_partial:
        labels = [
            f"{source_name} ({', '.join(sorted(entity_types))})"
            for source_name, entity_types in sorted(unprojectable_layers.items())
        ]
        raise DomainFailure(
            FailureReason.GEOMETRY_UNPROJECTABLE_LAYERS, {"layer_names": labels}
        )
    incomplete_physical_layers = sorted(
        {
            layer.source_name
            for layer in project.layers
            if layer.mapped_kind in PHYSICAL_LAYER_KINDS and not layer.geometry_complete
        }
        | set(unprojectable_layers)
        | _native_building_paths_without_surface(mapping_by_layer, source_features)
    )
    if incomplete_physical_layers and not allow_partial:
        raise DomainFailure(
            FailureReason.GEOMETRY_INCOMPLETE_LAYERS,
            {"layer_names": incomplete_physical_layers},
        )
    blocking_arrays = [
        feature.get("properties", {})
        for feature in source_features
        if feature.get("properties", {}).get("source_analysis_blocking")
    ]
    if blocking_arrays:
        total = sum(
            int(
                item.get(
                    "source_block_components", item.get("source_block_instances", 0)
                )
                or 0
            )
            for item in blocking_arrays
        )
        raise DomainFailure(
            FailureReason.GEOMETRY_BLOCK_ARRAY_UNSUPPORTED,
            {"component_count": total},
        )
    return incomplete_physical_layers


def _calculation_geometry(
    feature: dict[str, Any],
    kind: LayerKind,
    invalid_layers: set[str],
) -> tuple[BaseGeometry | None, bool]:
    """Сохраняем авторский контур; второй результат определяет, сообщать ли об обработанном объекте."""
    properties = feature["properties"]
    source_layer = str(properties.get("source_layer", ""))
    try:
        geometry = shape(feature["geometry"])
    except Exception:
        invalid_layers.add(source_layer)
        properties["source_invalid_geometry"] = True
        return None, False
    if geometry.is_empty or not geometry.is_valid:
        if kind in PHYSICAL_LAYER_KINDS or kind == LayerKind.LAWN:
            invalid_layers.add(source_layer)
            properties["source_invalid_geometry"] = True
        return None, True
    if kind == LayerKind.SITE_BORDER:
        center_geometry = properties.get("source_polyline_center_geometry")
        if isinstance(center_geometry, dict):
            try:
                geometry = shape(center_geometry)
            except (KeyError, TypeError, ValueError):
                geometry = GeometryCollection()
            if geometry.is_empty or not geometry.is_valid:
                invalid_layers.add(source_layer)
                properties["source_invalid_boundary_geometry"] = True
                return None, False
    return geometry, True


def _project_source_features(
    source_features: list[dict[str, Any]],
    mapping_by_layer: dict[str, LayerKind | None],
    utility_by_layer: dict[str, dict[str, Any]],
    progress: ProgressReporter | None,
) -> tuple[dict[LayerKind, list[BaseGeometry]], list[dict[str, Any]]]:
    """Сохраняем порядок отображения; контекст не становится расчётным препятствием."""
    grouped: dict[LayerKind, list[BaseGeometry]] = {kind: [] for kind in LayerKind}
    visible_features: list[dict[str, Any]] = []
    invalid_layers: set[str] = set()
    total = len(source_features)
    report_step = max(1, total // SOURCE_READ_PROGRESS_STEPS)
    stage = message("geometry.progress.read_source")
    unit = message("geometry.progress.object_unit")
    _report(
        progress,
        stage,
        CALCULATION_PROGRESS_START,
        0,
        total,
        unit,
    )
    for index, feature in enumerate(source_features, start=1):
        properties = feature.setdefault("properties", {})
        kind = (
            LayerKind.IGNORE
            if properties.get("source_context_only")
            else mapping_by_layer.get(properties.get("source_layer", ""))
            or LayerKind.IGNORE
        )
        properties["kind"] = kind.value
        assign_utility_context(properties, utility_by_layer)
        report = True
        if kind != LayerKind.IGNORE:
            geometry, report = _calculation_geometry(feature, kind, invalid_layers)
            if geometry is not None:
                grouped[kind].append(geometry)
        visible_features.append(feature)
        # Нечитаемый объект и негодная авторская граница не сообщают о завершённой обработке.
        if report and (index % report_step == 0 or index == total):
            _report(
                progress,
                stage,
                SOURCE_READ_PROGRESS_END * index / max(1, total),
                index,
                total,
                unit,
            )
    if invalid_layers:
        raise DomainFailure(
            FailureReason.GEOMETRY_INVALID_LAYERS,
            {"layer_names": sorted(invalid_layers)},
        )
    return grouped, visible_features


def _site_geometry(
    candidates: list[BaseGeometry], progress: ProgressReporter | None
) -> tuple[BaseGeometry | None, dict[str, Any] | None]:
    """Собираем подтверждённую границу; её отсутствие оставляет ручные рабочие области."""
    _report(
        progress,
        message("geometry.progress.assemble_site"),
        None,
        len(candidates),
        len(candidates),
        message("geometry.progress.contour_unit"),
    )
    polygons = [polygon for geometry in candidates for polygon in _polygons(geometry)]
    if not polygons:
        polygons = list(
            polygonize(
                [
                    geometry
                    for geometry in candidates
                    if geometry.geom_type in {"LineString", "MultiLineString"}
                ]
            )
        )
    site = unary_union(polygons).buffer(0) if polygons else None
    surface = (
        None
        if site is None or site.is_empty
        else {
            "type": "Feature",
            "id": "calculated-site-surface",
            "properties": {
                "kind": "site_surface",
                "label": message("geometry.site_surface"),
                "area_m2": round(site.area, 2),
                "calculation_only": True,
            },
            "geometry": mapping(site),
        }
    )
    _report(
        progress,
        message("geometry.progress.site_ready")
        if site is not None
        else message("geometry.progress.site_missing"),
        SITE_PROGRESS_END,
        len(candidates),
        len(candidates),
        message("geometry.progress.contour_unit"),
    )
    return site, surface


def _append_constraint(
    clipped: BaseGeometry,
    forbidden_parts: list[BaseGeometry],
    features: list[dict[str, Any]],
    *,
    feature_id: str,
    rule_id: str,
    label: str,
    distance: float,
) -> None:
    """Публикуем раздельные фрагменты, сохраняя целую геометрию для вычитания."""
    if not clipped.is_empty:
        forbidden_parts.append(clipped)
        features.extend(
            _fragment_features(
                clipped,
                feature_id=feature_id,
                properties={
                    "kind": "forbidden",
                    "rule_id": rule_id,
                    "label": label,
                    "distance_m": distance,
                },
            )
        )


def _constraint_geometry(
    grouped: dict[LayerKind, list[BaseGeometry]],
    site: BaseGeometry | None,
    progress: ProgressReporter | None,
) -> tuple[BaseGeometry, list[dict[str, Any]]]:
    """Строим нормативные и занятые зоны по существующему реестру правил."""
    forbidden_parts: list[BaseGeometry] = []
    features: list[dict[str, Any]] = []
    relevant: dict[str, tuple[float, list[BaseGeometry]]] = {}
    for registry_kind in CONSTRAINT_KINDS:
        kind = LayerKind(registry_kind)
        distance = envelope_distance(registry_kind)
        vicinity = site.buffer(distance) if site is not None else None
        geometries = [
            geometry
            for geometry in grouped[kind]
            if vicinity is None or geometry.intersects(vicinity)
        ]
        relevant[registry_kind] = distance, geometries
    total = sum(len(items) for _, items in relevant.values())
    processed = 0
    rule_count = len(relevant)
    for index, (registry_kind, (distance, geometries)) in enumerate(relevant.items()):
        if not geometries:
            continue
        rule_id, label, _ = CONSTRAINT_KINDS[registry_kind]
        fraction_start = (
            SITE_PROGRESS_END + CONSTRAINT_PROGRESS_SPAN * index / rule_count
        )
        fraction_end = (
            SITE_PROGRESS_END + CONSTRAINT_PROGRESS_SPAN * (index + 1) / rule_count
        )
        _report(
            progress,
            message("geometry.progress.build_zone", {"label": label.lower()}),
            None,
            processed,
            total,
            message("geometry.progress.constraint_unit"),
        )
        unioned = _union_in_batches(
            geometries,
            progress=progress,
            stage=message("geometry.progress.union_objects", {"label": label.lower()}),
            fraction_start=fraction_start,
            fraction_end=fraction_start
            + (fraction_end - fraction_start) * CONSTRAINT_UNION_PROGRESS_SHARE,
        )
        buffered = unioned.buffer(distance)
        # Сначала завершаем пересечение, затем сообщаем о готовой зоне.
        clipped = buffered.intersection(site) if site is not None else buffered
        processed += len(geometries)
        fraction = max(
            fraction_end,
            SITE_PROGRESS_END + CONSTRAINT_PROGRESS_SPAN * processed / max(1, total),
        )
        _report(
            progress,
            message("geometry.progress.zone_ready", {"label": label.lower()}),
            fraction,
            processed,
            total,
            message("geometry.progress.constraint_unit"),
        )
        _append_constraint(
            clipped,
            forbidden_parts,
            features,
            feature_id=f"forbidden-{rule_id}",
            rule_id=rule_id,
            label=label,
            distance=distance,
        )
    for registry_kind, (_, label) in OCCUPIED_KINDS.items():
        kind = LayerKind(registry_kind)
        geometries = grouped[kind]
        if not geometries:
            continue
        occupied = _union_in_batches(
            geometries,
            progress=progress,
            stage=message("geometry.progress.occupied", {"label": label.lower()}),
            fraction_start=OCCUPIED_PROGRESS_START,
            fraction_end=OCCUPIED_PROGRESS_END,
        )
        clipped = occupied.intersection(site) if site is not None else occupied
        _append_constraint(
            clipped,
            forbidden_parts,
            features,
            feature_id=f"occupied-{kind.value}",
            rule_id=f"occupied-{kind.value}",
            label=message("geometry.occupied_area", {"label": label.lower()}),
            distance=0,
        )
    _report(
        progress,
        message("geometry.progress.allowed_area"),
        None,
        processed,
        total,
        message("geometry.progress.constraint_unit"),
    )
    forbidden = (
        unary_union(forbidden_parts) if forbidden_parts else GeometryCollection()
    )
    return forbidden, features


class ShapelyGeometryEngine:
    """Строим нормативные отступы и допустимые области по исходной геометрии."""

    def __init__(
        self, max_cached_projects: int = DEFAULT_CHECKER_CACHE_PROJECTS
    ) -> None:
        self.max_cached_projects = max_cached_projects
        self._position_checkers: OrderedDict[str, tuple[tuple, PositionChecker]] = (
            OrderedDict()
        )
        self._checker_lock = RLock()

    def _position_checker(self, project: Project) -> PositionChecker:
        # Изменение рабочих областей влияет на проверку посадки и роста даже без
        # новой ревизии исходной геометрии. Старую границу нельзя брать из кеша.
        version = checker_basis(project)
        with self._checker_lock:
            cached = self._position_checkers.get(project.id)
            if cached is not None and cached[0] == version:
                self._position_checkers.move_to_end(project.id)
                return cached[1]
            checker = PositionChecker(checker_project(project))
            self._position_checkers[project.id] = (version, checker)
            self._position_checkers.move_to_end(project.id)
            while len(self._position_checkers) > self.max_cached_projects:
                self._position_checkers.popitem(last=False)
            return checker

    def discard(self, project_id: str) -> None:
        with self._checker_lock:
            self._position_checkers.pop(project_id, None)

    def calculate(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        require_confirmed_layer_mapping(project)
        require_usable_site_boundary(project)
        allow_partial = partial_geometry_accepted(project.source_file)
        provenance = (
            project.source_file.prepared_provenance if project.source_file else None
        )
        review = provenance.opening_review if provenance else None
        if project.source_geometry is None:
            raise DomainFailure(FailureReason.GEOMETRY_SOURCE_MISSING)
        mapping_by_layer = {
            layer.source_name: layer.mapped_kind for layer in project.layers
        }
        utility_by_layer = utility_contexts_by_layer(project.layers)
        source_features = deepcopy(
            project.source_geometry.feature_collection.get("features", [])
        )
        incomplete_physical_layers = _require_physical_geometry(
            project, source_features, mapping_by_layer, allow_partial
        )
        grouped, visible_features = _project_source_features(
            source_features, mapping_by_layer, utility_by_layer, progress
        )
        full_site, site_surface_feature = _site_geometry(
            grouped[LayerKind.SITE_BORDER], progress
        )
        site = full_site
        forbidden, constraint_features = _constraint_geometry(grouped, site, progress)
        allowed = site.difference(forbidden).buffer(0) if site is not None else None
        # Общая область показывает наиболее строгие отступы, а конкретную посадку
        # проверяет её собственное правило. Раздельные фрагменты видны на карте,
        # но итоговые площади относятся к целому результату.
        allowed_features = (
            []
            if allowed is None or allowed.is_empty
            else _fragment_features(
                allowed,
                feature_id="allowed-area",
                properties={
                    "kind": "allowed",
                    "label": message("geometry.allowed_area"),
                    "scope": "conservative_all_plant_kinds",
                },
            )
        )
        _report(
            progress,
            message("geometry.progress.work_areas"),
            WORK_AREAS_PROGRESS_START,
            0,
            unit=message("geometry.progress.area_unit"),
        )
        planting_zone_features = [
            {
                "type": "Feature",
                "id": f"planting-area-{zone.id}",
                "properties": {
                    "kind": "planting_area",
                    "planting_zone_id": zone.id,
                    "label": zone.label,
                },
                "geometry": deepcopy(zone.geometry),
            }
            for zone in project.planting_zones
        ]
        _report(
            progress,
            stage=message(
                "geometry.progress.work_areas_ready",
                {"count": len(planting_zone_features)},
            ),
            fraction=CALCULATION_PROGRESS_COMPLETE,
            processed=len(planting_zone_features),
            total=len(planting_zone_features),
            unit=message("geometry.progress.area_unit"),
        )
        derived_features = [
            *([site_surface_feature] if site_surface_feature is not None else []),
            *constraint_features,
            *allowed_features,
            *planting_zone_features,
        ]
        return GeometrySnapshot(
            calculation_scope="available_data"
            if (
                incomplete_physical_layers
                or (
                    project.source_file
                    and project.source_file.cad_snapshot_provenance
                    and project.source_file.cad_snapshot_provenance.unresolved_instances
                )
                or (review and (review.skipped_drawings or review.skipped_references))
            )
            else "complete_source",
            feature_collection={
                "type": "FeatureCollection",
                "features": [*visible_features, *derived_features],
            },
            # Расчёт остаётся двумерным; исходные высоты сохраняем для сцены.
            vertical_primitives=deepcopy(project.source_geometry.vertical_primitives),
            site_area_m2=round(full_site.area, 2) if full_site is not None else None,
            planning_area_m2=round(site.area, 2) if site is not None else None,
            allowed_area_m2=round(allowed.area, 2) if allowed is not None else None,
        )

    def validate_position(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> None:
        if project.geometry is None:
            raise DomainFailure(FailureReason.GEOMETRY_CALCULATION_REQUIRED)
        violation = self._position_checker(project).check(
            x, y, radius, cast(Literal["tree", "shrub"], plant_kind)
        )
        if violation:
            raise PositionRejected(violation)

    def position_violation(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> PositionViolation | None:
        return self._position_checker(project).check(
            x, y, radius, cast(Literal["tree", "shrub"], plant_kind)
        )

    def automatic_safe_geometry(
        self,
        project: Project,
        geometry: dict,
        radius: float,
        plant_kind: str = "tree",
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> dict:
        safe = self._position_checker(project).automatic_safe_area(
            shape(geometry),
            radius,
            cast(Literal["tree", "shrub"], plant_kind),
            growth_canopy_radius,
            growth_root_radius,
        )
        return mapping(safe)

    def placement_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str | None:
        advisory = self._position_checker(project).advisory(x, y, radius)
        return advisory.description if advisory else None

    def placement_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None:
        return self._position_checker(project).advisory(x, y, radius)

    def position_rule_trace(
        self,
        project: Project,
        x: float,
        y: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
        mature_crown_diameter_m: float | None = None,
    ) -> PlantingRuleTrace:
        return position_rule_trace(
            self._position_checker(project),
            project,
            x,
            y,
            plant_kind,
            mature_crown_diameter_m,
        )

    def future_growth_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str | None:
        advisory = self._position_checker(project).growth_advisory(
            x, y, canopy_radius, root_radius
        )
        return advisory.description if advisory else None

    def future_growth_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None:
        return self._position_checker(project).growth_advisory(
            x, y, canopy_radius, root_radius
        )
