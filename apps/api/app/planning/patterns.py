from __future__ import annotations

from math import ceil, cos, floor, hypot, pi, sin, sqrt
from random import Random

from shapely import prepare
from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from app.planning.pattern_contracts import (
    BrushPreviewRequest,
    FillPatternRequest,
    PlacementMaskRequest,
    RowPatternRequest,
)
from app.planning.ports import PatternCandidate
from app.planning.regular_grid import RegularFillGrid
from app.planning.sampling import polygon_components, sparse_area_sampler
from app.planting_zones.contracts import PlantingZoneAssignment

MAX_PATTERN_CANDIDATES = 5000


def _bounded(candidates: list[PatternCandidate]) -> list[PatternCandidate]:
    if len(candidates) > MAX_PATTERN_CANDIDATES:
        raise ValueError(
            f"Рисунок создаёт больше {MAX_PATTERN_CANDIDATES} позиций. Увеличьте шаг или уменьшите участки"
        )
    return candidates


def _poisson_candidates(
    geometry, spacing: float, target: int, seed: int, *, alternatives: bool = False
) -> list[PatternCandidate]:
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
    if alternatives:
        # Every disconnected planting pocket gets a probe, even if its area
        # is too small to win the area-weighted random draw. These are NOT
        # reserved planting positions: only the evaluator can accept them.
        for part in sorted(polygon_components(geometry), key=lambda g: g.area, reverse=True):
            point = part.representative_point()
            accepted.append(PatternCandidate(point.x, point.y))
            if len(accepted) >= target:
                return accepted
    random = Random(f"poisson:{seed}")
    max_attempts = max(4_000, target * 120)
    sampler = sparse_area_sampler(geometry, target, max_attempts)
    # GEOS indexes repeated coverage queries without changing coordinates,
    # random draws, boundary inclusion or the spacing predicate.
    prepare(
        geometry if sampler is None else [window.geometry for window in sampler.windows]
    )
    attempts_without_acceptance = 0
    for _ in range(max_attempts):
        if sampler is None:
            component = geometry
            x = min_x + random.random() * width
            y = min_y + random.random() * height
        else:
            component, x, y = sampler.draw(random)
        point = Point(x, y)
        if not component.covers(point):
            attempts_without_acceptance += 1
            continue
        bucket = (floor(x / cell), floor(y / cell))
        too_close = False
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other_x, other_y in buckets.get(
                    (bucket[0] + offset_x, bucket[1] + offset_y), []
                ):
                    if hypot(x - other_x, y - other_y) + 1e-9 < spacing:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            attempts_without_acceptance += 1
            if accepted and attempts_without_acceptance >= max(
                2_000, len(accepted) * 20
            ):
                break
            continue
        # Alternative probes do not occupy space. Only the application can
        # reserve a position, after geometry, growth and spacing all pass.
        if not alternatives:
            buckets.setdefault(bucket, []).append((x, y))
        accepted.append(PatternCandidate(round(x, 6), round(y, 6)))
        attempts_without_acceptance = 0
        if len(accepted) >= target:
            break
    return accepted


def generate_row(request: RowPatternRequest) -> list[PatternCandidate]:
    axis = shape(request.axis)
    if (
        not isinstance(axis, LineString)
        or axis.is_empty
        or len(axis.coords) < 2
        or axis.length <= 0
    ):
        raise ValueError("Ось ряда должна быть линией минимум из двух точек")
    usable_length = axis.length - request.start_offset_m - request.end_offset_m
    if usable_length < 0:
        raise ValueError("Начальный и конечный отступы длиннее оси ряда")

    distances: list[float] = []
    last = axis.length - request.end_offset_m
    if request.placement_mode == "count":
        # Quantity is the total across both sides, not an implicit per-side
        # count later truncated by the application to half of the line.
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
        raise ValueError("Один из выбранных участков больше не существует")

    usable_parts = []
    for zone in selected:
        polygon = shape(zone.geometry)
        if polygon.is_empty:
            continue
        if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Участок «{zone.label}» не является замкнутой областью")
        work = zone.geometry.get("ga_work_zone")
        # The native domain already includes CAD setbacks. Erode only the
        # user's boundary, not every conservative cell/domain component again.
        usable = (
            polygon.intersection(shape(work).buffer(-request.edge_offset_m))
            if work and request.edge_offset_m
            else polygon.buffer(-request.edge_offset_m)
            if request.edge_offset_m
            else polygon
        )
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
        # The online queue controls spatial coverage and CAD batches. Flattening
        # all probes in one 32 m window first used to starve distant pockets.
        return points

    grid = RegularFillGrid(usable_geometry, request.angle_deg, request.layout)

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
            count = grid.count(middle, target)
            if count >= target:
                low = middle
            else:
                high = middle
        candidates = grid.candidates(low)
        if len(candidates) > target:
            if target == 1:
                return [candidates[len(candidates) // 2]]
            indices = [
                round(index * (len(candidates) - 1) / (target - 1))
                for index in range(target)
            ]
            return [candidates[index] for index in indices]
        return candidates

    candidates = grid.candidates(request.spacing_m)
    return _bounded(candidates)


def _mask_geometry(
    request: PlacementMaskRequest, zones: list[PlantingZoneAssignment]
) -> BaseGeometry:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise ValueError("Один из выбранных участков больше не существует")
    parts: list[BaseGeometry] = []
    for zone in selected:
        polygon = shape(zone.geometry)
        if polygon.is_empty:
            continue
        if polygon.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Участок «{zone.label}» не является замкнутой областью")
        usable = (
            polygon.buffer(-request.edge_offset_m) if request.edge_offset_m else polygon
        )
        if not usable.is_empty:
            parts.append(usable)
    return unary_union(parts) if parts else Point().buffer(0)


def _cluster_offsets(
    spacing: float, count: int, angle: float
) -> list[tuple[float, float]]:
    """Return a compact hexagonal grove with a stable per-grove rotation."""
    offsets = [(0.0, 0.0)]
    ring = 1
    while len(offsets) < count:
        radius = ring * spacing
        slots = 6 * ring
        for index in range(slots):
            theta = angle + (2 * pi * index / slots)
            offsets.append((radius * cos(theta), radius * sin(theta)))
            if len(offsets) >= count:
                break
        ring += 1
    return offsets


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
    # Sampling the boundary of a buffered road produces two parallel avenues
    # for linework and follows the actual outer edge for polygonal carriageways.
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

    # Round-robin prevents the first DXF road from consuming a count-limited
    # result before the remaining recognised roads have been represented.
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
    """Generate candidates for a named spatial intent, never final placements."""
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


def generate_building_contour(
    request: PlacementMaskRequest, usable: BaseGeometry, guides: list[dict]
) -> list[PatternCandidate]:
    """Sample parallel facade contours, never scatter over a broad area band.

    The caller supplies the domain-computed safe area (including species growth).
    Automatic offset follows its nearest usable distance; explicit offsets are
    not silently enlarged. Final spacing/geometry checks remain authoritative.
    """
    if usable.is_empty:
        return []
    buildings = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "building"
    ]
    if not buildings:
        return []
    if request.screen_side != "perimeter":
        raise ValueError(
            "Для ряда по контуру используйте периметр здания; ряд вдоль дороги задаётся отдельно."
        )
    occupied = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "planned_plant_clearance"
    ]
    available = usable.difference(unary_union(occupied)) if occupied else usable
    if available.is_empty:
        return []
    footprint = unary_union(buildings)
    components = list(footprint.geoms) if hasattr(footprint, "geoms") else [footprint]
    accepted = []

    def lines(geometry):
        if geometry.geom_type in {"LineString", "LinearRing"}:
            yield geometry
        elif hasattr(geometry, "geoms"):
            for part in geometry.geoms:
                yield from lines(part)

    for index, building in enumerate(components):
        if building.geom_type != "Polygon":
            continue
        offset = (
            request.building_offset_m
            if request.building_offset_m is not None
            else max(0.5, building.distance(usable) + 0.05)
        )
        if offset > 60:
            continue
        contour = building.buffer(offset, quad_segs=32).boundary.intersection(available)
        for line in lines(contour):
            if line.length < 0.01:
                continue
            count = max(1, floor(line.length / request.spacing_m))
            for number in range(count):
                point = line.interpolate((number + 0.5) * line.length / count)
                accepted.append(
                    PatternCandidate(
                        round(point.x, 6),
                        round(point.y, 6),
                        group_key=f"facade-{index}",
                    )
                )
                if len(accepted) > MAX_PATTERN_CANDIDATES:
                    return _bounded(accepted)
    return _bounded(accepted)


def generate_building_screen(
    request: PlacementMaskRequest, usable: BaseGeometry, guides: list[dict]
) -> list[PatternCandidate]:
    """Groups in an exterior band, not arbitrary filling of the whole site.

    All coordinates stay downstream of the ordinary safety/spacing validator.
    Road-facing is a geometric placement criterion, not a visibility score.
    """
    buildings = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "building"
    ]
    roads = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "road"
    ]
    if usable.is_empty or not buildings:
        return []
    buildings = unary_union(buildings)
    band = (
        buildings.buffer(max(24, request.spacing_m * 3))
        .difference(buildings)
        .intersection(usable)
    )
    occupied = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "planned_plant_clearance"
    ]
    if occupied:
        band = band.difference(unary_union(occupied))
    if request.screen_side == "roads" and not roads:
        raise ValueError(
            "В чертеже не найдены проезды. Выберите размещение по периметру."
        )
    roads = unary_union(roads) if roads else None

    def fits(point):
        if not band.covers(point):
            return False
        if request.screen_side == "perimeter":
            return True
        wall, _ = nearest_points(buildings, point)
        _, road = nearest_points(point, roads)
        toward_road = (point.x - wall.x) * (road.x - wall.x) + (point.y - wall.y) * (
            road.y - wall.y
        ) > 0
        return (
            toward_road
            and point.distance(roads) < wall.distance(roads)
            and not LineString([point, road]).crosses(buildings)
        )

    # Try several orientations before giving up a grove in a narrow strip.
    # Each full grove is generated first; road-facing then filters it so the
    # two views stay comparable rather than jumping to unrelated positions.
    centres = _poisson_candidates(
        band,
        max(request.cluster_gap_m, request.spacing_m * 3),
        MAX_PATTERN_CANDIDATES,
        request.seed,
    )
    accepted = []
    for index, centre in enumerate(centres):
        variants = []
        for turn in range(12):
            points = [
                Point(round(centre.x + x, 6), round(centre.y + y, 6))
                for x, y in _cluster_offsets(
                    request.spacing_m, request.cluster_size, turn * pi / 6
                )
            ]
            variants.append([p for p in points if band.covers(p)])
        points = [p for p in max(variants, key=len) if fits(p)]
        if len(points) >= 2:
            accepted.extend(
                PatternCandidate(p.x, p.y, group_key=f"grove-{index}") for p in points
            )
    return _bounded(accepted)


def generate_brush(
    request: BrushPreviewRequest, zones: list[PlantingZoneAssignment]
) -> list[PatternCandidate]:
    requested_ids = set(request.zone_ids)
    selected = [zone for zone in zones if zone.id in requested_ids]
    missing = requested_ids - {zone.id for zone in selected}
    if missing:
        raise ValueError("Один из выбранных участков больше не существует")
    add_corridors = []
    subtract_corridors = []
    for stroke in request.strokes:
        geometry = shape(stroke.geometry)
        if (
            not isinstance(geometry, LineString)
            or geometry.is_empty
            or len(geometry.coords) < 2
            or geometry.length <= 0
        ):
            raise ValueError("Мазок должен быть линией минимум из двух точек")
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
