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


def _natural_candidates(geometry, spacing: float, target: int, seed: int) -> list[PatternCandidate]:
    """Deterministic blue-noise-like points without an underlying grid.

    A low-discrepancy sequence covers the whole extent while a spatial hash
    enforces the requested minimum distance. This avoids the visible square
    lattice and remains bounded for a 5k preview.
    """
    min_x, min_y, max_x, max_y = geometry.bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        return []
    cell = max(spacing, 1e-6)
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    accepted: list[PatternCandidate] = []
    offset = seed % 1_000_003
    # The R2 sequence uses two irrational increments. Unlike a rectangular
    # lattice (or dyadic coordinates scaled to a round CAD extent), it does
    # not repeat a visible set of X/Y rails.
    plastic = 1.324717957244746
    alpha_x = 1 / plastic
    alpha_y = 1 / (plastic * plastic)
    max_attempts = max(2_000, target * 160)
    for attempt in range(1, max_attempts + 1):
        index = attempt + offset
        x = min_x + ((0.5 + index * alpha_x) % 1) * width
        y = min_y + ((0.5 + index * alpha_y) % 1) * height
        point = Point(x, y)
        if not geometry.covers(point):
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
            continue
        buckets.setdefault(bucket, []).append((x, y))
        accepted.append(PatternCandidate(round(x, 6), round(y, 6)))
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
        if polygon.is_empty or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
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
        return _natural_candidates(usable_geometry, request.spacing_m, target, request.seed)

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

    min_x, min_y, max_x, max_y = target.bounds
    first_x = floor(min_x / request.spacing_m) * request.spacing_m
    first_y = floor(min_y / request.spacing_m) * request.spacing_m
    threshold = {"sparse": 0.42, "balanced": 0.7, "dense": 1.0}[request.density]
    candidates: list[PatternCandidate] = []
    row = 0
    y = first_y
    while y <= max_y + 1e-9:
        col = 0
        x = first_x + (request.spacing_m / 2 if row % 2 else 0)
        while x <= max_x + 1e-9:
            point = Point(x, y)
            random = Random(f"brush:{request.seed}:{row}:{col}")
            if target.covers(point) and random.random() <= threshold:
                if request.composition == "trees":
                    kind = "tree"
                elif request.composition == "shrubs":
                    kind = "shrub"
                else:
                    kind = "tree" if random.random() < request.tree_share else "shrub"
                candidates.append(PatternCandidate(round(x, 6), round(y, 6), kind))
                if len(candidates) >= request.max_sites:
                    return candidates
            x += request.spacing_m
            col += 1
        y += request.spacing_m
        row += 1
    return candidates


class ShapelyCandidateGenerator:
    def generate(self, request: RowPatternRequest | FillPatternRequest | BrushPreviewRequest, zones: list[PlantingZoneAssignment]) -> list[PatternCandidate]:
        if isinstance(request, BrushPreviewRequest):
            return generate_brush(request, zones)
        return generate_row(request) if request.type == "row" else generate_fill(request, zones)
