"""Conservative render-only interpretation of projected curb paths and symbols.

The caller admits native metadata and same-plane surface adjacency. No CAD
reader, source repair, snapping or lifecycle inference runs in this module.
"""

from __future__ import annotations

from collections import defaultdict
from math import acos, cos, hypot, isfinite, radians, sin
from statistics import median
from typing import Any

from shapely import STRtree, line_merge, maximum_inscribed_circle
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

MAX_DIAGNOSTIC_ADDRESSES = 64


def _native_area(detail: dict[str, Any]) -> Any:
    value = detail.get("source_native_area")
    return value if value is not None else detail.get("source_native_area_units2")


def projected_curb_line(
    detail: dict[str, Any],
    geometry: BaseGeometry | None = None,
    width_m: float = 0.15,
) -> bool:
    """A native closed curve projected as Polygon remains a line for rendering.

    A HATCH, REGION, explicit footprint, area witness or polygon with holes
    retains its actual surface interpretation. A narrow outlined footprint is
    retained unless the caller proved this is an admitted lawn/sidewalk border.
    Width is an explicitly illustrative display parameter, never CAD evidence.
    """
    entity = str(
        detail.get("source_entity_type", detail.get("entity_type", ""))
    ).upper()
    if (
        entity not in {"LWPOLYLINE", "POLYLINE"}
        or detail.get("source_closed_path") is not True
        or detail.get("source_polygon_projection") is not True
        or _native_area(detail) is not None
        or detail.get("source_geometry_purpose") == "footprint"
        or detail.get("representation") == "footprint"
    ):
        return False
    if geometry is None:
        return True
    if (
        not isinstance(geometry, (Polygon, MultiPolygon))
        or geometry.is_empty
        or not geometry.is_valid
    ):
        return False
    parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    if any(part.interiors for part in parts):
        return False
    if not isfinite(width_m) or width_m <= 0:
        raise ValueError("Невалидная ширина отображения бордюра")
    adjacency = (
        detail.get("lawn_boundary_evidence") is True or detail.get("adjacency") is True
    )
    if not adjacency:
        # A thin rectangle/ribbon can be an outlined actual kerb footprint.
        # Its morphology alone does not authorize widening both of its edges.
        if all(
            maximum_inscribed_circle(part).length * 2 <= width_m * 1.5 for part in parts
        ):
            return False
    return True


def _flat_signature(rows: list[dict[str, Any]]) -> tuple[float, float, float] | None:
    signatures = set()
    for row in rows:
        a, b = row["start_scene_xyz_m"], row["end_scene_xyz_m"]
        width, height = float(row["width_m"]), float(row["height_m"])
        if (
            len(a) != 3
            or len(b) != 3
            or not all(isfinite(float(value)) for value in (*a, *b, width, height))
            or width <= 0
            or height <= 0
        ):
            raise ValueError("Невалидные размеры или координаты бордюра")
        if a[2] != b[2]:
            return None
        signatures.add((float(a[2]), width, height))
    return next(iter(signatures)) if len(signatures) == 1 else None


def _line_parts(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry] if not geometry.is_empty else []
    if isinstance(geometry, MultiLineString):
        return [part for part in geometry.geoms if not part.is_empty]
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return _line_parts(geometry.boundary)
    return []


def _declared_footprint(border: LineString, width: float) -> BaseGeometry:
    # Match the joined display mesh construction in curb_geometry.py.
    return border.buffer(width / 2, cap_style="flat", join_style="mitre", mitre_limit=2)


def _parallel_match(
    glyph: LineString, border: LineString, width: float, max_offset: float
) -> dict[str, float] | None:
    footprint = _declared_footprint(border, width)
    if not footprint.buffer(max_offset).covers(glyph):
        return None
    samples = [
        glyph.interpolate(value, normalized=True) for value in (0, 0.25, 0.5, 0.75, 1)
    ]
    distances = [point.distance(border) for point in samples]
    centre = samples[2]
    station = border.project(centre)
    delta = min(0.20, glyph.length * 0.25)
    if border.is_ring:
        before, after = (
            (station - delta) % border.length,
            (station + delta) % border.length,
        )
    else:
        before, after = max(0, station - delta), min(border.length, station + delta)
    a, b = border.interpolate(before), border.interpolate(after)
    first, last = glyph.coords[0], glyph.coords[-1]
    vx, vy = b.x - a.x, b.y - a.y
    gx, gy = last[0] - first[0], last[1] - first[1]
    denominator = hypot(vx, vy) * hypot(gx, gy)
    if denominator <= 1e-12:
        return None
    alignment = min(1, abs((vx * gx + vy * gy) / denominator))
    if alignment < cos(radians(12)):
        return None
    angle = acos(alignment)
    # A straight source stroke tilted by a proved small angle changes its
    # offset by length*sin(angle). That local geometry is not a parallelism
    # failure, and does not authorize a larger whole-glyph coverage bound.
    if max(distances) - min(distances) > max(0.015, width * 0.25) + glyph.length * sin(
        angle
    ):
        return None
    envelope_distances = [point.distance(footprint) for point in samples]
    nearest = border.interpolate(station)
    signed = vx * (centre.y - nearest.y) - vy * (centre.x - nearest.x)
    return {
        "station": station,
        "minimum_offset_m": min(envelope_distances),
        "maximum_offset_m": max(envelope_distances),
        "minimum_axis_offset_m": min(distances),
        "maximum_axis_offset_m": max(distances),
        "mean_offset_m": sum(distances) / len(distances),
        "parallel_angle_deg": angle * 180 / 3.141592653589793,
        "axis_offset_slope": (vx * gy - vy * gx) / (vx * gx + vy * gy),
        "side": 1 if signed > 0 else -1 if signed < 0 else 0,
        "closed_curve": float(border.is_ring),
        "border_length_m": border.length,
    }


def _closed_symbol_run(
    records: list[dict[str, Any]],
    border: LineString,
    width: float,
    max_offset: float,
) -> list[dict[str, Any]]:
    """Prove an entire native dashed ring, including its corner strokes.

    Only exactly joined source endpoints join split units. Uniform unit
    lengths and gaps, a simple closed source loop, and two-way whole-loop
    coverage are required. A partial row or single bent mark cannot qualify.
    """
    if not border.is_ring:
        return []
    footprint = _declared_footprint(border, width)
    near = [
        record
        for record in records
        if footprint.buffer(max_offset).covers(record["line"])
    ]
    if len(near) < 6:
        return []
    merged = line_merge(MultiLineString([record["line"] for record in near]))
    units = _line_parts(merged)
    if len(units) < 6:
        return []
    length = median(unit.length for unit in units)
    if (
        not 0 < length <= 1.25
        or max(abs(unit.length - length) for unit in units) > max(0.015, length * 0.10)
        or abs(sum(unit.length for unit in units) - sum(r["line"].length for r in near))
        > 1e-8
    ):
        return []
    units.sort(key=lambda line: border.project(line.interpolate(0.5, normalized=True)))
    oriented = []
    for unit in units:
        start = border.project(unit.interpolate(0))
        end = border.project(unit.interpolate(1, normalized=True))
        delta = (end - start + border.length / 2) % border.length - border.length / 2
        oriented.append(unit if delta > 0 else LineString(list(unit.coords)[::-1]))
    gaps = [
        LineString([unit.coords[-1], oriented[(i + 1) % len(oriented)].coords[0]])
        for i, unit in enumerate(oriented)
    ]
    spacing = median(gap.length for gap in gaps)
    if not length * 0.1 <= spacing <= length * 1.1 or max(
        abs(gap.length - spacing) for gap in gaps
    ) > max(0.05, spacing * 0.20):
        return []
    coordinates = [point for unit in oriented for point in unit.coords]
    loop = LineString([*coordinates, coordinates[0]])
    area = Polygon(loop)
    target_area = Polygon(border)
    if (
        not loop.is_simple
        or not area.is_valid
        or not area.contains(target_area.centroid)
        or not target_area.contains(area.centroid)
        or not 0.75 <= loop.length / border.length <= 1.33
        or not footprint.buffer(max_offset).covers(loop)
        or not loop.buffer(max_offset + width / 2).covers(border)
    ):
        return []
    result = []
    for record in near:
        line = record["line"]
        points = [line.interpolate(t, normalized=True) for t in (0, 0.25, 0.5, 0.75, 1)]
        axis = [point.distance(border) for point in points]
        envelope = [point.distance(footprint) for point in points]
        result.append(
            {
                **record,
                "proof_kind": "closed_native_repeat",
                "unit_length": length,
                "closed_repeat_units": len(units),
                "closed_repeat_gap_m": spacing,
                "closed_repeat_loop_length_m": loop.length,
                "closed_loop": loop,
                "match": {
                    "minimum_offset_m": min(envelope),
                    "maximum_offset_m": max(envelope),
                    "minimum_axis_offset_m": min(axis),
                    "maximum_axis_offset_m": max(axis),
                    "mean_offset_m": sum(axis) / len(axis),
                    # Individual corner angle is not the ring witness.
                    "parallel_angle_deg": None,
                },
            }
        )
    return result


def _station_order(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda record: record["match"]["station"])
    if ordered and all(record["match"]["closed_curve"] for record in records):
        stations = [record["match"]["station"] for record in ordered]
        circumference = records[0]["match"]["border_length_m"]
        gaps = [b - a for a, b in zip(stations, stations[1:])]
        gaps.append(stations[0] + circumference - stations[-1])
        after_gap = (max(range(len(gaps)), key=lambda index: gaps[index]) + 1) % len(
            stations
        )
        # The start vertex of a closed CAD path must not split a repeated run.
        ordered = ordered[after_gap:] + ordered[:after_gap]
    return ordered


def _stations(ordered: list[dict[str, Any]]) -> list[float]:
    result: list[float] = []
    for record in ordered:
        value = record["match"]["station"]
        if result and record["match"]["closed_curve"]:
            while value < result[-1]:
                value += record["match"]["border_length_m"]
        result.append(value)
    return result


def _regular_run(records: list[dict[str, Any]]) -> bool:
    if len(records) < 3:
        return False
    length = median(record["length"] for record in records)
    if max(abs(record["length"] - length) for record in records) > max(
        0.015, length * 0.05
    ):
        return False
    ordered = _station_order(records)
    stations = _stations(ordered)
    steps = [b - a for a, b in zip(stations, stations[1:])]
    spacing = median(steps)
    offsets = [
        record["match"]["mean_offset_m"] * record["match"]["side"] for record in ordered
    ]
    geometric_offset_trend = all(
        abs(
            offsets[index + 1]
            - offsets[index]
            - step
            * (
                ordered[index]["match"]["axis_offset_slope"]
                + ordered[index + 1]["match"]["axis_offset_slope"]
            )
            / 2
        )
        <= max(0.015, length * 0.05)
        for index, step in enumerate(steps)
    )
    # Keep distinct physical pieces that overlap or are too far apart to be
    # the repeated marks of one unit-glyph symbol. Missing runs stay unresolved.
    return (
        spacing >= length * 0.9
        and spacing <= length * 3
        and max(abs(step - spacing) for step in steps) <= max(0.05, spacing * 0.20)
        and (
            max(record["match"]["mean_offset_m"] for record in records)
            - min(record["match"]["mean_offset_m"] for record in records)
            <= 0.15
            or geometric_offset_trend
        )
    )


def _regular_runs(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Prove local consecutive repeats; a gap elsewhere cannot erase evidence."""
    if len(records) < 3:
        return []
    ordered = _station_order(records)
    stations = _stations(ordered)
    steps = [b - a for a, b in zip(stations, stations[1:])]
    spacing = median(steps)
    chunks: list[list[dict[str, Any]]] = [[]]
    for index, record in enumerate(ordered):
        if index and abs(steps[index - 1] - spacing) > max(0.05, spacing * 0.20):
            chunks.append([])
        chunks[-1].append(record)
    result = []
    for chunk in chunks:
        proven: set[int] = set()
        for index in range(len(chunk) - 2):
            if _regular_run(chunk[index : index + 3]):
                proven.update((index, index + 1, index + 2))
        run: list[dict[str, Any]] = []
        for index, record in enumerate(chunk):
            if index in proven:
                run.append(record)
            elif run:
                result.append(run)
                run = []
        if run:
            result.append(run)
    return result


def suppress_offset_curb_glyphs(
    rows: list[dict[str, Any]],
    whole_source_lines: dict[str, list[list[list[float]]]],
    role_evidence: dict[str, dict[str, Any]],
    known_borders: dict[str, BaseGeometry],
    max_offset_m: float = 0.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Give admitted physical rims display priority over repeated native marks.

    Borders are supplied only after the caller proves whole-curve adjacency to
    accepted same-plane ground surfaces. Nearby arbitrary long CAD lines have
    no authority here. A source glyph is suppressed wholly, never clipped or
    shifted. The source rows, paths, IDs and native metadata remain untouched.
    """
    if not isfinite(max_offset_m) or not 0 < max_offset_m <= 0.5:
        raise ValueError("Невалидный предел смещения графики бордюра")
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["source_handle"]].append(row)
    signatures = {
        identifier: _flat_signature(group) for identifier, group in by_source.items()
    }
    unresolved: dict[str, set[str]] = defaultdict(set)

    def retain(reason: str, identifier: str) -> None:
        unresolved[reason].add(identifier)

    border_parts: list[tuple[str, LineString, tuple[float, float, float]]] = []
    for identifier, geometry in known_borders.items():
        signature = signatures.get(identifier)
        if signature is None:
            continue
        whole_chains = whole_source_lines.get(identifier, [])
        whole_points = [point for chain in whole_chains for point in chain]
        if (
            not whole_points
            or any(len(chain) < 2 for chain in whole_chains)
            or any(
                len(point) != 3 or not isfinite(float(point[2]))
                for point in whole_points
            )
            or len({point[2] for point in whole_points}) != 1
        ):
            retain("authority_whole_source_grade_unproven", identifier)
            continue
        for part in _line_parts(geometry):
            if part.length >= 2:
                border_parts.append((identifier, part, signature))
    tree = STRtree([record[1] for record in border_parts])
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    closed_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    candidate_count = 0

    for identifier in by_source:
        if identifier in known_borders:
            continue
        detail = role_evidence.get(identifier, {})
        entity = str(
            detail.get("source_entity_type", detail.get("entity_type", ""))
        ).upper()
        if entity not in {"LINE", "LWPOLYLINE", "POLYLINE"}:
            continue
        chains = whole_source_lines.get(identifier, [])
        if len(chains) != 1 or len(chains[0]) < 2:
            retain("whole_native_line_unavailable", identifier)
            continue
        line = LineString([point[:2] for point in chains[0]])
        if not line.is_valid or not 0 < line.length <= 1.25:
            continue
        candidate_count += 1
        if entity != "LINE" and detail.get("source_closed_path") is not False:
            retain("native_open_straight_stroke_unproven", identifier)
            continue
        source_signature = signatures[identifier]
        if source_signature is None or len({point[2] for point in chains[0]}) != 1:
            retain("grade_or_multiple_levels_retained", identifier)
            continue
        if (
            _native_area(detail) is not None
            or detail.get("source_geometry_purpose") == "footprint"
            or detail.get("representation") == "footprint"
        ):
            retain("physical_footprint_retained", identifier)
            continue
        native_group = detail.get("source_instance_chain", [])
        if (
            not isinstance(native_group, (list, tuple))
            or len(native_group) < 2
            or any(not isinstance(value, str) or not value for value in native_group)
        ):
            retain("native_symbol_group_unproven", identifier)
            continue
        symbol_key = (
            tuple(native_group),
            detail.get("layer_id", detail.get("source_layer", "")),
            source_signature,
        )
        closed_groups[symbol_key].append(
            {
                "id": identifier,
                "length": line.length,
                "line": line,
                "native_group": list(native_group),
            }
        )
        direct = LineString([line.coords[0], line.coords[-1]])
        if (
            line.hausdorff_distance(direct) > 1e-8
            or abs(line.length - direct.length) > 1e-8
        ):
            retain("native_open_straight_stroke_unproven", identifier)
            continue
        matches = []
        for j in tree.query(line.buffer(max_offset_m + source_signature[1] / 2)):
            target, border, signature = border_parts[int(j)]
            if signature != source_signature or border.length < line.length * 3:
                continue
            match = _parallel_match(line, border, signature[1], max_offset_m)
            if match:
                matches.append((target, border, match))
        if not matches:
            retain("confirmed_parallel_rim_unavailable", identifier)
            continue
        matches.sort(key=lambda value: (value[2]["mean_offset_m"], value[0]))
        candidates: list[
            tuple[tuple[str, LineString, dict[str, float]], dict[str, Any]]
        ] = []
        primary_ambiguous = False
        for index, selected in enumerate(matches):
            equivalent_targets = [selected[0]]
            ambiguous = False
            rim_relation = "single_admitted_rim"
            for other in matches:
                if (
                    other is selected
                    or abs(other[2]["mean_offset_m"] - selected[2]["mean_offset_m"])
                    > 0.005
                ):
                    continue
                if other[1].equals(selected[1]):
                    equivalent_targets.append(other[0])
                    rim_relation = "coincident_source_curves"
                elif (
                    _declared_footprint(selected[1], source_signature[1])
                    .intersection(_declared_footprint(other[1], source_signature[1]))
                    .covers(line)
                ):
                    # The declared footprints cover the entire glyph together,
                    # so this is one envelope without a gap between two rims.
                    equivalent_targets.append(other[0])
                    rim_relation = "overlapping_declared_display_footprints"
                else:
                    ambiguous = True
                    break
            if ambiguous:
                if index == 0:
                    primary_ambiguous = True
                    break
                continue
            candidates.append(
                (
                    selected,
                    {
                        "id": identifier,
                        "length": line.length,
                        "match": selected[2],
                        "native_group": list(native_group),
                        "target_ids": sorted(set(equivalent_targets)),
                        "rim_relation": rim_relation,
                        "rim_width_m": source_signature[1],
                    },
                )
            )
        if primary_ambiguous:
            retain("multiple_physical_rims_ambiguous", identifier)
            continue
        # Prove a repeat before picking its closest authority. Otherwise,
        # overlapping or split admitted border paths can take alternate glyphs
        # out of the same complete native run and fabricate an evidence gap.
        for selected, record in candidates:
            key = (
                tuple(native_group),
                detail.get("layer_id", detail.get("source_layer", "")),
                signatures[identifier],
                selected[0],
                selected[1].wkb,
                selected[2]["side"],
            )
            groups[key].append(record)
    suppressed, aliases = set(), []
    proven_runs: list[list[dict[str, Any]]] = []
    considered_ids: set[str] = set()
    for group in groups.values():
        runs = _regular_runs(group)
        considered_ids.update(record["id"] for record in group)
        proven_runs.extend(runs)
    ambiguous_ids = unresolved["multiple_physical_rims_ambiguous"]
    for closed_key, records in closed_groups.items():
        if len(records) < 6:
            continue
        ring_signature: tuple[float, float, float] = closed_key[-1]
        closed_proofs: dict[
            tuple[str, ...], list[tuple[str, LineString, list[dict[str, Any]]]]
        ] = defaultdict(list)
        for identifier, border, border_signature in border_parts:
            if border_signature != ring_signature or not border.is_ring:
                continue
            run = _closed_symbol_run(records, border, ring_signature[1], max_offset_m)
            if run:
                closed_proofs[tuple(sorted(record["id"] for record in run))].append(
                    (identifier, border, run)
                )
        for proof_set in closed_proofs.values():
            proof_set.sort(
                key=lambda proof: (
                    sum(r["match"]["mean_offset_m"] for r in proof[2]) / len(proof[2]),
                    proof[0],
                )
            )
            identifier, border, run = proof_set[0]
            score = sum(r["match"]["mean_offset_m"] for r in run) / len(run)
            authority_ids = [identifier]
            relation = "single_admitted_closed_rim"
            ambiguous = False
            for other_id, other_border, other_run in proof_set[1:]:
                other_score = sum(r["match"]["mean_offset_m"] for r in other_run) / len(
                    other_run
                )
                if other_score - score > 0.005:
                    break
                if other_border.equals(border):
                    authority_ids.append(other_id)
                    relation = "coincident_source_curves"
                elif (
                    _declared_footprint(border, ring_signature[1])
                    .intersection(_declared_footprint(other_border, ring_signature[1]))
                    .covers(run[0]["closed_loop"])
                ):
                    authority_ids.append(other_id)
                    relation = "overlapping_declared_display_footprints"
                else:
                    ambiguous = True
                    break
            if ambiguous:
                for record in run:
                    retain("multiple_physical_rims_ambiguous", record["id"])
                continue
            for record in run:
                record.update(
                    target_ids=sorted(authority_ids),
                    rim_relation=relation,
                    rim_width_m=ring_signature[1],
                )
            proven_runs.append(run)
    proven_runs = [
        [r for r in run if r["id"] not in ambiguous_ids] for run in proven_runs
    ]
    proven_runs = [run for run in proven_runs if run]
    proven_ids = {record["id"] for run in proven_runs for record in run}
    for identifier in sorted(considered_ids - proven_ids):
        retain("repeated_native_glyph_pattern_unproven", identifier)
    chosen: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for run in proven_runs:
        for record in run:
            existing = chosen.get(record["id"])
            choice_key = (record["match"]["mean_offset_m"], record["target_ids"])
            if existing is None or choice_key < (
                existing[0]["match"]["mean_offset_m"],
                existing[0]["target_ids"],
            ):
                chosen[record["id"]] = (record, run)
    for record, group in chosen.values():
        unit_length = record.get(
            "unit_length", median(record["length"] for record in group)
        )
        suppressed.add(record["id"])
        aliases.append(
            {
                "source_handle": record["id"],
                "retained_source_handle": record["target_ids"][0],
                "retained_source_handles": record["target_ids"],
                "rim_relation": record["rim_relation"],
                "reason": "repeated_native_line_symbol_near_confirmed_surface_rim",
                "pattern_kind": record.get(
                    "proof_kind", "local_parallel_native_repeat"
                ),
                "status": "illustrative_display_priority_source_geometry_preserved",
                "source_instance_chain": record["native_group"],
                "source_length_m": record["length"],
                "pattern_size": len(group),
                "pattern_unit_length_m": unit_length,
                "minimum_offset_m": record["match"]["minimum_offset_m"],
                "maximum_offset_m": record["match"]["maximum_offset_m"],
                "minimum_axis_offset_m": record["match"]["minimum_axis_offset_m"],
                "maximum_axis_offset_m": record["match"]["maximum_axis_offset_m"],
                "offset_reference": "declared_display_footprint",
                "retained_rim_width_m": record["rim_width_m"],
                "whole_glyph_envelope_coverage": True,
                "parallel_angle_deg": record["match"]["parallel_angle_deg"],
                "axis_offset_slope": record["match"].get("axis_offset_slope"),
                "maximum_allowed_offset_m": max_offset_m,
                **{
                    field: record[field]
                    for field in (
                        "closed_repeat_units",
                        "closed_repeat_gap_m",
                        "closed_repeat_loop_length_m",
                    )
                    if field in record
                },
                "interpretation": "native symbol display, not a claim that different source positions are physical duplicates",
            }
        )
    return (
        [row for row in rows if row["source_handle"] not in suppressed],
        aliases,
        {
            "policy": "whole native local straight repeats or proved complete closed repeats near admitted same-plane surface rims only",
            "offset_reference": "declared_display_footprint",
            "maximum_offset_m": max_offset_m,
            "candidate_source_count": candidate_count,
            "proven_pattern_count": len(
                {tuple(sorted(record["id"] for record in run)) for run in proven_runs}
            ),
            "suppressed_source_count": len(suppressed),
            "suppressed_segment_count": sum(
                row["source_handle"] in suppressed for row in rows
            ),
            "unresolved_display_counts": {
                reason: len(identifiers - suppressed)
                for reason, identifiers in unresolved.items()
                if identifiers - suppressed
            },
            "unresolved_display_addresses": {
                reason: sorted(identifiers - suppressed)[:MAX_DIAGNOSTIC_ADDRESSES]
                for reason, identifiers in unresolved.items()
                if identifiers - suppressed
            },
        },
    )


MAX_GRAPHIC_ASSEMBLY_SOURCES = 512
MAX_GRAPHIC_ASSEMBLY_POINTS = 32768
_PHYSICAL_CURB_PURPOSES = {
    "footprint",
    "centerline",
    "centreline",
    "axis",
    "physical_centerline",
    "physical_centreline",
}


def _native_graphic_repeat(lines: list[LineString]) -> dict[str, Any] | None:
    """Infer drawing cadence from detached native paths, never from a rim."""
    merged = line_merge(MultiLineString(lines))
    units = _line_parts(merged)
    if len(units) < 6:
        return None
    lengths = [unit.length for unit in units]
    unit_length = median(lengths)
    tolerance = max(0.015, unit_length * 0.10)
    full_units = sum(abs(length - unit_length) <= tolerance for length in lengths)
    half_units = sum(abs(length - unit_length / 2) <= tolerance for length in lengths)
    if (
        not 0.2 <= unit_length <= 1.25
        or full_units < 6
        or full_units + half_units != len(units)
        or half_units > 2
    ):
        return None
    endpoints = [
        (index, side, (unit.coords[0] if side == 0 else unit.coords[-1])[:2])
        for index, unit in enumerate(units)
        for side in (0, 1)
    ]
    nearest: dict[tuple[int, int], tuple[float, int, int]] = {}
    ties = 0
    for index, side, point in endpoints:
        distances = sorted(
            (hypot(point[0] - other[0], point[1] - other[1]), other_index, other_side)
            for other_index, other_side, other in endpoints
            if other_index != index
        )
        nearest[(index, side)] = distances[0]
        ties += int(abs(distances[1][0] - distances[0][0]) < 1e-5)
    gap = median(record[0] for record in nearest.values())
    if not unit_length * 0.15 <= gap <= unit_length * 2:
        return None
    repeated_endpoints = sum(
        nearest[(other_index, other_side)][1:] == endpoint
        and abs(distance - gap) <= max(0.005, gap * 0.05)
        for endpoint, (distance, other_index, other_side) in nearest.items()
    )
    repeat_fraction, tie_fraction = (
        repeated_endpoints / len(endpoints),
        ties / len(endpoints),
    )
    if repeat_fraction < 0.80 or tie_fraction > 0.10:
        return None
    return {
        "detached_unit_count": len(units),
        "full_unit_count": full_units,
        "half_unit_count": half_units,
        "unit_length_m": unit_length,
        "inferred_gap_m": gap,
        "repeat_endpoint_fraction": repeat_fraction,
        "nearest_tie_fraction": tie_fraction,
    }


def suppress_native_curb_graphics(
    rows: list[dict[str, Any]],
    whole_source_lines: dict[str, list[list[list[float]]]],
    role_evidence: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep proved native drawing marks in 2D instead of extruding each dash.

    This is an inferred display interpretation, not a measured physical
    duplicate or a replacement curb. Qualification uses the entire assembly
    from retained AutoCAD capture properties and whole source paths. No map,
    nearest physical rim, view clipping, CAD repair or archive reader is used.
    """
    visible = {row["source_handle"] for row in rows}
    groups: dict[tuple[tuple[str, ...], str], list[str]] = defaultdict(list)
    unresolved: dict[str, set[str]] = defaultdict(set)
    assembly_rejections: dict[str, int] = defaultdict(int)

    def reject(reason: str, identifiers: list[str]) -> None:
        assembly_rejections[reason] += 1
        unresolved[reason].update(identifiers)

    for identifier in sorted(set(role_evidence) | visible):
        detail = role_evidence.get(identifier, {})
        if (
            detail.get("role") != "curb"
            and detail.get("category") != "curb"
            and identifier not in visible
        ):
            continue
        chain = detail.get("source_instance_chain")
        layer = detail.get("source_layer")
        if (
            not isinstance(chain, (list, tuple))
            or len(chain) < 2
            or any(not isinstance(member, str) or not member for member in chain)
            or not isinstance(layer, str)
            or not layer
        ):
            unresolved["native_block_ancestry_unproven"].add(identifier)
            continue
        groups[(tuple(chain), layer)].append(identifier)

    suppressed: set[str] = set()
    interpretations: list[dict[str, Any]] = []
    for (chain, layer), identifiers in sorted(groups.items()):
        if len(identifiers) > MAX_GRAPHIC_ASSEMBLY_SOURCES:
            reject("native_assembly_display_budget_exceeded", identifiers)
            continue
        lines: list[LineString] = []
        elevations: set[float] = set()
        point_count = 0
        failure = None
        for identifier in identifiers:
            detail = role_evidence.get(identifier, {})
            if (
                detail.get("role") != "curb"
                or detail.get("category") != "curb"
                or detail.get("mapping_confirmed") is not True
            ):
                failure = "curb_assembly_role_unconfirmed"
                break
            if (
                detail.get("source_native_geometry") is not True
                or detail.get("source_geometry_provider") != "autocad_snapshot_v1"
            ):
                failure = "native_capture_properties_unproven"
                break
            if any(
                str(detail.get(field, "")).strip().casefold() in _PHYSICAL_CURB_PURPOSES
                for field in ("source_geometry_purpose", "representation")
            ):
                failure = "explicit_physical_curb_representation_retained"
                break
            native_area = _native_area(detail)
            if native_area is not None:
                if (
                    isinstance(native_area, bool)
                    or not isinstance(native_area, (int, float))
                    or not isfinite(native_area)
                    or native_area < 0
                ):
                    failure = "native_curb_area_unproven"
                    break
                if native_area > 0:
                    failure = "explicit_physical_curb_representation_retained"
                    break
            if (
                detail.get("source_entity_type")
                not in {"LINE", "LWPOLYLINE", "POLYLINE"}
                or detail.get("source_closed_path") is not False
            ):
                failure = "closed_or_nonstroke_curb_geometry_retained"
                break
            chains = whole_source_lines.get(identifier, [])
            if len(chains) != 1 or len(chains[0]) < 2:
                failure = "whole_native_curb_stroke_unavailable"
                break
            points = chains[0]
            point_count += len(points)
            if point_count > MAX_GRAPHIC_ASSEMBLY_POINTS:
                failure = "native_assembly_display_budget_exceeded"
                break
            if any(
                len(point) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not isfinite(value)
                    for value in point
                )
                for point in points
            ):
                failure = "nonfinite_native_curb_geometry_retained"
                break
            elevations.update(float(point[2]) for point in points)
            line = LineString([point[:2] for point in points])
            if (
                line.is_empty
                or not line.is_valid
                or not line.is_simple
                or line.length <= 0
            ):
                failure = "invalid_native_curb_stroke_retained"
                break
            lines.append(line)
        if failure is not None:
            reject(failure, identifiers)
            continue
        if len(elevations) != 1:
            reject("graded_or_multiple_native_levels_retained", identifiers)
            continue
        proof = _native_graphic_repeat(lines)
        if proof is None:
            reject("whole_native_graphic_repeat_unproven", identifiers)
            continue
        suppressed.update(identifiers)
        interpretations.append(
            {
                "status": "repeated_native_graphic_2d_only",
                "reason": "disconnected_repeated_native_unit_strokes",
                "inferred": True,
                "source_ids": identifiers,
                "visible_source_ids": sorted(visible.intersection(identifiers)),
                "source_instance_chain": list(chain),
                "source_layer": layer,
                "source_native_geometry": True,
                "source_geometry_provider": "autocad_snapshot_v1",
                "whole_source_elevation_m": next(iter(elevations)),
                **proof,
                "interpretation": "inferred illustrative drawing semantics; source and 2D preserved, no physical curb inferred or replaced",
            }
        )
    retained = [row for row in rows if row["source_handle"] not in suppressed]
    return (
        retained,
        interpretations,
        {
            "policy": "whole native disconnected repeat assemblies are 2D drawing graphics",
            "status": "inferred_illustrative_display_interpretation",
            "native_assembly_count": len(groups),
            "qualified_assembly_count": len(interpretations),
            "qualified_source_count": len(suppressed),
            "suppressed_visible_source_count": len(suppressed.intersection(visible)),
            "suppressed_visible_segment_count": len(rows) - len(retained),
            "unresolved_assembly_counts": dict(assembly_rejections),
            "unresolved_display_counts": {
                reason: len(ids) for reason, ids in unresolved.items()
            },
            "unresolved_display_addresses": {
                reason: sorted(ids)[:MAX_DIAGNOSTIC_ADDRESSES]
                for reason, ids in unresolved.items()
            },
            "maximum_assembly_sources": MAX_GRAPHIC_ASSEMBLY_SOURCES,
            "maximum_assembly_points": MAX_GRAPHIC_ASSEMBLY_POINTS,
        },
    )
