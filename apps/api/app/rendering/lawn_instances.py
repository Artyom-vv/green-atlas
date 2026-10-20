"""Blender grass instances with bounded, camera-aware surface coverage."""

import hashlib
import math
import random
from pathlib import Path

import bpy
import render_style as style
from lawn_scatter import scatter_lawn
from mathutils import Vector


def _boundary_segments(rows):
    """Keep union edges by occupied side, including repeated source faces."""
    edges = {}
    for row in rows:
        if row.get("semantic", row.get("kind")) != "lawn":
            continue
        vertices = row.get("vertices", ())
        faces = row.get("triangles_scene_xyz_m")
        if faces is None:
            faces = (
                [vertices[index] for index in face]
                for face in row.get("faces", ())
                if len(face) == 3
            )
        for face in faces:
            for index in range(3):
                a, b = face[index], face[(index + 1) % 3]
                ka = tuple(round(float(value) * 1e7) for value in a)
                kb = tuple(round(float(value) * 1e7) for value in b)
                if ka == kb:
                    continue
                key = (ka, kb) if ka < kb else (kb, ka)
                opposite = face[(index + 2) % 3]
                left, right = key
                side = (right[0] - left[0]) * (float(opposite[1]) * 1e7 - left[1]) - (
                    right[1] - left[1]
                ) * (float(opposite[0]) * 1e7 - left[0])
                if abs(side) > 1e-8:
                    edges[key] = edges.get(key, 0) | (1 if side > 0 else 2)
    # Both sides occupied means an internal edge. Repeated faces on the same
    # side retain their boundary instead of cancelling it by even parity.
    return (
        (tuple(value / 1e7 for value in a[:2]), tuple(value / 1e7 for value in b[:2]))
        for (a, b), sides in edges.items()
        if sides != 3
    )


def _clip_segment(a, b, bounds):
    start, end = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in (
        (-dx, a[0] - bounds[0]),
        (dx, bounds[1] - a[0]),
        (-dy, a[1] - bounds[2]),
        (dy, bounds[3] - a[1]),
    ):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            start = max(start, t)
        else:
            end = min(end, t)
        if start > end:
            return None
    return (
        (a[0] + start * dx, a[1] + start * dy),
        (a[0] + end * dx, a[1] + end * dy),
    )


def _segment_cells(a, b, size):
    ix, iy = math.floor(a[0] / size), math.floor(a[1] / size)
    ex, ey = math.floor(b[0] / size), math.floor(b[1] / size)
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    tx = (((ix + (sx > 0)) * size - a[0]) / dx) if dx else math.inf
    ty = (((iy + (sy > 0)) * size - a[1]) / dy) if dy else math.inf
    stepx = abs(size / dx) if dx else math.inf
    stepy = abs(size / dy) if dy else math.inf
    for _ in range(abs(ex - ix) + abs(ey - iy) + 3):
        yield ix, iy
        if (ix, iy) == (ex, ey):
            break
        if tx < ty:
            ix += sx
            tx += stepx
        else:
            iy += sy
            ty += stepy


def _boundary_index(rows, samples, radius, size=2.0):
    if not samples:
        return {}
    padding = math.ceil(radius / size) + 1
    active = set()
    for sample in samples:
        x, y = sample.position[:2]
        ix, iy = math.floor(x / size), math.floor(y / size)
        for dx in range(-padding, padding + 1):
            for dy in range(-padding, padding + 1):
                active.add((ix + dx, iy + dy))
    bounds = (
        min(cell[0] for cell in active) * size,
        (max(cell[0] for cell in active) + 1) * size,
        min(cell[1] for cell in active) * size,
        (max(cell[1] for cell in active) + 1) * size,
    )
    buckets = {}
    for a, b in _boundary_segments(rows):
        clipped = _clip_segment(a, b, bounds)
        if clipped is None:
            continue
        for cell in _segment_cells(*clipped, size):
            if cell in active:
                buckets.setdefault(cell, []).append((a, b))
    return buckets


def _clearance(position, buckets, radius, size=2.0):
    x, y = position[:2]
    nearest = radius * radius
    for ix in range(
        math.floor((x - radius) / size), math.floor((x + radius) / size) + 1
    ):
        for iy in range(
            math.floor((y - radius) / size), math.floor((y + radius) / size) + 1
        ):
            for a, b in buckets.get((ix, iy), ()):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = dx * dx + dy * dy
                t = (
                    min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / length))
                    if length
                    else 0.0
                )
                nearest = min(
                    nearest, (x - a[0] - t * dx) ** 2 + (y - a[1] - t * dy) ** 2
                )
    return math.sqrt(nearest)


def add_lawn_instances(
    surface_rows,
    sources,
    *,
    camera_position,
    camera_target,
    camera_up,
    vertical_fov_radians,
    aspect,
    near_m,
    far_m,
    max_instances=12000,
):
    seed_text = (
        ":".join(
            sorted(
                row["id"]
                for row in surface_rows
                if row.get("semantic", row.get("kind")) == "lawn"
            )
        )
        + ":botaniq"
    )
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    result = scatter_lawn(
        surface_rows,
        camera_position,
        camera_target,
        vertical_fov_radians,
        aspect,
        camera_up=camera_up,
        near_m=near_m,
        far_m=far_m,
        max_instances=max_instances,
        # Overlap the 0.74 m cut-grass assets into continuous short turf.
        spacing_m=0.30,
        seed=seed,
    )
    assets, radii, tuned = [], [], []
    for index, source in enumerate(sources):
        collection, height = style.load_collection_asset(
            Path(source),
            f"Botaniq_lawn_patch_{index}",
            "lawn_detail_proxy",
        )
        tuned.extend(style.tune_botaniq_lawn_material(collection))
        assets.append((collection, height, Path(source)))
        radius = max(
            math.hypot(point.x, point.y)
            for obj in collection.objects
            if obj.type == "MESH"
            for corner in obj.bound_box
            for point in (obj.matrix_world @ Vector(corner),)
        )
        if not math.isfinite(radius) or radius <= 0:
            raise RuntimeError("Botaniq grass footprint is invalid")
        radii.append(radius)
    if not assets:
        raise RuntimeError("Botaniq grass assets are missing")
    # A full circular footprint also covers every random patch rotation. Thin
    # borders and holes shrink the patch instead of spilling grass onto paving.
    maximum_scale = 2.2
    maximum_radius = max(radii) * 1.12 * maximum_scale
    boundary = _boundary_index(surface_rows, result.samples, maximum_radius)
    count, clipped_count, scales = 0, 0, []
    for sample in result.samples:
        rng = random.Random(sample.stable_seed)
        index = min(int(rng.random() * len(assets)), len(assets) - 1)
        collection, _, _ = assets[index]
        requested = (0.98 + rng.random() * 0.10) * min(
            maximum_scale, sample.coverage_scale
        )
        clearance = _clearance(sample.position, boundary, radii[index] * requested)
        horizontal = min(requested, max(0.0, clearance - 1e-4) / radii[index])
        clipped_count += clearance < radii[index] * requested - 1e-6
        if horizontal <= 1e-6:
            continue
        instance = bpy.data.objects.new(f"Botaniq_lawn_{count:04d}", None)
        instance.instance_type = "COLLECTION"
        instance.instance_collection = collection
        instance.location = (*sample.position[:2], sample.position[2] + 0.003)
        # As projected cells widen, tall isolated patches become visible rows.
        # Blend subpixel blades into the continuous turf material instead of
        # stretching their silhouette across the distant lawn.
        height_scale = (0.45 + rng.random() * 0.10) / sample.coverage_scale
        instance.scale = (horizontal, horizontal, height_scale)
        instance.rotation_euler.z = rng.random() * math.tau
        instance["semantic"] = "lawn_detail_proxy"
        instance["source_surface_id"] = sample.surface_id
        bpy.context.collection.objects.link(instance)
        scales.append(horizontal)
        count += 1
    metrics = {
        "policy": "camera-stratified bounded shared-geometry instances",
        "budget": 12000,
        "raster_candidates": result.raster_candidate_count,
        "candidates": result.candidate_count,
        "visible_tiles": result.visible_tile_count,
        "sampler_auxiliary_bytes": result.auxiliary_bytes,
        "instances": count,
        "foreground_spacing_m": result.spacing_m,
        "boundary_clipped_instances": clipped_count,
        "max_horizontal_scale": max(scales, default=0.0),
        "shared_meshes": sum(len(collection.objects) for collection, _, _ in assets),
        "camera_near_m": near_m,
        "camera_far_m": far_m,
    }
    return count, assets, seed, sorted(set(tuned)), metrics
