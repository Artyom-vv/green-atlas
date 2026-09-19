"""Diagnostic WCS snapshot; not an admitted product import or CRS claim."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44
from shapely.geometry import Polygon, mapping
from verify_native_region_boundaries import verify
from verify_native_insert_transforms import verify_transforms


def build(source: Path, controls: Path, inventory_path: Path, transforms: Path):
    manifest = json.loads((controls / "manifest.json").read_text())
    inventory = json.loads(inventory_path.read_text())
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != manifest["source_sha256"]:
        raise ValueError("Source changed")
    if Path(inventory["source"]).resolve() != source.resolve():
        raise ValueError("Inventory source mismatch")
    if inventory.get("source_sha256") != digest:
        raise ValueError("Inventory content digest missing or mismatched")
    if inventory["dxf_units"] != 6:
        raise ValueError("Only metre-unit source qualified for this experiment")
    transform_evidence = verify_transforms(
        ezdxf.readfile(source), inventory["acis_instances"], transforms)
    result = verify(controls, 1e-6, include_geometry=True)
    if any(r["status"] != "local_comparison_pass" for r in result["rows"]):
        raise ValueError("Incomplete local boundary comparison")
    local = {r["layer"]: r["geometry"] for r in result["rows"]}
    blocks = defaultdict(list)
    for row in inventory["acis"]:
        if row["type"] == "REGION":
            blocks[row["layout"]].append(row)
    features = []
    for placement in inventory["acis_instances"]:
        matrix = Matrix44(placement["matrix"])
        # Only translations were observed in this source. Do not generalize
        # local error tolerances to scaling, shear or projection silently.
        identity = list(Matrix44())
        if any(abs(placement["matrix"][i] - identity[i]) > 1e-12
               for i in range(16) if i not in (12, 13, 14)):
            raise ValueError("Nontranslation matrix needs separate qualification")
        for row in blocks[placement["block"]]:
            coords = local[f'SOURCE_{row["handle"]}']["coordinates"][0]
            transformed = list(matrix.transform_vertices([(x, y, 0) for x, y in coords]))
            if any(abs(v.z) > 1e-9 for v in transformed):
                raise ValueError("Nonzero elevation requires projection policy")
            polygon = Polygon([(v.x, v.y) for v in transformed])
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError("Invalid WCS polygon")
            chain = [c["handle"] for c in placement["chain"]]
            features.append(dict(type="Feature", id="/".join(chain + [row["handle"]]),
                geometry=mapping(polygon), properties=dict(
                    entity_type="REGION", source_handle=row["handle"],
                    source_layer=row["layer"], insert_chain=chain,
                    source_sha256=digest, native_world_verified=False)))
    if len(features) != len(manifest["rows"]):
        raise ValueError("This experiment requires one placement per definition")
    return dict(type="FeatureCollection", features=features,
                evidence=dict(scope="diagnostic WCS projection; not product admission",
                              source_sha256=digest, native_world_verified=False,
                              transforms=transform_evidence))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("controls", type=Path)
    p.add_argument("inventory", type=Path)
    p.add_argument("transforms", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    snapshot = build(a.source, a.controls, a.inventory, a.transforms)
    with a.output.open("x", encoding="utf8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False)
    print(json.dumps({"features": len(snapshot["features"]), "evidence": snapshot["evidence"]}))
