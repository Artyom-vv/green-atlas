"""Test a whole prepared drawing with existing reader and default layer mapping.

No upload bypass, database writes, layer suppression, or claims that the default
mapping is professionally verified. Budget overrides are explicit diagnostics.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.geojson_size import coordinate_count
from app.projects.contracts import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-features", type=int, default=100_000)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    content = args.source.read_bytes()
    started = time.monotonic()
    result = {"source": str(args.source), "bytes": len(content),
              "source_sha256": hashlib.sha256(content).hexdigest(),
              "ordinary_upload_bytes_allowed": len(content) <= MAX_DXF_CONTENT_BYTES,
              "feature_budget": args.max_features,
              "policy_changed": False, "mapping_verified": False,
              "project_published": False, "full_flow_passed": False}

    def save():
        result["elapsed_seconds"] = time.monotonic() - started
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")

    try:
        imported = EzdxfReader(capacity=SourceGeometryCapacity(max_features=args.max_features)).read(args.source.name, content)
        features = imported.geometry.feature_collection["features"]
        result["reader"] = {"status": "completed", "seconds": time.monotonic() - started,
                            "entities": imported.entity_count, "features": len(features),
                            "coordinates": sum(coordinate_count(f["geometry"]) for f in features),
                            "warnings": imported.warnings, "bounds": imported.bounds,
                            "units": imported.units, "units_assumed": imported.units_assumed,
                            "kinds": dict(Counter(l.mapped_kind.value for l in imported.layers)),
                            "layers": [l.model_dump(mode="json") for l in imported.layers]}
        save()
    except Exception as error:
        result["reader"] = {"status": "failed", "error": str(error), "traceback": traceback.format_exc()}
        save()
        return
    calculation_start = time.monotonic()
    try:
        project = Project(name="Isolated street audit", layers=imported.layers, source_geometry=imported.geometry)
        calculated = ShapelyGeometryEngine().calculate(project)
        result["calculation_with_unverified_default_mapping"] = {
            "status": "completed", "seconds": time.monotonic() - calculation_start,
            "features": len(calculated.feature_collection["features"]),
            "warning": "Execution success does not verify all physical constraints or regulatory roles."}
    except Exception as error:
        result["calculation_with_unverified_default_mapping"] = {"status": "failed", "error": str(error)}
    save()
    print(json.dumps({k: v for k, v in result.items() if k != "reader"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
