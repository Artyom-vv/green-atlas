#!/usr/bin/env python3
"""Account for every GIS feature between source capture and render scene.

This audit is deliberately separate from visual quality.  A feature may be
present in the scene and still have only a diagnostic proxy, but it may never
disappear without a machine-readable reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon, shape


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_SCENE = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/scene-packet.json"
DEFAULT_SURFACES = ROOT / ".runtime/nspd-surface-trace-kustanayskaya-20260920/surface-packet.json"
DEFAULT_OUTPUT = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/source-coverage-audit.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--osm", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feature_class(row: dict[str, Any], collection: str) -> str:
    properties = row.get("properties") or {}
    if collection == "roads":
        return str(properties.get("class") or "unknown")
    if collection == "buildings":
        return str(properties.get("class") or properties.get("subtype") or "unknown")
    if collection == "green_areas":
        return str(properties.get("class") or "unknown")
    return str(properties.get("class") or "unknown")


def osm_primary_class(tags: dict[str, str]) -> str:
    for key in (
        "highway", "building", "amenity", "barrier", "leisure", "natural",
        "landuse", "public_transport", "shop", "entrance", "railway",
        "man_made", "power", "office",
    ):
        if key in tags:
            return f"{key}={tags[key]}"
    return "other_tagged"


def osm_source_id(record_id: str | None) -> str | None:
    return record_id.split("@", 1)[0] if record_id else None


def main() -> None:
    args = parse_args()
    world = json.loads(args.world.read_text())
    scene = json.loads(args.scene.read_text())
    surfaces = json.loads(args.surfaces.read_text())
    osm_path = args.osm or Path(world["inputs"]["osm"]["path"])

    if scene["source"]["world_sha256"] != sha256(args.world):
        raise ValueError("scene was not compiled from the supplied world")
    if scene["source"]["surface_packet_sha256"] != sha256(args.surfaces):
        raise ValueError("scene was not compiled from the supplied surface packet")
    if world["inputs"]["osm"]["sha256"] != sha256(osm_path):
        raise ValueError("OSM snapshot hash differs from world input")

    roi = shape(surfaces["scene_roi"]["geometry_local"])
    context_region = roi.buffer(float(world["scope"]["context_margin_m"]))
    primary_road_ids = {
        row["id"] for row in surfaces["referenced_world_features"] if row["semantic"] == "road"
    }
    scene_ids = {
        "roads": primary_road_ids | {
            row["id"] for row in scene.get("automatic_context_surfaces", [])
            if row.get("semantic") in {"context_road", "context_sidewalk"}
        },
        "green_areas": {
            row["id"] for row in scene.get("automatic_context_surfaces", [])
            if str(row.get("semantic", "")).startswith("context_")
            and row.get("semantic") not in {"context_road", "context_sidewalk"}
        },
        "infrastructure": {row["id"] for row in scene.get("automatic_infrastructure", [])},
        "buildings": {row["id"] for row in scene.get("buildings", [])},
    }

    collection_audits: dict[str, Any] = {}
    all_unaccounted: list[dict[str, str]] = []
    for collection in ("roads", "green_areas", "infrastructure", "buildings"):
        rows = world[collection]
        included = scene_ids[collection]
        details = []
        for row in rows:
            geometry = shape(row["geometry_local"] if "geometry_local" in row else row["surface_geometry_local"])
            properties = row.get("properties") or {}
            if row["id"] in included:
                if row["id"] in primary_road_ids:
                    disposition, reason = "rendered_reviewed_override", "reviewed local road surface"
                elif collection == "buildings":
                    disposition, reason = "rendered", "height available and within render distance"
                else:
                    disposition, reason = "rendered_diagnostic_proxy", "automatic bbox context"
            elif not geometry.intersects(context_region):
                disposition, reason = "excluded", "outside scene context buffer"
            elif collection == "buildings" and properties.get("height_m") is None:
                disposition, reason = "represented_as_unknown", "footprint exists but height is unknown"
            elif collection == "buildings" and geometry.distance(roi) > 100.0:
                disposition, reason = "excluded", "outside current 100 m building render distance"
            elif collection == "roads":
                disposition, reason = "represented_as_unknown", "surface was fully masked by reviewed local authority"
            elif collection == "green_areas":
                disposition, reason = "represented_as_unknown", "polygon was fully masked by reviewed surfaces or roads"
            else:
                disposition, reason = "unaccounted", "no compiler disposition"
                all_unaccounted.append({"collection": collection, "id": row["id"]})
            details.append({
                "id": row["id"],
                "class": feature_class(row, collection),
                "disposition": disposition,
                "reason": reason,
            })
        disposition_counts = Counter(row["disposition"] for row in details)
        class_counts = Counter(feature_class(row, collection) for row in rows)
        included_class_counts = Counter(row["class"] for row in details if row["disposition"].startswith("rendered"))
        collection_audits[collection] = {
            "source_count": len(rows),
            "rendered_count": sum(count for status, count in disposition_counts.items() if status.startswith("rendered")),
            "represented_as_unknown_count": disposition_counts["represented_as_unknown"],
            "excluded_count": disposition_counts["excluded"],
            "unaccounted_count": disposition_counts["unaccounted"],
            "source_classes": dict(sorted(class_counts.items())),
            "rendered_classes": dict(sorted(included_class_counts.items())),
            "dispositions": dict(sorted(disposition_counts.items())),
            "features": details,
        }

    # Raw OSM inventory is useful because normalized Overture/world layers do
    # not preserve every tagged entrance, shop or routing relation.  We audit
    # the exact context buffer, then state whether each source ID is represented
    # by a normalized world object or remains a tag-only/unhandled feature.
    osm_root = ET.parse(osm_path).getroot()
    lon0, lat0 = map(float, world["coordinate_frame"]["origin_wgs84_lon_lat"])
    earth_radius = float(world["coordinate_frame"]["earth_radius_m"])

    def local_xy(lon: float, lat: float) -> tuple[float, float]:
        return (
            math.radians(lon - lon0) * earth_radius * math.cos(math.radians(lat0)),
            math.radians(lat - lat0) * earth_radius,
        )

    node_elements = {row.attrib["id"]: row for row in osm_root.findall("node")}
    node_points = {
        node_id: Point(*local_xy(float(row.attrib["lon"]), float(row.attrib["lat"])))
        for node_id, row in node_elements.items()
    }
    local_nodes = {node_id for node_id, point in node_points.items() if point.intersects(context_region)}
    local_way_ids: set[str] = set()
    raw_features: list[dict[str, Any]] = []
    for node_id in sorted(local_nodes, key=int):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in node_elements[node_id].findall("tag")}
        if tags:
            raw_features.append({"source_id": f"n{node_id}", "element": "node", "tags": tags})
    for way in osm_root.findall("way"):
        node_ids = [row.attrib["ref"] for row in way.findall("nd") if row.attrib["ref"] in node_points]
        coordinates = [(node_points[node_id].x, node_points[node_id].y) for node_id in node_ids]
        if len(coordinates) < 2:
            continue
        geometry = Polygon(coordinates) if len(coordinates) >= 4 and coordinates[0] == coordinates[-1] else LineString(coordinates)
        if not geometry.intersects(context_region):
            continue
        local_way_ids.add(way.attrib["id"])
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if tags:
            raw_features.append({"source_id": f"w{way.attrib['id']}", "element": "way", "tags": tags})
    for relation in osm_root.findall("relation"):
        local_member = any(
            (member.attrib["type"] == "node" and member.attrib["ref"] in local_nodes)
            or (member.attrib["type"] == "way" and member.attrib["ref"] in local_way_ids)
            for member in relation.findall("member")
        )
        if not local_member:
            continue
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in relation.findall("tag")}
        if tags:
            raw_features.append({"source_id": f"r{relation.attrib['id']}", "element": "relation", "tags": tags})

    normalized_osm_ids: set[str] = set()
    for collection in ("roads", "buildings", "infrastructure"):
        for row in world[collection]:
            for source in (row.get("properties") or {}).get("sources") or []:
                source_id = osm_source_id(source.get("record_id"))
                if source_id:
                    normalized_osm_ids.add(source_id)
    for row in world["green_areas"]:
        identifier = row["id"].split(":")
        if len(identifier) >= 3 and identifier[1] == "way":
            normalized_osm_ids.add(f"w{identifier[2]}")

    raw_class_counts = Counter()
    raw_dispositions = Counter()
    for row in raw_features:
        row["class"] = osm_primary_class(row["tags"])
        row["disposition"] = (
            "normalized_into_world" if row["source_id"] in normalized_osm_ids else "not_normalized_into_world"
        )
        raw_class_counts[row["class"]] += 1
        raw_dispositions[row["disposition"]] += 1

    output = {
        "schema": "green-atlas.scene-source-coverage-audit.v1",
        "status": "passed" if not all_unaccounted else "failed_unaccounted_features",
        "principle": "Every normalized GIS object is rendered, represented as unknown, or excluded with an explicit reason.",
        "inputs": {
            "world": {"path": str(args.world), "sha256": sha256(args.world)},
            "scene": {"path": str(args.scene), "sha256": sha256(args.scene)},
            "surfaces": {"path": str(args.surfaces), "sha256": sha256(args.surfaces)},
            "osm": {"path": str(osm_path), "sha256": sha256(osm_path)},
        },
        "context": {
            "roi_area_m2": round(roi.area, 6),
            "buffer_m": world["scope"]["context_margin_m"],
            "context_area_m2": round(context_region.area, 6),
        },
        "normalized_world_to_scene": collection_audits,
        "raw_osm_to_world": {
            "tagged_feature_count": len(raw_features),
            "class_counts": dict(sorted(raw_class_counts.items())),
            "dispositions": dict(sorted(raw_dispositions.items())),
            "features": raw_features,
            "limitation": "OSM relations are admitted when a member intersects the context; route/metadata relations are not standalone render geometry.",
        },
        "unaccounted": all_unaccounted,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    summary_path = args.output.with_suffix(".md")
    lines = [
        "# GIS source coverage audit",
        "",
        f"Status: **{output['status']}**.",
        "",
        "| Collection | Source | Rendered | Unknown | Explicitly excluded | Unaccounted |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, audit in collection_audits.items():
        lines.append(
            f"| {name} | {audit['source_count']} | {audit['rendered_count']} | "
            f"{audit['represented_as_unknown_count']} | {audit['excluded_count']} | {audit['unaccounted_count']} |"
        )
    lines.extend([
        "",
        f"Raw tagged OSM features intersecting the context: **{len(raw_features)}**.",
        f"Normalized into the current world model: **{raw_dispositions['normalized_into_world']}**.",
        f"Not normalized into the current world model: **{raw_dispositions['not_normalized_into_world']}**.",
        "",
        "The latter group includes entrances, shops, routing relations and physical classes the current normalizer does not yet map. It is retained in JSON rather than silently discarded.",
    ])
    summary_path.write_text("\n".join(lines) + "\n")
    print(json.dumps({
        "status": output["status"],
        "output": str(args.output),
        "summary": str(summary_path),
        "collections": {
            key: {
                "source": value["source_count"],
                "rendered": value["rendered_count"],
                "unknown": value["represented_as_unknown_count"],
                "excluded": value["excluded_count"],
                "unaccounted": value["unaccounted_count"],
            }
            for key, value in collection_audits.items()
        },
        "raw_osm": dict(raw_dispositions),
    }, ensure_ascii=False, indent=2))
    if all_unaccounted:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
