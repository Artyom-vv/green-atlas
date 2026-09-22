"""Import an engineering LandXML TIN with explicit CRS and vertical datum.

The importer is deliberately strict: it never guesses coordinate order, CRS or
vertical datum.  It preserves point/face IDs and breaklines so the resulting
terrain can replace broad DEM context inside its actual coverage geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(element: ET.Element, name: str):
    return [child for child in element.iter() if local_name(child.tag) == name]


def xyz(values: list[float], axis_order: str) -> list[float]:
    if len(values) != 3:
        raise ValueError(f"Expected 3 coordinates, got {len(values)}")
    if axis_order == "northing-easting-elevation":
        return [values[1], values[0], values[2]]
    if axis_order == "easting-northing-elevation":
        return values
    raise ValueError(f"Unsupported axis order: {axis_order}")


def triangle_area_xy(a: list[float], b: list[float], c: list[float]) -> float:
    return abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])) / 2


def parse_surface(surface: ET.Element, axis_order: str) -> dict[str, Any]:
    definitions = [row for row in surface if local_name(row.tag) == "Definition"]
    if len(definitions) != 1:
        raise ValueError(f"Surface {surface.attrib.get('name')} must contain exactly one Definition")
    definition = definitions[0]
    point_nodes = children(definition, "P")
    points: dict[str, list[float]] = {}
    for node in point_nodes:
        point_id = node.attrib.get("id")
        if not point_id or point_id in points:
            raise ValueError(f"Missing or duplicate LandXML point id: {point_id}")
        values = [float(value) for value in (node.text or "").split()]
        points[point_id] = xyz(values, axis_order)
    if len(points) < 3:
        raise ValueError(f"Surface {surface.attrib.get('name')} has fewer than 3 points")

    faces = []
    referenced = set()
    for node in children(definition, "F"):
        references = (node.text or "").split()[:3]
        if len(references) != 3:
            raise ValueError("LandXML face must start with exactly three point references")
        missing = [reference for reference in references if reference not in points]
        if missing:
            raise ValueError(f"LandXML face references missing points: {missing}")
        coordinates = [points[reference] for reference in references]
        if triangle_area_xy(*coordinates) <= 1e-10:
            raise ValueError(f"Degenerate LandXML face: {references}")
        faces.append({"point_ids": references})
        referenced.update(references)
    if not faces:
        raise ValueError(f"Surface {surface.attrib.get('name')} has no faces")

    breaklines = []
    for index, node in enumerate(children(definition, "PntList3D")):
        values = [float(value) for value in (node.text or "").split()]
        if len(values) < 6 or len(values) % 3:
            raise ValueError("LandXML PntList3D must contain at least two coordinate triples")
        coordinates = [xyz(values[offset:offset+3], axis_order) for offset in range(0, len(values), 3)]
        breaklines.append({"id": f"breakline:{index+1}", "coordinates": coordinates})

    ordered_ids = sorted(points, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))
    index_by_id = {point_id: index for index, point_id in enumerate(ordered_ids)}
    vertices = [points[point_id] for point_id in ordered_ids]
    triangles = [[index_by_id[point_id] for point_id in face["point_ids"]] for face in faces]
    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    zs = [point[2] for point in vertices]
    area = sum(triangle_area_xy(vertices[a], vertices[b], vertices[c]) for a, b, c in triangles)
    return {
        "name": surface.attrib.get("name") or "unnamed",
        "source_description": surface.attrib.get("desc"),
        "surface_type": definition.attrib.get("surfType"),
        "vertices": vertices,
        "point_ids": ordered_ids,
        "triangles": triangles,
        "breaklines": breaklines,
        "bounds_xyz": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "triangle_area_sum_xy_m2": round(area, 9),
        "unreferenced_point_ids": sorted(set(points) - referenced),
    }


def import_landxml(
    path: Path, horizontal_crs: str, vertical_datum: str, axis_order: str,
    surface_name: str | None = None,
) -> dict[str, Any]:
    if not horizontal_crs.strip() or horizontal_crs.lower() == "unknown":
        raise ValueError("A verified horizontal CRS identifier is required")
    if not vertical_datum.strip() or vertical_datum.lower() == "unknown":
        raise ValueError("A verified vertical datum identifier is required")
    root = ET.parse(path).getroot()
    surfaces = children(root, "Surface")
    if surface_name is not None:
        surfaces = [surface for surface in surfaces if surface.attrib.get("name") == surface_name]
    if len(surfaces) != 1:
        names = [surface.attrib.get("name") for surface in surfaces]
        raise ValueError(f"Select exactly one LandXML surface; matched {len(surfaces)}: {names}")
    surface = parse_surface(surfaces[0], axis_order)
    return {
        "schema": "green-atlas.terrain-source-packet.v1",
        "status": "admitted_engineering_tin",
        "source": {
            "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path),
            "format": "LandXML",
        },
        "coordinates": {
            "horizontal_crs": horizontal_crs,
            "vertical_datum": vertical_datum,
            "source_axis_order": axis_order,
            "canonical_xyz": "easting,northing,elevation in metres",
        },
        "surface": surface,
        "quality": {
            "finite_vertices": all(math.isfinite(value) for point in surface["vertices"] for value in point),
            "vertices": len(surface["vertices"]),
            "triangles": len(surface["triangles"]),
            "breaklines": len(surface["breaklines"]),
            "topology_status": "validated_references_and_non_degenerate_faces",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--horizontal-crs", required=True)
    parser.add_argument("--vertical-datum", required=True)
    parser.add_argument(
        "--axis-order",
        choices=("northing-easting-elevation", "easting-northing-elevation"),
        required=True,
    )
    parser.add_argument("--surface-name")
    args = parser.parse_args()
    packet = import_landxml(
        args.input, args.horizontal_crs, args.vertical_datum, args.axis_order, args.surface_name
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": packet["status"], "surface": packet["surface"]["name"],
        **packet["quality"], "output": str(args.output.resolve()),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
