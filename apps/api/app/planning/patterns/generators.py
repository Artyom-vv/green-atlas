"""Геометрическая генерация кандидатов рядов, заполнений, масок и кисти без
самостоятельного допуска посадок.
"""

from __future__ import annotations

from itertools import chain, zip_longest
from math import ceil, floor, hypot, pi, sqrt
from random import Random

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.patterns.building_patterns import (
    generate_building_contour,
    generate_building_screen,
)
from app.planning.patterns.contracts import (
    BrushPreviewRequest,
    FillPatternRequest,
    PatternPreviewRequest,
    PlacementMaskRequest,
    RowPatternRequest,
)
from app.planning.ports import CandidateGeneratorPort, PatternCandidate
from app.planning.search.allocation import equal_zone_targets
from app.planning.search.regular_grid import RegularFillGrid
from app.planning.search.sampling import bounded_candidates as _bounded
from app.planning.search.sampling import cluster_offsets as _cluster_offsets
from app.planning.search.sampling import poisson_candidates as _poisson_candidates
from app.planting_zones.contracts import PlantingZoneAssignment
from app.shared.failures import DomainFailure, FailureReason


def generate_pattern_candidates(
    generator: CandidateGeneratorPort,
    request: PatternPreviewRequest,
    zones: list[PlantingZoneAssignment],
    zone_targets: dict[str, int],
    guide_geometries: list[dict] | None = None,
    *,
    alternatives: bool = False,
) -> list[PatternCandidate]:
    """Генерирует квоты отдельно; свободный поиск чередует зоны, остальные режимы сохраняют группы."""
    if zone_targets:
        # Каждая квота получает свой резерв; генерация общего объединения
        # может исчерпать бюджет до маленьких или удалённых зон.
        budget = request.target_count
        remaining_extra = budget - sum(zone_targets.values())
        extra_targets = equal_zone_targets(
            [zone_id for zone_id, target in zone_targets.items() if target],
            remaining_extra,
        )
        zone_candidates: list[list[PatternCandidate]] = []
        for zone in zones:
            target = zone_targets[zone.id]
            if not target:
                continue
            local = request.model_copy(
                update={
                    "zone_ids": [zone.id],
                    "target_count": target + extra_targets.get(zone.id, 0),
                }
            )
            points = (
                generator.generate(local, [zone], guide_geometries)
                if isinstance(request, PlacementMaskRequest)
                else generator.generate(local, [zone], alternatives=alternatives)
            )
            zone_candidates.append(points)
        generated_candidates = (
            [
                point
                for row in zip_longest(*zone_candidates)
                for point in row
                if point is not None
            ]
            if alternatives
            else list(chain.from_iterable(zone_candidates))
        )
    else:
        generated_candidates = (
            generator.generate(request, zones, guide_geometries)
            if isinstance(request, PlacementMaskRequest)
            else generator.generate(request, zones, alternatives=alternatives)
        )
    return generated_candidates


def generate_row(request: RowPatternRequest) -> list[PatternCandidate]:
    axis = shape(request.axis)
    if (
        not isinstance(axis, LineString)
        or axis.is_empty
        or len(axis.coords) < 2
        or axis.length <= 0
    ):
        raise DomainFailure(FailureReason.ROW_AXIS_INVALID)
    usable_length = axis.length - request.start_offset_m - request.end_offset_m
    if usable_length < 0:
        raise DomainFailure(FailureReason.ROW_OFFSETS_EXCEED_AXIS)

    distances: list[float] = []
    last = axis.length - request.end_offset_m
    if request.placement_mode == "count":
        # Количество задаётся суммарно для обеих сторон, а не для каждой с последующим
        # усечением до половины линии.
        stations = (
            (request.target_count + 1) // 2
            if request.side == "both"
            else request.target_count
        )
        if stations == 1:
            distances = [request.start_offset_m + usable_length / 2]
        else:
            step = usable_length / (stations - 1)
            distances = [
                request.start_offset_m + step * index for index in range(stations)
            ]
    else:
        distance = request.start_offset_m
        while distance <= last + 1e-9:
            distances.append(distance)
            distance += request.spacing_m

    sides = {
        "center": (0,),
        "left": (1,),
        "right": (-1,),
        "both": (1, -1),
    }[request.side]
    candidates: list[PatternCandidate] = []
    for distance in distances:
        center = axis.interpolate(distance)
        before = axis.interpolate(max(0, distance - min(0.05, axis.length / 100)))
        after = axis.interpolate(
            min(axis.length, distance + min(0.05, axis.length / 100))
        )
        dx, dy = after.x - before.x, after.y - before.y
        length = hypot(dx, dy)
        if length <= 1e-12:
            continue
        normal_x, normal_y = -dy / length, dx / length
        for side in sides:
            if (
                request.placement_mode == "count"
                and len(candidates) >= request.target_count
            ):
                break
            candidates.append(
                PatternCandidate(
                    x=round(center.x + normal_x * request.lateral_offset_m * side, 6),
                    y=round(center.y + normal_y * request.lateral_offset_m * side, 6),
                )
            )
    return _bounded(candidates)


def usable_zone_geometry(
    zone: PlantingZoneAssignment, edge_offset_m: float
) -> BaseGeometry:
    """Возвращает область центров с отступом только от рабочей границы.

    Уже проверенные отступы от препятствий сохраняются; вызывающий код может
    подготовить область до генерации, применив рабочий отступ ровно один раз.
    """
    polygon = shape(zone.geometry)
    if polygon.is_empty:
        return polygon
    if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
        raise DomainFailure(FailureReason.ZONE_NOT_CLOSED, {"zone_label": zone.label})
    if not edge_offset_m:
        return polygon
    work = zone.geometry.get("ga_work_zone")
    # Native-область уже учитывает нормативы, прогноз и точность CAD-контура.
    # Дополнительный отступ относится к рабочему участку, а не к каждому
    # отверстию, фрагменту или сертифицированной ячейке расчётной области.
    return (
        polygon.intersection(shape(work).buffer(-edge_offset_m))
        if work
        else polygon.buffer(-edge_offset_m)
    )


def generate_fill(
    request: FillPatternRequest,
    zones: list[PlantingZoneAssignment],
    *,
    alternatives: bool = False,
) -> list[PatternCandidate]:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise DomainFailure(FailureReason.ZONE_MISSING, {"zone_ids": sorted(missing)})

    usable_parts = []
    for zone in selected:
        usable = usable_zone_geometry(zone, request.edge_offset_m)
        if usable.is_empty:
            continue
        usable_parts.append(usable)

    if not usable_parts:
        return []
    usable_geometry = unary_union(usable_parts)

    if request.layout == "natural":
        target = (
            request.target_count
            if request.placement_mode == "count"
            else MAX_PATTERN_CANDIDATES
        )
        points = _poisson_candidates(
            usable_geometry,
            request.spacing_m,
            target,
            request.seed,
            alternatives=alternatives,
        )
        # Очередь управляет пространственным покрытием и CAD-порциями. Сведение
        # всех проб к одному окну 32 м оставляло далёкие фрагменты без кандидатов.
        return points

    grid = RegularFillGrid(usable_geometry, request.angle_deg, request.layout)

    if request.placement_mode == "count":
        return grid.count_candidates(request.spacing_m, request.target_count)

    candidates = grid.candidates(request.spacing_m)
    return _bounded(candidates)


def _mask_geometry(
    request: PlacementMaskRequest, zones: list[PlantingZoneAssignment]
) -> BaseGeometry:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise DomainFailure(FailureReason.ZONE_MISSING, {"zone_ids": sorted(missing)})
    parts: list[BaseGeometry] = []
    for zone in selected:
        usable = usable_zone_geometry(zone, request.edge_offset_m)
        if not usable.is_empty:
            parts.append(usable)
    return unary_union(parts) if parts else Point().buffer(0)


def _generate_cluster_mask(
    request: PlacementMaskRequest, geometry: BaseGeometry
) -> list[PatternCandidate]:
    target = (
        request.target_count
        if request.placement_mode == "count"
        else MAX_PATTERN_CANDIDATES
    )
    if target <= 0 or geometry.is_empty:
        return []
    grove_count = max(1, ceil(target / request.cluster_size))
    outer_ring = max(1, ceil((request.cluster_size - 1) / 6))
    centre_spacing = max(
        request.cluster_gap_m, request.spacing_m * (2 * outer_ring + 1)
    )
    centres = _poisson_candidates(geometry, centre_spacing, grove_count, request.seed)
    candidates: list[PatternCandidate] = []
    seen: set[tuple[float, float]] = set()
    for grove_index, centre in enumerate(centres):
        random = Random(f"grove:{request.seed}:{grove_index}:{centre.x}:{centre.y}")
        for offset_x, offset_y in _cluster_offsets(
            request.spacing_m, request.cluster_size, random.random() * 2 * pi
        ):
            key = (round(centre.x + offset_x, 6), round(centre.y + offset_y, 6))
            if key in seen or not geometry.covers(Point(*key)):
                continue
            seen.add(key)
            candidates.append(PatternCandidate(*key, group_key=f"grove-{grove_index}"))
            if len(candidates) >= target:
                return candidates
    return _bounded(candidates)


def _line_parts(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return _line_parts(geometry.boundary)
    parts: list[LineString] = []
    for part in getattr(geometry, "geoms", []):
        parts.extend(_line_parts(part))
    return parts


def _generate_road_edge_mask(
    request: PlacementMaskRequest,
    geometry: BaseGeometry,
    guides: list[dict],
) -> list[PatternCandidate]:
    if geometry.is_empty or not guides:
        return []
    # Выборка границы буфера дороги даёт две параллельные линии для линейного
    # исходника и следует настоящему внешнему краю полигональной проезжей части.
    lines: list[LineString] = []
    for value in guides:
        guide = shape(value)
        if guide.is_empty:
            continue
        lines.extend(_line_parts(guide.buffer(request.road_offset_m).boundary))
    sampled: list[list[tuple[float, float]]] = []
    for line in lines:
        if line.length <= 1e-9:
            continue
        positions = []
        distance = request.spacing_m / 2
        while distance <= line.length + 1e-9:
            point = line.interpolate(distance)
            if geometry.covers(point):
                positions.append((point.x, point.y))
            distance += request.spacing_m
        if positions:
            sampled.append(positions)

    # Поочерёдный обход дорог не даёт первой дороге исчерпать лимит результата
    # до появления кандидатов у остальных распознанных дорог.
    target = (
        request.target_count
        if request.placement_mode == "count"
        else MAX_PATTERN_CANDIDATES
    )
    candidates: list[PatternCandidate] = []
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    cell = max(request.spacing_m, 1e-6)
    longest = max((len(points) for points in sampled), default=0)
    for position_index in range(longest):
        for points in sampled:
            if position_index >= len(points):
                continue
            x, y = points[position_index]
            bucket = (floor(x / cell), floor(y / cell))
            if any(
                hypot(x - other_x, y - other_y) + 1e-9 < request.spacing_m
                for offset_x in (-1, 0, 1)
                for offset_y in (-1, 0, 1)
                for other_x, other_y in buckets.get(
                    (bucket[0] + offset_x, bucket[1] + offset_y), []
                )
            ):
                continue
            buckets.setdefault(bucket, []).append((x, y))
            candidates.append(PatternCandidate(round(x, 6), round(y, 6)))
            if len(candidates) >= target:
                return candidates
    return _bounded(candidates)


def generate_mask(
    request: PlacementMaskRequest,
    zones: list[PlantingZoneAssignment],
    guide_geometries: list[dict] | None = None,
) -> list[PatternCandidate]:
    """Строит кандидатов для заданного пространственного замысла до окончательной
    проверки.
    """
    geometry = _mask_geometry(request, zones)
    if request.mask_id == "building_contour":
        return generate_building_contour(request, geometry, guide_geometries or [])
    if request.mask_id == "building_screen":
        return generate_building_screen(request, geometry, guide_geometries or [])
    if request.mask_id == "regular_grid":
        return generate_fill(
            FillPatternRequest(
                base_plan_version=request.base_plan_version,
                plant_kind=request.plant_kind,
                zone_ids=request.zone_ids,
                placement_mode=request.placement_mode,
                target_count=request.target_count,
                layout="regular",
                spacing_m=request.spacing_m,
                edge_offset_m=request.edge_offset_m,
                angle_deg=request.angle_deg,
                seed=request.seed,
                layout_radius_m=request.layout_radius_m,
                size_class=request.size_class,
                species_revision_id=request.species_revision_id,
                spacing_policy=request.spacing_policy,
            ),
            zones,
        )
    if request.mask_id == "cluster_groves":
        return _generate_cluster_mask(request, geometry)
    return _generate_road_edge_mask(request, geometry, guide_geometries or [])


def generate_brush(
    request: BrushPreviewRequest, zones: list[PlantingZoneAssignment]
) -> list[PatternCandidate]:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise DomainFailure(
            FailureReason.ZONE_MISSING,
            {"zone_ids": sorted(requested_ids - {zone.id for zone in selected})},
        )
    add_corridors: list[BaseGeometry] = []
    subtract_corridors: list[BaseGeometry] = []
    for stroke in request.strokes:
        geometry = shape(stroke.geometry)
        if (
            not isinstance(geometry, LineString)
            or geometry.is_empty
            or len(geometry.coords) < 2
            or geometry.length <= 0
        ):
            raise DomainFailure(FailureReason.BRUSH_STROKE_INVALID)
        corridor = geometry.buffer(
            request.width_m / 2, cap_style="round", join_style="round"
        )
        (add_corridors if stroke.mode == "add" else subtract_corridors).append(corridor)
    if not add_corridors:
        return []
    target = unary_union(add_corridors)
    if subtract_corridors:
        target = target.difference(unary_union(subtract_corridors))
    zone_geometry = unary_union([shape(zone.geometry) for zone in selected])
    target = target.intersection(zone_geometry)
    if target.is_empty:
        return []

    threshold = {"sparse": 0.42, "balanced": 0.7, "dense": 1.0}[request.density]
    effective_spacing = request.spacing_m / sqrt(threshold)
    points = _poisson_candidates(
        target, effective_spacing, request.max_sites, request.seed
    )
    candidates: list[PatternCandidate] = []
    for index, point in enumerate(points):
        random = Random(f"brush-kind:{request.seed}:{index}:{point.x}:{point.y}")
        if request.composition == "trees":
            kind = "tree"
        elif request.composition == "shrubs":
            kind = "shrub"
        else:
            kind = "tree" if random.random() < request.tree_share else "shrub"
        candidates.append(PatternCandidate(point.x, point.y, kind))
    return candidates


class ShapelyCandidateGenerator:
    def generate(
        self,
        request: RowPatternRequest
        | FillPatternRequest
        | PlacementMaskRequest
        | BrushPreviewRequest,
        zones: list[PlantingZoneAssignment],
        guide_geometries: list[dict] | None = None,
        *,
        alternatives: bool = False,
    ) -> list[PatternCandidate]:
        if isinstance(request, BrushPreviewRequest):
            return generate_brush(request, zones)
        if isinstance(request, PlacementMaskRequest):
            return generate_mask(request, zones, guide_geometries)
        return (
            generate_row(request)
            if request.type == "row"
            else generate_fill(request, zones, alternatives=alternatives)
        )
