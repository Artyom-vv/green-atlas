"""Read-only DXF terrain inventory; spatial matches are hypotheses, not elevations.

Run with the API venv: probe_terrain_evidence.py INPUT_DIR OUTPUT_DIR.
Does not modify source drawings or project state. Block coordinates below are
local definition coordinates; they are not claimed to be flattened WCS instances.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import re
import sys

import ezdxf
import numpy as np


NUMBER = re.compile(r"\s*[+-]?\d{2,3}[.,]\d{1,3}\s*")


def point_z(e):
    t = e.dxftype()
    if t in {"INSERT", "TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
        return [float(e.dxf.insert.z)]
    if t == "POINT":
        return [float(e.dxf.location.z)]
    if t == "LINE":
        return [float(e.dxf.start.z), float(e.dxf.end.z)]
    if t in {"ARC", "CIRCLE", "ELLIPSE"}:
        return [float(e.dxf.center.z)]
    if t == "LWPOLYLINE":
        return [float(e.dxf.elevation)]
    if t == "POLYLINE":
        return [float(v.dxf.location.z) for v in e.vertices]
    if t == "3DFACE":
        return [float(e.dxf.get(k).z) for k in ("vtx0", "vtx1", "vtx2", "vtx3")]
    if t == "MESH":
        return [float(v[2]) for v in e.get_data().vertices]
    return []


def inventory(path):
    doc = ezdxf.readfile(path)
    layers = collections.defaultdict(lambda: {"types": collections.Counter(), "nonzero_z_entities": 0, "z_min": None, "z_max": None, "numeric_text_count": 0, "numeric_examples": []})
    refs, errors = [], []
    for block in doc.blocks:
        if block.block.is_xref:
            refs.append({"name": block.name, "path": block.block.dxf.get("xref_path", "")})
        for e in block:
            r = layers[e.dxf.layer]
            r["types"][e.dxftype()] += 1
            try:
                zs = point_z(e)
                if zs:
                    r["z_min"] = min(zs) if r["z_min"] is None else min(r["z_min"], *zs)
                    r["z_max"] = max(zs) if r["z_max"] is None else max(r["z_max"], *zs)
                    r["nonzero_z_entities"] += int(any(abs(z) > 1e-8 for z in zs))
                if e.dxftype() in {"TEXT", "MTEXT"}:
                    s = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
                    if NUMBER.fullmatch(s):
                        r["numeric_text_count"] += 1
                        if len(r["numeric_examples"]) < 5:
                            r["numeric_examples"].append(s)
            except Exception as ex:
                errors.append({"handle": e.dxf.handle, "error": str(ex)})
    return doc, {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "units": doc.units, "modelspace_types": dict(collections.Counter(e.dxftype() for e in doc.modelspace())), "layers_definition_scope": dict(layers), "xref_definitions": refs, "errors": errors}


def candidates(doc):
    points, texts = [], []
    for e in doc.modelspace():
        if e.dxf.layer != "Горизонтали":
            continue
        if e.dxftype() == "INSERT" and e.dxf.name.startswith("PIKET"):
            children = list(doc.blocks[e.dxf.name])
            points.append({"handle": e.dxf.handle, "xy": list(e.dxf.insert)[:2], "z": e.dxf.insert.z, "block": e.dxf.name, "children": dict(collections.Counter(c.dxftype() for c in children)), "attributes": {a.dxf.tag: a.dxf.text for a in e.attribs}})
        elif e.dxftype() == "TEXT" and NUMBER.fullmatch(e.dxf.text):
            texts.append({"handle": e.dxf.handle, "xy": list(e.dxf.insert)[:2], "value": float(e.dxf.text.replace(",", ".")), "rotation": e.dxf.rotation, "valign": e.dxf.valign, "height": e.dxf.height})
    if len(points) < 2:
        return {"error": "Fewer than two PIKET instances; no matching attempted"}
    xy = np.array([p["xy"] for p in points])
    groups = collections.defaultdict(list)
    for t in texts:
        distances = np.sqrt(((xy - t["xy"]) ** 2).sum(axis=1))
        ids = np.argsort(distances)[:2]
        t["nearest_distance_m"] = float(distances[ids[0]])
        t["second_distance_m"] = float(distances[ids[1]])
        t["nearest_piket_handle"] = points[ids[0]]["handle"]
        # A diagnostic radius only, NOT an admission threshold.
        if distances[ids[0]] <= 3:
            groups[int(ids[0])].append(t)
    counts = collections.Counter(len(groups[i]) for i in range(len(points)))
    pairs = [{"piket": points[i], "labels": ts, "height_spread_m": max(t["value"] for t in ts)-min(t["value"] for t in ts)} for i, ts in groups.items() if len(ts) > 1]
    return {"status": "hypotheses_only_not_terrain", "method": "Nearest TEXT insertion point within 3m; no semantic acceptance", "piket_count": len(points), "text_count": len(texts), "piket_label_multiplicity": dict(sorted(counts.items())), "unassigned_labels": len(texts)-sum(len(v) for v in groups.values()), "numeric_range": [min(t["value"] for t in texts), max(t["value"] for t in texts)], "points": points, "texts": texts, "multi_label_candidates": pairs}


def main():
    root, out = map(Path, sys.argv[1:3])
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    for path in sorted(root.rglob("*.dxf")):
        print(path.name, flush=True)
        try:
            doc, row = inventory(path)
            summary.append(row)
            if path.name == "00.1_10004141_Топография.dxf":
                (out / "topography-candidates.json").write_text(json.dumps(candidates(doc), ensure_ascii=False, indent=2))
        except Exception as ex:
            summary.append({"path": str(path), "error": str(ex)})
    (out / "inventory.json").write_text(json.dumps({"scope": "all block definitions including layouts; not flattened instance WCS; ACIS/proxy interiors not decoded", "files": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
