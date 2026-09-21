"""Inventory an OSM API snapshot without pretending it is aligned to local DXF.

Usage:
    python audit_osm_context.py input.osm output-directory

The emitted GeoJSON remains in WGS84.  A later, reviewed control-point transform
must align it to a drawing that has no machine-readable CRS.
"""
from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tags(element) -> dict[str, str]:
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: audit_osm_context.py input.osm output-directory")
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = ET.parse(source).getroot()
    bounds = root.find("bounds")
    nodes = {
        node.attrib["id"]: {
            "coordinates": [float(node.attrib["lon"]), float(node.attrib["lat"])],
            "tags": tags(node),
            "version": node.attrib.get("version"),
            "timestamp": node.attrib.get("timestamp"),
        }
        for node in root.findall("node")
    }

    point_counts = Counter()
    features = []
    for node_id, node in nodes.items():
        if not node["tags"]:
            continue
        for key in ("natural", "highway", "amenity", "barrier", "man_made"):
            if key in node["tags"]:
                point_counts[f"{key}={node['tags'][key]}"] += 1
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": node["coordinates"]},
            "properties": {
                "osm_type": "node",
                "osm_id": node_id,
                "version": node["version"],
                "timestamp": node["timestamp"],
                "tags": node["tags"],
                "alignment_status": "candidate_wgs84_not_aligned_to_dxf",
            },
        })

    way_counts = Counter()
    named_street_segments = []
    buildings_with_levels = 0
    for way in root.findall("way"):
        way_tags = tags(way)
        refs = [nd.attrib["ref"] for nd in way.findall("nd")]
        if not refs or any(ref not in nodes for ref in refs):
            continue
        coordinates = [nodes[ref]["coordinates"] for ref in refs]
        if "building" in way_tags:
            way_counts["building"] += 1
            if "building:levels" in way_tags or "height" in way_tags:
                buildings_with_levels += 1
        if "highway" in way_tags:
            way_counts[f"highway={way_tags['highway']}"] += 1
        if way_tags.get("name") == "Кустанайская улица":
            named_street_segments.append(way.attrib["id"])
        closed = len(coordinates) >= 4 and coordinates[0] == coordinates[-1]
        geometry_type = "Polygon" if closed and any(key in way_tags for key in ("building", "landuse", "leisure", "amenity")) else "LineString"
        geometry_coordinates = [coordinates] if geometry_type == "Polygon" else coordinates
        features.append({
            "type": "Feature",
            "geometry": {"type": geometry_type, "coordinates": geometry_coordinates},
            "properties": {
                "osm_type": "way",
                "osm_id": way.attrib["id"],
                "version": way.attrib.get("version"),
                "timestamp": way.attrib.get("timestamp"),
                "tags": way_tags,
                "alignment_status": "candidate_wgs84_not_aligned_to_dxf",
            },
        })

    bbox = None
    if bounds is not None:
        bbox = [
            float(bounds.attrib["minlon"]), float(bounds.attrib["minlat"]),
            float(bounds.attrib["maxlon"]), float(bounds.attrib["maxlat"]),
        ]
    geojson = {"type": "FeatureCollection", "bbox": bbox, "features": features}
    (output / "osm-context-wgs84.geojson").write_text(json.dumps(geojson, ensure_ascii=False, indent=2))
    receipt = {
        "schema": "green-atlas.external-context-audit.v1",
        "source": str(source),
        "source_sha256": sha256(source),
        "source_api_url": (
            "https://api.openstreetmap.org/api/0.6/map?bbox="
            + ",".join(str(value) for value in bbox)
        ) if bbox else None,
        "generator": root.attrib.get("generator"),
        "bbox_wgs84": bbox,
        "counts": {
            "nodes": len(nodes),
            "ways": len(root.findall("way")),
            "relations": len(root.findall("relation")),
            "features_exported": len(features),
        },
        "way_classes": dict(way_counts),
        "point_classes": dict(point_counts),
        "kustanayskaya_named_way_ids": named_street_segments,
        "buildings_with_levels_or_height": buildings_with_levels,
        "natural_tree_nodes": point_counts.get("natural=tree", 0),
        "alignment": {
            "status": "candidate_only",
            "reason": "DXF has metres and a textual Moscow CRS declaration but no GEODATA or verified WGS84 transform",
            "required_next": "fit and validate one shared transform from reviewed control points; record residuals",
        },
        "usage": {
            "inside_project_priority": "DXF/topographic survey",
            "outside_project_priority": "OSM may provide context after alignment",
            "vegetation": "do not create trees from this snapshot: no natural=tree nodes are present",
            "license": "ODbL; publish attribution to OpenStreetMap contributors",
        },
    }
    (output / "osm-context-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
