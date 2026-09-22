"""Extract source-backed street-object insertion points for the render ROI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf
from shapely.geometry import Point, box, mapping


ROOT = Path(__file__).resolve().parents[2]
DXF_ROOT = ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf"
OUTPUT = ROOT / ".runtime/deterministic-render-audit-20260919/street-objects.geojson"
ROI = (15840.0, -4980.0, 15893.0, -4937.0)
LAYERS = {
    "Колодцы": "utility_well",
    "Фонари": "street_light",
    "Светофоры": "traffic_light",
    "Люки": "manhole",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    matches = list(DXF_ROOT.rglob("00.1_10004141_Топография.dxf"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one topography DXF, found {len(matches)}")
    source = matches[0]
    source_hash = sha256(source)
    document = ezdxf.readfile(source)
    scope = box(*ROI)
    features = []
    for entity in document.modelspace().query("INSERT"):
        semantic_class = LAYERS.get(entity.dxf.layer)
        if not semantic_class:
            continue
        point = Point(entity.dxf.insert.x, entity.dxf.insert.y)
        if not scope.covers(point):
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(point),
            "properties": {
                "semantic_class": semantic_class,
                "source_file": str(source),
                "source_sha256": source_hash,
                "source_handle": entity.dxf.handle,
                "source_layer": entity.dxf.layer,
                "block_name": entity.dxf.name,
                "rotation_deg": float(entity.dxf.rotation),
                "xscale": float(entity.dxf.xscale),
                "yscale": float(entity.dxf.yscale),
                "zscale": float(entity.dxf.zscale),
                "status": "source_insert_point; symbol_is_not_a_physical_3d_asset",
            },
        })
    features.sort(key=lambda row: (row["properties"]["semantic_class"], row["properties"]["source_handle"]))
    result = {
        "type": "FeatureCollection",
        "bbox": list(ROI),
        "source": {"path": str(source), "sha256": source_hash},
        "features": features,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    counts = {}
    for feature in features:
        key = feature["properties"]["semantic_class"]
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({"output": str(OUTPUT), "objects": len(features), "by_class": counts},
                     ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
