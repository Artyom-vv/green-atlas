from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Literal

from shapely.geometry import GeometryCollection, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from app.dxf_import.layer_contracts import LayerKind
from app.dxf_import.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.geometry.contracts import GeometrySnapshot
from app.geometry.domain import CONSTRAINT_KINDS, PositionChecker, envelope_distance
from app.geometry.rule_trace import position_rule_trace
from app.operations.progress import ProgressReporter, WorkProgress
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace

RULES = {
    LayerKind.BUILDING: "building",
    LayerKind.ROAD: "road",
}

# These entity classes may describe a real building, retaining wall or network
# volume, but the reader cannot derive an auditable 2D footprint from them.
# Treating an absent footprint as empty ground would be more dangerous than
# stopping the calculation. The operator can still explicitly map the layer to
# ``ignore`` when it is known to be drafting-only context.
UNPROJECTABLE_PHYSICAL_ENTITY_TYPES = frozenset({
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
})
PHYSICAL_LAYER_KINDS = frozenset({
    LayerKind.SITE_BORDER,
    LayerKind.BUILDING,
    LayerKind.ROAD,
    LayerKind.UTILITY,
    LayerKind.EXISTING_GREEN,
    LayerKind.WATER,
    LayerKind.RESTRICTED,
})


def _unprojectable_physical_layers(project: Project, source_features: list[dict]) -> dict[str, set[str]]:
    """Return mapped physical layers whose real geometry is not available.

    ``Layer.entity_types`` covers entities that have no map feature at all.
    Context-only MESH/POLYLINE/PROXY features need a second inspection because
    they are deliberately kept as a visual anchor rather than a constraint.
    """
    issues: dict[str, set[str]] = {}
    mapped_layers = {
        layer.source_name: layer
        for layer in project.layers
        if layer.mapped_kind in PHYSICAL_LAYER_KINDS
    }
    for source_name, layer in mapped_layers.items():
        unsupported = set(layer.entity_types).intersection(UNPROJECTABLE_PHYSICAL_ENTITY_TYPES)
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
        if source_name not in mapped_layers or not properties.get("source_context_only"):
            continue
        entity_type = str(properties.get("entity_type", ""))
        if entity_type == "ACAD_PROXY_ENTITY" or properties.get("source_mesh_context"):
            issues.setdefault(source_name, set()).add(entity_type or "MESH")
    return issues

def _polygons(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        return [polygon for part in geometry.geoms for polygon in _polygons(part)]
    return []


def _stable_polygon_parts(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Return disconnected map targets instead of one aggregate MultiPolygon.

    Constraint math still uses the complete geometry. Only the published map
    representation is split, so hovering one local setback cannot highlight
    every disconnected object covered by the same regulation.
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
    batch_size: int = 320,
) -> BaseGeometry:
    """Union large DXF layers in bounded batches instead of one opaque call.

    Large municipal DXFs often contain thousands of small line segments. A
    single unary_union can monopolize the worker long enough to look frozen.
    Batched unions both reduce peak intermediate complexity and expose honest
    progress between completed batches. The final union is still exact.
    """
    if not geometries:
        return GeometryCollection()
    if len(geometries) <= batch_size:
        if progress:
            progress(WorkProgress(
                stage=stage,
                # GEOS does not expose progress inside a single union. Mark
                # the stage as indeterminate rather than advancing a bar to
                # an invented percentage and leaving it frozen there.
                fraction=None,
                processed=None,
                total=None,
                unit=None,
            ))
        return unary_union(geometries)

    partials: list[BaseGeometry] = []
    total = len(geometries)
    for offset in range(0, total, batch_size):
        partials.append(unary_union(geometries[offset:offset + batch_size]))
        processed = min(total, offset + batch_size)
        if progress:
            progress(WorkProgress(
                stage=stage,
                fraction=fraction_start + (fraction_end - fraction_start) * processed / total,
                processed=processed,
                total=total,
                unit="геометрий",
            ))
    if progress:
        progress(WorkProgress(
            stage=f"Сводим контуры: {stage.removeprefix('Объединяем объекты: ')}",
            # The final union has no truthful sub-counter. Its inputs are the
            # completed batches above, so keep the bar indeterminate until
            # GEOS returns instead of implying that all geometry is ready.
            fraction=None,
            processed=None,
            total=None,
            unit=None,
        ))
    return unary_union(partials)


class ShapelyGeometryEngine:
    """Builds rule buffers and allowed planting areas from imported geometry."""

    def __init__(self, max_cached_projects: int = 32) -> None:
        self.max_cached_projects = max_cached_projects
        self._position_checkers: OrderedDict[str, tuple[tuple, PositionChecker]] = OrderedDict()
        self._checker_lock = RLock()

    def _position_checker(self, project: Project) -> PositionChecker:
        # Zone edits affect selected-area and growth checks even when the DXF
        # geometry revision is unchanged. Never reuse an older zone boundary.
        version = (project.geometry_version if project.geometry else 0,
                   tuple(zone.model_dump_json() for zone in project.planting_zones))
        with self._checker_lock:
            cached = self._position_checkers.get(project.id)
            if cached is not None and cached[0] == version:
                self._position_checkers.move_to_end(project.id)
                return cached[1]
            checker = PositionChecker(project)
            self._position_checkers[project.id] = (version, checker)
            self._position_checkers.move_to_end(project.id)
            while len(self._position_checkers) > self.max_cached_projects:
                self._position_checkers.popitem(last=False)
            return checker

    def calculate(self, project: Project, progress: ProgressReporter | None = None) -> GeometrySnapshot:
        if project.source_geometry is None:
            raise ValueError("Сначала импортируйте DXF")
        mapping_by_layer = {layer.source_name: layer.mapped_kind for layer in project.layers}
        utility_by_layer = utility_contexts_by_layer(project.layers)
        source_features = deepcopy(project.source_geometry.feature_collection.get("features", []))
        unprojectable_layers = _unprojectable_physical_layers(project, source_features)
        if unprojectable_layers:
            labels = ", ".join(
                f"{source_name} ({', '.join(sorted(entity_types))})"
                for source_name, entity_types in sorted(unprojectable_layers.items())
            )
            raise ValueError(
                "DXF содержит CAD-объекты без проверяемого плоского контура в слоях: "
                f"{labels}. Нельзя строить ограничения, иначе занятая территория могла бы выглядеть свободной. "
                "Подготовьте 2D-контур или явно сопоставьте слой как неиспользуемый; исходный DXF сохранён без изменений."
            )
        incomplete_physical_layers = sorted(
            layer.source_name
            for layer in project.layers
            if layer.mapped_kind in PHYSICAL_LAYER_KINDS and not layer.geometry_complete
        )
        if incomplete_physical_layers:
            raise ValueError(
                "Карта содержит только часть объектов в слоях: "
                f"{', '.join(incomplete_physical_layers)}. Нельзя строить ограничения, иначе занятая "
                "территория могла бы выглядеть свободной. Загрузите рабочий фрагмент или явно "
                "сопоставьте неполный слой как неиспользуемый; исходный DXF сохранён без изменений."
            )
        blocking_arrays = [
            feature.get("properties", {})
            for feature in source_features
            if feature.get("properties", {}).get("source_analysis_blocking")
        ]
        if blocking_arrays:
            total = sum(int(item.get("source_block_components", item.get("source_block_instances", 0)) or 0) for item in blocking_arrays)
            raise ValueError(
                "DXF содержит блоковый массив минимум из "
                f"{total} компонентов, который нельзя безопасно развернуть для расчёта. "
                "Уменьшите массив или загрузите рабочий фрагмент; исходный DXF можно просматривать и экспортировать без изменений."
            )
        grouped: dict[LayerKind, list] = {kind: [] for kind in LayerKind}
        visible_features: list[dict] = []
        invalid_constraint_layers: set[str] = set()

        total_features = len(source_features)
        report_step = max(1, total_features // 100)
        if progress:
            progress(WorkProgress(stage="Читаем геометрию слоёв", fraction=0.0, processed=0, total=total_features, unit="объектов"))
        for index, feature in enumerate(source_features, start=1):
            properties = feature.setdefault("properties", {})
            source_layer = properties.get("source_layer", "")
            if properties.get("source_context_only"):
                # A clipped raster/PDF/DWF/DGN frame describes the extent of
                # an external document, not a physical obstacle or site
                # boundary. Preserve it for map context but never turn it
                # into a planting rule because its source pixels are absent.
                properties["kind"] = LayerKind.IGNORE.value
                assign_utility_context(properties, utility_by_layer)
                visible_features.append(feature)
                if progress and (index % report_step == 0 or index == total_features):
                    progress(WorkProgress(stage="Читаем геометрию слоёв", fraction=0.35 * index / max(1, total_features), processed=index, total=total_features, unit="объектов"))
                continue
            kind = mapping_by_layer.get(source_layer) or LayerKind.IGNORE
            properties["kind"] = kind.value
            # Explicit mapping owns network interpretation. Imported names or
            # old derived feature properties cannot confirm a network type.
            assign_utility_context(properties, utility_by_layer)
            if kind == LayerKind.IGNORE:
                visible_features.append(feature)
                if progress and (index % report_step == 0 or index == total_features):
                    progress(WorkProgress(stage="Читаем геометрию слоёв", fraction=0.35 * index / max(1, total_features), processed=index, total=total_features, unit="объектов"))
                continue
            try:
                geometry = shape(feature["geometry"])
            except Exception:
                # A malformed physical object is not evidence of empty ground.
                # Keep the source untouched and refuse an authoritative result;
                # the existing source-editor remains available for correction.
                invalid_constraint_layers.add(str(source_layer))
                properties["source_invalid_geometry"] = True
                visible_features.append(feature)
                continue
            if geometry.is_empty or not geometry.is_valid:
                # Repairing a self-intersecting boundary would invent a
                # footprint. For any mapped physical geometry that is
                # less safe than stopping the calculation: an operator must
                # decide which intended contour is authoritative.
                if kind in PHYSICAL_LAYER_KINDS:
                    invalid_constraint_layers.add(str(source_layer))
                    properties["source_invalid_geometry"] = True
                visible_features.append(feature)
                if progress and (index % report_step == 0 or index == total_features):
                    progress(WorkProgress(stage="Читаем геометрию слоёв", fraction=0.35 * index / max(1, total_features), processed=index, total=total_features, unit="объектов"))
                continue
            if not geometry.is_empty:
                calculation_geometry = geometry
                if kind == LayerKind.SITE_BORDER:
                    center_geometry = properties.get("source_polyline_center_geometry")
                    if isinstance(center_geometry, dict):
                        try:
                            authored_boundary = shape(center_geometry)
                        except (KeyError, TypeError, ValueError):
                            authored_boundary = GeometryCollection()
                        if authored_boundary.is_empty or not authored_boundary.is_valid:
                            invalid_constraint_layers.add(str(source_layer))
                            properties["source_invalid_boundary_geometry"] = True
                            visible_features.append(feature)
                            continue
                        calculation_geometry = authored_boundary
                grouped[kind].append(calculation_geometry)
                visible_features.append(feature)
            if progress and (index % report_step == 0 or index == total_features):
                progress(WorkProgress(stage="Читаем геометрию слоёв", fraction=0.35 * index / max(1, total_features), processed=index, total=total_features, unit="объектов"))

        if invalid_constraint_layers:
            layers = ", ".join(sorted(invalid_constraint_layers))
            raise ValueError(
                "DXF содержит отсутствующую, пустую или некорректную геометрию "
                f"в слоях: {layers}. Исправьте контур или сопоставьте его как справочный слой."
            )

        site_candidates = grouped[LayerKind.SITE_BORDER]
        if progress:
            progress(WorkProgress(stage="Собираем границу проектирования", fraction=None, processed=len(site_candidates), total=len(site_candidates), unit="контуров"))
        site_polygons = [polygon for geometry in site_candidates for polygon in _polygons(geometry)]
        if not site_polygons:
            site_polygons = list(polygonize([geometry for geometry in site_candidates if geometry.geom_type in {"LineString", "MultiLineString"}]))
        # A survey fragment often carries buildings, paths and utilities but
        # not the outer project boundary. That is not a reason to make the
        # whole drawing unusable: the operator can draw the working area on
        # the map. In this mode we deliberately do *not* invent a site-wide
        # allowed polygon. Verified setbacks are still rendered and checked
        # inside the explicitly selected manual areas.
        full_site = unary_union(site_polygons).buffer(0) if site_polygons else None
        site = full_site
        site_surface_feature = None if site is None or site.is_empty else {
            "type": "Feature",
            "id": "calculated-site-surface",
            "properties": {
                "kind": "site_surface",
                "label": "Расчётная граница территории",
                "area_m2": round(site.area, 2),
                "calculation_only": True,
            },
            "geometry": mapping(site),
        }
        if progress:
            progress(WorkProgress(
                stage="Граница проектирования готова" if site is not None else "Граница не найдена: доступна ручная область",
                fraction=0.42,
                processed=len(site_candidates),
                total=len(site_candidates),
                unit="контуров",
            ))

        forbidden_parts = []
        constraint_features: list[dict] = []
        relevant_constraints: dict[LayerKind, list[BaseGeometry]] = {}
        rule_distances: dict[LayerKind, float] = {}
        for kind, registry_kind in RULES.items():
            distance = envelope_distance(registry_kind)
            rule_distances[kind] = distance
            vicinity = site.buffer(distance) if site is not None else None
            relevant_constraints[kind] = [
                geometry
                for geometry in grouped[kind]
                if vicinity is None or geometry.intersects(vicinity)
            ]
        total_constraints = sum(len(items) for items in relevant_constraints.values())
        processed_constraints = 0
        rule_count = len(RULES)
        for rule_index, (kind, registry_kind) in enumerate(RULES.items()):
            geometries = relevant_constraints[kind]
            if not geometries:
                continue
            rule_id, label, _distances = CONSTRAINT_KINDS[registry_kind]
            distance = rule_distances[kind]
            rule_fraction_start = 0.42 + 0.40 * rule_index / rule_count
            rule_fraction_end = 0.42 + 0.40 * (rule_index + 1) / rule_count
            if progress:
                progress(WorkProgress(stage=f"Строим зону: {label.lower()}", fraction=None, processed=processed_constraints, total=total_constraints, unit="объектов ограничений"))
            unioned = _union_in_batches(
                geometries,
                progress=progress,
                stage=f"Объединяем объекты: {label.lower()}",
                fraction_start=rule_fraction_start,
                fraction_end=rule_fraction_start + (rule_fraction_end - rule_fraction_start) * 0.72,
            )
            buffered = unioned.buffer(distance)
            clipped = buffered.intersection(site) if site is not None else buffered
            processed_constraints += len(geometries)
            if progress:
                fraction = max(rule_fraction_end, 0.42 + 0.40 * processed_constraints / max(1, total_constraints))
                progress(WorkProgress(stage=f"Зона готова: {label.lower()}", fraction=fraction, processed=processed_constraints, total=total_constraints, unit="объектов ограничений"))
            if clipped.is_empty:
                continue
            forbidden_parts.append(clipped)
            constraint_features.extend(
                _fragment_features(
                    clipped,
                    feature_id=f"forbidden-{rule_id}",
                    properties={
                        "kind": "forbidden",
                        "rule_id": rule_id,
                        "label": label,
                        "distance_m": distance,
                    },
                )
            )

        occupied_labels = {
            LayerKind.EXISTING_GREEN: "Существующее озеленение",
            LayerKind.WATER: "Водный объект",
            LayerKind.RESTRICTED: "Техническая или непригодная зона",
        }
        for kind, label in occupied_labels.items():
            geometries = grouped[kind]
            if not geometries:
                continue
            occupied = _union_in_batches(
                geometries,
                progress=progress,
                stage=f"Собираем занятые контуры: {label.lower()}",
                fraction_start=0.80,
                fraction_end=0.84,
            )
            clipped = occupied.intersection(site) if site is not None else occupied
            if clipped.is_empty:
                continue
            forbidden_parts.append(clipped)
            constraint_features.extend(
                _fragment_features(
                    clipped,
                    feature_id=f"occupied-{kind.value}",
                    properties={
                        "kind": "forbidden",
                        "rule_id": f"occupied-{kind.value}",
                        "label": f"Занято: {label.lower()}",
                        "distance_m": 0,
                    },
                )
            )

        if progress:
            progress(WorkProgress(stage="Вычисляем итоговую допустимую область", fraction=None, processed=processed_constraints, total=total_constraints, unit="объектов ограничений"))
        forbidden = unary_union(forbidden_parts) if forbidden_parts else GeometryCollection()
        allowed = site.difference(forbidden).buffer(0) if site is not None else None
        # This is the intersection left after the strictest currently known
        # buffer for every plant class. It is a useful common map cue, not a
        # second universal validation rule: a shrub may be valid outside it
        # when its own regulatory distance is smaller. Publish disconnected
        # components independently so the map exposes local areas, while the
        # snapshot totals below continue to describe the complete result.
        allowed_features = (
            []
            if allowed is None or allowed.is_empty
            else _fragment_features(
                allowed,
                feature_id="allowed-area",
                properties={
                    "kind": "allowed",
                    "label": "Базово допустимая зона",
                    "scope": "conservative_all_plant_kinds",
                },
            )
        )
        if progress:
            progress(WorkProgress(stage="Фиксируем выбранные рабочие области", fraction=0.92, processed=0, total=None, unit="областей"))
        planting_zone_features = [{
            "type": "Feature",
            "id": f"planting-area-{zone.id}",
            "properties": {
                "kind": "planting_area",
                "planting_zone_id": zone.id,
                "label": zone.label,
            },
            "geometry": deepcopy(zone.geometry),
        } for zone in project.planting_zones]
        if progress:
            progress(WorkProgress(stage=f"Выбрано рабочих областей: {len(planting_zone_features)}", fraction=1.0, processed=len(planting_zone_features), total=len(planting_zone_features), unit="областей"))
        derived_features = [
            *([site_surface_feature] if site_surface_feature is not None else []),
            *constraint_features,
            *allowed_features,
            *planting_zone_features,
        ]
        return GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": [*visible_features, *derived_features]},
            # Regulatory calculation remains strictly 2D. Carry the source
            # XYZ evidence through unchanged for the scene instead of
            # flattening or attempting to derive terrain from it.
            vertical_primitives=deepcopy(project.source_geometry.vertical_primitives),
            site_area_m2=round(full_site.area, 2) if full_site is not None else None,
            planning_area_m2=round(site.area, 2) if site is not None else None,
            allowed_area_m2=round(allowed.area, 2) if allowed is not None else None,
        )

    def validate_position(self, project: Project, x: float, y: float, radius: float, plant_kind: str = "tree") -> None:
        if project.geometry is None:
            raise ValueError("Сначала рассчитайте допустимые зоны")
        violation = self._position_checker(project).check(x, y, radius, plant_kind)  # type: ignore[arg-type]
        if violation:
            raise ValueError(violation.description)

    def position_violation(self, project: Project, x: float, y: float, radius: float, plant_kind: str = "tree"):
        return self._position_checker(project).check(x, y, radius, plant_kind)  # type: ignore[arg-type]

    def automatic_safe_geometry(
        self,
        project: Project,
        geometry: dict,
        radius: float,
        plant_kind: str = "tree",
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> dict:
        safe = self._position_checker(project).automatic_safe_area(  # type: ignore[arg-type]
            shape(geometry),
            radius,
            plant_kind,
            growth_canopy_radius,
            growth_root_radius,
        )
        return mapping(safe)

    def placement_advisory(self, project: Project, x: float, y: float, radius: float) -> str | None:
        advisory = self._position_checker(project).advisory(x, y, radius)
        return advisory.description if advisory else None

    def placement_advisory_detail(self, project: Project, x: float, y: float, radius: float):
        return self._position_checker(project).advisory(x, y, radius)

    def position_rule_trace(self, project: Project, x: float, y: float, plant_kind: Literal["tree", "shrub"] = "tree", mature_crown_diameter_m: float | None = None) -> PlantingRuleTrace:
        return position_rule_trace(self._position_checker(project), project, x, y, plant_kind, mature_crown_diameter_m)

    def future_growth_advisory(self, project: Project, x: float, y: float, canopy_radius: float, root_radius: float) -> str | None:
        advisory = self._position_checker(project).growth_advisory(x, y, canopy_radius, root_radius)
        return advisory.description if advisory else None

    def future_growth_advisory_detail(self, project: Project, x: float, y: float, canopy_radius: float, root_radius: float):
        return self._position_checker(project).growth_advisory(x, y, canopy_radius, root_radius)
