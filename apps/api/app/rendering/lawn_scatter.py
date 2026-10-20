"""Bounded, camera-stratified sampling of admitted lawn surface triangles.

This module also runs in Blender's bundled Python. It deliberately needs only
the standard library and never expands an indexed mesh into another mesh.
"""

from __future__ import annotations

import heapq
import math
import sys
from array import array
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]
MAX_LAWN_INSTANCES = 12_000
_MASK = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class LawnSample:
    position: Vec3
    surface_id: str
    stable_seed: int
    coverage_scale: float


@dataclass(frozen=True, slots=True)
class LawnScatterResult:
    samples: tuple[LawnSample, ...]
    candidate_count: int
    visible_tile_count: int
    auxiliary_bytes: int
    spacing_m: float
    raster_candidate_count: int = 0


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(a: Vec3) -> Vec3:
    length = math.sqrt(_dot(a, a))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("Lawn camera direction must be finite and nonzero")
    return a[0] / length, a[1] / length, a[2] / length


def _mix(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & _MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
    return value ^ (value >> 31)


def _fraction(value: int) -> float:
    return ((value >> 11) + 0.5) / (1 << 53)


def _triangles(row: Mapping[str, Any]) -> Iterable[tuple[Vec3, Vec3, Vec3]]:
    direct = row.get("triangles_scene_xyz_m")
    if direct is not None:
        for face in direct:
            if len(face) != 3:
                raise ValueError("Lawn sampler requires admitted triangle faces")
            yield cast(
                tuple[Vec3, Vec3, Vec3],
                tuple(tuple(float(v) for v in point) for point in face),
            )
        return
    vertices = row.get("vertices", ())
    for face in row.get("faces", ()):
        if len(face) != 3:
            raise ValueError("Lawn sampler requires admitted triangle faces")
        yield cast(
            tuple[Vec3, Vec3, Vec3],
            tuple(tuple(float(v) for v in vertices[index]) for index in face),
        )


def _clip_plane(
    points: list[Vec3], plane: tuple[float, float, float, float]
) -> list[Vec3]:
    if not points:
        return []
    result: list[Vec3] = []
    previous = points[-1]
    before = _dot(previous, plane[:3]) + plane[3]
    for current in points:
        after = _dot(current, plane[:3]) + plane[3]
        if (before >= 0.0) != (after >= 0.0):
            t = before / (before - after)
            result.append(
                cast(
                    Vec3,
                    tuple(
                        previous[i] + t * (current[i] - previous[i]) for i in range(3)
                    ),
                )
            )
        if after >= 0.0:
            result.append(current)
        previous, before = current, after
    return result


def _clip_rectangle(
    points: list[Vec2], bounds: tuple[float, float, float, float]
) -> list[Vec2]:
    result = points
    for axis, limit, greater in (
        (0, bounds[0], True),
        (0, bounds[1], False),
        (1, bounds[2], True),
        (1, bounds[3], False),
    ):
        if not result:
            break
        clipped: list[Vec2] = []
        previous = result[-1]
        before = previous[axis] - limit
        for current in result:
            after = current[axis] - limit
            inside_before = before >= 0.0 if greater else before <= 0.0
            inside_after = after >= 0.0 if greater else after <= 0.0
            if inside_before != inside_after:
                t = before / (before - after)
                clipped.append(
                    (
                        previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]),
                    )
                )
            if inside_after:
                clipped.append(current)
            previous, before = current, after
        result = clipped
    return result


def _nearest(points: list[Vec2], anchor: Vec2) -> tuple[Vec2, float]:
    positive = negative = False
    best = points[0]
    best_distance = math.inf
    area = 0.0
    previous = points[-1]
    for current in points:
        area += previous[0] * current[1] - previous[1] * current[0]
        dx, dy = current[0] - previous[0], current[1] - previous[1]
        px, py = anchor[0] - previous[0], anchor[1] - previous[1]
        side = dx * py - dy * px
        positive |= side > 1e-14
        negative |= side < -1e-14
        length = dx * dx + dy * dy
        t = max(0.0, min(1.0, (px * dx + py * dy) / length)) if length else 0.0
        point = previous[0] + t * dx, previous[1] + t * dy
        distance = (point[0] - anchor[0]) ** 2 + (point[1] - anchor[1]) ** 2
        if (distance, point) < (best_distance, best):
            best, best_distance = point, distance
        previous = current
    if not (positive and negative) and len(points) >= 3 and abs(area) > 1e-16:
        return anchor, 0.0
    return best, best_distance


def _quotas(counts: list[int], weights: list[float], budget: int) -> list[int]:
    """Give every occupied view tile a place before distributing detail."""
    quotas = [int(count > 0) for count in counts]
    remaining = min(budget, sum(counts)) - sum(quotas)
    while remaining > 0:
        active = [i for i, count in enumerate(counts) if quotas[i] < count]
        total = sum(weights[i] for i in active)
        if not active or total <= 0.0:
            break
        shares = [(remaining * weights[i] / total, i) for i in active]
        added = 0
        for share, i in shares:
            take = min(counts[i] - quotas[i], int(share))
            quotas[i] += take
            added += take
        remaining -= added
        if remaining <= 0:
            break
        for _, i in sorted(shares, key=lambda item: (-(item[0] % 1.0), item[1])):
            if quotas[i] < counts[i]:
                quotas[i] += 1
                remaining -= 1
                if remaining <= 0:
                    break
    return quotas


def scatter_lawn(
    surface_rows: Sequence[Mapping[str, Any]],
    camera_position: Vec3,
    camera_target: Vec3,
    vertical_fov_radians: float,
    aspect: float,
    *,
    camera_up: Vec3 = (0.0, 0.0, 1.0),
    near_m: float = 0.06,
    far_m: float = 120.0,
    spacing_m: float = 0.56,
    max_instances: int = MAX_LAWN_INSTANCES,
    seed: int = 0,
) -> LawnScatterResult:
    """Cover visible lawn fairly; reserve detail for the foreground.

    Screen cells, not input triangle order, own samples. Exact frustum clipping
    admits only supplied surface triangles, including narrow strips and holes.
    A conservative boundary sample prevents a strip smaller than a cell from
    disappearing. A nearest-depth tie handles overlapping surface triangles.
    Temporary arrays are at most eight cells per instance, irrespective of the
    physical area, the number of triangles, or the distance of the lawn.
    """
    budget = max(0, min(MAX_LAWN_INSTANCES, int(max_instances)))
    if budget == 0:
        return LawnScatterResult((), 0, 0, 0, spacing_m)
    if not (
        0.0 < vertical_fov_radians < math.pi
        and aspect > 0.0
        and 0.0 < near_m < far_m
        and spacing_m > 0.0
    ):
        raise ValueError("Invalid lawn camera or sampling settings")
    if not all(
        math.isfinite(v)
        for v in (
            *camera_position,
            *camera_target,
            *camera_up,
            vertical_fov_radians,
            aspect,
            near_m,
            far_m,
            spacing_m,
        )
    ):
        raise ValueError("Lawn camera and sampling settings must be finite")
    forward = _unit(_sub(camera_target, camera_position))
    right = _unit(_cross(forward, camera_up))
    up = _unit(_cross(right, forward))
    tan_y = math.tan(vertical_fov_radians * 0.5)
    tan_x = tan_y * aspect
    nx = max(1, int(math.sqrt(budget * 8 * min(aspect, budget * 8))))
    ny = max(1, int(budget * 8 / nx))
    cells = nx * ny
    step_x, step_y = 2.0 / nx, 2.0 / ny
    us, vs, depths = (array("d", [math.nan]) * cells for _ in range(3))
    owners = array("i", [-1]) * cells
    auxiliary_bytes = cells * (3 * us.itemsize + owners.itemsize)
    row_ids = sorted(
        {
            str(row.get("id", row.get("source_id", "lawn")))
            for row in surface_rows
            if row.get("semantic", row.get("kind")) == "lawn"
        }
    )
    owner_index = {name: i for i, name in enumerate(row_ids)}
    planes = (
        (0.0, 0.0, 1.0, -near_m),
        (0.0, 0.0, -1.0, far_m),
        (1.0, 0.0, tan_x, 0.0),
        (-1.0, 0.0, tan_x, 0.0),
        (0.0, 1.0, tan_y, 0.0),
        (0.0, -1.0, tan_y, 0.0),
    )
    for row in surface_rows:
        if row.get("semantic", row.get("kind")) != "lawn":
            continue
        owner = owner_index[str(row.get("id", row.get("source_id", "lawn")))]
        for triangle in _triangles(row):
            camera_points: list[Vec3] = []
            for world_point in triangle:
                relative = _sub(world_point, camera_position)
                camera_points.append(
                    (_dot(relative, right), _dot(relative, up), _dot(relative, forward))
                )
            normal = _cross(
                _sub(camera_points[1], camera_points[0]),
                _sub(camera_points[2], camera_points[0]),
            )
            numerator = _dot(normal, camera_points[0])
            if abs(numerator) <= 1e-12:
                continue
            polygon = camera_points
            for plane in planes:
                polygon = _clip_plane(polygon, plane)
                if len(polygon) < 3:
                    break
            if len(polygon) < 3:
                continue
            projected = [
                (point[0] / (point[2] * tan_x), point[1] / (point[2] * tan_y))
                for point in polygon
            ]
            ix0 = max(0, int(math.floor((min(p[0] for p in projected) + 1) / step_x)))
            ix1 = min(
                nx - 1, int(math.floor((max(p[0] for p in projected) + 1) / step_x))
            )
            iy0 = max(0, int(math.floor((min(p[1] for p in projected) + 1) / step_y)))
            iy1 = min(
                ny - 1, int(math.floor((max(p[1] for p in projected) + 1) / step_y))
            )
            for iy in range(iy0, iy1 + 1):
                bottom = -1.0 + iy * step_y
                for ix in range(ix0, ix1 + 1):
                    cell = iy * nx + ix
                    cell_seed = _mix(seed ^ cell)
                    left = -1.0 + ix * step_x
                    # Use the full cell rather than a central stripe: narrow
                    # vertical jitter makes distant grass read as screen rows.
                    anchor = (
                        left + step_x * (0.05 + 0.90 * _fraction(cell_seed)),
                        bottom + step_y * (0.05 + 0.90 * _fraction(_mix(cell_seed))),
                    )
                    projected_point, distance = _nearest(projected, anchor)
                    if distance > 0.0:
                        clipped = _clip_rectangle(
                            projected, (left, left + step_x, bottom, bottom + step_y)
                        )
                        if not clipped:
                            continue
                        projected_point, distance = _nearest(clipped, anchor)
                    u, v = round(projected_point[0], 12), round(projected_point[1], 12)
                    denominator = (
                        normal[0] * u * tan_x + normal[1] * v * tan_y + normal[2]
                    )
                    if abs(denominator) <= 1e-15:
                        continue
                    depth = round(numerator / denominator, 9)
                    if not near_m - 1e-7 <= depth <= far_m + 1e-7:
                        continue
                    distance = round((u - anchor[0]) ** 2 + (v - anchor[1]) ** 2, 18)
                    existing = owners[cell]
                    if existing >= 0:
                        old_distance = round(
                            (us[cell] - anchor[0]) ** 2 + (vs[cell] - anchor[1]) ** 2,
                            18,
                        )
                        if (distance, depth, owner) >= (
                            old_distance,
                            depths[cell],
                            existing,
                        ):
                            continue
                    us[cell], vs[cell], depths[cell], owners[cell] = u, v, depth, owner
    # Perspective puts many screen cells on the same small foreground patch.
    # Keep a single deterministic representative per metric lawn cell before
    # allocating the instance budget. This table is bounded by the raster size.
    raster_candidate_count = sum(owner >= 0 for owner in owners)
    representatives: dict[tuple[int, int], int] = {}
    for cell, owner in enumerate(owners):
        if owner < 0:
            continue
        u, v, depth = us[cell], vs[cell], depths[cell]
        x = camera_position[0] + depth * (
            forward[0] + right[0] * u * tan_x + up[0] * v * tan_y
        )
        y = camera_position[1] + depth * (
            forward[1] + right[1] * u * tan_x + up[1] * v * tan_y
        )
        key = math.floor(x / spacing_m), math.floor(y / spacing_m)
        previous = representatives.get(key)
        if previous is None or _mix(seed ^ cell) > _mix(seed ^ previous):
            if previous is not None:
                owners[previous] = -1
            representatives[key] = cell
        else:
            owners[cell] = -1
    auxiliary_bytes += sys.getsizeof(representatives) + sum(
        sys.getsizeof(key) + sum(sys.getsizeof(v) for v in key) + sys.getsizeof(value)
        for key, value in representatives.items()
    )
    del representatives
    tile_x, tile_y = min(64, max(1, nx // 4)), min(40, max(1, ny // 4))
    # Even budgets below the regular tile count can cover every occupied tile.
    while tile_x * tile_y > budget:
        if tile_x >= tile_y:
            tile_x -= 1
        else:
            tile_y -= 1
    counts, weights = [0] * (tile_x * tile_y), [0.0] * (tile_x * tile_y)
    for cell, owner in enumerate(owners):
        if owner < 0:
            continue
        tile = (cell // nx * tile_y // ny) * tile_x + (cell % nx * tile_x // nx)
        counts[tile] += 1
        weights[tile] += 1.0 + 2.0 / (1.0 + depths[cell] / 12.0)
    quotas = _quotas(counts, weights, budget)
    heaps: list[list[tuple[float, int]]] = [[] for _ in counts]
    for cell, owner in enumerate(owners):
        if owner < 0:
            continue
        tile = (cell // nx * tile_y // ny) * tile_x + (cell % nx * tile_x // nx)
        quota = quotas[tile]
        weight = 1.0 + 2.0 / (1.0 + depths[cell] / 12.0)
        priority = math.log(_fraction(_mix(seed ^ cell ^ 0xD1B54A32D192ED03))) / weight
        heap = heaps[tile]
        if len(heap) < quota:
            heapq.heappush(heap, (priority, cell))
        elif priority > heap[0][0]:
            heapq.heapreplace(heap, (priority, cell))
    selected = sorted(cell for heap in heaps for _, cell in heap)
    samples: list[LawnSample] = []
    for cell in selected:
        u, v, depth = us[cell], vs[cell], depths[cell]
        position = cast(
            Vec3,
            tuple(
                round(
                    camera_position[i]
                    + depth * (forward[i] + right[i] * u * tan_x + up[i] * v * tan_y),
                    9,
                )
                for i in range(3)
            ),
        )
        tile = (cell // nx * tile_y // ny) * tile_x + (cell % nx * tile_x // nx)
        thinning = math.sqrt(counts[tile] / quotas[tile])
        ray = tuple(
            forward[i] + right[i] * u * tan_x + up[i] * v * tan_y for i in range(3)
        )
        footprint = spacing_m
        if abs(ray[2]) > 1e-7:
            derivatives: list[Vec3] = []
            for basis, step in ((right, tan_x * step_x), (up, tan_y * step_y)):
                derivatives.append(
                    cast(
                        Vec3,
                        tuple(
                            depth * step * (basis[i] - ray[i] * basis[2] / ray[2])
                            for i in range(3)
                        ),
                    )
                )
            footprint = (
                math.sqrt(math.sqrt(_dot(_cross(*derivatives), _cross(*derivatives))))
                * thinning
            )
        samples.append(
            LawnSample(
                position,
                row_ids[owners[cell]],
                _mix(seed ^ cell),
                min(8.0, max(1.0, footprint / spacing_m)),
            )
        )
    return LawnScatterResult(
        tuple(samples),
        sum(counts),
        sum(c > 0 for c in counts),
        auxiliary_bytes,
        spacing_m,
        raster_candidate_count,
    )
