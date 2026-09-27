"""Audit converted DXF files for machine-readable terrain evidence.

Numeric height labels are not a terrain surface.  This audit keeps them
separate from coordinates carried by geometry and from native mesh/TIN entity
types, so a planar topographic drawing cannot silently pass a terrain gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

import ezdxf


TERRAIN_ENTITY_TYPES = {
    "3DFACE", "MESH", "POLYLINE", "POLYFACEMESH", "POLYMESH",
    "ACAD_PROXY_ENTITY", "AECC_TIN_SURFACE", "AECC_GRID_SURFACE",
}
NUMBER = re.compile(r"(?<![\w])[-+±]?\d{1,4}(?:[.,]\d{1,4})?(?![\w])")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite_z(values: Iterable[Any]) -> list[float]:
    result = []
    for value in values:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            result.append(value)
    return result


def entity_z_values(entity: Any) -> list[float]:
    kind = entity.dxftype()
    try:
        if kind == "POINT":
            return finite_z([entity.dxf.location.z])
        if kind in {"LINE", "XLINE", "RAY"}:
            return finite_z([entity.dxf.start.z, entity.dxf.end.z])
        if kind == "LWPOLYLINE":
            return finite_z([entity.dxf.elevation])
        if kind == "POLYLINE":
            return finite_z(vertex.dxf.location.z for vertex in entity.vertices)
        if kind == "3DFACE":
            return finite_z(entity.dxf.get(f"vtx{index}").z for index in range(4))
        if kind == "MESH":
            return finite_z(vertex.z for vertex in entity.vertices)
        if kind == "INSERT":
            return finite_z([entity.dxf.insert.z])
        if kind in {"CIRCLE", "ARC", "ELLIPSE", "TEXT", "MTEXT"}:
            location = entity.dxf.get("insert", entity.dxf.get("center", None))
            return finite_z([location.z]) if location is not None else []
        elevation = entity.dxf.get("elevation", None)
        return finite_z([getattr(elevation, "z", elevation)]) if elevation is not None else []
    except (AttributeError, TypeError, ValueError, ezdxf.DXFError):
        return []


def text_value(entity: Any) -> str:
    try:
        if entity.dxftype() == "TEXT":
            return entity.dxf.text or ""
        if entity.dxftype() == "MTEXT":
            return entity.plain_text() or ""
        if entity.dxftype() in {"ATTRIB", "ATTDEF"}:
            return entity.dxf.text or ""
    except (AttributeError, ezdxf.DXFError):
        pass
    return ""


def audit_entities(entities: Iterable[Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    z_values: list[float] = []
    numeric_annotations = 0
    numeric_examples: list[str] = []
    for entity in entities:
        kind = entity.dxftype()
        counts[kind] += 1
        z_values.extend(entity_z_values(entity))
        value = text_value(entity).strip()
        if value and NUMBER.search(value):
            numeric_annotations += 1
            if len(numeric_examples) < 12:
                numeric_examples.append(value[:120])
    nonzero = [value for value in z_values if abs(value) > 1e-9]
    return {
        "entity_types": dict(sorted(counts.items())),
        "entities": sum(counts.values()),
        "terrain_entity_types_present": sorted(set(counts) & TERRAIN_ENTITY_TYPES),
        "coordinate_z": {
            "samples": len(z_values),
            "nonzero_samples": len(nonzero),
            "nonzero_min": min(nonzero) if nonzero else None,
            "nonzero_max": max(nonzero) if nonzero else None,
        },
        "numeric_text_annotations": numeric_annotations,
        "numeric_text_examples": numeric_examples,
    }


def audit_dxf(path: Path) -> dict[str, Any]:
    document = ezdxf.readfile(path)
    model = audit_entities(document.modelspace())
    definitions = audit_entities(
        entity
        for block in document.blocks
        if not block.name.startswith("*")
        for entity in block
    )
    explicit_mesh = any(
        kind in {"3DFACE", "MESH", "POLYFACEMESH", "POLYMESH", "AECC_TIN_SURFACE", "AECC_GRID_SURFACE"}
        for kind in model["terrain_entity_types_present"] + definitions["terrain_entity_types_present"]
    )
    nonzero_z = model["coordinate_z"]["nonzero_samples"] + definitions["coordinate_z"]["nonzero_samples"]
    annotations = model["numeric_text_annotations"] + definitions["numeric_text_annotations"]
    if explicit_mesh:
        status = "explicit_terrain_or_mesh_entity_present_requires_topology_review"
    elif nonzero_z:
        status = "explicit_z_primitives_present_but_no_terrain_mesh"
    elif annotations:
        status = "planar_geometry_with_numeric_annotations_not_a_terrain_surface"
    else:
        status = "planar_geometry_no_machine_readable_terrain"
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "modelspace": model,
        "block_definitions": definitions,
        "terrain_assessment": {
            "status": status,
            "terrain_gate_passed": False,
            "reason": (
                "A terrain gate requires a referenced TIN/DTM or reviewed XYZ-to-surface relation; "
                "numeric labels and isolated Z values are insufficient."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths: list[Path] = []
    for candidate in args.inputs:
        if candidate.is_dir():
            paths.extend(sorted(candidate.rglob("*.dxf")))
        elif candidate.suffix.lower() == ".dxf":
            paths.append(candidate)
    unique = sorted({path.resolve() for path in paths})
    audits = [audit_dxf(path) for path in unique]
    packet = {
        "schema": "green-atlas.cad-terrain-evidence.v1",
        "files": audits,
        "summary": {
            "files": len(audits),
            "terrain_gate_passed_files": sum(row["terrain_assessment"]["terrain_gate_passed"] for row in audits),
            "explicit_mesh_candidate_files": sum(
                row["terrain_assessment"]["status"].startswith("explicit_terrain_or_mesh") for row in audits
            ),
            "files_with_nonzero_geometry_z": sum(
                row["modelspace"]["coordinate_z"]["nonzero_samples"]
                + row["block_definitions"]["coordinate_z"]["nonzero_samples"] > 0
                for row in audits
            ),
            "files_with_numeric_annotations_only": sum(
                row["terrain_assessment"]["status"].startswith("planar_geometry_with_numeric") for row in audits
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output.resolve()), **packet["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
