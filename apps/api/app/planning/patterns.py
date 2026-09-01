from __future__ import annotations

from math import floor, hypot, sqrt
from random import Random

from shapely.affinity import rotate
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from app.contracts import BrushPreviewRequest, FillPatternRequest, PlantingZoneAssignment, RowPatternRequest
from app.planning.ports import PatternCandidate


MAX_PATTERN_CANDIDATES = 5000


def _bounded(candidates: list[PatternCandidate]) -> list[PatternCandidate]:
    if len(candidates) > MAX_PATTERN_CANDIDATES:
        raise ValueError(f"Рисунок создаёт больше {MAX_PATTERN_CANDIDATES} позиций. Увеличьте шаг или уменьшите участки")
    return candidates


def _poisson_candidates(geometry, spacing: float, target: int, seed: int) -> list[PatternCandidate]:
    """Generate a deterministic random-sequential Poisson sample.

    Candidates are drawn over the whole extent instead of walking an implicit
    lattice. A spatial hash keeps the minimum-distance check local. The result
    is stable for the same geometry and seed, but it has no repeated X/Y rails
    that can be mistaken for an engineering placement grid.
    """
    min_x, min_y, max_x, max_y = geometry.bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        return []
    cell = max(spacing, 1e-6)
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    accepted: list[PatternCandidate] = []
    random = Random(f"poisson:{seed}")
    max_attempts = max(4_000, target * 120)
    attempts_without_acceptance = 0
    for _ in range(max_attempts):
        x = min_x + random.random() * width
        y = min_y + random.random() * height
        point = Point(x, y)
        if not geometry.covers(point):
            attempts_without_acceptance += 1
            continue
        bucket = (floor(x / cell), floor(y / cell))
        too_close = False
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other_x, other_y in buckets.get((bucket[0] + offset_x, bucket[1] + offset_y), []):
                    if hypot(x - other_x, y - other_y) + 1e-9 < spacing:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            attempts_without_acceptance += 1
            if accepted and attempts_without_acceptance >= max(2_000, len(accepted) * 20):
                break
            continue
        buckets.setdefault(bucket, []).append((x, y))
        accepted.append(PatternCandidate(round(x, 6), round(y, 6)))
        attempts_without_acceptance = 0
        if len(accepted) >= target:
            break
    return accepted


def generate_row(request: RowPatternRequest) -> list[PatternCandidate]:
    axis = shape(request.axis)
    if not isinstance(axis, LineString) or axis.is_empty or len(axis.coords) < 2 or axis.length <= 0:
        raise ValueError("Ось ряда должна быть линией минимум из двух точек")
    usable_length = axis.length - request.start_offset_m - request.end_offset_m
    if usable_length < 0:
        raise ValueError("Начальный и конечный отступы длиннее оси ряда")

    distances: list[float] = []
    last = axis.length - request.end_offset_m
    if request.placement_mode == "count":
        if request.target_count == 2:
            distances = [request.start_offset_m, last]
        else:
            step = usable_length / (request.target_count - 1)
            distances = [request.start_offset_m + step * index for index in range(request.target_count)]
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
        after = axis.interpolate(min(axis.length, distance + min(0.05, axis.length / 100)))
        dx, dy = after.x - before.x, after.y - before.y
        length = hypot(dx, dy)
        if length <= 1e-12:
            continue
        normal_x, normal_y = -dy / length, dx / length
        for side in sides:
            candidates.append(PatternCandidate(
                x=round(center.x + normal_x * request.lateral_offset_m * side, 6),
                y=round(center.y + normal_y * request.lateral_offset_m * side, 6),
            ))
    return _bounded(candidates)


def generate_fill(request: FillPatternRequest, zones: list[PlantingZoneAssignment]) -> list[PatternCandidate]:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise ValueError("Один из выбранных участков больше не существует")

    usable_parts = []
    for zone in selected:
        polygon = shape(zone.geometry)
        if polygon.is_empty:
            continue
        if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Участок «{zone.label}» не является замкнутой областью")
        usable = polygon.buffer(-request.edge_offset_m) if request.edge_offset_m else polygon
        if usable.is_empty:
            continue
        usable_parts.append(usable)

    if not usable_parts:
        return []
    usable_geometry = unary_union(usable_parts)

    if request.layout == "natural":
        target = request.target_count if request.placement_mode == "count" else MAX_PATTERN_CANDIDATES
        return _poisson_candidates(usable_geometry, request.spacing_m, target, request.seed)

    def candidates_at(spacing: float, limit: int | None = None) -> list[PatternCandidate]:
        candidates: list[PatternCandidate] = []
        seen: set[tuple[float, float]] = set()
        rotated = rotate(usable_geometry, -request.angle_deg, origin=(0, 0), use_radians=False)
        min_x, min_y, max_x, max_y = rotated.bounds
        first_x = floor(min_x / spacing) * spacing
        first_y = floor(min_y / spacing) * spacing
        row = 0
        row_step = spacing * sqrt(3) / 2 if request.layout == "staggered" else spacing
        y = first_y
        while y <= max_y + 1e-9:
            col = 0
            x_offset = spacing / 2 if request.layout == "staggered" and row % 2 else 0
            x = first_x + x_offset
            while x <= max_x + 1e-9:
                candidate_x, candidate_y = x, y
                point = Point(candidate_x, candidate_y)
                if rotated.covers(point):
                    restored = rotate(point, request.angle_deg, origin=(0, 0), use_radians=False)
                    key = (round(restored.x, 6), round(restored.y, 6))
                    if key not in seen:
                        seen.add(key)
                        candidates.append(PatternCandidate(*key))
                        if limit is not None and len(candidates) >= limit:
                            return candidates
                x += spacing
                col += 1
            y += row_step
            row += 1
        return candidates

    if request.placement_mode == "count":
        target = request.target_count
        minimum = request.spacing_m
        low = minimum
        high = max(minimum * 2, sqrt(max(usable_geometry.area, 1)) * 2)
        # Find the largest spacing that still yields the requested count.
        # Search requests stop as soon as the threshold is reached, so a
        # multi-hectare DXF never materialises an irrelevant full grid.
        for _ in range(16):
            middle = (low + high) / 2
            count = len(candidates_at(middle, target))
            if count >= target:
                low = middle
            else:
                high = middle
        candidates = candidates_at(low)
        if len(candidates) > target:
            if target == 1:
                return [candidates[len(candidates) // 2]]
            indices = [round(index * (len(candidates) - 1) / (target - 1)) for index in range(target)]
            return [candidates[index] for index in indices]
        return candidates

    candidates = candidates_at(request.spacing_m)
    return _bounded(candidates)


def generate_brush(request: BrushPreviewRequest, zones: list[PlantingZoneAssignment]) -> list[PatternCandidate]:
    add_corridors = []
    subtract_corridors = []
    for stroke in request.strokes:
        geometry = shape(stroke.geometry)
        if not isinstance(geometry, LineString) or geometry.is_empty or len(geometry.coords) < 2 or geometry.length <= 0:
            raise ValueError("Мазок должен быть линией минимум из двух точек")
        corridor = geometry.buffer(request.width_m / 2, cap_style="round", join_style="round")
        (add_corridors if stroke.mode == "add" else subtract_corridors).append(corridor)
    if not add_corridors:
        return []
    target = unary_union(add_corridors)
    if subtract_corridors:
        target = target.difference(unary_union(subtract_corridors))
    zone_geometry = unary_union([shape(zone.geometry) for zone in zones])
    target = target.intersection(zone_geometry)
    if target.is_empty:
        return []

    threshold = {"sparse": 0.42, "balanced": 0.7, "dense": 1.0}[request.density]
    effective_spacing = request.spacing_m / sqrt(threshold)
    points = _poisson_candidates(target, effective_spacing, request.max_sites, request.seed)
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
    def generate(self, request: RowPatternRequest | FillPatternRequest | BrushPreviewRequest, zones: list[PlantingZoneAssignment]) -> list[PatternCandidate]:
        if isinstance(request, BrushPreviewRequest):
            return generate_brush(request, zones)
        return generate_row(request) if request.type == "row" else generate_fill(request, zones)
